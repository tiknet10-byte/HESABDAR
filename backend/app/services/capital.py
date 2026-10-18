"""The owner's capital and the business's worth at one glance.

* Capital (equity 3100): money the owner puts in (into a cash/bank account, or by paying a supplier from their own
  pocket) and takes out. What we "owed" a person who is really the owner (a customer named «سرمایه» brought over with
  a credit balance) can be turned into capital.
* Gold, coins and foreign currency (1400, one sub-account each): bought from a cash account, sold into one, brought in
  or taken out by the owner. Kept at what they cost (average cost); the market price is only for display, the gain is
  booked when they are sold (4900).
* position(): where the money is (cash, customers' debts, stock, gold/dollars), what the business owes, its net worth,
  and how the net worth changed in the period: worth at the start + put in - taken out + profit = worth now.

Every operation posts a balanced journal entry; nothing is deleted (a mistake is voided: its entry is reversed on its own
date, as if it never happened).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    CapitalMove,
    Customer,
    Deposit,
    Holding,
    HoldingMove,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    PaymentAccount,
    Purchase,
    Supplier,
    local_now,
)
from . import accounting
from .accounting import AccountingError, Leg, account, cash_account_for, ensure_not_future, post
from .audit import audit

CAPITAL = accounting.EQUITY  # 3100
HOLDINGS = "1400"
HOLDING_GAIN = "4900"
CHART = [
    (HOLDINGS, "طلا، سکه و ارز", "asset", "1000"),
    (HOLDING_GAIN, "سود و زیان فروش طلا و ارز", "revenue", "4000"),
]
KIND_FA = {"add": "پول گذاشتید", "withdraw": "پول برداشتید", "supplier": "به فروشنده از جیب خودتان دادید",
           "convert": "طلب شخص ← سرمایهٔ شما"}
HOLDING_KINDS = {"gold": "طلا", "coin": "سکه", "currency": "ارز", "other": "سایر"}
MOVE_FA = {"opening": "از قبل داشتیم", "buy": "خرید", "sell": "فروش", "owner_in": "شما آوردید (سرمایه)",
           "owner_out": "شما بردید (برداشت)"}
# names that suggest a person in the customer list is really the owner's capital
CAPITAL_WORDS = ("سرمایه", "مدیریت", "مدیر")


def ensure_chart(db: Session) -> None:
    existing = set(db.scalars(select(LedgerAccount.code)))
    for code, name, typ, parent in CHART:
        if code not in existing:
            db.add(LedgerAccount(code=code, name=name, type=typ, parent_code=parent, is_system=True))
    db.flush()


def _acc(db: Session, code: str) -> LedgerAccount:
    ensure_chart(db)
    return account(db, code)


def _positive(amount: int, label: str = "مبلغ") -> int:
    amount = int(amount or 0)
    if amount <= 0:
        raise AccountingError(f"{label} باید بیشتر از صفر باشد")
    return amount


# ------------------------------------------------------------------ owner's capital
def add_or_withdraw(db: Session, *, kind: str, amount: int, payment_account: PaymentAccount | None, at: datetime | None = None,
                    notes: str = "", user=None) -> CapitalMove:  # noqa: ANN001
    """add: the owner put money into this account. withdraw: the owner took money out of it for themselves."""
    if kind not in ("add", "withdraw"):
        raise AccountingError("نوع نامعتبر")
    if payment_account is None:
        raise AccountingError("حساب (صندوق، کارت یا بانک) را انتخاب کنید")
    amount = _positive(amount)
    ensure_not_future(at, "تاریخ")
    m = CapitalMove(kind=kind, amount=amount, payment_account_id=payment_account.id, at=at or local_now(), notes=notes,
                    user_id=getattr(user, "id", None))
    db.add(m)
    db.flush()
    cash, cap = cash_account_for(db, payment_account), _acc(db, CAPITAL)
    legs = [Leg(cash, debit=amount), Leg(cap, credit=amount)] if kind == "add" else [Leg(cap, debit=amount), Leg(cash, credit=amount)]
    post(db, f"{'آوردن سرمایه' if kind == 'add' else 'برداشت از سرمایه'} - {payment_account.name}" + (f" - {notes}" if notes else ""),
         legs, "capital", m.id, at=m.at)
    audit(db, f"capital.{kind}", "capital", m.id, {"amount": amount, "account": payment_account.id}, user=user)
    return m


def owner_paid_supplier(db: Session, *, purchase: Purchase, amount: int, at: datetime | None = None, notes: str = "",
                        kind: str = "supplier", customer_id: int | None = None, user=None) -> CapitalMove:  # noqa: ANN001
    """The owner paid (part of) what we owe for this purchase from their own pocket: the debt to the supplier goes
    down and the owner's capital goes up (no cash account of the business moves)."""
    from . import inventory

    if purchase.status == "void":
        raise AccountingError("این خرید باطل شده است")
    amount = _positive(amount)
    if purchase.paid + amount > purchase.total:
        raise AccountingError("مبلغ از ماندهٔ این خرید بیشتر است")
    ensure_not_future(at, "تاریخ پرداخت")
    m = CapitalMove(kind=kind, amount=amount, purchase_id=purchase.id, customer_id=customer_id, at=at or local_now(),
                    notes=notes, user_id=getattr(user, "id", None))
    db.add(m)
    inventory._add_paid(db, purchase, amount)
    sup = db.get(Supplier, purchase.supplier_id) if purchase.supplier_id else None
    post(db, f"{'پرداخت از جیب مدیر' if kind == 'supplier' else 'تبدیل طلب به سرمایه'} - خرید {purchase.number}"
         + (f" - {sup.name}" if sup else ""),
         [Leg(inventory._acc(db, inventory.PAYABLE), debit=amount), Leg(_acc(db, CAPITAL), credit=amount)], "capital", m.id, at=m.at)
    inventory._status(purchase)
    audit(db, f"capital.{kind}", "capital", m.id, {"amount": amount, "purchase": purchase.id}, user=user)
    return m


