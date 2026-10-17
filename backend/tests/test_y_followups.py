"""1.20.1: the sales file follows the books (returns on their own day), appointments are done only by an invoice,
website orders moved to the trash or deleted, refund rows that name no order row."""
import csv
import io
from datetime import date, datetime, time, timedelta

TODAY = date.today()


def _db():
    from app.core.db import SessionLocal

    return SessionLocal()


def sales_file(client, start: date, end: date) -> list[dict]:
    from app.api.system import CSV_HEAD

    r = client.get("/api/reports/export.csv", params={"start": start.isoformat(), "end": end.isoformat()})
    assert r.status_code == 200, r.text
    head = {v: k for k, v in CSV_HEAD.items()}
    return [{head[k]: v for k, v in x.items()} for x in csv.DictReader(io.StringIO(r.text.lstrip("﻿")))]


def file_total(rows: list[dict]) -> int:
    return sum(int(x["net"]) * (-1 if x["type"] == "برگشت" else 1) for x in rows)


def revenue(client, d: date) -> int:
    return client.get("/api/reports/summary", params={"start": d.isoformat(), "end": d.isoformat()}).json()["revenue"]


def test_sales_file_has_returns_on_their_own_day(client, accounts):
    """An invoice of 20 days ago (two rows, an invoice discount) returned today: the file keeps the sale on its own
    day and lists the return today, amounts positive; each day's file adds up to that day's sales in the reports."""
    line = client.post("/api/lines", json={"name": "خط فایل فروش"}).json()
    svc = client.post("/api/services", json={"line_id": line["id"], "name": "خدمت فایل فروش", "base_price": 3_000_000}).json()
    old = TODAY - timedelta(days=20)
    before = {d: (file_total(sales_file(client, d, d)), revenue(client, d)) for d in (old, TODAY)}
    inv = client.post("/api/invoices", json={
        "customer_name": "مشتری فایل فروش", "customer_mobile": "09151200101", "discount": 1_000_000,
        "issued_at": datetime.combine(old, time(11)).isoformat(),
        "items": [{"service_id": svc["id"], "unit_price": 3_000_000}, {"service_id": svc["id"], "unit_price": 2_000_001}],
        "payments": [{"payment_account_id": accounts["صندوق نقدی"], "amount": 4_000_001}]})
    assert inv.status_code == 200, inv.text
    inv = inv.json()
    assert client.post(f"/api/invoices/{inv['id']}/void", params={"payments": "refund", "reason": "پس آورد"}).status_code == 200
    sold = [x for x in sales_file(client, old, old) if x["number"] == inv["number"]]
    back = [x for x in sales_file(client, TODAY, TODAY) if x["number"] == inv["number"]]
    assert [x["type"] for x in sold] == ["فروش", "فروش"] and [x["type"] for x in back] == ["برگشت", "برگشت"]
    assert sum(int(x["net"]) for x in sold) == 4_000_001 == sum(int(x["net"]) for x in back)  # to the Rial
    assert back[0]["invoice_date"] == sold[0]["date"] != back[0]["date"]
    for d in (old, TODAY):  # each day: the file's sales minus its returns == the reports' sales of the day
        now = (file_total(sales_file(client, d, d)), revenue(client, d))
        assert now[0] - before[d][0] == now[1] - before[d][1]
    # a mistake cancelled on its own date never happened: in neither day
    inv2 = client.post("/api/invoices", json={
        "customer_name": "مشتری فایل فروش", "customer_mobile": "09151200101", "issued_at": datetime.combine(old, time(12)).isoformat(),
        "items": [{"service_id": svc["id"], "unit_price": 1_000_000}]}).json()
    assert client.post(f"/api/invoices/{inv2['id']}/void").status_code == 200
    assert not [x for x in sales_file(client, old, TODAY) if x["number"] == inv2["number"]]


