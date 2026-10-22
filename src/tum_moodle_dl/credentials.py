"""Login details kept in the system keychain (macOS Keychain, Secret Service, Windows vault)."""

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "tum-moodle-dl"
USERNAME = "username"
PASSWORD = "password"
TOTP_SECRET = "totp-secret"


def get(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)


def store(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def delete(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass
