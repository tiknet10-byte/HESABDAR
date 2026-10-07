"""Which POS terminal / card receives the money of each service line, of deposits and of product sales.

Every line (and deposits, and products) is linked to at least one POS terminal and one card (or bank account /
cash box); the first of each list is the default. The invoice form uses it to split what the customer pays per
line onto that line's own POS automatically, so registering a sale needs no account choice in the usual case.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PaymentAccount, ServiceLine
from . import settings_store

KEY = "payments.routing"
POS_KINDS = {"pos"}
CARD_KINDS = {"card", "bank", "cash", "gateway"}
GROUPS = {"deposits": "بیعانه‌ها", "products": "فروش محصولات"}


def _clean(entry: dict | None, accounts: dict[int, PaymentAccount]) -> dict:
    entry = entry or {}
    pos = [int(i) for i in entry.get("pos", []) if int(i) in accounts and accounts[int(i)].kind in POS_KINDS]
    card = [int(i) for i in entry.get("card", []) if int(i) in accounts and accounts[int(i)].kind in CARD_KINDS]
    return {"pos": list(dict.fromkeys(pos)), "card": list(dict.fromkeys(card))}


def get_routing(db: Session) -> dict:
    """{lines: {line_id: {pos, card}}, deposits: {pos, card}, products: {pos, card}} with only active accounts."""
    raw = settings_store.get(db, KEY) or {}
    accounts = {a.id: a for a in db.scalars(select(PaymentAccount).where(PaymentAccount.is_active.is_(True)))}
    return {"lines": {int(k): _clean(v, accounts) for k, v in (raw.get("lines") or {}).items()},
            **{g: _clean(raw.get(g), accounts) for g in GROUPS}}


def problems(db: Session, routing: dict | None = None) -> list[str]:
    """What is still missing: every active line, deposits and products need >= 1 POS and >= 1 card."""
    r = routing or get_routing(db)
    out = []
    targets = [(f"لاین «{l.name}»", r["lines"].get(l.id)) for l in db.scalars(select(ServiceLine).where(ServiceLine.is_active.is_(True))
                                                                           .order_by(ServiceLine.id))]
    targets += [(label, r.get(g)) for g, label in GROUPS.items()]
    for label, e in targets:
        miss = [x for x, k in (("کارتخوان", "pos"), ("کارت", "card")) if not (e or {}).get(k)]
        if miss:
            out.append(f"{label}: {' و '.join(miss)} تعیین نشده")
    return out


def save_routing(db: Session, data: dict) -> dict:
    accounts = {a.id: a for a in db.scalars(select(PaymentAccount).where(PaymentAccount.is_active.is_(True)))}
    clean = {"lines": {str(int(k)): _clean(v, accounts) for k, v in (data.get("lines") or {}).items()},
             **{g: _clean(data.get(g), accounts) for g in GROUPS}}
    settings_store.set_value(db, KEY, clean)
    db.flush()  # a new setting row is only visible to the next read after a flush
    return get_routing(db)


def default_account(db: Session, group: str | int | None, prefer: str = "pos") -> int | None:
    """Default receiving account of a line id, 'deposits' or 'products' (POS first unless prefer='card')."""
    r = get_routing(db)
    e = r["lines"].get(int(group)) if isinstance(group, int) or (isinstance(group, str) and group.isdigit()) else r.get(group or "")
    if not e:
        return None
    order = ("pos", "card") if prefer == "pos" else ("card", "pos")
    return next((e[k][0] for k in order if e.get(k)), None)