def test_appointment_done_only_by_its_invoice(client):
    """'Done' by hand is refused (the invoice does it); one set 'done' by hand in an older version has no money behind
    it: not in the customer's purchases, not in the service's use. The previous software's history still counts."""
    from app.models import Appointment
    from app.services.legacy_import import IMPORT_NOTE

    line = client.post("/api/lines", json={"name": "خط انجام دستی"}).json()
    svc = client.post("/api/services", json={"line_id": line["id"], "name": "خدمت انجام دستی", "base_price": 7_000_000}).json()
    c = client.post("/api/customers", json={"full_name": "مشتری انجام دستی", "mobile": "09151200102"}).json()
    start = datetime.combine(TODAY + timedelta(days=2), time(10))
    a = client.post("/api/appointments", json={"customer_id": c["id"], "service_id": svc["id"], "start_at": start.isoformat(),
                                               "allow_outside_hours": True})
    assert a.status_code == 200, a.text
    a = a.json()
    r = client.patch(f"/api/appointments/{a['id']}", params={"status": "done"})
    assert r.status_code == 400 and "فاکتور" in r.json()["detail"]
    assert client.put(f"/api/appointments/{a['id']}", json={"status": "done"}).status_code == 400
    assert client.get(f"/api/appointments/{a['id']}").json()["status"] == "booked"
    at = datetime.combine(TODAY - timedelta(days=4), time(10))
    with _db() as db:  # one set 'done' by hand before this version (the newer visit), one brought over from the previous software
        db.add_all([Appointment(customer_id=c["id"], service_id=svc["id"], start_at=at, status="done", quoted_price=7_000_000, notes=""),
                    Appointment(customer_id=c["id"], service_id=svc["id"], start_at=at - timedelta(days=3), status="done",
                                quoted_price=2_000_000, notes=f"{IMPORT_NOTE} - فیش 7")])
        db.commit()
    row = next(x for x in client.get("/api/customers", params={"q": "09151200102"}).json()["items"] if x["id"] == c["id"])
    assert row["spent_old"] == 2_000_000 == row["total_spent"]  # money: only the previous software's receipt
    file = client.get(f"/api/customers/{c['id']}").json()
    assert sorted(h["amount"] or 0 for h in file["history"]) == [0, 2_000_000]
    # visits: both, in the list as in the customer file (the hand-done one is a visit without money)
    assert row["visits"] == len({h["date"][:10] for h in file["history"]}) == 2
    assert row["last_visit"][:10] == file["history"][0]["date"][:10] == at.date().isoformat()
    use = next(x for x in client.get("/api/services/usage").json() if x.get("service_id", x.get("id")) == svc["id"])
    assert (use["old"], use["old_amount"]) == (1, 2_000_000)


# ---------------------------------------------------------------------------------------------- website orders
from tests.test_g8_woo import _balanced, _connect, _moves, _orders, _product, _sync, shop  # noqa: E402, F401


def _gone_check_due() -> None:
    """The trash check runs once an hour: make it due now."""
    from app.services import woo

    with _db() as db:
        st = woo.state(db)
        st.pop("gone_at", None)
        woo._save_state(db, st)
        db.commit()


def test_an_order_put_in_the_websites_trash_is_undone(client, shop):
    """The website's order list leaves the trash out: the booked orders are looked up by id. One in the trash is
    undone like a cancelled one (its money goes back once); one deleted for good is reported, its invoice kept."""
    sp = shop.add_product("کرم سطل زباله", "Y-TRASH-1", 200_000, stock=10)
    p = _product(client, "کرم سطل زباله", "Y-TRASH-1", qty=10)
    gw = _connect(client, shop, url="y-trash-1.example.com")
    keep = shop.add_order([(sp, 1)], phone="09151200103", first="سفارش", last="ماندنی")
    gone = shop.add_order([(sp, 2)], phone="09151200104", first="سفارش", last="زباله")
    wiped = shop.add_order([(sp, 3)], phone="09151200105", first="سفارش", last="پاک‌شده")
    _sync(client)
    booked = _orders(client)
    assert all(booked[o["id"]]["state"] == "booked" for o in (keep, gone, wiped))
    assert client.get(f"/api/products/{p['id']}").json()["stock_qty"] == 4
    assert sum(v for _, v in _moves(gw)) == 12_000_000
    shop.trash(gone["id"])
    shop.delete(wiped["id"])
    _gone_check_due()
    out = _sync(client, catalog=False)
    assert out["gone"].get("cancelled") == 1 and out["gone"].get("deleted") == 1, out
    now = _orders(client)
    assert now[gone["id"]]["state"] == "cancelled" and client.get(f"/api/invoices/{now[gone['id']]['invoice_id']}").json()["status"] == "void"
    assert now[wiped["id"]]["status"] == "deleted" and now[wiped["id"]]["note"]
    assert client.get(f"/api/invoices/{now[wiped['id']]['invoice_id']}").json()["status"] != "void"  # kept: a person decides
    assert now[keep["id"]]["state"] == "booked"
    assert sum(v for _, v in _moves(gw)) == 12_000_000 - 4_000_000  # the trashed order's money went back, once
    assert client.get(f"/api/products/{p['id']}").json()["stock_qty"] == 6
    with _db() as db:
        from app.models import Alert

        assert db.query(Alert).filter(Alert.kind == "woo_deleted", Alert.ref_id == now[wiped["id"]]["invoice_id"]).count() == 1
    # the next checks change nothing more and report the deleted one only once
    _gone_check_due()
    again = _sync(client, catalog=False)
    assert not again["gone"].get("cancelled") and not again["gone"].get("deleted")
    assert sum(v for _, v in _moves(gw)) == 8_000_000
    _balanced(client)


