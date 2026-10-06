"""Reports, analytics, forecasting and automatic insights."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

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


# money that really came in during a period: deposits brought over from the previous software are opening
# balances (received in the old system), so they never count as cash received here
REAL_DEPOSIT = Deposit.source.not_in(("import", "void_credit"))  # void_credit: already counted as the invoice payment


def summary(db: Session, start: date | None = None, end: date | None = None) -> dict:
    s, e = _range(start, end)
    inv_q = (select(Invoice).where(Invoice.issued_at.between(s, e), Invoice.status != "void")
             .options(selectinload(Invoice.items)))
    invoices = db.scalars(inv_q).all()
    revenue = sum(i.total for i in invoices)
    discounts = sum(i.discount for i in invoices) + sum(it.discount for i in invoices for it in i.items)
    expenses = int(db.scalar(select(func.coalesce(func.sum(Expense.amount), 0)).where(Expense.spent_at.between(s, e))) or 0)
    received_payments = int(db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.paid_at.between(s, e))) or 0)
    received_deposits = int(db.scalar(select(func.coalesce(func.sum(Deposit.amount), 0)).where(Deposit.received_at.between(s, e), REAL_DEPOSIT)) or 0)
    held = int(db.scalar(select(func.coalesce(func.sum(Deposit.amount), 0)).where(Deposit.status == "held")) or 0)
    held_count = db.scalar(select(func.count(Deposit.id)).where(Deposit.status == "held")) or 0
    receivable = int(db.scalar(select(func.coalesce(func.sum(Invoice.total - Invoice.paid), 0))
                               .where(Invoice.status.in_(["issued", "partial"]))) or 0)

    customer_ids = {i.customer_id for i in invoices}
    # customers brought over from the previous software are not "new" on the day they were imported
    new_customers = db.scalar(select(func.count(Customer.id)).where(Customer.created_at.between(s, e), Customer.source != "import")) or 0
    # returning = had an earlier invoice here, or earlier services in the previous software's history
    earlier = set()
    if customer_ids:
        earlier |= set(db.scalars(select(Invoice.customer_id).where(Invoice.customer_id.in_(customer_ids), Invoice.issued_at < s,
                                                                    Invoice.status != "void")))
        earlier |= set(db.scalars(select(Appointment.customer_id).where(Appointment.customer_id.in_(customer_ids),
                                                                        Appointment.status == "done", Appointment.start_at < s)))
    returning = len(earlier)

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
    for pid, amt in db.execute(select(Deposit.payment_account_id, Deposit.amount).where(Deposit.received_at.between(s, e), REAL_DEPOSIT)):
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


def agenda(db: Session, now: datetime | None = None) -> dict:
    """What needs attention today: today's appointments, the next ones, and open items to settle."""
    from ..models import local_now

    now = now or local_now()
    day = datetime.combine(now.date(), datetime.min.time())
    tomorrow = day + timedelta(days=1)
    svc = {x.id: x for x in db.scalars(select(Service))}
    staff = {p.id: p.full_name for p in db.scalars(select(Staff))}

    def row(a: Appointment, cname: str) -> dict:
        sv = svc.get(a.service_id)
        return {"id": a.id, "start_at": a.start_at.isoformat(timespec="minutes"), "customer": cname, "customer_id": a.customer_id,
                "service": sv.name if sv else None, "line": sv.line.name if sv else None, "staff": staff.get(a.staff_id),
                "status": a.status, "time_unknown": bool(a.time_unknown),
                "minutes": a.duration_minutes or (sv.duration_minutes if sv else 60)}

    base = select(Appointment, Customer.full_name).join(Customer, Customer.id == Appointment.customer_id)
    today = [row(a, n) for a, n in db.execute(base.where(Appointment.start_at >= day, Appointment.start_at < tomorrow,
                                                          Appointment.status.in_(("booked", "done", "no_show")))
                                                   .order_by(Appointment.time_unknown.is_(True), Appointment.start_at))]
    upcoming = [row(a, n) for a, n in db.execute(base.where(Appointment.start_at >= tomorrow, Appointment.status == "booked")
                                                      .order_by(Appointment.start_at).limit(6))]
    count = lambda q: db.execute(q).one()  # noqa: E731
    overdue = count(select(func.count(Deposit.id), func.coalesce(func.sum(Deposit.amount), 0))
                    .join(Appointment, Appointment.id == Deposit.appointment_id)
                    .where(Deposit.status == "held", Appointment.start_at < day))
    no_appt = count(select(func.count(Deposit.id), func.coalesce(func.sum(Deposit.amount), 0))
                    .where(Deposit.status == "held", Deposit.appointment_id.is_(None)))
    unpaid = count(select(func.count(Invoice.id), func.coalesce(func.sum(Invoice.total - Invoice.paid), 0))
                   .where(Invoice.status.in_(("issued", "partial"))))
    past_open = db.scalar(select(func.count(Appointment.id)).where(Appointment.status == "booked", Appointment.start_at < day)) or 0
    unknown_time = db.scalar(select(func.count(Appointment.id)).where(Appointment.status == "booked", Appointment.time_unknown.is_(True),
                                                                     Appointment.start_at >= day)) or 0
    tomorrow_n = db.scalar(select(func.count(Appointment.id)).where(Appointment.status == "booked", Appointment.start_at >= tomorrow,
                                                                   Appointment.start_at < tomorrow + timedelta(days=1))) or 0
    return {"today": today, "upcoming": upcoming, "tomorrow_count": tomorrow_n,
            "todo": {"overdue_deposits": [overdue[0], int(overdue[1])], "deposits_without_appointment": [no_appt[0], int(no_appt[1])],
                     "unpaid_invoices": [unpaid[0], int(unpaid[1])], "past_open_appointments": past_open,
                     "unknown_time_appointments": unknown_time}}


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
    for at, amt in db.execute(select(Deposit.received_at, Deposit.amount).where(Deposit.received_at.between(s, e), REAL_DEPOSIT)):
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


