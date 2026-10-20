"""Paying staff and employees (1.22): advances during the month, the month-end settlement / salary, the receipt read
to fill the form, each person's month, and the employees' salaries."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.security import has_permission
from ..models import PaymentAccount, SalaryAccrual, Staff, StaffPayout, local_now
from ..services import payout_receipt, staffpay
from ..services.jalali import jmonth_of
from .deps import require

router = APIRouter(prefix="/api", tags=["staff pay"])


def _404(what: str = "رکورد"):  # noqa: ANN202
    raise HTTPException(404, f"{what} پیدا نشد")


def _month(jy: int | None, jm: int | None) -> tuple[int, int]:
    if jy is None or jm is None:
        return jmonth_of(local_now())
    if not (1 <= jm <= 12) or not (1300 <= jy <= 1500):
        raise HTTPException(400, "ماه نامعتبر است")
    return jy, jm


def _accounts(db: Session) -> dict[int, str]:
    return {pa.id: pa.name for pa in db.scalars(select(PaymentAccount))}


# ------------------------------------------------------------------ paying someone
class PayoutIn(BaseModel):
    amount: int
    kind: str = "settlement"  # advance (برداشتی / مساعده) | settlement (تسویه / پرداخت حقوق)
    payment_account_id: int | None = None
    from_owner: bool = False  # the owner paid it from their own pocket (their capital grows)
    paid_at: datetime | None = None
    jy: int | None = None  # the month it is for (default: the month it was paid in)
    jm: int | None = None
    reference: str = ""
    description: str = ""
    receipt_token: str | None = None  # the receipt read by /staff/payout-receipt (its picture is kept with the payout)
    receipt: dict | None = None  # what was read from it
    force: bool = False  # it looked like a payment already entered, and the person confirmed it is another one
    before_start: bool = False  # paid before the program started (the previous software's time): Dr 2200 / Cr 3200


def _small(parsed: dict | None) -> dict | None:
    """What was read from the receipt, kept with the payout: known fields only, short."""
    if not isinstance(parsed, dict):
        return None
    keys = ("amount", "reference", "paid_at", "bank", "source_card", "dest_card", "dest_name", "iban", "success", "description")
    out = {k: parsed.get(k) for k in keys if isinstance(parsed.get(k), (str, int, float, bool)) or parsed.get(k) is None}
    from ..services.bank_parties import mask_text

    out = {k: (mask_text(v)[:200] if isinstance(v, str) else v) for k, v in out.items()}  # no whole card / IBAN kept
    if isinstance(parsed.get("raw_text"), str):
        out["raw_text"] = mask_text(parsed["raw_text"])[:4000]
    return out


@router.post("/staff/{pid}/payout")
def pay(pid: int, body: PayoutIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    person = db.get(Staff, pid) or _404("شخص")
    if body.from_owner and not has_permission(user.role, "capital"):
        raise HTTPException(403, "پرداخت از جیب مدیر فقط با دسترسی مدیر ثبت می‌شود")
    pa = None
    if body.before_start:
        body.from_owner = False
    elif not body.from_owner:
        pa = db.get(PaymentAccount, body.payment_account_id) if body.payment_account_id else None
        if pa is None:
            raise HTTPException(400, "حسابی که پول از آن پرداخت شد را انتخاب کنید")
        if not pa.is_active:
            raise HTTPException(400, f"حساب «{pa.name}» بایگانی شده است؛ حساب دیگری انتخاب کنید")
    sha = None
    if body.receipt_token:
        if not payout_receipt.keep_image(db, body.receipt_token):
            raise HTTPException(400, "عکس رسید پیدا نشد؛ رسید را دوباره انتخاب کنید")
        sha = body.receipt_token
    parsed = body.receipt if isinstance(body.receipt, dict) else None
    try:
        p = staffpay.pay(db, person, body.amount, kind=body.kind, payment_account=pa, from_owner=body.from_owner,
                         paid_at=body.paid_at, jy=body.jy, jm=body.jm, reference=(body.reference or "").strip(),
                         description=(body.description or "").strip(), source="receipt" if (sha or parsed) else "manual",
                         image_path=None, image_sha256=sha, parsed=_small(parsed), force=body.force,
                         before_start=body.before_start, user=user)
    except staffpay.Duplicate as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except IntegrityError as exc:  # the same receipt booked from another screen at this very moment
        db.rollback()
        raise HTTPException(409, "این رسید همین الان (از صفحهٔ دیگری) ثبت شد؛ صورت‌حساب این شخص را نگاه کنید") from exc
    except (staffpay.PayError, ValueError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    if parsed and payout_receipt.may_learn(db, person, parsed):
        payout_receipt.learn_card(person, parsed.get("dest_card"))
    try:
        db.commit()
    except IntegrityError as exc:  # the same receipt booked from another screen at this very moment
        db.rollback()
        raise HTTPException(409, "این رسید همین الان (از صفحهٔ دیگری) ثبت شد؛ صورت‌حساب این شخص را نگاه کنید") from exc
    return {"ok": True, "payout": staffpay._payout_row(db, p, _accounts(db)), "balance": staffpay.balance_at(db, pid),
            "kind_label": staffpay.kind_label(person, p.kind), "month": staffpay.month_label(p.jy, p.jm)}


@router.post("/staff/payout-receipt")
def read_receipt(file: UploadFile | None = File(None), text: str = Form(""), caption: str = Form(""),
                 staff_id: int | None = Form(None), kind: str = Form(""), db: Session = Depends(get_db),
                 _=Depends(require("finance"))):
    """Read a payout receipt (its picture or text) and the words sent with it; nothing is booked. A plain function: the
    assistant may take a while to read a picture, and the server keeps answering everyone else meanwhile."""
    data, media = None, None
    if file is not None:
        data = file.file.read(payout_receipt.MAX_BYTES + 1)
        media = (file.content_type or "").split(";")[0].strip().lower()
        if media == "image/jpg":
            media = "image/jpeg"
    if not data and not (text or "").strip():
        raise HTTPException(400, "عکس رسید را انتخاب کنید یا متن آن را بنویسید")
    try:
        return payout_receipt.read(db, image=data or None, media_type=media, text=text or "", caption=caption or "",
                                   staff_id=staff_id, kind=kind or None)
    except payout_receipt.ReceiptError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/staff/payouts")
def payouts(staff_id: int | None = None, jy: int | None = None, jm: int | None = None, db: Session = Depends(get_db),
            _=Depends(require("finance"))):
    """Payouts, newest first: of one person and / or of one month (the month they are for)."""
    q = select(StaffPayout).order_by(StaffPayout.paid_at.desc(), StaffPayout.id.desc())
    if staff_id:
        q = q.where(StaffPayout.staff_id == staff_id)
    if jy is not None and jm is not None:
        y, m = _month(jy, jm)
        q = q.where(StaffPayout.jy == y, StaffPayout.jm == m)
    accounts = _accounts(db)
    names = {p.id: p.full_name for p in db.scalars(select(Staff))}
    return [{**staffpay._payout_row(db, p, accounts), "staff_id": p.staff_id, "staff": names.get(p.staff_id)}
            for p in db.scalars(q.limit(1000))]


class VoidIn(BaseModel):
    reason: str = ""


@router.post("/staff/payouts/{payout_id}/void")
def void_payout(payout_id: int, body: VoidIn, db: Session = Depends(get_db), user=Depends(require("void"))):
    p = db.get(StaffPayout, payout_id) or _404("پرداخت")
    try:
        staffpay.void_payout(db, p, body.reason.strip(), user=user)
    except (staffpay.PayError, ValueError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    payout_receipt.forget_card(db, db.get(Staff, p.staff_id), p)  # a card learned from a mistake is not kept
    db.commit()
    return {"ok": True, "balance": staffpay.balance_at(db, p.staff_id)}


@router.get("/staff/payouts/{payout_id}/image")
def payout_image(payout_id: int, db: Session = Depends(get_db), _=Depends(require("finance"))):
    p = db.get(StaffPayout, payout_id) or _404("پرداخت")
    found = payout_receipt.image(db, p.image_sha256)
    if found is None:
        _404("عکس رسید")
    data, media = found
    return Response(content=data, media_type=media, headers={"Cache-Control": "private, max-age=3600",
                                                             "X-Content-Type-Options": "nosniff",
                                                             "Content-Disposition": "inline"})


@router.get("/staff/{pid}/statement")
def statement(pid: int, jy: int | None = None, jm: int | None = None, db: Session = Depends(get_db),
              _=Depends(require("finance"))):
    """One person's month from the books: owed at the start, earned, given, left; paid for it after it ended."""
    db.get(Staff, pid) or _404("شخص")
    y, m = _month(jy, jm)
    return staffpay.statement(db, pid, y, m)


