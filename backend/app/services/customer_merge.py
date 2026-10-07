"""Customers registered twice: same first and last name, different mobile (or none).

Two people can share a name - the mobile tells them apart - so the system never merges on its own: it shows
the candidates and the user decides. Merging moves every record of the duplicate (invoices, payments,
deposits, appointments, waiting list, the ledger lines that make up its balance, messages, receipts) to the
customer that is kept, and keeps the duplicate's mobile and customer code as extra ones, so searching by the
old number or code - and future imports from the previous software - still find the person.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import String, cast, func, select, update
from sqlalchemy.orm import Session

from ..models import (
    Appointment,
    ConversationMessage,
    Customer,
    Deposit,
    InboundReceipt,
    Invoice,
    JournalLine,
    KnowledgeItem,
    Payment,
    WaitlistEntry,
)
from .audit import audit
from .service_catalog import name_key

PLACEHOLDER = "مشتری "  # names the bot gives before a real name is known
LINKED = (Appointment, WaitlistEntry, Deposit, Invoice, Payment, JournalLine, InboundReceipt, ConversationMessage)


def person_key(name: str | None) -> str:
    """Comparable full name: ی/ي, ک/ك, spaces and half-spaces don't matter."""
    return name_key(name) if name and not name.startswith(PLACEHOLDER) else ""


def _pair(a: int, b: int) -> str:
    return f"{min(a, b)}-{max(a, b)}"


def not_same_pairs(db: Session) -> set[str]:
    return set(db.scalars(select(KnowledgeItem.key).where(KnowledgeItem.kind == "customer_not_same")))


def mark_different(db: Session, ids: list[int], user=None) -> int:  # noqa: ANN001
    """The user said these customers are different people: never suggest them together again."""
    known = not_same_pairs(db)
    n = 0
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if a != b and _pair(a, b) not in known:
                db.add(KnowledgeItem(kind="customer_not_same", key=_pair(a, b), value={}))
                known.add(_pair(a, b))
                n += 1
    audit(db, "customer.not_same", "customer", ",".join(map(str, ids)), {}, user=user)
    db.flush()
    return n


def stats(db: Session, ids: list[int]) -> dict[int, dict]:
    """What each customer has, so the user can tell which record is the real / main one."""
    out = {i: {"invoices": 0, "spent": 0, "deposits_held": 0, "appointments": 0, "last": None} for i in ids}
    if not ids:
        return out
    for cid, n, total, last in db.execute(select(Invoice.customer_id, func.count(Invoice.id), func.sum(Invoice.total), func.max(Invoice.issued_at))
                                          .where(Invoice.customer_id.in_(ids), Invoice.status != "void").group_by(Invoice.customer_id)):
        out[cid].update(invoices=int(n), spent=int(total or 0), last=last.isoformat(timespec="minutes") if last else None)
    for cid, amt in db.execute(select(Deposit.customer_id, func.sum(Deposit.amount)).where(Deposit.customer_id.in_(ids), Deposit.status == "held")
                               .group_by(Deposit.customer_id)):
        out[cid]["deposits_held"] = int(amt or 0)
    for cid, n, last in db.execute(select(Appointment.customer_id, func.count(Appointment.id), func.max(Appointment.start_at))
                                   .where(Appointment.customer_id.in_(ids), Appointment.status.in_(("booked", "done")))
                                   .group_by(Appointment.customer_id)):
        out[cid]["appointments"] = int(n)
        stamp = last.isoformat(timespec="minutes") if last else None
        if stamp and (out[cid]["last"] is None or stamp > out[cid]["last"]):
            out[cid]["last"] = stamp
    return out


def brief(c: Customer, st: dict | None = None) -> dict:
    return {"id": c.id, "code": c.legacy_code, "full_name": c.full_name, "mobile": c.mobile, "other_mobiles": c.other_mobiles or [],
            "mobile_raw": c.mobile_raw, "source": c.source, "created_at": c.created_at.isoformat(timespec="minutes"), **(st or {})}


def same_name(db: Session, name: str, exclude: int | None = None, limit: int = 5) -> list[dict]:
    """Customers with exactly this first and last name (after unifying letters) - asked about when adding one."""
    key = person_key(name)
    if len(key) < 3:
        return []
    first = (name or "").strip().split()[0] if (name or "").strip() else ""
    q = select(Customer).where(Customer.id != (exclude or 0))
    if first:  # narrow in SQL by the first word, then compare the whole name exactly
        from .search import fa_like
        q = q.where(fa_like(Customer.full_name, first))
    found = [c for c in db.scalars(q.limit(400)) if person_key(c.full_name) == key][:limit]
    st = stats(db, [c.id for c in found])
    return [brief(c, st[c.id]) for c in found]


