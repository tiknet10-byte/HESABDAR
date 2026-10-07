"""Stock changes made here that the website (WooCommerce) must get.

Called by purchases, in-person sales, voids, opening stock and stock counts. The change is queued in the same
transaction as the change itself (if that is rolled back, nothing is sent); the sync job sends it to the website.
Website orders don't queue anything: the website already took them out of its own stock.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Product, WooOutbox, WooProduct
from . import settings_store

CFG_KEY = "woo.config"


def active(db: Session) -> bool:
    cfg = settings_store.get(db, CFG_KEY) or {}
    return bool(cfg.get("enabled") and cfg.get("push_stock", True) and cfg.get("url"))


def _on_site(db: Session, product_id: int) -> bool:
    p = db.get(Product, product_id)
    if p is None:
        return False
    return bool(p.sku) or db.scalar(select(WooProduct.id).where(WooProduct.product_id == product_id).limit(1)) is not None


def add(db: Session, product_id: int, qty: int, reason: str) -> None:
    """The stock here changed by qty (+ in, - out): the website's stock changes by the same amount."""
    if qty and active(db) and _on_site(db, product_id):
        db.add(WooOutbox(product_id=product_id, kind="delta", qty=qty, reason=reason[:128]))


def set_to_stock(db: Session, product_id: int, reason: str) -> None:
    """The real quantity is known (stock count): the website's stock becomes the stock here."""
    if active(db) and _on_site(db, product_id):
        db.add(WooOutbox(product_id=product_id, kind="set", qty=0, reason=reason[:128]))
