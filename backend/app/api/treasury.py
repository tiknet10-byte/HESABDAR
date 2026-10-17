"""Treasury (خزانه) of each service line per Jalali month: seen with the reports, set by the owner / admin."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..services import treasury
from ..services.jalali import jmonth_of
from ..models import local_now
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
