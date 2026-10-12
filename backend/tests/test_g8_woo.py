"""Website shop (WooCommerce) audit: secrets, missed orders, stock sent twice, dates, refunds, COD, links."""
import logging
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Deposit, JournalEntry, JournalLine, PaymentAccount, Product
from app.services import woo
from tests.fake_woo import FakeWoo

_n = [0]


def _key() -> tuple[str, str]:
    """A key of its own for every store (a new key = orders read again from the start date)."""
    _n[0] += 1
    return f"ck_g8_{_n[0]:04d}_abcdefghij", f"cs_g8_{_n[0]:04d}_secretsecret"


def _store(**kw) -> FakeWoo:  # noqa: ANN003
    key, secret = _key()
    fake = FakeWoo(key=key, secret=secret, **kw)
    woo._transport = fake.transport()
    return fake


@pytest.fixture()
def shop(client):
    fake = _store()
    yield fake
    woo._transport = None
    client.put("/api/woo/config", json={"enabled": False})


@pytest.fixture()
def done(client):
    """For tests that make their own store: switch the connection off afterwards."""
    yield
    woo._transport = None
    client.put("/api/woo/config", json={"enabled": False})


def _connect(client, shop, start: date | None = None) -> int:
    gw = client.post("/api/accounts", json={"kind": "gateway", "name": f"درگاه گ۸ {shop.ids}"}).json()["id"]
    r = client.put("/api/woo/config", json={"url": "g8shop.example.com", "key": shop.key, "secret": shop.secret, "enabled": True,
                                            "start": (start or date.today() - timedelta(days=5)).isoformat(), "account_id": gw,
                                            "method_accounts": {}})
    assert r.status_code == 200, r.text
    t = client.post("/api/woo/test").json()
    assert t["ok"], t
    return gw


def _sync(client, catalog: bool = True) -> dict:
    r = client.post(f"/api/woo/sync?catalog={'true' if catalog else 'false'}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert not out.get("error"), out
    return out


def _orders(client) -> dict[int, dict]:
    return {o["order_id"]: o for o in client.get("/api/woo/orders?limit=500").json()}


def _balanced(client) -> None:
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"]


def _moves(gw: int) -> list[tuple[datetime, int]]:
    """Money in (+) / out (-) of a payment account, from its ledger lines, oldest first."""
    with SessionLocal() as db:
        acc = db.get(PaymentAccount, gw).ledger_account_id
        return [(at, int(v)) for at, v in db.execute(
            select(JournalEntry.at, JournalLine.debit - JournalLine.credit).join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalLine.account_id == acc).order_by(JournalEntry.at, JournalEntry.id))]


def _product(client, name: str, sku: str, qty: int = 0) -> dict:
    p = client.post("/api/products", json={"name": name, "sku": sku, "sale_price": 1_000_000}).json()
    if qty:
        client.post(f"/api/products/{p['id']}/opening", json={"qty": qty, "unit_cost": 500_000})
    return p


# ---------------------------------------------------------------------------------------------- secrets
def test_key_and_secret_never_reach_the_log_or_the_browser(client, caplog, done):
    # a host that drops the Authorization header: the key goes in the address (query), which httpx logs
    shop = _store(header_auth=False)
    sp = shop.add_product("کرم گ۸ لاگ", "G8-LOG-1", 100_000, stock=3)
    _connect(client, shop)
    with caplog.at_level(logging.INFO):
        _sync(client)
        shop.add_order([(sp, 1)], phone="09151080001", first="لاگ", last="گ۸")
        _sync(client)
    assert client.get("/api/woo/config").json()["config"]["auth"] == "query"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "HTTP Request" in text  # the requests are still logged (useful to find problems) ...
    assert shop.secret not in text and shop.key not in text  # ... without the key and secret
    for path in ("/api/woo/config", "/api/woo/status", "/api/woo/log", "/api/woo/orders", "/api/woo/outbox", "/api/woo/products"):
        body = client.get(path).text
        assert shop.secret not in body and shop.key not in body, path


def test_bad_settings_are_a_clear_message_not_a_crash(client, done):
    shop = _store()
    _connect(client, shop)
    for bad in ({"interval": "abc"}, {"start": "1403/01/01"}, {"account_id": "x"}):
        r = client.put("/api/woo/config", json=bad)
        assert r.status_code == 400, (bad, r.text)


