import json
import time

import pytest
from fake_tum import FakeTum
from typer.testing import CliRunner

from tum_moodle_dl import cli, credentials
from tum_moodle_dl.totp import Totp

SECRET = "JBSWY3DPEHPK3PXP"


@pytest.fixture
def fake(monkeypatch):
    fake = FakeTum()
    monkeypatch.setattr(cli, "new_client", fake.client)
    return fake


@pytest.fixture
def tmdl():
    runner = CliRunner()

    def run(*args, input=None, ok=True):
        result = runner.invoke(cli.app, list(args), input=input)
        if ok:
            assert result.exit_code == 0, result.output
        return result

    return run


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config" / "tum-moodle-dl" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        f'root = "{tmp_path / "Moodle"}"\n\n[[courses]]\nid = 5\noriginal_filenames = true\n'
    )
    return path


def test_login_stores_details_and_session(fake, tmdl, isolated, tmp_path):
    tmdl("login", input="go42tum\ncorrect horse\n123456\n")
    assert credentials.get(credentials.USERNAME) == "go42tum"
    assert credentials.get(credentials.PASSWORD) == "correct horse"
    session = tmp_path / "state" / "tum-moodle-dl" / "session.json"
    assert session.stat().st_mode & 0o777 == 0o600
    assert any(c["name"] == "MoodleSession" for c in json.loads(session.read_text())["cookies"])


def test_wrong_password_is_not_stored(fake, tmdl):
    result = tmdl("login", input="go42tum\nwrong\n", ok=False)
    assert result.exit_code == 1
    assert "password you entered was incorrect" in result.output
    assert credentials.get(credentials.PASSWORD) is None


def test_courses_and_sync(fake, tmdl, config, tmp_path):
    tmdl("login", input="go42tum\ncorrect horse\n123456\n")

    result = tmdl("courses")
    assert "Analysis für Informatik" in result.output and "✓" in result.output

    result = tmdl("sync")
    assert "2 new, 0 updated, 0 unchanged" in result.output
    course = tmp_path / "Moodle" / "AfI WS25"
    assert (course / "Übungen/Blatt 1/blatt01.pdf").read_bytes() == b"%PDF sheet 1"
    sent = fake.download_requests[0]
    assert sent["filesrealnames"] == ["1"] and sent["submitbutton"] == ["ZIP-Archiv erstellen"]
    assert sent["item_resource_7"] == ["1"] and "addnumbering" not in sent
    assert fake.logins == 1  # the saved session was reused

    assert "0 new, 0 updated, 2 unchanged" in tmdl("sync").output


def test_sync_course_not_in_config(fake, tmdl, tmp_path):
    tmdl("login", input="go42tum\ncorrect horse\n123456\n")
    tmdl("sync", "5")
    assert (tmp_path / "home" / "Moodle" / "AfI WS25" / "Allgemeines").is_dir()
    assert "filesrealnames" not in fake.download_requests[0]


def test_expired_session_without_a_stored_secret_needs_a_terminal(fake, tmdl, config):
    tmdl("login", input="go42tum\ncorrect horse\n123456\n")
    fake.expire_sessions()
    result = tmdl("sync", ok=False)
    assert "no terminal to type it in" in result.output


def test_stored_secret_logs_in_unattended(fake, tmdl, config):
    fake.code = lambda: Totp.parse(SECRET).at(time.time())
    tmdl("login", "--totp-secret", input=f"go42tum\ncorrect horse\n{SECRET}\n")
    assert credentials.get(credentials.TOTP_SECRET) == SECRET

    fake.expire_sessions()
    result = tmdl("sync")
    assert "logging in again" in result.output and "2 new" in result.output
    assert fake.logins == 2


def test_wrong_stored_secret_gives_up_after_one_retry(fake, tmdl, config, monkeypatch):
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    tmdl("login", input="go42tum\ncorrect horse\n123456\n")
    credentials.store(credentials.TOTP_SECRET, SECRET)  # fake still wants 123456
    fake.expire_sessions()
    result = tmdl("sync", ok=False)
    assert "stored authenticator secret seems wrong" in result.output
    assert len(fake.code_submissions) == 1 + 2


