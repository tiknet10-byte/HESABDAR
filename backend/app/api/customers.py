from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..models import Appointment, Customer, Deposit, Invoice, Payment, Service, Staff
from ..services import accounting
from ..services.audit import audit
from ..services.textutil import normalize_mobile
from .deps import require

router = APIRouter(prefix="/api/customers", tags=["customers"])


class CustomerIn(BaseModel):
    full_name: str
    mobile: str | None = None
    instagram: str | None = None
    whatsapp: str | None = None
    birth_date: str | None = None
    notes: str = ""
    tags: list[str] = []


def _c(c: Customer, extra: dict | None = None) -> dict:
    return {"id": c.id, "full_name": c.full_name, "mobile": c.mobile, "instagram": c.instagram, "whatsapp": c.whatsapp,
            "birth_date": c.birth_date, "notes": c.notes, "tags": c.tags, "source": c.source, "known_cards": c.known_cards,
            "legacy_code": c.legacy_code, "created_at": c.created_at.isoformat(), **(extra or {})}


@router.get("")
def list_customers(q: str = "", limit: int = 100, offset: int = 0, db: Session = Depends(get_db), _=Depends(require("read"))):
    stmt = select(Customer)
    if q:
        like = f"%{q}%"
        mob = normalize_mobile(q)
        stmt = stmt.where(or_(Customer.full_name.like(like), Customer.mobile.like(f"%{mob or q}%"), Customer.instagram.like(like),
                                Customer.legacy_code == q.strip()))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Customer.id.desc()).limit(min(limit, 500)).offset(offset)).all()
    stats = dict(db.execute(select(Invoice.customer_id, func.sum(Invoice.total)).where(Invoice.status != "void",
                            Invoice.customer_id.in_([c.id for c in rows])).group_by(Invoice.customer_id)).all())
    held = dict(db.execute(select(Deposit.customer_id, func.sum(Deposit.amount)).where(Deposit.status == "held",
                           Deposit.customer_id.in_([c.id for c in rows])).group_by(Deposit.customer_id)).all())
    return {"total": total, "items": [_c(c, {"total_spent": int(stats.get(c.id) or 0), "deposits_held": int(held.get(c.id) or 0)}) for c in rows]}


@router.post("")
def create_customer(body: CustomerIn, db: Session = Depends(get_db), user=Depends(require("write"))):
    mobile = normalize_mobile(body.mobile)
    if body.mobile and not mobile:
        raise HTTPException(400, "شماره موبایل معتبر نیست")
    if mobile and db.scalar(select(Customer).where(Customer.mobile == mobile)):
        raise HTTPException(409, "مشتری با این شماره موبایل وجود دارد")
    c = Customer(**{**body.model_dump(), "mobile": mobile, "instagram": (body.instagram or "").lstrip("@").lower() or None})
    db.add(c)
    db.flush()
    audit(db, "customer.create", "customer", c.id, {"name": c.full_name}, user=user)
    db.commit()
    return _c(c)


@router.get("/{cid}")
def get_customer(cid: int, db: Session = Depends(get_db), _=Depends(require("read"))):
    c = db.get(Customer, cid) or _404()
    invoices = db.scalars(select(Invoice).where(Invoice.customer_id == cid).order_by(Invoice.issued_at.desc())).all()
    deposits = db.scalars(select(Deposit).where(Deposit.customer_id == cid).order_by(Deposit.received_at.desc())).all()
    payments = db.scalars(select(Payment).where(Payment.customer_id == cid).order_by(Payment.paid_at.desc())).all()
    appts = db.scalars(select(Appointment).where(Appointment.customer_id == cid).order_by(Appointment.start_at.desc())).all()
    return _c(c, {
        "balance": accounting.customer_balance(db, cid),
        "invoices": [{"id": i.id, "number": i.number, "issued_at": i.issued_at.isoformat(), "total": i.total, "paid": i.paid,
                      "status": i.status, "items": [it.description for it in i.items]} for i in invoices],
        "deposits": [{"id": d.id, "amount": d.amount, "status": d.status, "received_at": d.received_at.isoformat(),
                      "service_id": d.service_id, "source": d.source} for d in deposits],
        "payments": [{"id": p.id, "amount": p.amount, "paid_at": p.paid_at.isoformat(), "account_id": p.payment_account_id} for p in payments],
        "appointments": [{"id": a.id, "start_at": a.start_at.isoformat(), "status": a.status, "service_id": a.service_id} for a in appts],
        "history": _service_history(db, invoices, appts),
        "upcoming": [{"id": a.id, "start_at": a.start_at.isoformat(timespec="minutes"), "service": _svc_name(db, a.service_id),
                      "staff": _staff_name(db, a.staff_id)} for a in reversed(appts) if a.status == "booked"],
    })


def _svc_name(db: Session, sid: int | None) -> str | None:
    s = db.get(Service, sid) if sid else None
    return s.name if s else None


def _staff_name(db: Session, pid: int | None) -> str | None:
    p = db.get(Staff, pid) if pid else None
    return p.full_name if p else None


def _service_history(db: Session, invoices: list[Invoice], appts: list[Appointment]) -> list[dict]:
    """Every service the customer received, newest first: invoice lines, plus completed appointments that have no
    invoice here (e.g. history brought over from the previous software)."""
    out = []
    for i in invoices:
        if i.status == "void":
            continue
        for it in i.items:
            out.append({"date": i.issued_at.isoformat(timespec="minutes"), "service": it.description or _svc_name(db, it.service_id),
                        "staff": _staff_name(db, it.staff_id), "amount": it.net_amount if it.net_amount is not None else it.unit_price * it.quantity - it.discount,
                        "source": "invoice", "invoice_id": i.id, "invoice_number": i.number})
    for a in appts:
        if a.status != "done" or a.invoice_id:
            continue
        name = _svc_name(db, a.service_id)
        if not name and "خدمت: " in (a.notes or ""):
            name = a.notes.split("خدمت: ", 1)[1].split(" - ")[0]
        out.append({"date": (a.start_at).isoformat(timespec="minutes"), "service": name or "—", "staff": _staff_name(db, a.staff_id),
                    "amount": a.quoted_price or None, "source": "import" if (a.notes or "").startswith("انتقال از نرم‌افزار قبلی") else "appointment",
                    "notes": a.notes})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


@router.put("/{cid}")
def update_customer(cid: int, body: CustomerIn, db: Session = Depends(get_db), user=Depends(require("write"))):
    c = db.get(Customer, cid) or _404()
    mobile = normalize_mobile(body.mobile)
    if body.mobile and not mobile:
        raise HTTPException(400, "شماره موبایل معتبر نیست")
    other = db.scalar(select(Customer).where(Customer.mobile == mobile, Customer.id != cid)) if mobile else None
    if other:
        raise HTTPException(409, f"این شماره متعلق به «{other.full_name}» است")
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    c.mobile = mobile
    audit(db, "customer.update", "customer", c.id, body.model_dump(), user=user)
    db.commit()
    return _c(c)


def _404():  # noqa: ANN202
    raise HTTPException(404, "مشتری یافت نشد")
