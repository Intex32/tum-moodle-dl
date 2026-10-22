"""Log in to TUM Moodle through the TUM Shibboleth identity provider (login.tum.de).

The identity provider is driven like a browser would be: each page is a form, and we decide
what to fill in by what the form asks for (credentials, a one-time code, or just "continue").
That keeps us independent of the exact order of pages, which TUM may change.

Like a user who ticks "keep me logged in" and "remember this device", we keep the identity
provider's cookies, so when the Moodle session expires, logging in again may not need a code.
"""

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from .forms import Form, error_text, page_title, parse_forms, post_data

MOODLE_URL = "https://www.moodle.tum.de"
MOODLE_HOST = "www.moodle.tum.de"
IDP_ENTITY_ID = "https://tumidp.lrz.de/idp/shibboleth"
LOGIN_URL = f"{MOODLE_URL}/Shibboleth.sso/Login?" + urlencode(
    {"providerId": IDP_ENTITY_ID, "target": f"{MOODLE_URL}/auth/shibboleth/index.php"}
)

MAX_STEPS = 15
MAX_CODE_ATTEMPTS = 3
CODE_HINTS = ("otp", "totp", "token", "code", "tan")
FACTOR_HINTS = ("totp", "otp", "authenticator", "app", "code", "token")
SECRET_FIELDS = ["SAMLResponse", "csrf_token", "j_password"]
# The second factor looks like privacyIDEA's Shibboleth plugin (login.tum.de's scripts use its
# helpers), which can offer to skip the code on this device for a while.
REMEMBER_DEVICE = "pidea_remember_me"

# Asked for a one-time code; receives the error the identity provider showed for the previous
# code, if it was rejected.
CodeProvider = Callable[[str | None], str]


class LoginError(Exception):
    pass


class BadCredentials(LoginError):
    pass


class BadOneTimeCode(LoginError):
    pass


def shibboleth_login(
    http: httpx.Client,
    username: str,
    password: str,
    one_time_code: CodeProvider,
    debug_dir: Path | None = None,
) -> httpx.Response:
    """Go through the TUM login until we are sent back to Moodle; returns that response."""
    response = http.get(LOGIN_URL)
    sent_credentials = False
    switched_mode = False
    code_attempts = 0
    for step in range(MAX_STEPS):
        if debug_dir:
            _dump(debug_dir, step, response)
        if response.url.host == MOODLE_HOST and not response.url.path.startswith("/Shibboleth.sso"):
            return response

        forms = parse_forms(response.text, str(response.url))
        if form := _with_field(forms, "SAMLResponse"):
            response = _submit(http, form)
        elif form := _with_field(forms, "j_password"):
            if sent_credentials:
                raise BadCredentials(error_text(response.text) or "username or password rejected")
            sent_credentials = True
            values = {"j_username": username, "j_password": password}
            if form.has("donotcache"):
                values["donotcache"] = ""  # "keep me logged in", so re-logins can skip all this
            response = _submit(http, form, values, button="_eventId_proceed")
        elif found := _code_field(forms):
            form, field = found
            if form.value("isEnrollViaMultichallenge") == "true":
                raise LoginError(
                    "TUM wants you to set up two-factor login first. "
                    "Log in once in a browser to do that, then run `tmdl login` again."
                )
            if code_attempts >= MAX_CODE_ATTEMPTS:
                raise BadOneTimeCode(error_text(response.text) or "one-time code rejected")
            previous_error = error_text(response.text) if code_attempts else None
            code_attempts += 1
            values = {field: one_time_code(previous_error)}
            if REMEMBER_DEVICE in form.checkboxes:
                values[REMEMBER_DEVICE] = "1"
            response = _submit(http, form, values)
        elif form := _passkey_or_push_mode(forms):
            if switched_mode:
                raise LoginError(
                    "TUM only offers a passkey or push confirmation as your second factor. "
                    "Add an authenticator app to your TUM account."
                )
            # Same request as privacyIDEA's "One-Time-Password" button.
            switched_mode = True
            response = _submit(http, form, {"mode": "otp", "_eventId_proceed": "proceed"})
        elif found := _factor_choice(forms):
            form, button = found
            response = _submit(http, form, button=button)
        elif form := _continue_only(forms):
            response = _submit(http, form)
        else:
            raise LoginError(_describe(response))
    raise LoginError("the login did not finish (too many pages)")


def _with_field(forms: list[Form], name: str) -> Form | None:
    return next((f for f in forms if f.has(name)), None)


def _code_field(forms: list[Form]) -> tuple[Form, str] | None:
    """The form and field asking for a one-time code."""
    for form in forms:
        if not form.inputs:
            continue
        for field in form.inputs:
            if field.autocomplete == "one-time-code":
                return form, field.name
        for field in form.inputs:
            if any(hint in f"{field.name} {field.id}".lower() for hint in CODE_HINTS):
                return form, field.name
        if len(form.inputs) == 1:
            return form, form.inputs[0].name
    return None


def _passkey_or_push_mode(forms: list[Form]) -> Form | None:
    """privacyIDEA's second-factor page, waiting for a passkey or a push confirmation."""
    return next((f for f in forms if f.value("mode") not in (None, "otp")), None)


def _factor_choice(forms: list[Form]) -> tuple[Form, str] | None:
    """A page asking which second factor to use: pick the authenticator app."""
    for form in forms:
        if form.inputs or len(form.buttons) < 2:
            continue
        for button in form.buttons:
            text = f"{button.name} {button.value} {button.label}".lower()
            if any(hint in text for hint in FACTOR_HINTS):
                return form, button.name
    return None


def _continue_only(forms: list[Form]) -> Form | None:
    """A page that only needs confirming, like "Loading Session Information"."""
    for form in forms:
        if not form.inputs and (len(form.buttons) <= 1 or form.button("_eventId_proceed")):
            return form
    return None


def _submit(
    http: httpx.Client,
    form: Form,
    values: dict[str, str | None] | None = None,
    button: str | None = None,
) -> httpx.Response:
    data = form.submission(values, button)
    if form.method == "POST":
        return http.post(form.action, data=post_data(data))
    return http.get(form.action, params=data)


def _describe(response: httpx.Response) -> str:
    where = response.url.copy_with(query=None)
    detail = (
        error_text(response.text) or page_title(response.text) or f"HTTP {response.status_code}"
    )
    return f"unexpected page at {where}: {detail}"


def _dump(debug_dir: Path, step: int, response: httpx.Response) -> None:
    """Save a page for troubleshooting, without the secrets it may contain."""
    soup = BeautifulSoup(response.text, "html.parser")
    for el in soup.find_all("input", attrs={"name": SECRET_FIELDS}):
        el["value"] = "[removed]"
    html = re.sub(r'("sesskey":")[^"]+', r"\1[removed]", str(soup))
    debug_dir.mkdir(parents=True, exist_ok=True)
    header = (
        f"<!-- {datetime.now().isoformat(timespec='seconds')} {response.status_code} "
        f"{response.url.copy_with(query=None)} -->\n"
    )
    (debug_dir / f"{step:02d}-{response.url.host}.html").write_text(header + html)
