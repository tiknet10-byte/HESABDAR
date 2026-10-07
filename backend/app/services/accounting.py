"""Double-entry accounting engine.

Every business operation (deposit, invoice, payment, expense, refund) posts a
balanced journal entry. Reports are derived from documents, while the ledger
guarantees the books always balance (sum of debits == sum of credits).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.events import bus
from ..models import (
    Appointment,
    Customer,
    Deposit,
    Expense,
    Invoice,
    InvoiceItem,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Payment,
    PaymentAccount,
    Service,
    Staff,
    local_now,
)
from .audit import audit
from .textutil import normalize_mobile

# --------------------------------------------------------------------- chart
CASH_PARENT = "1100"
AR = "1200"
DEPOSITS = "2100"
EQUITY = "3100"
OPENING = "3200"  # opening balances brought over from the previous software
REVENUE_PARENT = "4100"
FORFEITED = "4800"
EXPENSE_PARENT = "5100"
COMMISSION = "5200"

DEFAULT_CHART = [
    ("1000", "دارایی‌ها", "asset", None),
    (CASH_PARENT, "موجودی نقد، بانک و کارتخوان", "asset", "1000"),
    (AR, "حساب‌های دریافتنی مشتریان", "asset", "1000"),
    ("2000", "بدهی‌ها", "liability", None),
    (DEPOSITS, "بیعانه‌های دریافتی مشتریان", "liability", "2000"),
    ("2200", "حقوق و پورسانت پرداختنی", "liability", "2000"),
    ("3000", "حقوق صاحبان سرمایه", "equity", None),
    (EQUITY, "سرمایه", "equity", "3000"),
    (OPENING, "مانده افتتاحیه (انتقال از نرم‌افزار قبلی)", "equity", "3000"),
    ("4000", "درآمدها", "revenue", None),
    (REVENUE_PARENT, "درآمد خدمات", "revenue", "4000"),
    (FORFEITED, "درآمد بیعانه‌های سوخت‌شده", "revenue", "4000"),
    ("5000", "هزینه‌ها", "expense", None),
    (EXPENSE_PARENT, "هزینه‌های عملیاتی", "expense", "5000"),
    (COMMISSION, "پورسانت پرسنل", "expense", "5000"),
    ("5300", "کسری و اضافی موجودی صندوق/حساب", "expense", "5000"),
]
CASH_DIFF = "5300"


class AccountingError(ValueError):
    pass


def ensure_chart(db: Session) -> None:
    existing = set(db.scalars(select(LedgerAccount.code)))
    for code, name, typ, parent in DEFAULT_CHART:
        if code not in existing:
            db.add(LedgerAccount(code=code, name=name, type=typ, parent_code=parent, is_system=True))
    db.flush()


def account(db: Session, code: str) -> LedgerAccount:
    acc = db.scalar(select(LedgerAccount).where(LedgerAccount.code == code))
    if acc is None:
        ensure_chart(db)
        acc = db.scalar(select(LedgerAccount).where(LedgerAccount.code == code))
    if acc is None:
        raise AccountingError(f"ledger account {code} not found")
    return acc


def _child(db: Session, parent: str, name: str, typ: str) -> LedgerAccount:
    """Find or create a sub-account under parent by name (e.g. revenue per line)."""
    acc = db.scalar(select(LedgerAccount).where(LedgerAccount.parent_code == parent, LedgerAccount.name == name))
    if acc:
        return acc
    code = _next_child_code(db, parent)
    acc = LedgerAccount(code=code, name=name, type=typ, parent_code=parent)
    db.add(acc)
    db.flush()
    return acc


def _next_child_code(db: Session, parent: str) -> str:
    """Next free code under parent (1100 -> 1101, 1102 ...). Never collides with another account's code:
    after 99 children of 1100 the plain sequence would reach 1200 (receivables), so it continues as 1100-100."""
    taken = set(db.scalars(select(LedgerAccount.code)))
    base = int(parent)
    for n in range(1, 100):
        if str(base + n) not in taken:
            return str(base + n)
    n = 100
    while f"{parent}-{n}" in taken:
        n += 1
    return f"{parent}-{n}"


def cash_account_for(db: Session, pa: PaymentAccount) -> LedgerAccount:
    if pa.ledger_account_id:
        return db.get(LedgerAccount, pa.ledger_account_id)
    acc = _child(db, CASH_PARENT, pa.name, "asset")
    pa.ledger_account_id = acc.id
    return acc


def revenue_account_for_line(db: Session, line_name: str) -> LedgerAccount:
    return _child(db, REVENUE_PARENT, f"درآمد {line_name}", "revenue")


def expense_account(db: Session, category: str) -> LedgerAccount:
    return _child(db, EXPENSE_PARENT, category, "expense")


STAFF_PAYABLE = "2200"


@dataclass
class Leg:
    account: LedgerAccount
    debit: int = 0
    credit: int = 0
    customer_id: int | None = None
    line_id: int | None = None
    staff_id: int | None = None


def post(db: Session, description: str, legs: list[Leg], ref_type: str = "", ref_id: int | None = None,
         at: datetime | None = None) -> JournalEntry:
    legs = [l for l in legs if l.debit or l.credit]
    dr = sum(l.debit for l in legs)
    cr = sum(l.credit for l in legs)
    if dr != cr:
        raise AccountingError(f"unbalanced entry: debit={dr} credit={cr}")
    if any(l.debit < 0 or l.credit < 0 for l in legs):
        raise AccountingError("negative amounts are not allowed in journal lines")
    entry = JournalEntry(description=description, ref_type=ref_type, ref_id=ref_id, at=at or local_now())
    for l in legs:
        entry.lines.append(JournalLine(account_id=l.account.id, debit=l.debit, credit=l.credit,
                                       customer_id=l.customer_id, line_id=l.line_id, staff_id=l.staff_id))
    db.add(entry)
    db.flush()
    return entry


# --------------------------------------------------------------------- staff
def staff_for_line(db: Session, line_id: int | None) -> list[Staff]:
    if not line_id:
        return []
    return list(db.scalars(select(Staff).where(Staff.line_id == line_id, Staff.is_active.is_(True)).order_by(Staff.id)))


def default_staff_id(db: Session, service_id: int | None, staff_id: int | None = None) -> int | None:
    """The staff member for a service: the one chosen, else the only active staff of the service's line."""
    if staff_id or not service_id:
        return staff_id
    svc = db.get(Service, service_id)
    people = staff_for_line(db, svc.line_id) if svc else []
    return people[0].id if len(people) == 1 else None


