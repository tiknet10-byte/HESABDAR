"""Import data from the previous salon software (Excel / CSV exports): preview -> commit -> (undo)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..models import ImportBatch
from ..services import legacy_import as li
from ..services.audit import audit
from .deps import require

router = APIRouter(prefix="/api/import/legacy", tags=["import"])
MAX_BYTES = 25 * 1024 * 1024


def _batch(db: Session, bid: int) -> ImportBatch:
    b = db.get(ImportBatch, bid)
    if b is None or not b.kind.startswith("legacy_"):
        raise HTTPException(404, "فایل انتقال یافت نشد")
    return b


def _out(b: ImportBatch, rows: bool = True) -> dict:
    s = b.summary or {}
    out = {"id": b.id, "file_name": b.file_name, "status": b.status, "created_at": b.created_at.isoformat(timespec="minutes"),
           **{k: v for k, v in s.items() if k != "created"}}
    if rows:
        bad = [r for r in b.rows if r.get("errors")]
        warn = [r for r in b.rows if not r.get("errors") and r.get("warnings")]
        out["sample"] = b.rows[:30]
        out["error_rows"] = bad[:200]
        out["warning_rows"] = warn[:200]
    return out


@router.get("")
def batches(db: Session = Depends(get_db), _=Depends(require("settings"))):
    q = select(ImportBatch).where(ImportBatch.kind.like("legacy_%")).order_by(ImportBatch.id.desc()).limit(50)
    return [_out(b, rows=False) for b in db.scalars(q)]


@router.get("/template")
def template(kind: str, _=Depends(require("settings"))):
    if kind not in li.TEMPLATES:
        raise HTTPException(400, "نوع نامعتبر")
    body = "﻿" + ",".join(li.TEMPLATES[kind]) + "\n"
    return StreamingResponse(iter([body]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=hesabdar-{kind}-template.csv"})


@router.post("/preview")
async def preview(file: UploadFile = File(...), kind: str = Form(...), unit: str = Form("rial"), mapping: str = Form(""),
                  db: Session = Depends(get_db), user=Depends(require("settings"))):
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(400, "حجم فایل بیش از ۲۵ مگابایت است؛ آن را در چند فایل جدا کنید")
    try:
        m = {k: v for k, v in json.loads(mapping).items()} if mapping else None
        b = li.preview(db, file.filename or "file", data, kind, "toman" if unit == "toman" else "rial", m)
    except (li.ImportProblem, json.JSONDecodeError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # broken / unsupported file
        db.rollback()
        raise HTTPException(400, f"فایل خوانده نشد: {exc}") from exc
    db.commit()
    return _out(b)


@router.get("/{bid}")
def get_batch(bid: int, db: Session = Depends(get_db), _=Depends(require("settings"))):
    return _out(_batch(db, bid))


class CommitIn(BaseModel):
    service_map: dict[str, int | str] = {}  # unknown service name -> existing service id | "new" | "skip"
    new_line_id: int | None = None
    payment_account_id: int | None = None


@router.post("/{bid}/commit")
def commit(bid: int, body: CommitIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    b = _batch(db, bid)
    try:
        result = li.commit(db, b, service_map=body.service_map, new_line_id=body.new_line_id,
                           payment_account_id=body.payment_account_id, user=user)
    except li.ImportProblem as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    audit(db, "legacy_import.commit", "import_batch", b.id, result, user=user)
    db.commit()
    return {**_out(b, rows=False), "result": result}


@router.post("/{bid}/undo")
def undo(bid: int, db: Session = Depends(get_db), user=Depends(require("settings"))):
    b = _batch(db, bid)
    try:
        result = li.undo(db, b)
    except li.ImportProblem as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    audit(db, "legacy_import.undo", "import_batch", b.id, result, user=user)
    db.commit()
    return result


@router.delete("/{bid}")
def discard(bid: int, db: Session = Depends(get_db), _=Depends(require("settings"))):
    b = _batch(db, bid)
    if b.status == "review":
        b.status = "discarded"
        b.rows = []
        db.commit()
    return {"ok": True}
