"""Treasury (خزانه) of each service line per Jalali month: seen with the reports, set by the owner / admin."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..models import TreasuryMove
from ..services import treasury, treasury_fund
from ..services.jalali import jmonth_of
from ..models import local_now
from ..core.security import has_permission
from .deps import require

router = APIRouter(prefix="/api/treasury", tags=["treasury"])


class RateIn(BaseModel):
    line_id: int
    jy: int
    jm: int
    rate_bp: int  # hundredths of a percent: 4250 = 42.5%
    expected_bp: int | None = None  # the rate the page showed (someone else changed it meanwhile -> 409)
    confirm: bool = False  # staff already paid this month / a balance would go negative: confirmed after the preview


def _month(jy: int | None, jm: int | None) -> tuple[int, int]:
    if jy is None or jm is None:
        return jmonth_of(local_now())
    if not (1 <= jm <= 12) or not (1300 <= jy <= 1500):
        raise HTTPException(400, "ماه نامعتبر است")
    return jy, jm


@router.get("")
def rates(jy: int | None = None, jm: int | None = None, db: Session = Depends(get_db), _=Depends(require("reports"))):
    y, m = _month(jy, jm)
    return {"jy": y, "jm": m, "current": list(jmonth_of(local_now())), "lines": treasury.month_rates(db, y, m)}


@router.post("/preview")
def preview(body: RateIn, db: Session = Depends(get_db), _=Depends(require("settings"))):
    try:
        return treasury.preview(db, body.line_id, body.jy, body.jm, body.rate_bp)
    except treasury.TreasuryError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("")
def save(body: RateIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    try:
        out = treasury.save(db, body.line_id, body.jy, body.jm, body.rate_bp, body.expected_bp, body.confirm, user=user)
    except treasury.Conflict as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except (treasury.TreasuryError, ValueError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return out


# ------------------------------------------------------------------ the treasury as a fund of each line (1.23)
@router.get("/fund")
def fund(db: Session = Depends(get_db), _=Depends(require("reports"))):
    """Every line's treasury month by month: set aside, spent from it, the balance carried forward; per staff member."""
    return treasury_fund.fund(db)


@router.get("/fund/{line_id}")
def fund_line(line_id: int, db: Session = Depends(get_db), _=Depends(require("reports"))):
    return treasury_fund.line_staff_months(db, line_id or None)


@router.get("/spending")
def spending(jy: int | None = None, jm: int | None = None, line_id: int | None = None, db: Session = Depends(get_db),
             _=Depends(require("reports"))):
    y, m = _month(jy, jm)
    return {"jy": y, "jm": m, "rows": treasury_fund.spending(db, y, m, line_id)}


class MoveIn(BaseModel):
    kind: str  # opening (the balance when the program started) | spent_before (spent before the program started)
    line_id: int
    staff_id: int | None = None
    amount: int
    at: datetime | None = None
    category: str = ""
    description: str = ""


@router.post("/moves")
def add_move(body: MoveIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    if body.kind == "opening" and not has_permission(user.role, "settings"):
        raise HTTPException(403, "موجودی اول خزانه را مدیر ثبت می‌کند")
    try:
        m = treasury_fund.add_move(db, kind=body.kind, line_id=body.line_id, staff_id=body.staff_id, amount=body.amount, at=body.at,
                                   category=body.category, description=body.description, user=user)
    except (treasury_fund.FundError, ValueError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"ok": True, "id": m.id}


@router.post("/moves/{move_id}/void")
def void_move(move_id: int, db: Session = Depends(get_db), user=Depends(require("void"))):
    m = db.get(TreasuryMove, move_id)
    if m is None:
        raise HTTPException(404, "ردیف پیدا نشد")
    try:
        treasury_fund.void_move(db, m, user=user)
    except treasury_fund.FundError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"ok": True}
