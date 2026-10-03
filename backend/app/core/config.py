"""Application configuration loaded from environment variables / .env file."""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]  # backend/
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), env_prefix="HESABDAR_", extra="ignore")

    app_name: str = "حسابدار سالن"
    environment: str = "local"  # local | production

    # Database: SQLite for local mode, PostgreSQL URL for web/server mode
    database_url: str = f"sqlite:///{DATA_DIR / 'hesabdar.db'}"

    # Security
    secret_key: str = ""  # MUST be set in production; generated & persisted locally otherwise
    access_token_minutes: int = 60 * 8
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Webhooks from messaging gateways (SMS forwarder, WhatsApp, Instagram) are signed with this
    webhook_secret: str = ""

    # Backups
    backup_dir: Path = DATA_DIR / "backups"
    backup_key: str = ""  # passphrase used to encrypt backups; generated locally if empty
    backup_interval_hours: int = 6
    backup_keep: int = 60

    # Money: amounts are stored in Rial (integer). Display currency for UI.
    display_currency: str = "toman"

    # AI (optional) - uses the Anthropic SDK when a key/credential is available
    ai_enabled: bool = True
    ai_model: str = "claude-opus-5-5"

    # Plugins
    external_plugins_dir: Path = BASE_DIR / "plugins"

    # Frontend build to serve (local mode serves the SPA from the same port)
    frontend_dist: Path = BASE_DIR.parent / "frontend" / "dist"


def _persisted_secret(name: str) -> str:
    """Generate once and store a random secret in data/ so local installs are secure by default."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f".{name}"
    if path.exists():
        return path.read_text().strip()
    value = secrets.token_urlsafe(48)
    path.write_text(value)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.secret_key:
        if s.environment == "production":
            raise RuntimeError("HESABDAR_SECRET_KEY must be set in production")
        s.secret_key = _persisted_secret("secret_key")
    if not s.backup_key:
        s.backup_key = _persisted_secret("backup_key")
    if not s.webhook_secret:
        s.webhook_secret = _persisted_secret("webhook_secret")
    s.backup_dir.mkdir(parents=True, exist_ok=True)
    return s
