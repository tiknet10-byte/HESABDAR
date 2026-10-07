from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Integer, String, cast, func, or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..models import Appointment, Customer, Deposit, Invoice, Payment, Service, Staff, WaitlistEntry
from ..services import accounting, customer_merge
from ..services.audit import audit
from ..services.search import fa_like
from ..services.textutil import normalize_mobile, to_en_digits
from .deps import require

router = APIRouter(prefix="/api/customers", tags=["customers"])


class CustomerIn(BaseModel):
    code: str | None = None  # customer code; empty = next free number
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
            "code": c.legacy_code, "legacy_code": c.legacy_code, "mobile_issue": c.mobile_issue, "mobile_raw": c.mobile_raw,
            "other_mobiles": c.other_mobiles or [], "other_codes": c.other_codes or [],
            "created_at": c.created_at.isoformat(), **(extra or {})}


def _activity():  # noqa: ANN202
    """Per customer: purchases (invoices here + services brought over from the previous software), visits, last visit."""
    inv = (select(Invoice.customer_id.label("cid"), func.sum(Invoice.total).label("amt"),
                  func.count(func.distinct(func.date(Invoice.issued_at))).label("n"), func.max(Invoice.issued_at).label("last"))
           .where(Invoice.status != "void").group_by(Invoice.customer_id).subquery())
    # completed appointments without an invoice = history (e.g. receipts of the previous software)
    hist = (select(Appointment.customer_id.label("cid"), func.sum(Appointment.quoted_price).label("amt"),
                   func.count(func.distinct(func.date(Appointment.start_at))).label("n"), func.max(Appointment.start_at).label("last"))
            .where(Appointment.status == "done", Appointment.invoice_id.is_(None)).group_by(Appointment.customer_id).subquery())
    held = (select(Deposit.customer_id.label("cid"), func.sum(Deposit.amount).label("amt"))
            .where(Deposit.status == "held").group_by(Deposit.customer_id).subquery())
    spent = func.coalesce(inv.c.amt, 0) + func.coalesce(hist.c.amt, 0)
    visits = func.coalesce(inv.c.n, 0) + func.coalesce(hist.c.n, 0)
    last = func.max(func.coalesce(inv.c.last, hist.c.last), func.coalesce(hist.c.last, inv.c.last))
    return inv, hist, held, spent, visits, last


SORTS = {"recent", "code", "name", "spent", "last_visit", "visits"}


@router.get("")
def list_customers(q: str = "", limit: int = 100, offset: int = 0, sort: str = "code", filter: str = "",  # noqa: A002
                   db: Session = Depends(get_db), _=Depends(require("read"))):
    inv, hist, held, spent, visits, last = _activity()
    stmt = (select(Customer, spent.label("spent"), func.coalesce(hist.c.amt, 0).label("spent_old"),
                   func.coalesce(held.c.amt, 0).label("held"), visits.label("visits"), last.label("last"))
            .outerjoin(inv, inv.c.cid == Customer.id).outerjoin(hist, hist.c.cid == Customer.id).outerjoin(held, held.c.cid == Customer.id))
    if q:
        like = f"%{q}%"
        mob = normalize_mobile(q)
        code = to_en_digits(q).strip()
        stmt = stmt.where(or_(fa_like(Customer.full_name, q), Customer.mobile.like(f"%{mob or code}%"), Customer.instagram.like(like),
                              Customer.legacy_code == code, Customer.mobile_raw.like(f"%{code}%"),
                              # numbers / codes of duplicate records merged into this customer
                              cast(Customer.other_mobiles, String).like(f"%{mob or code}%"),
                              cast(Customer.other_codes, String).like(f'%"{code}"%')))
    if filter == "held":
        stmt = stmt.where(held.c.amt > 0)
    elif filter == "no_mobile":
        stmt = stmt.where(Customer.mobile.is_(None))
    elif filter == "mobile_issue":
        stmt = stmt.where(Customer.mobile_issue.in_(("invalid", "duplicate")))
    elif filter == "no_history":
        stmt = stmt.where(visits == 0, held.c.amt.is_(None))
    order = {"recent": [Customer.id.desc()], "name": [Customer.full_name],
             "code": [func.cast(Customer.legacy_code, Integer).desc(), Customer.id.desc()],
             "spent": [spent.desc(), Customer.id.desc()], "visits": [visits.desc(), Customer.id.desc()],
             "last_visit": [last.desc().nulls_last(), Customer.id.desc()]}[sort if sort in SORTS else "code"]
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(*order).limit(min(limit, 500)).offset(max(offset, 0))).all()
    return {"total": total, "items": [_c(c, {"total_spent": int(sp or 0), "spent_old": int(old or 0), "deposits_held": int(h or 0),
                                             "visits": int(v or 0), "last_visit": str(lv)[:16] if lv else None})
                                      for c, sp, old, h, v, lv in rows]}