def duplicate_groups(db: Session) -> list[dict]:
    """Every group of customers with the same full name (minus pairs the user said are different people)."""
    by_key: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for cid, name in db.execute(select(Customer.id, Customer.full_name)):
        k = person_key(name)
        if len(k) >= 3:
            by_key[k].append((cid, name))
    known = not_same_pairs(db)
    groups = []
    for k, members in by_key.items():
        if len(members) < 2:
            continue
        ids = [m[0] for m in members]
        # drop the group when every pair in it was already marked as different people
        if all(_pair(a, b) in known for i, a in enumerate(ids) for b in ids[i + 1:]):
            continue
        groups.append(ids)
    all_ids = [i for g in groups for i in g]
    customers = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_(all_ids or [-1])))}
    st = stats(db, all_ids)
    out = []
    for ids in groups:
        members = sorted((brief(customers[i], st[i]) for i in ids),
                         key=lambda x: (-(x["invoices"] + x["appointments"]), -x["spent"], x["id"]))
        out.append({"name": members[0]["full_name"], "customers": members,
                    "different_pairs": [p for p in known if all(int(x) in ids for x in p.split("-"))]})
    out.sort(key=lambda g: g["name"])
    return out


def merge(db: Session, keep: Customer, drop: Customer, user=None) -> dict:  # noqa: ANN001
    """Move everything of `drop` to `keep` and remove `drop`. Balances (debts, open deposits), purchase history and
    appointments simply add up, because they are all computed from the moved records."""
    if keep.id == drop.id:
        raise ValueError("یک مشتری را نمی‌توان با خودش یکی کرد")
    moved = {}
    for m in LINKED:
        res = db.execute(update(m).where(m.customer_id == drop.id).values(customer_id=keep.id))
        moved[m.__tablename__] = res.rowcount or 0
    # numbers and codes: the kept record gets the other's as extra ones (or as its own when it has none)
    mobiles = [x for x in [drop.mobile, *(drop.other_mobiles or [])] if x]
    codes = [x for x in [drop.legacy_code, *(drop.other_codes or [])] if x]
    drop.mobile = None
    drop.legacy_code = None
    db.flush()
    if not keep.mobile and mobiles:
        keep.mobile = mobiles.pop(0)
        keep.mobile_issue = keep.mobile_raw = None
    extra_m = [x for x in [*(keep.other_mobiles or []), *mobiles] if x != keep.mobile]
    keep.other_mobiles = list(dict.fromkeys(extra_m)) or None
    extra_c = [x for x in [*(keep.other_codes or []), *codes] if x != keep.legacy_code]
    keep.other_codes = list(dict.fromkeys(extra_c)) or None
    for f in ("instagram", "whatsapp", "birth_date"):
        if not getattr(keep, f) and getattr(drop, f):
            setattr(keep, f, getattr(drop, f))
            setattr(drop, f, None)
    if drop.notes and drop.notes not in (keep.notes or ""):
        keep.notes = "\n".join(x for x in (keep.notes, drop.notes) if x)
    keep.tags = list(dict.fromkeys([*(keep.tags or []), *(drop.tags or [])]))
    keep.known_cards = list(dict.fromkeys([*(keep.known_cards or []), *(drop.known_cards or [])]))
    db.flush()
    info = {"kept": keep.id, "removed": drop.id, "name": drop.full_name, "mobiles": mobiles, "codes": codes, "moved": moved}
    db.delete(drop)
    db.flush()
    audit(db, "customer.merge", "customer", keep.id, info, user=user)
    return info


def find_by_other_mobile(db: Session, mobile: str) -> Customer | None:
    return next((c for c in db.scalars(select(Customer).where(cast(Customer.other_mobiles, String).like(f'%"{mobile}"%')))
                 if mobile in (c.other_mobiles or [])), None)


def find_by_other_code(db: Session, code: str) -> Customer | None:
    return next((c for c in db.scalars(select(Customer).where(cast(Customer.other_codes, String).like(f'%"{code}"%')))
                 if code in (c.other_codes or [])), None)
