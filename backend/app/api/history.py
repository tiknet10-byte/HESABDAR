"""Staff work of the previous software (1.23): each month's revenue, treasury and staff share, worked out like invoice
rows; booking a month owes the staff their share (Dr 3200 / Cr 2200); names not matched at import set to a staff member;
the day the program started."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..services import history_shares
from .deps import require

router = APIRouter(prefix="/api/history", tags=["history shares"])


def _bad(exc: Exception, db: Session):  # noqa: ANN202
    db.rollback()
    raise HTTPException(400, str(exc)) from exc


@router.get("/months")
def months(db: Session = Depends(get_db), _=Depends(require("reports"))):
    return history_shares.months(db)


class MonthIn(BaseModel):
    jy: int
    jm: int
    percents: dict[str, float] | None = None  # {staff id: percent of that month}; others: the month's / today's


@router.post("/month")
def month(body: MonthIn, db: Session = Depends(get_db), _=Depends(require("reports"))):
    """A month in detail (read only); with percents: what it would be with them."""
    try:
        return history_shares.month_detail(db, body.jy, body.jm, body.percents)
    except history_shares.HistoryError as exc:
        _bad(exc, db)


class BookIn(BaseModel):
    months: list[MonthIn]


@router.post("/book")
def book(body: BookIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    """Book (or book again) one or more months; each one that can't be booked says why (the rest are)."""
    done, errors = [], []
    for m in body.months:
        try:
            with db.begin_nested():
                hm = history_shares.book_month(db, m.jy, m.jm, m.percents, user=user)
            if hm is not None:
                done.append({"jy": m.jy, "jm": m.jm, "staff_share": hm.staff_share})
        except (history_shares.HistoryError, ValueError) as exc:
            errors.append({"jy": m.jy, "jm": m.jm, "error": str(exc)})
    db.commit()
    return {"ok": not errors, "done": done, "errors": errors}


class UnbookIn(BaseModel):
    jy: int
    jm: int


@router.post("/unbook")
def unbook(body: UnbookIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    try:
        history_shares.unbook_month(db, body.jy, body.jm, user=user)
    except (history_shares.HistoryError, ValueError) as exc:
        _bad(exc, db)
    db.commit()
    return {"ok": True}


@router.get("/staff-names")
def staff_names(db: Session = Depends(get_db), _=Depends(require("reports"))):
    return history_shares.staff_names(db)


class RelinkIn(BaseModel):
    name: str | None = None  # the name written in the previous software ...
    line_id: int | None = None  # ... or: the line's rows that have no staff member
    staff_id: int


@router.post("/relink")
def relink(body: RelinkIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    try:
        if body.name:
            n = history_shares.relink(db, body.name, body.staff_id, user=user)
        elif body.line_id:
            n = history_shares.assign_line(db, body.line_id, body.staff_id, user=user)
        else:
            raise history_shares.HistoryError("نام یا لاین را انتخاب کنید")
    except (history_shares.HistoryError, ValueError) as exc:
        _bad(exc, db)
    db.commit()
    return {"ok": True, "rows": n}


class StartIn(BaseModel):
    day: date | None = None  # None: the day of the first invoice / opening balance


@router.put("/start")
def set_start(body: StartIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    try:
        n = history_shares.set_start(db, body.day, user=user)
    except (history_shares.HistoryError, ValueError) as exc:
        _bad(exc, db)
    db.commit()
    start = history_shares.books_start(db)
    return {"ok": True, "start": start.date().isoformat() if start else None, "rebooked": n}
