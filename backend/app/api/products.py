"""Products sold in the clinic and on the website: catalog, stock, purchases from suppliers and product profit."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Integer, func, or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..models import (
    Customer,
    Invoice,
    InvoiceItem,
    PaymentAccount,
    Product,
    Purchase,
    StockMove,
    Supplier,
    SupplierPayment,
    TradeHistory,
)
from ..services import inventory, settings_store
from ..services.accounting import AccountingError
from ..services.audit import audit
from ..services.jalali import gregorian_to_jalali
from ..services.search import fa_like
from ..services.textutil import normalize_mobile, to_en_digits
from .deps import require

router = APIRouter(prefix="/api", tags=["products"])


def _404(what: str = "مورد"):  # noqa: ANN202
    raise HTTPException(404, f"{what} پیدا نشد")


def _err(exc: Exception):  # noqa: ANN202
    raise HTTPException(400, str(exc)) from exc


class ProductIn(BaseModel):
    code: str | None = None  # empty = next number
    sku: str | None = None
    name: str
    brand: str = ""
    category: str = ""
    unit: str = "عدد"
    sale_price: int = 0
    online_price: int | None = None
    reorder_level: int = 0
    notes: str = ""
    is_active: bool = True


def _p(p: Product) -> dict:
    margin = (p.sale_price - (p.unit_cost or 0)) if p.unit_cost is not None else None
    return {"id": p.id, "code": p.code, "sku": p.sku, "name": p.name, "brand": p.brand, "category": p.category, "unit": p.unit,
            "sale_price": p.sale_price, "online_price": p.online_price, "reorder_level": p.reorder_level, "notes": p.notes,
            "is_active": p.is_active, "stock_qty": p.stock_qty, "stock_value": p.stock_value, "unit_cost": p.unit_cost,
            "last_purchase_cost": p.last_purchase_cost, "margin": margin,
            "margin_pct": round(margin / p.sale_price * 100, 1) if margin is not None and p.sale_price else None,
            "low": p.is_active and p.stock_qty <= p.reorder_level}


def _check(db: Session, body: ProductIn, pid: int | None = None) -> tuple[str, str | None]:
    code = to_en_digits(body.code or "").strip() or None
    if code is not None:
        other = db.scalar(select(Product).where(Product.code == code, Product.id != (pid or 0)))
        if other:
            raise HTTPException(409, f"کد {code} متعلق به محصول «{other.name}» است")
    sku = (body.sku or "").strip() or None
    if sku:
        other = db.scalar(select(Product).where(func.lower(Product.sku) == sku.lower(), Product.id != (pid or 0)))
        if other:
            raise HTTPException(409, f"SKU «{sku}» متعلق به محصول «{other.name}» است")
    if not body.name.strip():
        raise HTTPException(400, "نام محصول را وارد کنید")
    if body.sale_price < 0 or (body.online_price or 0) < 0:
        raise HTTPException(400, "قیمت نمی‌تواند منفی باشد")
    return code or inventory.next_product_code(db), sku


# ------------------------------------------------------------------ catalog
@router.get("/products")
def products(q: str = "", all: bool = False, low: bool = False, db: Session = Depends(get_db), _=Depends(require("read"))):  # noqa: A002
    stmt = select(Product).order_by(func.cast(Product.code, Integer), Product.id)
    if not all:
        stmt = stmt.where(Product.is_active.is_(True))
    if q.strip():
        code = to_en_digits(q).strip()
        stmt = stmt.where(or_(fa_like(Product.name, q), Product.code == code, func.lower(Product.sku).like(f"%{code.lower()}%"),
                              fa_like(Product.brand, q), fa_like(Product.category, q)))
    rows = [_p(p) for p in db.scalars(stmt.limit(2000))]
    return [r for r in rows if r["low"]] if low else rows


@router.get("/products/summary")
def products_summary(db: Session = Depends(get_db), _=Depends(require("read"))):
    """Inventory value, low stock, what is owed to suppliers - for the products page header."""
    act = select(Product).where(Product.is_active.is_(True))
    items = list(db.scalars(act))
    owed = db.scalar(select(func.coalesce(func.sum(Purchase.total - Purchase.paid), 0)).where(Purchase.status.in_(("open", "partial")))) or 0
    return {"count": len(items), "stock_value": sum(p.stock_value for p in items), "units": sum(max(0, p.stock_qty) for p in items),
            "low": sum(1 for p in items if p.stock_qty <= p.reorder_level), "negative": sum(1 for p in items if p.stock_qty < 0),
            "owed_to_suppliers": int(owed), "method": inventory.method(db), "method_label": inventory.METHODS[inventory.method(db)]}


@router.post("/products")
def create_product(body: ProductIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    code, sku = _check(db, body)
    p = Product(**{**body.model_dump(exclude={"code", "sku"}), "name": body.name.strip(), "code": code, "sku": sku})
    db.add(p)
    db.flush()
    audit(db, "product.create", "product", p.id, body.model_dump(), user=user)
    db.commit()
    return _p(p)


@router.put("/products/{pid}")
def update_product(pid: int, body: ProductIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    p = db.get(Product, pid) or _404("محصول")
    code, sku = _check(db, body, pid)
    for k, v in body.model_dump(exclude={"code", "sku"}).items():
        setattr(p, k, v)
    p.name, p.code, p.sku = body.name.strip(), code, sku
    audit(db, "product.update", "product", p.id, body.model_dump(), user=user)
    db.commit()
    return _p(p)


@router.delete("/products/{pid}")
def delete_product(pid: int, db: Session = Depends(get_db), user=Depends(require("finance"))):
    """Never-used products are deleted; products with history are archived (hidden, kept in the books)."""
    p = db.get(Product, pid) or _404("محصول")
    used = db.scalar(select(func.count(StockMove.id)).where(StockMove.product_id == pid)) or \
        db.scalar(select(func.count(InvoiceItem.id)).where(InvoiceItem.product_id == pid))
    if used:
        p.is_active = False
    else:
        db.delete(p)
    audit(db, "product.delete", "product", pid, {"archived": bool(used)}, user=user)
    db.commit()
    return {"ok": True, "archived": bool(used)}


@router.get("/products/{pid}")
def product_detail(pid: int, db: Session = Depends(get_db), _=Depends(require("read"))):
    """The product with its stock card (kardex): every move with the running quantity and value."""
    p = db.get(Product, pid) or _404("محصول")
    moves = list(db.scalars(select(StockMove).where(StockMove.product_id == pid).order_by(StockMove.at, StockMove.id)))
    inv_of = {}
    item_ids = [m.invoice_item_id for m in moves if m.invoice_item_id]
    if item_ids:
        for iid, num, inv_id, status in db.execute(select(InvoiceItem.id, Invoice.number, Invoice.id, Invoice.status)
                                                   .join(Invoice, Invoice.id == InvoiceItem.invoice_id).where(InvoiceItem.id.in_(item_ids))):
            inv_of[iid] = (num, inv_id, status)
    qty = value = 0
    card = []
    for m in moves:
        qty += m.qty
        value += m.cost if m.qty > 0 else -m.cost
        ref = inv_of.get(m.invoice_item_id)
        card.append({"id": m.id, "at": m.at.isoformat(timespec="minutes"), "kind": m.kind, "label": inventory.MOVE_LABELS.get(m.kind, m.kind),
                     "qty": m.qty, "cost": m.cost, "unit_cost": m.cost // abs(m.qty) if m.qty else 0, "estimated": m.estimated,
                     "balance_qty": qty, "balance_value": value, "note": m.note,
                     "invoice": ref[0] if ref else None, "invoice_id": ref[1] if ref else None})
    hist = []
    names: dict[int, str] = {}
    for h in db.scalars(select(TradeHistory).where(TradeHistory.product_id == pid).order_by(TradeHistory.at.desc(), TradeHistory.id.desc()).limit(300)):
        if h.customer_id and h.customer_id not in names:
            c = db.get(Customer, h.customer_id)
            names[h.customer_id] = c.full_name if c else ""
        hist.append({"at": h.at.isoformat(timespec="minutes"), "kind": h.kind, "source": h.source, "doc_no": h.doc_no, "party": h.party,
                     "customer_id": h.customer_id, "customer": names.get(h.customer_id) if h.customer_id else None,
                     "qty": h.qty, "unit_price": h.unit_price, "amount": h.amount, "cost": h.cost})
    return {**_p(p), "moves": card[::-1], "history": hist}


class OpeningIn(BaseModel):
    qty: int
    unit_cost: int
    at: datetime | None = None


@router.post("/products/{pid}/opening")
def product_opening(pid: int, body: OpeningIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    p = db.get(Product, pid) or _404("محصول")
    try:
        inventory.opening_stock(db, p, body.qty, body.unit_cost, body.at, user=user)
    except AccountingError as exc:
        db.rollback()
        _err(exc)
    db.commit()
    return _p(p)


class CountIn(BaseModel):
    counted: int
    at: datetime | None = None
    note: str = ""


@router.post("/products/{pid}/count")
def product_count(pid: int, body: CountIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    p = db.get(Product, pid) or _404("محصول")
    try:
        m = inventory.count_stock(db, p, body.counted, body.at, body.note, user=user)
    except AccountingError as exc:
        db.rollback()
        _err(exc)
    db.commit()
    return {**_p(p), "difference": m.qty if m else 0, "difference_cost": m.cost if m else 0}


# ------------------------------------------------------------------ settings
@router.get("/products-settings")
def product_settings(db: Session = Depends(get_db), _=Depends(require("read"))):
    return {"method": inventory.method(db), "methods": inventory.METHODS,
            "allow_negative": bool(settings_store.get(db, "products.allow_negative", True))}


class ProductSettingsIn(BaseModel):
    method: str
    allow_negative: bool = True


@router.put("/products-settings")
def save_product_settings(body: ProductSettingsIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    try:
        n = inventory.set_method(db, body.method, user=user)
    except AccountingError as exc:
        db.rollback()
        _err(exc)
    settings_store.set_value(db, "products.allow_negative", body.allow_negative)
    db.commit()
    return {**product_settings(db), "recosted": n}


# ------------------------------------------------------------------ suppliers & purchases
class SupplierIn(BaseModel):
    name: str
    mobile: str | None = None
    notes: str = ""


@router.get("/suppliers")
def suppliers(db: Session = Depends(get_db), _=Depends(require("read"))):
    owed = dict(db.execute(select(Purchase.supplier_id, func.sum(Purchase.total - Purchase.paid))
                           .where(Purchase.status.in_(("open", "partial"))).group_by(Purchase.supplier_id)).all())
    bought = dict(db.execute(select(Purchase.supplier_id, func.sum(Purchase.total)).where(Purchase.status != "void")
                             .group_by(Purchase.supplier_id)).all())
    return [{"id": s.id, "name": s.name, "mobile": s.mobile, "notes": s.notes, "owed": int(owed.get(s.id) or 0),
             "bought": int(bought.get(s.id) or 0)} for s in db.scalars(select(Supplier).order_by(Supplier.name))]


@router.post("/suppliers")
def create_supplier(body: SupplierIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    if not body.name.strip():
        raise HTTPException(400, "نام تأمین‌کننده را وارد کنید")
    s = Supplier(name=body.name.strip(), mobile=normalize_mobile(body.mobile) or body.mobile, notes=body.notes)
    db.add(s)
    db.flush()
    audit(db, "supplier.create", "supplier", s.id, body.model_dump(), user=user)
    db.commit()
    return {"id": s.id, "name": s.name, "mobile": s.mobile, "notes": s.notes, "owed": 0, "bought": 0}


class PurchaseItemIn(BaseModel):
    product_id: int
    quantity: int
    unit_price: int


class PurchasePayIn(BaseModel):
    payment_account_id: int
    amount: int


class PurchaseIn(BaseModel):
    supplier_id: int | None = None
    supplier_name: str | None = None  # a new supplier on the fly
    supplier_ref: str = ""
    at: datetime | None = None
    items: list[PurchaseItemIn]
    discount: int = 0
    shipping: int = 0
    notes: str = ""
    payments: list[PurchasePayIn] = []


def _pur(p: Purchase, db: Session, full: bool = False) -> dict:
    sup = db.get(Supplier, p.supplier_id) if p.supplier_id else None
    out = {"id": p.id, "number": p.number, "supplier_id": p.supplier_id, "supplier": sup.name if sup else None, "supplier_ref": p.supplier_ref,
           "at": p.at.isoformat(timespec="minutes"), "subtotal": p.subtotal, "discount": p.discount, "shipping": p.shipping,
           "total": p.total, "paid": p.paid, "due": p.total - p.paid, "status": p.status, "notes": p.notes, "items_count": len(p.items)}
    if full:
        names = {x.id: x for x in db.scalars(select(Product).where(Product.id.in_([i.product_id for i in p.items])))}
        accounts = {a.id: a.name for a in db.scalars(select(PaymentAccount))}
        out["items"] = [{"product_id": i.product_id, "product": names[i.product_id].name if i.product_id in names else "?",
                         "code": names[i.product_id].code if i.product_id in names else None, "quantity": i.quantity,
                         "unit_price": i.unit_price, "cost": i.cost, "unit_cost": i.cost // i.quantity if i.quantity else 0} for i in p.items]
        out["payments"] = [{"id": s.id, "amount": s.amount, "account": accounts.get(s.payment_account_id), "paid_at": s.paid_at.isoformat(timespec="minutes")}
                           for s in db.scalars(select(SupplierPayment).where(SupplierPayment.purchase_id == p.id).order_by(SupplierPayment.id))]
    return out


@router.get("/purchases")
def purchases(status: str = "", supplier_id: int | None = None, start: date | None = None, end: date | None = None,
              db: Session = Depends(get_db), _=Depends(require("read"))):
    q = select(Purchase).order_by(Purchase.at.desc(), Purchase.id.desc()).limit(500)
    if status == "unpaid":
        q = q.where(Purchase.status.in_(("open", "partial")))
    elif status:
        q = q.where(Purchase.status == status)
    if supplier_id:
        q = q.where(Purchase.supplier_id == supplier_id)
    if start:
        q = q.where(Purchase.at >= datetime.combine(start, datetime.min.time()))
    if end:
        q = q.where(Purchase.at <= datetime.combine(end, datetime.max.time()))
    return [_pur(p, db) for p in db.scalars(q)]


@router.post("/purchases")
def create_purchase(body: PurchaseIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    sup = db.get(Supplier, body.supplier_id) if body.supplier_id else None
    if sup is None and (body.supplier_name or "").strip():
        sup = db.scalar(select(Supplier).where(Supplier.name == body.supplier_name.strip())) or Supplier(name=body.supplier_name.strip())
        db.add(sup)
        db.flush()
    try:
        pur = inventory.record_purchase(db, supplier=sup, items=[i.model_dump() for i in body.items], at=body.at, discount=body.discount,
                                        shipping=body.shipping, supplier_ref=body.supplier_ref, notes=body.notes,
                                        payments=[p.model_dump() for p in body.payments], user=user)
    except AccountingError as exc:
        db.rollback()
        _err(exc)
    db.commit()
    return _pur(pur, db, full=True)


@router.get("/purchases/{pid}")
def purchase_detail(pid: int, db: Session = Depends(get_db), _=Depends(require("read"))):
    return _pur(db.get(Purchase, pid) or _404("خرید"), db, full=True)


class PayIn(BaseModel):
    payment_account_id: int
    amount: int
    paid_at: datetime | None = None


@router.post("/purchases/{pid}/pay")
def pay_purchase(pid: int, body: PayIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
    pur = db.get(Purchase, pid) or _404("خرید")
    try:
        inventory.pay_supplier(db, pur, db.get(PaymentAccount, body.payment_account_id), body.amount, body.paid_at, user=user)
    except AccountingError as exc:
        db.rollback()
        _err(exc)
    db.commit()
    return _pur(pur, db, full=True)


@router.post("/purchases/{pid}/void")
def void_purchase(pid: int, reason: str = "", db: Session = Depends(get_db), user=Depends(require("void"))):
    pur = db.get(Purchase, pid) or _404("خرید")
    inventory.void_purchase(db, pur, reason, user=user)
    db.commit()
    return _pur(pur, db, full=True)


# ------------------------------------------------------------------ profit report
def _jm(d: datetime) -> str:
    y, m, _ = gregorian_to_jalali(d.year, d.month, d.day)
    return f"{y:04d}-{m:02d}"


def product_profit(db: Session, start: date | None = None, end: date | None = None, channel: str = "") -> dict:
    """Revenue (after discounts), cost of goods sold and gross profit per product, per month and per sales channel."""
    s = datetime.combine(start, datetime.min.time()) if start else None
    e = datetime.combine(end, datetime.max.time()) if end else None
    q = (select(InvoiceItem, Invoice.issued_at, Invoice.channel).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
         .where(InvoiceItem.product_id.is_not(None), Invoice.status != "void"))
    if s:
        q = q.where(Invoice.issued_at >= s)
    if e:
        q = q.where(Invoice.issued_at <= e)
    if channel in ("online", "in_person"):
        q = q.where(Invoice.channel == "online") if channel == "online" else q.where(or_(Invoice.channel.is_(None), Invoice.channel == "in_person"))
    rows = db.execute(q).all() if channel != "history" else []
    cogs: dict[int, int] = defaultdict(int)
    estimated: set[int] = set()
    ids = [it.id for it, _, _ in rows]
    for i in range(0, len(ids), 900):
        for m in db.scalars(select(StockMove).where(StockMove.invoice_item_id.in_(ids[i:i + 900]), StockMove.kind.in_(("sale", "sale_return")))):
            cogs[m.invoice_item_id] += m.cost if m.kind == "sale" else -m.cost
            if m.estimated:
                estimated.add(m.invoice_item_id)
    products = {p.id: p for p in db.scalars(select(Product))}
    per: dict[int, dict] = {}
    months: dict[str, dict] = {}
    chans: dict[str, dict] = {}
    tot = {"qty": 0, "revenue": 0, "cogs": 0, "profit": 0, "estimated": 0}
    for it, at, ch in rows:
        rev = it.net_amount if it.net_amount is not None else it.amount
        c = cogs[it.id]
        p = products.get(it.product_id)
        r = per.setdefault(it.product_id, {"id": it.product_id, "code": p.code if p else None, "sku": p.sku if p else None,
                                           "name": p.name if p else it.description, "qty": 0, "revenue": 0, "cogs": 0, "profit": 0,
                                           "online": 0, "estimated": False})
        for bucket in (r, tot, months.setdefault(_jm(at), {"key": _jm(at), "qty": 0, "revenue": 0, "cogs": 0, "profit": 0}),
                       chans.setdefault(ch or "in_person", {"channel": ch or "in_person", "qty": 0, "revenue": 0, "cogs": 0, "profit": 0})):
            bucket["qty"] += it.quantity
            bucket["revenue"] += rev
            bucket["cogs"] += c
            bucket["profit"] += rev - c
        if ch == "online":
            r["online"] += it.quantity
        if it.id in estimated:
            r["estimated"] = True
            tot["estimated"] += 1

    # sales brought over from the product accounting software (Tizpardaz) or from Chehreh: history with its own cost
    expenses = 0
    if channel in ("", "history"):
        hq = select(TradeHistory).where(TradeHistory.kind.in_(("sale", "sale_return", "expense")))
        if s:
            hq = hq.where(TradeHistory.at >= s)
        if e:
            hq = hq.where(TradeHistory.at <= e)
        for h in db.scalars(hq):
            if h.kind == "expense":
                expenses += h.amount
                continue
            sign = -1 if h.kind == "sale_return" else 1
            p = products.get(h.product_id) if h.product_id else None
            r = per.setdefault(h.product_id or 0, {"id": h.product_id, "code": p.code if p else None, "sku": p.sku if p else None,
                                                   "name": p.name if p else "کالای نامشخص (سوابق)", "qty": 0, "revenue": 0, "cogs": 0,
                                                   "profit": 0, "online": 0, "estimated": False})
            if h.product_id is None:  # which product it was is not known, so neither is its cost
                r["unknown_cost"] = True
                tot["unknown_cost"] = tot.get("unknown_cost", 0) + sign * h.amount
            r["history"] = r.get("history", 0) + sign * h.qty
            for bucket in (r, tot, months.setdefault(_jm(h.at), {"key": _jm(h.at), "qty": 0, "revenue": 0, "cogs": 0, "profit": 0}),
                           chans.setdefault("history", {"channel": "history", "qty": 0, "revenue": 0, "cogs": 0, "profit": 0})):
                bucket["qty"] += sign * h.qty
                bucket["revenue"] += sign * h.amount
                bucket["cogs"] += sign * (h.cost or 0)
                bucket["profit"] += sign * (h.amount - (h.cost or 0))
    tot["expenses"] = expenses
    tot["net"] = tot["profit"] - expenses

    def margin(d: dict) -> dict:
        return {**d, "margin": round(d["profit"] / d["revenue"] * 100, 1) if d["revenue"] else None}
    return {"method": inventory.method(db), "method_label": inventory.METHODS[inventory.method(db)], "totals": margin(tot),
            "products": sorted((margin(x) for x in per.values()), key=lambda x: -x["profit"]),
            "months": [margin(months[k]) for k in sorted(months)], "channels": [margin(x) for x in chans.values()]}


@router.get("/reports/products")
def products_report(start: date | None = None, end: date | None = None, channel: str = "", db: Session = Depends(get_db),
                    _=Depends(require("reports"))):
    return product_profit(db, start, end, channel)
