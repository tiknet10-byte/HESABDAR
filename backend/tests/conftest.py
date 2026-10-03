import os
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="hesabdar-test-")
os.environ["HESABDAR_DATABASE_URL"] = f"sqlite:///{Path(_tmp) / 'test.db'}"
os.environ["HESABDAR_BACKUP_DIR"] = str(Path(_tmp) / "backups")
os.environ["HESABDAR_ENVIRONMENT"] = "test"
os.environ["HESABDAR_SECRET_KEY"] = "test-secret-key-for-unit-tests-only-0123456789"
os.environ["HESABDAR_WEBHOOK_SECRET"] = "hook-secret"
os.environ["HESABDAR_AI_ENABLED"] = "false"
os.environ["HESABDAR_FRONTEND_DIST"] = str(Path(_tmp) / "no-dist")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        r = c.post("/api/auth/setup", json={"username": "owner", "password": "Secret123", "full_name": "مدیر", "salon_name": "سالن تست"})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield c


@pytest.fixture()
def accounts(client):
    return {a["name"]: a["id"] for a in client.get("/api/accounts").json()}


@pytest.fixture()
def services(client):
    return {s["name"]: s for s in client.get("/api/services").json()}