def test_a_website_that_ignores_the_id_list_is_not_asked_order_by_order(client, shop):
    """If the website does not narrow its answer to the ids asked for (it lists orders not asked for), nothing is
    guessed from what is missing: no order is asked for one by one, none is reported deleted."""
    sp = shop.add_product("کرم بدون فیلتر", "Y-TRASH-2", 100_000, stock=10)
    _product(client, "کرم بدون فیلتر", "Y-TRASH-2", qty=10)
    _connect(client, shop, url="y-trash-2.example.com")
    a = shop.add_order([(sp, 1)], phone="09151200106", first="الف", last="بی‌فیلتر")
    shop.add_order([(sp, 1)], status="pending", paid=False, phone="09151200107", first="ب", last="بی‌فیلتر")  # not booked here
    _sync(client)
    real = shop.handler

    def no_include(request):  # noqa: ANN001, ANN202
        url = request.url.copy_remove_param("include")
        return real(request.__class__(request.method, url, headers=request.headers, content=request.content))

    shop.handler = no_include
    from app.services import woo

    woo._transport = shop.transport()
    shop.orders.pop(a["id"])  # gone from the website, but the website's answers can't show which orders were asked for
    _gone_check_due()
    shop.calls.clear()
    out = _sync(client, catalog=False)
    assert not out["gone"].get("deleted") and _orders(client)[a["id"]]["status"] != "deleted"
    assert not [p for m, p in shop.calls if p.startswith("/orders/")]


def test_refund_rows_that_name_no_order_row_are_shared_over_the_products_rows():
    """A refund row without _refunded_item_id: the product's rows give back what each still has, not all on the first."""
    from app.services.woo import plan

    o = {"line_items": [{"id": 1, "product_id": 7, "variation_id": 0, "name": "کرم", "quantity": 1, "subtotal": "100", "total": "100"},
                        {"id": 2, "product_id": 7, "variation_id": 0, "name": "کرم", "quantity": 2, "subtotal": "200", "total": "200"}],
         "shipping_total": "0", "total_tax": "0", "fee_lines": [], "total": "300", "refunds": [{"id": 9, "total": "-200"}]}
    refunds = [{"id": 9, "line_items": [{"product_id": 7, "variation_id": 0, "quantity": -2, "total": "-200"}]}]
    pl = plan(o, refunds, 10)
    assert sum(x["quantity"] for x in pl["lines"]) == 1  # 3 bought, 2 given back
    assert sum(x["net"] for x in pl["lines"]) + sum(x["unit_price"] for x in pl["extras"]) - pl["discount"] == pl["target"] == 1_000
    assert not pl["extras"] and pl["discount"] == 0


def test_the_trash_check_never_holds_back_the_stock_sent_to_the_website(client, shop):
    """The orders are read, then the trash check fails (the website times out on that one request): the stock change
    still goes to the website, the run shows no error, the problem is logged once and the check waits an hour."""
    import httpx

    from app.services import woo

    sp = shop.add_product("کرم تایم‌اوت زباله", "Y-TRASH-3", 100_000, stock=10)
    p = _product(client, "کرم تایم‌اوت زباله", "Y-TRASH-3")
    _connect(client, shop, url="y-trash-3.example.com")
    shop.add_order([(sp, 1)], phone="09151200108", first="الف", last="تایم‌اوت")
    _sync(client)
    real, tries = shop.handler, []

    def slow_on_ids(request):  # noqa: ANN001, ANN202
        if "include" in request.url.params:
            tries.append(1)
            raise httpx.ReadTimeout("timed out", request=request)
        return real(request)

    shop.handler = slow_on_ids
    woo._transport = shop.transport()
    client.post("/api/purchases", json={"supplier_name": "پخش زباله", "items": [{"product_id": p["id"], "quantity": 5, "unit_price": 500_000}]})
    _gone_check_due()
    out = _sync(client, catalog=False)  # no error for the run
    assert out["gone"].get("error") == 1 and shop.products[sp]["stock_quantity"] == 14  # 10 - 1 sold on the site + 5 bought here
    assert len(tries) == 1
    _sync(client, catalog=False)  # the next run within the hour does not try again
    assert len(tries) == 1
    _gone_check_due()
    _sync(client, catalog=False)  # tried again (an hour later): the same problem is not written again
    logged = [x for x in client.get("/api/woo/log").json() if "سطل زباله" in x["message"] and "انجام نشد" in x["message"]]
    assert len(tries) == 2 and len(logged) == 1


