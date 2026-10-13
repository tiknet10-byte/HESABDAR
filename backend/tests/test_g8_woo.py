"""Website shop (WooCommerce) audit: secrets, missed orders, stock sent twice, dates, refunds, COD, links."""
import logging
import threading
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import Deposit, Invoice, JournalEntry, JournalLine, PaymentAccount, Product
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


def _connect(client, shop, start: date | None = None, url: str = "g8shop.example.com") -> int:
    gw = client.post("/api/accounts", json={"kind": "gateway", "name": f"درگاه گ۸ {shop.ids}"}).json()["id"]
    r = client.put("/api/woo/config", json={"url": url, "key": shop.key, "secret": shop.secret, "enabled": True,
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


def test_the_key_is_never_sent_to_a_plain_http_address(client, done):
    shop = _store(header_auth=False)  # a host that needs the key in the address ...
    _connect(client, shop)
    shop.redirect_http = True  # ... and a website that sends every request on to http://
    r = client.post("/api/woo/sync").json()
    assert "https" in r["error"] and shop.insecure == 0
    assert client.post("/api/woo/test").json()["ok"] is False and shop.insecure == 0


def test_a_wrong_key_is_not_put_in_the_address(client, done):
    shop = _store()
    _connect(client, shop, url="g8key.example.com")
    assert client.get("/api/woo/config").json()["config"]["auth"] == "basic"
    shop.secret = "cs_changed_on_the_website"  # the key was deleted / made again on the website
    r = client.post("/api/woo/sync").json()
    assert "اشتباه" in r["error"]
    assert client.get("/api/woo/config").json()["config"]["auth"] == "basic"  # still sent in the header, not the address


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
    shop._touch(o)  # e.g. a note added on the website while the courier is on the way: read again, still not paid
    assert _sync(client)["orders"].get("unchanged") == 1
    assert client.get(f"/api/invoices/{inv_id}").json()["paid"] == 0 and _moves(gw) == []
    shop.set_status(o["id"], "completed")
    done_at = datetime.now()
    assert _sync(client)["orders"].get("paid") == 1
    assert client.get(f"/api/invoices/{inv_id}").json()["paid"] == 5_000_000
    [(at, v)] = _moves(gw)
    assert v == 5_000_000 and abs(at - done_at) < timedelta(minutes=2)  # on the day the courier brought it
    _balanced(client)


def test_a_payment_confirmed_later_is_dated_by_the_clinic_clock(client, done):
    here = datetime.now().astimezone().utcoffset()
    shop = _store(site_offset=here - timedelta(hours=5))  # the website's clock shows 5 hours earlier than the clinic
    sp = shop.add_product("کرم گ۸ کارت", "G8-BACS-1", 150_000, stock=10)
    gw = _connect(client, shop)
    o = shop.add_order([(sp, 2)], status="on-hold", paid=False, phone="09151080012", first="کارت", last="به‌کارت", method="bacs",
                       method_title="کارت به کارت", created=datetime.now() - timedelta(days=2))
    _sync(client)
    assert _moves(gw) == []
    shop.set_status(o["id"], "processing", paid=True)  # the transfer is confirmed now
    paid_at = datetime.now()
    assert _sync(client)["orders"].get("paid") == 1
    [(at, v)] = _moves(gw)
    assert v == 3_000_000 and abs(at - paid_at) < timedelta(minutes=2)  # not 5 hours earlier
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


def test_the_sync_button_during_the_periodic_job_does_not_book_an_order_twice(client, shop):
    sp = shop.add_product("کرم گ۸ هم‌زمانی", "G8-RACE-1", 100_000, stock=10)
    _connect(client, shop)
    _sync(client)
    o = shop.add_order([(sp, 1)], phone="09151080019", first="هم‌زمانی", last="گ۸")
    entered, release = threading.Event(), threading.Event()

    def hold(page: int) -> None:  # the periodic job is reading the orders ...
        shop.on_orders_page = None
        entered.set()
        release.wait(10)
    shop.on_orders_page = hold

    def periodic() -> None:
        with SessionLocal() as db:
            woo.run_sync(db)
    t = threading.Thread(target=periodic)
    t.start()
    try:
        assert entered.wait(10)
        assert client.post("/api/woo/sync").json()["busy"] is True  # ... when someone presses the button
    finally:
        release.set()
        t.join(10)
    _sync(client)
    with SessionLocal() as db:
        n = db.scalar(select(func.count(Invoice.id)).where(Invoice.notes.like(f"سفارش سایت #{o['number']}%")))
    assert n == 1 and _orders(client)[o["id"]]["state"] == "booked"


def test_an_order_put_back_to_pending_payment_is_taken_back(client, shop):
    sp = shop.add_product("کرم گ۸ برگشت به انتظار", "G8-PEND-1", 100_000, stock=10)
    p = _product(client, "کرم گ۸ برگشت به انتظار", "G8-PEND-1", qty=10)
    gw = _connect(client, shop)
    o = shop.add_order([(sp, 2)], status="on-hold", paid=False, phone="09151080018", first="انتظار", last="گ۸", method="bacs")
    _sync(client)
    first = _orders(client)[o["id"]]["invoice_id"]
    assert client.get(f"/api/products/{p['id']}").json()["stock_qty"] == 8
    shop.set_status(o["id"], "pending")  # the website takes the order back (and puts its goods back in its stock)
    assert _sync(client)["orders"].get("cancelled") == 1
    assert client.get(f"/api/invoices/{first}").json()["status"] == "void" and _orders(client)[o["id"]]["state"] == "waiting"
    assert client.get(f"/api/products/{p['id']}").json()["stock_qty"] == 10
    shop.set_status(o["id"], "processing", paid=True)  # paid after all: sold again
    assert _sync(client)["orders"].get("booked") == 1
    inv = client.get(f"/api/invoices/{_orders(client)[o['id']]['invoice_id']}").json()
    assert inv["id"] != first and inv["total"] == inv["paid"] == 2_000_000
    assert client.get(f"/api/products/{p['id']}").json()["stock_qty"] == 8 and sum(v for _, v in _moves(gw)) == 2_000_000
    _balanced(client)


def test_an_order_deleted_while_the_pages_are_read_does_not_hide_another(client, shop, monkeypatch):
    monkeypatch.setattr(woo, "PER_PAGE", 1)
    sp = shop.add_product("سرم گ۸ حذفی", "G8-DEL-1", 100_000, stock=50)
    _connect(client, shop)
    _sync(client)
    old = (datetime.now() - timedelta(days=2)).replace(microsecond=0)
    orders = [shop.add_order([(sp, 1)], phone=f"0915108{1300 + i}", first="حذفی", last=f"گ۸ {i}", created=old + timedelta(minutes=i))
              for i in range(3)]
    for i, o in enumerate(orders):
        shop._touch(o, old + timedelta(minutes=i))

    def delete_first(page: int) -> None:  # the first order is deleted on the website just after it was read
        if page == 2 and shop.on_orders_page:
            shop.on_orders_page = None
            del shop.orders[orders[0]["id"]]
    shop.on_orders_page = delete_first
    _sync(client)
    got = _orders(client)
    assert [got.get(o["id"], {}).get("state") for o in orders[1:]] == ["booked", "booked"]


def test_new_orders_arriving_while_the_pages_are_read_do_not_hold_the_reading_back(client, shop, monkeypatch):
    monkeypatch.setattr(woo, "PER_PAGE", 1)
    sp = shop.add_product("سرم گ۸ شلوغ", "G8-BUSY-1", 100_000, stock=50)
    _connect(client, shop)
    _sync(client)
    old = (datetime.now() - timedelta(days=2)).replace(microsecond=0)
    orders = [shop.add_order([(sp, 1)], phone=f"0915108{1400 + i}", first="شلوغ", last=f"گ۸ {i}", created=old + timedelta(minutes=i))
              for i in range(3)]
    for i, o in enumerate(orders):
        shop._touch(o, old + timedelta(minutes=i))
    new = []

    def busy(page: int) -> None:  # a busy website: a new order comes in during every reading
        if page == 2 and len(new) < 3:
            new.append(shop.add_order([(sp, 1)], phone=f"0915108{1410 + len(new)}", first="شلوغ", last="تازه"))
    shop.on_orders_page = busy
    _sync(client)
    shop.on_orders_page = None
    got = _orders(client)
    assert [got.get(o["id"], {}).get("state") for o in orders + new] == ["booked"] * (3 + len(new))
    with SessionLocal() as db:
        cursor = datetime.fromisoformat(woo.state(db)["cursor"])
    assert cursor > (old + timedelta(minutes=2)).astimezone(timezone.utc).replace(tzinfo=None)  # the next run starts later


def test_orders_changed_in_the_same_second_are_all_read(client, shop, monkeypatch):
    monkeypatch.setattr(woo, "PER_PAGE", 2)
    sp = shop.add_product("سرم گ۸ هم‌زمان", "G8-TIE-1", 100_000, stock=50)
    _connect(client, shop)
    _sync(client)
    same = (datetime.now() - timedelta(days=1)).replace(microsecond=0)
    orders = [shop.add_order([(sp, 1)], phone=f"0915108{1200 + i}", first="هم‌زمان", last=f"گ۸ {i}", created=same - timedelta(minutes=5 - i))
              for i in range(5)]
    for o in orders:  # e.g. a bulk status change on the website: all changed in the same second
        shop._touch(o, same)
    _sync(client)
    got = _orders(client)
    assert [got.get(o["id"], {}).get("state") for o in orders] == ["booked"] * 5


def test_an_order_changed_during_a_long_reading_is_read_again(client, shop, monkeypatch):
    monkeypatch.setattr(woo, "PER_PAGE", 1)
    sp = shop.add_product("ژل گ۸ طولانی", "G8-LONG-1", 100_000, stock=50)
    _connect(client, shop)
    _sync(client)
    old = (datetime.now() - timedelta(days=2)).replace(microsecond=0)
    a = shop.add_order([(sp, 1)], phone="09151080013", first="طولانی", last="الف", created=old)
    b = shop.add_order([(sp, 1)], phone="09151080014", first="طولانی", last="ب", created=old + timedelta(minutes=1))
    shop._touch(a, old + timedelta(minutes=1))
    shop._touch(b, old + timedelta(minutes=2))
    _sync(client)
    # a slow reading that began 40 minutes ago (the website's clock): after A was read, A was cancelled (30 minutes
    # ago) and B changed (5 minutes ago, read with this change)
    now = datetime.now().replace(microsecond=0)
    shop.clock_shift = -timedelta(minutes=40)

    def change(page: int) -> None:
        if page == 2 and shop.on_orders_page:
            shop.on_orders_page = None
            shop.set_status(a["id"], "cancelled")
            shop._touch(a, now - timedelta(minutes=30))
            shop._touch(b, now - timedelta(minutes=5))
    shop.on_orders_page = change
    _sync(client)
    shop.clock_shift = timedelta(0)
    assert _orders(client)[a["id"]]["state"] == "booked"  # read before it was cancelled ...
    _sync(client)
    assert _orders(client)[a["id"]]["state"] == "cancelled"  # ... so it is read again on the next run


def test_money_goes_back_on_the_account_it_came_in_on_and_on_the_refunds_day(client, shop):
    sp = shop.add_product("کرم گ۸ حساب", "G8-ACC-1", 100_000, stock=10)
    gw = _connect(client, shop)
    created = (datetime.now() - timedelta(days=3)).replace(microsecond=0)
    o = shop.add_order([(sp, 3)], phone="09151080015", first="حساب", last="درگاه", created=created)
    _sync(client)
    paid_at = created + timedelta(minutes=2)
    assert _moves(gw) == [(paid_at, 3_000_000)]
    # later the owner chooses another account for website orders
    gw2 = client.post("/api/accounts", json={"kind": "gateway", "name": f"درگاه دوم گ۸ {shop.ids}"}).json()["id"]
    assert client.put("/api/woo/config", json={"account_id": gw2}).status_code == 200
    # one piece given back yesterday; today the order is cancelled (the rest given back today, without a refund on the site)
    yday = (datetime.now() - timedelta(days=1)).replace(microsecond=0)
    shop.refund(o["id"], 100_000, [(o["line_items"][0]["id"], 1, 100_000)], at=yday)
    assert _sync(client)["orders"].get("rebooked") == 1
    inv = client.get(f"/api/invoices/{_orders(client)[o['id']]['invoice_id']}").json()
    assert inv["total"] == 2_000_000 and inv["paid"] == 2_000_000
    shop.set_status(o["id"], "cancelled")
    today = datetime.now()
    assert _sync(client)["orders"].get("cancelled") == 1
    assert _sync(client)["orders"].get("cancelled") is None  # once
    moves = _moves(gw)
    assert sum(v for _, v in moves) == 0 and _moves(gw2) == []
    assert sum(v for at, v in moves if at <= paid_at) == 3_000_000  # received on the order's day ...
    out = [(at, v) for at, v in moves if at > paid_at]
    assert [v for _, v in out] == [-1_000_000, -2_000_000]  # ... given back on the refund's day, the rest today
    assert out[0][0] == yday and abs(out[1][0] - today) < timedelta(minutes=2)
    assert client.get(f"/api/invoices/{inv['id']}").json()["status"] == "void"
    _balanced(client)


def test_money_taken_at_the_clinic_for_a_website_order_is_never_lost(client, shop):
    sp = shop.add_product("کرم گ۸ صندوق", "G8-CASH-1", 200_000, stock=10)
    gw = _connect(client, shop)
    cash = client.post("/api/accounts", json={"kind": "cash", "name": f"صندوق گ۸ {shop.ids}"}).json()["id"]
    when = datetime.now() - timedelta(days=1)
    a = shop.add_order([(sp, 2)], status="on-hold", paid=False, phone="09151080016", first="صندوق", last="الف", method="bacs", created=when)
    b = shop.add_order([(sp, 1)], status="on-hold", paid=False, phone="09151080017", first="صندوق", last="ب", method="bacs", created=when)
    _sync(client)
    got = _orders(client)
    for o, amount in ((a, 4_000_000), (b, 2_000_000)):  # the customers paid at the clinic («دریافت بدهی»)
        r = client.post(f"/api/invoices/{got[o['id']]['invoice_id']}/payments", json={"payment_account_id": cash, "amount": amount})
        assert r.status_code == 200, r.text
    # A: changed on the website (one piece instead of two), then marked paid there
    li = a["line_items"][0]
    li["quantity"], li["subtotal"], li["total"], a["total"] = 1, "200000.00", "200000.00", "200000.00"
    shop._touch(a)
    assert _sync(client)["orders"].get("rebooked") == 1
    inv_a = client.get(f"/api/invoices/{_orders(client)[a['id']]['invoice_id']}").json()
    assert inv_a["total"] == 2_000_000 and inv_a["paid"] == 2_000_000  # paid by the money taken at the clinic
    shop.set_status(a["id"], "processing", paid=True)
    _sync(client)
    assert client.get(f"/api/invoices/{inv_a['id']}").json()["paid"] == 2_000_000
    # B: cancelled on the website
    shop.set_status(b["id"], "cancelled")
    assert _sync(client)["orders"].get("cancelled") == 1
    # the money of the clinic is still in its cash box; what is not used stays the customers' credit; no gateway money
    assert sum(v for _, v in _moves(cash)) == 6_000_000 and _moves(gw) == []
    with SessionLocal() as db:
        held = {d.customer_id: (d.amount, d.kind) for d in db.scalars(select(Deposit).where(
            Deposit.customer_id.in_([inv_a["customer_id"], _orders(client)[b["id"]]["customer_id"]]), Deposit.status == "held"))}
    assert held == {inv_a["customer_id"]: (2_000_000, "credit"), _orders(client)[b["id"]]["customer_id"]: (2_000_000, "credit")}
    assert any("اعتبار مشتری ماند" in x["message"] for x in client.get("/api/woo/log").json())
    _balanced(client)


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


def test_a_product_unlinked_by_hand_is_not_linked_again_by_its_sku(client, shop):
    sp = shop.add_product("کرم گ۸ جداشده", "G8-UNL-1", 100_000, stock=5)
    p = _product(client, "کرم گ۸ جداشده", "G8-UNL-1", qty=5)
    _connect(client, shop)
    _sync(client)
    assert next(r for r in client.get("/api/woo/products").json()["rows"] if r["woo_id"] == sp)["product"]["id"] == p["id"]
    assert client.post("/api/woo/products/link", json={"woo_id": sp, "product_id": None}).json()["ok"]
    _sync(client)
    assert next(r for r in client.get("/api/woo/products").json()["rows"] if r["woo_id"] == sp)["product"] is None


def test_another_website_gets_its_own_currency(client, done):
    shop = _store()  # toman
    _connect(client, shop)
    _sync(client)
    # moved to another website whose prices are in Rial; synced without pressing "test" first
    other = _store(currency="IRR")
    sp = other.add_product("سرم گ۸ ریالی", "G8-IRR-1", 2_500_000, stock=3)
    r = client.put("/api/woo/config", json={"url": "g8other.example.com", "key": other.key, "secret": other.secret})
    assert r.status_code == 200 and r.json()["config"]["unit"] == ""
    _sync(client)
    assert client.get("/api/woo/config").json()["config"]["unit"] == "IRR"
    row = next(r for r in client.get("/api/woo/products").json()["rows"] if r["woo_id"] == sp)
    assert row["site_price"] == 2_500_000  # not 25,000,000 (read as toman)


def test_a_paid_order_put_back_to_pending_is_not_a_refund(client, shop):
    """A paid order set back to "pending payment" and paid again: its money is received once, on its own day - no
    refund today (the website gave nothing back) and no second receipt."""
    sp = shop.add_product("کرم گ۸ انتظار پرداخت‌شده", "G8-PEND-2", 100_000, stock=10)
    _product(client, "کرم گ۸ انتظار پرداخت‌شده", "G8-PEND-2", qty=10)
    gw = _connect(client, shop)
    day = datetime.now().replace(microsecond=0) - timedelta(days=2)
    o = shop.add_order([(sp, 2)], phone="09151080020", first="انتظار", last="پرداخت‌شده گ۸", created=day)
    _sync(client)
    shop.set_status(o["id"], "pending")
    assert _sync(client)["orders"].get("cancelled") == 1
    assert _orders(client)[o["id"]]["paid"] is False and _orders(client)[o["id"]]["state"] == "waiting"
    today = date.today()
    assert sum(v for _, v in _moves(gw)) == 0 and not [v for at, v in _moves(gw) if at.date() == today]
    shop.set_status(o["id"], "processing", paid=True)
    assert _sync(client)["orders"].get("booked") == 1
    inv = client.get(f"/api/invoices/{_orders(client)[o['id']]['invoice_id']}").json()
    assert inv["total"] == inv["paid"] == 2_000_000
    assert sum(v for _, v in _moves(gw)) == 2_000_000 and not [v for at, v in _moves(gw) if at.date() == today]
    back = client.get("/api/reports/money-returned", params={"start": (today - timedelta(days=5)).isoformat()}).json()
    assert not [x for x in back["items"] if x["kind"] == "refund" and "پرداخت‌شده گ۸" in (x["customer"] or "")]
    _balanced(client)


def test_an_order_of_a_product_unlinked_by_hand_does_not_touch_the_skus_product(client, shop):
    """The owner unlinked a website product from the product with its SKU ("it is not this one"): its order does not
    take that product's stock; it gets a product of its own (as an unknown website product does), kept as its link."""
    wrong = _product(client, "کرم گ۸ ۵۰ میل", "G8-UNL-3", qty=5)
    sp = shop.add_product("کرم گ۸ ۳۰ میل", "G8-UNL-3", 100_000, stock=5)
    _connect(client, shop)
    _sync(client)
    assert client.post("/api/woo/products/link", json={"woo_id": sp, "product_id": None}).json()["ok"]
    o = shop.add_order([(sp, 1)], phone="09151080021", first="جداشده", last="گ۸")
    assert _sync(client)["orders"].get("booked") == 1
    assert client.get(f"/api/products/{wrong['id']}").json()["stock_qty"] == 5
    inv = client.get(f"/api/invoices/{_orders(client)[o['id']]['invoice_id']}").json()
    own = inv["items"][0]["product_id"]
    assert own != wrong["id"] and inv["total"] == 1_000_000
    _sync(client)
    row = next(r for r in client.get("/api/woo/products").json()["rows"] if r["woo_id"] == sp)
    assert row["product"]["id"] == own  # kept as its link, not taken back by the SKU
    _balanced(client)


def test_a_product_removed_from_the_website_is_not_linked_or_priced(client, shop):
    """The same SKU listed again on the website with a new price, the old listing deleted: the new price is kept and
    the deleted listing is not counted (it is gone from the list in the same read)."""
    p = _product(client, "سرم گ۸ آگهی تازه", "G8-RELIST-1")
    old = shop.add_product("سرم گ۸ آگهی قدیمی", "G8-RELIST-1", 300_000, stock=2)
    _connect(client, shop)
    assert _sync(client)["catalog"]["linked"] == 1
    del shop.products[old]
    shop.add_product("سرم گ۸ آگهی تازه", "G8-RELIST-1", 400_000, stock=2)
    out = _sync(client)
    assert out["catalog"]["linked"] == 1
    assert client.get(f"/api/products/{p['id']}").json()["online_price"] == 4_000_000