# ------------------------------------------------------------------ revenue by line / staff / service
J_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def _jmonth(day: str, cache: dict) -> str:
    if day not in cache:
        from .jalali import gregorian_to_jalali

        y, m, d = (int(x) for x in day[:10].split("-"))
        jy, jm, _ = gregorian_to_jalali(y, m, d)
        cache[day] = f"{jy:04d}-{jm:02d}"
    return cache[day]


def _jlabel(key: str) -> str:
    jy, jm = key.split("-")
    return f"{J_MONTHS[int(jm) - 1]} {jy}"


def _revenue_rows(db: Session, s: datetime, e: datetime, source: str = "all") -> list[dict]:
    """Every sale in the period at day x line x staff x service level (sums), from invoices here and from the
    service history brought over from the previous software. Amounts are the full price paid (no staff share deducted)."""
    rows: list[dict] = []
    if source in ("all", "new"):
        amt = func.coalesce(InvoiceItem.net_amount, InvoiceItem.unit_price * InvoiceItem.quantity - InvoiceItem.discount)
        q = (select(func.date(Invoice.issued_at), InvoiceItem.line_id, InvoiceItem.staff_id, InvoiceItem.service_id,
                    InvoiceItem.description, func.sum(amt), func.sum(InvoiceItem.quantity))
             .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
             .where(Invoice.issued_at.between(s, e), Invoice.status != "void")
             .group_by(func.date(Invoice.issued_at), InvoiceItem.line_id, InvoiceItem.staff_id, InvoiceItem.service_id,
                       InvoiceItem.description))
        for day, line_id, staff_id, service_id, desc, total, n in db.execute(q):
            rows.append({"day": str(day), "line_id": line_id, "staff_id": staff_id, "service_id": service_id, "name": desc,
                         "amount": int(total or 0), "count": int(n or 0), "old": False})
    if source in ("all", "old"):
        q = (select(func.date(Appointment.start_at), Service.line_id, Appointment.staff_id, Appointment.service_id,
                    func.sum(Appointment.quoted_price), func.count(Appointment.id))
             .outerjoin(Service, Service.id == Appointment.service_id)
             .where(Appointment.status == "done", Appointment.invoice_id.is_(None), Appointment.start_at.between(s, e))
             .group_by(func.date(Appointment.start_at), Service.line_id, Appointment.staff_id, Appointment.service_id))
        for day, line_id, staff_id, service_id, total, n in db.execute(q):
            rows.append({"day": str(day), "line_id": line_id, "staff_id": staff_id, "service_id": service_id, "name": None,
                         "amount": int(total or 0), "count": int(n or 0), "old": True})
    return rows


