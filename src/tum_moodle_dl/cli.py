"""The ``tmdl`` command."""

import sys
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, NoReturn

import httpx
import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import DownloadColumn, Progress, SpinnerColumn, TextColumn, TransferSpeedColumn
from rich.table import Table

from . import config, credentials
from .login import BadCredentials, BadOneTimeCode, CodeProvider, LoginError
from .moodle import Course, Moodle, MoodleError, load_cookies, new_client, save_cookies
from .sync import SyncReport, sync_zip
from .totp import Totp

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="Download your TUM Moodle courses."
)
# Don't hard-wrap messages: paths and names stay on one line, also in cron logs.
out = Console(soft_wrap=True)
err = Console(stderr=True, soft_wrap=True)

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        envvar="TMDL_CONFIG",
        help="Config file [default: ~/.config/tum-moodle-dl/config.toml]",
        show_default=False,
    ),
]


@app.command()
def login(
    username: Annotated[
        str | None, typer.Option("--username", "-u", help="Your TUM ID, e.g. go42tum.")
    ] = None,
    totp_secret: Annotated[
        bool,
        typer.Option(
            "--totp-secret",
            help="Store your authenticator app's secret so codes are generated for you. "
            "Needed for unattended runs, but it means one machine holds both login factors.",
        ),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Save every login page to a folder, for troubleshooting."),
    ] = False,
) -> None:
    """Log in with your TUM ID and remember the session."""
    stored_username = credentials.get(credentials.USERNAME)
    username = username or stored_username or typer.prompt("TUM ID (e.g. go42tum)")
    stored_password = credentials.get(credentials.PASSWORD) if username == stored_username else None
    password = stored_password or typer.prompt("Password", hide_input=True)
    secret = credentials.get(credentials.TOTP_SECRET)
    if totp_secret:
        secret = typer.prompt(
            "Authenticator secret (base32, or the otpauth:// link)", hide_input=True
        )

    debug_dir = None
    if debug:
        debug_dir = config.state_dir() / "debug" / datetime.now().strftime("%Y%m%d-%H%M%S")
    with new_client() as http:
        try:
            Moodle(http).login(username, password, _code_provider(secret, True), debug_dir)
        except BadCredentials as e:
            if stored_password:
                credentials.delete(credentials.PASSWORD)  # probably changed; ask next time
            _fail(f"Login failed: {e}")
        except (LoginError, ValueError, httpx.HTTPError) as e:
            hint = "" if debug else "\nRun `tmdl login --debug` to save the login pages."
            _fail(f"Login failed: {e}{hint}")
        finally:
            if debug_dir:
                err.print(f"Saved the login pages to {debug_dir}", markup=False)
        save_cookies(http, _session_file())

    credentials.store(credentials.USERNAME, username)
    credentials.store(credentials.PASSWORD, password)
    if totp_secret and secret:
        credentials.store(credentials.TOTP_SECRET, secret)
    out.print("Logged in. Your login details are in the system keychain.")


@app.command()
def logout(
    forget: Annotated[
        bool,
        typer.Option(
            "--forget",
            help="Also remove your TUM ID, password and authenticator secret from the keychain.",
        ),
    ] = False,
) -> None:
    """End the Moodle session."""
    with new_client() as http:
        load_cookies(http, _session_file())
        moodle = Moodle(http)
        try:
            if moodle.check_session():
                moodle.logout()
        except httpx.HTTPError:
            pass  # offline: dropping the session here is enough
    _session_file().unlink(missing_ok=True)
    if forget:
        for name in (credentials.USERNAME, credentials.PASSWORD, credentials.TOTP_SECRET):
            credentials.delete(name)
    out.print("Logged out." + (" Removed your login details from the keychain." if forget else ""))


courses_app = typer.Typer(help="List your courses and choose which ones to sync.")
app.add_typer(courses_app, name="courses")

CourseArgs = Annotated[
    list[str],
    typer.Argument(help="Course IDs, or parts of course names.", metavar="COURSE..."),
]


@courses_app.callback(invoke_without_command=True)
def courses(
    ctx: typer.Context,
    all_courses: Annotated[
        bool, typer.Option("--all", help="Include past and future courses.")
    ] = False,
    config_path: ConfigOption = None,
) -> None:
    """List your courses and choose which ones to sync."""
    if ctx.invoked_subcommand:
        return
    _, cfg = _load_config(config_path)
    enrolled = _enrolled("all" if all_courses else "inprogress")

    synced = {c.id for c in cfg.courses}
    table = Table("ID", "Course", "Short name", "Synced", box=None)
    # Courses without an end date count as current forever, so put the newest first.
    for course in sorted(enrolled, key=lambda c: -c.startdate):
        mark = "✓" if course.id in synced else ""
        table.add_row(str(course.id), escape(course.fullname), escape(course.shortname), mark)
    out.print(table)
    if not synced:
        out.print("\nChoose courses to sync with `tmdl courses add <ID or part of the name>`.")


