"""The website shop (WooCommerce): orders become invoices, stock changes here go to the website."""
from datetime import date, datetime, timedelta

import pytest

from app.services import woo
from tests.fake_woo import FakeWoo


def balanced(client):
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"]
    return {r["code"]: r for r in tb["rows"]}


@pytest.fixture()
def shop(client):
    fake = FakeWoo()
    woo._transport = fake.transport()
    yield fake
    woo._transport = None
    client.put("/api/woo/config", json={"enabled": False})


def _sync(client):
    r = client.post("/api/woo/sync")
    assert r.status_code == 200, r.text
    out = r.json()
    assert not out.get("error"), out
    return out


def _connect(client, shop, start=None):
    gw = client.post("/api/accounts", json={"kind": "gateway", "name": f"درگاه زرین‌پال {shop.ids}"}).json()["id"]
    r = client.put("/api/woo/config", json={"url": "shop.example.com/", "key": shop.key, "secret": shop.secret, "enabled": True,
                                            "start": (start or date.today() - timedelta(days=1)).isoformat(), "account_id": gw})
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]
    assert cfg["url"] == "https://shop.example.com" and cfg["secret"] == woo.MASK and cfg["key"] != shop.key  # never sent back
    t = client.post("/api/woo/test").json()
    assert t["ok"] and t["currency"] == "IRT" and t["currency_label"] == "تومان"
    assert "woo.config" not in client.get("/api/settings").json()
    return gw


