"""Appointments, deposits (بیعانه), invoices, payments, expenses and the ledger."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select
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
    WaitlistEntry,
    local_now,
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
    allow_outside_hours: bool = False  # manager confirmed booking outside working hours / on a day off
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
            "quoted_price": a.quoted_price, "notes": a.notes, "invoice_id": a.invoice_id,
            "original_start_at": a.original_start_at.isoformat(timespec="minutes") if a.original_start_at else None,
            "time_unknown": bool(a.time_unknown),
            "deposits": [{"id": d.id, "amount": d.amount, "status": d.status, "received_at": d.received_at.isoformat(timespec="minutes")}
                         for d in deps]}


def _ensure_bookable(db: Session, service_id: int | None, staff_id: int | None, start_at: datetime, *, customer_id: int | None,
                     duration_minutes: int | None, exclude_id: int | None = None, allow_outside_hours: bool = False) -> None:
    """Reject any booking that is in the past, overlaps, exceeds capacity or (unless overridden) is outside working hours."""
    errors = scheduling.validate_slot(db, service_id, staff_id, start_at, duration_minutes=duration_minutes, exclude_id=exclude_id,
                                      customer_id=customer_id, allow_outside_hours=allow_outside_hours)
    if errors:
        overridable = all(e["overridable"] for e in errors)
        raise HTTPException(409, detail=" | ".join(e["message"] for e in errors)
                            + (" - برای ثبت خارج از ساعت کاری، گزینه آن را تأیید کنید" if overridable else ""))


def _book(db: Session, customer: Customer, service_id: int | None, staff_id: int | None, start_at: datetime,
          notes: str, user, quoted_price: int | None = None, duration_minutes: int | None = None,  # noqa: ANN001
          allow_outside_hours: bool = False) -> Appointment:
    svc = db.get(Service, service_id) if service_id else None
    if svc and duration_minutes == svc.duration_minutes:
        duration_minutes = None  # same as the service default
    staff_id = accounting.default_staff_id(db, service_id, staff_id)
    _ensure_bookable(db, service_id, staff_id, start_at, customer_id=customer.id, duration_minutes=duration_minutes,
                     allow_outside_hours=allow_outside_hours)
    a = Appointment(customer_id=customer.id, service_id=service_id, staff_id=staff_id, start_at=start_at.replace(second=0, microsecond=0),
                    quoted_price=quoted_price if quoted_price is not None else (svc.base_price if svc else 0), notes=notes,
                    duration_minutes=duration_minutes)
    db.add(a)
    db.flush()
    audit(db, "appointment.create", "appointment", a.id, {"customer": customer.id}, user=user)
    return a


@router.get("/appointments")
def appointments(start: date | None = None, end: date | None = None, customer_id: int | None = None, status: str | None = None,
                 line_id: int | None = None, db: Session = Depends(get_db), _=Depends(require("read"))):
    """Appointments in a date range; with customer_id, all of that customer's (open) appointments."""
    q = select(Appointment, Customer.full_name, Customer.mobile).join(Customer, Customer.id == Appointment.customer_id)
    if line_id:
        # by service line; an appointment without a service (brought over with only a line/staff) follows its staff's line
        q = q.where(or_(Appointment.service_id.in_(select(Service.id).where(Service.line_id == line_id)),
                        Appointment.service_id.is_(None) & Appointment.staff_id.in_(select(Staff.id).where(Staff.line_id == line_id))))
    if customer_id:
        q = q.where(Appointment.customer_id == customer_id)
    else:
        s = datetime.combine(start or date.today() - timedelta(days=7), datetime.min.time())
        e = datetime.combine(end or date.today() + timedelta(days=60), datetime.max.time())
        q = q.where(Appointment.start_at.between(s, e))
    if status:
        q = q.where(Appointment.status == status)
    rows = db.execute(q.order_by(Appointment.start_at)).all()
    out = [{**_appt(db, a, n), "customer_mobile": m} for a, n, m in rows]
    # flag overlaps that already exist in the data (e.g. created before the strict rules), so they can be fixed
    active = [(x, datetime.fromisoformat(x["start_at"]), datetime.fromisoformat(x["start_at"]) + timedelta(minutes=x["duration_minutes"] or 60))
              for x in out if x["status"] in scheduling.OCCUPYING and not x["original_start_at"] and not x["time_unknown"]]
    for i, (x, s1, e1) in enumerate(active):
        for y, s2, e2 in active[i + 1:]:
            if s2 >= e1:
                break
            if s1 < e2 and (x["customer_id"] == y["customer_id"] or (x["staff_id"] and x["staff_id"] == y["staff_id"])):
                x["conflict"] = y["conflict"] = True
    return out


