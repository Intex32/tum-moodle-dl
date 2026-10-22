"""Talk to TUM Moodle with a logged-in web session."""

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from . import __version__
from .forms import error_text, page_title, parse_forms, post_data
from .login import MOODLE_URL, CodeProvider, LoginError, shibboleth_login

# Moodle builds the whole archive before it sends the first byte.
DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, read=900.0)
LOGGED_OUT_ERRORS = {"servicerequireslogin", "requireloginerror", "invalidsesskey"}


class MoodleError(Exception):
    pass


class NotLoggedIn(MoodleError):
    pass


@dataclass(frozen=True)
class Course:
    id: int
    shortname: str
    fullname: str
    startdate: int = 0  # unix time


def new_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=30.0,
        headers={"User-Agent": f"tum-moodle-dl/{__version__}"},
    )


def save_cookies(http: httpx.Client, path: Path) -> None:
    http.cookies.jar.clear_expired_cookies()
    cookies = [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
        for c in http.cookies.jar
    ]
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"cookies": cookies}, f)


def load_cookies(http: httpx.Client, path: Path) -> None:
    try:
        cookies = json.loads(path.read_text())["cookies"]
    except (FileNotFoundError, ValueError, KeyError):
        return
    for c in cookies:
        http.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])


class Moodle:
    def __init__(self, http: httpx.Client):
        self.http = http
        self.sesskey: str | None = None

    def check_session(self) -> bool:
        """Whether we are logged in; also picks up the session key needed for most requests."""
        response = self.http.get(f"{MOODLE_URL}/my/")
        match = re.search(r'"sesskey":"([^"]+)"', response.text)
        # Logged-out visitors are sent to the login page or the front page instead.
        if response.status_code == 200 and response.url.path.startswith("/my") and match:
            self.sesskey = match.group(1)
            return True
        self.sesskey = None
        return False

    def login(
        self,
        username: str,
        password: str,
        one_time_code: CodeProvider,
        debug_dir: Path | None = None,
    ) -> None:
        response = shibboleth_login(self.http, username, password, one_time_code, debug_dir)
        if not self.check_session():
            detail = error_text(response.text) or page_title(response.text) or str(response.url)
            raise LoginError(f"the TUM login worked, but Moodle did not log us in: {detail}")

    def logout(self) -> None:
        if self.sesskey:
            self.http.get(f"{MOODLE_URL}/login/logout.php", params={"sesskey": self.sesskey})
            self.sesskey = None

    def call(self, method: str, **args: Any) -> Any:
        """Call one of the web service functions Moodle's own pages use."""
        response = self.http.post(
            f"{MOODLE_URL}/lib/ajax/service.php",
            params={"sesskey": self.sesskey or "", "info": method},
            json=[{"index": 0, "methodname": method, "args": args}],
        )
        response.raise_for_status()
        result = response.json()
        # Errors before the call is dispatched come back as a single object.
        error = result if isinstance(result, dict) else result[0].get("exception")
        if error:
            if error.get("errorcode") in LOGGED_OUT_ERRORS:
                raise NotLoggedIn("the Moodle session has expired")
            raise MoodleError(error.get("message") or error.get("error") or str(error))
        return result[0]["data"]

    def courses(self, classification: str = "all") -> list[Course]:
        """Enrolled courses; ``classification`` is all, inprogress, future or past."""
        data = self.call(
            "core_course_get_enrolled_courses_by_timeline_classification",
            classification=classification,
            offset=0,
            limit=0,
            sort="fullname",
        )
        return [
            Course(c["id"], c["shortname"], c["fullname"], c.get("startdate") or 0)
            for c in data["courses"]
        ]

    def download_center(
        self,
        course_id: int,
        out: BinaryIO,
        *,
        original_filenames: bool = False,
        progress: Callable[[int], None] | None = None,
    ) -> None:
        """Write a zip of everything the course's Download Center offers to ``out``."""
        page = self.http.get(
            f"{MOODLE_URL}/local/downloadcenter/index.php", params={"courseid": course_id}
        )
        if page.url.path.startswith("/login"):
            raise NotLoggedIn("the Moodle session has expired")
        forms = parse_forms(page.text, str(page.url))
        form = next((f for f in forms if f.has("_qf__local_downloadcenter_download_form")), None)
        if form is None:
            detail = error_text(page.text) or page_title(page.text) or f"HTTP {page.status_code}"
            raise MoodleError(f"no Download Center for course {course_id}: {detail}")

        # Every section and activity is ticked by default; we only choose the file names.
        options = {"filesrealnames": "1" if original_filenames else None}
        data = post_data(form.submission(options, button="submitbutton"))
        with self.http.stream("POST", form.action, data=data, timeout=DOWNLOAD_TIMEOUT) as response:
            response.raise_for_status()
            if "html" in response.headers.get("content-type", ""):
                response.read()
                detail = error_text(response.text) or page_title(response.text)
                raise MoodleError(f"Moodle sent a page instead of a zip: {detail}")
            for chunk in response.iter_bytes():
                out.write(chunk)
                if progress:
                    progress(len(chunk))
