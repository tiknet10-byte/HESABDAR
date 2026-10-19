"""The owner's capital (put in / taken out), gold-coins-currency, and the business's worth at one glance."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..models import CapitalMove, Holding, HoldingMove, PaymentAccount, Purchase
from ..services import capital
from ..services.accounting import AccountingError
from .deps import require

router = APIRouter(prefix="/api/capital", tags=["capital"])


def _done(db: Session, fn):  # noqa: ANN001, ANN202
    try:
        out = fn()
        db.commit()
        return out
    except AccountingError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


def _get(db: Session, model, id_: int | None, label: str):  # noqa: ANN001, ANN202
    obj = db.get(model, id_) if id_ else None
    if obj is None:
        raise HTTPException(404, f"{label} پیدا نشد")
    return obj


@router.get("/position")
def position(start: date | None = None, end: date | None = None, db: Session = Depends(get_db), _=Depends(require("capital"))):
    out = capital.position(db, start, end)
    db.commit()  # accounts created on first use
    return out


class MoveIn(BaseModel):
    kind: str  # add | withdraw
    amount: int
    payment_account_id: int
    at: datetime | None = None
    notes: str = ""


@router.get("/moves")
def moves(limit: int = 200, db: Session = Depends(get_db), _=Depends(require("capital"))):
    rows = db.scalars(select(CapitalMove).order_by(CapitalMove.at.desc(), CapitalMove.id.desc()).limit(min(limit, 1000)))
    out = [capital.move_out(db, m) for m in rows] + capital.staff_from_pocket(db)
    return sorted(out, key=lambda m: m["at"], reverse=True)[:min(limit, 1000)]


@router.post("/moves")
def add_move(body: MoveIn, db: Session = Depends(get_db), user=Depends(require("capital"))):
    pa = _get(db, PaymentAccount, body.payment_account_id, "حساب")
    return _done(db, lambda: capital.move_out(db, capital.add_or_withdraw(db, kind=body.kind, amount=body.amount, payment_account=pa,
                                                                         at=body.at, notes=body.notes, user=user)))


class SupplierIn(BaseModel):
    purchase_id: int
    amount: int
    at: datetime | None = None
    notes: str = ""


@router.post("/supplier")
def owner_paid_supplier(body: SupplierIn, db: Session = Depends(get_db), user=Depends(require("capital"))):
    pur = _get(db, Purchase, body.purchase_id, "خرید")
    return _done(db, lambda: capital.move_out(db, capital.owner_paid_supplier(db, purchase=pur, amount=body.amount, at=body.at,
                                                                             notes=body.notes, user=user)))


@router.get("/convertible")
def convertible(q: str = "", db: Session = Depends(get_db), _=Depends(require("capital"))):
    return capital.convertible(db, q)


class ConvertIn(BaseModel):
    deposit_ids: list[int] = []
    purchase_ids: list[int] = []


@router.post("/convert")
def convert(body: ConvertIn, db: Session = Depends(get_db), user=Depends(require("capital"))):
    if not body.deposit_ids and not body.purchase_ids:
        raise HTTPException(400, "چیزی انتخاب نشده است")
    return _done(db, lambda: capital.convert(db, deposit_ids=body.deposit_ids, purchase_ids=body.purchase_ids, user=user))


class VoidIn(BaseModel):
    reason: str = ""


@router.post("/moves/{mid}/void")
def void_move(mid: int, body: VoidIn, db: Session = Depends(get_db), user=Depends(require("capital"))):
    m = _get(db, CapitalMove, mid, "گردش")
    return _done(db, lambda: capital.move_out(db, capital.void_move(db, m, body.reason, user=user)))


# ------------------------------------------------------------------ gold, coins, currency
class HoldingIn(BaseModel):
    name: str
    kind: str = "gold"
    unit: str = "گرم"
    notes: str = ""
    is_active: bool = True


@router.get("/holdings")
def holdings(db: Session = Depends(get_db), _=Depends(require("capital"))):
    return [capital.holding_out(db, h) for h in db.scalars(select(Holding).order_by(Holding.is_active.desc(), Holding.id))]


@router.post("/holdings")
def create_holding(body: HoldingIn, db: Session = Depends(get_db), user=Depends(require("capital"))):
    return _done(db, lambda: capital.holding_out(db, capital.create_holding(db, name=body.name, kind=body.kind, unit=body.unit,
                                                                           notes=body.notes, user=user)))


@router.put("/holdings/{hid}")
def update_holding(hid: int, body: HoldingIn, db: Session = Depends(get_db), _=Depends(require("capital"))):
    h = _get(db, Holding, hid, "دارایی")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "نام را بنویسید")
    if name != h.name and db.scalar(select(Holding).where(Holding.name == name, Holding.id != h.id)):
        raise HTTPException(400, "دارایی با همین نام وجود دارد")
    if not body.is_active and h.qty_m:
        raise HTTPException(400, "تا وقتی مقداری از آن دارید نمی‌شود غیرفعالش کرد")
    h.name, h.unit, h.notes, h.is_active = name, body.unit.strip() or h.unit, body.notes, body.is_active
    h.kind = body.kind if body.kind in capital.HOLDING_KINDS else h.kind
    db.commit()
    return capital.holding_out(db, h)


class PriceIn(BaseModel):
    price: int


@router.post("/holdings/{hid}/price")
def set_price(hid: int, body: PriceIn, db: Session = Depends(get_db), user=Depends(require("capital"))):
    h = _get(db, Holding, hid, "دارایی")
    return _done(db, lambda: capital.holding_out(db, capital.set_price(db, h, body.price, user=user)))


class HoldingMoveIn(BaseModel):
    kind: str  # opening | buy | sell | owner_in | owner_out
    qty: str  # "12.5"
    amount: int = 0
    payment_account_id: int | None = None
    at: datetime | None = None
    notes: str = ""


@router.get("/holdings/{hid}/moves")
def holding_moves(hid: int, db: Session = Depends(get_db), _=Depends(require("capital"))):
    _get(db, Holding, hid, "دارایی")
    rows = db.scalars(select(HoldingMove).where(HoldingMove.holding_id == hid).order_by(HoldingMove.at.desc(), HoldingMove.id.desc()))
    return [capital.holding_move_out(db, m) for m in rows]


@router.post("/holdings/{hid}/moves")
def add_holding_move(hid: int, body: HoldingMoveIn, db: Session = Depends(get_db), user=Depends(require("capital"))):
    h = _get(db, Holding, hid, "دارایی")
    pa = db.get(PaymentAccount, body.payment_account_id) if body.payment_account_id else None
    return _done(db, lambda: capital.holding_move_out(db, capital.holding_move(db, h, kind=body.kind, qty=body.qty, amount=body.amount,
                                                                              payment_account=pa, at=body.at, notes=body.notes,
                                                                              user=user)))


@router.post("/holding-moves/{mid}/void")
def void_holding_move(mid: int, body: VoidIn, db: Session = Depends(get_db), user=Depends(require("capital"))):
    m = _get(db, HoldingMove, mid, "گردش")
    return _done(db, lambda: capital.holding_move_out(db, capital.void_holding_move(db, m, body.reason, user=user)))
