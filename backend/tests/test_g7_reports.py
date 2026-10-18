"""Reports agree with the ledger: returns on their own day, daily cash, website refunds, staff shares, receivables,
new customers, previous-software history, product cost per source, 'all records' start, forecast."""
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select

TODAY = date.today()


def _db():
    from app.core.db import SessionLocal

    return SessionLocal()


def ledger(prefixes: tuple[str, ...], day: date, end: date | None = None, credit: bool = True) -> int:
    """Movement of the accounts whose code starts with one of the prefixes, in entries dated in [day, end]."""
    from app.models import JournalEntry, JournalLine, LedgerAccount

    s, e = datetime.combine(day, time.min), datetime.combine(end or day, time.max)
    sign = (JournalLine.credit - JournalLine.debit) if credit else (JournalLine.debit - JournalLine.credit)
    with _db() as db:
        rows = db.execute(select(LedgerAccount.code, func.sum(sign)).join(JournalLine, JournalLine.account_id == LedgerAccount.id)
                          .join(JournalEntry, JournalEntry.id == JournalLine.entry_id).where(JournalEntry.at.between(s, e))
                          .group_by(LedgerAccount.code)).all()
    return int(sum(v for code, v in rows if code.startswith(prefixes)))


SALES = ("41", "42")  # services per line + products (not forfeits 48xx, not gold/currency 49xx)


def summ(client, d: date, end: date | None = None) -> dict:
    return client.get("/api/reports/summary", params={"start": d.isoformat(), "end": (end or d).isoformat()}).json()


def daily(client, d: date) -> dict:
    return client.get("/api/reports/daily", params={"start": d.isoformat(), "end": d.isoformat()}).json()[0]


def shares(client, d: date, end: date | None = None) -> dict:
    return client.get("/api/reports/staff-shares", params={"start": d.isoformat(), "end": (end or d).isoformat()}).json()


def staff_row(rep: dict, sid) -> dict:  # noqa: ANN001
    return next((r for r in rep["staff"] if r["staff_id"] == sid), {"revenue": 0, "staff_share": 0, "salon_share": 0, "services": 0})


def setup_line(client, name: str, price: int, percent: float | None = None):  # noqa: ANN201
    line = client.post("/api/lines", json={"name": name}).json()
    svc = client.post("/api/services", json={"line_id": line["id"], "name": f"خدمت {name}", "base_price": price}).json()
    person = client.post("/api/staff", json={"full_name": f"پرسنل {name}", "line_id": line["id"],
                                             "commission_percent": percent}).json() if percent is not None else None
    return line, svc, person