@router.get("/appointments/suggest")
def suggest_slots(service_id: int, staff_id: int | None = None, after: datetime | None = None, count: int = 6,
                  duration: int | None = None, db: Session = Depends(get_db), _=Depends(require("read"))):
    """First free times for a service, based on its duration (or the given one), working hours and staff capacity."""
    return scheduling.find_slots(db, service_id, staff_id, after, min(count, 20), duration_minutes=duration)


@router.get("/appointments/check")
def check_time(service_id: int | None = None, start_at: datetime | None = None, staff_id: int | None = None,
               duration: int | None = None, customer_id: int | None = None, exclude_id: int | None = None,
               db: Session = Depends(get_db), _=Depends(require("read"))):
    """Live check used by the booking form before saving."""
    if not start_at:
        return {"ok": False, "errors": []}
    staff_id = accounting.default_staff_id(db, service_id, staff_id)
    errors = scheduling.validate_slot(db, service_id, staff_id, start_at, duration_minutes=duration, exclude_id=exclude_id,
                                      customer_id=customer_id)
    return {"ok": not errors, "errors": errors, "overridable": bool(errors) and all(e["overridable"] for e in errors)}


@router.get("/appointments/calendar")
def booking_calendar(line_id: int, start: date | None = None, days: int = 182, db: Session = Depends(get_db), _=Depends(require("read"))):
    """Occupancy of a line per day (empty / partial / full / closed) for the next months - the booking guide."""
    line = _get(db, ServiceLine, line_id, "لاین")
    return {**scheduling.line_calendar(db, line.id, start or date.today(), max(7, min(days, 400))), "line": line.name, "color": line.color}


@router.get("/appointments/{aid}")
def get_appointment(aid: int, db: Session = Depends(get_db), _=Depends(require("read"))):
    a = _get(db, Appointment, aid, "نوبت")
    c = db.get(Customer, a.customer_id)
    return {**_appt(db, a, c.full_name), "customer_mobile": c.mobile}


@router.post("/appointments")
def create_appointment(body: AppointmentIn, db: Session = Depends(get_db), user=Depends(require("write"))):
    c = _customer(db, body.customer_id, body.customer_name, body.customer_mobile, user)
    a = _book(db, c, body.service_id, body.staff_id, body.start_at, body.notes, user, body.quoted_price, body.duration_minutes,
              allow_outside_hours=body.allow_outside_hours)
    for d in db.scalars(select(Deposit).where(Deposit.id.in_(body.deposit_ids or [-1]), Deposit.customer_id == c.id)):
        d.appointment_id = a.id
        if not d.service_id and a.service_id:
            d.service_id = a.service_id
    db.commit()
    return _appt(db, a, c.full_name)


class AppointmentUpdate(BaseModel):
    service_id: int | None = None
    staff_id: int | None = None
    start_at: datetime | None = None
    duration_minutes: int | None = None
    notes: str | None = None
    status: str | None = None
    allow_outside_hours: bool = False


@router.put("/appointments/{aid}")
def edit_appointment(aid: int, body: AppointmentUpdate, db: Session = Depends(get_db), user=Depends(require("write"))):
    a = _get(db, Appointment, aid, "نوبت")
    data = body.model_dump(exclude_unset=True)
    allow = data.pop("allow_outside_hours", False)
    if "status" in data and data["status"] not in ("booked", "done", "cancelled", "no_show"):
        raise HTTPException(400, "وضعیت نامعتبر")
    timing = {"service_id", "staff_id", "start_at", "duration_minutes"} & data.keys()
    if timing and a.status != "booked":
        raise HTTPException(400, "فقط نوبت‌های رزرو (انجام‌نشده) قابل جابه‌جایی هستند")
    for k, v in data.items():
        setattr(a, k, v)
    if "start_at" in data:
        a.time_unknown = None  # the real hour is set now
    svc = db.get(Service, a.service_id) if a.service_id else None
    if svc and a.duration_minutes == svc.duration_minutes:
        a.duration_minutes = None
    if timing and not a.time_unknown:
        a.staff_id = accounting.default_staff_id(db, a.service_id, a.staff_id)
        db.flush()
        _ensure_bookable(db, a.service_id, a.staff_id, a.start_at, customer_id=a.customer_id, duration_minutes=a.duration_minutes,
                         exclude_id=a.id, allow_outside_hours=allow)
    audit(db, "appointment.update", "appointment", a.id, {k: str(v) for k, v in data.items()}, user=user)
    db.commit()
    return _appt(db, a)


