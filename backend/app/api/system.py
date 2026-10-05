"""Reports, AI, alerts, settings, plugins, backups, audit and learning endpoints."""
from __future__ import annotations

import csv
import logging
import io
import shutil
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import assistant, claude
from ..ai.tools import invoke, schema, tools_for
from ..core.db import get_db
from ..models import Alert, AuditLog
from ..plugins.manager import manager
from ..services import backup, learning, matching, reports, settings_store
from ..services.audit import audit, verify_audit_chain
from .deps import current_user, require

router = APIRouter(prefix="/api", tags=["system"])
log = logging.getLogger("hesabdar.system")


# ---------------------------------------------------------------- reports
@router.get("/reports/summary")
def summary(start: date | None = None, end: date | None = None, db: Session = Depends(get_db), _=Depends(require("reports"))):
    return reports.summary(db, start, end)


@router.get("/reports/daily")
def daily(start: date | None = None, end: date | None = None, db: Session = Depends(get_db), _=Depends(require("reports"))):
    return reports.daily_series(db, start, end)


@router.get("/reports/staff-shares")
def staff_shares(start: date | None = None, end: date | None = None, db: Session = Depends(get_db), _=Depends(require("reports"))):
    return reports.staff_shares(db, start, end)


@router.get("/reports/forecast")
def forecast(days: int = 30, db: Session = Depends(get_db), _=Depends(require("reports"))):
    return reports.forecast(db, min(days, 180))


@router.get("/reports/insights")
def insights(db: Session = Depends(get_db), _=Depends(require("reports"))):
    return {"insights": reports.insights(db), "customers": reports.customer_rfm(db, 30)}


@router.get("/reports/export.csv")
def export_csv(start: date | None = None, end: date | None = None, db: Session = Depends(get_db), _=Depends(require("reports"))):
    rows = reports.invoice_item_rows(db, start, end)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["number", "date", "customer", "service", "qty", "unit_price", "discount"])
    w.writeheader()
    w.writerows(rows)
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=sales.csv"})


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user=Depends(current_user)):
    today = date.today()
    out = {"today": reports.summary(db, today, today), "reconciliation": matching.unreconciled_summary(db),
           "now": __import__("datetime").datetime.now().isoformat(timespec="minutes"),
           "alerts": [{"id": a.id, "level": a.level, "title": a.title, "message": a.message, "at": a.at.isoformat()}
                      for a in db.scalars(select(Alert).where(Alert.is_read.is_(False)).order_by(Alert.at.desc()).limit(8))],
           "ai_available": claude.available()}
    from ..core.security import has_permission
    if has_permission(user.role, "reports"):
        out["month"] = reports.summary(db)
        out["series"] = reports.daily_series(db)
        out["insights"] = reports.insights(db)
    return out


# ---------------------------------------------------------------- alerts
@router.get("/alerts")
def alerts(db: Session = Depends(get_db), _=Depends(require("read"))):
    return [{"id": a.id, "level": a.level, "kind": a.kind, "title": a.title, "message": a.message, "at": a.at.isoformat(),
             "is_read": a.is_read, "ref_type": a.ref_type, "ref_id": a.ref_id}
            for a in db.scalars(select(Alert).order_by(Alert.at.desc()).limit(200))]


@router.post("/alerts/{aid}/read")
def read_alert(aid: int, db: Session = Depends(get_db), _=Depends(require("read"))):
    a = db.get(Alert, aid)
    if a:
        a.is_read = True
        db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- AI
class ChatIn(BaseModel):
    messages: list[dict]


class InvokeIn(BaseModel):
    arguments: dict = {}


@router.post("/ai/chat")
def ai_chat(body: ChatIn, db: Session = Depends(get_db), user=Depends(current_user)):
    try:
        return assistant.chat(db, user, body.messages[-30:])
    except Exception as exc:  # surface AI/network problems as a friendly message
        raise HTTPException(502, f"خطا در ارتباط با هوش مصنوعی: {exc}") from exc


@router.get("/ai/tools")
def ai_tools(user=Depends(current_user)):
    """Tool definitions in Anthropic/MCP-compatible JSON schema form - for any external AI agent."""
    return [schema(t) | {"permission": t.permission} for t in tools_for(user)]


