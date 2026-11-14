"""Cryptographic helpers for staff time-based MFA and one-use recovery codes."""
import base64
import hashlib
import hmac
import io
import os
import secrets
import struct
import time
from urllib.parse import quote

import qrcode
from cryptography.fernet import Fernet, InvalidToken

from security import SECRET_KEY


def _fernet() -> Fernet:
    encryption_secret = os.getenv("SMARTLIB_MFA_ENCRYPTION_KEY") or SECRET_KEY
    key_material = hashlib.sha256(b"EVSU-SmartLib-MFA-v1:" + encryption_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key_material))


def encrypt_totp_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("ascii")).decode("ascii")


def decrypt_totp_secret(encrypted: str) -> str:
    try:
        return _fernet().decrypt(encrypted.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise ValueError("MFA encryption key is unavailable or has changed") from exc


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_code(secret: str, at: int | None = None) -> str:
    normalized = secret.upper().strip()
    key = base64.b32decode(normalized + "=" * ((8 - len(normalized) % 8) % 8))
    counter = int((time.time() if at is None else at) // 30)
    return _code_for_counter(key, counter)


def _code_for_counter(key: bytes, counter: int) -> str:
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{number:06d}"


def verify_totp(secret: str, submitted: str, at: int | None = None) -> bool:
    return matching_totp_counter(secret, submitted, at) is not None


def matching_totp_counter(secret: str, submitted: str, at: int | None = None) -> int | None:
    code = "".join(char for char in submitted if char.isdigit())
    if len(code) != 6 or not code.isascii():
        return None
    counter = int((time.time() if at is None else at) // 30)
    normalized = secret.upper().strip()
    key = base64.b32decode(normalized + "=" * ((8 - len(normalized) % 8) % 8))
    for candidate in (counter - 1, counter, counter + 1):
        if hmac.compare_digest(_code_for_counter(key, candidate), code):
            return candidate
    return None


def provisioning_qr(secret: str, account: str) -> str:
    label = quote(f"EVSU SmartLib:{account}", safe="")
    issuer = quote("EVSU SmartLib", safe="")
    uri = f"otpauth://totp/{label}?secret={quote(secret)}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
    image = qrcode.make(uri)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def new_recovery_codes(count: int = 10) -> list[str]:
    return [secrets.token_urlsafe(12).replace("-", "").replace("_", "") for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    normalized = "".join(char for char in code.casefold() if char.isalnum())
    return hashlib.sha256(b"EVSU-SmartLib-recovery-v1:" + normalized.encode("ascii", errors="ignore")).hexdigest()