@router.patch("/appointments/{aid}")
def update_appointment(aid: int, status: str, db: Session = Depends(get_db), user=Depends(require("write"))):
    if status not in ("booked", "done", "cancelled", "no_show"):
        raise HTTPException(400, "وضعیت نامعتبر")
    a = _get(db, Appointment, aid, "نوبت")
    if status == "booked" and a.status != "booked":
        # re-activating a cancelled / no-show appointment takes its slot back: it must still be free and in the future
        _ensure_bookable(db, a.service_id, a.staff_id, a.start_at, customer_id=a.customer_id, duration_minutes=a.duration_minutes,
                         exclude_id=a.id)
    was_booked = a.status == "booked"
    a.status = status
    audit(db, "appointment.status", "appointment", a.id, {"status": status}, user=user)
    db.commit()
    # a cancelled future appointment frees its time: tell the UI so it can offer it to the waiting (VIP) list
    freed = _freed_slot(db, a, a.start_at) if status == "cancelled" and was_booked else None
    return {"ok": True, "freed": freed}


def _freed_slot(db: Session, a: Appointment, start_at: datetime) -> dict | None:
    """The slot an appointment no longer uses, if it is still in the future and someone is waiting."""
    if start_at < local_now():
        return None
    waiting = db.scalar(select(func.count(WaitlistEntry.id)).where(WaitlistEntry.status == "waiting")) or 0
    svc = db.get(Service, a.service_id) if a.service_id else None
    return {"appointment_id": a.id, "start_at": start_at.isoformat(timespec="minutes"), "service_id": a.service_id,
            "service": svc.name if svc else None, "staff_id": a.staff_id,
            "duration_minutes": scheduling.appointment_minutes(db, a), "waiting": waiting}


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
    book_outside_hours: bool = False
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
    book_at = body.book_at
    service_id = body.service_id
    if not book_at and not appointment_id and service_id and settings_store.get(db, "booking.auto"):
        slots = scheduling.find_slots(db, service_id, body.staff_id, count=1, duration_minutes=body.book_duration)
        if slots:
            book_at = datetime.fromisoformat(slots[0]["start_at"])
    if book_at:
        appointment_id = _book(db, c, service_id, body.staff_id, book_at, body.notes, user, duration_minutes=body.book_duration,
                               allow_outside_hours=body.book_outside_hours).id
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
    return _dep(d, c.full_name, db)


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
    appointment_ids: list[int] = []  # appointments this invoice settles (marked done)


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
    freed: list[dict] = []
    done_at = inv.issued_at.replace(second=0, microsecond=0)
    for aid in sorted({*body.appointment_ids, *([body.appointment_id] if body.appointment_id else [])}):
        a = db.get(Appointment, aid)
        if a and a.customer_id == c.id and a.status == "booked":
            a.status = "done"
            a.invoice_id = inv.id
            if a.start_at.date() > done_at.date():
                # done earlier than the booked day: record when it really happened and release the reserved time
                a.original_start_at = a.start_at
                a.start_at = done_at
                slot = _freed_slot(db, a, a.original_start_at)
                if slot:
                    freed.append(slot)
            audit(db, "appointment.done_by_invoice", "appointment", a.id,
                  {"invoice": inv.number, "released": a.original_start_at.isoformat() if a.original_start_at else None}, user=user)
    learning.refresh_price_stats(db)
    db.commit()
    return {**_inv(inv, c.full_name), "freed": freed}


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
    summary = accounting.ledger_summary(rows)
    return {"rows": rows, "total_debit": summary["turnover_debit"], "total_credit": summary["turnover_credit"],
            "entries": db.scalar(select(func.count(JournalEntry.id))) or 0, **summary}


def _day_range(start: date | None, end: date | None) -> tuple[datetime | None, datetime | None]:
    """Inclusive day range -> [start 00:00, day after end 00:00)."""
    return (datetime.combine(start, datetime.min.time()) if start else None,
            datetime.combine(end + timedelta(days=1), datetime.min.time()) if end else None)


