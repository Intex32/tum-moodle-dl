import pytest

from tum_moodle_dl.totp import Totp

# RFC 6238 appendix B, SHA-1, secret "12345678901234567890".
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.parametrize(
    ("timestamp", "code"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
    ],
)
def test_rfc_6238_vectors(timestamp, code):
    assert (
        Totp.parse(f"otpauth://totp/TUM:go42tum?secret={RFC_SECRET}&digits=8").at(timestamp) == code
    )


def test_six_digit_codes_from_a_plain_secret():
    assert Totp.parse(RFC_SECRET.lower()).at(59) == "287082"


def test_spaces_and_missing_padding_are_fine():
    assert Totp.parse("gezd gnbv gy3t qojq").key == b"1234567890"


@pytest.mark.parametrize("secret", ["", "not base32!", "otpauth://hotp/x?secret=GEZDGNBV"])
def test_rejects_bad_secrets(secret):
    with pytest.raises(ValueError):
        Totp.parse(secret)
