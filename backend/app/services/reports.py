"""Reports, analytics, forecasting and automatic insights."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .textutil import toman
from ..models import (
    Appointment,
    Customer,
    Deposit,
    Expense,
    Invoice,
    InvoiceItem,
    Payment,
    PaymentAccount,
    Service,
    ServiceLine,
    Staff,
)


def _range(start: date | None, end: date | None) -> tuple[datetime, datetime]:
    end = end or date.today()
    start = start or (end - timedelta(days=29))
    return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.max.time())


def summary(db: Session, start: date | None = None, end: date | None = None) -> dict:
    s, e = _range(start, end)
    inv_q = select(Invoice).where(Invoice.issued_at.between(s, e), Invoice.status != "void")
    invoices = db.scalars(inv_q).all()
    revenue = sum(i.total for i in invoices)
    discounts = sum(i.discount for i in invoices) + sum(it.discount for i in invoices for it in i.items)
    expenses = int(db.scalar(select(func.coalesce(func.sum(Expense.amount), 0)).where(Expense.spent_at.between(s, e))) or 0)
    received_payments = int(db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.paid_at.between(s, e))) or 0)
    received_deposits = int(db.scalar(select(func.coalesce(func.sum(Deposit.amount), 0)).where(Deposit.received_at.between(s, e))) or 0)
    held = int(db.scalar(select(func.coalesce(func.sum(Deposit.amount), 0)).where(Deposit.status == "held")) or 0)
    held_count = db.scalar(select(func.count(Deposit.id)).where(Deposit.status == "held")) or 0
    receivable = sum(i.total - i.paid for i in db.scalars(select(Invoice).where(Invoice.status.in_(["issued", "partial"]))))

    customer_ids = {i.customer_id for i in invoices}
    new_customers = db.scalar(select(func.count(Customer.id)).where(Customer.created_at.between(s, e))) or 0
    returning = 0
    for cid in customer_ids:
        prev = db.scalar(select(func.count(Invoice.id)).where(Invoice.customer_id == cid, Invoice.issued_at < s, Invoice.status != "void"))
        returning += 1 if prev else 0

    by_line: dict[str, int] = defaultdict(int)
    by_service: dict[str, dict] = {}
    by_staff: dict[str, int] = defaultdict(int)
    lines = {l.id: l for l in db.scalars(select(ServiceLine))}
    staff = {p.id: p.full_name for p in db.scalars(select(Staff))}
    for inv in invoices:
        factor = (inv.total / inv.subtotal) if inv.subtotal else 1
        for it in inv.items:
            amt = round(it.amount * factor)
            by_line[lines[it.line_id].name if it.line_id in lines else "سایر"] += amt
            key = it.description or "—"
            d = by_service.setdefault(key, {"name": key, "count": 0, "revenue": 0})
            d["count"] += it.quantity
            d["revenue"] += amt
            if it.staff_id:
                by_staff[staff.get(it.staff_id, "?")] += amt

    accounts = {a.id: a for a in db.scalars(select(PaymentAccount))}
    by_account: dict[str, int] = defaultdict(int)
    for pid, amt in db.execute(select(Payment.payment_account_id, Payment.amount).where(Payment.paid_at.between(s, e))):
        by_account[accounts[pid].name if pid in accounts else "?"] += int(amt)
    for pid, amt in db.execute(select(Deposit.payment_account_id, Deposit.amount).where(Deposit.received_at.between(s, e))):
        by_account[accounts[pid].name if pid in accounts else "?"] += int(amt)

    expense_by_cat: dict[str, int] = defaultdict(int)
    for cat, amt in db.execute(select(Expense.category, Expense.amount).where(Expense.spent_at.between(s, e))):
        expense_by_cat[cat] += int(amt)

    appts = db.execute(select(Appointment.status, func.count()).where(Appointment.start_at.between(s, e)).group_by(Appointment.status)).all()
    appt_stats = {k: v for k, v in appts}
    total_appts = sum(appt_stats.values()) or 0

    return {
        "period": {"start": s.date().isoformat(), "end": e.date().isoformat()},
        "revenue": revenue,
        "expenses": expenses,
        "net_profit": revenue - expenses,
        "cash_in": received_payments + received_deposits,
        "deposits_received": received_deposits,
        "deposits_held": held,
        "deposits_held_count": held_count,
        "receivable": receivable,
        "discounts": discounts,
        "invoice_count": len(invoices),
        "avg_ticket": revenue // len(invoices) if invoices else 0,
        "customers_served": len(customer_ids),
        "new_customers": new_customers,
        "returning_customers": returning,
        "by_line": sorted(({"name": k, "value": v} for k, v in by_line.items()), key=lambda x: -x["value"]),
        "by_account": sorted(({"name": k, "value": v} for k, v in by_account.items()), key=lambda x: -x["value"]),
        "by_staff": sorted(({"name": k, "value": v} for k, v in by_staff.items()), key=lambda x: -x["value"]),
        "top_services": sorted(by_service.values(), key=lambda x: -x["revenue"])[:10],
        "expense_by_category": sorted(({"name": k, "value": v} for k, v in expense_by_cat.items()), key=lambda x: -x["value"]),
        "appointments": {"total": total_appts, **appt_stats,
                         "no_show_rate": round(appt_stats.get("no_show", 0) / total_appts, 3) if total_appts else 0},
    }


def daily_series(db: Session, start: date | None = None, end: date | None = None) -> list[dict]:
    s, e = _range(start, end)
    rev: dict[date, int] = defaultdict(int)
    exp: dict[date, int] = defaultdict(int)
    cash: dict[date, int] = defaultdict(int)
    for at, total in db.execute(select(Invoice.issued_at, Invoice.total).where(Invoice.issued_at.between(s, e), Invoice.status != "void")):
        rev[at.date()] += int(total)
    for at, amt in db.execute(select(Expense.spent_at, Expense.amount).where(Expense.spent_at.between(s, e))):
        exp[at.date()] += int(amt)
    for at, amt in db.execute(select(Payment.paid_at, Payment.amount).where(Payment.paid_at.between(s, e))):
        cash[at.date()] += int(amt)
    for at, amt in db.execute(select(Deposit.received_at, Deposit.amount).where(Deposit.received_at.between(s, e))):
        cash[at.date()] += int(amt)
    out = []
    d = s.date()
    while d <= e.date():
        out.append({"date": d.isoformat(), "revenue": rev[d], "expenses": exp[d], "cash_in": cash[d]})
        d += timedelta(days=1)
    return out


# ------------------------------------------------------------------ forecasting
def forecast(db: Session, days: int = 30, history_days: int = 120) -> dict:
    """Holt's damped-trend smoothing + day-of-week seasonality (pure python, no heavy deps).

    Also projects deposits held (booked future revenue) and appointment load.
    Returns point forecast with a simple confidence band from residual spread.
    """
    end = date.today() - timedelta(days=1)  # today is incomplete
    hist = daily_series(db, end - timedelta(days=history_days - 1), end)
    values = [h["revenue"] for h in hist]
    if sum(1 for v in values if v) < 7:
        return {"method": "insufficient_data", "points": [], "total": 0,
                "message": "برای پیش‌بینی دقیق حداقل به ۷ روز داده فروش نیاز است"}

    # weekday seasonality index
    dow_sum: dict[int, float] = defaultdict(float)
    dow_cnt: dict[int, int] = defaultdict(int)
    for h in hist:
        wd = date.fromisoformat(h["date"]).weekday()
        dow_sum[wd] += h["revenue"]
        dow_cnt[wd] += 1
    overall = (sum(values) / len(values)) or 1
    season = {wd: ((dow_sum[wd] / dow_cnt[wd]) / overall if dow_cnt[wd] else 1.0) for wd in range(7)}

    # Holt on deseasonalized series
    alpha, beta, phi = 0.1, 0.03, 0.9
    level, trend = None, 0.0
    residuals = []
    for h in hist:
        wd = date.fromisoformat(h["date"]).weekday()
        x = h["revenue"] / (season[wd] or 1)
        if level is None:
            level = x
            continue
        pred = (level + phi * trend) * (season[wd] or 1)
        residuals.append(h["revenue"] - pred)
        prev = level
        level = alpha * x + (1 - alpha) * (level + phi * trend)
        trend = beta * (level - prev) + (1 - beta) * phi * trend
    spread = (sum(r * r for r in residuals) / len(residuals)) ** 0.5 if residuals else 0

    points = []
    damp = 0.0
    for i in range(1, days + 1):
        d = end + timedelta(days=i)
        damp += phi ** i
        y = max(0.0, (level + damp * trend) * season[d.weekday()])
        points.append({"date": d.isoformat(), "revenue": int(y), "low": int(max(0, y - 1.28 * spread)), "high": int(y + 1.28 * spread)})

    upcoming = db.scalar(select(func.count(Appointment.id)).where(
        Appointment.start_at.between(datetime.now(), datetime.now() + timedelta(days=days)), Appointment.status == "booked")) or 0
    booked_value = int(db.scalar(select(func.coalesce(func.sum(Appointment.quoted_price), 0)).where(
        Appointment.start_at.between(datetime.now(), datetime.now() + timedelta(days=days)), Appointment.status == "booked")) or 0)
    last30 = sum(values[-30:])
    total = sum(p["revenue"] for p in points)
    return {
        "method": "holt_damped_weekly_seasonality",
        "points": points,
        "total": total,
        "change_vs_last_period": round((total - last30) / last30, 3) if last30 else None,
        "trend_per_day": int(trend),
        "weekday_index": {str(k): round(v, 2) for k, v in season.items()},
        "booked_appointments": upcoming,
        "booked_value": booked_value,
    }


def insights(db: Session) -> list[dict]:
    """Rule-based insights (AI can produce richer ones via the assistant)."""
    out: list[dict] = []
    today = date.today()
    cur = summary(db, today - timedelta(days=29), today)
    prev = summary(db, today - timedelta(days=59), today - timedelta(days=30))
    if prev["revenue"]:
        ch = (cur["revenue"] - prev["revenue"]) / prev["revenue"]
        if abs(ch) >= 0.1:
            out.append({"level": "success" if ch > 0 else "warning",
                        "text": f"درآمد ۳۰ روز اخیر نسبت به دوره قبل {abs(ch) * 100:.0f}٪ {'افزایش' if ch > 0 else 'کاهش'} داشته است"})
    prev_lines = {x["name"]: x["value"] for x in prev["by_line"]}
    for x in cur["by_line"]:
        p = prev_lines.get(x["name"])
        if p and abs(x["value"] - p) / p >= 0.25:
            out.append({"level": "info", "text": f"لاین «{x['name']}» {abs(x['value'] - p) / p * 100:.0f}٪ {'رشد' if x['value'] > p else 'افت'} داشته"})
    if cur["deposits_held_count"]:
        out.append({"level": "info", "text": f"{cur['deposits_held_count']} بیعانه باز به مبلغ {toman(cur['deposits_held'])} در انتظار ارائه خدمت است"})
    if cur["appointments"].get("no_show_rate", 0) > 0.1:
        out.append({"level": "warning", "text": f"نرخ عدم مراجعه {cur['appointments']['no_show_rate'] * 100:.0f}٪ است؛ یادآوری پیامکی پیشنهاد می‌شود"})
    if cur["expenses"] > cur["revenue"] and cur["revenue"]:
        out.append({"level": "danger", "text": "هزینه‌های ۳۰ روز اخیر از درآمد بیشتر بوده است"})
    # churn: good customers not seen in 60 days
    cutoff = datetime.now() - timedelta(days=60)
    rows = db.execute(select(Invoice.customer_id, func.max(Invoice.issued_at), func.count(Invoice.id))
                      .where(Invoice.status != "void").group_by(Invoice.customer_id)).all()
    lost = [cid for cid, last, n in rows if n >= 3 and last < cutoff]
    if lost:
        out.append({"level": "warning", "text": f"{len(lost)} مشتری وفادار بیش از ۶۰ روز است مراجعه نکرده‌اند؛ کمپین بازگشت پیشنهاد می‌شود"})
    return out


def customer_rfm(db: Session, limit: int = 50) -> list[dict]:
    rows = db.execute(select(Invoice.customer_id, func.max(Invoice.issued_at), func.count(Invoice.id), func.sum(Invoice.total))
                      .where(Invoice.status != "void").group_by(Invoice.customer_id)
                      .order_by(func.sum(Invoice.total).desc()).limit(limit)).all()
    names = {c.id: c.full_name for c in db.scalars(select(Customer).where(Customer.id.in_([r[0] for r in rows])))}
    out = []
    for cid, last, n, total in rows:
        days = (datetime.now() - last).days
        segment = "وفادار" if n >= 5 and days < 45 else "در خطر ریزش" if days > 60 and n >= 2 else "جدید" if n == 1 else "فعال"
        out.append({"customer_id": cid, "name": names.get(cid, "?"), "visits": n, "total": int(total or 0),
                    "last_visit_days": days, "segment": segment})
    return out


def services_catalog(db: Session) -> list[dict]:
    rows = db.scalars(select(Service).order_by(Service.line_id, Service.name)).all()
    return [{"id": s.id, "name": s.name, "line": s.line.name, "base_price": s.base_price,
             "learned_avg_price": s.learned_avg_price, "learned_count": s.learned_count, "default_deposit": s.default_deposit}
            for s in rows]


def invoice_item_rows(db: Session, start: date | None, end: date | None) -> list[dict]:
    s, e = _range(start, end)
    rows = db.execute(select(Invoice.number, Invoice.issued_at, Customer.full_name, InvoiceItem.description,
                             InvoiceItem.quantity, InvoiceItem.unit_price, InvoiceItem.discount)
                      .join(Invoice, Invoice.id == InvoiceItem.invoice_id).join(Customer, Customer.id == Invoice.customer_id)
                      .where(Invoice.issued_at.between(s, e), Invoice.status != "void")).all()
    return [dict(number=r[0], date=r[1].isoformat(), customer=r[2], service=r[3], qty=r[4], unit_price=r[5], discount=r[6]) for r in rows]


def staff_shares(db: Session, start: date | None = None, end: date | None = None) -> dict:
    """Who did what: revenue, staff share (commission) and salon share per staff member and per line.

    Uses the commission recorded on each invoice item at issue time; older items without it fall back to
    the staff member's current percent.
    """
    from ..services.accounting import STAFF_PAYABLE, account, staff_balance
    from ..models import JournalEntry, JournalLine

    s, e = _range(start, end)
    staff = {p.id: p for p in db.scalars(select(Staff))}
    lines = {l.id: l.name for l in db.scalars(select(ServiceLine))}
    rows = db.execute(select(InvoiceItem, Invoice.subtotal, Invoice.total).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
                      .where(Invoice.issued_at.between(s, e), Invoice.status != "void")).all()
    per_staff: dict = {}
    per_line: dict = {}
    for it, subtotal, total in rows:
        net = it.net_amount if it.net_amount is not None else (round(it.amount * total / subtotal) if subtotal else it.amount)
        person = staff.get(it.staff_id)
        if it.commission_amount is not None:
            comm = it.commission_amount
        else:
            comm = round(net * (person.commission_percent or 0) / 100) if person else 0
        key = it.staff_id or 0
        r = per_staff.setdefault(key, {"staff_id": it.staff_id, "name": person.full_name if person else "بدون پرسنل",
                                       "line": lines.get(person.line_id) if person and person.line_id else None,
                                       "percent": person.commission_percent if person else 0,
                                       "services": 0, "revenue": 0, "staff_share": 0, "salon_share": 0})
        r["services"] += it.quantity
        r["revenue"] += net
        r["staff_share"] += comm
        r["salon_share"] += net - comm
        lk = it.line_id or 0
        lr = per_line.setdefault(lk, {"line_id": it.line_id, "name": lines.get(it.line_id, "سایر"), "services": 0,
                                      "revenue": 0, "staff_share": 0, "salon_share": 0})
        lr["services"] += it.quantity
        lr["revenue"] += net
        lr["staff_share"] += comm
        lr["salon_share"] += net - comm
    # payouts in the period and outstanding balances
    payable = account(db, STAFF_PAYABLE)
    paid = dict(db.execute(select(JournalLine.staff_id, func.sum(JournalLine.debit))
                           .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                           .where(JournalLine.account_id == payable.id, JournalEntry.ref_type == "staff_payout",
                                  JournalEntry.at.between(s, e)).group_by(JournalLine.staff_id)).all())
    for pid, p in staff.items():
        if p.is_active and pid not in per_staff:
            per_staff[pid] = {"staff_id": pid, "name": p.full_name, "line": lines.get(p.line_id), "percent": p.commission_percent,
                              "services": 0, "revenue": 0, "staff_share": 0, "salon_share": 0}
    for r in per_staff.values():
        r["paid"] = int(paid.get(r["staff_id"]) or 0) if r["staff_id"] else 0
        r["balance"] = staff_balance(db, r["staff_id"]) if r["staff_id"] else 0
    staff_rows = sorted(per_staff.values(), key=lambda x: -x["revenue"])
    totals = {k: sum(r[k] for r in staff_rows) for k in ("revenue", "staff_share", "salon_share", "paid", "balance")}
    return {"period": {"start": s.date().isoformat(), "end": e.date().isoformat()}, "staff": staff_rows,
            "lines": sorted(per_line.values(), key=lambda x: -x["revenue"]), "totals": totals}
