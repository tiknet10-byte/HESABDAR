"""Treasury (خزانه): a part of each service line's revenue taken first every month, before the rest is split between
the staff member and the salon.

The rate is per line and Jalali month, in hundredths of a percent (4250 = 42.5%). A month without its own rate uses
the line's latest earlier one (none at all: 0, as before 1.21).

It is booked on every service row of an invoice when the invoice is issued: treasury = net x rate (integer, half up),
staff share = (net - treasury) x the staff member's percent (as before), salon share = the rest. The staff share is the
commission in the books (5200 / 2200), so the profit and every report follow the ledger; the treasury and the salon
share are both the salon's (revenue 41xx stays whole, no account of its own).

Changing a month's rate later books that month's rows again - only that month (later months that used the old rate
keep it: they get a row of their own first). The commission entry of each changed invoice is posted again on its own
date, and for a returned invoice the commission part of its return too, so a return takes back exactly what was given.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import Invoice, InvoiceItem, JournalEntry, JournalLine, ServiceLine, Staff, TreasuryRate, local_now
from .jalali import jmonth_of, month_range, next_month

BP = 10_000  # 100.00%


class TreasuryError(ValueError):
    pass


class Conflict(TreasuryError):
    """The rate was changed by someone else since the page showed it."""


def pct_bp(amount: int, bp: int) -> int:
    """amount x bp / 10000 in whole Rial, half up (exact integers; a float percent like 44.45 can round the wrong way)."""
    q = (abs(int(amount)) * int(bp) + BP // 2) // BP
    return q if amount >= 0 else -q


def commission_of(base: int, percent: float) -> int:
    """The staff member's share of what is left after the treasury - computed exactly as before 1.21 (rate 0 = same)."""
    return int(round(base * percent / 100))


def _rate_row(db: Session, line_id: int, jy: int, jm: int) -> TreasuryRate | None:
    """The row that gives (line, month) its rate: the month's own, else the latest earlier one."""
    return db.scalar(select(TreasuryRate).where(
        TreasuryRate.line_id == line_id, or_(TreasuryRate.jy < jy, and_(TreasuryRate.jy == jy, TreasuryRate.jm <= jm)))
        .order_by(TreasuryRate.jy.desc(), TreasuryRate.jm.desc()).limit(1))


def effective_bp(db: Session, line_id: int | None, at: datetime | date) -> int:
    if not line_id:
        return 0
    row = _rate_row(db, line_id, *jmonth_of(at))
    return row.rate_bp if row else 0


def book_item(item: InvoiceItem, bp: int | None) -> None:
    """Treasury of one service row (the staff share is then worked out on what is left)."""
    if bp is None or item.net_amount is None:
        item.treasury_bp, item.treasury_amount = None, None
    else:
        item.treasury_bp, item.treasury_amount = int(bp), pct_bp(item.net_amount, bp)


def base_of(item: InvoiceItem) -> int:
    return (item.net_amount or 0) - (item.treasury_amount or 0)


# ------------------------------------------------------------------ the month's rows of a line
def _month_items(db: Session, line_id: int, jy: int, jm: int) -> list[tuple[InvoiceItem, Invoice]]:
    """Every service row of the line on invoices issued in the month (voided ones too: their return is booked again)."""
    s, e = month_range(jy, jm)
    return list(db.execute(select(InvoiceItem, Invoice).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
                           .where(InvoiceItem.line_id == line_id, InvoiceItem.product_id.is_(None),
                                  func.coalesce(InvoiceItem.kind, "") != "extra", Invoice.issued_at >= s, Invoice.issued_at < e)
                           .order_by(Invoice.id, InvoiceItem.id)).all())


def _undo_dates(db: Session, invoice_ids: list[int]) -> dict[int, datetime]:
    """When each voided invoice was taken back in the books (as the reports date it)."""
    if not invoice_ids:
        return {}
    return dict(db.execute(select(JournalEntry.ref_id, func.min(JournalEntry.at)).where(
        JournalEntry.ref_type == "invoice_void", JournalEntry.ref_id.in_(invoice_ids)).group_by(JournalEntry.ref_id)).all())


def _plan(db: Session, line_id: int, jy: int, jm: int, bp: int) -> list[dict]:
    """What every row of the month would become at rate bp (nothing is changed)."""
    rows = _month_items(db, line_id, jy, jm)
    undo = _undo_dates(db, sorted({inv.id for _, inv in rows if inv.status == "void"}))
    out = []
    for it, inv in rows:
        if it.net_amount is None:
            continue  # a row from before the discount was shared per row (1.3): never booked again
        legacy = it.commission_percent is None
        t = (it.treasury_amount or 0) if legacy else pct_bp(it.net_amount, bp)
        old_c = it.commission_amount or 0
        # a row from before the staff share was recorded on it (commission NULL) has no commission entry: left alone
        new_c = old_c if legacy else (commission_of(it.net_amount - t, it.commission_percent) if it.commission_percent else 0)
        at_undo = undo.get(inv.id) if inv.status == "void" else None
        out.append({"item": it, "invoice": inv, "treasury": t, "old_c": old_c, "new_c": new_c,
                    "legacy": legacy, "void": inv.status == "void",
                    "cancelled": at_undo is not None and at_undo == inv.issued_at,
                    "returned_at": at_undo if at_undo is not None and at_undo != inv.issued_at else None})
    return out


