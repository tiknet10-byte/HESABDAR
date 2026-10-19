"""Paying staff and employees, and each person's month at a glance.

* Practitioners earn a share of their work (services and forfeited deposits of their line, after the line's treasury);
  employees (Staff.kind "employee") earn a fixed monthly salary, booked as an expense of the clinic (51xx «حقوق و
  دستمزد») when the month's salary is entered. Both are owed on 2200 with the person's staff_id.
* During the month they take advances (برداشتی / مساعده); at the end of the month the rest is settled (تسویه / پرداخت
  حقوق). A person who took more than they earned owes the difference: it stays on 2200 and is taken from next month.
  Both are StaffPayout rows: Dr 2200 / Cr the cash account (or 3100 when the owner paid from their own pocket); a
  payout entered by mistake is voided on its own date (it never happened).
* statement(): the month of one person straight from the ledger (2200): what was owed at the start, what was earned,
  given and left at the end, and what was paid for this month after it ended.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (JournalEntry, JournalLine, LedgerAccount, PaymentAccount, SalaryAccrual, Staff, StaffPayout,
                      local_now)
from .jalali import J_MONTHS, jmonth_of, month_range

KINDS = ("advance", "settlement")
MAX_RIAL = 10**15  # as accounting.MAX_RIAL
SALARY_CATEGORY = "حقوق و دستمزد"
DUPLICATE_WINDOW = timedelta(minutes=10)


class PayError(ValueError):
    pass


class Duplicate(PayError):
    """Looks like a payment already entered (same receipt, or same person and amount a moment ago)."""


def is_employee(p: Staff | None) -> bool:
    return bool(p) and (p.kind or "") == "employee"


def month_label(jy: int, jm: int) -> str:
    return f"{J_MONTHS[jm - 1]} {jy}"


def kind_label(p: Staff, kind: str) -> str:
    if kind == "advance":
        return "مساعده" if is_employee(p) else "برداشتی"
    return "پرداخت حقوق" if is_employee(p) else "تسویه"


def _month_ok(jy: int, jm: int) -> None:
    if not (1 <= jm <= 12) or not (1300 <= jy <= 1500):
        raise PayError("ماه نامعتبر است")
    if (jy, jm) > jmonth_of(local_now()):
        raise PayError("ماه آینده هنوز نیامده است")


def _lines(db: Session, staff_id: int):  # noqa: ANN202
    from .accounting import STAFF_PAYABLE, account

    acc = account(db, STAFF_PAYABLE)
    return (select(JournalEntry.at, JournalEntry.ref_type, JournalEntry.ref_id, JournalLine.debit, JournalLine.credit)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalLine.account_id == acc.id, JournalLine.staff_id == staff_id))


def balance_at(db: Session, staff_id: int, before: datetime | None = None) -> int:
    """What we owed the person (+) or they owed us (-) just before `before` (None: now)."""
    q = _lines(db, staff_id).subquery()
    stmt = select(func.coalesce(func.sum(q.c.credit - q.c.debit), 0))
    if before is not None:
        stmt = stmt.where(q.c.at < before)
    return int(db.scalar(stmt) or 0)


# ------------------------------------------------------------------ payouts
def pay(db: Session, staff: Staff, amount: int, *, kind: str = "settlement", payment_account: PaymentAccount | None = None,
        from_owner: bool = False, paid_at: datetime | None = None, jy: int | None = None, jm: int | None = None,
        reference: str = "", description: str = "", source: str = "manual", image_path: str | None = None,
        image_sha256: str | None = None, parsed: dict | None = None, force: bool = False, user=None) -> StaffPayout:  # noqa: ANN001
    from .accounting import EQUITY, STAFF_PAYABLE, Leg, account, cash_account_for, ensure_not_future, post
    from .audit import audit, write_lock

    amount = int(amount)
    if amount <= 0:
        raise PayError("مبلغ پرداخت باید مثبت باشد")
    if amount > MAX_RIAL:
        raise PayError("مبلغ پرداخت بیش از حد بزرگ است؛ عدد را دوباره نگاه کنید")
    if kind not in KINDS:
        raise PayError("نوع پرداخت نامعتبر است")
    if not from_owner and payment_account is None:
        raise PayError("حسابی که پول از آن پرداخت شد را انتخاب کنید")
    ensure_not_future(paid_at, "تاریخ پرداخت")
    at = paid_at or local_now()
    if jy is None or jm is None:
        jy, jm = jmonth_of(at)
    _month_ok(jy, jm)
    write_lock(db)  # a double click or the same receipt sent twice: the second one sees the first
    if not force:
        if image_sha256 and db.scalar(select(StaffPayout.id).where(StaffPayout.image_sha256 == image_sha256,
                                                                   StaffPayout.status == "booked").limit(1)):
            raise Duplicate("این رسید قبلاً ثبت شده است")
        if reference and db.scalar(select(StaffPayout.id).where(StaffPayout.reference == reference, StaffPayout.amount == amount,
                                                                StaffPayout.status == "booked").limit(1)):
            raise Duplicate("پرداختی با همین شمارهٔ پیگیری و همین مبلغ قبلاً ثبت شده است")
        day = datetime.combine(at.date(), datetime.min.time())
        if db.scalar(select(StaffPayout.id).where(StaffPayout.staff_id == staff.id, StaffPayout.amount == amount,
                                                  StaffPayout.status == "booked", StaffPayout.paid_at >= day,
                                                  StaffPayout.paid_at < day + timedelta(days=1),
                                                  StaffPayout.created_at >= local_now() - DUPLICATE_WINDOW).limit(1)):
            raise Duplicate("همین مبلغ با همین تاریخ چند دقیقه پیش برای همین شخص ثبت شده است؛ اگر پرداخت جداگانه‌ای است، دوباره تأیید کنید")
    p = StaffPayout(staff_id=staff.id, kind=kind, jy=jy, jm=jm, amount=amount, paid_at=at,
                    payment_account_id=None if from_owner else payment_account.id, from_owner=bool(from_owner),
                    reference=(reference or "")[:64], description=description or "", source=source, image_path=image_path,
                    image_sha256=image_sha256, parsed=parsed, created_by=getattr(user, "id", None))
    db.add(p)
    db.flush()
    credit = account(db, EQUITY) if from_owner else cash_account_for(db, payment_account)
    text = f"{kind_label(staff, kind)} {staff.full_name} - {month_label(jy, jm)}" + (f" - {description}" if description else "")
    entry = post(db, text[:256], [
        Leg(account(db, STAFF_PAYABLE), debit=amount, staff_id=staff.id, line_id=staff.line_id),
        Leg(credit, credit=amount, staff_id=staff.id),
    ], "staff_payout", p.id, at=at)
    p.entry_id = entry.id
    audit(db, "staff.payout", "staff", staff.id, {"payout": p.id, "kind": kind, "amount": amount, "month": f"{jy}/{jm:02d}",
                                                  "account": p.payment_account_id, "from_owner": bool(from_owner),
                                                  "source": source}, user=user)
    return p


def void_payout(db: Session, p: StaffPayout, reason: str = "", user=None) -> StaffPayout:  # noqa: ANN001
    """A payout entered by mistake: reversed on its own date, as if it never happened."""
    from .accounting import AccountingError, Leg, _claim, post
    from .audit import audit

    if p.status != "booked" or not _claim(db, p, "booked", "void"):
        raise PayError("این پرداخت قبلاً باطل شده است")
    entry = db.get(JournalEntry, p.entry_id) if p.entry_id else None
    if entry is None:
        raise AccountingError("سند این پرداخت پیدا نشد")
    staff = db.get(Staff, p.staff_id)
    post(db, f"ابطال {kind_label(staff, p.kind)} {staff.full_name}" + (f" - {reason}" if reason else ""), [
        Leg(db.get(LedgerAccount, ln.account_id), debit=ln.credit, credit=ln.debit, staff_id=ln.staff_id, line_id=ln.line_id)
        for ln in entry.lines
    ], "staff_payout", p.id, at=entry.at, check_size=False)
    p.voided_at, p.void_reason = local_now(), (reason or "")[:256]
    audit(db, "staff.payout_void", "staff", p.staff_id, {"payout": p.id, "amount": p.amount, "reason": reason}, user=user)
    return p


def backfill_payouts(db: Session) -> int:
    """Payouts booked before 1.22 (an entry only, its ref_id = the staff id) get their StaffPayout row, so they can be
    listed, voided and shown in the statements. Once: an entry with a row is never taken again."""
    from .accounting import EQUITY, STAFF_PAYABLE, account

    done = {e for (e,) in db.execute(select(StaffPayout.entry_id).where(StaffPayout.entry_id.is_not(None)))}
    payable, equity = account(db, STAFF_PAYABLE).id, account(db, EQUITY).id
    by_ledger = {pa.ledger_account_id: pa.id for pa in db.scalars(select(PaymentAccount)) if pa.ledger_account_id}
    n = 0
    moved_back: dict[tuple[int, int, int], int] = {}  # (person, year, month) -> legacy payouts already counted for the month before
    for entry in db.scalars(select(JournalEntry).where(JournalEntry.ref_type == "staff_payout").order_by(JournalEntry.at, JournalEntry.id)):
        if entry.id in done:
            continue
        pay_line = next((ln for ln in entry.lines if ln.account_id == payable and ln.debit > 0 and ln.staff_id), None)
        other = next((ln for ln in entry.lines if ln.account_id != payable and ln.credit > 0), None)
        if pay_line is None:
            continue  # a reversal (credit on 2200) belongs to the payout it reverses; nothing before 1.22 had one
        jy, jm = jmonth_of(entry.at)
        # before 1.22 a payout had no month: one made in the first half of a month while the person was still owed
        # from the months before was that month's settlement (the Shahrivar share paid on 2 Mehr)
        from .jalali import gregorian_to_jalali

        # (only until what was owed at the month's start is covered: an advance after that is this month's)
        if gregorian_to_jalali(entry.at.year, entry.at.month, entry.at.day)[2] <= 15:
            key = (pay_line.staff_id, jy, jm)
            owed = balance_at(db, pay_line.staff_id, month_range(jy, jm)[0])
            if moved_back.get(key, 0) < owed:
                moved_back[key] = moved_back.get(key, 0) + int(pay_line.debit)
                jy, jm = (jy, jm - 1) if jm > 1 else (jy - 1, 12)
        p = StaffPayout(staff_id=pay_line.staff_id, kind="settlement", jy=jy, jm=jm, amount=int(pay_line.debit), paid_at=entry.at,
                        payment_account_id=by_ledger.get(other.account_id) if other else None,
                        from_owner=bool(other and other.account_id == equity), description=entry.description or "",
                        source="legacy", entry_id=entry.id, created_at=entry.at)
        db.add(p)
        db.flush()
        entry.ref_id = p.id
        n += 1
    if n:
        db.flush()
    return n


# ------------------------------------------------------------------ employees' salaries
def accrue_salary(db: Session, staff: Staff, jy: int, jm: int, amount: int, description: str = "", user=None) -> SalaryAccrual:  # noqa: ANN001
    """An employee's salary for a month: an expense of the clinic, owed to them until paid. One per person and month
    (to change it, void it and enter it again)."""
    from .accounting import STAFF_PAYABLE, Leg, account, expense_account, post
    from .audit import audit, write_lock

    if not is_employee(staff):
        raise PayError("حقوق ثابت فقط برای کارمندان ثبت می‌شود؛ سهم پرسنل از کارکردشان خودکار حساب می‌شود")
    amount = int(amount)
    if amount <= 0:
        raise PayError("مبلغ حقوق باید مثبت باشد")
    if amount > MAX_RIAL:
        raise PayError("مبلغ حقوق بیش از حد بزرگ است؛ عدد را دوباره نگاه کنید")
    _month_ok(jy, jm)
    write_lock(db)
    if db.scalar(select(SalaryAccrual.id).where(SalaryAccrual.staff_id == staff.id, SalaryAccrual.jy == jy, SalaryAccrual.jm == jm,
                                                SalaryAccrual.status == "booked").limit(1)):
        raise Duplicate(f"حقوق {month_label(jy, jm)} برای {staff.full_name} قبلاً ثبت شده است؛ برای تغییر، اول آن را باطل کنید")
    s, e = month_range(jy, jm)
    now = local_now()
    at = now if now < e else e - timedelta(seconds=1)  # a past month: on its last moment, so it is that month's cost
    a = SalaryAccrual(staff_id=staff.id, jy=jy, jm=jm, amount=amount, at=at, description=description or "",
                      created_by=getattr(user, "id", None))
    db.add(a)
    db.flush()
    post(db, f"حقوق {staff.full_name} - {month_label(jy, jm)}" + (f" - {description}" if description else ""), [
        Leg(expense_account(db, SALARY_CATEGORY), debit=amount, staff_id=staff.id),
        Leg(account(db, STAFF_PAYABLE), credit=amount, staff_id=staff.id),
    ], "salary", a.id, at=at)
    audit(db, "salary.accrue", "staff", staff.id, {"salary": a.id, "amount": amount, "month": f"{jy}/{jm:02d}"}, user=user)
    return a


def void_salary(db: Session, a: SalaryAccrual, user=None) -> SalaryAccrual:  # noqa: ANN001
    from .accounting import Leg, _claim, post
    from .audit import audit

    if a.status != "booked" or not _claim(db, a, "booked", "void"):
        raise PayError("این حقوق قبلاً باطل شده است")
    entry = db.scalar(select(JournalEntry).where(JournalEntry.ref_type == "salary", JournalEntry.ref_id == a.id).order_by(JournalEntry.id))
    staff = db.get(Staff, a.staff_id)
    post(db, f"ابطال حقوق {staff.full_name} - {month_label(a.jy, a.jm)}", [
        Leg(db.get(LedgerAccount, ln.account_id), debit=ln.credit, credit=ln.debit, staff_id=ln.staff_id, line_id=ln.line_id)
        for ln in entry.lines
    ], "salary", a.id, at=entry.at, check_size=False)
    a.voided_at = local_now()
    audit(db, "salary.void", "staff", a.staff_id, {"salary": a.id, "amount": a.amount}, user=user)
    return a


# ------------------------------------------------------------------ a person's month
EARNED = {"commission": "services", "invoice_void": "services", "forfeit_commission": "forfeits", "salary": "salary"}


def _payout_row(db: Session, p: StaffPayout, accounts: dict[int, str]) -> dict:
    return {"id": p.id, "kind": p.kind, "amount": p.amount, "paid_at": p.paid_at.isoformat(timespec="minutes"),
            "jy": p.jy, "jm": p.jm, "account": "از جیب مدیر" if p.from_owner else accounts.get(p.payment_account_id),
            "payment_account_id": p.payment_account_id, "from_owner": p.from_owner, "reference": p.reference,
            "description": p.description, "source": p.source, "status": p.status, "has_image": bool(p.image_sha256),
            "void_reason": p.void_reason}


def statement(db: Session, staff_id: int, jy: int, jm: int) -> dict:
    """One person's month from the books: owed at the start + earned - given = owed at the end; and what was paid for
    this month after it ended (a settlement paid on the 2nd of the next month)."""
    from . import reports

    person = db.get(Staff, staff_id)
    if person is None:
        raise PayError("شخص پیدا نشد")
    s, e = month_range(jy, jm)
    opening = balance_at(db, staff_id, s)
    earned = {"services": 0, "forfeits": 0, "salary": 0, "other": 0}
    given = 0
    for at, ref_type, _ref_id, debit, credit in db.execute(_lines(db, staff_id).where(JournalEntry.at >= s, JournalEntry.at < e)):
        if ref_type == "staff_payout":
            given += int(debit) - int(credit)
        else:
            earned[EARNED.get(ref_type, "other")] += int(credit) - int(debit)
    closing = opening + sum(earned.values()) - given
    accounts = {pa.id: pa.name for pa in db.scalars(select(PaymentAccount))}
    in_month = [_payout_row(db, p, accounts) for p in db.scalars(
        select(StaffPayout).where(StaffPayout.staff_id == staff_id, StaffPayout.paid_at >= s, StaffPayout.paid_at < e)
        .order_by(StaffPayout.paid_at, StaffPayout.id))]
    later = [_payout_row(db, p, accounts) for p in db.scalars(
        select(StaffPayout).where(StaffPayout.staff_id == staff_id, StaffPayout.jy == jy, StaffPayout.jm == jm,
                                  StaffPayout.status == "booked", StaffPayout.paid_at >= e).order_by(StaffPayout.paid_at))]
    paid_later = sum(x["amount"] for x in later)
    work = None
    if not is_employee(person):
        rep = reports.staff_shares(db, s.date(), (e - timedelta(days=1)).date())
        work = next((r for r in rep["staff"] if r["staff_id"] == staff_id), None)
    salaries = [{"id": a.id, "amount": a.amount, "at": a.at.isoformat(timespec="minutes"), "description": a.description,
                 "status": a.status} for a in db.scalars(select(SalaryAccrual).where(
                     SalaryAccrual.staff_id == staff_id, SalaryAccrual.jy == jy, SalaryAccrual.jm == jm).order_by(SalaryAccrual.id))]
    lines = {}
    if person.line_id:
        from ..models import ServiceLine

        line = db.get(ServiceLine, person.line_id)
        lines = {"line": line.name if line else None}
    return {"staff_id": staff_id, "name": person.full_name, "mobile": person.mobile, "employee": is_employee(person),
            "job_title": person.job_title, "salary": person.salary, "percent": person.commission_percent, **lines,
            "jy": jy, "jm": jm, "month": month_label(jy, jm), "current": (jy, jm) == jmonth_of(local_now()),
            "opening": opening, "earned": earned, "earned_total": sum(earned.values()), "given": given, "closing": closing,
            "advances": sum(x["amount"] for x in in_month if x["kind"] == "advance" and x["status"] == "booked"),
            "payouts": in_month, "paid_later": paid_later, "later": later, "remaining": closing - paid_later,
            "balance_now": balance_at(db, staff_id), "work": work, "salaries": salaries}


def people_month(db: Session, jy: int, jm: int, employees: bool) -> list[dict]:
    """Every active person (or one still owed / owing money) of one kind, with their month in short."""
    s, e = month_range(jy, jm)
    people = [p for p in db.scalars(select(Staff).order_by(Staff.id)) if is_employee(p) == employees]
    out = []
    for p in people:
        st = statement(db, p.id, jy, jm)
        if not p.is_active and not st["opening"] and not st["closing"] and not st["payouts"] and not st["balance_now"]:
            continue
        out.append({k: st[k] for k in ("staff_id", "name", "mobile", "job_title", "salary", "percent", "opening", "earned",
                                       "earned_total", "given", "advances", "closing", "paid_later", "remaining",
                                       "balance_now", "salaries")} | {"active": p.is_active})
    return out