def test_g7_return_counts_on_its_own_day_like_the_ledger(client, accounts):
    """An invoice of 40 days ago returned (voided with refund) today: its sale stays in its own day, the return is
    counted today - in the summary, the daily series, staff shares and the revenue report, as in the ledger."""
    line, svc, person = setup_line(client, "G7 برگشت", 5_000_000, 40)
    old = TODAY - timedelta(days=40)
    card = accounts["کارتخوان ملت"]

    def snap():
        return {"old": summ(client, old), "today": summ(client, TODAY), "old_l": ledger(SALES, old), "today_l": ledger(SALES, TODAY),
                "comm_today": ledger(("5200",), TODAY, credit=False), "daily": daily(client, TODAY),
                "sh_old": staff_row(shares(client, old), person["id"]), "sh_today": staff_row(shares(client, TODAY), person["id"]),
                "rev_today": client.get("/api/reports/revenue", params={"start": TODAY.isoformat(), "end": TODAY.isoformat(),
                                                                         "line_id": line["id"]}).json()["totals"]["revenue"]}

    before = snap()
    inv = client.post("/api/invoices", json={"customer_name": "مشتری G7 برگشت", "customer_mobile": "09151070001",
                                             "issued_at": datetime.combine(old, time(11, 0)).isoformat(),
                                             "items": [{"service_id": svc["id"], "unit_price": 5_000_000, "staff_id": person["id"]}],
                                             "payments": [{"payment_account_id": card, "amount": 5_000_000}]})
    assert inv.status_code == 200, inv.text
    inv = inv.json()
    mid = snap()
    assert mid["old"]["revenue"] - before["old"]["revenue"] == 5_000_000 == mid["old_l"] - before["old_l"]
    assert mid["sh_old"]["revenue"] - before["sh_old"]["revenue"] == 5_000_000
    r = client.post(f"/api/invoices/{inv['id']}/void", params={"payments": "refund", "reason": "مشتری پس آورد"})
    assert r.status_code == 200, r.text
    after = snap()
    # the old day keeps its sale (as the books do) ...
    assert after["old"]["revenue"] == mid["old"]["revenue"] and after["old_l"] == mid["old_l"]
    assert (after["sh_old"]["revenue"], after["sh_old"]["staff_share"]) == (mid["sh_old"]["revenue"], mid["sh_old"]["staff_share"])
    # ... and today has the return: sales, returns, staff share and the daily series agree with the ledger
    assert after["today"]["revenue"] - mid["today"]["revenue"] == -5_000_000 == after["today_l"] - mid["today_l"]
    assert after["today"]["returns"] - mid["today"]["returns"] == 5_000_000
    assert after["today"]["returns_count"] - mid["today"]["returns_count"] == 1
    assert after["sh_today"]["revenue"] - mid["sh_today"]["revenue"] == -5_000_000
    assert after["sh_today"]["staff_share"] - mid["sh_today"]["staff_share"] == -2_000_000 == after["comm_today"] - mid["comm_today"]
    assert after["daily"]["revenue"] - mid["daily"]["revenue"] == -5_000_000
    assert after["daily"]["cash_in"] - mid["daily"]["cash_in"] == -5_000_000 == after["today"]["cash_in"] - mid["today"]["cash_in"]
    assert after["rev_today"] - mid["rev_today"] == -5_000_000
    # the staff member's detail lists the return (negative, today) and adds up to the total
    d = client.get(f"/api/reports/revenue/staff/{person['id']}", params={"start": TODAY.isoformat(), "end": TODAY.isoformat()}).json()
    assert d["totals"]["revenue"] == -5_000_000 == sum(x["amount"] for x in d["records"])
    assert d["records"][0]["returned"] is True and d["records"][0]["ref"] == inv["number"]
    # day details: today's sales include the return, the return is listed
    day = client.get("/api/dashboard/day").json()
    assert any(x["number"] == inv["number"] and x["total"] == 5_000_000 for x in day["returns"])
    assert day["sales"] == after["today"]["revenue"]


def test_g7_mistake_cancel_vanishes_from_its_day(client, accounts):
    line, svc, _ = setup_line(client, "G7 لغو اشتباه", 3_000_000)
    d10 = TODAY - timedelta(days=10)
    def snap():
        t = summ(client, TODAY)
        return summ(client, d10)["revenue"], ledger(SALES, d10), t["revenue"], ledger(SALES, TODAY), t.get("returns_count")

    before = snap()
    inv = client.post("/api/invoices", json={"customer_name": "مشتری G7 لغو", "customer_mobile": "09151070002",
                                             "issued_at": datetime.combine(d10, time(10, 0)).isoformat(),
                                             "items": [{"service_id": svc["id"], "unit_price": 3_000_000}],
                                             "payments": [{"payment_account_id": accounts["صندوق نقدی"], "amount": 3_000_000}]}).json()
    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200  # default: a mistake, cancelled on its own date
    assert snap() == before  # it never happened: not a sale on its day, not a return today


