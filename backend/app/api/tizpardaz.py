"""Import from Tizpardaz (the clinic's product accounting software): preview -> choices -> commit -> (undo)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..models import ImportBatch
from ..services import routing, settings_store, tizpardaz as tp
from ..services.audit import audit
from ..services.legacy_import import ImportProblem
from .deps import require

router = APIRouter(prefix="/api/import/tizpardaz", tags=["import"])
MAX_BYTES = 25 * 1024 * 1024


def _batch(db: Session, bid: int) -> ImportBatch:
    b = db.get(ImportBatch, bid)
    if b is None or not b.kind.startswith("tp_"):
        raise HTTPException(404, "فایل انتقال پیدا نشد")
    return b


def _out(b: ImportBatch, rows: bool = True) -> dict:
    s = b.summary or {}
    out = {"id": b.id, "file_name": b.file_name, "status": b.status, "created_at": b.created_at.isoformat(timespec="minutes"),
           **{k: v for k, v in s.items() if k not in ("created", "mapping", "docs")}}
    if rows:
        out["rows"] = b.rows[:3000]
    return out


@router.get("")
def batches(db: Session = Depends(get_db), _=Depends(require("settings"))):
    q = select(ImportBatch).where(ImportBatch.kind.like("tp_%")).order_by(ImportBatch.id.desc()).limit(50)
    return [_out(b, rows=False) for b in db.scalars(q)]


@router.get("/sku-map")
def get_sku_map(db: Session = Depends(get_db), _=Depends(require("settings"))):
    text = tp.sku_map_text(db)
    return {"text": text, "count": len(tp.parse_sku_map(text))}


class SkuIn(BaseModel):
    text: str


@router.put("/sku-map")
def save_sku_map(body: SkuIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    settings_store.set_value(db, "tizpardaz.sku_map", body.text)
    audit(db, "tizpardaz.sku_map", "settings", "tizpardaz.sku_map", {"count": len(tp.parse_sku_map(body.text))}, user=user)
    db.commit()
    return get_sku_map(db)


@router.post("/preview")
async def preview(file: UploadFile = File(...), kind: str = Form(...), unit: str = Form("toman"), sku_text: str = Form(""),
                  db: Session = Depends(get_db), _=Depends(require("settings"))):
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(400, "حجم فایل بیش از ۲۵ مگابایت است")
    try:
        b = tp.preview(db, file.filename or "file", data, kind, "rial" if unit == "rial" else "toman", sku_text)
    except ImportProblem as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # broken / unsupported file
        db.rollback()
        raise HTTPException(400, f"فایل خوانده نشد: {exc}") from exc
    db.commit()
    return _out(b)


@router.get("/seller-credits")
def seller_credits(db: Session = Depends(get_db), _=Depends(require("settings"))):
    """Sellers whose Tizpardaz credit balance was brought over as a customer deposit (it is what we owe them)."""
    return tp.seller_credits(db)


class IdsIn(BaseModel):
    deposit_ids: list[int]


@router.post("/seller-credits")
def fix_seller_credits(body: IdsIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    try:
        out = tp.credit_to_payable(db, body.deposit_ids, user=user)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return out


@router.get("/{bid}")
def get_batch(bid: int, db: Session = Depends(get_db), _=Depends(require("settings"))):
    return _out(_batch(db, bid))


class CommitIn(BaseModel):
    choices: dict[str, int | str] = {}  # customers: row -> customer id | "new" | "skip"; products: row -> "p:id" | "s:id" | "new" | "skip"
    balances: bool = True  # customers: bring the debit / credit balances over as opening balances
    at: datetime | None = None  # date of the balances / stock
    account_id: int | None = None  # customers' credit balances are kept as open deposits on this account
    product_map: dict[str, int | str] = {}  # journal: unknown product name -> product id | "skip"
    customer_map: dict[str, int | str] = {}  # journal: unknown customer -> customer id | "new" | "skip"
    credit_as: dict[str, str] = {}  # customers: row -> "deposit" (customer's prepaid credit) | "payable" (we owe this seller)


@router.post("/{bid}/commit")
def commit(bid: int, body: CommitIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    b = _batch(db, bid)
    if b.status != "review":
        raise HTTPException(400, "این فایل قبلاً ثبت یا کنار گذاشته شده است")
    kind = b.kind[3:]
    try:
        if kind == "customers":
            result = tp.commit_customers(db, b, {str(k): v for k, v in body.choices.items()}, balances=body.balances, at=body.at,
                                         account_id=body.account_id or routing.default_account(db, "deposits"), user=user,
                                         credit_as={str(k): v for k, v in body.credit_as.items()})
        elif kind == "products":
            result = tp.commit_products(db, b, {str(k): str(v) for k, v in body.choices.items()}, at=body.at, user=user)
        else:
            result = tp.commit_journal(db, b, body.product_map, body.customer_map, user=user)
    except (ImportProblem, ValueError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    b.status = "committed"
    b.rows = b.rows[:3000]
    audit(db, "tizpardaz.commit", "import_batch", b.id, {"kind": kind, **result}, user=user)
    db.commit()
    return {**_out(b, rows=False), "result": result}


@router.post("/{bid}/undo")
def undo(bid: int, db: Session = Depends(get_db), user=Depends(require("settings"))):
    b = _batch(db, bid)
    try:
        result = tp.undo(db, b)
    except ImportProblem as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    audit(db, "tizpardaz.undo", "import_batch", b.id, result, user=user)
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