def test_a_new_key_for_the_same_website_still_finds_an_order_put_in_the_trash(client, shop):
    """The orders belong to the website's address, not to its key: an order put in the trash before the key was
    changed is still found (it is no longer in the website's order list to be read again)."""
    sp = shop.add_product("کرم کلید نو", "Y-TRASH-4", 100_000, stock=10)
    _product(client, "کرم کلید نو", "Y-TRASH-4", qty=10)
    _connect(client, shop, url="y-trash-4.example.com")
    o = shop.add_order([(sp, 1)], phone="09151200109", first="الف", last="کلید نو")
    _sync(client)
    shop.trash(o["id"])
    shop.key, shop.secret = shop.key + "x", shop.secret + "x"  # a new key made on the website
    _connect(client, shop, url="y-trash-4.example.com")
    out = _sync(client, catalog=False)
    assert out["gone"].get("cancelled") == 1 and _orders(client)[o["id"]]["state"] == "cancelled"


def test_orders_of_another_website_are_never_reported_deleted(client, shop):
    """Orders read from a website connected before (another address), or by a version before this check, are not
    looked for on the website connected now."""
    from app.models import WooOrder

    sp = shop.add_product("کرم سایت قبلی", "Y-TRASH-5", 100_000, stock=10)
    _product(client, "کرم سایت قبلی", "Y-TRASH-5", qty=10)
    _connect(client, shop, url="y-old-site.example.com")
    a = shop.add_order([(sp, 1)], phone="09151200110", first="الف", last="سایت قبلی")
    b = shop.add_order([(sp, 1)], phone="09151200111", first="ب", last="سایت قبلی")
    _sync(client)
    with _db() as db:  # b was read by 1.20.1 (no website recorded)
        db.query(WooOrder).filter(WooOrder.order_id == b["id"]).update({"site": None})
        db.commit()
    shop.orders.pop(a["id"]), shop.orders.pop(b["id"])  # not on the website connected next
    _connect(client, shop, url="y-new-site.example.com")
    shop.calls.clear()
    out = _sync(client, catalog=False)
    assert not out.get("gone", {}).get("deleted")
    assert not [x for x in shop.calls if x[1].startswith("/orders/")]
    assert all(_orders(client)[x["id"]]["status"] != "deleted" for x in (a, b))


def test_a_visit_day_is_counted_once_in_the_list_as_in_the_customer_file(client, accounts):
    """An invoice, a visit set done by hand in an older version and a product sale brought over, all on one day: one
    visit in the customers list, as in the customer file; another day makes two."""
    from app.models import Appointment, TradeHistory

    line = client.post("/api/lines", json={"name": "خط روز مراجعه"}).json()
    svc = client.post("/api/services", json={"line_id": line["id"], "name": "خدمت روز مراجعه", "base_price": 3_000_000}).json()
    day = datetime.combine(TODAY - timedelta(days=6), time(10))
    inv = client.post("/api/invoices", json={"customer_name": "مشتری روز مراجعه", "customer_mobile": "09151200112",
                                             "issued_at": day.replace(hour=11).isoformat(),
                                             "items": [{"service_id": svc["id"], "unit_price": 3_000_000}],
                                             "payments": [{"payment_account_id": accounts["صندوق نقدی"], "amount": 3_000_000}]}).json()
    cid = inv["customer_id"]
    with _db() as db:
        db.add_all([Appointment(customer_id=cid, service_id=svc["id"], start_at=day, status="done", quoted_price=3_000_000, notes=""),
                    TradeHistory(source="tizpardaz", kind="sale", at=day.replace(hour=17), customer_id=cid, description="کرم",
                                 qty=1, unit_price=500_000, amount=500_000, fp=f"y-visit-{cid}")])
        db.commit()

    def both():  # noqa: ANN202
        row = next(x for x in client.get("/api/customers", params={"q": "09151200112"}).json()["items"] if x["id"] == cid)
        file = client.get(f"/api/customers/{cid}").json()
        return row["visits"], len({h["date"][:10] for h in file["history"]}), row["total_spent"]

    assert both() == (1, 1, 3_500_000)  # the hand-done visit carries no money
    with _db() as db:
        db.add(TradeHistory(source="tizpardaz", kind="sale", at=day - timedelta(days=2), customer_id=cid, description="کرم",
                            qty=1, unit_price=500_000, amount=500_000, fp=f"y-visit2-{cid}"))
        db.commit()
    assert both() == (2, 2, 4_000_000)