def test_g7_product_return_cost_follows_the_ledger(client, accounts):
    prod = client.post("/api/products", json={"name": "کالای G7 برگشت", "sale_price": 2_000_000}).json()
    r = client.post(f"/api/products/{prod['id']}/opening", json={"qty": 5, "unit_cost": 1_200_000,
                                                                "at": datetime.combine(TODAY - timedelta(days=30), time(9)).isoformat()})
    assert r.status_code == 200, r.text
    d20 = TODAY - timedelta(days=20)
    inv = client.post("/api/invoices", json={"customer_name": "مشتری G7 کالا", "customer_mobile": "09151070003",
                                             "issued_at": datetime.combine(d20, time(12)).isoformat(),
                                             "items": [{"product_id": prod["id"], "quantity": 2, "unit_price": 2_000_000}],
                                             "payments": [{"payment_account_id": accounts["کارتخوان ملت"], "amount": 4_000_000}]}).json()
    s20 = summ(client, d20)
    before = (summ(client, TODAY), ledger(("5400",), TODAY, credit=False), ledger(("4200",), TODAY))
    assert client.post(f"/api/invoices/{inv['id']}/void", params={"payments": "refund"}).status_code == 200
    after = (summ(client, TODAY), ledger(("5400",), TODAY, credit=False), ledger(("4200",), TODAY))
    assert after[0]["cogs"] - before[0]["cogs"] == -2_400_000 == after[1] - before[1]
    assert after[0]["product_revenue"] - before[0]["product_revenue"] == -4_000_000 == after[2] - before[2]
    assert summ(client, d20)["cogs"] == s20["cogs"] and summ(client, d20)["product_revenue"] == s20["product_revenue"]


def test_g7_daily_cash_agrees_with_summary_for_deposit_refunds(client, accounts):
    cash = accounts["صندوق نقدی"]
    yesterday = datetime.combine(TODAY - timedelta(days=1), time(10))
    before = (daily(client, TODAY)["cash_in"], summ(client, TODAY)["cash_in"])
    dep = client.post("/api/deposits", json={"customer_name": "بیعانه G7", "customer_mobile": "09151070004", "amount": 3_000_000,
                                             "payment_account_id": cash, "received_at": yesterday.isoformat()}).json()
    assert client.post(f"/api/deposits/{dep['id']}/close", json={"action": "refund"}).status_code == 200
    # received and refunded the same day: nothing kept
    dep2 = client.post("/api/deposits", json={"customer_id": dep["customer_id"], "amount": 1_000_000, "payment_account_id": cash}).json()
    assert client.post(f"/api/deposits/{dep2['id']}/close", json={"action": "refund"}).status_code == 200
    after = (daily(client, TODAY)["cash_in"], summ(client, TODAY)["cash_in"])
    assert after[0] - before[0] == -3_000_000 == after[1] - before[1]


def test_g7_website_refund_is_order_money_not_a_deposit(client, accounts):
    from app.models import Customer, PaymentAccount
    from app.services import accounting

    before = summ(client, TODAY)
    with _db() as db:
        c = Customer(full_name="خریدار سایت G7", mobile="09151070005", source="woocommerce", legacy_code="G7-W1")
        db.add(c)
        db.flush()
        pa = db.get(PaymentAccount, accounts["کارتخوان ملت"])
        # as woo._book does: an order of 1,000,000 paid, 200,000 given back on the website (net invoice 800,000)
        inv = accounting.issue_invoice(db, customer=c, items=[{"description": "سفارش سایت G7", "unit_price": 800_000}],
                                       source="woocommerce", channel="online")
        accounting.record_payment(db, payment_account=pa, amount=800_000, invoice=inv, source="woocommerce")
        dep = accounting.record_deposit(db, customer=c, amount=200_000, payment_account=pa, source="woocommerce")
        accounting.close_deposit(db, dep, "refund", pa)
        db.commit()
        did = dep.id
    after = summ(client, TODAY)
    assert after["deposits_received"] == before["deposits_received"]  # not a deposit
    assert after["cash_received"] - before["cash_received"] == 1_000_000  # what came in with the order
    assert after["cash_paid_back"] - before["cash_paid_back"] == 200_000
    assert after["cash_in"] - before["cash_in"] == 800_000
    back = client.get("/api/reports/money-returned").json()
    row = next(x for x in back["items"] if x["deposit_id"] == did)
    assert row["kind"] == "refund" and row["amount"] == 200_000