def test_website_orders_become_invoices_on_the_right_customer(client, shop):
    a = client.post("/api/products", json={"name": "ضد آفتاب لایتنس", "sku": "LT-EX-MLX-001", "sale_price": 6_000_000}).json()
    b = client.post("/api/products", json={"name": "پماد داو", "sku": "DOV-GF-POM-150", "sale_price": 3_000_000}).json()
    client.post(f"/api/products/{a['id']}/opening", json={"qty": 10, "unit_cost": 3_000_000})
    client.post(f"/api/products/{b['id']}/opening", json={"qty": 5, "unit_cost": 1_500_000})
    sara = client.post("/api/customers", json={"full_name": "سارا احمدی", "mobile": "09158880001"}).json()
    mina = client.post("/api/customers", json={"full_name": "مینا رضائی"}).json()  # brought over from Tizpardaz: no mobile
    pa = shop.add_product("ضد آفتاب لایتنس اکسترا", "lt-ex-mlx-001", 500_000, stock=10)
    pb = shop.add_product("پماد داو", "DOV-GF-POM-150", 300_000, stock=5)
    _, (v1, _v2) = shop.add_variable("کرم آبرسان", [("۵۰ میل", "AB-50", 400_000, 6), ("۱۰۰ میل", "AB-100", 700_000, 3)])
    shop.add_product("کارت هدیه", "", 100_000, manage=False)
    gw = _connect(client, shop)

    old = shop.add_order([(pa, 1)], phone="09158880001", first="سارا", last="احمدی", created=datetime.now() - timedelta(days=3))
    o1 = shop.add_order([(pa, 2)], phone="0915 888 0001", first="سارا", last="احمدی", shipping=50_000, coupon=100_000)
    o2 = shop.add_order([(pb, 1)], status="on-hold", paid=False, phone="09158880002", first="مینا", last="رضایی", method="bacs",
                        method_title="کارت به کارت")
    o3 = shop.add_order([(v1, 2)], phone="09158880003", first="نیلوفر", last="کاظمی")
    shop.add_order([(pa, 1)], status="pending", paid=False, phone="09158880003", first="نیلوفر", last="کاظمی")
    out = _sync(client)
    assert out["catalog"]["linked"] == 2 and out["orders"] == {"before_start": 1, "booked": 3, "waiting": 1}
    assert out.get("stock", {}) == {}  # website orders are already out of the website's stock: nothing is sent back

    orders = {o["order_id"]: o for o in client.get("/api/woo/orders").json()}
    assert old["id"] not in orders
    inv1 = client.get(f"/api/invoices/{orders[o1['id']]['invoice_id']}").json()
    # 2 x 500,000 toman - 100,000 coupon + 50,000 shipping = 950,000 toman, paid on the gateway
    assert inv1["total"] == 9_500_000 and inv1["paid"] == 9_500_000 and inv1["channel"] == "online" and inv1["customer_id"] == sara["id"]
    line = next(i for i in inv1["items"] if i["product_id"])
    assert line["product_id"] == a["id"] and line["quantity"] == 2 and line["unit_price"] == 5_000_000 and line["discount"] == 1_000_000
    assert any(i["description"] == "هزینهٔ ارسال سفارش سایت" and i["unit_price"] == 500_000 for i in inv1["items"])
    # the namesake brought over without a mobile is the same person: she gets the mobile, the bank transfer is not paid yet
    assert orders[o2["id"]]["customer_id"] == mina["id"] and client.get(f"/api/customers/{mina['id']}").json()["mobile"] == "09158880002"
    inv2 = client.get(f"/api/invoices/{orders[o2['id']]['invoice_id']}").json()
    assert inv2["total"] == 3_000_000 and inv2["paid"] == 0
    # a new customer with her mobile; the variation that isn't here became a product (to be completed by the user)
    nil = client.get(f"/api/customers/{orders[o3['id']]['customer_id']}").json()
    assert nil["full_name"] == "نیلوفر کاظمی" and nil["mobile"] == "09158880003" and nil["source"] == "woocommerce"
    inv3 = client.get(f"/api/invoices/{orders[o3['id']]['invoice_id']}").json()
    made = client.get(f"/api/products/{inv3['items'][0]['product_id']}").json()
    assert made["sku"] == "AB-50" and made["stock_qty"] == -2 and inv3["total"] == 8_000_000
    assert any("ساخته شد" in x["message"] for x in client.get("/api/woo/log").json())
    # stock here follows the website's orders; the last online price is remembered
    pa_here = client.get(f"/api/products/{a['id']}").json()
    assert pa_here["stock_qty"] == 8 and pa_here["last_online_price"] == 5_000_000 and pa_here["online_price"] == 5_000_000
    assert client.get(f"/api/products/{b['id']}").json()["stock_qty"] == 4
    balanced(client)

    # nothing changed: nothing happens (orders read again are left as they are)
    assert set(_sync(client)["orders"]) <= {"unchanged", "waiting", "before_start"}
    # the bank transfer arrives; one of the two creams is given back; the sunscreen order is fully refunded
    shop.set_status(o2["id"], "processing", paid=True)
    shop.refund(o3["id"], 400_000, [(o3["line_items"][0]["id"], 1, 400_000)])
    shop.refund(o1["id"], 950_000)
    res = _sync(client)["orders"]
    assert (res.get("paid"), res.get("rebooked"), res.get("cancelled"), res.get("booked")) == (1, 1, 1, None)
    assert client.get(f"/api/invoices/{inv2['id']}").json()["paid"] == 3_000_000
    assert client.get(f"/api/invoices/{inv1['id']}").json()["status"] == "void"
    assert client.get(f"/api/products/{a['id']}").json()["stock_qty"] == 10
    orders = {o["order_id"]: o for o in client.get("/api/woo/orders").json()}
    inv3b = client.get(f"/api/invoices/{orders[o3['id']]['invoice_id']}").json()
    assert inv3b["id"] != inv3["id"] and inv3b["total"] == 4_000_000 and inv3b["paid"] == 4_000_000
    assert client.get(f"/api/products/{made['id']}").json()["stock_qty"] == -1
    # money of the gateway: everything received on the order days, the refunds paid back today
    acc = {r["code"]: r for r in client.get("/api/ledger/trial-balance").json()["rows"]}
    gw_row = next(r for r in acc.values() if r["name"].startswith("درگاه زرین‌پال"))
    assert gw_row["balance"] == 3_000_000 + 4_000_000  # 9.5M in and out, 8M in and 4M out, 3M in
    returned = client.get("/api/reports/money-returned").json()
    assert returned["paid_back"] >= 9_500_000 + 4_000_000
    balanced(client)
    assert gw  # used