# ---------------------------------------------------------------------------------------------- orders
def test_no_order_is_missed_when_an_order_changes_while_the_pages_are_read(client, shop, monkeypatch):
    monkeypatch.setattr(woo, "PER_PAGE", 2)
    sp = shop.add_product("سرم گ۸ صفحه", "G8-PAGE-1", 100_000, stock=50)
    _connect(client, shop)
    _sync(client)
    # a backlog from two days ago (e.g. the first sync, or the clinic PC was off) ...
    old = (datetime.now() - timedelta(days=2)).replace(microsecond=0)
    orders = [shop.add_order([(sp, 1)], phone=f"0915108{1100 + i}", first="صفحه", last=f"گ۸ {i}", created=old + timedelta(minutes=i))
              for i in range(5)]
    for i, o in enumerate(orders):
        shop._touch(o, old + timedelta(minutes=i, seconds=30))
    # ... and while the program reads page 2, someone edits the first order on the website (it moves to the end)
    def edit_first(page: int) -> None:
        if page == 2 and shop.on_orders_page:
            shop.on_orders_page = None
            shop._touch(orders[0])
    shop.on_orders_page = edit_first
    _sync(client)
    _sync(client)
    got = _orders(client)
    assert [got.get(o["id"], {}).get("state") for o in orders] == ["booked"] * 5


def test_more_than_ten_refunds_are_all_read(client, shop):
    sp = shop.add_product("ماسک گ۸ ده", "G8-TEN-1", 100_000, stock=20)
    p = _product(client, "ماسک گ۸ ده", "G8-TEN-1", qty=20)
    gw = _connect(client, shop)
    o = shop.add_order([(sp, 12)], phone="09151080002", first="ده", last="برگشتی")
    _sync(client)
    line = o["line_items"][0]["id"]
    for _ in range(11):  # eleven pieces given back one by one (the website lists refunds 10 per page)
        shop.refund(o["id"], 100_000, [(line, 1, 100_000)])
    _sync(client)
    row = _orders(client)[o["id"]]
    inv = client.get(f"/api/invoices/{row['invoice_id']}").json()
    assert inv["total"] == 1_000_000 and inv["paid"] == 1_000_000
    assert [(i["quantity"], i["unit_price"], i["discount"]) for i in inv["items"]] == [(1, 1_000_000, 0)]
    assert client.get(f"/api/products/{p['id']}").json()["stock_qty"] == 20 - 1
    with SessionLocal() as db:
        back = db.scalars(select(Deposit).where(Deposit.customer_id == inv["customer_id"], Deposit.status == "refunded")).all()
    assert len(back) == 11 and sum(d.amount for d in back) == 11 * 1_000_000
    assert sum(v for _, v in _moves(gw)) == 1_000_000  # 12 in, 11 back
    _balanced(client)


def test_an_order_with_the_same_new_product_twice_is_booked(client, shop):
    _connect(client, shop)
    _sync(client)
    # a product added on the website after the last catalog read, sold twice in one order (two lines)
    sp = shop.add_product("کرم گ۸ جدید", "G8-DUP-1", 150_000, stock=10)
    o = shop.add_order([(sp, 1), (sp, 2)], phone="09151080003", first="دو", last="سطری")
    out = _sync(client, catalog=False)
    assert out["orders"].get("booked") == 1
    inv = client.get(f"/api/invoices/{_orders(client)[o['id']]['invoice_id']}").json()
    assert inv["total"] == 4_500_000 and len({i["product_id"] for i in inv["items"]}) == 1
    with SessionLocal() as db:
        assert len(db.scalars(select(Product).where(Product.sku == "G8-DUP-1")).all()) == 1
    _balanced(client)


def test_one_broken_order_does_not_stop_the_others(client, shop):
    sp = shop.add_product("کرم گ۸ خراب", "G8-BAD-1", 100_000, stock=10)
    _connect(client, shop)
    _sync(client)
    bad = shop.add_order([(sp, 1)], phone="09151080004", first="خراب", last="گ۸")
    del bad["line_items"][0]["id"]  # something the program did not expect
    good = shop.add_order([(sp, 1)], phone="09151080005", first="سالم", last="گ۸")
    out = _sync(client)
    assert out["orders"].get("booked") == 1 and out["orders"].get("error") == 1
    got = _orders(client)
    assert got[good["id"]]["state"] == "booked" and got[bad["id"]]["state"] == "error" and got[bad["id"]]["note"]
    # fixed on the website: booked on the next run (the failed order is read again)
    bad["line_items"][0]["id"] = shop._id()
    shop._touch(bad)
    _sync(client)
    assert _orders(client)[bad["id"]]["state"] == "booked"


