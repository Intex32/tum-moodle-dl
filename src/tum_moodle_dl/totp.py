"""Time-based one-time codes (RFC 6238), as shown by authenticator apps."""

import base64
import binascii
import hashlib
import hmac
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

ALGORITHMS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


@dataclass(frozen=True)
class Totp:
    key: bytes
    digits: int = 6
    period: int = 30
    algorithm: str = "SHA1"

    @classmethod
    def parse(cls, secret: str) -> "Totp":
        """Accept a base32 secret or an ``otpauth://totp/...`` link (the content of the QR code)."""
        secret = secret.strip()
        params: dict[str, str] = {}
        if secret.lower().startswith("otpauth://"):
            url = urlparse(secret)
            if url.netloc.lower() != "totp":
                raise ValueError("only time-based (totp) links are supported")
            params = {k.lower(): v[0] for k, v in parse_qs(url.query).items()}
            secret = params.get("secret", "")
        b32 = secret.replace(" ", "").replace("-", "").upper()
        if not b32:
            raise ValueError("the secret is empty")
        try:
            key = base64.b32decode(b32 + "=" * (-len(b32) % 8))
        except binascii.Error:
            raise ValueError("the secret is not valid base32") from None
        algorithm = params.get("algorithm", "SHA1").upper()
        if algorithm not in ALGORITHMS:
            raise ValueError(f"unsupported algorithm {algorithm}")
        try:
            return cls(key, int(params.get("digits", 6)), int(params.get("period", 30)), algorithm)
        except ValueError:
            raise ValueError("digits and period must be numbers") from None

    def at(self, timestamp: float) -> str:
        counter = int(timestamp // self.period).to_bytes(8, "big")
        digest = hmac.new(self.key, counter, ALGORITHMS[self.algorithm]).digest()
        offset = digest[-1] & 0x0F
        code = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
        return str(code % 10**self.digits).zfill(self.digits)

    def now(self) -> str:
        return self.at(time.time())

    def seconds_left(self) -> float:
        return self.period - time.time() % self.period