@router.post("/ai/tools/{name}")
def ai_invoke(name: str, body: InvokeIn, db: Session = Depends(get_db), user=Depends(current_user)):
    try:
        result = invoke(db, user, name, body.arguments)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(400, f"invalid arguments: {exc}") from exc
    audit(db, "ai.tool", "tool", name, {"args": body.arguments}, user=user)
    db.commit()
    return result


@router.get("/ai/knowledge-export")
def knowledge_export(db: Session = Depends(get_db), _=Depends(require("reports"))):
    """Everything the system has learned, for syncing to an external AI / RAG store."""
    from ..models import KnowledgeItem
    return {"services": reports.services_catalog(db), "settings": {k: v for k, v in settings_store.all_settings(db).items() if k.startswith("salon.")},
            "knowledge": [{"kind": k.kind, "key": k.key, "value": k.value} for k in db.scalars(select(KnowledgeItem).limit(20000))]}


@router.post("/learning/retrain")
def retrain(db: Session = Depends(get_db), user=Depends(require("settings"))):
    out = learning.retrain(db)
    audit(db, "learning.retrain", data=out, user=user)
    db.commit()
    return out


# ---------------------------------------------------------------- settings
SECRET_KEYS = {"ai.api_key"}


def _public_settings(db: Session) -> dict:
    out = settings_store.all_settings(db)
    for k in SECRET_KEYS:
        out[k] = claude.MASK if out.get(k) else ""
    return out


@router.get("/settings")
def get_settings_(db: Session = Depends(get_db), _=Depends(require("read"))):
    return _public_settings(db)


@router.put("/settings")
def put_settings(body: dict, db: Session = Depends(get_db), user=Depends(require("settings"))):
    from ..core.security import encrypt_secret
    for k, v in body.items():
        if k in SECRET_KEYS:
            if v == claude.MASK:
                continue  # unchanged
            v = encrypt_secret(str(v).strip())
        if k in settings_store.DEFAULTS or k.startswith("custom."):
            settings_store.set_value(db, k, v)
    audit(db, "settings.update", data={k: ("***" if k in SECRET_KEYS else v) for k, v in body.items()}, user=user)
    db.commit()
    return _public_settings(db)


@router.post("/ai/test")
def ai_test(_=Depends(require("settings"))):
    if not claude.available():
        return {"ok": False, "error": "کلید API وارد نشده یا بسته anthropic نصب نیست"}
    return claude.test_connection()


# ---------------------------------------------------------------- plugins
class PluginConfigIn(BaseModel):
    enabled: bool | None = None
    config: dict | None = None


@router.get("/plugins")
def plugins(_=Depends(require("read"))):
    out = []
    for p in manager.list():
        secret_keys = {k for k, v in (p["config_schema"].get("properties") or {}).items() if v.get("secret")}
        p["config"] = {k: ("••••" if k in secret_keys and v else v) for k, v in p["config"].items()}
        out.append(p)
    return out


@router.put("/plugins/{name}")
def plugin_update(name: str, body: PluginConfigIn, db: Session = Depends(get_db), user=Depends(require("plugins"))):
    if name not in manager.plugins:
        raise HTTPException(404, "افزونه یافت نشد")
    if body.config is not None:
        current = manager.plugins[name].ctx.config
        merged = {k: (current.get(k) if v == "••••" else v) for k, v in body.config.items()}
        manager.set_config(name, merged)
    if body.enabled is not None:
        manager.set_enabled(name, body.enabled)
    audit(db, "plugin.update", "plugin", name, {"enabled": body.enabled}, user=user)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- backups
@router.get("/backups")
def backups(_=Depends(require("backup"))):
    return backup.list_backups()


@router.post("/backups")
def create_backup(db: Session = Depends(get_db), user=Depends(require("backup"))):
    try:
        m = backup.create_backup("manual", settings_store.get(db, "backup.mirror_dir"))
    except backup.BackupError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit(db, "backup.create", "backup", m["name"], user=user)
    db.commit()
    return m