@router.post("")
def create_customer(body: CustomerIn, db: Session = Depends(get_db), user=Depends(require("write"))):
    mobile = normalize_mobile(body.mobile)
    if body.mobile and not mobile:
        raise HTTPException(400, "شماره موبایل معتبر نیست")
    if mobile and (db.scalar(select(Customer).where(Customer.mobile == mobile)) or customer_merge.find_by_other_mobile(db, mobile)):
        raise HTTPException(409, "مشتری با این شماره موبایل وجود دارد")
    code = _free_code(db, body.code)
    data = body.model_dump(exclude={"code"})
    c = Customer(**{**data, "mobile": mobile, "legacy_code": code, "instagram": (body.instagram or "").lstrip("@").lower() or None})
    db.add(c)
    db.flush()
    audit(db, "customer.create", "customer", c.id, {"name": c.full_name}, user=user)
    db.commit()
    return _c(c)


def _unused(cids: list[int] | None = None):  # noqa: ANN202
    """Customers with no history at all: no appointment, deposit, invoice, payment or waiting-list entry."""
    q = select(Customer)
    for m in (Appointment, Deposit, Invoice, Payment, WaitlistEntry):
        q = q.where(~Customer.id.in_(select(m.customer_id)))
    if cids is not None:
        q = q.where(Customer.id.in_(cids or [-1]))
    return q


@router.get("/same-name")
def same_name(name: str, exclude: int | None = None, db: Session = Depends(get_db), _=Depends(require("read"))):
    """Customers with exactly this first and last name - asked about before adding a new one (same person?)."""
    return customer_merge.same_name(db, name, exclude)


@router.get("/duplicates")
def duplicates(db: Session = Depends(get_db), _=Depends(require("read"))):
    """Groups of customers with the same full name (and different / missing mobiles) for the user to check."""
    return customer_merge.duplicate_groups(db)


class MergeIn(BaseModel):
    keep_id: int
    drop_ids: list[int]


