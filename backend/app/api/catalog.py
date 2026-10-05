"""Service lines, services, staff, payment accounts (POS / cards / bank accounts)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.security import mask_card
from ..models import (
    Appointment,
    Deposit,
    Expense,
    InvoiceItem,
    JournalLine,
    LedgerAccount,
    Payment,
    PaymentAccount,
    Service,
    ServiceLine,
    Staff,
)
from ..services import accounting, learning
from ..services.audit import audit
from .deps import require

router = APIRouter(prefix="/api", tags=["catalog"])


class LineIn(BaseModel):
    name: str
    color: str = "#c084fc"
    icon: str = "sparkles"
    is_active: bool = True


class ServiceIn(BaseModel):
    line_id: int
    name: str
    base_price: int = 0
    min_price: int | None = None
    max_price: int | None = None
    default_deposit: int = 0
    duration_minutes: int = 60
    aliases: list[str] = []
    is_active: bool = True


class StaffIn(BaseModel):
    full_name: str
    mobile: str | None = None
    line_id: int | None = None
    commission_percent: float = 0
    is_active: bool = True


class AccountIn(BaseModel):
    kind: str  # pos | card | bank | cash | gateway
    name: str
    bank_name: str = ""
    card_number: str | None = None
    iban: str | None = None
    terminal_id: str | None = None
    owner_name: str = ""
    provider: str | None = None
    provider_config: dict = {}
    is_active: bool = True


def _line(l: ServiceLine) -> dict:
    return {"id": l.id, "name": l.name, "color": l.color, "icon": l.icon, "is_active": l.is_active}


def _service(s: Service) -> dict:
    return {"id": s.id, "line_id": s.line_id, "line": s.line.name if s.line else None, "name": s.name, "base_price": s.base_price,
            "min_price": s.min_price, "max_price": s.max_price, "default_deposit": s.default_deposit,
            "duration_minutes": s.duration_minutes, "aliases": s.aliases, "is_active": s.is_active,
            "learned_avg_price": s.learned_avg_price, "learned_count": s.learned_count}


def _account(a: PaymentAccount) -> dict:
    cfg = {k: ("••••" if any(x in k for x in ("token", "secret", "password", "key")) else v) for k, v in (a.provider_config or {}).items()}
    return {"id": a.id, "kind": a.kind, "name": a.name, "bank_name": a.bank_name, "card_mask": a.card_mask, "iban": a.iban,
            "terminal_id": a.terminal_id, "owner_name": a.owner_name, "provider": a.provider, "provider_config": cfg,
            "is_active": a.is_active}


# ---------------------------------------------------------------- lines
@router.get("/lines")
def lines(all: bool = False, db: Session = Depends(get_db), _=Depends(require("read"))):
    q = select(ServiceLine).order_by(ServiceLine.id)
    if not all:
        q = q.where(ServiceLine.is_active.is_(True))
    return [_line(l) for l in db.scalars(q)]


@router.post("/lines")
def create_line(body: LineIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    l = db.scalar(select(ServiceLine).where(ServiceLine.name == body.name.strip()))
    if l is not None:
        if l.is_active:
            raise HTTPException(409, "این لاین وجود دارد")
        l.is_active = True  # re-activate an archived line with the same name
        l.color, l.icon = body.color, body.icon
    else:
        l = ServiceLine(**{**body.model_dump(), "name": body.name.strip()})
        db.add(l)
    db.flush()
    accounting.revenue_account_for_line(db, l.name)
    audit(db, "line.create", "line", l.id, body.model_dump(), user=user)
    _names_changed()
    db.commit()
    return _line(l)


@router.put("/lines/{lid}")
def update_line(lid: int, body: LineIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    l = db.get(ServiceLine, lid) or _404()
    if body.name != l.name:
        # keep the line's revenue account (and its history) - just rename it
        acc = accounting.revenue_account_for_line(db, l.name)
        acc.name = f"درآمد {body.name}"
    for k, v in body.model_dump().items():
        setattr(l, k, v)
    audit(db, "line.update", "line", l.id, body.model_dump(), user=user)
    _names_changed()
    db.commit()
    return _line(l)


@router.delete("/lines/{lid}")
def delete_line(lid: int, db: Session = Depends(get_db), user=Depends(require("settings"))):
    """Delete a line with all its services (services used in the books are archived, not erased)."""
    l = db.get(ServiceLine, lid) or _404()
    for s in list(db.scalars(select(Service).where(Service.line_id == lid))):
        _remove_service(db, s)
    used = db.scalar(select(func.count(InvoiceItem.id)).where(InvoiceItem.line_id == lid)) or \
        db.scalar(select(func.count(Service.id)).where(Service.line_id == lid))
    if used:
        l.is_active = False
    else:
        db.delete(l)
    audit(db, "line.delete", "line", lid, {"name": l.name, "archived": bool(used)}, user=user)
    _names_changed()
    db.commit()
    return {"ok": True, "archived": bool(used)}


def _remove_service(db: Session, s: Service) -> bool:
    """Hard-delete an unused service; archive (hide) one that appears in invoices, deposits or appointments."""
    used = any(db.scalar(select(func.count()).select_from(m).where(m.service_id == s.id))
               for m in (InvoiceItem, Deposit, Appointment))
    if used:
        s.is_active = False
    else:
        db.delete(s)
    db.flush()
    return used


# ---------------------------------------------------------------- services
@router.get("/services")
def services(all: bool = False, db: Session = Depends(get_db), _=Depends(require("read"))):
    q = select(Service).order_by(Service.line_id, Service.name)
    if not all:
        q = q.where(Service.is_active.is_(True))
    return [_service(s) for s in db.scalars(q)]


@router.post("/services")
def create_service(body: ServiceIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    s = Service(**body.model_dump())
    db.add(s)
    db.flush()
    learning.learn_text(db, s.name, s.id, weight=3)
    audit(db, "service.create", "service", s.id, body.model_dump(), user=user)
    db.commit()
    return _service(s)


@router.put("/services/{sid}")
def update_service(sid: int, body: ServiceIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    s = db.get(Service, sid) or _404()
    old_price = s.base_price
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    for a in body.aliases:
        learning.learn_text(db, a, s.id, weight=3)
    audit(db, "service.update", "service", s.id, {**body.model_dump(), "old_price": old_price}, user=user)
    db.commit()
    return _service(s)


@router.delete("/services/{sid}")
def delete_service(sid: int, db: Session = Depends(get_db), user=Depends(require("settings"))):
    s = db.get(Service, sid) or _404()
    line_id = s.line_id
    archived = _remove_service(db, s)
    line_removed = False
    if not db.scalar(select(func.count(Service.id)).where(Service.line_id == line_id, Service.is_active.is_(True))):
        line = db.get(ServiceLine, line_id)
        if line:  # last service of the line is gone -> remove the line too
            if db.scalar(select(func.count(Service.id)).where(Service.line_id == line_id)) or \
                    db.scalar(select(func.count(InvoiceItem.id)).where(InvoiceItem.line_id == line_id)):
                line.is_active = False
            else:
                db.delete(line)
            line_removed = True
    audit(db, "service.delete", "service", sid, {"archived": archived, "line_removed": line_removed}, user=user)
    db.commit()
    return {"ok": True, "archived": archived, "line_removed": line_removed}


@router.get("/services/classify")
def classify(text: str, amount: int | None = None, customer_id: int | None = None, db: Session = Depends(get_db),
             _=Depends(require("read"))):
    if amount:
        return learning.guess_service_for_deposit(db, amount, customer_id, text)
    return learning.classify_text(db, text)


# ---------------------------------------------------------------- staff
@router.get("/staff")
def staff(line_id: int | None = None, service_id: int | None = None, db: Session = Depends(get_db), _=Depends(require("read"))):
    """Staff list; filter by line, or by a service (= the staff of that service's line)."""
    if service_id:
        svc = db.get(Service, service_id)
        line_id = svc.line_id if svc else None
    q = select(Staff).order_by(Staff.id)
    if line_id:
        q = q.where(Staff.line_id == line_id)
    lines = {l.id: l.name for l in db.scalars(select(ServiceLine))}
    return [{"id": p.id, "full_name": p.full_name, "mobile": p.mobile, "line_id": p.line_id, "line": lines.get(p.line_id),
             "commission_percent": p.commission_percent, "is_active": p.is_active} for p in db.scalars(q)]


class PayoutIn(BaseModel):
    amount: int
    payment_account_id: int
    description: str = ""


@router.post("/staff/{pid}/payout")
def staff_payout(pid: int, body: PayoutIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    p = db.get(Staff, pid) or _404()
    pa = db.get(PaymentAccount, body.payment_account_id) or _404()
    try:
        accounting.pay_staff(db, p, body.amount, pa, description=body.description, user=user)
    except accounting.AccountingError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"ok": True, "balance": accounting.staff_balance(db, pid)}


@router.post("/staff")
def create_staff(body: StaffIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    p = Staff(**body.model_dump())
    db.add(p)
    db.flush()
    audit(db, "staff.create", "staff", p.id, body.model_dump(), user=user)
    _names_changed()
    db.commit()
    return {"id": p.id, **body.model_dump()}


@router.put("/staff/{pid}")
def update_staff(pid: int, body: StaffIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    p = db.get(Staff, pid) or _404()
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    audit(db, "staff.update", "staff", p.id, body.model_dump(), user=user)
    _names_changed()
    db.commit()
    return {"id": p.id, **body.model_dump()}


# ---------------------------------------------------------------- payment accounts
@router.get("/accounts")
def accounts(all: bool = False, db: Session = Depends(get_db), _=Depends(require("read"))):
    q = select(PaymentAccount).order_by(PaymentAccount.id)
    if not all:
        q = q.where(PaymentAccount.is_active.is_(True))
    return [_account(a) for a in db.scalars(q)]


@router.delete("/accounts/{aid}")
def delete_account(aid: int, db: Session = Depends(get_db), user=Depends(require("settings"))):
    """Delete a POS / card / bank account. If it already has transactions it is archived so the books stay intact."""
    a = db.get(PaymentAccount, aid) or _404()
    used = any(db.scalar(select(func.count()).select_from(m).where(m.payment_account_id == aid)) for m in (Deposit, Payment, Expense))
    if a.ledger_account_id and db.scalar(select(func.count(JournalLine.id)).where(JournalLine.account_id == a.ledger_account_id)):
        used = True
    if used:
        a.is_active = False
    else:
        ledger_id = a.ledger_account_id
        db.delete(a)
        db.flush()
        if ledger_id:
            acc = db.get(LedgerAccount, ledger_id)
            if acc:
                db.delete(acc)
    audit(db, "account.delete", "payment_account", aid, {"name": a.name, "archived": used}, user=user)
    db.commit()
    return {"ok": True, "archived": used}


@router.get("/accounts/balances")
def balances(db: Session = Depends(get_db), _=Depends(require("finance"))):
    out = accounting.account_balances(db)
    db.commit()
    return out


@router.post("/accounts")
def create_account(body: AccountIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    if body.kind not in ("pos", "card", "bank", "cash", "gateway"):
        raise HTTPException(400, "نوع حساب نامعتبر")
    data = body.model_dump(exclude={"card_number"})
    a = PaymentAccount(**data, card_mask=mask_card(body.card_number))
    db.add(a)
    db.flush()
    accounting.cash_account_for(db, a)
    audit(db, "account.create", "payment_account", a.id, {"name": a.name, "kind": a.kind}, user=user)
    db.commit()
    return _account(a)


@router.put("/accounts/{aid}")
def update_account(aid: int, body: AccountIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    a = db.get(PaymentAccount, aid) or _404()
    data = body.model_dump(exclude={"card_number", "provider_config"})
    for k, v in data.items():
        setattr(a, k, v)
    if body.card_number:
        a.card_mask = mask_card(body.card_number)
    # keep secrets that came back masked
    merged = dict(a.provider_config or {})
    for k, v in body.provider_config.items():
        if v != "••••":
            merged[k] = v
    a.provider_config = merged
    audit(db, "account.update", "payment_account", a.id, {"name": a.name}, user=user)
    db.commit()
    return _account(a)


def _names_changed() -> None:
    from .finance import _NAMES
    _NAMES.clear()


def _404():  # noqa: ANN202
    raise HTTPException(404, "یافت نشد")