def test_dates_follow_the_clinic_clock_not_the_websites_time_zone(client, done):
    here = datetime.now().astimezone().utcoffset()
    shop = _store(site_offset=here + timedelta(hours=5))  # the website set to another time zone
    sp = shop.add_product("کرم گ۸ ساعت", "G8-TZ-1", 200_000, stock=10)
    gw = _connect(client, shop)
    _sync(client)
    at = datetime.combine(date.today() - timedelta(days=2), datetime.min.time()).replace(hour=22)  # 10 pm here = next day there
    o = shop.add_order([(sp, 1)], phone="09151080006", first="ساعت", last="گ۸", created=at)
    _sync(client)
    inv = client.get(f"/api/invoices/{_orders(client)[o['id']]['invoice_id']}").json()
    assert inv["issued_at"].startswith(at.isoformat(timespec="minutes"))
    assert _moves(gw) == [(at + timedelta(minutes=2), 2_000_000)]  # paid 2 minutes later, the same evening


def test_a_full_refund_gives_the_money_back_on_the_refunds_day(client, shop):
    sp = shop.add_product("سرم گ۸ برگشت", "G8-REF-1", 300_000, stock=10)
    p = _product(client, "سرم گ۸ برگشت", "G8-REF-1", qty=10)
    gw = _connect(client, shop)
    created = (datetime.now() - timedelta(days=3)).replace(microsecond=0)
    o = shop.add_order([(sp, 2)], phone="09151080007", first="برگشت", last="کامل", created=created)
    _sync(client)
    inv_id = _orders(client)[o["id"]]["invoice_id"]
    assert client.get(f"/api/invoices/{inv_id}").json()["paid"] == 6_000_000
    # given back on the website two days ago; the clinic PC syncs only today
    back = (datetime.now() - timedelta(days=2)).replace(microsecond=0)
    shop.refund(o["id"], 600_000, [(o["line_items"][0]["id"], 2, 600_000)], at=back)
    res = _sync(client)["orders"]
    assert res.get("cancelled") == 1 and client.get(f"/api/invoices/{inv_id}").json()["status"] == "void"
    moves = _moves(gw)
    assert sum(v for _, v in moves) == 0
    assert sum(v for at, v in moves if at < back) == 6_000_000  # the money was on the gateway until the refund ...
    assert all(at <= back for at, _ in moves)  # ... and went out on the refund's day, not on the day of the sync
    assert client.get(f"/api/products/{p['id']}").json()["stock_qty"] == 10
    assert _sync(client)["orders"].get("cancelled") is None  # once
    assert sum(v for _, v in _moves(gw)) == 0
    _balanced(client)


def test_cash_on_delivery_is_paid_when_the_order_is_completed(client, shop):
    sp = shop.add_product("کرم گ۸ درب منزل", "G8-COD-1", 250_000, stock=10)
    gw = _connect(client, shop)
    # WooCommerce marks a cash-on-delivery order "paid" as soon as it is placed; the courier brings the money later
    o = shop.add_order([(sp, 2)], phone="09151080008", first="پرداخت", last="درمحل", method="cod", method_title="پرداخت در محل")
    assert o["date_paid"]
    _sync(client)
    inv_id = _orders(client)[o["id"]]["invoice_id"]
    assert client.get(f"/api/invoices/{inv_id}").json()["paid"] == 0 and _moves(gw) == []
    shop.set_status(o["id"], "completed")
    assert _sync(client)["orders"].get("paid") == 1
    assert client.get(f"/api/invoices/{inv_id}").json()["paid"] == 5_000_000
    assert sum(v for _, v in _moves(gw)) == 5_000_000
    _balanced(client)


def test_website_orders_never_use_the_customers_service_deposit(client, shop, accounts):
    sp = shop.add_product("کرم گ۸ بیعانه", "G8-DEP-1", 100_000, stock=10)
    dep = client.post("/api/deposits", json={"customer_name": "بیعانه گ۸", "customer_mobile": "09151080009", "amount": 3_000_000,
                                             "payment_account_id": next(iter(accounts.values())), "notes": "نوبت لیزر"}).json()
    _connect(client, shop)
    o = shop.add_order([(sp, 1)], phone="09151080009", first="بیعانه", last="گ۸")
    _sync(client)
    row = _orders(client)[o["id"]]
    assert row["customer_id"] == dep["customer_id"]
    inv = client.get(f"/api/invoices/{row['invoice_id']}").json()
    assert inv["paid"] == 1_000_000  # paid on the website's gateway
    assert client.get(f"/api/deposits/{dep['id']}").json()["status"] == "held"


def test_two_syncs_at_once_do_not_run_together(client, shop):
    _connect(client, shop)
    assert woo._lock.acquire(blocking=False)
    try:
        assert client.post("/api/woo/sync").json()["busy"] is True
    finally:
        woo._lock.release()


