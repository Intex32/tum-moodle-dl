"""A stand-in for login.tum.de and www.moodle.tum.de, served through httpx.MockTransport.

The login pages copy the structure of the real identity provider's pages.
"""

import io
import json
import secrets
import zipfile
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx

MOODLE = "https://www.moodle.tum.de"
SSO = "https://login.tum.de/idp/profile/SAML2/Redirect/SSO"
SAML_RESPONSE = "PHNhbWxwOlJlc3BvbnNlIHNlY3JldD0ieWVzIi8+"

LOADING_PAGE = """<html><head><title>TUM Login - Loading Session Information</title></head><body>
<form name="form1" action="/idp/profile/SAML2/Redirect/SSO?execution=e1s1" method="post">
<input type="hidden" name="csrf_token" value="_csrf1" />
<input name="shib_idp_ls_exception.shib_idp_session_ss" type="hidden" />
<input name="shib_idp_ls_success.shib_idp_session_ss" type="hidden" value="false" />
<input name="shib_idp_ls_supported" type="hidden" />
<input name="_eventId_proceed" type="hidden" />
<input type="submit" value="Continue" />
</form></body></html>"""

LOGIN_PAGE = """<html><head><title>TUM Shibboleth IdP - Login</title></head><body>
{error}
<form action="/idp/profile/SAML2/Redirect/SSO?execution=e1s2" method="post">
<input type="hidden" name="csrf_token" value="_csrf2" />
<input class="form-element form-field" id="username" name="j_username" type="text"
 placeholder="e.g. go42tum / example@tum.de" autocomplete="username" required="required"/>
<input class="form-element form-field" id="password" name="j_password" type="password"
 autocomplete="current-password" required="required" />
<input type="checkbox" name="donotcache-dummy" id="donotcache-dummy" value="1" disabled="disabled" />
<input type="hidden" name="donotcache" id="donotcache" value="1" />
<input id="_shib_idp_revokeConsent" type="checkbox" name="_shib_idp_revokeConsent" value="true" />
<button id="btnLogin" class="btnLogin" type="submit" name="_eventId_proceed">Login</button>
<input id="passkeyLoginRequested" name="initialPasskeyLoginRequested" value="0" type="hidden">
<button id="btnPasskeyNk" class="btnLoginPasskey" type="submit" name="_eventId_passkey">Passkey</button>
</form></body></html>"""

FACTOR_PAGE = """<html><head><title>TUM Login - Second factor</title></head><body>
<form action="/idp/profile/SAML2/Redirect/SSO?execution=e1s3" method="post">
<input type="hidden" name="csrf_token" value="_csrf3" />
<button type="submit" name="_eventId_passkey">Passkey</button>
<button type="submit" name="_eventId_totp">Authenticator app</button>
</form></body></html>"""

CODE_FIELDS = {
    "autocomplete": '<input name="j_value" type="text" autocomplete="one-time-code" />',
    "hinted": '<input id="otp-input" name="otp" type="text" inputmode="numeric" />',
    "only-field": '<input name="fudis_value" type="tel" />',
}
CODE_PAGE = """<html><head><title>TUM Login - Verification</title></head><body>
{error}
<form action="/idp/profile/SAML2/Redirect/SSO?execution=e1s4" method="post">
<input type="hidden" name="csrf_token" value="_csrf4" />
{field}
<button type="submit" name="_eventId_proceed">Verify</button>
<button type="submit" name="_eventId_cancel">Cancel</button>
</form></body></html>"""