def _payouts_since(db: Session, staff_ids: list[int], since: datetime) -> dict[int, int]:
    from .accounting import STAFF_PAYABLE, account

    if not staff_ids:
        return {}
    payable = account(db, STAFF_PAYABLE)
    return {k: int(v or 0) for k, v in db.execute(
        select(JournalLine.staff_id, func.sum(JournalLine.debit)).join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalLine.account_id == payable.id, JournalEntry.ref_type == "staff_payout", JournalEntry.at >= since,
               JournalLine.staff_id.in_(staff_ids)).group_by(JournalLine.staff_id)).all()}


def _check_month(db: Session, line_id: int, jy: int, jm: int, bp: int) -> ServiceLine:
    line = db.get(ServiceLine, line_id)
    if line is None:
        raise TreasuryError("لاین پیدا نشد")
    if not (1 <= jm <= 12) or not (1300 <= jy <= 1500):
        raise TreasuryError("ماه نامعتبر است")
    if (jy, jm) > jmonth_of(local_now()):
        raise TreasuryError("درصد خزانه را برای ماه جاری یا ماه‌های گذشته تعیین کنید (ماه آینده هنوز نیامده)")
    if not (0 <= int(bp) <= BP):
        raise TreasuryError("درصد خزانه باید بین ۰ تا ۱۰۰ باشد")
    return line


def preview(db: Session, line_id: int, jy: int, jm: int, bp: int) -> dict:
    """What saving rate bp for (line, month) changes, per staff member - read only."""
    line = _check_month(db, line_id, jy, jm, bp)
    row = _rate_row(db, line_id, jy, jm)
    old_bp = row.rate_bp if row else 0
    plan = _plan(db, line_id, jy, jm, bp)
    s, _ = month_range(jy, jm)
    per: dict[int, dict] = {}
    elsewhere: dict[tuple[int, int], dict] = defaultdict(lambda: {"change": 0, "treasury_change": 0})
    revenue = t_before = t_after = 0
    for p in plan:
        it = p["item"]
        d = p["new_c"] - p["old_c"]
        sold = not p["cancelled"]
        returned_here = p["returned_at"] is not None and jmonth_of(p["returned_at"]) == (jy, jm)
        k = (1 if sold else 0) - (1 if returned_here else 0)  # how the row counts in this month's report
        revenue += k * it.net_amount
        t_before += k * (it.treasury_amount or 0)
        t_after += k * p["treasury"]
        dt = p["treasury"] - (it.treasury_amount or 0)
        if p["returned_at"] is not None and not returned_here and (d or dt):
            # returned in a later month: that return takes back the new amounts there
            w = elsewhere[jmonth_of(p["returned_at"])]
            w["change"] -= d
            w["treasury_change"] -= dt
        if not it.staff_id:
            continue
        r = per.setdefault(it.staff_id, {"staff_id": it.staff_id, "before": 0, "after": 0, "balance_change": 0})
        r["before"] += k * p["old_c"]
        r["after"] += k * p["new_c"]
        if not p["void"]:
            r["balance_change"] += d  # a voided row's share and its return change together: nothing owed changes
    from .accounting import staff_balance

    paid = _payouts_since(db, list(per), s)
    staff_rows = []
    for sid, r in per.items():
        person = db.get(Staff, sid)
        bal = staff_balance(db, sid)
        staff_rows.append({**r, "name": person.full_name if person else "—", "change": r["after"] - r["before"],
                           "paid_since": paid.get(sid, 0), "balance": bal, "balance_after": bal + r["balance_change"]})
    staff_rows.sort(key=lambda x: -abs(x["change"]))
    touched = [x for x in staff_rows if x["balance_change"]]
    return {"line_id": line_id, "line": line.name, "jy": jy, "jm": jm, "old_bp": old_bp, "new_bp": int(bp),
            "own": bool(row and (row.jy, row.jm) == (jy, jm)), "inherited_from": [row.jy, row.jm] if row and (row.jy, row.jm) != (jy, jm) else None,
            "revenue": revenue, "treasury_before": t_before, "treasury_after": t_after,
            "invoices": len({p["invoice"].id for p in plan if p["new_c"] != p["old_c"] or (p["item"].treasury_amount or 0) != p["treasury"]}),
            "staff": staff_rows, "returns_elsewhere": [{"jy": y, "jm": m, **v} for (y, m), v in sorted(elsewhere.items()) if v["change"] or v["treasury_change"]],
            "needs_confirm": any(x["paid_since"] > 0 for x in touched) or any(x["balance_after"] < 0 for x in touched),
            "later_kept": _later_months_to_keep(db, line_id, jy, jm, old_bp)}