def convertible(db: Session, q: str = "") -> list[dict]:
    """People whose credit with us (open deposits, or what we owe them for purchases) may really be the owner's capital.
    Names like «سرمایه» / «مدیریت» are suggested; others are found by searching their name."""
    from .textutil import normalize_text

    key = normalize_text(q) if q else ""
    people: dict[int, dict] = {}

    def person(cid: int) -> dict | None:
        if cid in people:
            return people[cid]
        c = db.get(Customer, cid)
        if c is None:
            return None
        suggested = any(w in normalize_text(c.full_name or "") for w in map(normalize_text, CAPITAL_WORDS))
        if not suggested and not (key and key in normalize_text(c.full_name or "")):
            return None
        people[cid] = {"customer_id": c.id, "name": c.full_name, "code": c.legacy_code, "suggested": suggested,
                       "deposits": [], "purchases": [], "total": 0}
        return people[cid]

    for d in db.scalars(select(Deposit).where(Deposit.status == "held").order_by(Deposit.received_at)):
        p = person(d.customer_id)
        if p is not None:
            p["deposits"].append({"id": d.id, "amount": d.amount, "at": d.received_at.isoformat(timespec="minutes"), "notes": d.notes})
            p["total"] += d.amount
    for pur, cid in db.execute(select(Purchase, Supplier.customer_id).join(Supplier, Supplier.id == Purchase.supplier_id)
                               .where(Purchase.status != "void", Purchase.total > Purchase.paid, Supplier.customer_id.is_not(None))):
        p = person(cid)
        if p is not None:
            p["purchases"].append({"id": pur.id, "number": pur.number, "due": pur.total - pur.paid,
                                   "at": pur.at.isoformat(timespec="minutes"), "notes": pur.notes})
            p["total"] += pur.total - pur.paid
    return sorted((p for p in people.values() if p["total"] > 0), key=lambda p: (not p["suggested"], -p["total"]))