# privacyIDEA's Shibboleth plugin (views/main.vm), trimmed.
PI_PAGE = """<html><head><title>TUM Login - privacyIDEA</title></head><body>
<form id="privacyidea-form" action="/idp/profile/SAML2/Redirect/SSO?execution=e1s4" method="post" autocomplete="off">
<input type="hidden" name="csrf_token" value="_csrf4" />
<div class="content">{body}
<div class="form-options">
<input id="mode" name="mode" value='{mode}' type="hidden">
<input id="isEnrollViaMultichallenge" name="isEnrollViaMultichallenge" value='{enroll}' type="hidden">
<input id="errorMessage" name="errorMessage" value="" type="hidden">
<input id="passkeyChallenge" name="passkeyChallenge" value='{challenge}' type="hidden">
<input id="passkeySignResponse" name="passkeySignResponse" value="" type="hidden">
<input id="selectedSerial" name="selectedSerial" value="" type="hidden">
</div></div></form></body></html>"""
PI_OTP_BODY = """<div class="form-element-wrapper" id="tokenList"><h3 id="tokenListHeader">Choose a token</h3>
<div class="pi-token"><span class="pi-token-type">passkey</span>
<button type="submit" class="form-element-button pi-token-use" name="_eventId_proceed"
 data-serial="PIPK0001" data-type="passkey">Use</button></div>
<div class="pi-token"><span class="pi-token-type">totp</span></div></div>
<div class="form-element-wrapper" id="one-time-password">{error}
<input id="otp" class="form-element form-field" name="otp" type="password" autocomplete="one-time-code"
 data-1p-ignore="true" autofocus value="" aria-label="One-Time-Password"><br>
<div id="rememberMe"><input id="pidea_remember_me" name="pidea_remember_me" value="1" type="checkbox">
<label for="pidea_remember_me">Remember this device</label></div>
<button class="form-element-button" id="pi-form-submit-button" type="submit" name="_eventId_proceed">Submit</button>
</div>"""
PI_PASSKEY_BODY = """<div class="form-element-wrapper" id="messages-and-images"><p>Use your passkey.</p></div>
<div class="form-element-wrapper" id="passkeyActive"><script>passkeyAuthentication()</script>
<input class="form-element-button" id="passkeyButton" type="button" name="passkeyButton" value="Passkey"></div>
<button class="form-element-button" id="otpButton" type="button">One-Time-Password</button>"""

SAML_PAGE = f"""<html><body onload="document.forms[0].submit()">
<form action="{MOODLE}/Shibboleth.sso/SAML2/POST" method="post">
<input type="hidden" name="RelayState" value="cookie:1792691625_2641"/>
<input type="hidden" name="SAMLResponse" value="{SAML_RESPONSE}"/>
<noscript><input type="submit" value="Continue"/></noscript>
</form></body></html>"""

MOODLE_PAGE = """<html><head><title>{title}</title><script>
M.cfg = {{"wwwroot":"https:\\/\\/www.moodle.tum.de","sesskey":"{sesskey}","sessiontimeout":"28800"}};
</script></head><body>{body}</body></html>"""

DOWNLOAD_CENTER_PAGE = """<form autocomplete="off" action="https://www.moodle.tum.de/local/downloadcenter/index.php"
 method="post" accept-charset="utf-8" id="mform1" class="mform" data-double-submit-protection="off">
<input name="courseid" type="hidden" value="{course_id}" />
<input name="sesskey" type="hidden" value="{sesskey}" />
<input name="_qf__local_downloadcenter_download_form" type="hidden" value="1" />
<input type="checkbox" name="item_topic_101" class="form-check-input" value="1" id="id_item_topic_101" checked>
<input type="checkbox" name="item_resource_7" class="form-check-input" value="1" id="id_item_resource_7" checked>
<input type="checkbox" name="item_folder_8" class="form-check-input" value="1" id="id_item_folder_8" checked>
<input type="checkbox" name="createindex" class="form-check-input" value="1" id="id_createindex" checked>
<input type="checkbox" name="filesrealnames" class="form-check-input" value="1" id="id_filesrealnames">
<input type="checkbox" name="addnumbering" class="form-check-input" value="1" id="id_addnumbering">
<input type="submit" class="btn btn-primary" name="submitbutton" id="id_submitbutton" value="ZIP-Archiv erstellen">
<input type="submit" class="btn btn-secondary" name="cancel" id="id_cancel" value="Abbrechen">
</form>"""


