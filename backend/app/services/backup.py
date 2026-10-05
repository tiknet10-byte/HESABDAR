"""Encrypted, verified backups with one-click restore.

* Online SQLite snapshot (safe while the app is running) -> gzip -> AES (Fernet) encryption.
* A SHA-256 checksum of the plaintext database is stored in a manifest and verified on restore.
* `PRAGMA integrity_check` is run on both the snapshot and the restored file.
* Before every restore a safety backup of the current state is taken automatically.
* Optional mirror directory (USB drive / Google Drive / Dropbox synced folder) for off-site copies.
* Retention: keeps the newest N backups.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from ..core import db as dbmod
from ..core.config import get_settings
from ..core.events import bus

MAGIC = b"HSBK1"


class BackupError(RuntimeError):
    pass


def _fernet(salt: bytes, passphrase: str | None = None) -> Fernet:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=390_000)
    key = base64.urlsafe_b64encode(kdf.derive((passphrase or get_settings().backup_key).encode()))
    return Fernet(key)


def _sqlite_path() -> Path:
    url = str(dbmod.engine.url)
    if not url.startswith("sqlite"):
        raise BackupError("پشتیبان‌گیری داخلی فقط برای SQLite است؛ برای PostgreSQL از pg_dump استفاده کنید (docs/BACKUP.md)")
    return Path(dbmod.engine.url.database)


def _tempdir():  # noqa: ANN202
    return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


def _copy_db(src: Path, dst: Path) -> None:
    """Online, consistent copy of a SQLite database (works while the app is running)."""
    with closing(sqlite3.connect(src, timeout=30)) as a, closing(sqlite3.connect(dst, timeout=30)) as b:
        a.backup(b)


def _integrity_ok(path: Path) -> bool:
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()


def create_backup(reason: str = "manual", mirror_dir: str | None = None) -> dict:
    s = get_settings()
    src = _sqlite_path()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    name = f"hesabdar-{stamp}"
    # NB: `with sqlite3.connect()` does not close the file; on Windows an open file cannot be deleted,
    # so every connection is closed explicitly before the temp folder is removed.
    with _tempdir() as tmp:
        snap = Path(tmp) / "snap.db"
        _copy_db(src, snap)
        if not _integrity_ok(snap):
            raise BackupError("integrity check of snapshot failed")
        raw = snap.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    salt = os.urandom(16)
    token = _fernet(salt).encrypt(gzip.compress(raw, compresslevel=9))
    file = s.backup_dir / f"{name}.hbk"
    file.write_bytes(MAGIC + salt + token)
    manifest = {"name": name, "file": file.name, "created_at": datetime.now().isoformat(timespec="seconds"),
                "reason": reason, "sha256": digest, "db_size": len(raw), "size": file.stat().st_size, "encrypted": True}
    (s.backup_dir / f"{name}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    if mirror_dir:
        try:
            Path(mirror_dir).mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, Path(mirror_dir) / file.name)
            shutil.copy2(s.backup_dir / f"{name}.json", Path(mirror_dir) / f"{name}.json")
            manifest["mirrored"] = True
        except OSError as exc:
            manifest["mirror_error"] = str(exc)
    _apply_retention()
    bus.emit("backup.created", manifest)
    return manifest


def list_backups() -> list[dict]:
    out = []
    for m in sorted(get_settings().backup_dir.glob("*.json"), reverse=True):
        try:
            out.append(json.loads(m.read_text()))
        except (OSError, ValueError):
            continue
    return out


def _decrypt(file: Path, passphrase: str | None = None) -> bytes:
    blob = file.read_bytes()
    if not blob.startswith(MAGIC):
        raise BackupError("فایل پشتیبان معتبر نیست")
    salt, token = blob[len(MAGIC):len(MAGIC) + 16], blob[len(MAGIC) + 16:]
    try:
        return gzip.decompress(_fernet(salt, passphrase).decrypt(token))
    except InvalidToken as exc:
        raise BackupError("کلید رمزگشایی اشتباه است یا فایل دستکاری شده") from exc


def verify_backup(name: str, passphrase: str | None = None) -> dict:
    s = get_settings()
    manifest = json.loads((s.backup_dir / f"{name}.json").read_text())
    raw = _decrypt(s.backup_dir / manifest["file"], passphrase)
    ok_hash = hashlib.sha256(raw).hexdigest() == manifest["sha256"]
    with _tempdir() as tmp:
        p = Path(tmp) / "v.db"
        p.write_bytes(raw)
        ok_integrity = _integrity_ok(p)
    return {"name": name, "checksum_ok": ok_hash, "integrity_ok": ok_integrity, "ok": ok_hash and ok_integrity}


def restore_backup(name: str, passphrase: str | None = None, file: Path | None = None) -> dict:
    s = get_settings()
    if file is None:
        manifest = json.loads((s.backup_dir / f"{name}.json").read_text())
        file = s.backup_dir / manifest["file"]
        expected = manifest["sha256"]
    else:
        expected = None
    raw = _decrypt(file, passphrase)
    if expected and hashlib.sha256(raw).hexdigest() != expected:
        raise BackupError("checksum mismatch - backup is corrupted")
    with _tempdir() as tmp:
        restored = Path(tmp) / "restore.db"
        restored.write_bytes(raw)
        if not _integrity_ok(restored):
            raise BackupError("integrity check failed on backup content")
        safety = create_backup(reason=f"before-restore:{name}")
        dbmod.engine.dispose()
        _copy_db(restored, _sqlite_path())
        dbmod.engine.dispose()
    return {"restored": name, "safety_backup": safety["name"]}


def _apply_retention() -> None:
    s = get_settings()
    manifests = sorted(s.backup_dir.glob("*.json"), reverse=True)
    for m in manifests[s.backup_keep:]:
        try:
            data = json.loads(m.read_text())
            (s.backup_dir / data["file"]).unlink(missing_ok=True)
            m.unlink(missing_ok=True)
        except (OSError, ValueError):
            continue
