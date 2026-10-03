"""Service lines, services, staff, payment accounts (POS / cards / bank accounts)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.security import mask_card
from ..models import PaymentAccount, Service, ServiceLine, Staff
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
def lines(db: Session = Depends(get_db), _=Depends(require("read"))):
    return [_line(l) for l in db.scalars(select(ServiceLine).order_by(ServiceLine.id))]


@router.post("/lines")
def create_line(body: LineIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    l = ServiceLine(**body.model_dump())
    db.add(l)
    db.flush()
    accounting.revenue_account_for_line(db, l.name)
    audit(db, "line.create", "line", l.id, body.model_dump(), user=user)
    db.commit()
    return _line(l)


@router.put("/lines/{lid}")
def update_line(lid: int, body: LineIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    l = db.get(ServiceLine, lid) or _404()
    for k, v in body.model_dump().items():
        setattr(l, k, v)
    audit(db, "line.update", "line", l.id, body.model_dump(), user=user)
    db.commit()
    return _line(l)


# ---------------------------------------------------------------- services
@router.get("/services")
def services(db: Session = Depends(get_db), _=Depends(require("read"))):
    return [_service(s) for s in db.scalars(select(Service).order_by(Service.line_id, Service.name))]


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


@router.get("/services/classify")
def classify(text: str, amount: int | None = None, customer_id: int | None = None, db: Session = Depends(get_db),
             _=Depends(require("read"))):
    if amount:
        return learning.guess_service_for_deposit(db, amount, customer_id, text)
    return learning.classify_text(db, text)


# ---------------------------------------------------------------- staff
@router.get("/staff")
def staff(db: Session = Depends(get_db), _=Depends(require("read"))):
    return [{"id": p.id, "full_name": p.full_name, "mobile": p.mobile, "line_id": p.line_id,
             "commission_percent": p.commission_percent, "is_active": p.is_active} for p in db.scalars(select(Staff).order_by(Staff.id))]


@router.post("/staff")
def create_staff(body: StaffIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    p = Staff(**body.model_dump())
    db.add(p)
    db.flush()
    audit(db, "staff.create", "staff", p.id, body.model_dump(), user=user)
    db.commit()
    return {"id": p.id, **body.model_dump()}


@router.put("/staff/{pid}")
def update_staff(pid: int, body: StaffIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    p = db.get(Staff, pid) or _404()
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    audit(db, "staff.update", "staff", p.id, body.model_dump(), user=user)
    db.commit()
    return {"id": p.id, **body.model_dump()}


# ---------------------------------------------------------------- payment accounts
@router.get("/accounts")
def accounts(db: Session = Depends(get_db), _=Depends(require("read"))):
    return [_account(a) for a in db.scalars(select(PaymentAccount).order_by(PaymentAccount.id))]


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


def _404():  # noqa: ANN202
    raise HTTPException(404, "یافت نشد")