def convert(db: Session, *, deposit_ids: list[int] | None = None, purchase_ids: list[int] | None = None, user=None) -> dict:  # noqa: ANN001
    """Turn these open deposits / purchase debts into the owner's capital, dated when they arose (they were capital
    from the start). Each one can be undone (void) later."""
    out = {"converted": 0, "amount": 0}
    for did in deposit_ids or []:
        d = db.get(Deposit, int(did))
        if d is None or d.status != "held" or not accounting._claim(db, d, "held", "capital"):
            raise AccountingError("این بیعانه دیگر باز نیست")
        m = CapitalMove(kind="convert", amount=d.amount, deposit_id=d.id, customer_id=d.customer_id, at=d.received_at,
                        notes=f"بیعانه/طلب #{d.id}", user_id=getattr(user, "id", None))
        db.add(m)
        db.flush()
        c = db.get(Customer, d.customer_id)
        post(db, f"تبدیل طلب «{c.full_name if c else ''}» به سرمایهٔ مدیر", [
            Leg(account(db, accounting.DEPOSITS), debit=d.amount, customer_id=d.customer_id),
            Leg(_acc(db, CAPITAL), credit=d.amount, customer_id=d.customer_id),
        ], "capital", m.id, at=m.at)
        d.status, d.closed_at = "capital", d.received_at
        audit(db, "capital.convert", "deposit", d.id, {"amount": d.amount, "customer": d.customer_id}, user=user)
        out["converted"] += 1
        out["amount"] += d.amount
    for pid in purchase_ids or []:
        pur = db.get(Purchase, int(pid))
        if pur is None:
            raise AccountingError("خرید پیدا نشد")
        sup = db.get(Supplier, pur.supplier_id) if pur.supplier_id else None
        m = owner_paid_supplier(db, purchase=pur, amount=pur.total - pur.paid, at=pur.at, kind="convert",
                                customer_id=sup.customer_id if sup else None, notes=f"طلب خرید {pur.number}", user=user)
        out["converted"] += 1
        out["amount"] += m.amount
    return out


def void_move(db: Session, m: CapitalMove, reason: str = "", user=None) -> CapitalMove:  # noqa: ANN001
    """Undo a capital move entered by mistake: its entry is reversed on its own date (as if it never happened)."""
    from . import inventory

    if m.status == "void" or not accounting._claim(db, m, "active", "void"):
        return m
    if m.deposit_id:
        d = db.get(Deposit, m.deposit_id)
        if d is None or not accounting._claim(db, d, "capital", "held"):
            raise AccountingError("وضعیت این بیعانه عوض شده است")
        d.closed_at = None
    if m.purchase_id:
        pur = db.get(Purchase, m.purchase_id)
        if pur.status != "void":
            pur.paid -= m.amount
            inventory._status(pur)
    _reverse(db, "capital", m.id, f"ابطال {KIND_FA.get(m.kind, m.kind)}", m.at)
    m.status = "void"
    audit(db, "capital.void", "capital", m.id, {"kind": m.kind, "amount": m.amount, "reason": reason}, user=user)
    return m


def void_for_purchase(db: Session, pur: Purchase, user=None) -> None:  # noqa: ANN001
    """A purchase is voided: what the owner paid on it from their own pocket goes back to them (reversed)."""
    for m in list(db.scalars(select(CapitalMove).where(CapitalMove.purchase_id == pur.id, CapitalMove.status == "active"))):
        _reverse(db, "capital", m.id, f"ابطال {KIND_FA.get(m.kind, m.kind)} (خرید {pur.number} باطل شد)", m.at)
        m.status = "void"
        audit(db, "capital.void", "capital", m.id, {"kind": m.kind, "amount": m.amount, "reason": "purchase void"}, user=user)


def _reverse(db: Session, ref_type: str, ref_id: int, description: str, at: datetime) -> None:
    for e in list(db.scalars(select(JournalEntry).where(JournalEntry.ref_type == ref_type, JournalEntry.ref_id == ref_id))):
        post(db, description, [Leg(db.get(LedgerAccount, ln.account_id), debit=ln.credit, credit=ln.debit, customer_id=ln.customer_id,
                                   line_id=ln.line_id, staff_id=ln.staff_id) for ln in e.lines], f"{ref_type}_void", ref_id, at=at)


