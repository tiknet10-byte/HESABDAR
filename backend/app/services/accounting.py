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
    ("4000", "درآمدها", "revenue", None),
    (REVENUE_PARENT, "درآمد خدمات", "revenue", "4000"),
    (FORFEITED, "درآمد بیعانه‌های سوخت‌شده", "revenue", "4000"),
    ("5000", "هزینه‌ها", "expense", None),
    (EXPENSE_PARENT, "هزینه‌های عملیاتی", "expense", "5000"),
    (COMMISSION, "پورسانت پرسنل", "expense", "5000"),
]


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
    codes = [int(c) for c in db.scalars(select(LedgerAccount.code).where(LedgerAccount.parent_code == parent)) if c.isdigit()]
    code = str(max(codes) + 1 if codes else int(parent) + 1)
    acc = LedgerAccount(code=code, name=name, type=typ, parent_code=parent)
    db.add(acc)
    db.flush()
    return acc


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
    if instagram:
        handle = instagram.lstrip("@").lower()
        return db.scalar(select(Customer).where(func.lower(Customer.instagram) == handle))
    return None


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
    c = Customer(full_name=full_name or f"مشتری {m or instagram or ''}".strip(), mobile=m,
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


def close_deposit(db: Session, dep: Deposit, action: str, refund_account: PaymentAccount | None = None, user=None) -> Deposit:
    """action: refund (money returned) or forfeit (customer no-show, deposit becomes income)."""
    if dep.status != "held":
        raise AccountingError("این بیعانه قبلاً تسویه شده است")
    if action == "refund":
        pa = refund_account or db.get(PaymentAccount, dep.payment_account_id)
        post(db, "استرداد بیعانه", [
            Leg(account(db, DEPOSITS), debit=dep.amount, customer_id=dep.customer_id),
            Leg(cash_account_for(db, pa), credit=dep.amount, customer_id=dep.customer_id),
        ], "deposit", dep.id)
        dep.status = "refunded"
    elif action == "forfeit":
        post(db, "سوخت بیعانه (عدم مراجعه)", [
            Leg(account(db, DEPOSITS), debit=dep.amount, customer_id=dep.customer_id),
            Leg(account(db, FORFEITED), credit=dep.amount, customer_id=dep.customer_id),
        ], "deposit", dep.id)
        dep.status = "forfeited"
    else:
        raise AccountingError("unknown action")
    audit(db, f"deposit.{action}", "deposit", dep.id, {"amount": dep.amount}, user=user)
    return dep


# --------------------------------------------------------------------- invoices
def next_invoice_number(db: Session) -> str:
    n = (db.scalar(select(func.max(Invoice.id))) or 0) + 1
    return f"INV-{n:06d}"


def issue_invoice(db: Session, *, customer: Customer, items: list[dict], discount: int = 0,
                  apply_deposit_ids: list[int] | None = None, apply_all_deposits: bool = False,
                  payments: list[dict] | None = None, issued_at: datetime | None = None,
                  source: str = "manual", notes: str = "", user=None) -> Invoice:
    """items: [{service_id?, description?, quantity, unit_price, discount?, staff_id?}]
    payments: [{payment_account_id, amount, reference?}]"""
    if not items:
        raise AccountingError("فاکتور بدون آیتم قابل ثبت نیست")
    ensure_not_future(issued_at, "تاریخ فاکتور")
    inv = Invoice(number=next_invoice_number(db), customer_id=customer.id, issued_at=issued_at or local_now(),
                  discount=discount, source=source, notes=notes)
    revenue_by_line: dict[int | None, int] = {}
    line_names: dict[int | None, str] = {}
    for it in items:
        svc = db.get(Service, it["service_id"]) if it.get("service_id") else None
        qty = int(it.get("quantity") or 1)
        price = int(it["unit_price"] if it.get("unit_price") is not None else (svc.base_price if svc else 0))
        item = InvoiceItem(service_id=svc.id if svc else None, line_id=svc.line_id if svc else it.get("line_id"),
                           staff_id=default_staff_id(db, svc.id if svc else None, it.get("staff_id")),
                           description=it.get("description") or (svc.name if svc else ""),
                           quantity=qty, unit_price=price, discount=int(it.get("discount") or 0))
        if item.amount < 0:
            raise AccountingError("مبلغ آیتم منفی است")
        inv.items.append(item)
        revenue_by_line[item.line_id] = revenue_by_line.get(item.line_id, 0) + item.amount
        if svc:
            line_names[item.line_id] = svc.line.name
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


def void_invoice(db: Session, inv: Invoice, reason: str = "", user=None) -> Invoice:
    if inv.status == "void":
        return inv
    if inv.paid:
        raise AccountingError("ابتدا پرداخت‌ها/بیعانه‌های این فاکتور را برگشت بزنید")
    for original in db.scalars(select(JournalEntry).where(JournalEntry.ref_type.in_(["invoice", "commission"]),
                                                         JournalEntry.ref_id == inv.id)):
        post(db, f"ابطال {'فاکتور' if original.ref_type == 'invoice' else 'سهم پرسنل'} {inv.number}", [
            Leg(db.get(LedgerAccount, l.account_id), debit=l.credit, credit=l.debit, customer_id=l.customer_id,
                line_id=l.line_id, staff_id=l.staff_id)
            for l in original.lines
        ], "invoice_void", inv.id)
    inv.status = "void"
    audit(db, "invoice.void", "invoice", inv.id, {"reason": reason}, user=user)
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
def trial_balance(db: Session) -> list[dict]:
    rows = db.execute(
        select(LedgerAccount.code, LedgerAccount.name, LedgerAccount.type,
               func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
        .join(JournalLine, JournalLine.account_id == LedgerAccount.id, isouter=True)
        .group_by(LedgerAccount.id).order_by(LedgerAccount.code)
    ).all()
    out = []
    for code, name, typ, dr, cr in rows:
        if dr or cr:
            balance = dr - cr if typ in ("asset", "expense") else cr - dr
            out.append({"code": code, "name": name, "type": typ, "debit": int(dr), "credit": int(cr), "balance": int(balance)})
    return out


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


def account_balances(db: Session) -> list[dict]:
    out = []
    for pa in db.scalars(select(PaymentAccount).order_by(PaymentAccount.id)):
        acc = cash_account_for(db, pa)
        dr, cr = db.execute(
            select(func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
            .where(JournalLine.account_id == acc.id)
        ).one()
        out.append({"id": pa.id, "name": pa.name, "kind": pa.kind, "bank_name": pa.bank_name, "balance": int(dr) - int(cr)})
    return out
