"""Password hashing, JWT tokens, role permissions and webhook signatures."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
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
    "finance": {"owner", "admin", "accountant"},  # moving money (refunds, expenses, payouts): never an AI login
    "reports": {"owner", "admin", "accountant", "ai_agent"},
    "settings": {"owner", "admin"},
    "void": {"owner", "admin"},  # cancelling an invoice: management only
    "capital": {"owner", "admin"},  # the owner's own money, gold and dollars, the business's worth
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


def create_access_token(user_id: int, role: str, minutes: int | None = None, ver: int = 0) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "ver": ver,  # the user's token_version: a new password or deactivation ends every older login
        "iat": now,
        "exp": now + timedelta(minutes=minutes or s.access_token_minutes),
    }
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])


def has_permission(role: str, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, set())


def host_allowed(host_header: str | None) -> bool:
    """Guard against DNS rebinding: a web page whose own domain name points to this PC must not reach the API.
    Allowed: localhost, any IP address (127.0.0.1, ::1, the PC's LAN address) and names in HESABDAR_ALLOWED_HOSTS."""
    s = get_settings()
    h = (host_header or "").strip().lower()
    if not h:
        return True  # no Host header: not a browser, nothing to rebind
    if h.startswith("["):  # [::1]:8000
        h = h[1:].split("]", 1)[0]
    elif h.count(":") == 1:  # name:port
        h = h.rsplit(":", 1)[0]
    h = h.rstrip(".")
    if h == "localhost" or (h == "testserver" and s.environment == "test"):
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    extra = {x.strip().lower().rstrip(".") for x in (s.allowed_hosts or "").split(",") if x.strip()}
    return "*" in extra or h in extra


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