def move_out(db: Session, m: CapitalMove) -> dict:
    pa = db.get(PaymentAccount, m.payment_account_id) if m.payment_account_id else None
    pur = db.get(Purchase, m.purchase_id) if m.purchase_id else None
    sup = db.get(Supplier, pur.supplier_id) if pur and pur.supplier_id else None
    c = db.get(Customer, m.customer_id) if m.customer_id else None
    sign = -1 if m.kind == "withdraw" else 1
    return {"id": m.id, "at": m.at.isoformat(timespec="minutes"), "kind": m.kind, "kind_label": KIND_FA.get(m.kind, m.kind),
            "amount": m.amount, "signed": sign * m.amount, "account": pa.name if pa else None, "purchase_id": m.purchase_id,
            "purchase": pur.number if pur else None, "supplier": sup.name if sup else None, "customer_id": m.customer_id,
            "customer": c.full_name if c else None, "deposit_id": m.deposit_id, "notes": m.notes, "status": m.status}


# ------------------------------------------------------------------ gold, coins, currency
def holding_account(db: Session, h: Holding) -> LedgerAccount:
    if h.ledger_account_id:
        return db.get(LedgerAccount, h.ledger_account_id)
    _acc(db, HOLDINGS)
    acc = LedgerAccount(code=accounting._next_child_code(db, HOLDINGS), name=h.name, type="asset", parent_code=HOLDINGS)
    db.add(acc)
    db.flush()
    h.ledger_account_id = acc.id
    return acc


def create_holding(db: Session, *, name: str, kind: str = "gold", unit: str = "گرم", notes: str = "", user=None) -> Holding:  # noqa: ANN001
    name = (name or "").strip()
    if not name:
        raise AccountingError("نام را بنویسید (مثلاً طلای آب‌شده، سکهٔ امامی، دلار)")
    if db.scalar(select(Holding).where(Holding.name == name)):
        raise AccountingError("دارایی با همین نام وجود دارد")
    h = Holding(name=name, kind=kind if kind in HOLDING_KINDS else "other", unit=(unit or "").strip() or "عدد", notes=notes)
    db.add(h)
    db.flush()
    holding_account(db, h)
    audit(db, "holding.create", "holding", h.id, {"name": name}, user=user)
    return h


def parse_qty(qty) -> int:  # noqa: ANN001
    """'12.5' grams -> 12500 thousandths (exact; no float rounding). Persian digits and «٫» are accepted."""
    from decimal import Decimal, InvalidOperation

    s = str(qty).strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫،", "01234567890123456789..")).replace(",", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise AccountingError("مقدار نامعتبر است") from None
    m = d * 1000
    if m != m.to_integral_value():
        raise AccountingError("مقدار حداکثر با سه رقم اعشار")
    return int(m)


def _refresh(db: Session, h: Holding) -> None:
    db.flush()
    q, c = db.execute(select(func.coalesce(func.sum(HoldingMove.qty_m), 0), func.coalesce(func.sum(HoldingMove.cost), 0))
                      .where(HoldingMove.holding_id == h.id, HoldingMove.status == "active")).one()
    h.qty_m, h.cost = int(q), int(c)


