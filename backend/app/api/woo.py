"""Website shop (WooCommerce) connection: settings, test, sync now, products comparison, orders, log."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..models import Customer, Invoice, Product, WooLog, WooOrder, WooOutbox, WooProduct
from ..services import woo
from ..services.audit import audit
from .deps import require

router = APIRouter(prefix="/api/woo", tags=["woo"])


def _status(db: Session) -> dict:
    st = woo.state(db)
    pending = db.scalar(select(func.count(WooOutbox.id)).where(WooOutbox.status == "pending")) or 0
    failed = db.scalar(select(func.count(WooOutbox.id)).where(WooOutbox.status == "pending", WooOutbox.attempts > 0)) or 0
    counts = dict(db.execute(select(WooOrder.state, func.count(WooOrder.id)).group_by(WooOrder.state)).all())
    site = db.scalar(select(func.count(WooProduct.id))) or 0
    linked = db.scalar(select(func.count(WooProduct.id)).where(WooProduct.product_id.is_not(None))) or 0
    return {"last_run": st.get("last_run"), "last_ok": st.get("last_ok"), "last_error": st.get("last_error") or "",
            "catalog_at": st.get("catalog_at"), "store": st.get("store") or {}, "methods": st.get("methods") or {},
            "outbox_pending": pending, "outbox_failing": failed, "orders": counts, "site_products": site, "linked": linked}


@router.get("/config")
def get_config(db: Session = Depends(get_db), _=Depends(require("settings"))):
    return {"config": woo.public_config(db), "status": _status(db)}


@router.put("/config")
def put_config(body: dict, db: Session = Depends(get_db), user=Depends(require("settings"))):
    try:
        cfg = woo.save_config(db, body)
    except woo.WooError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    audit(db, "woo.config", "settings", "woo", {k: v for k, v in cfg.items() if k not in ("key", "secret")}, user=user)
    db.commit()
    return {"config": cfg, "status": _status(db)}


@router.post("/test")
def test(db: Session = Depends(get_db), _=Depends(require("settings"))):
    try:
        out = woo.test_connection(db)
    except woo.WooError as exc:
        db.rollback()
        return {"ok": False, "error": str(exc)}
    db.commit()
    return out


@router.post("/sync")
def sync_now(catalog: bool = True, db: Session = Depends(get_db), _=Depends(require("settings"))):
    try:
        out = woo.run_sync(db, force_catalog=catalog)
    except woo.WooError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**out, "status": _status(db)}


@router.get("/status")
def status(db: Session = Depends(get_db), _=Depends(require("read"))):
    return _status(db)


@router.get("/products")
def products(db: Session = Depends(get_db), _=Depends(require("read"))):
    """Website products next to the products here: stock and price, side by side."""
    here = {p.id: p for p in db.scalars(select(Product))}
    rows, linked = [], set()
    for wp in db.scalars(select(WooProduct).order_by(WooProduct.name)):
        p = here.get(wp.product_id) if wp.product_id else None
        if p:
            linked.add(p.id)
        pending = db.scalar(select(func.count(WooOutbox.id)).where(WooOutbox.product_id == p.id, WooOutbox.status == "pending")) if p else 0
        rows.append({"woo_id": wp.id, "parent_id": wp.parent_id, "sku": wp.sku, "site_name": wp.name, "site_status": wp.status,
                     "site_price": wp.price, "site_stock": wp.stock_qty, "manage_stock": wp.manage_stock,
                     "product": {"id": p.id, "code": p.code, "name": p.name, "stock_qty": p.stock_qty, "online_price": p.online_price,
                                 "sale_price": p.sale_price, "last_online_price": p.last_online_price} if p else None,
                     "pending": pending,
                     "stock_differs": bool(p) and (not wp.manage_stock or (wp.stock_qty or 0) != max(0, p.stock_qty))})
    missing = [{"id": p.id, "code": p.code, "name": p.name, "sku": p.sku, "stock_qty": p.stock_qty}
               for p in here.values() if p.sku and p.is_active and p.id not in linked]
    return {"rows": rows, "not_on_site": sorted(missing, key=lambda x: x["code"])}


class LinkIn(BaseModel):
    woo_id: int
    product_id: int | None = None


@router.post("/products/link")
def link(body: LinkIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    """Link a website product to a product here by hand (e.g. its SKU is written differently)."""
    wp = db.get(WooProduct, body.woo_id)
    if wp is None:
        raise HTTPException(404, "محصول سایت پیدا نشد")
    p = db.get(Product, body.product_id) if body.product_id else None
    if body.product_id and p is None:
        raise HTTPException(404, "محصول پیدا نشد")
    wp.product_id = p.id if p else None
    if p and wp.sku and not p.sku:
        p.sku = wp.sku
    audit(db, "woo.link", "product", body.product_id, {"woo_id": wp.id}, user=user)
    db.commit()
    return {"ok": True}


class AlignIn(BaseModel):
    product_ids: list[int] = []
    all: bool = False


@router.post("/stock/align")
def align(body: AlignIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
    """Website stock = stock here, for the chosen products (or all linked ones that differ); sent right away."""
    ids = body.product_ids
    if body.all:
        ids = [r["product"]["id"] for r in products(db)["rows"] if r["product"] and r["stock_differs"]]
    n = woo.align_stock(db, ids)
    audit(db, "woo.align", "woo", None, {"products": ids}, user=user)
    db.commit()
    out = {"queued": n}
    if n:
        try:
            out["sync"] = woo.run_sync(db, force_catalog=False)
        except woo.WooError as exc:
            out["error"] = str(exc)
    return out


@router.get("/orders")
def orders(limit: int = 100, db: Session = Depends(get_db), _=Depends(require("read"))):
    q = (select(WooOrder, Invoice.number, Invoice.status, Customer.full_name, Customer.mobile)
         .outerjoin(Invoice, Invoice.id == WooOrder.invoice_id).outerjoin(Customer, Customer.id == WooOrder.customer_id)
         .order_by(WooOrder.order_id.desc()).limit(min(limit, 500)))
    return [{"order_id": o.order_id, "number": o.number, "status": o.status, "status_fa": woo.STATUS_FA.get(o.status, o.status),
             "state": o.state, "total": o.total, "refunded": o.refunded, "paid": o.paid, "method": o.method,
             "created": o.created.isoformat(timespec="minutes") if o.created else None, "note": o.note,
             "invoice_id": o.invoice_id, "invoice": num, "invoice_status": ist, "customer_id": o.customer_id, "customer": name, "mobile": mob}
            for o, num, ist, name, mob in db.execute(q)]


@router.get("/outbox")
def outbox(limit: int = 100, db: Session = Depends(get_db), _=Depends(require("read"))):
    names = dict(db.execute(select(Product.id, Product.name)).all())
    q = select(WooOutbox).order_by(WooOutbox.id.desc()).limit(min(limit, 500))
    return [{"id": e.id, "product_id": e.product_id, "product": names.get(e.product_id), "kind": e.kind, "qty": e.qty, "reason": e.reason,
             "status": e.status, "attempts": e.attempts, "error": e.error, "result": e.result,
             "created_at": e.created_at.isoformat(timespec="minutes"), "done_at": e.done_at.isoformat(timespec="minutes") if e.done_at else None}
            for e in db.scalars(q)]


@router.post("/outbox/retry")
def retry(db: Session = Depends(get_db), _=Depends(require("settings"))):
    n = 0
    for e in db.scalars(select(WooOutbox).where(WooOutbox.status == "error")):
        e.status, e.attempts = "pending", 0
        n += 1
    db.commit()
    return {"retried": n}


@router.get("/log")
def log(limit: int = 100, db: Session = Depends(get_db), _=Depends(require("read"))):
    q = select(WooLog).order_by(WooLog.id.desc()).limit(min(limit, 500))
    return [{"at": x.at.isoformat(timespec="minutes"), "level": x.level, "message": x.message} for x in db.scalars(q)]
