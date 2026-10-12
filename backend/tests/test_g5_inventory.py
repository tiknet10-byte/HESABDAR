"""Costing engine and product pages: voided invoices, back-dated counts, inventory value == ledger, delete, who sees costs.
Every money figure is checked to the Rial, under the three costing methods."""
import random
from datetime import datetime, timedelta
from types import SimpleNamespace as M

import pytest

from app.services.inventory import replay

METHODS = ("average", "fifo", "periodic")
# fixed past dates inside one Jalali month (Shahrivar 1405), so the monthly average is one period
D = datetime(2026, 9, 1, 10, 0)


def _bal(client, code):
    rows = client.get("/api/ledger/trial-balance").json()["rows"]
    r = next((x for x in rows if x["code"] == code), None)
    return (r["debit"] - r["credit"]) if r else 0


def _books(client):
    return {c: _bal(client, c) for c in ("1300", "5400", "5500")}


def _delta(client, before):
    now = _books(client)
    return {c: now[c] - before[c] for c in now}


@pytest.fixture()
def method(client, request):
    client.put("/api/products-settings", json={"method": request.param})
    yield request.param
    client.put("/api/products-settings", json={"method": "average"})


def _product(client, name, price=500_000):
    r = client.post("/api/products", json={"name": name, "sale_price": price})
    assert r.status_code == 200, r.text
    return r.json()


def _customer(client, name, mobile):
    r = client.post("/api/customers", json={"full_name": name, "mobile": mobile})
    assert r.status_code == 200, r.text
    return r.json()


def _sell(client, cid, pid, qty, at):
    r = client.post("/api/invoices", json={"customer_id": cid, "issued_at": at.isoformat(), "items": [{"product_id": pid, "quantity": qty}]})
    assert r.status_code == 200, r.text
    return r.json()