def test_g7_staff_shares_services_only_and_forfeits_per_line(client, accounts, services):
    line, svc, person = setup_line(client, "G7 سهم", 10_000_000, 40)
    prod = client.post("/api/products", json={"name": "کالای G7 سهم", "sale_price": 1_000_000}).json()
    rep0 = shares(client, TODAY)
    none0, other0 = staff_row(rep0, None), next((x for x in rep0["lines"] if x["line_id"] is None), {"revenue": 0})
    inv = client.post("/api/invoices", json={"customer_name": "مشتری G7 سهم", "customer_mobile": "09151070006",
                                             "items": [{"service_id": svc["id"], "unit_price": 10_000_000, "staff_id": person["id"]},
                                                       {"product_id": prod["id"], "quantity": 1, "unit_price": 1_000_000}]})
    assert inv.status_code == 200, inv.text
    rep = shares(client, TODAY)
    assert staff_row(rep, None)["revenue"] == none0["revenue"]  # the product is not a service of "بدون پرسنل"
    assert next((x for x in rep["lines"] if x["line_id"] is None), {"revenue": 0})["revenue"] == other0["revenue"]
    assert rep["totals"]["revenue"] - rep0["totals"]["revenue"] == 10_000_000
    assert rep["totals"]["salon_share"] - rep0["totals"]["salon_share"] == 6_000_000
    mine = staff_row(rep, person["id"])
    assert (mine["revenue"], mine["staff_share"], mine["salon_share"]) == (10_000_000, 4_000_000, 6_000_000)
    # a forfeited deposit of this line: the line's own income, shared like its services (1.22): the only staff member
    # of the line is the deposit's, so 40% of it is theirs
    dep = client.post("/api/deposits", json={"customer_name": "غایب G7 سهم", "customer_mobile": "09151070007", "amount": 1_500_000,
                                             "payment_account_id": accounts["صندوق نقدی"], "service_id": svc["id"]}).json()
    assert client.post(f"/api/deposits/{dep['id']}/close", json={"action": "forfeit"}).status_code == 200
    rep2 = shares(client, TODAY)
    lr = next(x for x in rep2["lines"] if x["line_id"] == line["id"])
    assert lr["forfeits"] == 1_500_000 and lr["revenue"] == 10_000_000
    assert rep2["totals"]["forfeits"] - rep["totals"]["forfeits"] == 1_500_000
    mine2 = staff_row(rep2, person["id"])
    assert (mine2["forfeits"], mine2["staff_share"], mine2["salon_share"]) == (1_500_000, 4_600_000, 6_900_000)
    assert lr["revenue"] + lr["forfeits"] == lr["treasury"] + lr["staff_share"] + lr["salon_share"]


def test_g7_inactive_staff_still_owed_is_listed(client, accounts):
    line, svc, person = setup_line(client, "G7 رفته", 6_000_000, 50)
    d20 = datetime.combine(TODAY - timedelta(days=20), time(10))
    client.post("/api/invoices", json={"customer_name": "مشتری G7 رفته", "customer_mobile": "09151070008", "issued_at": d20.isoformat(),
                                       "items": [{"service_id": svc["id"], "unit_price": 6_000_000, "staff_id": person["id"]}]})
    r = client.put(f"/api/staff/{person['id']}", json={"full_name": person["full_name"], "line_id": line["id"],
                                                         "commission_percent": 50, "is_active": False})
    assert r.status_code == 200, r.text
    rep = shares(client, TODAY - timedelta(days=6), TODAY)
    row = next(r for r in rep["staff"] if r["staff_id"] == person["id"])
    assert row["balance"] == 3_000_000 and row["active"] is False and row["revenue"] == 0
    # the total owed to staff = the ledger (2200), less what is owed to the clinic's employees (their own page)
    from app.models import JournalLine, LedgerAccount, Staff

    with _db() as db:
        employees = int(db.scalar(select(func.coalesce(func.sum(JournalLine.credit - JournalLine.debit), 0))
                                  .join(LedgerAccount, LedgerAccount.id == JournalLine.account_id)
                                  .where(LedgerAccount.code == "2200",
                                         JournalLine.staff_id.in_(select(Staff.id).where(Staff.kind == "employee")))) or 0)
    owed = ledger(("2200",), date(2000, 1, 1), TODAY) - employees
    assert rep["totals"]["balance"] == owed