def holding_move(db: Session, h: Holding, *, kind: str, qty, amount: int = 0, payment_account: PaymentAccount | None = None,  # noqa: ANN001
                 at: datetime | None = None, notes: str = "", user=None) -> HoldingMove:  # noqa: ANN001
    """opening / buy / owner_in bring the holding in at `amount` (its cost); sell / owner_out take `qty` out at the
    average cost (sell: money `amount` into the account, the difference is a gain or loss)."""
    if kind not in MOVE_FA:
        raise AccountingError("نوع نامعتبر")
    if not h.is_active:
        raise AccountingError("این دارایی غیرفعال است")
    q = parse_qty(qty)
    if q <= 0:
        raise AccountingError("مقدار باید بیشتر از صفر باشد")
    ensure_not_future(at, "تاریخ")
    at = at or local_now()
    _refresh(db, h)
    acc = holding_account(db, h)
    cap = _acc(db, CAPITAL)
    if kind in ("buy", "sell") and payment_account is None:
        raise AccountingError("حساب پرداخت/دریافت را انتخاب کنید")
    # the average cost of what goes out depends on what came in before it: moves are kept in date order
    later = select(func.count(HoldingMove.id)).where(HoldingMove.holding_id == h.id, HoldingMove.status == "active", HoldingMove.at > at)
    if kind in ("opening", "buy", "owner_in"):
        later = later.where(HoldingMove.qty_m < 0)
    if db.scalar(later):
        raise AccountingError("برای این دارایی بعد از این تاریخ فروش یا برداشت ثبت شده؛ تاریخ را بعد از آخرین گردش بگذارید")
    m = HoldingMove(holding_id=h.id, kind=kind, at=at, notes=notes,
                    payment_account_id=payment_account.id if payment_account else None)
    label = f"{h.name} ({_fmt_qty(q)} {h.unit})"
    if kind in ("opening", "buy", "owner_in"):
        amount = _positive(amount, "مبلغ (ارزش)")
        m.qty_m, m.amount, m.cost = q, amount, amount
        credit = {"opening": account(db, accounting.OPENING), "owner_in": cap}.get(kind) or cash_account_for(db, payment_account)
        legs = [Leg(acc, debit=amount), Leg(credit, credit=amount)]
        text = {"opening": "طلا/ارز اول دوره", "buy": "خرید", "owner_in": "آوردن طلا/ارز توسط مدیر (سرمایه)"}[kind]
    else:
        if q > h.qty_m:
            raise AccountingError(f"موجودی {h.name} فقط {_fmt_qty(h.qty_m)} {h.unit} است")
        book = h.cost if q == h.qty_m else h.cost * q // h.qty_m  # average cost of what goes out (exact integers)
        m.qty_m, m.cost = -q, -book
        if kind == "sell":
            amount = _positive(amount, "مبلغ فروش")
            m.amount, m.gain = amount, amount - book
            legs = [Leg(cash_account_for(db, payment_account), debit=amount), Leg(acc, credit=book)]
            if m.gain > 0:
                legs.append(Leg(_acc(db, HOLDING_GAIN), credit=m.gain))
            elif m.gain < 0:
                legs.append(Leg(_acc(db, HOLDING_GAIN), debit=-m.gain))
            text = "فروش"
        else:
            m.amount = book
            legs = [Leg(cap, debit=book), Leg(acc, credit=book)]
            text = "برداشت طلا/ارز توسط مدیر"
    db.add(m)
    db.flush()
    post(db, f"{text} {label}" + (f" - {notes}" if notes else ""), [lg for lg in legs if lg.debit or lg.credit], "holding", m.id, at=at)
    _refresh(db, h)
    audit(db, f"holding.{kind}", "holding", h.id, {"move": m.id, "qty_m": m.qty_m, "amount": m.amount}, user=user)
    return m


def void_holding_move(db: Session, m: HoldingMove, reason: str = "", user=None) -> HoldingMove:  # noqa: ANN001
    """Only the latest move of a holding can be undone (the average cost of later moves depends on earlier ones)."""
    if m.status == "void":
        return m
    db.flush()
    last = db.scalar(select(HoldingMove).where(HoldingMove.holding_id == m.holding_id, HoldingMove.status == "active")
                     .order_by(HoldingMove.at.desc(), HoldingMove.id.desc()).limit(1))
    if last is None or last.id != m.id:
        raise AccountingError("فقط آخرین گردش این دارایی را می‌شود باطل کرد؛ اول گردش‌های بعدی را باطل کنید")
    h = db.get(Holding, m.holding_id)
    if m.qty_m > 0:
        _refresh(db, h)
        if h.qty_m - m.qty_m < 0:
            raise AccountingError("بعد از این ورود، بخشی از آن خارج شده است")
    if not accounting._claim(db, m, "active", "void"):
        return m
    _reverse(db, "holding", m.id, f"ابطال {MOVE_FA.get(m.kind, m.kind)} {h.name}", m.at)
    _refresh(db, h)
    audit(db, "holding.void", "holding", h.id, {"move": m.id, "reason": reason}, user=user)
    return m


def set_price(db: Session, h: Holding, price: int, user=None) -> Holding:  # noqa: ANN001
    """Today's market price of one unit (Rial) - only to show what the holding is worth now; nothing is booked."""
    price = int(price or 0)
    if price < 0:
        raise AccountingError("قیمت نامعتبر است")
    h.market_price, h.market_at = (price or None), local_now()
    audit(db, "holding.price", "holding", h.id, {"price": price}, user=user)
    return h