def revenue_breakdown(db: Session, start: date | None = None, end: date | None = None, line_id: int | None = None,
                      staff_id: int | None = None, source: str = "all") -> dict:
    """Revenue per Jalali month, per line, per staff member and per service."""
    if start is None:
        first = [x for x in (db.scalar(select(func.min(Invoice.issued_at)).where(Invoice.status != "void")),
                             db.scalar(select(func.min(Appointment.start_at)).where(Appointment.status == "done"))) if x]
        start = min(first).date() if first else date.today()
    s, e = _range(start, end)
    services = {x.id: x for x in db.scalars(select(Service))}
    lines = {x.id: x for x in db.scalars(select(ServiceLine))}
    people = {x.id: x for x in db.scalars(select(Staff))}
    # a line run by a single person: their services are attributed to them when the record has no staff
    by_line_staff: dict[int, list[int]] = defaultdict(list)
    for p in people.values():
        if p.line_id and p.is_active:
            by_line_staff[p.line_id].append(p.id)
    cache: dict = {}
    months: dict[str, dict] = {}
    agg_line: dict[int, dict] = {}
    agg_staff: dict[int, dict] = {}
    agg_svc: dict[str, dict] = {}
    total = {"revenue": 0, "count": 0, "old": 0, "new": 0, "inferred_staff": 0}
    for r in _revenue_rows(db, s, e, source):
        svc = services.get(r["service_id"])
        lid = r["line_id"] or (svc.line_id if svc else None) or (people[r["staff_id"]].line_id if r["staff_id"] in people else None)
        sid = r["staff_id"]
        inferred = False
        if not sid and lid and len(by_line_staff.get(lid, [])) == 1:
            sid, inferred = by_line_staff[lid][0], True
        if line_id and lid != line_id:
            continue
        if staff_id is not None and (sid or 0) != staff_id:
            continue
        amt, n = r["amount"], r["count"]
        mk = _jmonth(r["day"], cache)
        m = months.setdefault(mk, {"key": mk, "label": _jlabel(mk), "total": 0, "count": 0, "by_line": defaultdict(int)})
        lname = lines[lid].name if lid in lines else "بدون لاین"
        m["total"] += amt
        m["count"] += n
        m["by_line"][lname] += amt
        L = agg_line.setdefault(lid or 0, {"id": lid or 0, "code": lines[lid].code if lid in lines else None, "name": lname,
                                           "color": lines[lid].color if lid in lines else "#94a3b8", "revenue": 0, "count": 0,
                                           "months": defaultdict(int)})
        L["revenue"] += amt
        L["count"] += n
        L["months"][mk] += amt
        P = agg_staff.setdefault(sid or 0, {"id": sid or 0, "name": people[sid].full_name if sid in people else "بدون پرسنل",
                                            "line": lines[people[sid].line_id].name if sid in people and people[sid].line_id in lines else None,
                                            "revenue": 0, "count": 0, "inferred": 0, "last": None, "months": defaultdict(int)})
        P["revenue"] += amt
        P["count"] += n
        P["inferred"] += n if inferred else 0
        P["months"][mk] += amt
        P["last"] = max(P["last"] or r["day"], r["day"])
        key = str(r["service_id"]) if r["service_id"] else f"name:{r['name'] or 'نامشخص'}"
        S = agg_svc.setdefault(key, {"id": r["service_id"], "code": svc.code if svc else None, "name": svc.name if svc else (r["name"] or "خدمت نامشخص"),
                                     "line": lname, "revenue": 0, "count": 0, "last": None, "old": 0,
                                     "archived": bool(svc and not svc.is_active)})
        S["revenue"] += amt
        S["count"] += n
        S["old"] += amt if r["old"] else 0
        S["last"] = max(S["last"] or r["day"], r["day"])
        total["revenue"] += amt
        total["count"] += n
        total["old" if r["old"] else "new"] += amt
        total["inferred_staff"] += n if inferred else 0
    keys = sorted(months)
    rev = total["revenue"] or 1

    def finish(d: dict) -> dict:
        d = {**d, "share": round(d["revenue"] / rev * 100, 1), "avg": d["revenue"] // d["count"] if d["count"] else 0}
        if "months" in d:
            d["months"] = [d["months"].get(k, 0) for k in keys]
        return d
    return {
        "period": {"start": s.date().isoformat(), "end": e.date().isoformat()},
        "totals": total,
        "months": [{**months[k], "by_line": dict(months[k]["by_line"])} for k in keys],
        "month_keys": keys,
        "lines": sorted((finish(x) for x in agg_line.values()), key=lambda x: -x["revenue"]),
        "staff": sorted((finish(x) for x in agg_staff.values()), key=lambda x: -x["revenue"]),
        "services": sorted((finish(x) for x in agg_svc.values()), key=lambda x: -x["revenue"]),
    }


def staff_revenue_detail(db: Session, staff_id: int, start: date | None = None, end: date | None = None,
                         limit: int = 100, offset: int = 0) -> dict:
    """One staff member: the breakdown plus the individual services (date, customer, service, amount, source)."""
    summary = revenue_breakdown(db, start, end, staff_id=staff_id)
    s, e = _range(date.fromisoformat(summary["period"]["start"]), end)
    person = db.get(Staff, staff_id) if staff_id else None
    line_staff = []
    if person and person.line_id:
        line_staff = [p.id for p in db.scalars(select(Staff).where(Staff.line_id == person.line_id, Staff.is_active.is_(True)))]
    sole = person is not None and line_staff == [person.id]
    svc_ids_of_line = select(Service.id).where(Service.line_id == person.line_id) if person and person.line_id else None
    amt = func.coalesce(InvoiceItem.net_amount, InvoiceItem.unit_price * InvoiceItem.quantity - InvoiceItem.discount)
    item_q = (select(Invoice.issued_at, Customer.full_name, Customer.legacy_code, InvoiceItem.description, amt, Invoice.number)
              .join(Invoice, Invoice.id == InvoiceItem.invoice_id).join(Customer, Customer.id == Invoice.customer_id)
              .where(Invoice.issued_at.between(s, e), Invoice.status != "void"))
    hist_q = (select(Appointment.start_at, Customer.full_name, Customer.legacy_code, Service.name, Appointment.quoted_price, Appointment.notes)
              .join(Customer, Customer.id == Appointment.customer_id).outerjoin(Service, Service.id == Appointment.service_id)
              .where(Appointment.status == "done", Appointment.invoice_id.is_(None), Appointment.start_at.between(s, e)))
    if staff_id:
        if sole and svc_ids_of_line is not None:  # unassigned records of their (single-person) line count as theirs
            item_q = item_q.where((InvoiceItem.staff_id == staff_id) | (InvoiceItem.staff_id.is_(None) & InvoiceItem.service_id.in_(svc_ids_of_line)))
            hist_q = hist_q.where((Appointment.staff_id == staff_id) | (Appointment.staff_id.is_(None) & Appointment.service_id.in_(svc_ids_of_line)))
        else:
            item_q = item_q.where(InvoiceItem.staff_id == staff_id)
            hist_q = hist_q.where(Appointment.staff_id == staff_id)
    else:
        item_q = item_q.where(InvoiceItem.staff_id.is_(None))
        hist_q = hist_q.where(Appointment.staff_id.is_(None))
    records = [{"at": at.isoformat(timespec="minutes"), "customer": c, "code": code, "service": svc, "amount": int(a or 0),
                "source": "new", "ref": num} for at, c, code, svc, a, num in db.execute(item_q)]
    for at, c, code, svc, a, notes in db.execute(hist_q):
        name = svc or ((notes or "").split("خدمت: ", 1)[1].split(" - ")[0] if "خدمت: " in (notes or "") else "نامشخص")
        records.append({"at": at.isoformat(timespec="minutes"), "customer": c, "code": code, "service": name, "amount": int(a or 0),
                        "source": "old", "ref": None})
    records.sort(key=lambda x: x["at"], reverse=True)
    return {**summary, "staff": person.full_name if person else "بدون پرسنل", "records_total": len(records),
            "records": records[offset:offset + limit]}


def day_details(db: Session, day: date | None = None) -> dict:
    """Behind the dashboard's 'today' cards: the day's invoices, every money movement and the customers served."""
    day = day or date.today()
    s, e = datetime.combine(day, datetime.min.time()), datetime.combine(day, datetime.max.time())
    accounts = {a.id: a.name for a in db.scalars(select(PaymentAccount))}
    ids = select(Invoice.customer_id).where(Invoice.issued_at.between(s, e)).union(
        select(Payment.customer_id).where(Payment.paid_at.between(s, e)),
        select(Deposit.customer_id).where(Deposit.received_at.between(s, e)),
        select(Appointment.customer_id).where(Appointment.start_at.between(s, e)),
        select(Customer.id).where(Customer.created_at.between(s, e)))
    names = {c.id: (c.full_name, c.legacy_code, c.mobile) for c in db.scalars(select(Customer).where(Customer.id.in_(ids)))}
    invs = db.scalars(select(Invoice).where(Invoice.issued_at.between(s, e)).options(selectinload(Invoice.items))
                      .order_by(Invoice.issued_at)).all()
    invoices = [{"id": i.id, "number": i.number, "at": i.issued_at.isoformat(timespec="minutes"), "customer": names.get(i.customer_id, ("",))[0],
                 "items": [it.description for it in i.items], "total": i.total, "paid": i.paid, "status": i.status} for i in invs]
    money_rows = []
    for p in db.scalars(select(Payment).where(Payment.paid_at.between(s, e)).order_by(Payment.paid_at)):
        kind = {"refund": "refund", "void_cancel": "void_cancel"}.get(p.source, "payment")
        inv = db.get(Invoice, p.invoice_id) if p.invoice_id else None
        money_rows.append({"at": p.paid_at.isoformat(timespec="minutes"), "kind": kind, "amount": p.amount,
                           "account": accounts.get(p.payment_account_id), "customer": names.get(p.customer_id, ("",))[0],
                           "ref": inv.number if inv else None})
    for d in db.scalars(select(Deposit).where(Deposit.received_at.between(s, e), REAL_DEPOSIT).order_by(Deposit.received_at)):
        money_rows.append({"at": d.received_at.isoformat(timespec="minutes"), "kind": "deposit", "amount": d.amount,
                           "account": accounts.get(d.payment_account_id), "customer": names.get(d.customer_id, ("",))[0], "ref": None})
    money_rows.sort(key=lambda x: x["at"])
    by_account: dict[str, int] = defaultdict(int)
    for r in money_rows:
        by_account[r["account"] or "?"] += r["amount"]
    # customers: invoiced today, or with an appointment today, or registered today
    served: dict[int, dict] = {}
    for i in invs:
        if i.status == "void":
            continue
        c = served.setdefault(i.customer_id, {"id": i.customer_id, "services": [], "total": 0, "appointment": None})
        c["services"] += [it.description for it in i.items]
        c["total"] += i.total
    for a in db.scalars(select(Appointment).where(Appointment.start_at.between(s, e), Appointment.status.in_(("booked", "done", "no_show")))):
        c = served.setdefault(a.customer_id, {"id": a.customer_id, "services": [], "total": 0, "appointment": None})
        c["appointment"] = {"at": a.start_at.isoformat(timespec="minutes"), "status": a.status, "time_unknown": bool(a.time_unknown)}
    new_ids = set(db.scalars(select(Customer.id).where(Customer.created_at.between(s, e), Customer.source != "import")))
    for cid in new_ids:
        served.setdefault(cid, {"id": cid, "services": [], "total": 0, "appointment": None})
    customers = [{**c, "name": names.get(c["id"], ("",))[0], "code": names.get(c["id"], ("", None))[1],
                  "mobile": names.get(c["id"], ("", None, None))[2], "new": c["id"] in new_ids} for c in served.values()]
    customers.sort(key=lambda x: (-x["total"], x["name"]))
    valid = [i for i in invs if i.status != "void"]
    return {"date": day.isoformat(), "invoices": invoices, "sales": sum(i.total for i in valid), "invoice_count": len(valid),
            "money": money_rows, "money_total": sum(r["amount"] for r in money_rows),
            "by_account": sorted(({"name": k, "value": v} for k, v in by_account.items()), key=lambda x: -x["value"]),
            "customers": customers}