def test_g7_receivable_includes_debts_brought_over(client, accounts):
    from app.models import Customer
    from app.services import accounting

    before = summ(client, TODAY)["receivable"]
    with _db() as db:
        c = Customer(full_name="بدهکار تیزپرداز G7", mobile="09151070009", source="tizpardaz", legacy_code="G7-T1")
        db.add(c)
        db.flush()
        accounting.post(db, "ماندهٔ بدهی انتقالی از تیزپرداز - G7", [
            accounting.Leg(accounting.account(db, accounting.AR), debit=7_000_000, customer_id=c.id),
            accounting.Leg(accounting.account(db, accounting.OPENING), credit=7_000_000, customer_id=c.id),
        ], "tp_opening", c.id)
        db.commit()
        cid = c.id
    _, svc, _ = setup_line(client, "G7 بدهی", 10_000_000)
    r = client.post("/api/invoices", json={"customer_id": cid, "items": [{"service_id": svc["id"], "unit_price": 10_000_000}]})
    assert r.status_code == 200, r.text
    assert summ(client, TODAY)["receivable"] - before == 17_000_000


def test_g7_imported_customers_are_not_new(client):
    from app.models import Customer

    before = summ(client, TODAY)["new_customers"]
    with _db() as db:
        db.add_all([Customer(full_name="وارداتی تیزپرداز G7", mobile="09151070010", source="tizpardaz", legacy_code="G7-T2"),
                    Customer(full_name="وارداتی چهره G7", mobile="09151070011", source="import", legacy_code="G7-C1")])
        db.commit()
    real = client.post("/api/customers", json={"full_name": "مشتری تازه G7", "mobile": "09151070012"}).json()
    assert summ(client, TODAY)["new_customers"] - before == 1
    day = client.get("/api/dashboard/day").json()
    new = {c["name"] for c in day["customers"] if c["new"]}
    assert real["full_name"] in new and "وارداتی تیزپرداز G7" not in new and "وارداتی چهره G7" not in new


def test_g7_done_without_invoice_is_old_history_only_when_imported(client):
    from app.models import Appointment
    from app.services.legacy_import import IMPORT_NOTE

    line, svc, _ = setup_line(client, "G7 سابقه", 8_000_000)
    c = client.post("/api/customers", json={"full_name": "مشتری G7 سابقه", "mobile": "09151070013"}).json()
    at = datetime.combine(TODAY - timedelta(days=3), time(10))
    with _db() as db:
        db.add_all([Appointment(customer_id=c["id"], service_id=svc["id"], start_at=at, status="done", quoted_price=8_000_000, notes=""),
                    Appointment(customer_id=c["id"], service_id=svc["id"], start_at=at, status="done", quoted_price=2_000_000,
                                notes=f"{IMPORT_NOTE} - فیش 12")])
        db.commit()
    rep = client.get("/api/reports/revenue", params={"line_id": line["id"], "start": (TODAY - timedelta(days=5)).isoformat()}).json()
    assert rep["totals"]["revenue"] == 2_000_000 == rep["totals"]["old"]  # the one set 'done' by hand has no money behind it


