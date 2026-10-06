"""Service lines, services, staff, payment accounts (POS / cards / bank accounts)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.security import mask_card
from ..models import (
    Appointment,
    Deposit,
    Expense,
    Invoice,
    InvoiceItem,
    JournalLine,
    LedgerAccount,
    Payment,
    PaymentAccount,
    Service,
    ServiceLine,
    Staff,
)
from ..services import accounting, learning, service_catalog
from ..services.audit import audit
from .deps import require

router = APIRouter(prefix="/api", tags=["catalog"])


class LineIn(BaseModel):
    code: str | None = None  # empty = next number
    name: str
    color: str = "#c084fc"
    icon: str = "sparkles"
    is_active: bool = True


class ServiceIn(BaseModel):
    code: str | None = None  # empty = next number in its line
    line_id: int
    name: str
    base_price: int = 0
    min_price: int | None = None
    max_price: int | None = None
    default_deposit: int = 0
    duration_minutes: int = 60
    aliases: list[str] = []
    is_active: bool = True


def _check_code(db: Session, model, code: str | None, own_id: int | None = None) -> str | None:  # noqa: ANN001
    """Normalise a hand-entered code (Persian digits allowed) and refuse a duplicate."""
    from ..services.textutil import to_en_digits

    code = to_en_digits(code or "").strip()
    if not code:
        return None
    if not code.isdigit():
        raise HTTPException(400, "کد فقط باید عدد باشد")
    other = db.scalar(select(model).where(model.code == code, model.id != (own_id or 0)))
    if other is not None:
        what = "لاین" if model is ServiceLine else "خدمت"
        where = f" در لاین «{other.line.name}»" if model is Service and other.line else ""
        if not other.is_active:  # hidden from the list, so say where it is
            raise HTTPException(409, f"کد {code} متعلق به {what} بایگانی‌شدهٔ «{other.name}»{where} است؛ آن را از بخش "
                                     f"«بایگانی‌شده» بازگردانید یا کد دیگری بدهید")
        raise HTTPException(409, f"کد {code} متعلق به {what} «{other.name}»{where} است")
    return code


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
    opening_balance: int | None = None  # money already in this account when the system started (Rial)
    opening_at: datetime | None = None


def _line(l: ServiceLine) -> dict:
    return {"id": l.id, "code": l.code, "name": l.name, "color": l.color, "icon": l.icon, "is_active": l.is_active}


def _service(s: Service) -> dict:
    return {"id": s.id, "code": s.code, "line_id": s.line_id, "line": s.line.name if s.line else None,
            "line_code": s.line.code if s.line else None, "name": s.name, "base_price": s.base_price,
            "min_price": s.min_price, "max_price": s.max_price, "default_deposit": s.default_deposit,
            "duration_minutes": s.duration_minutes, "aliases": s.aliases, "is_active": s.is_active,
            "learned_avg_price": s.learned_avg_price, "learned_count": s.learned_count,
            "line_active": bool(s.line and s.line.is_active)}


def _account(a: PaymentAccount) -> dict:
    cfg = {k: ("••••" if any(x in k for x in ("token", "secret", "password", "key")) else v) for k, v in (a.provider_config or {}).items()}
    return {"id": a.id, "kind": a.kind, "name": a.name, "bank_name": a.bank_name, "card_mask": a.card_mask, "iban": a.iban,
            "terminal_id": a.terminal_id, "owner_name": a.owner_name, "provider": a.provider, "provider_config": cfg,
            "is_active": a.is_active}


# ---------------------------------------------------------------- lines
@router.get("/lines")
def lines(all: bool = False, db: Session = Depends(get_db), _=Depends(require("read"))):
    q = select(ServiceLine).order_by(func.cast(ServiceLine.code, Integer), ServiceLine.id)
    if not all:
        q = q.where(ServiceLine.is_active.is_(True))
    return [_line(l) for l in db.scalars(q)]


@router.post("/lines")
def create_line(body: LineIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    key = service_catalog.name_key(body.name)
    l = next((x for x in db.scalars(select(ServiceLine).order_by(ServiceLine.is_active.desc(), ServiceLine.id))
              if service_catalog.name_key(x.name) == key), None)
    archived, restored = 0, l is not None
    if l is not None:
        if l.is_active:
            raise HTTPException(409, f"لاین «{l.name}» وجود دارد")
        l.is_active = True  # re-activate an archived line with the same name (keeps its code and history)
        l.color, l.icon = body.color, body.icon
        archived = db.scalar(select(func.count(Service.id)).where(Service.line_id == l.id, Service.is_active.is_(False))) or 0
    else:
        l = ServiceLine(**{**body.model_dump(), "name": body.name.strip(), "code": _check_code(db, ServiceLine, body.code)})
        db.add(l)
    db.flush()
    accounting.revenue_account_for_line(db, l.name)
    audit(db, "line.create", "line", l.id, body.model_dump(), user=user)
    _names_changed()
    db.commit()
    return {**_line(l), "restored": restored, "archived_services": archived}


@router.post("/lines/{lid}/restore")
def restore_line(lid: int, db: Session = Depends(get_db), user=Depends(require("settings"))):
    l = db.get(ServiceLine, lid) or _404()
    l.is_active = True
    audit(db, "line.restore", "line", l.id, {"name": l.name}, user=user)
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
    code = _check_code(db, ServiceLine, body.code, lid)
    for k, v in body.model_dump(exclude={"code"}).items():
        setattr(l, k, v)
    if code:
        l.code = code
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
    q = select(Service).order_by(func.cast(Service.code, Integer), Service.line_id, Service.name)
    if not all:
        q = q.where(Service.is_active.is_(True))
    return [_service(s) for s in db.scalars(q)]


@router.post("/services")
def create_service(body: ServiceIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    line = db.get(ServiceLine, body.line_id)
    if line is None:
        raise HTTPException(400, "لاین انتخاب‌شده وجود ندارد")
    name = body.name.strip()
    same = service_catalog.find_by_name(db, line.id, name)
    if same is not None and same.is_active:
        raise HTTPException(409, f"«{same.name}» (کد {same.code}) در این لاین وجود دارد")
    code = _check_code(db, Service, body.code, same.id if same else None)
    restored = same is not None
    if same is not None:  # the same service was deleted (archived) before: bring it back instead of a duplicate
        s = same
        for k, v in body.model_dump(exclude={"code"}).items():
            setattr(s, k, v)
        s.name, s.is_active = name, True
        if code:
            s.code = code
    else:
        s = Service(**{**body.model_dump(), "name": name, "code": code})
        db.add(s)
    line.is_active = True
    db.flush()
    learning.learn_text(db, s.name, s.id, weight=3)
    audit(db, "service.restore" if restored else "service.create", "service", s.id, body.model_dump(), user=user)
    db.commit()
    return {**_service(s), "restored": restored}


@router.put("/services/{sid}")
def update_service(sid: int, body: ServiceIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    s = db.get(Service, sid) or _404()
    line = db.get(ServiceLine, body.line_id)
    if line is None:
        raise HTTPException(400, "لاین انتخاب‌شده وجود ندارد")
    same = service_catalog.find_by_name(db, line.id, body.name, exclude=sid)
    if same is not None and same.is_active and body.is_active:
        raise HTTPException(409, f"«{same.name}» (کد {same.code}) در لاین «{line.name}» وجود دارد؛ اگر یک خدمت‌اند «ادغام» کنید")
    old_price, old_line, old_code = s.base_price, s.line_id, s.code
    code = _check_code(db, Service, body.code, sid)
    for k, v in body.model_dump(exclude={"code"}).items():
        setattr(s, k, v)
    s.name = body.name.strip()
    if code:
        s.code = code
    elif old_line != s.line_id and not service_catalog.code_fits_line(s.code, line, _line_codes(db)):
        # moved to another line with the automatic code: number it in the new line (line 3 -> 3xx)
        service_catalog.recode(db, s)
    if s.is_active:
        line.is_active = True
    for a in body.aliases:
        learning.learn_text(db, a, s.id, weight=3)
    audit(db, "service.update", "service", s.id, {**body.model_dump(), "old_price": old_price, "old_code": old_code}, user=user)
    db.commit()
    return {**_service(s), "old_code": old_code if old_code != s.code else None}


def _line_codes(db: Session) -> set[str]:
    return {c for c in db.scalars(select(ServiceLine.code)) if c}


@router.get("/services/health")
def services_health(db: Session = Depends(get_db), _=Depends(require("read"))):
    """Problems in the list of services (archived but sold, duplicates/typos, codes not matching the line ...)."""
    return service_catalog.health(db)


@router.get("/services/{sid}/usage")
def service_usage(sid: int, db: Session = Depends(get_db), _=Depends(require("read"))):
    db.get(Service, sid) or _404()
    return service_catalog.usage(db, [sid]).get(sid) or {"invoices": 0, "old": 0, "deposits": 0, "appointments": 0, "amount": 0}


class MergeIn(BaseModel):
    into_id: int


@router.post("/services/{sid}/merge")
def merge_service(sid: int, body: MergeIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    """Same service registered twice (e.g. a typo in the previous software): move all its records to the other one."""
    src = db.get(Service, sid) or _404()
    dst = db.get(Service, body.into_id) or _404()
    if src.id == dst.id:
        raise HTTPException(400, "یک خدمت را نمی‌توان با خودش ادغام کرد")
    info = {"from": src.name, "from_code": src.code, "into": dst.name, "into_code": dst.code}
    moved = service_catalog.merge(db, src, dst)
    if dst.is_active:
        dst.line.is_active = True
    audit(db, "service.merge", "service", dst.id, {**info, "moved": moved}, user=user)
    db.commit()
    return {**_service(dst), "moved": moved}


@router.post("/services/{sid}/restore")
def restore_service(sid: int, db: Session = Depends(get_db), user=Depends(require("settings"))):
    s = db.get(Service, sid) or _404()
    same = service_catalog.find_by_name(db, s.line_id, s.name, exclude=sid)
    if same is not None and same.is_active:
        raise HTTPException(409, f"«{same.name}» (کد {same.code}) در همین لاین فعال است؛ به‌جای بازگردانی، این دو را «ادغام» کنید")
    s.is_active = True
    s.line.is_active = True
    audit(db, "service.restore", "service", s.id, {"name": s.name, "code": s.code}, user=user)
    db.commit()
    return _service(s)


class RecodeIn(BaseModel):
    ids: list[int] = []  # empty = every service whose code doesn't match its line


@router.post("/services/recode")
def recode_services(body: RecodeIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    lines_ = {l.id: l for l in db.scalars(select(ServiceLine))}
    lcodes = {l.code for l in lines_.values() if l.code}
    q = select(Service).order_by(Service.line_id, func.cast(Service.code, Integer), Service.id)
    if body.ids:
        q = q.where(Service.id.in_(body.ids))
    changed = []
    for s in db.scalars(q):
        if not service_catalog.code_fits_line(s.code, lines_.get(s.line_id), lcodes):
            old = s.code
            changed.append({"id": s.id, "name": s.name, "old": old, "new": service_catalog.recode(db, s)})
    audit(db, "service.recode", "service", "", {"changed": changed}, user=user)
    db.commit()
    return {"changed": changed}


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


@router.get("/services/usage")
def services_usage(db: Session = Depends(get_db), _=Depends(require("read"))):
    """Per service: sales on invoices here and in the service history brought over from the previous software
    (done appointments without an invoice - the same split the revenue report uses). Services never sold are absent."""
    amt = func.coalesce(InvoiceItem.net_amount, InvoiceItem.unit_price * InvoiceItem.quantity - InvoiceItem.discount)
    out: dict[int, dict] = {}

    def row(sid: int) -> dict:
        return out.setdefault(sid, {"service_id": sid, "invoices": 0, "invoice_amount": 0, "invoice_last": None,
                                    "old": 0, "old_amount": 0, "old_last": None})

    q = (select(InvoiceItem.service_id, func.count(func.distinct(InvoiceItem.invoice_id)), func.sum(amt), func.max(Invoice.issued_at))
         .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
         .where(InvoiceItem.service_id.is_not(None), Invoice.status != "void")
         .group_by(InvoiceItem.service_id))
    for sid, n, total, last in db.execute(q):
        r = row(sid)
        r["invoices"], r["invoice_amount"], r["invoice_last"] = int(n or 0), int(total or 0), last
    q = (select(Appointment.service_id, func.count(Appointment.id), func.sum(Appointment.quoted_price), func.max(Appointment.start_at))
         .where(Appointment.service_id.is_not(None), Appointment.status == "done", Appointment.invoice_id.is_(None))
         .group_by(Appointment.service_id))
    for sid, n, total, last in db.execute(q):
        r = row(sid)
        r["old"], r["old_amount"], r["old_last"] = int(n or 0), int(total or 0), last
    return list(out.values())


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


class OpeningIn(BaseModel):
    amount: int  # Rial; may be negative (e.g. an overdrawn bank account)
    at: datetime | None = None


class AdjustIn(BaseModel):
    actual: int  # the real balance counted / read from the bank (Rial)
    note: str = ""


@router.get("/accounts/opening")
def openings(db: Session = Depends(get_db), _=Depends(require("finance"))):
    """Opening balance and current balance of every account (cash box, cards, POS, bank)."""
    out = []
    for row in accounting.account_balances(db):
        pa = db.get(PaymentAccount, row["id"])
        amount, at = accounting.opening_balance(db, pa)
        out.append({**row, "opening": amount, "opening_at": at.isoformat(timespec="minutes") if at else None})
    db.commit()
    return out


@router.put("/accounts/{aid}/opening")
def set_opening(aid: int, body: OpeningIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    a = db.get(PaymentAccount, aid) or _404()
    try:
        accounting.set_opening_balance(db, a, body.amount, body.at, user=user)
    except accounting.AccountingError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"ok": True}


@router.post("/accounts/{aid}/adjust")
def adjust(aid: int, body: AdjustIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    a = db.get(PaymentAccount, aid) or _404()
    diff = accounting.adjust_balance(db, a, body.actual, body.note, user=user)
    db.commit()
    return {"difference": diff}


@router.post("/accounts")
def create_account(body: AccountIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    if body.kind not in ("pos", "card", "bank", "cash", "gateway"):
        raise HTTPException(400, "نوع حساب نامعتبر")
    data = body.model_dump(exclude={"card_number", "opening_balance", "opening_at"})
    a = PaymentAccount(**data, card_mask=mask_card(body.card_number))
    db.add(a)
    db.flush()
    accounting.cash_account_for(db, a)
    if body.opening_balance:
        try:
            accounting.set_opening_balance(db, a, body.opening_balance, body.opening_at, user=user)
        except accounting.AccountingError as exc:
            db.rollback()
            raise HTTPException(400, str(exc)) from exc
    audit(db, "account.create", "payment_account", a.id, {"name": a.name, "kind": a.kind}, user=user)
    db.commit()
    return _account(a)


@router.put("/accounts/{aid}")
def update_account(aid: int, body: AccountIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    a = db.get(PaymentAccount, aid) or _404()
    data = body.model_dump(exclude={"card_number", "provider_config", "opening_balance", "opening_at"})
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
