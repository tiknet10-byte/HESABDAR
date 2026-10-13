"""Reports, analytics, forecasting and automatic insights."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from .textutil import toman
from ..models import (
    Appointment,
    AuditLog,
    Customer,
    Deposit,
    Expense,
    Invoice,
    InvoiceItem,
    JournalEntry,
    Payment,
    PaymentAccount,
    Service,
    ServiceLine,
    Staff,
    StockMove,
    User,
)


def _range(start: date | None, end: date | None) -> tuple[datetime, datetime]:
    end = end or date.today()
    start = start or (end - timedelta(days=29))
    return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.max.time())


# money that really came in during a period: deposits brought over from the previous software are opening
# balances (received in the old system), so they never count as cash received here
REAL_DEPOSIT = Deposit.source.not_in(("import", "void_credit"))  # void_credit: already counted as the invoice payment
# money of a website order that the website later gave back is booked as "received with the order, paid back on the
# refund's day" (a deposit of source woocommerce): it is the order's money (invoice payment / invoice refund), not a deposit
SITE_REFUND = "woocommerce"

# customers brought over from other software (Chehreh, Tizpardaz) are not "new" on the day they were imported
IMPORTED_CUSTOMERS = ("import", "tizpardaz")

# product sales are reported as a line of their own (they have no service line)
PRODUCTS_LINE = -1
PRODUCTS_NAME = "فروش محصولات"
PRODUCTS_COLOR = "#0ea5e9"


def _old_service():  # noqa: ANN202
    """A done appointment without an invoice is a service of the previous software only when it was brought over by
    the import (its note starts with the import's mark); one set to 'done' by hand has no money behind it."""
    from .legacy_import import IMPORT_NOTE

    return (Appointment.status == "done") & Appointment.invoice_id.is_(None) & Appointment.notes.startswith(IMPORT_NOTE, autoescape=True)


def _undo():  # noqa: ANN202
    """When the sale of each voided invoice was taken back in the books (the date of its reversing entries): on the
    invoice's own date when it was cancelled as a mistake (it never happened), on the day of the void when it was
    returned (refund / kept as credit). The reports follow the ledger: such a sale stays in its own day and month,
    and the return is counted (negative) on the day it happened."""
    return (select(JournalEntry.ref_id.label("invoice_id"), func.min(JournalEntry.at).label("at"))
            .where(JournalEntry.ref_type == "invoice_void").group_by(JournalEntry.ref_id).subquery())


def _sold(q, s: datetime, e: datetime, undo):  # noqa: ANN001, ANN202
    """Invoices sold in [s, e] (q must select from / join Invoice): all of them except those cancelled as a mistake."""
    return q.outerjoin(undo, undo.c.invoice_id == Invoice.id).where(
        Invoice.issued_at.between(s, e), (Invoice.status != "void") | (undo.c.at != Invoice.issued_at))


def _returned(q, s: datetime, e: datetime, undo):  # noqa: ANN001, ANN202
    """Invoices returned in [s, e]: voided after their own day (q must select from / join Invoice)."""
    return q.join(undo, undo.c.invoice_id == Invoice.id).where(
        Invoice.status == "void", undo.c.at.between(s, e), undo.c.at != Invoice.issued_at)


def product_cogs(db: Session, s: datetime, e: datetime) -> int:
    """Cost of the products sold on invoices in the period minus the cost of those returned in it - as booked (5400):
    each sale on its invoice's date, each return on its own date."""
    signed = case((StockMove.kind == "sale_return", -StockMove.cost), else_=StockMove.cost)
    return int(db.scalar(select(func.coalesce(func.sum(signed), 0)).where(
        StockMove.kind.in_(("sale", "sale_return")), StockMove.invoice_item_id.is_not(None), StockMove.at.between(s, e))) or 0)


def history_cogs(db: Session, s: datetime, e: datetime) -> int:
    """Cost of the product sales brought over from other software (worked out when they were imported)."""
    from ..models import TradeHistory

    signed = case((TradeHistory.kind == "sale_return", -TradeHistory.cost), else_=TradeHistory.cost)
    return int(db.scalar(select(func.coalesce(func.sum(signed), 0)).where(TradeHistory.kind.in_(("sale", "sale_return")),
                                                                          TradeHistory.at.between(s, e))) or 0)


# money that went back to customers, and corrections of money wrongly recorded as received
OUT_KINDS = {"deposit_refund": "استرداد بیعانه", "refund": "برگشت پول فاکتور", "void_cancel": "لغو دریافتِ فاکتور اشتباه"}


def money_movements(db: Session, s: datetime, e: datetime) -> list[dict]:
    """Every real money movement of customers in a period, signed (+ in, - out), with its account:
    invoice payments, deposits received, deposits refunded, invoice money paid back and cancelled mistaken receipts.
    Money a website order paid and the website gave back later is order money: a payment, then an invoice refund."""
    accounts = {a.id: a.name for a in db.scalars(select(PaymentAccount))}
    rows: list[dict] = []
    for p in db.scalars(select(Payment).where(Payment.paid_at.between(s, e))):
        kind = p.source if p.source in ("refund", "void_cancel") else "payment"
        rows.append({"at": p.paid_at, "kind": kind, "amount": int(p.amount), "account_id": p.payment_account_id,
                     "customer_id": p.customer_id, "invoice_id": p.invoice_id, "deposit_id": None})
    for d in db.scalars(select(Deposit).where(Deposit.received_at.between(s, e), REAL_DEPOSIT)):
        rows.append({"at": d.received_at, "kind": "payment" if d.source == SITE_REFUND else "deposit", "amount": int(d.amount),
                     "account_id": d.payment_account_id, "customer_id": d.customer_id, "invoice_id": None, "deposit_id": d.id})
    # a refunded deposit is money paid out on the day of the refund (also one brought over from the old software)
    for d in db.scalars(select(Deposit).where(Deposit.status == "refunded", Deposit.closed_at.between(s, e))):
        rows.append({"at": d.closed_at, "kind": "refund" if d.source == SITE_REFUND else "deposit_refund", "amount": -int(d.amount),
                     "account_id": d.refund_account_id or d.payment_account_id,
                     "customer_id": d.customer_id, "invoice_id": None, "deposit_id": d.id})
    for r in rows:
        r["account"] = accounts.get(r["account_id"])
    rows.sort(key=lambda x: x["at"])
    return rows


def _per_account(rows: list[dict]) -> list[dict]:
    """Per account: money in (mistaken receipts already taken off), money paid back, and the net."""
    acc: dict[str, dict] = {}
    for r in rows:
        a = acc.setdefault(r["account"] or "?", {"name": r["account"] or "?", "in": 0, "out": 0})
        if r["kind"] in ("deposit_refund", "refund"):
            a["out"] += -r["amount"]
        else:
            a["in"] += r["amount"]
    return sorted(({**a, "value": a["in"] - a["out"]} for a in acc.values()), key=lambda x: -x["in"])


# a website order's shipping / tax / fee rows belong to the product sale (booked on 4200), not to any service line
NOT_EXTRA = func.coalesce(InvoiceItem.kind, "") != "extra"


def _product_side(it: InvoiceItem) -> bool:
    return bool(it.product_id) or it.kind == "extra"


def _net(it: InvoiceItem, inv: Invoice) -> int:
    """An item's amount after its share of the invoice discount (as booked); older items without it: proportional."""
    if it.net_amount is not None:
        return int(it.net_amount)
    return round(it.amount * inv.total / inv.subtotal) if inv.subtotal else it.amount


def _profit(db: Session, s: datetime, e: datetime) -> dict:
    from .capital import profit_breakdown

    return profit_breakdown(db, s, e + timedelta(microseconds=1))


def receivable_total(db: Session) -> int:
    """What customers owe, from the books (1200): unpaid invoices and debts brought over (opening balances) alike.
    A customer whose account is in credit does not lower what the others owe."""
    from ..models import JournalLine
    from .accounting import AR, account

    per = (select(func.sum(JournalLine.debit - JournalLine.credit).label("amt")).where(JournalLine.account_id == account(db, AR).id)
           .group_by(JournalLine.customer_id).subquery())
    return int(db.scalar(select(func.coalesce(func.sum(per.c.amt), 0)).where(per.c.amt > 0)) or 0)


def summary(db: Session, start: date | None = None, end: date | None = None) -> dict:
    s, e = _range(start, end)
    undo = _undo()
    # sales of the period, and invoices of earlier days returned (voided) in it: negative, on the day of the return
    invoices = db.scalars(_sold(select(Invoice), s, e, undo).options(selectinload(Invoice.items))).all()
    returned = db.scalars(_returned(select(Invoice), s, e, undo).options(selectinload(Invoice.items))).all()
    signed = [(i, 1) for i in invoices] + [(i, -1) for i in returned]
    gross = sum(i.total for i in invoices)
    returns = sum(i.total for i in returned)
    revenue = gross - returns
    discounts = sum(k * (i.discount + sum(it.discount for it in i.items)) for i, k in signed)
    expenses = int(db.scalar(select(func.coalesce(func.sum(Expense.amount), 0)).where(Expense.spent_at.between(s, e))) or 0)
    moves = money_movements(db, s, e)
    received_deposits = sum(r["amount"] for r in moves if r["kind"] == "deposit")
    paid_back = -sum(r["amount"] for r in moves if r["kind"] in ("deposit_refund", "refund"))
    held = int(db.scalar(select(func.coalesce(func.sum(Deposit.amount), 0)).where(Deposit.status == "held")) or 0)
    held_count = db.scalar(select(func.count(Deposit.id)).where(Deposit.status == "held")) or 0
    receivable = receivable_total(db)

    customer_ids = {i.customer_id for i in invoices}
    # customers brought over from other software are not "new" on the day they were imported
    new_customers = db.scalar(select(func.count(Customer.id)).where(Customer.created_at.between(s, e),
                                                                    Customer.source.not_in(IMPORTED_CUSTOMERS))) or 0
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
    for inv, k in signed:
        for it in inv.items:
            amt = k * _net(it, inv)
            by_line[PRODUCTS_NAME if _product_side(it) else lines[it.line_id].name if it.line_id in lines else "سایر"] += amt
            key = it.description or "—"
            d = by_service.setdefault(key, {"name": key, "count": 0, "revenue": 0})
            d["count"] += k * it.quantity
            d["revenue"] += amt
            if it.staff_id:
                by_staff[staff.get(it.staff_id, "?")] += amt

    by_account = _per_account(moves)

    expense_by_cat: dict[str, int] = defaultdict(int)
    for cat, amt in db.execute(select(Expense.category, Expense.amount).where(Expense.spent_at.between(s, e))):
        expense_by_cat[cat] += int(amt)

    appts = db.execute(select(Appointment.status, func.count()).where(Appointment.start_at.between(s, e)).group_by(Appointment.status)).all()
    appt_stats = {k: v for k, v in appts}
    total_appts = sum(appt_stats.values()) or 0

    return {
        "period": {"start": s.date().isoformat(), "end": e.date().isoformat()},
        # sales of the period minus invoices returned in it (as in the books: a return is counted on its own day)
        "revenue": revenue,
        "gross_sales": gross,
        "returns": returns,
        "returns_count": len(returned),
        "expenses": expenses,
        # gross profit of products = their sales - what they cost (COGS); services have no stock cost
        "cogs": (cogs := product_cogs(db, s, e)),
        "product_revenue": (prod_rev := sum(k * _net(it, i) for i, k in signed for it in i.items if _product_side(it))),
        "product_profit": prod_rev - cogs,
        # forfeited deposits (no-shows): income of their line
        "forfeited": sum(int(x[2]) for x in forfeit_rows(db, s, e)),
        # profit straight from the books: every income (services, products, forfeits, gold/dollar gains) minus every
        # cost (goods sold, staff share, expenses, cash and stock shortages) dated in the period
        "profit": (profit := _profit(db, s, e)),
        "net_profit": profit["total"],
        # net money of customers: received minus paid back (a refunded deposit is not money kept)
        "cash_in": sum(r["amount"] for r in moves),
        "cash_received": sum(r["amount"] for r in moves if r["kind"] not in ("deposit_refund", "refund")),
        "cash_paid_back": paid_back,
        "deposits_received": received_deposits,
        "deposits_held": held,
        "deposits_held_count": held_count,
        "receivable": receivable,
        "discounts": discounts,
        "invoice_count": len(invoices),
        "avg_ticket": gross // len(invoices) if invoices else 0,
        "customers_served": len(customer_ids),
        "new_customers": new_customers,
        "returning_customers": returning,
        "by_line": sorted(({"name": k, "value": v} for k, v in by_line.items()), key=lambda x: -x["value"]),
        "by_account": by_account,
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
    no_appt = count(select(func.count(Deposit.id), func.coalesce(func.sum(Deposit.amount), 0))  # credits need no booking
                    .where(Deposit.status == "held", Deposit.appointment_id.is_(None), func.coalesce(Deposit.kind, "") != "credit"))
    unpaid = count(select(func.count(Invoice.id), func.coalesce(func.sum(Invoice.total - Invoice.paid), 0))
                   .where(Invoice.status.in_(("issued", "partial"))))
    past_open = db.scalar(select(func.count(Appointment.id)).where(Appointment.status == "booked", Appointment.start_at < day)) or 0
    unknown_time = db.scalar(select(func.count(Appointment.id)).where(Appointment.status == "booked", Appointment.time_unknown.is_(True),
                                                                     Appointment.start_at >= day)) or 0
    # booked although all of their deposits were refunded / forfeited (the customer most likely cancelled)
    has_dep = select(Deposit.appointment_id).where(Deposit.appointment_id.is_not(None))
    open_dep = select(Deposit.appointment_id).where(Deposit.appointment_id.is_not(None), Deposit.status.in_(("held", "applied")))
    deposit_gone = db.scalar(select(func.count(Appointment.id)).where(Appointment.status == "booked", Appointment.id.in_(has_dep),
                                                                     Appointment.id.not_in(open_dep))) or 0
    tomorrow_n = db.scalar(select(func.count(Appointment.id)).where(Appointment.status == "booked", Appointment.start_at >= tomorrow,
                                                                   Appointment.start_at < tomorrow + timedelta(days=1))) or 0
    return {"today": today, "upcoming": upcoming, "tomorrow_count": tomorrow_n,
            "todo": {"overdue_deposits": [overdue[0], int(overdue[1])], "deposits_without_appointment": [no_appt[0], int(no_appt[1])],
                     "unpaid_invoices": [unpaid[0], int(unpaid[1])], "past_open_appointments": past_open,
                     "unknown_time_appointments": unknown_time, "deposit_gone_appointments": deposit_gone}}


def daily_series(db: Session, start: date | None = None, end: date | None = None) -> list[dict]:
    s, e = _range(start, end)
    rev: dict[date, int] = defaultdict(int)
    exp: dict[date, int] = defaultdict(int)
    cash: dict[date, int] = defaultdict(int)
    undo = _undo()  # sales per day, minus the invoices returned that day (as summary())
    for total, at in db.execute(_sold(select(Invoice.total, Invoice.issued_at), s, e, undo)):
        rev[at.date()] += int(total)
    for total, at in db.execute(_returned(select(Invoice.total, undo.c.at), s, e, undo)):
        rev[at.date()] -= int(total)
    for at, amt in db.execute(select(Expense.spent_at, Expense.amount).where(Expense.spent_at.between(s, e))):
        exp[at.date()] += int(amt)
    # net money of customers per day: the same movements as summary()'s cash_in (refunded deposits included)
    for r in money_movements(db, s, e):
        cash[r["at"].date()] += r["amount"]
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
    # the days before the first sale (the program was not in use yet) are not "no sales"
    first = next((i for i, h in enumerate(hist) if h["revenue"]), len(hist))
    hist = hist[first:]
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
        if season[wd] <= 0:
            continue  # a weekday the salon is closed (e.g. Friday): says nothing about the level, forecast 0 for it
        x = h["revenue"] / season[wd]
        if level is None:
            level = x
            continue
        pred = (level + phi * trend) * (season[wd] or 1)
        residuals.append(h["revenue"] - pred)
        prev = level
        level = alpha * x + (1 - alpha) * (level + phi * trend)
        trend = beta * (level - prev) + (1 - beta) * phi * trend
    if level is None:  # no weekday with real sales (e.g. only sales returned on the same weekday): nothing to go on
        return {"method": "insufficient_data", "points": [], "total": 0,
                "message": "برای پیش‌بینی دقیق حداقل به ۷ روز داده فروش نیاز است"}
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
    # services only: products are a line of their own (revenue report), they have no staff and no staff share.
    # A return (invoice voided after its day) counts negative on its own day, as its sale and staff share in the books.
    undo = _undo()
    q = (select(InvoiceItem, Invoice.subtotal, Invoice.total).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
         .where(InvoiceItem.product_id.is_(None), NOT_EXTRA))
    rows = [(*r, 1) for r in db.execute(_sold(q, s, e, undo))] + [(*r, -1) for r in db.execute(_returned(q, s, e, undo))]
    per_staff: dict = {}
    per_line: dict = {}
    for it, subtotal, total, k in rows:
        net = it.net_amount if it.net_amount is not None else (round(it.amount * total / subtotal) if subtotal else it.amount)
        person = staff.get(it.staff_id)
        if it.commission_amount is not None:
            comm = it.commission_amount
        else:
            comm = round(net * (person.commission_percent or 0) / 100) if person else 0
        net, comm = k * net, k * comm
        key = it.staff_id or 0
        r = per_staff.setdefault(key, {"staff_id": it.staff_id, "name": person.full_name if person else "بدون پرسنل",
                                       "line": lines.get(person.line_id) if person and person.line_id else None,
                                       "percent": person.commission_percent if person else 0,
                                       "services": 0, "revenue": 0, "staff_share": 0, "salon_share": 0})
        r["services"] += k * it.quantity
        r["revenue"] += net
        r["staff_share"] += comm
        r["salon_share"] += net - comm
        lk = it.line_id or 0
        lr = per_line.setdefault(lk, {"line_id": it.line_id, "name": lines.get(it.line_id, "سایر"), "services": 0,
                                      "revenue": 0, "staff_share": 0, "salon_share": 0, "forfeits": 0})
        lr["services"] += k * it.quantity
        lr["revenue"] += net
        lr["staff_share"] += comm
        lr["salon_share"] += net - comm
    # forfeited deposits (no-shows): income of their line that is all the salon's (no staff share); shown on its own
    for _, line_id, amount in forfeit_rows(db, s, e):
        lr = per_line.setdefault(line_id or 0, {"line_id": line_id, "name": lines.get(line_id, "سایر"), "services": 0,
                                                "revenue": 0, "staff_share": 0, "salon_share": 0, "forfeits": 0})
        lr["forfeits"] += int(amount)
    # payouts in the period and outstanding balances
    payable = account(db, STAFF_PAYABLE)
    paid = dict(db.execute(select(JournalLine.staff_id, func.sum(JournalLine.debit))
                           .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                           .where(JournalLine.account_id == payable.id, JournalEntry.ref_type == "staff_payout",
                                  JournalEntry.at.between(s, e)).group_by(JournalLine.staff_id)).all())
    balances = {pid: staff_balance(db, pid) for pid in staff}
    for pid, p in staff.items():
        # also someone no longer active who is still owed money or was paid in the period (to settle with them)
        if pid not in per_staff and (p.is_active or balances[pid] or paid.get(pid)):
            per_staff[pid] = {"staff_id": pid, "name": p.full_name, "line": lines.get(p.line_id), "percent": p.commission_percent,
                              "services": 0, "revenue": 0, "staff_share": 0, "salon_share": 0}
    for r in per_staff.values():
        r["paid"] = int(paid.get(r["staff_id"]) or 0) if r["staff_id"] else 0
        r["balance"] = balances.get(r["staff_id"], 0) if r["staff_id"] else 0
        r["active"] = bool(staff[r["staff_id"]].is_active) if r["staff_id"] in staff else True
    staff_rows = sorted(per_staff.values(), key=lambda x: -x["revenue"])
    totals = {k: sum(r[k] for r in staff_rows) for k in ("revenue", "staff_share", "salon_share", "paid", "balance")}
    totals["forfeits"] = sum(r["forfeits"] for r in per_line.values())
    return {"period": {"start": s.date().isoformat(), "end": e.date().isoformat()}, "staff": staff_rows,
            "lines": sorted(per_line.values(), key=lambda x: -(x["revenue"] + x["forfeits"])), "totals": totals}


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


FORFEIT_NAME = "بیعانهٔ سوخت‌شده (عدم مراجعه)"


def forfeit_rows(db: Session, s: datetime, e: datetime) -> list[tuple]:
    """(day, line id, amount) of forfeited-deposit income booked in the period."""
    from ..models import JournalEntry, JournalLine, LedgerAccount

    accs = select(LedgerAccount.id).where((LedgerAccount.code == "4800") | (LedgerAccount.parent_code == "4800"))
    q = (select(func.date(JournalEntry.at), JournalLine.line_id, func.sum(JournalLine.credit - JournalLine.debit))
         .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
         .where(JournalLine.account_id.in_(accs), JournalEntry.at.between(s, e))
         .group_by(func.date(JournalEntry.at), JournalLine.line_id))
    return [r for r in db.execute(q) if r[2]]


def _revenue_rows(db: Session, s: datetime, e: datetime, source: str = "all") -> list[dict]:
    """Every sale in the period at day x line x staff x service level (sums), from invoices here and from the
    service history brought over from the previous software. Amounts are the full price paid (no staff share deducted)."""
    rows: list[dict] = []
    if source in ("all", "new"):
        amt = func.coalesce(InvoiceItem.net_amount, InvoiceItem.unit_price * InvoiceItem.quantity - InvoiceItem.discount)
        undo = _undo()
        # sales on their invoice's day; invoices returned (voided after their day) negative on the day of the return
        for when, filt, k in ((Invoice.issued_at, _sold, 1), (undo.c.at, _returned, -1)):
            day_of = func.date(when)
            q = filt(select(day_of, InvoiceItem.line_id, InvoiceItem.staff_id, InvoiceItem.service_id,
                            InvoiceItem.product_id, InvoiceItem.description, func.sum(amt), func.sum(InvoiceItem.quantity),
                            InvoiceItem.kind)
                     .select_from(InvoiceItem).join(Invoice, Invoice.id == InvoiceItem.invoice_id), s, e, undo)
            q = q.group_by(day_of, InvoiceItem.line_id, InvoiceItem.staff_id, InvoiceItem.service_id, InvoiceItem.product_id,
                           InvoiceItem.description, InvoiceItem.kind)
            for day, line_id, staff_id, service_id, product_id, desc, total, n, kind in db.execute(q):
                rows.append({"day": str(day), "line_id": PRODUCTS_LINE if product_id or kind == "extra" else line_id, "staff_id": staff_id,
                             "service_id": service_id, "product_id": product_id, "name": desc,
                             "amount": k * int(total or 0), "count": k * int(n or 0), "old": False, "returned": k < 0})
    if source in ("all", "new"):
        # forfeited deposits: income of their service line (from the ledger: 4800 and its per-line accounts)
        for day, line_id, total in forfeit_rows(db, s, e):
            rows.append({"day": str(day), "line_id": line_id, "staff_id": None, "service_id": None, "product_id": None,
                         "name": FORFEIT_NAME, "amount": int(total or 0), "count": 0, "old": False, "forfeit": True})
    if source in ("all", "old"):
        q = (select(func.date(Appointment.start_at), Service.line_id, Appointment.staff_id, Appointment.service_id,
                    func.sum(Appointment.quoted_price), func.count(Appointment.id))
             .outerjoin(Service, Service.id == Appointment.service_id)
             .where(_old_service(), Appointment.start_at.between(s, e))
             .group_by(func.date(Appointment.start_at), Service.line_id, Appointment.staff_id, Appointment.service_id))
        for day, line_id, staff_id, service_id, total, n in db.execute(q):
            rows.append({"day": str(day), "line_id": line_id, "staff_id": staff_id, "service_id": service_id, "name": None,
                         "amount": int(total or 0), "count": int(n or 0), "old": True})
        # product sales brought over (Tizpardaz, and products sold in Chehreh): the products line
        from ..models import TradeHistory
        net = func.coalesce(TradeHistory.net_amount, TradeHistory.amount)  # after its invoice's discount
        signed = case((TradeHistory.kind == "sale_return", -net), else_=net)
        qty = case((TradeHistory.kind == "sale_return", -TradeHistory.qty), else_=TradeHistory.qty)
        q = (select(func.date(TradeHistory.at), TradeHistory.product_id, TradeHistory.description, func.sum(signed), func.sum(qty))
             .where(TradeHistory.kind.in_(("sale", "sale_return")), TradeHistory.at.between(s, e))
             .group_by(func.date(TradeHistory.at), TradeHistory.product_id, TradeHistory.description))
        for day, product_id, desc, total, n in db.execute(q):
            rows.append({"day": str(day), "line_id": PRODUCTS_LINE, "staff_id": None, "service_id": None, "product_id": product_id,
                         "name": desc, "amount": int(total or 0), "count": int(n or 0), "old": True})
    return rows


def revenue_breakdown(db: Session, start: date | None = None, end: date | None = None, line_id: int | None = None,
                      staff_id: int | None = None, source: str = "all") -> dict:
    """Revenue per Jalali month, per line, per staff member and per service."""
    if start is None:  # all records: from the first one of the chosen source (also product sales brought over)
        from ..models import JournalLine, LedgerAccount, TradeHistory

        qs = []
        if source in ("all", "new"):
            forfeits = select(LedgerAccount.id).where((LedgerAccount.code == "4800") | (LedgerAccount.parent_code == "4800"))
            qs += [select(func.min(Invoice.issued_at)),
                   select(func.min(JournalEntry.at)).join(JournalLine, JournalLine.entry_id == JournalEntry.id)
                   .where(JournalLine.account_id.in_(forfeits))]
        if source in ("all", "old"):
            qs += [select(func.min(Appointment.start_at)).where(_old_service()),
                   select(func.min(TradeHistory.at)).where(TradeHistory.kind.in_(("sale", "sale_return")))]
        first = [x for x in (db.scalar(q) for q in qs) if x]
        start = min(first).date() if first else date.today()
    s, e = _range(start, end)
    from types import SimpleNamespace

    services = {x.id: x for x in db.scalars(select(Service))}
    lines = {x.id: x for x in db.scalars(select(ServiceLine))}
    lines[PRODUCTS_LINE] = SimpleNamespace(id=PRODUCTS_LINE, name=PRODUCTS_NAME, code="P", color=PRODUCTS_COLOR)
    people = {x.id: x for x in db.scalars(select(Staff))}
    from ..models import Product

    products = {x.id: x for x in db.scalars(select(Product))}
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
    total = {"revenue": 0, "count": 0, "old": 0, "new": 0, "inferred_staff": 0, "forfeits": 0, "returns": 0}
    for r in _revenue_rows(db, s, e, source):
        svc = services.get(r["service_id"])
        lid = r["line_id"] or (svc.line_id if svc else None) or (people[r["staff_id"]].line_id if r["staff_id"] in people else None)
        sid = r["staff_id"]
        inferred = False
        forfeit = bool(r.get("forfeit"))
        if not sid and not forfeit and lid and lid != PRODUCTS_LINE and len(by_line_staff.get(lid, [])) == 1:
            sid, inferred = by_line_staff[lid][0], True
        if line_id and lid != line_id:
            continue
        if staff_id is not None and (forfeit or lid == PRODUCTS_LINE or (sid or 0) != staff_id):
            continue  # one person's detail (also "بدون پرسنل"): their services only, as in the staff table
        amt, n = r["amount"], r["count"]
        mk = _jmonth(r["day"], cache)
        m = months.setdefault(mk, {"key": mk, "label": _jlabel(mk), "total": 0, "count": 0, "by_line": defaultdict(int)})
        lname = lines[lid].name if lid in lines else "بدون لاین"
        m["total"] += amt
        m["count"] += n
        m["by_line"][lname] += amt
        L = agg_line.setdefault(lid or 0, {"id": lid or 0, "code": lines[lid].code if lid in lines else None, "name": lname,
                                           "color": lines[lid].color if lid in lines else "#94a3b8", "revenue": 0, "count": 0,
                                           "forfeits": 0, "months": defaultdict(int)})
        L["revenue"] += amt
        L["count"] += n
        L["months"][mk] += amt
        if forfeit:
            L["forfeits"] += amt
            total["forfeits"] += amt
        if lid == PRODUCTS_LINE or forfeit:  # products / forfeited deposits are nobody's service: not in the staff table
            P = {"revenue": 0, "count": 0, "inferred": 0, "months": defaultdict(int), "last": None}
        else:
            P = agg_staff.setdefault(sid or 0, {
                "id": sid or 0, "name": people[sid].full_name if sid in people else "بدون پرسنل",
                "line": lines[people[sid].line_id].name if sid in people and people[sid].line_id in lines else None,
                "revenue": 0, "count": 0, "inferred": 0, "last": None, "months": defaultdict(int)})
        P["revenue"] += amt
        P["count"] += n
        P["inferred"] += n if inferred else 0
        P["months"][mk] += amt
        back = bool(r.get("returned"))  # a return: not the last time it was done / sold
        P["last"] = P["last"] if back else max(P["last"] or r["day"], r["day"])
        prod = products.get(r.get("product_id")) if r.get("product_id") else None
        key = f"forfeit:{lid or 0}" if forfeit else f"p:{prod.id}" if prod else str(r["service_id"]) if r["service_id"] else f"name:{r['name'] or 'نامشخص'}"
        S = agg_svc.setdefault(key, {"id": r["service_id"], "product_id": prod.id if prod else None,
                                     "code": prod.code if prod else svc.code if svc else None,
                                     "name": prod.name if prod else svc.name if svc else (r["name"] or "خدمت نامشخص"),
                                     "line": lname, "revenue": 0, "count": 0, "last": None, "old": 0,
                                     "archived": bool(svc and not svc.is_active)})
        S["revenue"] += amt
        S["count"] += n
        S["old"] += amt if r["old"] else 0
        S["last"] = S["last"] if back else max(S["last"] or r["day"], r["day"])
        total["revenue"] += amt
        total["count"] += n
        total["old" if r["old"] else "new"] += amt
        total["inferred_staff"] += n if inferred else 0
        total["returns"] -= amt if back else 0
    keys = sorted(months)
    rev = total["revenue"] or 1
    if PRODUCTS_LINE in agg_line:  # products: what they cost and the gross profit (of the chosen source only)
        pl = agg_line[PRODUCTS_LINE]
        pl["cogs"] = ((product_cogs(db, s, e) if source in ("all", "new") else 0)
                      + (history_cogs(db, s, e) if source in ("all", "old") else 0))
        pl["profit"] = pl["revenue"] - pl["cogs"]
        pl["margin"] = round(pl["profit"] / pl["revenue"] * 100, 1) if pl["revenue"] else None

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
    undo = _undo()
    # services only (products are nobody's service); a return is listed negative on its own day, as in the totals
    item_q = (select(Invoice.issued_at, Customer.full_name, Customer.legacy_code, InvoiceItem.description, amt, Invoice.number, undo.c.at)
              .select_from(InvoiceItem).join(Invoice, Invoice.id == InvoiceItem.invoice_id).join(Customer, Customer.id == Invoice.customer_id)
              .where(InvoiceItem.product_id.is_(None), NOT_EXTRA))
    hist_q = (select(Appointment.start_at, Customer.full_name, Customer.legacy_code, Service.name, Appointment.quoted_price, Appointment.notes)
              .join(Customer, Customer.id == Appointment.customer_id).outerjoin(Service, Service.id == Appointment.service_id)
              .where(_old_service(), Appointment.start_at.between(s, e)))
    if staff_id:
        if sole and svc_ids_of_line is not None:  # unassigned records of their (single-person) line count as theirs
            of_line = (InvoiceItem.line_id == person.line_id) | (InvoiceItem.line_id.is_(None) & InvoiceItem.service_id.in_(svc_ids_of_line))
            item_q = item_q.where((InvoiceItem.staff_id == staff_id) | (InvoiceItem.staff_id.is_(None) & of_line))
            hist_q = hist_q.where((Appointment.staff_id == staff_id) | (Appointment.staff_id.is_(None) & Appointment.service_id.in_(svc_ids_of_line)))
        else:
            item_q = item_q.where(InvoiceItem.staff_id == staff_id)
            hist_q = hist_q.where(Appointment.staff_id == staff_id)
    else:
        # "بدون پرسنل": without the records of a single-person line (the table counts them as that person's)
        sole = [lid for lid, n in db.execute(select(Staff.line_id, func.count(Staff.id)).where(Staff.is_active.is_(True),
                                                                                             Staff.line_id.is_not(None)).group_by(Staff.line_id)) if n == 1]
        sole_svc = select(Service.id).where(Service.line_id.in_(sole))
        item_of_sole = ((InvoiceItem.line_id.is_not(None) & InvoiceItem.line_id.in_(sole))
                        | (InvoiceItem.line_id.is_(None) & InvoiceItem.service_id.is_not(None) & InvoiceItem.service_id.in_(sole_svc)))
        item_q = item_q.where(InvoiceItem.staff_id.is_(None), ~item_of_sole)
        hist_q = hist_q.where(Appointment.staff_id.is_(None), ~(Appointment.service_id.is_not(None) & Appointment.service_id.in_(sole_svc)))
    records = [{"at": at.isoformat(timespec="minutes"), "customer": c, "code": code, "service": svc, "amount": int(a or 0),
                "source": "new", "ref": num} for at, c, code, svc, a, num, _ in db.execute(_sold(item_q, s, e, undo))]
    records += [{"at": back.isoformat(timespec="minutes"), "customer": c, "code": code, "service": svc, "amount": -int(a or 0),
                 "source": "new", "ref": num, "returned": True} for _, c, code, svc, a, num, back in db.execute(_returned(item_q, s, e, undo))]
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
    inv_numbers = {i.id: i.number for i in db.scalars(select(Invoice).where(Invoice.id.in_(
        select(Payment.invoice_id).where(Payment.paid_at.between(s, e)))))}
    # the day's sales as summary() counts them: invoices of the day (not those cancelled as a mistake) minus
    # invoices of earlier days returned (voided) today
    undo = _undo()
    sold = set(db.scalars(_sold(select(Invoice.id), s, e, undo)))
    returned = db.execute(_returned(select(Invoice, undo.c.at), s, e, undo).order_by(undo.c.at)).all()
    moves = money_movements(db, s, e)
    extra = ({r["customer_id"] for r in moves} | {i.customer_id for i, _ in returned}) - names.keys()
    if extra:
        names.update({c.id: (c.full_name, c.legacy_code, c.mobile) for c in db.scalars(select(Customer).where(Customer.id.in_(extra)))})
    money_rows = [{"at": r["at"].isoformat(timespec="minutes"), "kind": r["kind"], "amount": r["amount"], "account": r["account"],
                   "customer": names.get(r["customer_id"], ("",))[0], "ref": inv_numbers.get(r["invoice_id"])} for r in moves]
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
    new_ids = set(db.scalars(select(Customer.id).where(Customer.created_at.between(s, e), Customer.source.not_in(IMPORTED_CUSTOMERS))))
    for cid in new_ids:
        served.setdefault(cid, {"id": cid, "services": [], "total": 0, "appointment": None})
    customers = [{**c, "name": names.get(c["id"], ("",))[0], "code": names.get(c["id"], ("", None))[1],
                  "mobile": names.get(c["id"], ("", None, None))[2], "new": c["id"] in new_ids} for c in served.values()]
    customers.sort(key=lambda x: (-x["total"], x["name"]))
    returns = [{"id": i.id, "number": i.number, "at": at.isoformat(timespec="minutes"), "issued_at": i.issued_at.isoformat(timespec="minutes"),
                "customer": names.get(i.customer_id, ("",))[0], "total": i.total} for i, at in returned]
    returns_total = sum(x["total"] for x in returns)
    return {"date": day.isoformat(), "invoices": invoices, "sales": sum(i.total for i in invs if i.id in sold) - returns_total,
            "invoice_count": len(sold), "returns": returns, "returns_total": returns_total,
            "money": money_rows, "money_total": sum(r["amount"] for r in money_rows),
            "money_in": sum(r["amount"] for r in money_rows if r["kind"] not in ("deposit_refund", "refund")),
            "money_out": -sum(r["amount"] for r in money_rows if r["kind"] in ("deposit_refund", "refund")),
            "by_account": _per_account(moves),
            "customers": customers}


def money_returned(db: Session, start: date | None = None, end: date | None = None) -> dict:
    """For the manager: every time money went back to a customer (deposit refunds, invoice refunds) and every
    mistaken receipt that was cancelled - who, how much, from which account, why, and which user did it."""
    s, e = _range(start, end)
    rows = [r for r in money_movements(db, s, e) if r["kind"] in OUT_KINDS]
    names = {c.id: (c.full_name, c.legacy_code) for c in db.scalars(select(Customer).where(Customer.id.in_({r["customer_id"] for r in rows})))}
    invs = {i.id: i for i in db.scalars(select(Invoice).where(Invoice.id.in_({r["invoice_id"] for r in rows if r["invoice_id"]})))}
    deps = {d.id: d for d in db.scalars(select(Deposit).where(Deposit.id.in_({r["deposit_id"] for r in rows if r["deposit_id"]})))}
    users = {u.id: u.full_name or u.username for u in db.scalars(select(User))}
    # who did it and why: the audit trail of the refund / the invoice void
    logs: dict[tuple[str, str], AuditLog] = {}
    keys = [("deposit.refund", str(r["deposit_id"])) for r in rows if r["deposit_id"]] + \
           [("invoice.void", str(r["invoice_id"])) for r in rows if r["invoice_id"]]
    if keys:
        for log in db.scalars(select(AuditLog).where(AuditLog.action.in_(("deposit.refund", "invoice.void")),
                                                      AuditLog.entity_id.in_({k[1] for k in keys})).order_by(AuditLog.id)):
            logs[(log.action, log.entity_id)] = log
    out = []
    for r in reversed(rows):
        log = logs.get(("deposit.refund", str(r["deposit_id"]))) if r["deposit_id"] else logs.get(("invoice.void", str(r["invoice_id"])))
        inv, dep = invs.get(r["invoice_id"]), deps.get(r["deposit_id"])
        out.append({"at": r["at"].isoformat(timespec="minutes"), "kind": r["kind"], "label": OUT_KINDS[r["kind"]], "amount": -r["amount"],
                    "account": r["account"], "customer": names.get(r["customer_id"], ("",))[0], "customer_code": names.get(r["customer_id"], ("", None))[1],
                    "invoice_id": r["invoice_id"], "invoice": inv.number if inv else None, "deposit_id": r["deposit_id"],
                    "deposit_received_at": dep.received_at.isoformat(timespec="minutes") if dep else None,
                    "reason": (log.data or {}).get("reason") if log else None,
                    "by": (users.get(log.user_id) if log.user_id else log.actor) if log else None})
    paid_back = [x for x in out if x["kind"] != "void_cancel"]
    return {"period": {"start": s.date().isoformat(), "end": e.date().isoformat()}, "items": out,
            "paid_back": sum(x["amount"] for x in paid_back), "paid_back_count": len(paid_back),
            "cancelled": sum(x["amount"] for x in out if x["kind"] == "void_cancel")}