def test_g7_old_source_products_cost_and_all_records_start(client):
    from app.models import TradeHistory

    day = date(2019, 5, 1)
    prod = client.post("/api/products", json={"name": "کالای G7 قدیمی", "sale_price": 2_000_000}).json()
    client.post(f"/api/products/{prod['id']}/opening", json={"qty": 5, "unit_cost": 1_200_000, "at": "2019-04-01T09:00:00"})
    r = client.post("/api/invoices", json={"customer_name": "مشتری G7 قدیمی", "customer_mobile": "09151070014",
                                           "issued_at": "2019-05-01T11:00:00",
                                           "items": [{"product_id": prod["id"], "quantity": 2, "unit_price": 2_000_000}]})
    assert r.status_code == 200, r.text
    with _db() as db:
        db.add_all([TradeHistory(source="tizpardaz", kind="sale", at=datetime(2019, 5, 1, 12), product_id=prod["id"], description="G7 قدیمی",
                                 qty=1, unit_price=1_500_000, amount=1_500_000, cost=1_000_000, fp="g7|old|1"),
                    TradeHistory(source="tizpardaz", kind="sale", at=datetime(2001, 1, 2, 12), product_id=prod["id"], description="G7 خیلی قدیمی",
                                 qty=1, unit_price=500_000, amount=500_000, cost=300_000, fp="g7|old|2")])
        db.commit()

    def products_line(source: str) -> dict:
        rep = client.get("/api/reports/revenue", params={"start": day.isoformat(), "end": day.isoformat(), "source": source}).json()
        return next(x for x in rep["lines"] if x["id"] == -1)

    old, new, both = products_line("old"), products_line("new"), products_line("all")
    assert (old["revenue"], old["cogs"], old["profit"]) == (1_500_000, 1_000_000, 500_000)
    assert (new["revenue"], new["cogs"], new["profit"]) == (4_000_000, 2_400_000, 1_600_000)
    assert (both["revenue"], both["cogs"]) == (5_500_000, 3_400_000)
    # "all records": from the first record of the chosen source, product sales brought over included
    for source in ("old", "all"):
        rep = client.get("/api/reports/revenue", params={"source": source}).json()
        assert rep["period"]["start"] == "2001-01-02"
    assert client.get("/api/reports/revenue", params={"source": "new"}).json()["period"]["start"] != "2001-01-02"


def test_g7_one_split_per_line_everywhere(client):
    """Summary, staff shares and the ledger split an invoice discount over the lines the same way (to the Rial)."""
    from app.models import Invoice, JournalEntry, JournalLine

    day = date(2018, 2, 3)
    l1, s1, _ = setup_line(client, "G7 مو", 1000)
    l2, s2, _ = setup_line(client, "G7 ناخن", 1000)
    inv = client.post("/api/invoices", json={"customer_name": "مشتری G7 تقسیم", "customer_mobile": "09151070015", "discount": 1000,
                                             "issued_at": "2018-02-03T10:00:00",
                                             "items": [{"service_id": s1["id"], "unit_price": 1000}, {"service_id": s2["id"], "unit_price": 1000},
                                                       {"service_id": s1["id"], "unit_price": 1000}]}).json()
    with _db() as db:
        entry = db.scalar(select(JournalEntry).where(JournalEntry.ref_type == "invoice", JournalEntry.ref_id == inv["id"]))
        booked = {l.line_id: l.credit for l in db.scalars(select(JournalLine).where(JournalLine.entry_id == entry.id, JournalLine.credit > 0))}
        assert db.get(Invoice, inv["id"]).total == 2000
    s = summ(client, day)
    by_line = {x["name"]: x["value"] for x in s["by_line"]}
    assert by_line[l1["name"]] == booked[l1["id"]] and by_line[l2["name"]] == booked[l2["id"]]
    assert by_line[l1["name"]] + by_line[l2["name"]] == 2000
    sh = {x["line_id"]: x["revenue"] for x in shares(client, day)["lines"]}
    assert sh[l1["id"]] == booked[l1["id"]] and sh[l2["id"]] == booked[l2["id"]]


