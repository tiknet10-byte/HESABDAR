"""Continuous learning engine (offline, explainable).

What it learns:
  * Prices: running statistics of the real price of each service from invoices.
  * Vocabulary: which words customers/staff use for which service (from labelled
    chats, invoice descriptions and corrections) -> a naive-Bayes style classifier.
  * Deposit patterns: typical deposit amount per service.

Everything is stored in the database (KnowledgeItem, Service.learned_*), so the
knowledge survives restarts, is included in backups and can be exported to an AI model.
Every user correction calls `learn_text` again, so the system keeps improving.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ConversationMessage, Deposit, Invoice, InvoiceItem, KnowledgeItem, Service
from .textutil import normalize_text, tokens


# ------------------------------------------------------------------ vocabulary
def learn_text(db: Session, text: str, service_id: int, weight: int = 1) -> None:
    for tok in set(tokens(text)):
        item = db.scalar(select(KnowledgeItem).where(KnowledgeItem.kind == "token_service", KnowledgeItem.key == tok))
        if item is None:
            item = KnowledgeItem(kind="token_service", key=tok, value={})
            db.add(item)
        counts = dict(item.value or {})
        counts[str(service_id)] = counts.get(str(service_id), 0) + weight
        item.value = counts
    db.flush()


def add_alias(db: Session, service: Service, alias: str) -> None:
    alias = normalize_text(alias)
    if alias and alias not in [normalize_text(a) for a in service.aliases or []] and alias != normalize_text(service.name):
        service.aliases = [*(service.aliases or []), alias]
    learn_text(db, alias, service.id, weight=3)


def classify_text(db: Session, text: str, top: int = 3) -> list[dict]:
    """Rank services by how well the text matches them."""
    norm = normalize_text(text)
    toks = tokens(text)
    services = list(db.scalars(select(Service).where(Service.is_active.is_(True))))
    if not services or not norm:
        return []
    scores: dict[int, float] = defaultdict(float)
    reasons: dict[int, list[str]] = defaultdict(list)

    # 1) direct name / alias hits (strong signal)
    for s in services:
        for name in [s.name, *(s.aliases or [])]:
            n = normalize_text(name)
            if n and n in norm:
                scores[s.id] += 3.0 + len(n) / 10
                reasons[s.id].append(f"نام «{name}» در متن")
                break

    # 2) learned vocabulary (log-likelihood with add-one smoothing)
    if toks:
        items = {k.key: k.value for k in db.scalars(select(KnowledgeItem).where(
            KnowledgeItem.kind == "token_service", KnowledgeItem.key.in_(toks)))}
        totals: Counter = Counter()
        for v in items.values():
            for sid, c in v.items():
                totals[int(sid)] += c
        for tok, v in items.items():
            tok_total = sum(v.values())
            for sid, c in v.items():
                sid = int(sid)
                scores[sid] += math.log((c + 1) / (tok_total + len(services))) + math.log(len(services))
                reasons[sid].append(f"واژه آموخته‌شده «{tok}»")

    ranked = sorted(((sid, sc) for sid, sc in scores.items() if sc > 0), key=lambda x: -x[1])[:top]
    by_id = {s.id: s for s in services}
    total = sum(sc for _, sc in ranked) or 1
    return [{"service_id": sid, "service": by_id[sid].name, "score": round(sc / total, 3), "reasons": reasons[sid][:3]}
            for sid, sc in ranked if sid in by_id]


# ------------------------------------------------------------------ prices
def refresh_price_stats(db: Session) -> int:
    """Recompute learned average price per service from (non-void) invoices; recent data weighs more."""
    rows = db.execute(
        select(InvoiceItem.service_id, InvoiceItem.unit_price, Invoice.issued_at)
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .where(InvoiceItem.service_id.is_not(None), Invoice.status != "void")
        .order_by(Invoice.issued_at.desc())
    ).all()
    per: dict[int, list[int]] = defaultdict(list)
    for sid, price, _ in rows:
        if len(per[sid]) < 200:
            per[sid].append(int(price))
    updated = 0
    for sid, prices in per.items():
        svc = db.get(Service, sid)
        if not svc:
            continue
        recent = prices[:30]
        svc.learned_avg_price = int(statistics.median(recent))
        svc.learned_count = len(prices)
        updated += 1
    db.flush()
    return updated


def price_check(service: Service, price: int) -> str | None:
    """Return a warning text if the price looks wrong for this service."""
    if service.min_price and price < service.min_price:
        return f"قیمت {price:,} کمتر از حداقل تعریف‌شده برای «{service.name}» است"
    if service.max_price and price > service.max_price:
        return f"قیمت {price:,} بیشتر از حداکثر تعریف‌شده برای «{service.name}» است"
    ref = service.learned_avg_price or service.base_price
    if ref and service.learned_count >= 3 and not (0.5 * ref <= price <= 1.8 * ref):
        return f"قیمت {price:,} با میانگین آموخته‌شده ({ref:,}) برای «{service.name}» فاصله زیادی دارد"
    return None


# ------------------------------------------------------------------ deposits
def guess_service_for_deposit(db: Session, amount: int, customer_id: int | None = None, text: str = "") -> list[dict]:
    """Guess what a deposit is for, combining text, learned deposit sizes and customer history."""
    scores: dict[int, float] = defaultdict(float)
    reasons: dict[int, list[str]] = defaultdict(list)
    services = {s.id: s for s in db.scalars(select(Service).where(Service.is_active.is_(True)))}

    for r in classify_text(db, text, top=5) if text else []:
        scores[r["service_id"]] += 2.0 * r["score"]
        reasons[r["service_id"]] += r["reasons"]

    # learned typical deposit per service
    dep_rows = db.execute(select(Deposit.service_id, Deposit.amount).where(Deposit.service_id.is_not(None))).all()
    typical: dict[int, list[int]] = defaultdict(list)
    for sid, amt in dep_rows:
        typical[sid].append(int(amt))
    for sid, s in services.items():
        candidates = typical.get(sid) or ([s.default_deposit] if s.default_deposit else [])
        if candidates:
            med = statistics.median(candidates)
            if med and abs(amount - med) / med < 0.05:
                scores[sid] += 1.0
                reasons[sid].append("مبلغ با بیعانه معمول این خدمت یکی است")
        if s.base_price and amount <= s.base_price and amount >= 0.2 * (s.learned_avg_price or s.base_price):
            scores[sid] += 0.2

    if customer_id:
        hist = db.execute(
            select(InvoiceItem.service_id).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
            .where(Invoice.customer_id == customer_id, InvoiceItem.service_id.is_not(None))
            .order_by(Invoice.issued_at.desc()).limit(10)
        ).scalars().all()
        for sid, cnt in Counter(hist).items():
            scores[sid] += 0.4 * cnt
            reasons[sid].append("مشتری قبلاً این خدمت را گرفته")

    ranked = sorted(scores.items(), key=lambda x: -x[1])[:3]
    total = sum(v for _, v in ranked) or 1
    return [{"service_id": sid, "service": services[sid].name, "score": round(v / total, 3),
             "reasons": list(dict.fromkeys(reasons[sid]))[:3]} for sid, v in ranked if sid in services and v > 0]


# ------------------------------------------------------------------ full retrain
def retrain(db: Session) -> dict:
    db.query(KnowledgeItem).filter(KnowledgeItem.kind == "token_service").delete()
    db.flush()
    n_msgs = 0
    for m in db.scalars(select(ConversationMessage).where(ConversationMessage.service_id.is_not(None))):
        learn_text(db, m.text, m.service_id)
        n_msgs += 1
    n_items = 0
    for it in db.scalars(select(InvoiceItem).where(InvoiceItem.service_id.is_not(None))):
        if it.description:
            learn_text(db, it.description, it.service_id)
            n_items += 1
    for s in db.scalars(select(Service)):
        learn_text(db, s.name, s.id, weight=3)
        for a in s.aliases or []:
            learn_text(db, a, s.id, weight=3)
    prices = refresh_price_stats(db)
    return {"messages": n_msgs, "invoice_items": n_items, "services_priced": prices}