@router.post("/backups/{name}/verify")
def verify(name: str, _=Depends(require("backup"))):
    try:
        return backup.verify_backup(name)
    except (backup.BackupError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/backups/{name}/restore")
def restore(name: str, user=Depends(require("users"))):  # owner only
    try:
        out = backup.restore_backup(name)
    except (backup.BackupError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    from ..core.db import SessionLocal
    with SessionLocal() as db:
        audit(db, "backup.restore", "backup", name, out, user=user, actor=user.username)
        db.commit()
    return out


@router.post("/backups/upload-restore")
async def upload_restore(file: UploadFile = File(...), passphrase: str = Form(""), user=Depends(require("users"))):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        p = Path(tmp) / "upload.hbk"
        with p.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        try:
            return backup.restore_backup(file.filename or "upload", passphrase or None, file=p)
        except backup.BackupError as exc:
            raise HTTPException(400, str(exc)) from exc


@router.get("/backups/{name}/download")
def download(name: str, _=Depends(require("backup"))):
    from ..core.config import get_settings
    if "/" in name or ".." in name:
        raise HTTPException(400)
    f = get_settings().backup_dir / f"{name}.hbk"
    if not f.exists():
        raise HTTPException(404)
    return StreamingResponse(f.open("rb"), media_type="application/octet-stream",
                             headers={"Content-Disposition": f"attachment; filename={f.name}"})


# ---------------------------------------------------------------- audit
@router.get("/audit")
def audit_log(limit: int = 200, db: Session = Depends(get_db), _=Depends(require("settings"))):
    return [{"id": a.id, "at": a.at.isoformat(), "actor": a.actor, "action": a.action, "entity": a.entity, "entity_id": a.entity_id,
             "data": a.data} for a in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 2000)))]


@router.get("/audit/verify")
def audit_verify(db: Session = Depends(get_db), _=Depends(require("settings"))):
    return verify_audit_chain(db)


# ---------------------------------------------------------------- data management (owner only)
class ResetIn(BaseModel):
    scope: str
    password: str
    confirm: str  # must be the word "حذف"


@router.get("/admin/data")
def data_stats(db: Session = Depends(get_db), _=Depends(require("users"))):
    from ..services import maintenance
    return {"stats": maintenance.stats(db), "scopes": maintenance.SCOPES}


@router.post("/admin/optimize")
def optimize_db(user=Depends(require("users"))):
    from ..services import maintenance
    return maintenance.optimize()


@router.post("/admin/reset")
def reset_data(body: ResetIn, db: Session = Depends(get_db), user=Depends(require("users"))):
    from ..core.security import verify_password
    from ..services import maintenance
    if body.confirm.strip() != "حذف":
        raise HTTPException(400, "برای تأیید، کلمه «حذف» را تایپ کنید")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(403, "رمز عبور اشتباه است")
    if body.scope not in maintenance.SCOPES:
        raise HTTPException(400, "نوع پاک‌سازی نامعتبر است")
    try:
        safety = backup.create_backup(f"before-reset:{body.scope}")["name"]
    except Exception as exc:  # never wipe data without a safety copy
        log.exception("safety backup before reset failed")
        raise HTTPException(500, f"پشتیبان ایمنی ساخته نشد؛ برای حفظ اطلاعات، پاک‌سازی انجام نشد ({type(exc).__name__}: {exc})") from exc
    try:
        counts = maintenance.reset(db, body.scope)
        audit(db, "admin.reset", "database", body.scope, {"deleted": counts, "safety_backup": safety}, user=user)
        db.commit()
    except Exception as exc:
        db.rollback()
        log.exception("reset failed")
        raise HTTPException(500, f"پاک‌سازی انجام نشد و هیچ اطلاعاتی حذف نشد ({type(exc).__name__}: {exc})") from exc
    try:
        maintenance.optimize()
    except Exception:  # noqa: BLE001 - optimizing is best effort
        pass
    return {"ok": True, "deleted": counts, "safety_backup": safety}


@router.post("/admin/demo")
def load_demo(db: Session = Depends(get_db), user=Depends(require("users"))):
    """Fill the database with realistic sample data for testing."""
    from ..seed import seed_demo
    out = seed_demo(db)
    audit(db, "admin.demo", data=out, user=user)
    db.commit()
    return out