def _fmt_qty(q: int) -> str:
    whole, frac = divmod(abs(q), 1000)
    s = f"{whole}" + (f".{frac:03d}".rstrip("0") if frac else "")
    return ("-" if q < 0 else "") + s


def market_value(h: Holding) -> int | None:
    return None if h.market_price is None else h.qty_m * h.market_price // 1000


def holding_out(db: Session, h: Holding) -> dict:
    mv = market_value(h)
    return {"id": h.id, "name": h.name, "kind": h.kind, "kind_label": HOLDING_KINDS.get(h.kind, h.kind), "unit": h.unit,
            "qty": _fmt_qty(h.qty_m), "qty_m": h.qty_m, "cost": h.cost, "avg_cost": h.cost * 1000 // h.qty_m if h.qty_m else 0,
            "market_price": h.market_price, "market_at": h.market_at.isoformat(timespec="minutes") if h.market_at else None,
            "market_value": mv, "unrealized": (mv - h.cost) if mv is not None else None, "is_active": h.is_active, "notes": h.notes}


def holding_move_out(db: Session, m: HoldingMove) -> dict:
    pa = db.get(PaymentAccount, m.payment_account_id) if m.payment_account_id else None
    return {"id": m.id, "at": m.at.isoformat(timespec="minutes"), "kind": m.kind, "kind_label": MOVE_FA.get(m.kind, m.kind),
            "qty": _fmt_qty(m.qty_m), "qty_m": m.qty_m, "amount": m.amount, "cost": m.cost, "gain": m.gain,
            "account": pa.name if pa else None, "notes": m.notes, "status": m.status}


# ------------------------------------------------------------------ the one-glance position
def year_start(today: date | None = None) -> date:
    """1 Farvardin of the current Jalali year."""
    from .jalali import gregorian_to_jalali, jalali_to_gregorian

    today = today or local_now().date()
    jy, _, _ = gregorian_to_jalali(today.year, today.month, today.day)
    return date(*jalali_to_gregorian(jy, 1, 1))


def _balances(db: Session, before: datetime | None = None, since: datetime | None = None) -> dict[int, int]:
    """account id -> debit - credit of the entries dated in [since, before)."""
    q = (select(JournalLine.account_id, func.coalesce(func.sum(JournalLine.debit), 0) - func.coalesce(func.sum(JournalLine.credit), 0))
         .join(JournalEntry, JournalEntry.id == JournalLine.entry_id).group_by(JournalLine.account_id))
    if before is not None:
        q = q.where(JournalEntry.at < before)
    if since is not None:
        q = q.where(JournalEntry.at >= since)
    return {aid: int(v) for aid, v in db.execute(q)}


PROFIT_PARTS = [  # (key, label, code prefix test, sign): revenue +, expense -
    ("services", "فروش خدمات", lambda c: c.startswith("41"), 1),
    ("products", "فروش محصولات", lambda c: c.startswith("42"), 1),
    ("forfeits", "بیعانه‌های سوخت‌شده", lambda c: c.startswith("48"), 1),
    ("holding_gain", "سود/زیان فروش طلا و ارز", lambda c: c.startswith("49"), 1),
    ("cogs", "بهای خرید کالاهای فروخته‌شده", lambda c: c.startswith("54"), -1),
    ("commission", "سهم پرسنل", lambda c: c.startswith("52"), -1),
    ("expenses", "هزینه‌ها", lambda c: c.startswith("51"), -1),
    ("cash_diff", "کسری/اضافهٔ صندوق", lambda c: c.startswith("53"), -1),
    ("shrinkage", "کسری و ضایعات انبار", lambda c: c.startswith("55"), -1),
]