def _later_months_to_keep(db: Session, line_id: int, jy: int, jm: int, old_bp: int) -> list[dict]:
    """Later months (up to this month) that take their rate from this one or an earlier one and already have invoices
    of the line: they keep the rate their invoices were booked with. Those without invoices take the new rate."""
    out = []
    cur = jmonth_of(local_now())
    own = {(r.jy, r.jm) for r in db.scalars(select(TreasuryRate).where(TreasuryRate.line_id == line_id))}
    y, m = next_month(jy, jm)
    while (y, m) <= cur and (y, m) not in own:
        s, e = month_range(y, m)
        if db.scalar(select(InvoiceItem.id).join(Invoice, Invoice.id == InvoiceItem.invoice_id).where(
                InvoiceItem.line_id == line_id, Invoice.issued_at >= s, Invoice.issued_at < e).limit(1)) is not None:
            out.append({"jy": y, "jm": m, "bp": old_bp})
        y, m = next_month(y, m)
    return out


def save(db: Session, line_id: int, jy: int, jm: int, bp: int, expected_bp: int | None = None, confirm: bool = False,
         user=None) -> dict:  # noqa: ANN001
    """Set (line, month)'s rate and book that month's rows again. One transaction; the caller commits."""
    from .accounting import repost_commissions
    from .audit import audit, write_lock

    bp = int(bp)
    _check_month(db, line_id, jy, jm, bp)
    write_lock(db)  # nobody issues an invoice or saves a rate in between
    pv = preview(db, line_id, jy, jm, bp)
    if expected_bp is not None and int(expected_bp) != pv["old_bp"]:
        raise Conflict("درصد خزانهٔ این ماه همین الان تغییر کرد؛ صفحه را تازه کنید")
    if pv["needs_confirm"] and not confirm:
        raise Conflict("به پرسنلِ این ماه پرداخت شده یا مانده‌اش منفی می‌شود؛ پیش‌نمایش را ببینید و تأیید کنید")
    now = local_now()
    for k in pv["later_kept"]:  # they keep the rate their invoices were booked with
        db.add(TreasuryRate(line_id=line_id, jy=k["jy"], jm=k["jm"], rate_bp=k["bp"], source="auto", updated_at=now,
                            updated_by=getattr(user, "id", None)))
    row = db.scalar(select(TreasuryRate).where(TreasuryRate.line_id == line_id, TreasuryRate.jy == jy, TreasuryRate.jm == jm))
    if row is None:
        row = TreasuryRate(line_id=line_id, jy=jy, jm=jm)
        db.add(row)
    row.rate_bp, row.source, row.updated_at, row.updated_by = bp, "owner", now, getattr(user, "id", None)
    changed: dict[int, Invoice] = {}
    for p in _plan(db, line_id, jy, jm, bp):
        it = p["item"]
        if p["legacy"]:
            continue
        if it.treasury_bp != bp or it.treasury_amount != p["treasury"]:
            it.treasury_bp, it.treasury_amount = bp, p["treasury"]
        if p["new_c"] != (it.commission_amount or 0):
            it.commission_amount = p["new_c"]
            changed[p["invoice"].id] = p["invoice"]
    db.flush()
    repost_commissions(db, list(changed.values()))
    audit(db, "treasury.set", "line", line_id, {
        "month": f"{jy}/{jm:02d}", "old_bp": pv["old_bp"], "new_bp": bp, "invoices": len(changed),
        "kept": [f"{k['jy']}/{k['jm']:02d}={k['bp']}" for k in pv["later_kept"]],
        "staff": {str(x["staff_id"]): x["change"] for x in pv["staff"] if x["change"]},
        "returns_elsewhere": {f"{x['jy']}/{x['jm']:02d}": [x["change"], x["treasury_change"]] for x in pv["returns_elsewhere"]}},
          user=user)
    return {**pv, "invoices": len(changed)}


def month_rates(db: Session, jy: int, jm: int) -> list[dict]:
    """Each service line's rate for the month: its own, or the one it takes from an earlier month."""
    lines = list(db.scalars(select(ServiceLine).order_by(ServiceLine.id)))
    s, e = month_range(jy, jm)
    used = {lid for (lid,) in db.execute(select(InvoiceItem.line_id).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
                                         .where(Invoice.issued_at >= s, Invoice.issued_at < e, InvoiceItem.line_id.is_not(None))
                                         .distinct())}
    out = []
    for line in lines:
        if not line.is_active and line.id not in used:
            continue
        row = _rate_row(db, line.id, jy, jm)
        own = bool(row and (row.jy, row.jm) == (jy, jm))
        out.append({"line_id": line.id, "name": line.name, "bp": row.rate_bp if row else 0, "own": own,
                    "source": row.source if own else None, "from": None if own or not row else [row.jy, row.jm],
                    "set": row is not None})
    return out