def test_stock_changes_here_go_to_the_website(client, shop):
    a = client.post("/api/products", json={"name": "سرم هیالورونیک", "sku": "HY-30", "sale_price": 2_000_000}).json()
    client.post("/api/products", json={"name": "ماسک مو", "sku": "MM-1", "sale_price": 1_000_000}).json()
    c = client.post("/api/products", json={"name": "فقط در کلینیک", "sku": "ONLY-HERE", "sale_price": 1_000_000}).json()
    client.post(f"/api/products/{a['id']}/opening", json={"qty": 6, "unit_cost": 1_000_000})  # before connecting: not sent
    sa = shop.add_product("سرم هیالورونیک", "HY-30", 200_000, stock=4)
    sb = shop.add_product("ماسک مو", "MM-1", 100_000, manage=False)
    _connect(client, shop)
    _sync(client)
    rows = {r["sku"]: r for r in client.get("/api/woo/products").json()["rows"]}
    assert rows["HY-30"]["stock_differs"] and rows["HY-30"]["site_stock"] == 4 and rows["HY-30"]["product"]["stock_qty"] == 6
    assert "ONLY-HERE" in [x["sku"] for x in client.get("/api/woo/products").json()["not_on_site"]]

    # first alignment: the website = the stock here (and stock management is switched on where it was off)
    r = client.post("/api/woo/stock/align", json={"all": True}).json()
    assert r["queued"] == 2 and shop.products[sa]["stock_quantity"] == 6 and shop.products[sb]["manage_stock"] is True
    assert shop.products[sb]["stock_quantity"] == 0

    # a purchase adds to the website's stock, an in-person sale takes from it, a void puts it back
    pur = client.post("/api/purchases", json={"supplier_name": "پخش", "items": [{"product_id": a["id"], "quantity": 5, "unit_price": 1_000_000},
                                                                               {"product_id": c["id"], "quantity": 2, "unit_price": 500_000}]}).json()
    cust = client.post("/api/customers", json={"full_name": "خریدار حضوری", "mobile": "09158880004"}).json()
    inv = client.post("/api/invoices", json={"customer_id": cust["id"], "items": [{"product_id": a["id"], "quantity": 2, "unit_price": 2_100_000}]}).json()
    shop.add_order([(sa, 1)], phone="09158880005", first="آنلاین", last="خریدار")  # the website sold one meanwhile
    out = _sync(client)
    assert out["orders"].get("booked") == 1
    assert out["stock"] == {"sent": 2, "skipped": 1}  # ONLY-HERE is not on the website
    assert shop.products[sa]["stock_quantity"] == 6 + 5 - 2 - 1
    here = client.get(f"/api/products/{a['id']}").json()
    assert here["stock_qty"] == 6 + 5 - 2 - 1 and here["last_sale_price"] == 2_100_000 and here["price_in_person"] == 2_100_000
    assert client.post(f"/api/invoices/{inv['id']}/void?reason=x&payments=cancel").status_code == 200
    assert client.post(f"/api/purchases/{pur['id']}/void").status_code == 200
    _sync(client)
    assert shop.products[sa]["stock_quantity"] == 6 + 5 - 2 - 1 + 2 - 5
    # a stock count: the website gets the counted quantity
    client.post(f"/api/products/{a['id']}/count", json={"counted": 9})
    _sync(client)
    assert shop.products[sa]["stock_quantity"] == 9
    sent = client.get("/api/woo/outbox").json()
    assert sent[0]["kind"] == "set" and sent[0]["status"] == "done" and sent[0]["result"].endswith("→ 9")

    # website down: changes wait and go later
    shop.down = True
    client.post(f"/api/products/{a['id']}/opening", json={"qty": 1, "unit_cost": 1_000_000})
    r = client.post("/api/woo/sync").json()
    assert "در دسترس نیست" in r["error"] and r["status"]["outbox_pending"] == 1
    shop.down = False
    _sync(client)
    assert shop.products[sa]["stock_quantity"] == 10

    # switched off: nothing is queued
    client.put("/api/woo/config", json={"enabled": False})
    client.post(f"/api/products/{a['id']}/opening", json={"qty": 1, "unit_cost": 1_000_000})
    assert client.get("/api/woo/status").json()["outbox_pending"] == 0
    balanced(client)