@router.get("/ledger/journal")
def journal(limit: int = 200, offset: int = 0, start: date | None = None, end: date | None = None,
            ref_type: str | None = None, q: str | None = None,
            db: Session = Depends(get_db), _=Depends(require("finance"))):
    accs = {a.id: a for a in db.scalars(select(LedgerAccount))}
    qs = select(JournalEntry)
    t0, t1 = _day_range(start, end)
    if t0:
        qs = qs.where(JournalEntry.at >= t0)
    if t1:
        qs = qs.where(JournalEntry.at < t1)
    if ref_type:
        qs = qs.where(JournalEntry.ref_type.in_(ref_type.split(",")))
    if q and q.strip():
        qs = qs.where(JournalEntry.description.contains(q.strip()))
    total = db.scalar(select(func.count()).select_from(qs.subquery())) or 0
    entries = list(db.scalars(qs.order_by(JournalEntry.at.desc(), JournalEntry.id.desc())
                              .offset(max(offset, 0)).limit(max(1, min(limit, 1000)))))
    cust_ids = {l.customer_id for e in entries for l in e.lines if l.customer_id}
    customers = {c.id: c.full_name for c in db.scalars(select(Customer).where(Customer.id.in_(cust_ids)))} if cust_ids else {}
    details = accounting._ref_details(db, entries)
    out = []
    for e in entries:
        # traditional journal layout: debit lines first, then credit lines
        lines = sorted(e.lines, key=lambda l: (0 if l.debit else 1, l.id))
        cust = next((customers[l.customer_id] for l in lines if l.customer_id in customers), None)
        out.append({"id": e.id, "at": e.at.isoformat(), "description": e.description, "ref_type": e.ref_type, "ref_id": e.ref_id,
                    "ref_label": accounting.REF_LABELS.get(e.ref_type, "سند دستی"), "detail": details.get((e.ref_type, e.ref_id), ""),
                    "customer": cust, "amount": sum(l.debit for l in lines), "credit_total": sum(l.credit for l in lines),
                    "lines": [{"account_id": l.account_id, "code": accs[l.account_id].code, "name": accs[l.account_id].name,
                               "type": accs[l.account_id].type, "parent": accs[l.account_id].parent_code, "account": f"{accs[l.account_id].code} {accs[l.account_id].name}",
                               "debit": l.debit, "credit": l.credit} for l in lines]})
    return {"total": total, "offset": offset, "entries": out}


@router.get("/ledger/accounts/{account_id}/statement")
def account_statement(account_id: int, start: date | None = None, end: date | None = None,
                      db: Session = Depends(get_db), _=Depends(require("finance"))):
    """Turnover of one ledger account (a cash box, bank, card, POS or any other account)."""
    acc = _get(db, LedgerAccount, account_id, "حساب")
    t0, t1 = _day_range(start, end)
    return accounting.account_statement(db, acc, t0, t1)


# ---------------------------------------------------------------- waiting list (VIP)
class WaitlistIn(BaseModel):
    customer_id: int | None = None
    customer_name: str | None = None
    customer_mobile: str | None = None
    service_id: int | None = None
    staff_id: int | None = None
    vip: bool = True
    preference: str = ""


class WaitlistUpdate(BaseModel):
    service_id: int | None = None
    staff_id: int | None = None
    vip: bool | None = None
    preference: str | None = None


class WaitlistBookIn(BaseModel):
    start_at: datetime
    service_id: int | None = None
    staff_id: int | None = None
    duration_minutes: int | None = None
    allow_outside_hours: bool = False


def _wait(db: Session, w: WaitlistEntry) -> dict:
    c = db.get(Customer, w.customer_id)
    svc = db.get(Service, w.service_id) if w.service_id else None
    person = db.get(Staff, w.staff_id) if w.staff_id else None
    return {"id": w.id, "customer_id": w.customer_id, "customer": c.full_name if c else None, "customer_mobile": c.mobile if c else None,
            "service_id": w.service_id, "service": svc.name if svc else None, "line": svc.line.name if svc else None,
            "line_id": svc.line_id if svc else None, "staff_id": w.staff_id, "staff": person.full_name if person else None,
            "vip": w.vip, "preference": w.preference, "status": w.status, "appointment_id": w.appointment_id,
            "created_at": w.created_at.isoformat(timespec="minutes")}


@router.get("/waitlist")
def waitlist(status: str = "waiting", db: Session = Depends(get_db), _=Depends(require("read"))):
    q = select(WaitlistEntry).order_by(WaitlistEntry.vip.desc(), WaitlistEntry.created_at)
    if status != "all":
        q = q.where(WaitlistEntry.status == status)
    return [_wait(db, w) for w in db.scalars(q.limit(500))]