def test_g7_unassigned_detail_has_no_products(client):
    day = date(2018, 3, 4)
    line, svc, _ = setup_line(client, "G7 بی‌پرسنل", 1_000_000)  # a line with no staff: nobody to attribute it to
    # a line with two people when the invoice was issued (no staff picked), one of them gone since: it is the other's
    l2, s2, p1 = setup_line(client, "G7 دونفره", 2_000_000, 30)
    p2 = client.post("/api/staff", json={"full_name": "پرسنل دوم G7", "line_id": l2["id"], "commission_percent": 30}).json()
    prod = client.post("/api/products", json={"name": "سرم G7", "sale_price": 3_000_000}).json()
    r = client.post("/api/invoices", json={"customer_name": "مشتری G7 بی‌پرسنل", "customer_mobile": "09151070016", "issued_at": "2018-03-04T10:00:00",
                                           "items": [{"service_id": svc["id"], "unit_price": 1_000_000},
                                                     {"service_id": s2["id"], "unit_price": 2_000_000},
                                                     {"product_id": prod["id"], "quantity": 1, "unit_price": 3_000_000}]})
    assert r.status_code == 200, r.text
    assert client.put(f"/api/staff/{p2['id']}", json={"full_name": p2["full_name"], "line_id": l2["id"], "commission_percent": 30,
                                                       "is_active": False}).status_code == 200
    params = {"start": day.isoformat(), "end": day.isoformat()}
    main = client.get("/api/reports/revenue", params=params).json()
    row = next(x for x in main["staff"] if x["id"] == 0)
    d = client.get("/api/reports/revenue/staff/0", params=params).json()
    assert row["revenue"] == d["totals"]["revenue"] == sum(x["amount"] for x in d["records"]) == 1_000_000
    assert "سرم G7" not in {x["service"] for x in d["records"]}
    sole = client.get(f"/api/reports/revenue/staff/{p1['id']}", params=params).json()
    assert sole["totals"]["revenue"] == sum(x["amount"] for x in sole["records"]) == 2_000_000


def test_g7_net_profit_is_the_ledger(client):
    day = date(2018, 4, 5)
    _, svc, person = setup_line(client, "G7 سود", 10_000_000, 40)
    before = summ(client, day)["net_profit"]
    client.post("/api/invoices", json={"customer_name": "مشتری G7 سود", "customer_mobile": "09151070017", "issued_at": "2018-04-05T10:00:00",
                                       "items": [{"service_id": svc["id"], "unit_price": 10_000_000, "staff_id": person["id"]}]})
    assert summ(client, day)["net_profit"] - before == 6_000_000  # 10m sales - 4m staff share


def test_g7_forecast_not_pulled_down_by_closed_fridays(monkeypatch):
    from app.services import reports

    def fake(db, start=None, end=None):  # noqa: ANN001, ANN202
        out, d = [], start
        while d <= end:
            out.append({"date": d.isoformat(), "revenue": 0 if d.weekday() == 4 else 10_000_000, "expenses": 0, "cash_in": 0})
            d += timedelta(days=1)
        return out

    monkeypatch.setattr(reports, "daily_series", fake)
    with _db() as db:
        f = reports.forecast(db, days=28)
    open_days = [p for p in f["points"] if date.fromisoformat(p["date"]).weekday() != 4]
    assert all(p["revenue"] == 0 for p in f["points"] if date.fromisoformat(p["date"]).weekday() == 4)
    assert all(abs(p["revenue"] - 10_000_000) <= 100_000 for p in open_days), [p["revenue"] for p in open_days]
    assert abs(f["total"] - 24 * 10_000_000) <= 2_400_000
    assert abs(f["trend_per_day"]) < 50_000


def test_g7_forecast_with_every_sale_returned_does_not_crash(monkeypatch):
    """A week of sales all returned the next week (same weekdays): no weekday has real sales - no forecast, no error."""
    from app.services import reports

    def fake(db, start=None, end=None):  # noqa: ANN001, ANN202
        out, d = [], start
        while d <= end:
            ago = (end - d).days
            out.append({"date": d.isoformat(), "revenue": 5_000_000 if 7 <= ago < 14 else -5_000_000 if ago < 7 else 0,
                        "expenses": 0, "cash_in": 0})
            d += timedelta(days=1)
        return out

    monkeypatch.setattr(reports, "daily_series", fake)
    with _db() as db:
        f = reports.forecast(db, days=7)
    assert f["method"] == "insufficient_data" and f["points"] == []