def test_last_sale_price_is_filled_in(client):
    p = client.post("/api/products", json={"name": "کرم دست", "sku": None, "sale_price": 900_000, "online_price": 950_000}).json()
    assert p["price_in_person"] == 900_000 and p["price_online"] == 950_000
    c = client.post("/api/customers", json={"full_name": "مشتری قیمت", "mobile": "09158880006"}).json()
    earlier = (datetime.now() - timedelta(days=2)).replace(microsecond=0)
    client.post("/api/invoices", json={"customer_id": c["id"], "items": [{"product_id": p["id"], "quantity": 1, "unit_price": 1_000_000}]})
    # an invoice entered later for an earlier day doesn't replace the newer price
    client.post("/api/invoices", json={"customer_id": c["id"], "issued_at": earlier.isoformat(),
                                       "items": [{"product_id": p["id"], "quantity": 1, "unit_price": 800_000}]})
    p = client.get(f"/api/products/{p['id']}").json()
    assert p["last_sale_price"] == 1_000_000 and p["price_in_person"] == 1_000_000
    # no price given: the last selling price is used
    inv = client.post("/api/invoices", json={"customer_id": c["id"], "items": [{"product_id": p["id"], "quantity": 2}]}).json()
    assert inv["items"][0]["unit_price"] == 1_000_000
    online = client.post("/api/invoices", json={"customer_id": c["id"], "channel": "online", "items": [{"product_id": p["id"], "quantity": 1}]}).json()
    assert online["items"][0]["unit_price"] == 950_000
    # a new list price is the price from now on
    body = {k: p[k] for k in ("name", "sku", "brand", "category", "unit", "online_price", "reorder_level", "notes", "is_active")}
    p = client.put(f"/api/products/{p['id']}", json={**body, "sale_price": 1_200_000}).json()
    assert p["last_sale_price"] is None and p["price_in_person"] == 1_200_000


def test_order_amounts_are_exact_with_odd_prices_tax_fees_and_refunds():
    o = {"id": 1, "total": "1185.50", "shipping_total": "40.00", "total_tax": "45.50", "refunds": [{"id": 9, "total": "-200.00"}],
         "fee_lines": [{"name": "بسته‌بندی", "total": "20.00"}, {"name": "تخفیف ویژه", "total": "-20.00"}],
         "line_items": [{"id": 11, "product_id": 5, "variation_id": 0, "sku": "A", "name": "A", "quantity": 3, "subtotal": "1000.00", "total": "900.00"},
                        {"id": 12, "product_id": 6, "variation_id": 0, "sku": "B", "name": "B", "quantity": 2, "subtotal": "200.00", "total": "200.00"}],
         "billing": {}}
    refunds = [{"id": 9, "amount": "200.00", "line_items": [{"product_id": 6, "variation_id": 0, "quantity": -2, "total": "-200.00",
                                                             "meta_data": [{"key": "_refunded_item_id", "value": "12"}]}]}]
    for f in (1, 10, 10_000):  # Rial, Toman, thousand Toman
        pl = woo.plan(o, refunds, f)
        line = pl["lines"][0]
        assert len(pl["lines"]) == 1 and line["quantity"] == 3  # B was fully given back
        assert line["unit_price"] * 3 - line["discount"] == 900 * f  # 333.33.. a piece: the rounding is in the discount
        extras = sum(x["unit_price"] for x in pl["extras"])
        assert 900 * f + extras - pl["discount"] == pl["target"] == round(985.5 * f)  # = what the website kept
        assert pl["refunded"] == 200 * f
    assert woo.rial("1185.50", 10) == 11_855 and woo.rial("", 10) == 0 and woo.rial(None, 1) == 0
    with pytest.raises(woo.WooError):
        woo.factor("USD")
