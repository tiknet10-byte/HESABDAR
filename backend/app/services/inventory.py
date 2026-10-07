"""Products: stock, purchases and the cost of what is sold (COGS) - the basis of product profit.

Costing methods (Settings > products; both FIFO and weighted average are accepted by IAS 2 and the Iranian
accounting standard No. 8 - LIFO is not, so it is not offered):

* ``average``  - moving weighted average (perpetual): after every purchase the unit cost becomes
                 (value in stock + purchase cost) / (qty in stock + purchased qty); a sale takes that cost.
* ``fifo``     - first in, first out: a sale uses the cost of the oldest units still in stock.
* ``periodic`` - periodic weighted average per (Jalali) month: every sale of a month costs
                 (value at the start of the month + the month's purchases) / (qty at start + qty bought).

Exactness: all amounts are integer Rial. A product's cost figures are never stored "as we go": the engine replays
every stock move of the product in date order and recomputes the cost of each sale, return and adjustment. So a
purchase entered late, a voided invoice or a change of method gives exactly the figures the method defines, and
the ledger entries of the moves whose cost changed are re-posted. Splitting a cost over units always leaves the
remainder with the units still in stock, so (cost of everything that came in) = (cost of everything that went out)
+ (value still in stock) to the Rial.

Selling more than is in stock is allowed (stock may not have been recorded yet): those units take the cost of the
next purchase (back-filled when it is entered); until then their cost is an estimate (last known unit cost) and
the sale is marked as such.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    InvoiceItem,
    JournalEntry,
    LedgerAccount,
    PaymentAccount,
    Product,
    Purchase,
    PurchaseItem,
    StockMove,
    Supplier,
    SupplierPayment,
    local_now,
)
from . import settings_store
from .accounting import OPENING, AccountingError, Leg, account, cash_account_for, ensure_not_future, post
from .audit import audit
from .jalali import gregorian_to_jalali

INVENTORY = "1300"
PAYABLE = "2300"
PRODUCT_REVENUE = "4200"
COGS = "5400"
SHRINKAGE = "5500"
CHART = [
    (INVENTORY, "موجودی کالا (محصولات)", "asset", "1000"),
    (PAYABLE, "حساب‌های پرداختنی تأمین‌کنندگان", "liability", "2000"),
    (PRODUCT_REVENUE, "درآمد فروش محصولات", "revenue", "4000"),
    (COGS, "بهای تمام‌شدهٔ کالای فروش‌رفته", "expense", "5000"),
    (SHRINKAGE, "کسری، ضایعات و اضافات انبار", "expense", "5000"),
]

METHODS = {
    "average": "میانگین موزون متحرک",
    "fifo": "اولین صادره از اولین وارده (FIFO)",
    "periodic": "میانگین موزون ماهانه (دوره‌ای)",
}
FIXED_IN = ("opening", "purchase")  # inbound moves whose cost is given
OUT = ("sale", "adjust_out")
ENGINE_IN = ("sale_return", "adjust_in")  # inbound moves whose cost the engine works out
# ledger accounts of engine-costed moves: (debit, credit)
MOVE_ACCOUNTS = {"sale": (COGS, INVENTORY), "sale_return": (INVENTORY, COGS),
                 "adjust_out": (SHRINKAGE, INVENTORY), "adjust_in": (INVENTORY, SHRINKAGE)}
MOVE_LABELS = {"opening": "موجودی اول دوره", "purchase": "خرید", "sale": "فروش", "sale_return": "برگشت از فروش",
               "adjust_in": "اضافهٔ انبار", "adjust_out": "کسری انبار"}


def ensure_chart(db: Session) -> None:
    existing = set(db.scalars(select(LedgerAccount.code)))
    for code, name, typ, parent in CHART:
        if code not in existing:
            db.add(LedgerAccount(code=code, name=name, type=typ, parent_code=parent, is_system=True))
    db.flush()


def _acc(db: Session, code: str) -> LedgerAccount:
    ensure_chart(db)
    return account(db, code)


def method(db: Session) -> str:
    m = settings_store.get(db, "products.costing", "average")
    return m if m in METHODS else "average"


def next_product_code(db: Session, reserved: set[str] | None = None) -> str:
    """Product codes continue the accounting software's numbering (Tizpardaz: 1, 2, 3 ...); `reserved` codes are skipped."""
    nums = {int(c) for c in [*db.scalars(select(Product.code)), *(reserved or ())] if c and c.isdigit()}
    n = max((x for x in nums if x < 10000), default=0) + 1
    while n in nums:
        n += 1
    return str(n)