def profit_breakdown(db: Session, s: datetime | None, e: datetime | None) -> dict:
    """Profit of the entries dated in [s, e) straight from the ledger: every income (+) and cost (-) of the period."""
    accs = {a.id: a for a in db.scalars(select(LedgerAccount))}
    bal = _balances(db, before=e, since=s)
    parts = {k: 0 for k, *_ in PROFIT_PARTS}
    other = 0
    for aid, v in bal.items():
        a = accs.get(aid)
        if a is None or a.type not in ("revenue", "expense"):
            continue
        amount = -v if a.type == "revenue" else v  # natural balance
        for key, _, test, sign in PROFIT_PARTS:
            if test(a.code):
                parts[key] += amount * sign
                break
        else:
            other += -v  # any other revenue (+) / expense (-)
    rows = [{"key": k, "label": label, "value": parts[k]} for k, label, _, _ in PROFIT_PARTS if parts[k]]
    if other:
        rows.append({"key": "other", "label": "سایر درآمدها و هزینه‌ها", "value": other})
    return {"rows": rows, "total": sum(r["value"] for r in rows)}


def _snapshot(db: Session, before: datetime | None) -> dict:
    accs = {a.id: a for a in db.scalars(select(LedgerAccount))}
    bal = _balances(db, before=before)
    by_code: dict[str, int] = {}
    totals = {"asset": 0, "liability": 0, "equity": 0, "revenue": 0, "expense": 0}
    for aid, v in bal.items():
        a = accs.get(aid)
        if a is None:
            continue
        natural = v if a.type in ("asset", "expense") else -v
        totals[a.type] = totals.get(a.type, 0) + natural
        group = a.parent_code if a.parent_code and a.parent_code not in ("1000", "2000", "3000", "4000", "5000") else a.code
        by_code[group] = by_code.get(group, 0) + natural
    return {"by_code": by_code, "totals": totals, "worth": totals["asset"] - totals["liability"]}