# ---------------------------------------------------------------------------------------------- stock
def test_a_lost_answer_does_not_send_the_stock_change_twice(client, shop):
    sp = shop.add_product("ژل گ۸ تایم‌اوت", "G8-LOST-1", 100_000, stock=10)
    p = _product(client, "ژل گ۸ تایم‌اوت", "G8-LOST-1")
    _connect(client, shop)
    _sync(client)
    client.post("/api/purchases", json={"supplier_name": "پخش گ۸", "items": [{"product_id": p["id"], "quantity": 5, "unit_price": 500_000}]})
    shop.lose_put = 1  # the website saves 10 -> 15 but its answer never arrives
    r = client.post("/api/woo/sync").json()
    assert r["error"] and shop.products[sp]["stock_quantity"] == 15
    _sync(client)
    assert shop.products[sp]["stock_quantity"] == 15  # not 20
    sent = [e for e in client.get("/api/woo/outbox").json() if e["product_id"] == p["id"]]
    assert [e["status"] for e in sent] == ["done"]
    # the next change goes as usual
    client.post("/api/purchases", json={"supplier_name": "پخش گ۸", "items": [{"product_id": p["id"], "quantity": 2, "unit_price": 500_000}]})
    _sync(client)
    assert shop.products[sp]["stock_quantity"] == 17


def test_a_stock_count_and_a_sale_at_the_same_moment_are_not_counted_twice(client, shop):
    sa = shop.add_product("تونر گ۸ الف", "G8-CNT-A", 100_000, stock=10)
    sb = shop.add_product("تونر گ۸ ب", "G8-CNT-B", 100_000, stock=10)
    a = _product(client, "تونر گ۸ الف", "G8-CNT-A", qty=10)
    b = _product(client, "تونر گ۸ ب", "G8-CNT-B", qty=10)
    _connect(client, shop)
    _sync(client)
    client.post("/api/purchases", json={"supplier_name": "پخش گ۸", "items": [{"product_id": a["id"], "quantity": 1, "unit_price": 500_000}]})
    client.post(f"/api/products/{b['id']}/count", json={"counted": 9})
    cust = client.post("/api/customers", json={"full_name": "حضوری گ۸", "mobile": "09151080010"}).json()

    def sell_b(pid: int) -> None:  # while the first product is being sent, one B is sold at the counter (another user)
        if pid == sa:
            shop.on_put = None
            r = client.post("/api/invoices", json={"customer_id": cust["id"], "items": [{"product_id": b["id"], "quantity": 1, "unit_price": 1_000_000}]})
            assert r.status_code == 200, r.text
    shop.on_put = sell_b
    with SessionLocal() as db:  # what the sync job does
        cfg = woo.config(db)
        woo.push_stock(db, woo.Client(cfg), cfg)
    _sync(client)
    assert client.get(f"/api/products/{b['id']}").json()["stock_qty"] == 8
    assert shop.products[sb]["stock_quantity"] == 8  # the count sent the stock with the sale in it: the sale isn't sent again
    assert shop.products[sa]["stock_quantity"] == 11


def test_a_product_linked_by_hand_stays_linked(client, shop):
    sp = shop.add_product("کرم گ۸ دستی", "G8-HAND-01", 100_000, stock=5)
    p = _product(client, "کرم گ۸ دستی", "G8HAND01", qty=5)  # the SKU is written differently here
    _connect(client, shop)
    _sync(client)
    row = next(r for r in client.get("/api/woo/products").json()["rows"] if r["woo_id"] == sp)
    assert row["product"] is None
    assert client.post("/api/woo/products/link", json={"woo_id": sp, "product_id": p["id"]}).json()["ok"]
    _sync(client)  # reads the website's products again
    row = next(r for r in client.get("/api/woo/products").json()["rows"] if r["woo_id"] == sp)
    assert row["product"] and row["product"]["id"] == p["id"]
    o = shop.add_order([(sp, 2)], phone="09151080011", first="دستی", last="گ۸")
    _sync(client)
    inv = client.get(f"/api/invoices/{_orders(client)[o['id']]['invoice_id']}").json()
    assert inv["items"][0]["product_id"] == p["id"]
    assert client.get(f"/api/products/{p['id']}").json()["stock_qty"] == 3
    # unlinked by hand: not linked again by the next read
    assert client.post("/api/woo/products/link", json={"woo_id": sp, "product_id": None}).json()["ok"]
    _sync(client)
    assert next(r for r in client.get("/api/woo/products").json()["rows"] if r["woo_id"] == sp)["product"] is None
