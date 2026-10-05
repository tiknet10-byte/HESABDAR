"""Appointments, deposits (بیعانه), invoices, payments, expenses and the ledger."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..models import (
    Appointment,
    Customer,
    Deposit,
    Expense,
    Invoice,
    JournalEntry,
    LedgerAccount,
    Payment,
    PaymentAccount,
    Service,
    ServiceLine,
    Staff,
)
from ..services import accounting, learning, scheduling, settings_store
from ..services.accounting import AccountingError
from ..services.audit import audit
from .deps import require

router = APIRouter(prefix="/api", tags=["finance"])


def _get(db: Session, model, id_: int | None, label: str = "رکورد"):  # noqa: ANN001, ANN202
    obj = db.get(model, id_) if id_ else None
    if obj is None:
        raise HTTPException(404, f"{label} یافت نشد")
    return obj


def _customer(db: Session, customer_id: int | None, name: str | None, mobile: str | None, user) -> Customer:  # noqa: ANN001
    if customer_id:
        return _get(db, Customer, customer_id, "مشتری")
    if not (name or mobile):
        raise HTTPException(400, "مشتری را انتخاب کنید یا نام/موبایل وارد کنید")
    c, _ = accounting.find_or_create_customer(db, name, mobile, user=user)
    return c


# ---------------------------------------------------------------- appointments
class AppointmentIn(BaseModel):
    customer_id: int | None = None
    customer_name: str | None = None
    customer_mobile: str | None = None
    service_id: int | None = None
    staff_id: int | None = None
    start_at: datetime
    quoted_price: int | None = None
    notes: str = ""
    status: str = "booked"
    duration_minutes: int | None = None  # override the service's default length for this booking
    deposit_ids: list[int] = []  # held deposits of the customer to attach to this appointment


def _appt(db: Session, a: Appointment, customer: str | None = None) -> dict:
    svc = db.get(Service, a.service_id) if a.service_id else None
    deps = db.scalars(select(Deposit).where(Deposit.appointment_id == a.id)).all()
    person = db.get(Staff, a.staff_id) if a.staff_id else None
    return {"id": a.id, "customer_id": a.customer_id, "customer": customer, "service_id": a.service_id,
            "staff": person.full_name if person else None, "line": svc.line.name if svc else None,
            "service": svc.name if svc else None, "duration_minutes": a.duration_minutes or (svc.duration_minutes if svc else 60),
            "custom_duration": a.duration_minutes is not None,
            "staff_id": a.staff_id, "start_at": a.start_at.isoformat(timespec="minutes"), "status": a.status,
            "quoted_price": a.quoted_price, "notes": a.notes,
            "deposits": [{"id": d.id, "amount": d.amount, "status": d.status, "received_at": d.received_at.isoformat(timespec="minutes")}
                         for d in deps]}


def _book(db: Session, customer: Customer, service_id: int | None, staff_id: int | None, start_at: datetime,
          notes: str, user, quoted_price: int | None = None, duration_minutes: int | None = None) -> Appointment:  # noqa: ANN001
    svc = db.get(Service, service_id) if service_id else None
    if svc and duration_minutes == svc.duration_minutes:
        duration_minutes = None  # same as the service default
    staff_id = accounting.default_staff_id(db, service_id, staff_id)
    a = Appointment(customer_id=customer.id, service_id=service_id, staff_id=staff_id, start_at=start_at.replace(second=0, microsecond=0),
                    quoted_price=quoted_price if quoted_price is not None else (svc.base_price if svc else 0), notes=notes,
                    duration_minutes=duration_minutes)
    db.add(a)
    db.flush()
    audit(db, "appointment.create", "appointment", a.id, {"customer": customer.id}, user=user)
    return a


@router.get("/appointments")
def appointments(start: date | None = None, end: date | None = None, db: Session = Depends(get_db), _=Depends(require("read"))):
    s = datetime.combine(start or date.today() - timedelta(days=7), datetime.min.time())
    e = datetime.combine(end or date.today() + timedelta(days=60), datetime.max.time())
    rows = db.execute(select(Appointment, Customer.full_name).join(Customer, Customer.id == Appointment.customer_id)
                      .where(Appointment.start_at.between(s, e)).order_by(Appointment.start_at)).all()
    return [_appt(db, a, n) for a, n in rows]


@router.get("/appointments/suggest")
def suggest_slots(service_id: int, staff_id: int | None = None, after: datetime | None = None, count: int = 6,
                  duration: int | None = None, db: Session = Depends(get_db), _=Depends(require("read"))):
    """First free times for a service, based on its duration (or the given one), working hours and staff capacity."""
    return scheduling.find_slots(db, service_id, staff_id, after, min(count, 20), duration_minutes=duration)


@router.get("/appointments/{aid}")
def get_appointment(aid: int, db: Session = Depends(get_db), _=Depends(require("read"))):
    a = _get(db, Appointment, aid, "نوبت")
    c = db.get(Customer, a.customer_id)
    return {**_appt(db, a, c.full_name), "customer_mobile": c.mobile}


@router.post("/appointments")
def create_appointment(body: AppointmentIn, db: Session = Depends(get_db), user=Depends(require("write"))):
    c = _customer(db, body.customer_id, body.customer_name, body.customer_mobile, user)
    warning = scheduling.check_slot(db, body.service_id, body.staff_id, body.start_at, duration_minutes=body.duration_minutes)
    a = _book(db, c, body.service_id, body.staff_id, body.start_at, body.notes, user, body.quoted_price, body.duration_minutes)
    for d in db.scalars(select(Deposit).where(Deposit.id.in_(body.deposit_ids or [-1]), Deposit.customer_id == c.id)):
        d.appointment_id = a.id
        if not d.service_id and a.service_id:
            d.service_id = a.service_id
    db.commit()
    return {**_appt(db, a, c.full_name), "warning": warning}


class AppointmentUpdate(BaseModel):
    service_id: int | None = None
    staff_id: int | None = None
    start_at: datetime | None = None
    duration_minutes: int | None = None
    notes: str | None = None
    status: str | None = None


@router.put("/appointments/{aid}")
def edit_appointment(aid: int, body: AppointmentUpdate, db: Session = Depends(get_db), user=Depends(require("write"))):
    a = _get(db, Appointment, aid, "نوبت")
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in ("booked", "done", "cancelled", "no_show"):
        raise HTTPException(400, "وضعیت نامعتبر")
    for k, v in data.items():
        setattr(a, k, v)
    svc = db.get(Service, a.service_id) if a.service_id else None
    if svc and a.duration_minutes == svc.duration_minutes:
        a.duration_minutes = None
    warning = scheduling.check_slot(db, a.service_id, a.staff_id, a.start_at, exclude_id=a.id,
                                    duration_minutes=a.duration_minutes) if (body.start_at or body.duration_minutes) else None
    audit(db, "appointment.update", "appointment", a.id, {k: str(v) for k, v in data.items()}, user=user)
    db.commit()
    return {**_appt(db, a), "warning": warning}


@router.patch("/appointments/{aid}")
def update_appointment(aid: int, status: str, db: Session = Depends(get_db), user=Depends(require("write"))):
    if status not in ("booked", "done", "cancelled", "no_show"):
        raise HTTPException(400, "وضعیت نامعتبر")
    a = _get(db, Appointment, aid, "نوبت")
    a.status = status
    audit(db, "appointment.status", "appointment", a.id, {"status": status}, user=user)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- deposits
class DepositIn(BaseModel):
    customer_id: int | None = None
    customer_name: str | None = None
    customer_mobile: str | None = None
    amount: int
    payment_account_id: int
    service_id: int | None = None
    appointment_id: int | None = None  # attach to an existing appointment
    received_at: datetime | None = None
    reference: str | None = None
    notes: str = ""
    # optional booking in the same step
    book_at: datetime | None = None
    book_duration: int | None = None
    staff_id: int | None = None


class DepositCloseIn(BaseModel):
    action: str  # refund | forfeit
    refund_account_id: int | None = None


def _dep(d: Deposit, name: str | None = None, db: Session | None = None) -> dict:
    out = {"id": d.id, "customer_id": d.customer_id, "customer": name, "amount": d.amount, "status": d.status,
           "payment_account_id": d.payment_account_id, "service_id": d.service_id, "appointment_id": d.appointment_id,
           "received_at": d.received_at.isoformat(timespec="minutes"), "reference": d.reference, "source": d.source,
           "applied_invoice_id": d.applied_invoice_id, "service_guess": d.service_guess, "notes": d.notes,
           "staff_id": d.staff_id}
    if db is not None:
        svc = db.get(Service, d.service_id) if d.service_id else None
        person = db.get(Staff, d.staff_id) if d.staff_id else None
        out["staff"] = person.full_name if person else None
        out["line"] = svc.line.name if svc else None
        appt = db.get(Appointment, d.appointment_id) if d.appointment_id else None
        out["service"] = svc.name if svc else None
        out["appointment_at"] = appt.start_at.isoformat(timespec="minutes") if appt else None
    return out


@router.get("/deposits")
def deposits(status: str | None = None, customer_id: int | None = None, db: Session = Depends(get_db), _=Depends(require("read"))):
    q = select(Deposit, Customer.full_name).join(Customer, Customer.id == Deposit.customer_id).order_by(Deposit.received_at.desc()).limit(500)
    if status:
        q = q.where(Deposit.status == status)
    if customer_id:
        q = q.where(Deposit.customer_id == customer_id)
    return [_dep(d, n, db) for d, n in db.execute(q).all()]


@router.post("/deposits")
def create_deposit(body: DepositIn, db: Session = Depends(get_db), user=Depends(require("write"))):
    c = _customer(db, body.customer_id, body.customer_name, body.customer_mobile, user)
    pa = _get(db, PaymentAccount, body.payment_account_id, "حساب دریافت")
    guess = [] if body.service_id else learning.guess_service_for_deposit(db, body.amount, c.id, body.notes)
    appointment_id = body.appointment_id
    warning = None
    book_at = body.book_at
    service_id = body.service_id
    if not book_at and not appointment_id and service_id and settings_store.get(db, "booking.auto"):
        slots = scheduling.find_slots(db, service_id, body.staff_id, count=1, duration_minutes=body.book_duration)
        if slots:
            book_at = datetime.fromisoformat(slots[0]["start_at"])
    if book_at:
        warning = scheduling.check_slot(db, service_id, body.staff_id, book_at, duration_minutes=body.book_duration)
        appointment_id = _book(db, c, service_id, body.staff_id, book_at, body.notes, user, duration_minutes=body.book_duration).id
    try:
        d = accounting.record_deposit(db, customer=c, amount=body.amount, payment_account=pa, received_at=body.received_at,
                                      reference=body.reference, service_id=service_id, appointment_id=appointment_id,
                                      staff_id=body.staff_id,
                                      notes=body.notes, service_guess={"candidates": guess}, user=user)
    except AccountingError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    if service_id and body.notes:
        learning.learn_text(db, body.notes, service_id)
    db.commit()
    return {**_dep(d, c.full_name, db), "warning": warning}


@router.post("/deposits/{did}/appointment")
def link_deposit(did: int, appointment_id: int, db: Session = Depends(get_db), user=Depends(require("write"))):
    d = _get(db, Deposit, did, "بیعانه")
    a = _get(db, Appointment, appointment_id, "نوبت")
    if a.customer_id != d.customer_id:
        raise HTTPException(400, "نوبت متعلق به مشتری دیگری است")
    d.appointment_id = a.id
    if not d.service_id:
        d.service_id = a.service_id
    audit(db, "deposit.link_appointment", "deposit", d.id, {"appointment": a.id}, user=user)
    db.commit()
    return _dep(d, None, db)


@router.post("/deposits/{did}/close")
def close_deposit(did: int, body: DepositCloseIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    d = _get(db, Deposit, did, "بیعانه")
    try:
        accounting.close_deposit(db, d, body.action, db.get(PaymentAccount, body.refund_account_id) if body.refund_account_id else None, user=user)
    except AccountingError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return _dep(d)


@router.post("/deposits/{did}/service")
def set_deposit_service(did: int, service_id: int, db: Session = Depends(get_db), user=Depends(require("write"))):
    """Confirm/correct which service a deposit is for - the system learns from this."""
    d = _get(db, Deposit, did, "بیعانه")
    d.service_id = service_id
    if d.notes:
        learning.learn_text(db, d.notes, service_id, weight=2)
    audit(db, "deposit.set_service", "deposit", d.id, {"service_id": service_id}, user=user)
    db.commit()
    return _dep(d, None, db)


# ---------------------------------------------------------------- invoices
class ItemIn(BaseModel):
    service_id: int | None = None
    description: str | None = None
    quantity: int = 1
    unit_price: int | None = None
    discount: int = 0
    staff_id: int | None = None


class PayIn(BaseModel):
    payment_account_id: int
    amount: int
    reference: str | None = None


class InvoiceIn(BaseModel):
    customer_id: int | None = None
    customer_name: str | None = None
    customer_mobile: str | None = None
    items: list[ItemIn]
    discount: int = 0
    apply_deposits: bool = True
    deposit_ids: list[int] | None = None
    payments: list[PayIn] = []
    issued_at: datetime | None = None
    notes: str = ""
    appointment_id: int | None = None


_NAMES: dict[str, tuple[float, dict[int, str]]] = {}


def _names(kind: str) -> dict[int, str]:
    """Small 5-second cache of staff / line names so listing many invoices stays fast."""
    import time

    from ..core.db import SessionLocal
    hit = _NAMES.get(kind)
    if hit and time.monotonic() - hit[0] < 5:
        return hit[1]
    with SessionLocal() as db:
        model = Staff if kind == "staff" else ServiceLine
        names = {o.id: o.full_name if kind == "staff" else o.name for o in db.scalars(select(model))}
    _NAMES[kind] = (time.monotonic(), names)
    return names


def _staff_names() -> dict[int, str]:
    return _names("staff")


def _line_names() -> dict[int, str]:
    return _names("line")


def _inv(i: Invoice, name: str | None = None) -> dict:
    return {"id": i.id, "number": i.number, "customer_id": i.customer_id, "customer": name, "issued_at": i.issued_at.isoformat(),
            "status": i.status, "subtotal": i.subtotal, "discount": i.discount, "total": i.total, "paid": i.paid,
            "due": i.total - i.paid, "source": i.source, "notes": i.notes,
            "items": [{"service_id": it.service_id, "line_id": it.line_id, "staff_id": it.staff_id, "description": it.description,
                       "quantity": it.quantity, "unit_price": it.unit_price, "discount": it.discount, "amount": it.amount,
                       "net_amount": it.net_amount, "commission_amount": it.commission_amount,
                       "staff": _staff_names().get(it.staff_id), "line": _line_names().get(it.line_id)} for it in i.items]}


@router.get("/invoices")
def invoices(start: date | None = None, end: date | None = None, status: str | None = None, db: Session = Depends(get_db),
             _=Depends(require("read"))):
    q = select(Invoice, Customer.full_name).join(Customer, Customer.id == Invoice.customer_id).order_by(Invoice.issued_at.desc()).limit(500)
    if start:
        q = q.where(Invoice.issued_at >= datetime.combine(start, datetime.min.time()))
    if end:
        q = q.where(Invoice.issued_at <= datetime.combine(end, datetime.max.time()))
    if status:
        q = q.where(Invoice.status == status)
    return [_inv(i, n) for i, n in db.execute(q).all()]


@router.post("/invoices/preview")
def preview_invoice(body: InvoiceIn, db: Session = Depends(get_db), _=Depends(require("write"))):
    """Price sanity checks before saving (learned price ranges)."""
    warnings = []
    for it in body.items:
        svc = db.get(Service, it.service_id) if it.service_id else None
        if svc and it.unit_price is not None:
            w = learning.price_check(svc, it.unit_price)
            if w:
                warnings.append(w)
    held = []
    if body.customer_id:
        held = [_dep(d, None, db) for d in db.scalars(select(Deposit).where(Deposit.customer_id == body.customer_id, Deposit.status == "held")
                                                         .order_by(Deposit.received_at))]
    return {"warnings": warnings, "held_deposits": held}


@router.post("/invoices")
def create_invoice(body: InvoiceIn, db: Session = Depends(get_db), user=Depends(require("write"))):
    c = _customer(db, body.customer_id, body.customer_name, body.customer_mobile, user)
    try:
        inv = accounting.issue_invoice(
            db, customer=c, items=[i.model_dump() for i in body.items], discount=body.discount,
            apply_deposit_ids=body.deposit_ids, apply_all_deposits=body.apply_deposits and not body.deposit_ids,
            payments=[p.model_dump() for p in body.payments], issued_at=body.issued_at, notes=body.notes, user=user)
    except AccountingError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    if body.appointment_id:
        a = db.get(Appointment, body.appointment_id)
        if a:
            a.status = "done"
    learning.refresh_price_stats(db)
    db.commit()
    return _inv(inv, c.full_name)


@router.get("/invoices/{iid}")
def get_invoice(iid: int, db: Session = Depends(get_db), _=Depends(require("read"))):
    i = _get(db, Invoice, iid, "فاکتور")
    c = db.get(Customer, i.customer_id)
    pays = db.scalars(select(Payment).where(Payment.invoice_id == iid)).all()
    deps = db.scalars(select(Deposit).where(Deposit.applied_invoice_id == iid)).all()
    return {**_inv(i, c.full_name), "customer_mobile": c.mobile,
            "payments": [{"id": p.id, "amount": p.amount, "account_id": p.payment_account_id, "paid_at": p.paid_at.isoformat(),
                          "reference": p.reference} for p in pays],
            "deposits": [_dep(d) for d in deps]}


@router.post("/invoices/{iid}/payments")
def pay_invoice(iid: int, body: PayIn, db: Session = Depends(get_db), user=Depends(require("write"))):
    i = _get(db, Invoice, iid, "فاکتور")
    try:
        accounting.record_payment(db, invoice=i, payment_account=_get(db, PaymentAccount, body.payment_account_id, "حساب"),
                                  amount=body.amount, reference=body.reference, user=user)
    except AccountingError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return _inv(i)


@router.post("/invoices/{iid}/void")
def void(iid: int, reason: str = "", db: Session = Depends(get_db), user=Depends(require("finance"))):
    i = _get(db, Invoice, iid, "فاکتور")
    try:
        accounting.void_invoice(db, i, reason, user=user)
    except AccountingError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return _inv(i)


# ---------------------------------------------------------------- expenses
class ExpenseIn(BaseModel):
    category: str
    amount: int
    payment_account_id: int
    spent_at: datetime | None = None
    description: str = ""
    staff_id: int | None = None


@router.get("/expenses")
def expenses(db: Session = Depends(get_db), _=Depends(require("finance"))):
    return [{"id": e.id, "category": e.category, "amount": e.amount, "payment_account_id": e.payment_account_id,
             "spent_at": e.spent_at.isoformat(), "description": e.description, "staff_id": e.staff_id}
            for e in db.scalars(select(Expense).order_by(Expense.spent_at.desc()).limit(500))]


@router.post("/expenses")
def create_expense(body: ExpenseIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    try:
        e = accounting.record_expense(db, category=body.category, amount=body.amount,
                                      payment_account=_get(db, PaymentAccount, body.payment_account_id, "حساب"),
                                      spent_at=body.spent_at, description=body.description, staff_id=body.staff_id, user=user)
    except AccountingError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"id": e.id}


# ---------------------------------------------------------------- ledger
@router.get("/ledger/trial-balance")
def trial_balance(db: Session = Depends(get_db), _=Depends(require("finance"))):
    rows = accounting.trial_balance(db)
    return {"rows": rows, "total_debit": sum(r["debit"] for r in rows), "total_credit": sum(r["credit"] for r in rows)}


@router.get("/ledger/journal")
def journal(limit: int = 200, db: Session = Depends(get_db), _=Depends(require("finance"))):
    accs = {a.id: (a.code, a.name) for a in db.scalars(select(LedgerAccount))}
    out = []
    for e in db.scalars(select(JournalEntry).order_by(JournalEntry.id.desc()).limit(min(limit, 1000))):
        out.append({"id": e.id, "at": e.at.isoformat(), "description": e.description, "ref_type": e.ref_type, "ref_id": e.ref_id,
                    "lines": [{"account": f"{accs[l.account_id][0]} {accs[l.account_id][1]}", "debit": l.debit, "credit": l.credit}
                              for l in e.lines]})
    return out
