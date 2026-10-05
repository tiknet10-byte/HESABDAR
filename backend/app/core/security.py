"""Password hashing, JWT tokens, role permissions and webhook signatures."""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .config import get_settings

_hasher = PasswordHasher()

ROLES = ("owner", "admin", "accountant", "receptionist", "staff", "ai_agent")

# permission -> roles allowed
PERMISSIONS: dict[str, set[str]] = {
    "read": {"owner", "admin", "accountant", "receptionist", "staff", "ai_agent"},
    "write": {"owner", "admin", "accountant", "receptionist", "ai_agent"},
    "finance": {"owner", "admin", "accountant", "ai_agent"},
    "reports": {"owner", "admin", "accountant", "ai_agent"},
    "settings": {"owner", "admin"},
    "users": {"owner"},
    "backup": {"owner", "admin"},
    "plugins": {"owner", "admin"},
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def validate_password_strength(password: str) -> None:
    if len(password) < 8:
        raise ValueError("رمز عبور باید حداقل ۸ کاراکتر باشد")
    if password.isdigit() or password.isalpha():
        raise ValueError("رمز عبور باید ترکیبی از حروف و اعداد باشد")


def create_access_token(user_id: int, role: str, minutes: int | None = None) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=minutes or s.access_token_minutes),
    }
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])


def has_permission(role: str, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, set())


def sign_payload(body: bytes, secret: str | None = None) -> str:
    key = (secret or get_settings().webhook_secret).encode()
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, signature: str | None, secret: str | None = None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(sign_payload(body, secret), signature)


def mask_card(number: str | None) -> str | None:
    """Never store full card numbers: keep first 6 (BIN, identifies bank) and last 4."""
    if not number:
        return number
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) < 10:
        return digits
    return f"{digits[:6]}******{digits[-4:]}"


def _secret_fernet():
    import base64

    from cryptography.fernet import Fernet

    key = hashlib.sha256(("hesabdar-secrets:" + get_settings().secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str) -> str:
    """Encrypt a secret (e.g. an AI API key) before storing it in the database."""
    return "enc:" + _secret_fernet().encrypt(value.encode()).decode() if value else ""


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    if not value.startswith("enc:"):
        return value
    try:
        return _secret_fernet().decrypt(value[4:].encode()).decode()
    except Exception:
        return ""