def pay_staff(db: Session, staff: Staff, amount: int, payment_account: PaymentAccount, paid_at: datetime | None = None,
              description: str = "", user=None) -> JournalEntry:
    """Settle (part of) a staff member's earned commission."""
    if amount <= 0:
        raise AccountingError("مبلغ پرداخت باید مثبت باشد")
    ensure_not_future(paid_at, "تاریخ پرداخت")
    entry = post(db, f"پرداخت سهم {staff.full_name}" + (f" - {description}" if description else ""), [
        Leg(account(db, STAFF_PAYABLE), debit=amount, staff_id=staff.id, line_id=staff.line_id),
        Leg(cash_account_for(db, payment_account), credit=amount, staff_id=staff.id),
    ], "staff_payout", staff.id, at=paid_at)
    audit(db, "staff.payout", "staff", staff.id, {"amount": amount, "account": payment_account.id}, user=user)
    return entry


def staff_balance(db: Session, staff_id: int) -> int:
    """Commission earned but not yet paid to the staff member."""
    acc = account(db, STAFF_PAYABLE)
    dr, cr = db.execute(select(func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
                        .where(JournalLine.account_id == acc.id, JournalLine.staff_id == staff_id)).one()
    return int(cr) - int(dr)


# --------------------------------------------------------------------- customers
def find_customer(db: Session, mobile: str | None = None, instagram: str | None = None) -> Customer | None:
    m = normalize_mobile(mobile)
    if m:
        c = db.scalar(select(Customer).where(Customer.mobile == m))
        if c:
            return c
        from .customer_merge import find_by_other_mobile

        c = find_by_other_mobile(db, m)  # a number of a merged duplicate record
        if c:
            return c
    if instagram:
        handle = instagram.lstrip("@").lower()
        return db.scalar(select(Customer).where(func.lower(Customer.instagram) == handle))
    return None


def next_customer_code(db: Session) -> str:
    """Next free numeric customer code (after the highest one, so it never collides with imported codes)."""
    nums = [int(c) for c in db.scalars(select(Customer.legacy_code).where(Customer.legacy_code.is_not(None))) if c.isdigit()]
    return str(max(nums, default=0) + 1)


def delete_customers(db: Session, customers: list[Customer]) -> int:
    """Delete customers that have no financial history; loose links (chat messages, inbound receipts) are detached."""
    from sqlalchemy import update

    from ..models import ConversationMessage, ConversationState, InboundReceipt

    ids = [c.id for c in customers]
    if not ids:
        return 0
    for m in (InboundReceipt, ConversationMessage):
        db.execute(update(m).where(m.customer_id.in_(ids)).values(customer_id=None))
    if hasattr(ConversationState, "customer_id"):
        db.execute(update(ConversationState).where(ConversationState.customer_id.in_(ids)).values(customer_id=None))
    for c in customers:
        db.delete(c)
    db.flush()
    return len(ids)


def assign_missing_codes(db: Session) -> int:
    """Give a code to every customer that has none (customers created before codes existed)."""
    missing = list(db.scalars(select(Customer).where(Customer.legacy_code.is_(None)).order_by(Customer.id)))
    if missing:
        n = int(next_customer_code(db))
        for i, c in enumerate(missing):
            c.legacy_code = str(n + i)
        db.flush()
    return len(missing)


def find_or_create_customer(db: Session, full_name: str | None, mobile: str | None = None,
                            instagram: str | None = None, source: str = "manual", user=None) -> tuple[Customer, bool]:
    existing = find_customer(db, mobile, instagram)
    if existing:
        if full_name and (not existing.full_name or existing.full_name.startswith("مشتری ")):
            existing.full_name = full_name
        if instagram and not existing.instagram:
            existing.instagram = instagram.lstrip("@").lower()
        return existing, False
    m = normalize_mobile(mobile)
    c = Customer(full_name=full_name or f"مشتری {m or instagram or ''}".strip(), mobile=m, legacy_code=next_customer_code(db),
                 instagram=instagram.lstrip("@").lower() if instagram else None, source=source)
    db.add(c)
    db.flush()
    audit(db, "customer.create", "customer", c.id, {"name": c.full_name, "mobile": m}, user=user)
    bus.emit("customer.created", {"id": c.id, "name": c.full_name, "mobile": m, "source": source}, db=db)
    return c, True



FUTURE_GRACE = timedelta(minutes=10)  # tolerate small clock differences between devices


def ensure_not_future(at: datetime | None, label: str = "تاریخ دریافت") -> None:
    """Money that has been received/paid can only be dated now or in the past."""
    if at is not None and at.replace(tzinfo=None) > local_now() + FUTURE_GRACE:
        raise AccountingError(f"{label} نمی‌تواند در آینده باشد؛ تاریخ امروز یا قبل از آن را انتخاب کنید")


# --------------------------------------------------------------------- deposits
def record_deposit(db: Session, *, customer: Customer, amount: int, payment_account: PaymentAccount,
                   received_at: datetime | None = None, reference: str | None = None, service_id: int | None = None,
                   staff_id: int | None = None,
                   appointment_id: int | None = None, source: str = "manual", notes: str = "",
                   service_guess: dict | None = None, user=None) -> Deposit:
    if amount <= 0:
        raise AccountingError("مبلغ بیعانه باید مثبت باشد")
    ensure_not_future(received_at, "تاریخ دریافت بیعانه")
    svc = db.get(Service, service_id) if service_id else None
    dep = Deposit(customer_id=customer.id, amount=amount, payment_account_id=payment_account.id,
                  received_at=received_at or local_now(), reference=reference, service_id=service_id,
                  appointment_id=appointment_id, source=source, notes=notes, service_guess=service_guess or {},
                  staff_id=default_staff_id(db, service_id, staff_id))
    db.add(dep)
    db.flush()
    post(db, f"دریافت بیعانه از {customer.full_name}", [
        Leg(cash_account_for(db, payment_account), debit=amount, customer_id=customer.id),
        Leg(account(db, DEPOSITS), credit=amount, customer_id=customer.id, line_id=svc.line_id if svc else None, staff_id=dep.staff_id),
    ], "deposit", dep.id, at=dep.received_at)
    audit(db, "deposit.create", "deposit", dep.id, {"amount": amount, "customer": customer.id, "source": source}, user=user)
    bus.emit("deposit.created", {"id": dep.id, "customer_id": customer.id, "amount": amount, "source": source}, db=db)
    return dep


def close_deposit(db: Session, dep: Deposit, action: str, refund_account: PaymentAccount | None = None, user=None,
                  at: datetime | None = None) -> Deposit:
    """action: refund (money returned) or forfeit (customer no-show, deposit becomes income). at: when (default now)."""
    if dep.status != "held":
        raise AccountingError("این بیعانه قبلاً تسویه شده است")
    if action == "refund":
        pa = refund_account or db.get(PaymentAccount, dep.payment_account_id)
        post(db, "استرداد بیعانه", [
            Leg(account(db, DEPOSITS), debit=dep.amount, customer_id=dep.customer_id),
            Leg(cash_account_for(db, pa), credit=dep.amount, customer_id=dep.customer_id),
        ], "deposit", dep.id, at=at)
        dep.status = "refunded"
        dep.refund_account_id = pa.id
    elif action == "forfeit":
        post(db, "سوخت بیعانه (عدم مراجعه)", [
            Leg(account(db, DEPOSITS), debit=dep.amount, customer_id=dep.customer_id),
            Leg(account(db, FORFEITED), credit=dep.amount, customer_id=dep.customer_id),
        ], "deposit", dep.id, at=at)
        dep.status = "forfeited"
    else:
        raise AccountingError("unknown action")
    dep.closed_at = at or local_now()
    audit(db, f"deposit.{action}", "deposit", dep.id, {"amount": dep.amount}, user=user)
    return dep


def backfill_deposit_closures(db: Session) -> int:
    """Deposits refunded / forfeited before `closed_at` existed: take the date and refund account from their journal
    entry, so the dashboard counts that money as going out on the right day and from the right account."""
    by_ledger = {pa.ledger_account_id: pa.id for pa in db.scalars(select(PaymentAccount)) if pa.ledger_account_id}
    n = 0
    for dep in db.scalars(select(Deposit).where(Deposit.status.in_(("refunded", "forfeited")), Deposit.closed_at.is_(None))):
        word = "استرداد" if dep.status == "refunded" else "سوخت"
        entry = db.scalar(select(JournalEntry).where(JournalEntry.ref_type == "deposit", JournalEntry.ref_id == dep.id,
                                                     JournalEntry.description.like(f"{word}%")).order_by(JournalEntry.id.desc()))
        dep.closed_at = entry.at if entry else dep.updated_at or dep.received_at
        if dep.status == "refunded" and dep.refund_account_id is None:
            credit = next((ln for ln in (entry.lines if entry else []) if ln.credit and ln.account_id in by_ledger), None)
            dep.refund_account_id = by_ledger[credit.account_id] if credit else dep.payment_account_id
        n += 1
    db.flush()
    return n


def follow_appointment(db: Session, dep: Deposit, action: str, mode: str = "auto", user=None) -> Appointment | None:
    """After a deposit is refunded or forfeited, its booked appointment usually goes too: a refund means the customer
    cancelled, a forfeit means they didn't come (or cancelled too late). mode: auto | cancel | keep.
    auto keeps the appointment only while another of its deposits is still open."""
    if mode == "keep" or not dep.appointment_id:
        return None
    a = db.get(Appointment, dep.appointment_id)
    if a is None or a.status != "booked":
        return None
    others = db.scalar(select(func.count(Deposit.id)).where(Deposit.appointment_id == a.id, Deposit.id != dep.id,
                                                             Deposit.status == "held")) or 0
    if mode == "auto" and others:
        return None
    a.status = "no_show" if action == "forfeit" and a.start_at <= local_now() else "cancelled"
    audit(db, "appointment.status", "appointment", a.id, {"status": a.status, "because": f"deposit.{action}", "deposit": dep.id}, user=user)
    return a


# --------------------------------------------------------------------- invoices
def next_invoice_number(db: Session) -> str:
    n = (db.scalar(select(func.max(Invoice.id))) or 0) + 1
    return f"INV-{n:06d}"


def issue_invoice(db: Session, *, customer: Customer, items: list[dict], discount: int = 0,
                  apply_deposit_ids: list[int] | None = None, apply_all_deposits: bool = False,
                  payments: list[dict] | None = None, issued_at: datetime | None = None,
                  source: str = "manual", notes: str = "", channel: str | None = None, user=None) -> Invoice:
    """items: [{service_id? | product_id?, description?, quantity, unit_price, discount?, staff_id?}]
    payments: [{payment_account_id, amount, reference?}]. Products leave the stock and their cost (COGS) is posted."""
    from ..models import Product
    from . import inventory, settings_store, woo_queue

    if not items:
        raise AccountingError("فاکتور بدون آیتم قابل ثبت نیست")
    ensure_not_future(issued_at, "تاریخ فاکتور")
    channel = channel if channel in ("online", "in_person") else None
    inv = Invoice(number=next_invoice_number(db), customer_id=customer.id, issued_at=issued_at or local_now(),
                  discount=discount, source=source, notes=notes, channel=channel)
    revenue_by_line: dict[int | str | None, int] = {}  # service line id, or "products"
    line_names: dict[int | str | None, str] = {}
    for it in items:
        prod = db.get(Product, it["product_id"]) if it.get("product_id") else None
        if it.get("product_id") and prod is None:
            raise AccountingError("محصول پیدا نشد")
        svc = db.get(Service, it["service_id"]) if it.get("service_id") and not prod else None
        qty = int(it.get("quantity") or 1)
        if qty <= 0:
            raise AccountingError("تعداد باید مثبت باشد")
        if prod:
            price = int(it["unit_price"] if it.get("unit_price") is not None else inventory.suggested_price(prod, channel))
            # products carry no staff commission; their line in the books is "product sales"
            item = InvoiceItem(product_id=prod.id, line_id=None, staff_id=None, description=it.get("description") or prod.name,
                               quantity=qty, unit_price=price, discount=int(it.get("discount") or 0))
        else:
            price = int(it["unit_price"] if it.get("unit_price") is not None else (svc.base_price if svc else 0))
            item = InvoiceItem(service_id=svc.id if svc else None, line_id=svc.line_id if svc else it.get("line_id"),
                               staff_id=default_staff_id(db, svc.id if svc else None, it.get("staff_id")),
                               description=it.get("description") or (svc.name if svc else ""),
                               quantity=qty, unit_price=price, discount=int(it.get("discount") or 0))
        if item.amount < 0:
            raise AccountingError("مبلغ آیتم منفی است")
        inv.items.append(item)
        key = "products" if prod else item.line_id
        revenue_by_line[key] = revenue_by_line.get(key, 0) + item.amount
        if svc:
            line_names[item.line_id] = svc.line.name
    wanted: dict[int, int] = {}
    for it in inv.items:
        if it.product_id:
            wanted[it.product_id] = wanted.get(it.product_id, 0) + it.quantity
    # (a website order is already sold: it is booked even when the stock here is short)
    if wanted and source != "woocommerce" and not settings_store.get(db, "products.allow_negative", True):
        for pid, q in wanted.items():
            p = db.get(Product, pid)
            if p.stock_qty < q:
                raise AccountingError(f"موجودی «{p.name}» کافی نیست (موجودی: {p.stock_qty}، فروش: {q})")
    inv.subtotal = sum(i.amount for i in inv.items)
    inv.total = inv.subtotal - discount
    if inv.total < 0:
        raise AccountingError("تخفیف بیشتر از مبلغ فاکتور است")
    db.add(inv)
    db.flush()

    # allocate invoice-level discount proportionally across lines, revenue recorded net
    legs = [Leg(account(db, AR), debit=inv.total, customer_id=customer.id)]
    remaining_discount = discount
    keys = list(revenue_by_line)
    for idx, line_id in enumerate(keys):
        gross = revenue_by_line[line_id]
        share = remaining_discount if idx == len(keys) - 1 else (discount * gross // inv.subtotal if inv.subtotal else 0)
        remaining_discount -= share
        if line_id == "products":
            legs.append(Leg(inventory._acc(db, inventory.PRODUCT_REVENUE), credit=gross - share, customer_id=customer.id))
            continue
        name = line_names.get(line_id) or "سایر خدمات"
        legs.append(Leg(revenue_account_for_line(db, name), credit=gross - share, customer_id=customer.id, line_id=line_id))
    post(db, f"فاکتور {inv.number} - {customer.full_name}", legs, "invoice", inv.id, at=inv.issued_at)

    # staff commission: each item's net (after its share of the invoice discount) x the staff member's percent.
    # Booked as an expense of the salon and a liability to the staff member until it is paid out.
    remaining_discount = discount
    comm_legs: list[Leg] = []
    for idx, item in enumerate(inv.items):
        share = remaining_discount if idx == len(inv.items) - 1 else (discount * item.amount // inv.subtotal if inv.subtotal else 0)
        remaining_discount -= share
        item.net_amount = item.amount - share
        person = db.get(Staff, item.staff_id) if item.staff_id else None
        if person and person.commission_percent:
            item.commission_percent = float(person.commission_percent)
            item.commission_amount = int(round(item.net_amount * person.commission_percent / 100))
            if item.commission_amount > 0:
                comm_legs += [Leg(account(db, COMMISSION), debit=item.commission_amount, line_id=item.line_id, staff_id=person.id),
                              Leg(account(db, STAFF_PAYABLE), credit=item.commission_amount, line_id=item.line_id, staff_id=person.id)]
        else:
            item.commission_percent, item.commission_amount = 0.0, 0
    if comm_legs:
        post(db, f"سهم پرسنل - فاکتور {inv.number}", comm_legs, "commission", inv.id, at=inv.issued_at)

    # products leave the stock; the costing engine works out their cost and posts it (COGS / inventory)
    sold = {inventory.sell(db, item, inv.issued_at).product_id for item in inv.items if item.product_id}
    for pid in sold:
        inventory.recost(db, pid)
    for item in inv.items:
        if item.product_id:
            inventory.remember_price(db.get(Product, item.product_id), item.unit_price, inv.issued_at, channel)
            if source != "woocommerce":  # a website order is already out of the website's stock
                woo_queue.add(db, item.product_id, -item.quantity, f"فروش حضوری {inv.number}")

    # apply customer deposits
    deposit_q = select(Deposit).where(Deposit.customer_id == customer.id, Deposit.status == "held")
    if apply_deposit_ids:
        deposit_q = deposit_q.where(Deposit.id.in_(apply_deposit_ids))
    if apply_deposit_ids or apply_all_deposits:
        for dep in db.scalars(deposit_q.order_by(Deposit.received_at)):
            due = inv.total - inv.paid
            if due <= 0:
                break
            if dep.amount > due:
                # split: the remainder stays as a held deposit
                remainder = Deposit(customer_id=dep.customer_id, amount=dep.amount - due, payment_account_id=dep.payment_account_id,
                                    received_at=dep.received_at, reference=dep.reference, source=dep.source,
                                    notes=f"باقیمانده بیعانه #{dep.id}")
                dep.amount = due
                db.add(remainder)
            post(db, f"تسویه بیعانه با فاکتور {inv.number}", [
                Leg(account(db, DEPOSITS), debit=dep.amount, customer_id=customer.id),
                Leg(account(db, AR), credit=dep.amount, customer_id=customer.id),
            ], "deposit_apply", dep.id, at=inv.issued_at)
            dep.status = "applied"
            dep.applied_invoice_id = inv.id
            inv.paid += dep.amount
            bus.emit("deposit.applied", {"id": dep.id, "invoice_id": inv.id, "amount": dep.amount}, db=db)

    for p in payments or []:
        if int(p.get("amount") or 0) > 0:
            record_payment(db, invoice=inv, payment_account=db.get(PaymentAccount, p["payment_account_id"]),
                           amount=int(p["amount"]), reference=p.get("reference"), paid_at=inv.issued_at,
                           source=source, user=user, _update_only=True)
    _refresh_status(inv)
    audit(db, "invoice.issue", "invoice", inv.id, {"number": inv.number, "total": inv.total}, user=user)
    bus.emit("invoice.issued", {"id": inv.id, "customer_id": customer.id, "total": inv.total}, db=db)
    return inv


def _refresh_status(inv: Invoice) -> None:
    if inv.status == "void":
        return
    inv.status = "paid" if inv.paid >= inv.total else ("partial" if inv.paid > 0 else "issued")


def record_payment(db: Session, *, payment_account: PaymentAccount, amount: int, invoice: Invoice | None = None,
                   customer: Customer | None = None, reference: str | None = None, paid_at: datetime | None = None,
                   source: str = "manual", user=None, _update_only: bool = False) -> Payment:
    if payment_account is None:
        raise AccountingError("حساب دریافت مشخص نیست")
    if amount <= 0:
        raise AccountingError("مبلغ پرداخت باید مثبت باشد")
    ensure_not_future(paid_at, "تاریخ دریافت")
    customer_id = invoice.customer_id if invoice else (customer.id if customer else None)
    if invoice is not None:
        if invoice.status == "void":
            raise AccountingError("فاکتور باطل شده است")
        if invoice.paid + amount > invoice.total:
            raise AccountingError("مبلغ پرداخت از مانده فاکتور بیشتر است؛ مازاد را به‌عنوان بیعانه ثبت کنید")
    pay = Payment(invoice_id=invoice.id if invoice else None, customer_id=customer_id,
                  payment_account_id=payment_account.id, amount=amount, reference=reference,
                  paid_at=paid_at or local_now(), source=source)
    db.add(pay)
    db.flush()
    post(db, f"دریافت وجه {('فاکتور ' + invoice.number) if invoice else ''}".strip(), [
        Leg(cash_account_for(db, payment_account), debit=amount, customer_id=customer_id),
        Leg(account(db, AR), credit=amount, customer_id=customer_id),
    ], "payment", pay.id, at=pay.paid_at)
    if invoice is not None:
        invoice.paid += amount
        _refresh_status(invoice)
    if not _update_only:
        audit(db, "payment.create", "payment", pay.id, {"amount": amount, "invoice": invoice.id if invoice else None}, user=user)
    bus.emit("payment.created", {"id": pay.id, "amount": amount, "customer_id": customer_id}, db=db)
    return pay


def void_invoice(db: Session, inv: Invoice, reason: str = "", user=None, payments: str = "cancel") -> Invoice:
    """Cancel an invoice completely and correctly, also when it was (partly) paid:

    * the invoice and staff-commission entries are reversed;
    * deposits used on it become open (held) again for the customer;
    * money received on it is either cancelled on its own date (payments="cancel": the invoice was a mistake,
      the payment never really happened - that day's receipts go back to what they really were), refunded today
      (payments="refund": money given back to the customer now) or kept for the customer as a new open deposit
      (payments="deposit");
    * appointments it settled are booked again (at their original time when that is still free).
    """
    from ..models import Appointment

    if inv.status == "void":
        return inv
    if payments not in ("cancel", "refund", "deposit"):
        raise AccountingError("نحوهٔ برگشت وجه نامعتبر است")
    for original in db.scalars(select(JournalEntry).where(JournalEntry.ref_type.in_(["invoice", "commission"]),
                                                         JournalEntry.ref_id == inv.id)):
        post(db, f"ابطال {'فاکتور' if original.ref_type == 'invoice' else 'سهم پرسنل'} {inv.number}", [
            Leg(db.get(LedgerAccount, l.account_id), debit=l.credit, credit=l.debit, customer_id=l.customer_id,
                line_id=l.line_id, staff_id=l.staff_id)
            for l in original.lines
        ], "invoice_void", inv.id)
    # deposits that paid part of it: back to open
    for dep in db.scalars(select(Deposit).where(Deposit.applied_invoice_id == inv.id, Deposit.status == "applied")):
        post(db, f"برگشت بیعانه از فاکتور باطل‌شده {inv.number}", [
            Leg(account(db, AR), debit=dep.amount, customer_id=inv.customer_id),
            Leg(account(db, DEPOSITS), credit=dep.amount, customer_id=inv.customer_id),
        ], "deposit", dep.id)
        dep.status = "held"
        dep.applied_invoice_id = None
    # money received on it
    for pay in list(db.scalars(select(Payment).where(Payment.invoice_id == inv.id, Payment.amount > 0))):
        pa = db.get(PaymentAccount, pay.payment_account_id)
        if payments in ("refund", "cancel"):
            mistake = payments == "cancel"
            when = pay.paid_at if mistake else local_now()
            back = Payment(invoice_id=inv.id, customer_id=pay.customer_id, payment_account_id=pay.payment_account_id,
                           amount=-pay.amount, reference=pay.reference, paid_at=when, source="void_cancel" if mistake else "refund")
            db.add(back)
            db.flush()
            post(db, f"{'لغو دریافت' if mistake else 'استرداد وجه'} فاکتور باطل‌شده {inv.number}", [
                Leg(account(db, AR), debit=pay.amount, customer_id=pay.customer_id),
                Leg(cash_account_for(db, pa), credit=pay.amount, customer_id=pay.customer_id),
            ], "payment", back.id, at=when)
        else:
            credit = Deposit(customer_id=inv.customer_id, amount=pay.amount, payment_account_id=pay.payment_account_id,
                             received_at=pay.paid_at, reference=pay.reference, source="void_credit",
                             notes=f"وجه فاکتور باطل‌شده {inv.number}")
            db.add(credit)
            db.flush()
            post(db, f"انتقال وجه فاکتور باطل‌شده {inv.number} به بیعانه", [
                Leg(account(db, AR), debit=pay.amount, customer_id=inv.customer_id),
                Leg(account(db, DEPOSITS), credit=pay.amount, customer_id=inv.customer_id),
            ], "deposit", credit.id)
    inv.paid = 0
    # products come back to stock at the cost they were sold at
    from . import inventory

    # website orders are cancelled on the website, which puts the goods back in its own stock
    inventory.return_invoice(db, inv.id, same_time=payments == "cancel", notify_site=inv.source != "woocommerce")
    # appointments it settled are open again
    from .scheduling import validate_slot

    for a in db.scalars(select(Appointment).where(Appointment.invoice_id == inv.id)):
        a.status = "booked"
        a.invoice_id = None
        if a.original_start_at:
            if not validate_slot(db, a.service_id, a.staff_id, a.original_start_at, duration_minutes=a.duration_minutes,
                                 exclude_id=a.id, customer_id=a.customer_id, allow_outside_hours=True):
                a.start_at = a.original_start_at
            a.original_start_at = None
    inv.status = "void"
    audit(db, "invoice.void", "invoice", inv.id, {"reason": reason, "payments": payments}, user=user)
    return inv


# --------------------------------------------------------------------- expenses
def record_expense(db: Session, *, category: str, amount: int, payment_account: PaymentAccount,
                   spent_at: datetime | None = None, description: str = "", staff_id: int | None = None, user=None) -> Expense:
    if amount <= 0:
        raise AccountingError("مبلغ هزینه باید مثبت باشد")
    ensure_not_future(spent_at, "تاریخ پرداخت هزینه")
    exp = Expense(category=category, amount=amount, payment_account_id=payment_account.id,
                  spent_at=spent_at or local_now(), description=description, staff_id=staff_id)
    db.add(exp)
    db.flush()
    post(db, f"هزینه: {category}", [
        Leg(expense_account(db, category), debit=amount),
        Leg(cash_account_for(db, payment_account), credit=amount),
    ], "expense", exp.id, at=exp.spent_at)
    audit(db, "expense.create", "expense", exp.id, {"amount": amount, "category": category}, user=user)
    bus.emit("expense.created", {"id": exp.id, "amount": amount, "category": category}, db=db)
    return exp


# --------------------------------------------------------------------- balances
DEBIT_NATURE = ("asset", "expense")


def natural_balance(typ: str, debit: int, credit: int) -> int:
    """Balance on the account's normal side: assets/expenses grow with debits, the rest with credits."""
    return debit - credit if typ in DEBIT_NATURE else credit - debit


def trial_balance(db: Session) -> list[dict]:
    """Two-column trial balance: turnover (sum of debits / credits) and the closing balance split into
    a debit-balance or credit-balance column (only one of them is non-zero per account)."""
    rows = db.execute(
        select(LedgerAccount.id, LedgerAccount.code, LedgerAccount.name, LedgerAccount.type, LedgerAccount.parent_code,
               func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
        .join(JournalLine, JournalLine.account_id == LedgerAccount.id, isouter=True)
        .group_by(LedgerAccount.id).order_by(LedgerAccount.code)
    ).all()
    out = []
    for aid, code, name, typ, parent, dr, cr in rows:
        dr, cr = int(dr), int(cr)
        if dr or cr:
            net = dr - cr
            out.append({"id": aid, "code": code, "name": name, "type": typ, "parent": parent, "debit": dr, "credit": cr,
                        "balance": natural_balance(typ, dr, cr),
                        "debit_balance": net if net > 0 else 0, "credit_balance": -net if net < 0 else 0})
    return out


def ledger_summary(rows: list[dict]) -> dict:
    """Totals for the ledger page.

    turnover_*: every posting adds the same amount to both sides, so these always grow (a 10m expense adds
    10m to both). balance_*: closing balances - an expense paid in cash moves 10m from cash to expense, so
    these stay the same; they only grow when the business really gets bigger (e.g. a new sale)."""
    by_type = {t: sum(r["balance"] for r in rows if r["type"] == t) for t in ("asset", "liability", "equity", "revenue", "expense")}
    return {
        "turnover_debit": sum(r["debit"] for r in rows),
        "turnover_credit": sum(r["credit"] for r in rows),
        "balance_debit": sum(r["debit_balance"] for r in rows),
        "balance_credit": sum(r["credit_balance"] for r in rows),
        "by_type": by_type,
        "cash": sum(r["balance"] for r in rows if r["parent"] == CASH_PARENT),
        "net_profit": by_type["revenue"] - by_type["expense"],
    }


def customer_balance(db: Session, customer_id: int) -> dict:
    """receivable: what the customer owes. deposits: prepaid credit held for them."""
    def _sum(code: str) -> int:
        acc = account(db, code)
        dr, cr = db.execute(
            select(func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
            .where(JournalLine.account_id == acc.id, JournalLine.customer_id == customer_id)
        ).one()
        return int(dr) - int(cr)
    receivable = _sum(AR)
    deposits = -_sum(DEPOSITS)
    return {"receivable": receivable, "deposits_held": deposits, "net": deposits - receivable}


# --------------------------------------------------------------------- opening balances
def opening_balance(db: Session, pa: PaymentAccount) -> tuple[int, datetime | None]:
    """Opening balance entered for a cash box / card / POS / bank account and its date."""
    acc = cash_account_for(db, pa)
    rows = db.execute(select(JournalEntry.at, func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
                      .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
                      .where(JournalEntry.ref_type == "opening_balance", JournalEntry.ref_id == pa.id, JournalLine.account_id == acc.id)
                      .group_by(JournalEntry.id)).all()
    return sum(int(v) for _, v in rows), (min(at for at, _ in rows) if rows else None)


def set_opening_balance(db: Session, pa: PaymentAccount, amount: int, at: datetime | None = None, user=None) -> int:
    """Set (or correct) the money that was already in an account when the system started.
    Booked against the owner's opening equity; replaces a previous opening entry of this account."""
    at = at or local_now()
    ensure_not_future(at, "تاریخ موجودی اولیه")
    for e in db.scalars(select(JournalEntry).where(JournalEntry.ref_type == "opening_balance", JournalEntry.ref_id == pa.id)):
        db.delete(e)
    db.flush()
    if amount:
        cash = cash_account_for(db, pa)
        opening = account(db, OPENING)
        post(db, f"موجودی اولیه {pa.name}", [
            Leg(cash, debit=amount) if amount > 0 else Leg(cash, credit=-amount),
            Leg(opening, credit=amount) if amount > 0 else Leg(opening, debit=-amount),
        ], "opening_balance", pa.id, at=at)
    audit(db, "account.opening_balance", "payment_account", pa.id, {"amount": amount, "at": at.isoformat()}, user=user)
    return amount


def adjust_balance(db: Session, pa: PaymentAccount, actual: int, note: str = "", user=None) -> int:
    """Cash count / bank statement check: book the difference between the real balance and the books
    (shortage = expense, overage = reduces that expense account). Returns the difference posted."""
    cash = cash_account_for(db, pa)
    dr, cr = db.execute(select(func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
                        .where(JournalLine.account_id == cash.id)).one()
    diff = int(actual) - (int(dr) - int(cr))
    if diff:
        other = account(db, CASH_DIFF)
        post(db, f"{'اضافی' if diff > 0 else 'کسری'} موجودی {pa.name}" + (f" - {note}" if note else ""), [
            Leg(cash, debit=diff) if diff > 0 else Leg(cash, credit=-diff),
            Leg(other, credit=diff) if diff > 0 else Leg(other, debit=-diff),
        ], "balance_adjust", pa.id)
    audit(db, "account.adjust", "payment_account", pa.id, {"actual": actual, "difference": diff}, user=user)
    return diff


def account_balances(db: Session) -> list[dict]:
    out = []
    for pa in db.scalars(select(PaymentAccount).order_by(PaymentAccount.id)):
        acc = cash_account_for(db, pa)
        dr, cr, n = db.execute(
            select(func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0),
                   func.count(JournalLine.id))
            .where(JournalLine.account_id == acc.id)
        ).one()
        out.append({"id": pa.id, "name": pa.name, "kind": pa.kind, "bank_name": pa.bank_name, "is_active": pa.is_active,
                    "ledger_account_id": acc.id, "code": acc.code, "total_in": int(dr), "total_out": int(cr),
                    "count": int(n), "balance": int(dr) - int(cr), "opening": opening_balance(db, pa)[0]})
    return out


REF_LABELS = {
    "opening_balance": "موجودی اولیه", "balance_adjust": "اصلاح موجودی (شمارش)",
    "deposit": "بیعانه", "deposit_apply": "تسویه بیعانه", "invoice": "فاکتور فروش", "invoice_void": "ابطال فاکتور",
    "payment": "دریافت وجه", "expense": "هزینه", "commission": "سهم پرسنل", "staff_payout": "پرداخت به پرسنل",
    "purchase": "خرید محصول", "purchase_void": "ابطال خرید", "supplier_payment": "پرداخت به تأمین‌کننده",
    "stock_move": "گردش انبار (بهای تمام‌شده)", "stock_opening": "موجودی اول دورهٔ کالا",
}


def account_statement(db: Session, acc: LedgerAccount, start: datetime | None = None, end: datetime | None = None) -> dict:
    """Every posting to one ledger account, oldest first, with the running balance after each one.
    For a cash box / bank / POS account debit = money in, credit = money out."""
    def _sums(*conds) -> tuple[int, int]:  # noqa: ANN002
        dr, cr = db.execute(select(func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
                            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                            .where(JournalLine.account_id == acc.id, *conds)).one()
        return int(dr), int(cr)

    opening = natural_balance(acc.type, *_sums(JournalEntry.at < start)) if start else 0
    q = (select(JournalLine, JournalEntry).join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
         .where(JournalLine.account_id == acc.id).order_by(JournalEntry.at, JournalEntry.id, JournalLine.id))
    if start:
        q = q.where(JournalEntry.at >= start)
    if end:
        q = q.where(JournalEntry.at < end)
    pairs = db.execute(q).all()

    entry_ids = {e.id for _, e in pairs}
    names = {a.id: (a.code, a.name) for a in db.scalars(select(LedgerAccount))}
    others: dict[int, list[str]] = {}
    if entry_ids:
        for l in db.scalars(select(JournalLine).where(JournalLine.entry_id.in_(entry_ids), JournalLine.account_id != acc.id)):
            nm = names.get(l.account_id, ("", "?"))[1]
            if nm not in others.setdefault(l.entry_id, []):
                others[l.entry_id].append(nm)
    cust_ids = {l.customer_id for l, _ in pairs if l.customer_id}
    staff_ids = {l.staff_id for l, _ in pairs if l.staff_id}
    customers = {c.id: c.full_name for c in db.scalars(select(Customer).where(Customer.id.in_(cust_ids)))} if cust_ids else {}
    staff = {s.id: s.full_name for s in db.scalars(select(Staff).where(Staff.id.in_(staff_ids)))} if staff_ids else {}
    details = _ref_details(db, [e for _, e in pairs])

    rows, running, total_dr, total_cr = [], opening, 0, 0
    for l, e in pairs:
        running += natural_balance(acc.type, l.debit, l.credit)
        total_dr += l.debit
        total_cr += l.credit
        rows.append({"entry_id": e.id, "at": e.at.isoformat(), "description": e.description, "ref_type": e.ref_type,
                     "ref_id": e.ref_id, "ref_label": REF_LABELS.get(e.ref_type, "سند دستی"),
                     "detail": details.get((e.ref_type, e.ref_id), ""), "counterpart": "، ".join(others.get(e.id, [])),
                     "customer": customers.get(l.customer_id), "staff": staff.get(l.staff_id),
                     "debit": l.debit, "credit": l.credit, "balance": running})
    return {"account": {"id": acc.id, "code": acc.code, "name": acc.name, "type": acc.type},
            "opening": opening, "total_debit": total_dr, "total_credit": total_cr, "closing": running,
            "count": len(rows), "rows": rows}


def _ref_details(db: Session, entries: list[JournalEntry]) -> dict[tuple[str, int | None], str]:
    """Short human detail for each source document (expense note, payment/deposit tracking number, invoice no.)."""
    ids: dict[str, set[int]] = {}
    for e in entries:
        if e.ref_id:
            ids.setdefault(e.ref_type, set()).add(e.ref_id)
    out: dict[tuple[str, int | None], str] = {}
    for x in db.scalars(select(Expense).where(Expense.id.in_(ids.get("expense", set())))):
        out[("expense", x.id)] = x.description or ""
    for x in db.scalars(select(Payment).where(Payment.id.in_(ids.get("payment", set())))):
        out[("payment", x.id)] = f"پیگیری {x.reference}" if x.reference else ""
    for x in db.scalars(select(Deposit).where(Deposit.id.in_(ids.get("deposit", set()) | ids.get("deposit_apply", set())))):
        d = f"پیگیری {x.reference}" if x.reference else ""
        out[("deposit", x.id)] = out[("deposit_apply", x.id)] = d
    for x in db.scalars(select(Invoice).where(Invoice.id.in_(ids.get("invoice", set()) | ids.get("invoice_void", set()) | ids.get("commission", set())))):
        for t in ("invoice", "invoice_void", "commission"):
            out[(t, x.id)] = x.number
    return out