@courses_app.command("add")
def courses_add(
    queries: CourseArgs,
    folder: Annotated[
        str | None,
        typer.Option(
            "--dir",
            help="Folder for the course, relative to root [default: its short name]",
            show_default=False,
        ),
    ] = None,
    original_filenames: Annotated[
        bool | None,
        typer.Option(
            "--original-filenames/--activity-names",
            help="Name files as uploaded, or after their activity [default: as in your config]",
            show_default=False,
        ),
    ] = None,
    config_path: ConfigOption = None,
) -> None:
    """Sync these courses from now on, or change their settings."""
    if folder and len(queries) > 1:
        _fail("--dir only works with one course at a time.")
    path, _ = _load_config(config_path)
    editor = _editor(path)
    enrolled = _enrolled("all")
    chosen = [_match(query, enrolled) for query in queries]
    added = [
        editor.add(
            course_id,
            course.fullname if course else None,
            folder=folder,
            original_filenames=original_filenames,
        )
        for course_id, course in chosen
    ]
    _save(editor)

    cfg = config.load(path)
    changed_settings = folder is not None or original_filenames is not None
    for (course_id, course), new in zip(chosen, added, strict=True):
        dest = cfg.destination(cfg.course(course_id), course.shortname if course else "")
        verb = "Syncing" if new else "Updated" if changed_settings else "Already syncing"
        out.print(f"{verb} {_label(course_id, course)} → {dest}", markup=False)
        if course is None:
            err.print(
                f"Course {course_id} is not one of your courses, so syncing it may fail.",
                style="yellow",
                markup=False,
            )


@courses_app.command("remove")
def courses_remove(queries: CourseArgs, config_path: ConfigOption = None) -> None:
    """Stop syncing these courses. Their folders stay as they are."""
    path, cfg = _load_config(config_path)
    editor = _editor(path)
    synced = {c.id for c in cfg.courses}
    # Names are only needed to find courses by name.
    names = [] if all(q.isdigit() for q in queries) else _enrolled("all")
    for query in queries:
        course_id, course = _match(query, [c for c in names if c.id in synced])
        if editor.remove(course_id):
            out.print(f"Stopped syncing {_label(course_id, course)}.", markup=False)
        else:
            err.print(f"{_label(course_id, course)} was not being synced.", markup=False)
    _save(editor)


@app.command()
def sync(
    course_ids: Annotated[
        list[int] | None,
        typer.Argument(
            help="Course IDs to sync [default: the courses in your config]", show_default=False
        ),
    ] = None,
    config_path: ConfigOption = None,
) -> None:
    """Download new and changed files into your course folders. Never deletes anything."""
    _, cfg = _load_config(config_path)
    targets = [cfg.course(i) for i in course_ids] if course_ids else cfg.courses
    if not targets:
        _fail(
            "No courses to sync yet. Choose some with `tmdl courses add <ID or part of the name>`."
        )

    failed = False
    with _moodle() as moodle:
        try:
            known = {c.id: c for c in moodle.courses("all")}
        except (MoodleError, httpx.HTTPError) as e:
            err.print(f"Could not look up course names ({e}); using IDs.", markup=False)
            known = {}
        for course in targets:
            info = known.get(course.id)
            name = info.fullname if info else f"Course {course.id}"
            dest = cfg.destination(course, info.shortname if info else "")
            try:
                report = _sync_course(
                    moodle, course.id, dest, cfg.uses_original_filenames(course), name
                )
            except (MoodleError, httpx.HTTPError, zipfile.BadZipFile, ValueError, OSError) as e:
                err.print(f"{name}: {e}", style="red", markup=False)
                failed = True
                continue
            out.print(f"{name} → {dest}: {report.summary()}", markup=False)
            for conflict in report.conflicts:
                out.print(
                    f"  You edited {conflict.local.relative_to(dest)}; "
                    f"Moodle's newer version is in {conflict.remote_copy.name}",
                    style="yellow",
                    markup=False,
                )
    if failed:
        raise typer.Exit(1)