def position(db: Session, start: date | None = None, end: date | None = None) -> dict:
    ensure_chart(db)
    start = start or year_start()
    end = end or local_now().date()
    s = datetime.combine(start, datetime.min.time())
    e = datetime.combine(end, datetime.min.time()) + timedelta(days=1)
    now, before = _snapshot(db, e), _snapshot(db, s)
    c = now["by_code"]
    cash = []
    for pa in db.scalars(select(PaymentAccount).order_by(PaymentAccount.id)):
        bal = _balances_of(db, cash_account_for(db, pa).id, e)
        if bal or pa.is_active:
            cash.append({"id": pa.id, "name": pa.name, "kind": pa.kind, "balance": bal})
    holdings = [holding_out(db, h) for h in db.scalars(select(Holding).order_by(Holding.id)) if h.is_active or h.qty_m]
    known = {accounting.CASH_PARENT, accounting.AR, "1300", HOLDINGS, accounting.DEPOSITS, "2200", "2300"}
    assets = [
        {"key": "cash", "label": "پول نقد، کارت‌ها و حساب‌های بانکی", "value": c.get(accounting.CASH_PARENT, 0)},
        {"key": "receivable", "label": "طلب از مشتری‌ها", "value": c.get(accounting.AR, 0)},
        {"key": "stock", "label": "کالاهای انبار (به قیمت خرید)", "value": c.get("1300", 0)},
        {"key": "holdings", "label": "طلا، سکه و ارز (به قیمت خرید)", "value": c.get(HOLDINGS, 0)},
    ]
    accs = {a.code: a for a in db.scalars(select(LedgerAccount))}
    other_assets = sum(v for k, v in c.items() if k not in known and accs.get(k) is not None and accs[k].type == "asset")
    # 2200 per person: what we owe some, and what others owe us (took more than they earned: taken from next month)
    owed, owing = _staff_split(db, e)
    if owing:
        assets.append({"key": "staff_advances", "label": "طلب از پرسنل و کارمندان (برداشتی بیش از کارکرد)", "value": owing})
    if other_assets:
        assets.append({"key": "other", "label": "سایر دارایی‌ها", "value": other_assets})
    liabilities = [
        {"key": "deposits", "label": "بیعانه‌های مشتری‌ها (پیش‌پرداخت خدمات)", "value": c.get(accounting.DEPOSITS, 0)},
        {"key": "staff", "label": "سهم پرسنل و حقوق کارمندان (پرداخت‌نشده)", "value": owed},
        {"key": "suppliers", "label": "بدهی به فروشنده‌ها", "value": c.get("2300", 0)},
    ]
    other_liab = sum(v for k, v in c.items() if k not in known and accs.get(k) is not None and accs[k].type == "liability")
    if other_liab:
        liabilities.append({"key": "other", "label": "سایر بدهی‌ها", "value": other_liab})
    unrealized = sum(h["unrealized"] for h in holdings if h["unrealized"] is not None)
    missing_price = [h["name"] for h in holdings if h["qty_m"] and h["market_price"] is None]

    # how the worth changed in the period: start + put in - taken out + opening balances entered + profit = now
    moves = list(db.scalars(select(CapitalMove).where(CapitalMove.status == "active", CapitalMove.at >= s, CapitalMove.at < e)))
    hmoves = list(db.scalars(select(HoldingMove).where(HoldingMove.status == "active", HoldingMove.kind.in_(("owner_in", "owner_out")),
                                                       HoldingMove.at >= s, HoldingMove.at < e)))
    put_in = sum(m.amount for m in moves if m.kind != "withdraw") + sum(m.amount for m in hmoves if m.kind == "owner_in")
    taken_out = sum(m.amount for m in moves if m.kind == "withdraw") + sum(m.amount for m in hmoves if m.kind == "owner_out")
    profit = profit_breakdown(db, s, e)
    opening_acc = account(db, accounting.OPENING)
    opening_in = -_balances(db, before=e, since=s).get(opening_acc.id, 0)
    flow = [
        {"key": "start", "label": f"ارزش خالص در {start.isoformat()}", "value": before["worth"]},
        {"key": "put_in", "label": "پولی که گذاشتید (سرمایه)", "value": put_in},
        {"key": "taken_out", "label": "پولی که برداشتید", "value": -taken_out},
        {"key": "profit", "label": "سود (یا زیان) این دوره", "value": profit["total"]},
    ]
    if opening_in:
        flow.append({"key": "opening", "label": "مانده‌های اول دوره که در این بازه وارد شد", "value": opening_in})
    rest = now["worth"] - sum(f["value"] for f in flow)
    if rest:
        flow.append({"key": "other", "label": "سایر تغییرات سرمایه", "value": rest})
    t = now["totals"]
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "assets": assets, "assets_total": t["asset"] + owing,
        "liabilities": liabilities, "liabilities_total": t["liability"] + owing,
        "worth": now["worth"],  # what the business is worth to the owner, at book value
        "worth_market": now["worth"] + unrealized,  # with gold/dollars at today's price
        "unrealized": unrealized, "missing_price": missing_price,
        "cash": cash, "holdings": holdings,
        "equity": {"capital": c.get(CAPITAL, 0), "opening": c.get(accounting.OPENING, 0),
                   "profit_to_date": t["revenue"] - t["expense"]},
        "flow": flow, "worth_start": before["worth"],
        "profit": profit,
        "moves": [move_out(db, m) for m in sorted(moves, key=lambda m: (m.at, m.id), reverse=True)],
        # equity + profit must equal assets - liabilities (the books balance); 0 when everything is right
        "check": t["asset"] - t["liability"] - t["equity"] - (t["revenue"] - t["expense"]),
    }


def _staff_split(db: Session, before: datetime) -> tuple[int, int]:
    """(owed to people, owed by people) on 2200 just before `before`, person by person."""
    acc = account(db, accounting.STAFF_PAYABLE)
    per = (select(func.sum(JournalLine.credit - JournalLine.debit).label("v")).join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
           .where(JournalLine.account_id == acc.id, JournalEntry.at < before).group_by(JournalLine.staff_id).subquery())
    owed = int(db.scalar(select(func.coalesce(func.sum(per.c.v), 0)).where(per.c.v > 0)) or 0)
    owing = -int(db.scalar(select(func.coalesce(func.sum(per.c.v), 0)).where(per.c.v < 0)) or 0)
    return owed, owing


def _balances_of(db: Session, account_id: int, before: datetime) -> int:
    v = db.scalar(select(func.coalesce(func.sum(JournalLine.debit), 0) - func.coalesce(func.sum(JournalLine.credit), 0))
                  .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                  .where(JournalLine.account_id == account_id, JournalEntry.at < before))
    return int(v or 0)