class FakeTum:
    def __init__(
        self,
        username: str = "go42tum",
        password: str = "correct horse",
        code: Callable[[], str] = lambda: "123456",
        code_field: str = "autocomplete",  # a CODE_FIELDS key, or "privacyidea"
        pi_mode: str = "otp",
        pi_enroll: bool = False,  # privacyIDEA asks to set up a token instead
        two_factor: bool = True,
        factor_choice: bool = False,
        courses: dict[int, tuple[str, str]] | None = None,
        files: dict[str, bytes] | None = None,
    ):
        self.username, self.password, self.code = username, password, code
        self.code_field, self.factor_choice = code_field, factor_choice
        self.pi_mode, self.pi_enroll, self.two_factor = pi_mode, pi_enroll, two_factor
        self.remembered_device: list[bool] = []
        self.courses = courses or {
            5: ("Analysis für Informatik", "AfI WS25"),
            6: ("Lineare Algebra für Informatik", "LA WS25"),
            7: ("Analysis 2", "Ana2 SS25"),
        }
        self.files = files or {
            "Allgemeines/Folien/vorlesung-01.pdf": b"%PDF slides 1",
            "Übungen/Blatt 1/blatt01.pdf": b"%PDF sheet 1",
        }
        self.idp_sessions: set[str] = set()
        self.moodle_sessions: dict[str, str] = {}  # MoodleSession -> sesskey
        self.code_submissions: list[tuple[str, str]] = []
        self.download_requests: list[dict[str, list[str]]] = []
        self.logins = 0

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle), follow_redirects=True)

    def expire_sessions(self) -> None:
        self.idp_sessions.clear()
        self.moodle_sessions.clear()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.tum.de":
            return self._idp(request)
        if request.url.host == "www.moodle.tum.de":
            return self._moodle(request)
        return httpx.Response(404)

    # login.tum.de

    def _idp(self, request: httpx.Request) -> httpx.Response:
        execution = request.url.params.get("execution")
        form = _form(request) if request.method == "POST" else {}
        value = lambda name: form.get(name, [""])[0]

        if execution is None and request.url.params.get("SAMLRequest"):
            cookie = request.headers.get("cookie", "")
            if any(f"shib_idp_session={s}" in cookie for s in self.idp_sessions):
                return _html(SAML_PAGE)
            return httpx.Response(302, headers={"Location": f"{SSO}?execution=e1s1"})
        if execution == "e1s1":
            if request.method == "GET":
                return _html(LOADING_PAGE)
            assert "_eventId_proceed" in form and value("csrf_token") == "_csrf1"
            return _html(LOGIN_PAGE.format(error=""))
        if execution == "e1s2":
            assert "_eventId_proceed" in form and value("csrf_token") == "_csrf2"
            assert "_eventId_passkey" not in form
            assert value("donotcache") == ""  # "keep me logged in" is ticked
            if (value("j_username"), value("j_password")) != (self.username, self.password):
                error = '<section><p class="form-element form-error">The password you entered was incorrect.</p></section>'
                return _html(LOGIN_PAGE.format(error=error))
            if not self.two_factor:
                return self._authenticated()
            return _html(FACTOR_PAGE) if self.factor_choice else self._code_page()
        if execution == "e1s3":
            assert "_eventId_totp" in form
            return self._code_page()
        if execution == "e1s4":
            assert "_eventId_proceed" in form and "_eventId_cancel" not in form
            if self.code_field == "privacyidea":
                if "otp" not in form:  # what the "One-Time-Password" button sends
                    assert value("mode") == "otp" and value("_eventId_proceed") == "proceed"
                    self.pi_mode = "otp"
                    return self._code_page()
                assert value("selectedSerial") == ""  # not the passkey token's "Use" button
                self.remembered_device.append(value("pidea_remember_me") == "1")
                field = "otp"
            else:
                field = next(n for n in form if n not in ("csrf_token", "_eventId_proceed"))
            self.code_submissions.append((field, value(field)))
            if value(field) != self.code():
                return self._code_page(error="Invalid one-time password.")
            return self._authenticated()
        return httpx.Response(404)

    def _authenticated(self) -> httpx.Response:
        session = secrets.token_hex(8)
        self.idp_sessions.add(session)
        response = _html(SAML_PAGE)
        response.headers["Set-Cookie"] = f"shib_idp_session={session}; Path=/idp; Secure"
        return response

    def _code_page(self, error: str = "") -> httpx.Response:
        error_html = f'<p class="output--error">{error}</p>' if error else ""
        if self.code_field != "privacyidea":
            return _html(CODE_PAGE.format(error=error_html, field=CODE_FIELDS[self.code_field]))
        if self.pi_mode == "otp":
            body, challenge = PI_OTP_BODY.format(error=error_html), ""
        else:
            body, challenge = PI_PASSKEY_BODY, "eyJjaGFsbGVuZ2UiOiJ4In0"
        enroll = str(self.pi_enroll).lower()
        return _html(
            PI_PAGE.format(body=body, mode=self.pi_mode, challenge=challenge, enroll=enroll)
        )

    # www.moodle.tum.de

    def _moodle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        session = request.headers.get("cookie", "")
        sesskey = next(
            (k for s, k in self.moodle_sessions.items() if f"MoodleSession={s}" in session), None
        )
        if path == "/Shibboleth.sso/Login":
            assert request.url.params["providerId"] == "https://tumidp.lrz.de/idp/shibboleth"
            return httpx.Response(302, headers={"Location": f"{SSO}?SAMLRequest=fVJN&RelayState=x"})
        if path == "/Shibboleth.sso/SAML2/POST":
            assert _form(request)["SAMLResponse"] == [SAML_RESPONSE]
            return httpx.Response(
                302,
                headers={
                    "Location": f"{MOODLE}/auth/shibboleth/index.php",
                    "Set-Cookie": "_shibsession_64656661756c74=abc; Path=/; Secure; HttpOnly",
                },
            )
        if path == "/auth/shibboleth/index.php":
            assert "_shibsession_64656661756c74=abc" in session
            moodle_session = secrets.token_hex(8)
            self.moodle_sessions[moodle_session] = secrets.token_hex(5)
            self.logins += 1
            return httpx.Response(
                303,
                headers={
                    "Location": f"{MOODLE}/my/",
                    "Set-Cookie": f"MoodleSession={moodle_session}; Path=/; Secure; HttpOnly",
                },
            )
        if path == "/login/index.php":
            return _html(MOODLE_PAGE.format(title="Login", sesskey="guest", body="TUM LOGIN"))
        if sesskey is None:
            if path == "/lib/ajax/service.php":
                return httpx.Response(
                    200, json={"error": "Login required", "errorcode": "servicerequireslogin"}
                )
            return httpx.Response(303, headers={"Location": f"{MOODLE}/login/index.php"})
        if path == "/my/":
            return _html(MOODLE_PAGE.format(title="Dashboard", sesskey=sesskey, body="Hi"))
        if path == "/login/logout.php":
            assert request.url.params["sesskey"] == sesskey
            self.moodle_sessions = {s: k for s, k in self.moodle_sessions.items() if k != sesskey}
            return httpx.Response(303, headers={"Location": f"{MOODLE}/"})
        if path == "/lib/ajax/service.php":
            return self._ajax(request, sesskey)
        if path == "/local/downloadcenter/index.php":
            return self._download_center(request, sesskey)
        return httpx.Response(404)

    def _ajax(self, request: httpx.Request, sesskey: str) -> httpx.Response:
        call = json.loads(request.content)[0]
        if request.url.params["sesskey"] != sesskey:
            error = {"message": "Invalid sesskey", "errorcode": "invalidsesskey"}
            return httpx.Response(200, json=[{"error": True, "exception": error}])
        assert call["methodname"] == request.url.params["info"]
        assert call["methodname"] == "core_course_get_enrolled_courses_by_timeline_classification"
        courses = [
            {"id": i, "fullname": full, "shortname": short, "startdate": 1_700_000_000 + i}
            for i, (full, short) in self.courses.items()
        ]
        return httpx.Response(200, json=[{"error": False, "data": {"courses": courses}}])

    def _download_center(self, request: httpx.Request, sesskey: str) -> httpx.Response:
        if request.method == "GET":
            course_id = int(request.url.params["courseid"])
            page = DOWNLOAD_CENTER_PAGE.format(course_id=course_id, sesskey=sesskey)
            return _html(MOODLE_PAGE.format(title="Download Center", sesskey=sesskey, body=page))
        form = _form(request)
        assert form["sesskey"] == [sesskey] and "cancel" not in form
        self.download_requests.append(form)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            for name, data in self.files.items():
                zf.writestr(name, data)
        return httpx.Response(
            200, content=buffer.getvalue(), headers={"Content-Type": "application/zip"}
        )


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode(), keep_blank_values=True)


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"Content-Type": "text/html; charset=utf-8"})