@router.post("/merge")
def merge_customers(body: MergeIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    """Same person registered more than once: everything of the others moves to `keep_id`."""
    keep = db.get(Customer, body.keep_id) or _404()
    done = []
    for did in dict.fromkeys(body.drop_ids):
        if did == keep.id:
            continue
        drop = db.get(Customer, did) or _404()
        done.append(customer_merge.merge(db, keep, drop, user=user))
    db.commit()
    return {**_c(keep), "merged": done}


class IdsIn(BaseModel):
    ids: list[int]


@router.post("/not-same")
def not_same(body: IdsIn, db: Session = Depends(get_db), user=Depends(require("write"))):
    """Same name, different people: don't suggest them as duplicates again."""
    n = customer_merge.mark_different(db, body.ids, user=user)
    db.commit()
    return {"ok": True, "pairs": n}


class MobileIn(BaseModel):
    mobile: str


@router.post("/{cid}/mobiles")
def add_mobile(cid: int, body: MobileIn, db: Session = Depends(get_db), user=Depends(require("write"))):
    """Another number of the same person (e.g. chosen as 'same person' when adding a customer with a new number)."""
    c = db.get(Customer, cid) or _404()
    m = normalize_mobile(body.mobile)
    if not m:
        raise HTTPException(400, "شماره موبایل معتبر نیست")
    owner = db.scalar(select(Customer).where(Customer.mobile == m)) or customer_merge.find_by_other_mobile(db, m)
    if owner is not None and owner.id != c.id:
        raise HTTPException(409, f"این شماره متعلق به «{owner.full_name}» است؛ اگر یک نفرند، از «مشتریان هم‌نام» یکی کنید")
    if owner is None:
        if not c.mobile:
            c.mobile, c.mobile_issue, c.mobile_raw = m, None, None
        else:
            c.other_mobiles = [*(c.other_mobiles or []), m]
        audit(db, "customer.add_mobile", "customer", c.id, {"mobile": m}, user=user)
    db.commit()
    return _c(c)


@router.get("/cleanup")
def cleanup_candidates(issues: str = "invalid,duplicate", db: Session = Depends(get_db), _=Depends(require("write"))):
    """Customers without any service/deposit whose mobile is wrong, incomplete, duplicate or missing - to delete in bulk."""
    wanted = [i for i in issues.split(",") if i in ("invalid", "duplicate", "missing")]
    q = _unused()
    conds = [Customer.mobile_issue.in_(wanted)] if wanted else []
    if "missing" in wanted:
        conds.append(Customer.mobile.is_(None) & Customer.mobile_issue.is_(None))
    if not conds:
        return {"total": 0, "items": []}
    rows = db.scalars(q.where(or_(*conds)).order_by(Customer.id).limit(5000)).all()
    return {"total": len(rows), "items": [_c(c) for c in rows]}


class BulkDeleteIn(BaseModel):
    ids: list[int]


@router.post("/bulk-delete")
def bulk_delete(body: BulkDeleteIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    """Delete many customers at once - only those that have no history; the others are left untouched."""
    rows = list(db.scalars(_unused(body.ids)))
    accounting.delete_customers(db, rows)
    audit(db, "customer.bulk_delete", "customer", None, {"count": len(rows), "ids": [c.id for c in rows][:500]}, user=user)
    db.commit()
    return {"deleted": len(rows), "skipped": len(set(body.ids)) - len(rows)}


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
    other = (db.scalar(select(Customer).where(Customer.mobile == mobile, Customer.id != cid))
             or customer_merge.find_by_other_mobile(db, mobile)) if mobile else None
    if other and other.id != cid:
        raise HTTPException(409, f"این شماره متعلق به «{other.full_name}» است")
    if mobile and mobile in (c.other_mobiles or []):  # one of their extra numbers becomes the main one
        c.other_mobiles = [x for x in [*(c.other_mobiles or []), c.mobile] if x and x != mobile] or None
    if body.code is not None and to_en_digits(body.code).strip() != (c.legacy_code or ""):
        c.legacy_code = _free_code(db, body.code, cid)
    for k, v in body.model_dump(exclude={"code"}).items():
        setattr(c, k, v)
    c.mobile = mobile
    if mobile and c.mobile_issue:  # the number was corrected
        c.mobile_issue = c.mobile_raw = None
    audit(db, "customer.update", "customer", c.id, body.model_dump(), user=user)
    db.commit()
    return _c(c)


def _free_code(db: Session, code: str | None, cid: int | None = None) -> str:
    code = to_en_digits(code or "").strip()
    if not code:
        return accounting.next_customer_code(db)
    other = db.scalar(select(Customer).where(Customer.legacy_code == code, Customer.id != (cid or 0)))
    if other:
        raise HTTPException(409, f"کد {code} متعلق به «{other.full_name}» است")
    return code


def _404():  # noqa: ANN202
    raise HTTPException(404, "مشتری یافت نشد")