# ------------------------------------------------------------------ the cards / IBANs a person is paid on (1.23)
def _card_row(r) -> dict:  # noqa: ANN001
    return {"id": r.id, "kind": r.match_kind, "mask": r.match_text, "auto": r.auto, "hits": r.hits, "undos": r.undos,
            "source": r.source}


@router.get("/staff/{pid}/pay-cards")
def pay_cards(pid: int, db: Session = Depends(get_db), _=Depends(require("finance"))):
    from ..models import BankRule

    db.get(Staff, pid) or _404("شخص")
    return [_card_row(r) for r in db.scalars(select(BankRule).where(BankRule.staff_id == pid, BankRule.target_type == "staff",
                                                                    BankRule.is_active.is_(True), BankRule.match_hash.is_not(None))
                                             .order_by(BankRule.id))]


class CardIn(BaseModel):
    number: str  # a card (16 digits) or IBAN (IR + 24 digits): only its hash and mask are kept


@router.post("/staff/{pid}/pay-cards")
def add_pay_card(pid: int, body: CardIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    """Money sent from a salon account to this card / IBAN is booked as this person's payout by itself (bank lines)."""
    from ..services import bank_booking

    person = db.get(Staff, pid) or _404("شخص")
    try:
        rule = bank_booking.staff_card(db, person, body.number, user=user)
    except (bank_booking.BankBookError, ValueError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return _card_row(rule)


@router.delete("/staff/pay-cards/{rule_id}")
def remove_pay_card(rule_id: int, db: Session = Depends(get_db), user=Depends(require("settings"))):
    from ..models import BankRule
    from ..services.audit import audit

    rule = db.get(BankRule, rule_id)
    if rule is None or rule.target_type != "staff":
        _404("کارت")
    rule.is_active = False
    audit(db, "bank_rule.remove", "bank_rule", rule.id, {"mask": rule.match_text, "staff": rule.staff_id}, user=user)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ settling everyone at once
@router.get("/staff/settle-plan")
def settle_plan(jy: int | None = None, jm: int | None = None, who: str = "all", db: Session = Depends(get_db),
                _=Depends(require("finance"))):
    """Everyone with what is still to be paid for the month (who: all | staff | employee)."""
    y, m = _month(jy, jm)
    try:
        rows = staffpay.settle_plan(db, y, m, who if who in ("all", "staff", "employee") else "all")
    except staffpay.PayError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"jy": y, "jm": m, "month": staffpay.month_label(y, m), "current": list(jmonth_of(local_now())), "rows": rows,
            "total_due": sum(r["due"] for r in rows)}


class SettleRow(BaseModel):
    staff_id: int
    amount: int
    expected: int  # what the list showed as still to pay (changed meanwhile -> not booked, said why)
    payment_account_id: int | None = None  # this person paid from another account
    description: str = ""
    force: bool = False


class SettleIn(BaseModel):
    jy: int
    jm: int
    payment_account_id: int | None = None
    from_owner: bool = False
    paid_at: datetime | None = None
    rows: list[SettleRow]


@router.post("/staff/settle")
def settle(body: SettleIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    """The month's settlement of several people at once; each one that can't be booked says why (the rest are)."""
    y, m = _month(body.jy, body.jm)
    if body.from_owner and not has_permission(user.role, "capital"):
        raise HTTPException(403, "پرداخت از جیب مدیر فقط با دسترسی مدیر ثبت می‌شود")
    pa = None
    if not body.from_owner:
        pa = db.get(PaymentAccount, body.payment_account_id) if body.payment_account_id else None
        if pa is None and not all(r.payment_account_id for r in body.rows):
            raise HTTPException(400, "حسابی که پول از آن پرداخت شد را انتخاب کنید")
        if pa is not None and not pa.is_active:
            raise HTTPException(400, f"حساب «{pa.name}» بایگانی شده است؛ حساب دیگری انتخاب کنید")
    try:
        out = staffpay.settle_many(db, y, m, [r.model_dump() for r in body.rows], payment_account=pa, from_owner=body.from_owner,
                                   paid_at=body.paid_at, user=user)
    except (staffpay.PayError, ValueError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"ok": not out["errors"], **out, "month": staffpay.month_label(y, m)}


# ------------------------------------------------------------------ employees
@router.get("/employees")
def employees(jy: int | None = None, jm: int | None = None, db: Session = Depends(get_db), _=Depends(require("finance"))):
    y, m = _month(jy, jm)
    rows = staffpay.people_month(db, y, m, employees=True)
    totals = {k: sum(r[k] or 0 for r in rows) for k in ("earned_total", "given", "closing", "remaining", "balance_now")}
    totals["salaries"] = sum(a["amount"] for r in rows for a in r["salaries"] if a["status"] == "booked")
    return {"jy": y, "jm": m, "month": staffpay.month_label(y, m), "current": list(jmonth_of(local_now())), "rows": rows,
            "totals": totals}


class SalaryRow(BaseModel):
    staff_id: int
    amount: int
    description: str = ""


class SalariesIn(BaseModel):
    jy: int
    jm: int
    rows: list[SalaryRow]


@router.post("/employees/salaries")
def add_salaries(body: SalariesIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    """The month's salaries of several employees at once; each one that can't be booked says why (the rest are)."""
    y, m = _month(body.jy, body.jm)
    done, errors = [], []
    for row in body.rows:
        person = db.get(Staff, row.staff_id)
        if person is None:
            errors.append({"staff_id": row.staff_id, "error": "شخص پیدا نشد"})
            continue
        try:
            with db.begin_nested():
                a = staffpay.accrue_salary(db, person, y, m, row.amount, row.description.strip(), user=user)
            done.append({"staff_id": person.id, "id": a.id, "amount": a.amount})
        except (staffpay.PayError, ValueError) as exc:
            errors.append({"staff_id": person.id, "name": person.full_name, "error": str(exc)})
    db.commit()
    return {"ok": not errors, "done": done, "errors": errors}


@router.post("/employees/salaries/{salary_id}/void")
def void_salary(salary_id: int, db: Session = Depends(get_db), user=Depends(require("void"))):
    a = db.get(SalaryAccrual, salary_id) or _404("حقوق")
    try:
        staffpay.void_salary(db, a, user=user)
    except (staffpay.PayError, ValueError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"ok": True}