@router.post("/waitlist")
def add_waitlist(body: WaitlistIn, db: Session = Depends(get_db), user=Depends(require("write"))):
    c = _customer(db, body.customer_id, body.customer_name, body.customer_mobile, user)
    dup = db.scalar(select(WaitlistEntry).where(WaitlistEntry.customer_id == c.id, WaitlistEntry.status == "waiting",
                                                WaitlistEntry.service_id == body.service_id))
    if dup:
        raise HTTPException(409, "این مشتری برای همین خدمت در لیست انتظار هست")
    w = WaitlistEntry(customer_id=c.id, service_id=body.service_id, staff_id=body.staff_id, vip=body.vip, preference=body.preference)
    db.add(w)
    db.flush()
    audit(db, "waitlist.add", "waitlist", w.id, {"customer": c.id}, user=user)
    db.commit()
    return _wait(db, w)


@router.put("/waitlist/{wid}")
def edit_waitlist(wid: int, body: WaitlistUpdate, db: Session = Depends(get_db), user=Depends(require("write"))):
    w = _get(db, WaitlistEntry, wid, "ردیف لیست انتظار")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(w, k, v)
    audit(db, "waitlist.edit", "waitlist", w.id, {}, user=user)
    db.commit()
    return _wait(db, w)


@router.delete("/waitlist/{wid}")
def remove_waitlist(wid: int, db: Session = Depends(get_db), user=Depends(require("write"))):
    w = _get(db, WaitlistEntry, wid, "ردیف لیست انتظار")
    w.status = "removed"
    audit(db, "waitlist.remove", "waitlist", w.id, {}, user=user)
    db.commit()
    return {"ok": True}


@router.get("/waitlist/offers")
def waitlist_offers(start_at: datetime, service_id: int | None = None, staff_id: int | None = None, duration: int | None = None,
                    db: Session = Depends(get_db), _=Depends(require("read"))):
    """Waiting customers ranked for a freed time: VIP first, then same service, then same line, then who waited longest.
    Each one says whether the time really fits them (their own service length, staff and other appointments)."""
    freed_svc = db.get(Service, service_id) if service_id else None
    out = []
    for w in db.scalars(select(WaitlistEntry).where(WaitlistEntry.status == "waiting")):
        svc_id = w.service_id or service_id
        svc = db.get(Service, svc_id) if svc_id else None
        same_service = bool(service_id and svc_id == service_id)
        same_line = bool(freed_svc and svc and svc.line_id == freed_svc.line_id)
        staff = w.staff_id or (staff_id if same_line else None)
        minutes = duration if same_service and duration else None
        errors = scheduling.validate_slot(db, svc_id, staff, start_at, duration_minutes=minutes, customer_id=w.customer_id)
        out.append({**_wait(db, w), "fits": not errors, "reason": " | ".join(e["message"] for e in errors),
                    "same_service": same_service, "same_line": same_line, "offer_service_id": svc_id,
                    "offer_service": svc.name if svc else None, "offer_staff_id": staff,
                    "offer_duration": minutes or (svc.duration_minutes if svc else None),
                    "overridable": bool(errors) and all(e["overridable"] for e in errors)})
    out.sort(key=lambda x: (not x["fits"], not x["vip"], not x["same_service"], not x["same_line"], x["created_at"]))
    return out


@router.post("/waitlist/{wid}/book")
def book_from_waitlist(wid: int, body: WaitlistBookIn, db: Session = Depends(get_db), user=Depends(require("write"))):
    w = _get(db, WaitlistEntry, wid, "ردیف لیست انتظار")
    if w.status != "waiting":
        raise HTTPException(400, "این مشتری دیگر در لیست انتظار نیست")
    c = _get(db, Customer, w.customer_id, "مشتری")
    a = _book(db, c, body.service_id or w.service_id, body.staff_id or w.staff_id, body.start_at,
              "از لیست انتظار" + (" (VIP)" if w.vip else ""), user, duration_minutes=body.duration_minutes,
              allow_outside_hours=body.allow_outside_hours)
    w.status = "booked"
    w.appointment_id = a.id
    audit(db, "waitlist.book", "waitlist", w.id, {"appointment": a.id}, user=user)
    db.commit()
    return _appt(db, a, c.full_name)