def _buy(client, pid, qty, unit, at):
    r = client.post("/api/purchases", json={"supplier_name": "پخش گروه پنج", "items": [{"product_id": pid, "quantity": qty, "unit_price": unit}],
                                           "at": at.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


def _void(client, inv, payments):
    r = client.post(f"/api/invoices/{inv['id']}/void?payments={payments}")
    assert r.status_code == 200, r.text


def _report_cogs(client, pid):
    row = next((r for r in client.get("/api/reports/products").json()["products"] if r["id"] == pid), None)
    return row["cogs"] if row else 0


def _moves(client, pid):
    """kardex moves (oldest first) with the invoice they belong to"""
    return client.get(f"/api/products/{pid}").json()["moves"][::-1]


# ------------------------------------------------------------------ pure engine
def _mv(i, kind, qty, at, cost=0, ref=None):
    return M(id=i, kind=kind, qty=qty, at=at, cost=cost, ref_move_id=ref)


@pytest.mark.parametrize("how", METHODS)
def test_engine_void_of_unbacked_sale_cancels_its_own_waiting_units(how):
    """Two sales without stock, the 2nd voided (cancel or a real later return), then a purchase: the purchase costs
    the live sale, the voided one nets 0, nothing is left waiting."""
    for ret_at in (D + timedelta(days=1), D + timedelta(days=1, hours=5)):  # same time (cancel) / later (refund)
        ms = [_mv(1, "sale", -1, D), _mv(2, "sale", -1, D + timedelta(days=1)), _mv(3, "sale_return", 1, ret_at, ref=2),
              _mv(4, "purchase", 1, D + timedelta(days=2), cost=300)]
        r = replay(ms, how)
        assert r["costs"][1] == (300, False)
        assert r["costs"][2][0] - r["costs"][3][0] == 0 and not r["costs"][2][1]
        assert r["pending"] == 0 and r["qty"] == 0 and r["book_value"] == 0


@pytest.mark.parametrize("how,cost2,value", [("average", 750, 750), ("fifo", 500, 1000), ("periodic", 750, 750)])
def test_engine_cancelled_invoice_never_happened(how, cost2, value):
    """FIFO: a mistaken (cancelled) invoice must not put its goods back as the newest layer."""
    ms = [_mv(1, "opening", 5, D, cost=500), _mv(2, "purchase", 5, D + timedelta(days=1), cost=1000),
          _mv(3, "sale", -5, D + timedelta(days=2)), _mv(4, "sale_return", 5, D + timedelta(days=2), ref=3),
          _mv(5, "sale", -5, D + timedelta(days=3))]
    r = replay(ms, how)
    assert r["costs"][3] == (0, False) and r["costs"][4] == (0, False)
    assert r["costs"][5] == (cost2, False) and r["value"] == value
    # the same as if the mistaken invoice had never been entered
    clean = replay([m for m in ms if m.id not in (3, 4)], how)
    assert clean["costs"][5] == r["costs"][5] and clean["value"] == r["value"]


@pytest.mark.parametrize("how", METHODS)
def test_engine_partly_backed_sale_returned_later(how):
    """1 in stock, 3 sold (2 waiting at an estimate), all 3 returned later: the 2 waiting are cancelled, the covered
    one comes back at its real cost; a purchase after that is plain stock."""
    ms = [_mv(1, "purchase", 1, D, cost=100), _mv(2, "sale", -3, D + timedelta(days=1)),
          _mv(3, "sale_return", 3, D + timedelta(days=2), ref=2), _mv(4, "purchase", 2, D + timedelta(days=3), cost=400)]
    r = replay(ms, how)
    # monthly average: every purchase of the month is in the sale's cost (3 x 500/3); the return takes it back
    cost = 500 if how == "periodic" else 100
    assert r["costs"][2] == (cost, False) and r["costs"][3] == (cost, False)
    assert r["qty"] == 3 and r["pending"] == 0 and r["book_value"] == 500
    # a partial return first cancels waiting units only (nothing comes back to stock at a cost)
    ms = [_mv(1, "purchase", 1, D, cost=100), _mv(2, "sale", -3, D + timedelta(days=1)), _mv(3, "sale_return", 1, D + timedelta(days=2), ref=2)]
    r = replay(ms, how)
    assert r["costs"][3] == (0, False) and r["costs"][2] == (200, True) and r["pending"] == 1 and r["book_value"] == -100


def test_engine_books_always_balance_random_histories():
    """For random histories (purchases, sales beyond stock, cancels, partial / later returns, counts) and every
    method: value of everything in - everything out == book value (= ledger 1300), stock = in stock - waiting,
    and a sale returned in full nets 0."""
    for seed in range(1500):
        rnd = random.Random(seed)
        moves, sales = [], []
        for i in range(1, rnd.randint(2, 12)):
            at = D + timedelta(days=rnd.randint(0, 70), minutes=rnd.randint(0, 900))
            k = rnd.choice(["purchase", "opening", "sale", "sale", "adjust_out", "adjust_in", "ret", "ret"])
            if k in ("purchase", "opening"):
                q = rnd.randint(1, 7)
                moves.append(_mv(i, k, q, at, cost=q * rnd.randint(1, 999) + rnd.randint(0, 5)))
            elif k in ("sale", "adjust_out"):
                moves.append(_mv(i, k, -rnd.randint(1, 6), at))
                if k == "sale":
                    sales.append(moves[-1])
            elif k == "adjust_in":
                moves.append(_mv(i, k, rnd.randint(1, 4), at))
            elif sales:
                s = sales.pop(rnd.randrange(len(sales)))
                same = rnd.random() < 0.5
                moves.append(_mv(i, "sale_return", -s.qty, s.at if same else s.at + timedelta(days=rnd.randint(0, 9)), ref=s.id))
        moves.sort(key=lambda m: (m.at, m.id))
        by_id = {m.id: m for m in moves}
        for how in METHODS:
            r = replay(moves, how)
            book = sum(r["costs"][m.id][0] * (1 if m.qty > 0 else -1) for m in moves)
            assert book == r["book_value"], (seed, how)
            assert r["qty"] == r["in_stock"] - r["pending"], (seed, how)
            for m in moves:
                if m.kind == "sale_return":
                    assert r["costs"][m.id][0] == r["costs"][by_id[m.ref_move_id].id][0], (seed, how)


# ------------------------------------------------------------------ through the API, against the ledger
@pytest.mark.parametrize("method", METHODS, indirect=True)
def test_void_of_unbacked_sale_then_purchase_report_equals_ledger(client, method):
    """Website-style: two sales before the goods were recorded, the 2nd invoice voided, then the purchase.
    Product report COGS == ledger 5400, product value == ledger 1300, the voided invoice costs nothing."""
    k = METHODS.index(method)
    for j, payments in enumerate(("cancel", "refund")):
        base = _books(client)
        p = _product(client, f"کرم گروه پنج {method} {payments}")
        c = _customer(client, f"خریدار پنج {method} {payments}", f"091510500{k}{j}")
        t = datetime.now().replace(microsecond=0) - timedelta(hours=6)
        inv1 = _sell(client, c["id"], p["id"], 1, t)
        inv2 = _sell(client, c["id"], p["id"], 1, t + timedelta(hours=1))
        _void(client, inv2, payments)
        _buy(client, p["id"], 1, 300_000, t + timedelta(hours=2) if payments == "cancel" else datetime.now().replace(microsecond=0))
        moves = _moves(client, p["id"])
        sale1 = next(m for m in moves if m["invoice_id"] == inv1["id"])
        assert sale1["cost"] == 300_000 and not sale1["estimated"]
        voided = [m for m in moves if m["invoice_id"] == inv2["id"]]
        assert sum(m["cost"] if m["kind"] == "sale" else -m["cost"] for m in voided) == 0
        d = _delta(client, base)
        assert d["5400"] == 300_000 == _report_cogs(client, p["id"])
        pv = client.get(f"/api/products/{p['id']}").json()
        assert pv["stock_qty"] == 0 and d["1300"] == 0 == pv["stock_value"]
        assert client.get("/api/ledger/trial-balance").json()["total_debit"] == client.get("/api/ledger/trial-balance").json()["total_credit"]


@pytest.mark.parametrize("method,cost2,value", [("average", 750_000, 750_000), ("fifo", 500_000, 1_000_000), ("periodic", 750_000, 750_000)],
                         indirect=["method"])
def test_cancelled_invoice_does_not_change_later_costs(client, method, cost2, value):
    """Opening 5 @100,000, purchase 5 @200,000, an invoice of 5 cancelled as a mistake, then a real sale of 5."""
    base = _books(client)
    p = _product(client, f"سرم گروه پنج {method}")
    c = _customer(client, f"مشتری لغو {method}", f"0915105010{METHODS.index(method)}")
    r = client.post(f"/api/products/{p['id']}/opening", json={"qty": 5, "unit_cost": 100_000, "at": D.isoformat()})
    assert r.status_code == 200, r.text
    _buy(client, p["id"], 5, 200_000, D + timedelta(days=1))
    wrong = _sell(client, c["id"], p["id"], 5, D + timedelta(days=2))
    _void(client, wrong, "cancel")
    real = _sell(client, c["id"], p["id"], 5, D + timedelta(days=3))
    sale = next(m for m in _moves(client, p["id"]) if m["invoice_id"] == real["id"])
    assert sale["cost"] == cost2
    pv = client.get(f"/api/products/{p['id']}").json()
    d = _delta(client, base)
    assert pv["stock_qty"] == 5 and pv["stock_value"] == value == d["1300"] - 0
    assert d["5400"] == cost2 == _report_cogs(client, p["id"])
    assert d["1300"] + d["5400"] == 1_500_000  # everything bought is either still here or sold


@pytest.mark.parametrize("method", METHODS, indirect=True)
def test_sale_beyond_stock_value_equals_ledger(client, method):
    """Buy 1 @100,000, sell 3: 2 units at an estimate. The product's value, the page summary and the kardex
    all show what the ledger shows (-200,000); a later purchase makes it exact."""
    base = _books(client)
    s0 = client.get("/api/products/summary").json()["stock_value"]
    p = _product(client, f"ماسک گروه پنج {method}")
    c = _customer(client, f"مشتری منفی {method}", f"0915105020{METHODS.index(method)}")
    _buy(client, p["id"], 1, 100_000, D)
    _sell(client, c["id"], p["id"], 3, D + timedelta(minutes=10))
    pv = client.get(f"/api/products/{p['id']}").json()
    d = _delta(client, base)
    assert d["1300"] == -200_000 == pv["stock_value"] and pv["stock_qty"] == -2
    assert pv["moves"][0]["balance_value"] == -200_000 and pv["moves"][0]["estimated"]
    assert client.get("/api/products/summary").json()["stock_value"] - s0 == -200_000
    _buy(client, p["id"], 4, 150_000, D + timedelta(minutes=20))
    pv = client.get(f"/api/products/{p['id']}").json()
    d = _delta(client, base)
    # 100,000 + 2 x 150,000; monthly average: 3 x (700,000 / 5)
    cogs = 420_000 if method == "periodic" else 400_000
    assert d["5400"] == cogs == _report_cogs(client, p["id"])
    assert d["1300"] == 700_000 - cogs == pv["stock_value"] and pv["stock_qty"] == 2
    assert pv["moves"][0]["balance_value"] == pv["stock_value"] and not any(m["estimated"] for m in pv["moves"])
    assert client.get("/api/products/summary").json()["stock_value"] - s0 == 700_000 - cogs


def test_archived_product_keeps_its_value_in_the_summary(client):
    s0 = client.get("/api/products/summary").json()
    i0 = _bal(client, "1300")
    p = _product(client, "لوسیون بایگانی گروه پنج")
    client.post(f"/api/products/{p['id']}/opening", json={"qty": 5, "unit_cost": 100_000, "at": D.isoformat()})
    r = client.delete(f"/api/products/{p['id']}").json()
    assert r["archived"] is True
    s1 = client.get("/api/products/summary").json()
    assert s1["stock_value"] - s0["stock_value"] == 500_000 == _bal(client, "1300") - i0
    assert s1["units"] - s0["units"] == 5 and s1["count"] == s0["count"]  # not counted as an active product


# ------------------------------------------------------------------ back-dated count
@pytest.mark.parametrize("method", METHODS, indirect=True)
def test_back_dated_count_uses_the_stock_of_that_day(client, method):
    """10 on day 0, 3 sold on day 2. A count of 10 dated day 1 is right (no difference); 9 dated day 1 is a shortage
    of 1 at that day's cost - not a phantom +3 found."""
    base = _books(client)
    p = _product(client, f"تونر گروه پنج {method}")
    c = _customer(client, f"مشتری شمارش {method}", f"0915105030{METHODS.index(method)}")
    client.post(f"/api/products/{p['id']}/opening", json={"qty": 10, "unit_cost": 100_000, "at": D.isoformat()})
    _sell(client, c["id"], p["id"], 3, D + timedelta(days=2))
    r = client.post(f"/api/products/{p['id']}/count", json={"counted": 10, "at": (D + timedelta(days=1)).isoformat()}).json()
    assert r["difference"] == 0 and r["stock_qty"] == 7
    r = client.post(f"/api/products/{p['id']}/count", json={"counted": 9, "at": (D + timedelta(days=1)).isoformat()}).json()
    assert r["difference"] == -1 and r["difference_cost"] == 100_000 and r["stock_qty"] == 6
    d = _delta(client, base)
    assert d["5500"] == 100_000 and d["5400"] == 300_000 and d["1300"] == 600_000 == r["stock_value"]
    # a count without a date is today's: compared with the stock now
    assert client.post(f"/api/products/{p['id']}/count", json={"counted": 6}).json()["difference"] == 0
    assert client.post(f"/api/products/{p['id']}/count", json={"counted": 7}).json()["difference"] == 1
    assert client.get(f"/api/products/{p['id']}").json()["stock_qty"] == 7


# ------------------------------------------------------------------ delete
def test_delete_product_archives_when_anything_points_at_it(client):
    p = _product(client, "محصول خرید باطل گروه پنج")
    pur = _buy(client, p["id"], 2, 50_000, D)
    assert client.post(f"/api/purchases/{pur['id']}/void").status_code == 200
    r = client.delete(f"/api/products/{p['id']}")
    assert r.status_code == 200 and r.json()["archived"] is True  # its purchase row still points at it
    assert client.get(f"/api/products/{p['id']}").json()["is_active"] is False
    fresh = _product(client, "محصول بی‌سابقه گروه پنج")
    assert client.delete(f"/api/products/{fresh['id']}").json()["archived"] is False
    assert client.get(f"/api/products/{fresh['id']}").status_code == 404


def test_delete_product_with_website_link_or_imported_history_archives(client):
    from app.core.db import SessionLocal
    from app.models import TradeHistory, WooProduct

    linked, old = _product(client, "محصول سایت گروه پنج"), _product(client, "محصول سابقه گروه پنج")
    with SessionLocal() as db:
        db.add(WooProduct(id=951050001, product_id=linked["id"], sku="G5-LINK", name="سایت"))
        db.add(TradeHistory(kind="purchase", at=D, product_id=old["id"], qty=1, amount=100_000, fp="g5-delete-history"))
        db.commit()
    for p in (linked, old):
        r = client.delete(f"/api/products/{p['id']}")
        assert r.status_code == 200 and r.json()["archived"] is True, r.text
        assert client.get(f"/api/products/{p['id']}").json()["is_active"] is False


# ------------------------------------------------------------------ who sees costs
def _as_role(client, name, role):
    from fastapi.testclient import TestClient

    from app.main import app
    r = client.post("/api/auth/users", json={"username": name, "password": "Passw0rd1", "role": role})
    assert r.status_code == 200, r.text
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": name, "password": "Passw0rd1"}).json()["access_token"]
    return c


def test_staff_and_receptionist_do_not_see_costs(client):
    p = _product(client, "کرم هزینه پنهان گروه پنج")
    pur = _buy(client, p["id"], 4, 120_000, D)
    sup_id = client.get(f"/api/purchases/{pur['id']}").json()["supplier_id"]
    own = client.get(f"/api/products/{p['id']}").json()
    assert own["unit_cost"] == 120_000 and own["stock_value"] == 480_000 and own["moves"][0]["cost"] == 480_000
    assert client.get(f"/api/purchases/{pur['id']}").json()["items"][0]["unit_price"] == 120_000
    for c in (_as_role(client, "g5_staff", "staff"), _as_role(client, "g5_rec", "receptionist")):
        row = next(x for x in c.get("/api/products", params={"q": p["code"]}).json() if x["id"] == p["id"])
        assert row["stock_qty"] == 4 and row["sale_price"] == 500_000
        assert all(row[k] is None for k in ("unit_cost", "last_purchase_cost", "stock_value", "margin", "margin_pct"))
        pv = c.get(f"/api/products/{p['id']}").json()
        assert pv["unit_cost"] is None and pv["moves"][0]["qty"] == 4 and pv["moves"][0]["balance_qty"] == 4
        assert all(m["cost"] is None and m["unit_cost"] is None and m["balance_value"] is None for m in pv["moves"])
        s = c.get("/api/products/summary").json()
        assert s["stock_value"] is None and s["owed_to_suppliers"] is None and s["units"] >= 4
        pl = next(x for x in c.get("/api/purchases").json() if x["id"] == pur["id"])
        assert pl["total"] is None and pl["due"] is None and pl["number"] == pur["number"]
        pd = c.get(f"/api/purchases/{pur['id']}").json()
        assert pd["items"][0]["quantity"] == 4 and pd["items"][0]["unit_price"] is None and pd["items"][0]["cost"] is None
        assert pd["payments"] == []
        sup = next(x for x in c.get("/api/suppliers").json() if x["id"] == sup_id)
        assert sup["owed"] is None and sup["bought"] is None and sup["history_total"] is None
        sd = c.get(f"/api/suppliers/{sup_id}").json()
        assert sd["owed"] is None and all(x["total"] is None for x in sd["purchases"])
        assert all(d["total"] is None for d in sd["docs"])
        assert c.get("/api/reports/products").status_code == 403
    # the owner still sees everything
    assert client.get("/api/products/summary").json()["stock_value"] is not None
    assert next(x for x in client.get("/api/suppliers").json() if x["id"] == sup_id)["bought"] >= 480_000


def test_trade_doc_prices_hidden_without_reports():
    """Tizpardaz invoices: a purchase's prices and a sale's cost / profit are left out for staff."""
    from app.api.products import _doc

    buy = {"kind": "purchase", "total": 900, "items_total": 1000, "paid": 0, "discount": 100, "additions": 0, "extras": [{"amount": 5}],
           "items": [{"qty": 2, "unit_price": 500, "amount": 1000, "cost": None, "profit": None}]}
    out = _doc(buy, False)
    assert out["total"] is None and out["items"][0]["unit_price"] is None and out["items"][0]["qty"] == 2 and out["extras"] == []
    sale = {"kind": "sale", "total": 900, "items": [{"qty": 1, "unit_price": 900, "amount": 900, "cost": 600, "profit": 300}]}
    out = _doc(sale, False)
    assert out["total"] == 900 and out["items"][0]["amount"] == 900 and out["items"][0]["cost"] is None and out["items"][0]["profit"] is None
    assert _doc(buy, True) is buy


def test_existing_data_recosted_once_at_start(client):
    """Data saved by the old engine (a cancelled invoice still carrying its cost, a stale stock value) is worked
    out again once when the program starts, and the books agree again."""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Product, StockMove
    from app.services import inventory, settings_store

    p = _product(client, "کرم داده قدیمی گروه پنج")
    c = _customer(client, "مشتری داده قدیمی", "09151050401")
    client.post(f"/api/products/{p['id']}/opening", json={"qty": 2, "unit_cost": 100_000, "at": D.isoformat()})
    _void(client, _sell(client, c["id"], p["id"], 1, D + timedelta(days=1)), "cancel")
    right = _books(client)
    with SessionLocal() as db:  # as the old engine left it
        prod = db.get(Product, p["id"])
        for m in db.scalars(select(StockMove).where(StockMove.product_id == p["id"], StockMove.kind.in_(("sale", "sale_return")))):
            m.cost = 100_000
            inventory._post_move(db, m, prod)
        prod.stock_value = 123
        settings_store.set_value(db, "products.engine", 1)
        db.commit()
    assert client.get(f"/api/products/{p['id']}").json()["stock_value"] == 123
    with SessionLocal() as db:
        assert inventory.recost_if_engine_changed(db) >= 1
        db.commit()
    with SessionLocal() as db:
        assert inventory.recost_if_engine_changed(db) == 0  # only once
    pv = client.get(f"/api/products/{p['id']}").json()
    assert pv["stock_value"] == 200_000 and pv["stock_qty"] == 2
    assert all(m["cost"] == 0 for m in pv["moves"] if m["kind"] in ("sale", "sale_return"))
    assert _books(client) == right
