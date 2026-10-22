import pytest
from fake_tum import SAML_RESPONSE, FakeTum, _html

from tum_moodle_dl.login import BadCredentials, BadOneTimeCode, LoginError
from tum_moodle_dl.moodle import Moodle


def login(fake: FakeTum, password="correct horse", codes=("123456",), debug_dir=None):
    asked: list[str | None] = []
    supply = iter(codes)

    def one_time_code(previous_error):
        asked.append(previous_error)
        return next(supply)

    moodle = Moodle(fake.client())
    moodle.login("go42tum", password, one_time_code, debug_dir)
    return moodle, asked


@pytest.mark.parametrize("code_field", ["autocomplete", "hinted", "only-field"])
def test_login_with_one_time_code(code_field):
    fake = FakeTum(code_field=code_field)
    moodle, asked = login(fake)
    assert moodle.sesskey in fake.moodle_sessions.values()
    assert asked == [None]
    assert fake.code_submissions[0][1] == "123456"


def test_privacyidea_code_page():
    fake = FakeTum(code_field="privacyidea")
    moodle, asked = login(fake)
    assert moodle.sesskey and asked == [None]
    assert fake.code_submissions == [("otp", "123456")]
    assert fake.remembered_device == [True]


def test_privacyidea_switches_from_passkey_to_code_entry():
    fake = FakeTum(code_field="privacyidea", pi_mode="passkey")
    moodle, _ = login(fake)
    assert moodle.sesskey
    assert fake.code_submissions == [("otp", "123456")]


class PasskeyMode(FakeTum):
    def _code_page(self, error=""):
        self.pi_mode = "passkey"  # ignores the switch to code entry
        return super()._code_page(error)


def test_privacyidea_without_code_entry():
    with pytest.raises(LoginError, match="Add an authenticator app"):
        login(PasskeyMode(code_field="privacyidea"))


def test_picks_the_authenticator_app_when_asked_for_a_second_factor():
    fake = FakeTum(factor_choice=True)
    moodle, _ = login(fake)
    assert moodle.sesskey


def test_wrong_password():
    with pytest.raises(BadCredentials, match="password you entered was incorrect"):
        login(FakeTum(), password="wrong")


def test_asks_again_after_a_rejected_code():
    fake = FakeTum()
    moodle, asked = login(fake, codes=("000000", "123456"))
    assert moodle.sesskey
    assert asked == [None, "Invalid one-time password."]


def test_gives_up_after_three_rejected_codes():
    with pytest.raises(BadOneTimeCode, match="Invalid one-time password"):
        login(FakeTum(), codes=("1", "2", "3", "4"))


def test_reuses_the_identity_provider_session():
    fake = FakeTum()
    moodle, _ = login(fake)
    fake.moodle_sessions.clear()
    assert not moodle.check_session()

    moodle.login("go42tum", "correct horse", lambda _: pytest.fail("asked for a code"))
    assert moodle.check_session()
    assert fake.logins == 2


class PasskeyOnly(FakeTum):
    def _code_page(self, error=""):
        return _html(
            "<html><head><title>Use your passkey</title></head><body><form>"
            "<button name='_eventId_passkey'>Passkey</button>"
            "<button name='_eventId_cancel'>Cancel</button></form></body></html>"
        )


def test_unexpected_page():
    with pytest.raises(LoginError, match="unexpected page .*: Use your passkey"):
        login(PasskeyOnly())


def test_debug_pages_leave_out_secrets(tmp_path):
    login(FakeTum(), debug_dir=tmp_path)
    pages = sorted(tmp_path.iterdir())
    assert any("Loading Session Information" in p.read_text() for p in pages)
    assert all(SAML_RESPONSE not in p.read_text() for p in pages)
    assert all('"sesskey":"[removed]"' in p.read_text() for p in pages if "M.cfg" in p.read_text())