# ------------------------------------------------------------------ the engine
def _part(total: int, take: int, qty: int) -> int:
    """Cost of `take` units out of `qty` units worth `total`; taking all of them takes all of the value."""
    return total if take >= qty else total * take // qty


def _period(at: datetime) -> tuple[int, int]:
    y, m, _ = gregorian_to_jalali(at.year, at.month, at.day)
    return y, m


def replay(moves: list[StockMove], how: str) -> dict:
    """Pure function of the move history (sorted by date, then id). Returns per-move cost and the stock left."""
    real: dict[int, int] = defaultdict(int)  # cost of the covered units of outbound moves
    pending: list[list[int]] = []  # outbound units not covered by stock yet: [move_id, qty]
    pend_units: dict[int, int] = defaultdict(int)
    cost_in: dict[int, int] = {}
    layers: list[list[int]] = []  # fifo: [qty, value]
    pool = [0, 0]  # average / periodic: [qty, value]
    last_unit = 0
    by_id = {m.id: m for m in moves}

    def fill_pending(q: int, value: int) -> tuple[int, int]:
        """A new arrival first covers units already sold without stock, at its own cost."""
        while q > 0 and pending:
            mid, k = pending[0]
            take = min(k, q)
            part = _part(value, take, q)
            real[mid] += part
            pend_units[mid] -= take
            q, value = q - take, value - part
            if take == k:
                pending.pop(0)
            else:
                pending[0][1] = k - take
        return q, value

    def add(q: int, value: int) -> None:
        nonlocal last_unit
        if q <= 0:
            return
        last_unit = value // q if q else last_unit
        q, value = fill_pending(q, value)
        if q <= 0:
            return
        if how == "fifo":
            layers.append([q, value])
        else:
            pool[0] += q
            pool[1] += value

    def take(mid: int, k: int) -> None:
        """Take k units out at the method's cost; whatever stock can't cover waits for the next arrival."""
        left = k
        if how == "fifo":
            while left > 0 and layers:
                q, value = layers[0]
                t = min(q, left)
                part = _part(value, t, q)
                real[mid] += part
                left -= t
                if t == q:
                    layers.pop(0)
                else:
                    layers[0] = [q - t, value - part]
        else:
            t = min(pool[0], left)
            if t > 0:
                part = _part(pool[1], t, pool[0])
                real[mid] += part
                pool[0] -= t
                pool[1] -= part
                left -= t
        if left > 0:
            pending.append([mid, left])
            pend_units[mid] += left

    def stock_unit() -> int:
        q = sum(x[0] for x in layers) if how == "fifo" else pool[0]
        v = sum(x[1] for x in layers) if how == "fifo" else pool[1]
        return v // q if q > 0 else last_unit

    def engine_in_cost(m: StockMove) -> int:
        if m.kind == "sale_return" and m.ref_move_id in by_id:
            sale = by_id[m.ref_move_id]
            sold = -sale.qty
            total = real[sale.id] + pend_units[sale.id] * last_unit  # what that sale cost (estimate for uncovered units)
            return _part(total, m.qty, sold) if sold > 0 else 0
        return stock_unit() * m.qty

    if how == "periodic":
        groups: dict[tuple[int, int], list[StockMove]] = defaultdict(list)
        for m in moves:
            groups[_period(m.at)].append(m)
        for key in sorted(groups):
            ms = groups[key]
            for m in ms:  # 1. the month's purchases (after covering earlier unbacked sales) join the pool
                if m.kind in FIXED_IN:
                    cost_in[m.id] = int(m.cost)
                    add(m.qty, int(m.cost))
            for m in ms:  # 2. every sale / shortage of the month at the month's average
                if m.kind in OUT:
                    take(m.id, -m.qty)
            for m in ms:  # 3. returns and found stock come back at their own cost
                if m.kind in ENGINE_IN:
                    cost_in[m.id] = engine_in_cost(m)
                    add(m.qty, cost_in[m.id])
    else:
        for m in moves:
            if m.kind in FIXED_IN:
                cost_in[m.id] = int(m.cost)
                add(m.qty, int(m.cost))
            elif m.kind in OUT:
                take(m.id, -m.qty)
            elif m.kind in ENGINE_IN:
                cost_in[m.id] = engine_in_cost(m)
                add(m.qty, cost_in[m.id])

    costs: dict[int, tuple[int, bool]] = {}
    for m in moves:
        if m.kind in OUT:
            est = pend_units[m.id] * last_unit
            costs[m.id] = (real[m.id] + est, pend_units[m.id] > 0)
        else:
            costs[m.id] = (cost_in.get(m.id, int(m.cost)), False)
    qty = sum(m.qty for m in moves)
    value = sum(x[1] for x in layers) if how == "fifo" else pool[1]
    in_stock = sum(x[0] for x in layers) if how == "fifo" else pool[0]
    return {"costs": costs, "qty": qty, "value": value, "in_stock": in_stock, "last_unit": last_unit,
            "unit": value // in_stock if in_stock > 0 else last_unit, "pending": sum(k for _, k in pending)}


def _post_move(db: Session, m: StockMove, product: Product) -> None:
    """(Re)post the ledger entry of an engine-costed move so the books follow its current cost."""
    for e in db.scalars(select(JournalEntry).where(JournalEntry.ref_type == "stock_move", JournalEntry.ref_id == m.id)):
        db.delete(e)  # through the ORM, so its lines go with it
    db.flush()
    if m.kind not in MOVE_ACCOUNTS or m.cost <= 0:
        return
    dr, cr = MOVE_ACCOUNTS[m.kind]
    post(db, f"{MOVE_LABELS[m.kind]} - {product.name} ({abs(m.qty)} {product.unit})",
         [Leg(_acc(db, dr), debit=m.cost), Leg(_acc(db, cr), credit=m.cost)], "stock_move", m.id, at=m.at)


def recost(db: Session, product_id: int) -> dict:
    """Recompute every cost of one product from its move history and bring the ledger in line."""
    p = db.get(Product, product_id)
    db.flush()
    moves = list(db.scalars(select(StockMove).where(StockMove.product_id == product_id).order_by(StockMove.at, StockMove.id)))
    r = replay(moves, method(db))
    ids = [m.id for m in moves if m.kind not in FIXED_IN]
    posted = set(db.scalars(select(JournalEntry.ref_id).where(JournalEntry.ref_type == "stock_move", JournalEntry.ref_id.in_(ids)))) if ids else set()
    for m in moves:
        cost, est = r["costs"][m.id]
        if m.kind in FIXED_IN:
            continue
        if cost != m.cost or est != m.estimated or (cost > 0) != (m.id in posted):
            m.cost, m.estimated = cost, est
            _post_move(db, m, p)
    p.stock_qty, p.stock_value, p.unit_cost = r["qty"], r["value"], r["unit"] or p.unit_cost
    last_buy = db.scalar(select(StockMove).where(StockMove.product_id == product_id, StockMove.kind.in_(FIXED_IN))
                         .order_by(StockMove.at.desc(), StockMove.id.desc()).limit(1))
    p.last_purchase_cost = last_buy.cost // last_buy.qty if last_buy and last_buy.qty else p.last_purchase_cost
    db.flush()
    return r


def recost_all(db: Session) -> int:
    ids = list(db.scalars(select(Product.id)))
    for pid in ids:
        recost(db, pid)
    return len(ids)


def set_method(db: Session, how: str, user=None) -> int:  # noqa: ANN001
    if how not in METHODS:
        raise AccountingError("روش محاسبهٔ بهای تمام‌شده نامعتبر است")
    old = method(db)
    settings_store.set_value(db, "products.costing", how)
    db.flush()
    n = recost_all(db) if how != old else 0
    audit(db, "products.costing", "settings", "products.costing", {"from": old, "to": how, "products": n}, user=user)
    return n


# ------------------------------------------------------------------ stock in / out
def opening_stock(db: Session, product: Product, qty: int, unit_cost: int, at: datetime | None = None, user=None) -> StockMove:  # noqa: ANN001
    """Stock already on the shelf when starting with the system (value goes to opening balances)."""
    if qty <= 0 or unit_cost < 0:
        raise AccountingError("تعداد باید مثبت و بهای واحد منفی نباشد")
    ensure_not_future(at, "تاریخ موجودی اول دوره")
    m = StockMove(product_id=product.id, at=at or local_now(), kind="opening", qty=qty, cost=qty * unit_cost, note="موجودی اول دوره")
    db.add(m)
    db.flush()
    if m.cost:
        post(db, f"موجودی اول دوره - {product.name}", [Leg(_acc(db, INVENTORY), debit=m.cost), Leg(account(db, OPENING), credit=m.cost)],
             "stock_opening", m.id, at=m.at)
    recost(db, product.id)
    audit(db, "product.opening", "product", product.id, {"qty": qty, "unit_cost": unit_cost}, user=user)
    return m


def count_stock(db: Session, product: Product, counted: int, at: datetime | None = None, note: str = "", user=None) -> StockMove | None:  # noqa: ANN001
    """Physical count: the difference with the system becomes a shortage (cost by the method) or found stock."""
    if counted < 0:
        raise AccountingError("موجودی شمارش‌شده نمی‌تواند منفی باشد")
    ensure_not_future(at, "تاریخ شمارش")
    db.flush()
    current = int(db.scalar(select(func.coalesce(func.sum(StockMove.qty), 0)).where(StockMove.product_id == product.id)) or 0)
    diff = counted - current
    if diff == 0:
        return None
    m = StockMove(product_id=product.id, at=at or local_now(), kind="adjust_in" if diff > 0 else "adjust_out", qty=diff,
                  note=note or f"شمارش انبار: {counted}")
    db.add(m)
    db.flush()
    recost(db, product.id)
    audit(db, "product.count", "product", product.id, {"system": current, "counted": counted}, user=user)
    return m


def sell(db: Session, item: InvoiceItem, at: datetime) -> StockMove:
    m = StockMove(product_id=item.product_id, at=at, kind="sale", qty=-int(item.quantity), invoice_item_id=item.id)
    db.add(m)
    db.flush()
    return m


def return_invoice(db: Session, invoice_id: int, *, same_time: bool) -> list[int]:
    """Goods of a voided invoice come back to stock at the cost they were sold at (at the sale's own time when
    the invoice was a mistake, now when the customer really returned them)."""
    item_ids = select(InvoiceItem.id).where(InvoiceItem.invoice_id == invoice_id)
    products = set()
    for s in list(db.scalars(select(StockMove).where(StockMove.kind == "sale", StockMove.invoice_item_id.in_(item_ids)))):
        db.add(StockMove(product_id=s.product_id, at=s.at if same_time else local_now(), kind="sale_return", qty=-s.qty,
                         invoice_item_id=s.invoice_item_id, ref_move_id=s.id, note="فاکتور باطل شد"))
        products.add(s.product_id)
    db.flush()
    for pid in products:
        recost(db, pid)
    return sorted(products)


# ------------------------------------------------------------------ purchases & suppliers
def next_purchase_number(db: Session) -> str:
    n = (db.scalar(select(func.max(Purchase.id))) or 0) + 1
    return f"PO-{n:06d}"


def _share(amount: int, weights: list[int]) -> list[int]:
    """Split amount over weights proportionally; integer parts adding up exactly (largest remainder)."""
    total = sum(weights)
    if amount <= 0 or total <= 0:
        return [0] * len(weights)
    raw = [w * amount / total for w in weights]
    parts = [int(x) for x in raw]
    order = sorted(range(len(weights)), key=lambda i: (-(raw[i] - parts[i]), i))
    for i in order[: amount - sum(parts)]:
        parts[i] += 1
    return parts


def record_purchase(db: Session, *, supplier: Supplier | None, items: list[dict], at: datetime | None = None, discount: int = 0,
                    shipping: int = 0, supplier_ref: str = "", notes: str = "", payments: list[dict] | None = None,
                    user=None) -> Purchase:  # noqa: ANN001
    """items: [{product_id, quantity, unit_price}]. The goods' cost = price - share of the discount + share of shipping."""
    if not items:
        raise AccountingError("خرید بدون کالا قابل ثبت نیست")
    ensure_not_future(at, "تاریخ خرید")
    rows = []
    for it in items:
        product = db.get(Product, int(it["product_id"]))
        qty, price = int(it.get("quantity") or 0), int(it.get("unit_price") or 0)
        if product is None or qty <= 0 or price < 0:
            raise AccountingError("کالا، تعداد یا قیمت خرید نامعتبر است")
        rows.append((product, qty, price))
    gross = [q * p for _, q, p in rows]
    subtotal = sum(gross)
    if discount < 0 or shipping < 0 or discount > subtotal:
        raise AccountingError("تخفیف یا هزینهٔ حمل نامعتبر است")
    disc, ship = _share(discount, gross), _share(shipping, gross if subtotal else [q for _, q, _ in rows])
    pur = Purchase(number=next_purchase_number(db), supplier_id=supplier.id if supplier else None, supplier_ref=supplier_ref,
                   at=at or local_now(), subtotal=subtotal, discount=discount, shipping=shipping,
                   total=subtotal - discount + shipping, notes=notes)
    for (product, qty, price), g, d, sh in zip(rows, gross, disc, ship):
        pur.items.append(PurchaseItem(product_id=product.id, quantity=qty, unit_price=price, cost=g - d + sh))
    db.add(pur)
    db.flush()
    for it in pur.items:
        db.add(StockMove(product_id=it.product_id, at=pur.at, kind="purchase", qty=it.quantity, cost=it.cost,
                         purchase_item_id=it.id, note=pur.number))
    name = supplier.name if supplier else "تأمین‌کننده"
    if pur.total:
        post(db, f"خرید {pur.number} از {name}", [Leg(_acc(db, INVENTORY), debit=pur.total), Leg(_acc(db, PAYABLE), credit=pur.total)],
             "purchase", pur.id, at=pur.at)
    db.flush()
    for pid in {it.product_id for it in pur.items}:
        recost(db, pid)
    for p in payments or []:
        if int(p.get("amount") or 0) > 0:
            pay_supplier(db, pur, db.get(PaymentAccount, int(p["payment_account_id"])), int(p["amount"]), paid_at=pur.at, user=user)
    _status(pur)
    audit(db, "purchase.create", "purchase", pur.id, {"number": pur.number, "total": pur.total}, user=user)
    return pur


def _status(pur: Purchase) -> None:
    if pur.status != "void":
        pur.status = "paid" if pur.paid >= pur.total else ("partial" if pur.paid > 0 else "open")


def pay_supplier(db: Session, pur: Purchase, pa: PaymentAccount | None, amount: int, paid_at: datetime | None = None,
                 notes: str = "", user=None) -> SupplierPayment:  # noqa: ANN001
    if pa is None:
        raise AccountingError("حساب پرداخت مشخص نیست")
    if amount <= 0 or pur.paid + amount > pur.total:
        raise AccountingError("مبلغ پرداخت باید مثبت و حداکثر برابر ماندهٔ خرید باشد")
    if pur.status == "void":
        raise AccountingError("این خرید باطل شده است")
    ensure_not_future(paid_at, "تاریخ پرداخت")
    sp = SupplierPayment(supplier_id=pur.supplier_id, purchase_id=pur.id, payment_account_id=pa.id, amount=amount,
                         paid_at=paid_at or local_now(), notes=notes)
    db.add(sp)
    db.flush()
    post(db, f"پرداخت بابت خرید {pur.number}", [Leg(_acc(db, PAYABLE), debit=amount), Leg(cash_account_for(db, pa), credit=amount)],
         "supplier_payment", sp.id, at=sp.paid_at)
    pur.paid += amount
    _status(pur)
    audit(db, "purchase.pay", "purchase", pur.id, {"amount": amount, "account": pa.id}, user=user)
    return sp


def void_purchase(db: Session, pur: Purchase, reason: str = "", user=None) -> Purchase:  # noqa: ANN001
    """A purchase entered by mistake: its goods leave the stock history (sales that used them are re-costed),
    the debt to the supplier and the payments made for it are reversed."""
    if pur.status == "void":
        return pur
    for e in db.scalars(select(JournalEntry).where(JournalEntry.ref_type == "purchase", JournalEntry.ref_id == pur.id)):
        post(db, f"ابطال خرید {pur.number}", [Leg(db.get(LedgerAccount, ln.account_id), debit=ln.credit, credit=ln.debit) for ln in e.lines],
             "purchase_void", pur.id)
    for sp in list(db.scalars(select(SupplierPayment).where(SupplierPayment.purchase_id == pur.id, SupplierPayment.amount > 0))):
        pa = db.get(PaymentAccount, sp.payment_account_id)
        back = SupplierPayment(supplier_id=sp.supplier_id, purchase_id=pur.id, payment_account_id=sp.payment_account_id,
                               amount=-sp.amount, paid_at=sp.paid_at, notes="ابطال خرید")
        db.add(back)
        db.flush()
        post(db, f"برگشت پرداخت خرید باطل‌شده {pur.number}", [Leg(cash_account_for(db, pa), debit=sp.amount), Leg(_acc(db, PAYABLE), credit=sp.amount)],
             "supplier_payment", back.id, at=sp.paid_at)
    products = set()
    for it in pur.items:
        for m in db.scalars(select(StockMove).where(StockMove.purchase_item_id == it.id)):
            products.add(m.product_id)
            db.delete(m)
    db.flush()
    pur.paid = 0
    pur.status = "void"
    for pid in products:
        recost(db, pid)
    audit(db, "purchase.void", "purchase", pur.id, {"reason": reason}, user=user)
    return pur