def test_logout_forget(fake, tmdl):
    tmdl("login", input="go42tum\ncorrect horse\n123456\n")
    tmdl("logout", "--forget")
    assert fake.moodle_sessions == {}
    assert credentials.get(credentials.PASSWORD) is None
    assert "Not logged in" in tmdl("courses", ok=False).output


@pytest.fixture
def logged_in(fake, tmdl):
    tmdl("login", input="go42tum\ncorrect horse\n123456\n")


def config_text(tmp_path):
    return (tmp_path / "config" / "tum-moodle-dl" / "config.toml").read_text()


def test_add_courses_by_id_and_name(logged_in, tmdl, tmp_path):
    result = tmdl("courses", "add", "5", "lineare algebra")
    assert "Syncing Analysis für Informatik (5) → " in result.output
    assert f"{tmp_path}/home/Moodle/LA WS25" in result.output
    text = config_text(tmp_path)
    assert "id = 5 # Analysis für Informatik\n\n[[courses]]\nid = 6 # Lineare Algebra" in text

    listing = tmdl("courses").output
    assert listing.count("✓") == 2

    result = tmdl("sync")
    assert "Lineare Algebra für Informatik → " in result.output


def test_add_picks_the_exact_name_and_rejects_ambiguous_ones(logged_in, tmdl, tmp_path):
    result = tmdl("courses", "add", "analysis", ok=False)
    assert '"analysis" matches several courses' in result.output
    assert "  5  Analysis für Informatik" in result.output and "  7  Analysis 2" in result.output

    tmdl("courses", "add", "analysis 2")
    assert "id = 7 # Analysis 2" in config_text(tmp_path)
    assert "No course matches" in tmdl("courses", "add", "chemie", ok=False).output


def test_add_again_changes_settings(logged_in, tmdl, tmp_path):
    tmdl("courses", "add", "5")
    assert "Already syncing" in tmdl("courses", "add", "5").output
    result = tmdl("courses", "add", "5", "--dir", "analysis", "--original-filenames")
    assert f"Updated Analysis für Informatik (5) → {tmp_path}/home/Moodle/analysis" in result.output
    assert 'dir = "analysis"\noriginal_filenames = true' in config_text(tmp_path)
    assert "one course at a time" in tmdl("courses", "add", "5", "6", "--dir", "x", ok=False).output


def test_remove_keeps_the_rest_of_the_config(logged_in, tmdl, config, tmp_path):
    config.write_text(
        "# my settings\n"
        f'root = "{tmp_path / "Moodle"}"  # synced via iCloud\n\n'
        "[[courses]]\nid = 5\noriginal_filenames = true\n"
    )
    tmdl("courses", "add", "6", "7")
    assert "Stopped syncing course 5." in tmdl("courses", "remove", "5").output
    assert "Stopped syncing Analysis 2 (7)." in tmdl("courses", "remove", "analysis").output
    assert "was not being synced" in tmdl("courses", "remove", "5").output

    text = config_text(tmp_path)
    assert text.startswith("# my settings\n") and "# synced via iCloud" in text
    assert "id = 6 # Lineare Algebra" in text and "id = 5" not in text and "id = 7" not in text


def test_new_config_explains_itself(logged_in, tmdl, tmp_path):
    tmdl("courses", "add", "5")
    text = config_text(tmp_path)
    assert text.startswith("# tum-moodle-dl settings.")
    assert '# root = "~/Moodle"' in text


def test_courses_lists_the_newest_first(logged_in, tmdl):
    listing = tmdl("courses").output
    assert listing.index("Analysis 2") < listing.index("Lineare Algebra") < listing.index("AfI")


def test_without_two_factor_login_no_code_is_needed(monkeypatch, tmdl, config):
    fake = FakeTum(two_factor=False)
    monkeypatch.setattr(cli, "new_client", fake.client)
    result = tmdl("login", input="go42tum\ncorrect horse\n")
    assert "One-time code" not in result.output

    fake.expire_sessions()  # logging in again works unattended, without a stored secret
    result = tmdl("sync")
    assert "logging in again" in result.output and "2 new" in result.output