def _sync_course(
    moodle: Moodle, course_id: int, dest: Path, original_filenames: bool, name: str
) -> SyncReport:
    with tempfile.TemporaryDirectory(prefix="tmdl-") as tmp:
        archive = Path(tmp) / "course.zip"
        with archive.open("wb") as f, _progress(name) as advance:
            moodle.download_center(
                course_id, f, original_filenames=original_filenames, progress=advance
            )
        return sync_zip(archive, dest)


@contextmanager
def _moodle() -> Iterator[Moodle]:
    """A logged-in Moodle; logs in again with the keychain details if the session expired."""
    with new_client() as http:
        load_cookies(http, _session_file())
        moodle = Moodle(http)
        try:
            if not moodle.check_session():
                username = credentials.get(credentials.USERNAME)
                password = credentials.get(credentials.PASSWORD)
                if not (username and password):
                    _fail("Not logged in. Run `tmdl login` first.")
                err.print("The Moodle session expired, logging in again…")
                secret = credentials.get(credentials.TOTP_SECRET)
                code_provider = _code_provider(secret, interactive=sys.stdin.isatty())
                moodle.login(username, password, code_provider)
                save_cookies(http, _session_file())
        except (LoginError, ValueError) as e:
            _fail(f"Login failed: {e}")
        except httpx.HTTPError as e:
            _fail(f"Could not reach TUM Moodle: {e}")
        try:
            yield moodle
        finally:
            save_cookies(http, _session_file())


def _code_provider(secret: str | None, interactive: bool) -> CodeProvider:
    totp = Totp.parse(secret) if secret else None

    retried = False

    def provide(previous_error: str | None) -> str:
        nonlocal retried
        if totp:
            if previous_error:
                if retried:
                    raise BadOneTimeCode(
                        f"{previous_error} The stored authenticator secret seems wrong; "
                        "store a new one with `tmdl login --totp-secret`."
                    )
                # Each code works only once, so a code used moments ago is rejected: wait.
                retried = True
                time.sleep(totp.seconds_left() + 1)
            return totp.now()
        if not interactive:
            raise LoginError(
                "TUM asks for a one-time code, but there is no terminal to type it in. "
                "Run `tmdl login`, or store your authenticator secret with "
                "`tmdl login --totp-secret`."
            )
        if previous_error:
            err.print(previous_error, style="red", markup=False)
        return typer.prompt("One-time code from your authenticator app").replace(" ", "")

    return provide


@contextmanager
def _progress(label: str) -> Iterator[Callable[[int], None]]:
    columns = (SpinnerColumn(), TextColumn("{task.description}"), DownloadColumn())
    with Progress(*columns, TransferSpeedColumn(), console=err, transient=True) as progress:
        task = progress.add_task(escape(label), total=None)
        yield lambda n: progress.advance(task, n)


def _enrolled(classification: str) -> list[Course]:
    with _moodle() as moodle:
        try:
            return moodle.courses(classification)
        except (MoodleError, httpx.HTTPError) as e:
            _fail(f"Could not list your courses: {e}")


def _match(query: str, courses: list[Course]) -> tuple[int, Course | None]:
    """The course an ID or a part of its name refers to."""
    if query.isdigit():
        course_id = int(query)
        return course_id, next((c for c in courses if c.id == course_id), None)
    needle = query.casefold()
    found = [
        c for c in courses if needle in c.fullname.casefold() or needle in c.shortname.casefold()
    ]
    exact = [c for c in found if needle in (c.fullname.casefold(), c.shortname.casefold())]
    if len(exact) == 1:
        return exact[0].id, exact[0]
    if len(found) == 1:
        return found[0].id, found[0]
    if not found:
        _fail(f'No course matches "{query}". `tmdl courses --all` lists all of yours.')
    matches = "\n".join(f"  {c.id}  {c.fullname}" for c in found)
    _fail(f'"{query}" matches several courses, so use the ID:\n{matches}')


def _label(course_id: int, course: Course | None) -> str:
    return f"{course.fullname} ({course_id})" if course else f"course {course_id}"


def _editor(path: Path) -> config.ConfigEditor:
    try:
        return config.ConfigEditor(path)
    except config.ConfigError as e:
        _fail(f"Config error: {e}")


def _save(editor: config.ConfigEditor) -> None:
    try:
        editor.save()
    except config.ConfigError as e:
        _fail(f"Could not update the config: {e}")


def _load_config(path: Path | None) -> tuple[Path, config.Config]:
    path = path or config.default_config_path()
    try:
        return path, config.load(path)
    except config.ConfigError as e:
        _fail(f"Config error: {e}")


def _session_file() -> Path:
    return config.state_dir() / "session.json"


def _fail(message: str) -> NoReturn:
    err.print(message, style="red", markup=False)
    raise typer.Exit(1)