def test_an_order_read_before_this_version_is_still_found_in_the_trash(client, shop):
    """An order booked before the website was recorded (1.20.1 and older) is looked up too: when the website shows the
    same order (number and date placed) in its trash, it is undone; an order of another website with the same id is
    never touched, and never reported deleted."""
    from app.models import WooOrder

    sp = shop.add_product("کرم نسخهٔ قبل", "Y-TRASH-6", 100_000, stock=10)
    _product(client, "کرم نسخهٔ قبل", "Y-TRASH-6", qty=10)
    _connect(client, shop, url="y-trash-6.example.com")
    same = shop.add_order([(sp, 1)], phone="09151200113", first="الف", last="نسخهٔ قبل")
    other = shop.add_order([(sp, 1)], phone="09151200114", first="ب", last="نسخهٔ قبل")
    _sync(client)
    with _db() as db:  # as an upgraded database holds them: no website; `other` looks like another website's order
        db.query(WooOrder).filter(WooOrder.order_id.in_([same["id"], other["id"]])).update({"site": None}, synchronize_session=False)
        db.query(WooOrder).filter(WooOrder.order_id == other["id"]).update({"number": "9999"}, synchronize_session=False)
        db.commit()
    shop.trash(same["id"])
    shop.trash(other["id"])
    _gone_check_due()
    out = _sync(client, catalog=False)
    now = _orders(client)
    assert out["gone"].get("cancelled") == 1 and now[same["id"]]["state"] == "cancelled"
    assert now[other["id"]]["state"] == "booked" and now[other["id"]]["status"] != "deleted"
    with _db() as db:
        assert db.query(WooOrder).filter(WooOrder.order_id == same["id"]).one().site == "y-trash-6.example.com"
        assert db.query(WooOrder).filter(WooOrder.order_id == other["id"]).one().site is None
    shop.orders.pop(other["id"])  # gone for good: still not reported (it may be of another website)
    _gone_check_due()
    assert not _sync(client, catalog=False)["gone"].get("deleted")


def test_older_orders_kept_with_the_websites_own_clock_or_renumbered_are_found_in_the_trash(client, shop):
    """1.18-1.19 kept the date an order was placed in the website's own time zone; a number plugin installed later
    shows another number for the same order: both are still taken as this website's orders when in its trash."""
    from app.models import WooOrder
    from app.services.woo import _dt

    sp = shop.add_product("کرم ساعت سایت", "Y-TRASH-7", 100_000, stock=10)
    _product(client, "کرم ساعت سایت", "Y-TRASH-7", qty=10)
    _connect(client, shop, url="y-trash-7.example.com")
    a = shop.add_order([(sp, 1)], phone="09151200115", first="الف", last="ساعت سایت")
    b = shop.add_order([(sp, 1)], phone="09151200116", first="ب", last="شماره نو")
    _sync(client)
    with _db() as db:
        db.query(WooOrder).filter(WooOrder.order_id.in_([a["id"], b["id"]])).update({"site": None}, synchronize_session=False)
        db.query(WooOrder).filter(WooOrder.order_id == a["id"]).update({"created": _dt(a["date_created"])}, synchronize_session=False)
        db.commit()
    shop.orders[b["id"]]["number"] = f"WEB-{b['id']}"  # how the website shows it now (its date of change untouched)
    shop.trash(a["id"])
    shop.trash(b["id"])
    _gone_check_due()
    out = _sync(client, catalog=False)
    assert out["gone"].get("cancelled") == 2, out
    assert {_orders(client)[x["id"]]["state"] for x in (a, b)} == {"cancelled"}


def test_the_customers_list_total_agrees_with_its_rows_for_every_filter(client):
    for f in ("", "held", "debt", "seller", "no_mobile", "mobile_issue", "no_history", "unknown"):
        for q in ("", "مشتری", "0915"):
            r = client.get("/api/customers", params={"filter": f, "q": q, "limit": 500, "sort": "visits"}).json()
            assert r["total"] == len(r["items"]) or (r["total"] > 500 and len(r["items"]) == 500), (f, q, r["total"])
