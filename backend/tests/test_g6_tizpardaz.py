"""Tizpardaz imports: a customer's credit is a credit (not a service deposit), re-imports stay merge-safe, two
accounts of one customer add up, undo keeps what was used since, stock of a product that already has moves comes in
as opening stock at Tizpardaz's price, invoice discounts are spread over the items. Money checked to the Rial."""
import io
from datetime import datetime, timedelta

import openpyxl
import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import Appointment, Customer, Deposit, Product, TradeDoc, TradeHistory, WooOutbox
from app.services import accounting, tizpardaz, woo_queue

CHEAD = ["کد حساب", "عنوان حساب", "بدهکار", "بستانکار"]
PHEAD = ["کد کالا", "نام کالا", "تعداد بدهکار", "تعداد بستانکار", "فی آخرین خرید", "قیمت فروش1", "sku"]
JHEAD = ["نوع سند", "شماره سند", "تاریخ", "عنوان حساب", "شرح", "مقدار", "فی", "بدهکار", "بستانکار"]


def _xlsx(rows):
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _up(client, kind, rows):
    r = client.post("/api/import/tizpardaz/preview", files={"file": (f"{kind}.xlsx", _xlsx(rows), "application/octet-stream")},
                    data={"kind": kind, "unit": "toman"})
    assert r.status_code == 200, r.text
    return r.json()


def _commit(client, b, **body):
    r = client.post(f"/api/import/tizpardaz/{b['id']}/commit", json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _undo(client, b):
    r = client.post(f"/api/import/tizpardaz/{b['id']}/undo")
    assert r.status_code == 200, r.text
    return r.json()


def _ledger(client):
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"]
    return {r["code"]: r["debit"] - r["credit"] for r in tb["rows"]}


def _d(before, after, code):
    return after.get(code, 0) - before.get(code, 0)


def _bal(client, cid):
    return client.get(f"/api/customers/{cid}").json()["balance"]


def _by_tp(code):
    with SessionLocal() as db:
        return db.scalar(select(Customer.id).where(Customer.tp_code == str(code)))


def _customer(client, name, mobile):
    r = client.post("/api/customers", json={"full_name": name, "mobile": mobile})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _product(client, name, **kw):
    r = client.post("/api/products", json={"name": name, "sale_price": 1, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_customers_credit_is_a_credit_and_balances_are_never_dated_in_the_future(client):
    b = _up(client, "customers", [CHEAD, [60101, "اعتبارشش(سارا)", None, 300_000]])
    assert b["rows"][0]["credit_as"] == "deposit"
    future = (datetime.now() + timedelta(days=3)).isoformat(timespec="minutes")
    r = client.post(f"/api/import/tizpardaz/{b['id']}/commit", json={"choices": {}, "balances": True, "at": future})
    assert r.status_code == 400 and "آینده" in r.json()["detail"]
    assert client.get(f"/api/import/tizpardaz/{b['id']}").json()["status"] == "review"  # nothing was brought over
    before = _ledger(client)
    res = _commit(client, b, choices={}, balances=True)
    assert res["credits"] == 1 and res["credit_amount"] == 3_000_000
    cid = _by_tp(60101)
    with SessionLocal() as db:
        dep = db.scalar(select(Deposit).where(Deposit.customer_id == cid))
        # product software money of the customer: a credit (pays anything, never forfeited), not a service deposit
        assert (dep.kind, dep.amount, dep.status, dep.service_id) == ("credit", 3_000_000, "held", None)
        with pytest.raises(accounting.AccountingError):
            accounting.close_deposit(db, dep, "forfeit")
        db.rollback()
    after = _ledger(client)
    assert _d(before, after, "2100") == -3_000_000 and _d(before, after, "3200") == 3_000_000
    assert _bal(client, cid)["deposits_held"] == 3_000_000


def test_reimport_after_a_merge_is_not_posted_twice_and_a_new_customer_gets_its_balance(client):
    a1 = _customer(client, "مینا اکبرشش", "09151060001")
    a2 = _customer(client, "مینا اکبرشش", "09151060002")
    rows = [CHEAD, [60201, "اکبرشش(مینا)", 300_000, None]]
    b = _up(client, "customers", rows)
    assert b["rows"][0]["status"] == "ambiguous"
    _commit(client, b, choices={str(b["rows"][0]["row"]): a2}, balances=True)
    assert _bal(client, a2)["receivable"] == 3_000_000
    r = client.post("/api/customers/merge", json={"keep_id": a1, "drop_ids": [a2]})
    assert r.status_code == 200, r.text
    assert _bal(client, a1)["receivable"] == 3_000_000
    # the same list again: the balance is known on the merged person (by their ledger lines), not posted again
    again = _up(client, "customers", rows)
    assert (again["rows"][0]["status"], again["rows"][0]["customer_id"]) == ("linked", a1)
    res = _commit(client, again, choices={}, balances=True)
    assert res.get("balance_already") == 1 and not res.get("debts")
    assert _bal(client, a1)["receivable"] == 3_000_000
    # a new person of a later list (they may get the removed record's id): their debt is brought over
    nb = _up(client, "customers", [CHEAD, [60202, "نورشش(لاله)", 400_000, None]])
    res = _commit(client, nb, choices={}, balances=True)
    assert res["new"] == 1 and res["debts"] == 1 and not res.get("balance_already")
    assert _bal(client, _by_tp(60202))["receivable"] == 4_000_000


def test_two_accounts_of_one_customer_are_added_up_never_dropped(client):
    rz = _customer(client, "رضا کاظمشش", "09151060003")
    b = _up(client, "customers", [CHEAD, [60301, "کاظمشش(رضا)", 100_000, None], [60302, "كاظمشش(رضا)", 250_000, None]])
    assert [r["customer_id"] for r in b["rows"]] == [rz, rz] and all(r["shared"] for r in b["rows"])
    assert b["summary"]["shared"] == 2  # the preview says so
    res = _commit(client, b, choices={}, balances=True)
    assert res["debts"] == 2 and res["debt_amount"] == 3_500_000 and not res.get("balance_already")
    assert _bal(client, rz)["receivable"] == 3_500_000
    u = _undo(client, b)
    assert u["balances"] == 2 and _bal(client, rz)["receivable"] == 0
    with SessionLocal() as db:
        assert db.get(Customer, rz).tp_code is None  # every code given back, last change first


def test_undoing_customers_keeps_a_debt_that_was_received(client, accounts):
    pos = next(iter(accounts.values()))
    before = _ledger(client)
    b = _up(client, "customers", [CHEAD, [60401, "بدهکارشش(نرگس)", 500_000, None], [60402, "فاکتورشش(الهه)", 100_000, None],
                                  [60403, "دستنخوردهشش(مریم)", 70_000, None]])
    _commit(client, b, choices={}, balances=True)
    narges, elahe, maryam = _by_tp(60401), _by_tp(60402), _by_tp(60403)
    # Narges pays 2,000,000 of her 5,000,000 opening debt
    r = client.post(f"/api/customers/{narges}/receive", json={"amount": 2_000_000, "payment_account_id": pos})
    assert r.status_code == 200, r.text
    # Elahe buys here on credit (3,000,000) and pays exactly that: «دریافت بدهی» settles the invoice first
    inv = client.post("/api/invoices", json={"customer_id": elahe, "items": [{"description": "خدمت تستشش", "unit_price": 3_000_000}]}).json()
    got = client.post(f"/api/customers/{elahe}/receive", json={"amount": 3_000_000, "payment_account_id": pos}).json()
    assert got["paid"] == [{"invoice": inv["number"], "amount": 3_000_000}]
    u = _undo(client, b)
    assert u["balances"] == 2 and u["balances_kept"] == 1
    assert _bal(client, narges)["receivable"] == 3_000_000  # her debt stays with its payment: never a false credit
    assert _bal(client, elahe)["receivable"] == 0
    assert client.get(f"/api/customers/{maryam}").status_code == 404  # nothing else of hers: she goes with the file
    after = _ledger(client)
    assert _d(before, after, "1200") == 3_000_000 and _d(before, after, "3200") == -5_000_000


def test_undo_keeps_a_credit_that_was_used_even_after_its_invoice_was_voided(client):
    before = _ledger(client)
    b = _up(client, "customers", [CHEAD, [60501, "اعتبارمصرفشش(شیوا)", None, 200_000]])
    _commit(client, b, choices={}, balances=True)
    cid = _by_tp(60501)
    with SessionLocal() as db:
        did = db.scalar(select(Deposit.id).where(Deposit.customer_id == cid))
    inv = client.post("/api/invoices", json={"customer_id": cid, "items": [{"description": "خدمت اعتبارشش", "unit_price": 2_000_000}],
                                             "deposit_ids": [did]}).json()
    assert inv["paid"] == 2_000_000, inv
    assert client.post(f"/api/invoices/{inv['id']}/void?payments=cancel").status_code == 200
    assert _bal(client, cid)["deposits_held"] == 2_000_000 and _bal(client, cid)["receivable"] == 0
    u = _undo(client, b)
    assert u.get("credits_kept") == 1 and not u.get("credits")
    bal = _bal(client, cid)
    assert (bal["receivable"], bal["deposits_held"]) == (0, 2_000_000)  # the books still match the credit
    after = _ledger(client)
    assert _d(before, after, "2100") == -2_000_000 and _d(before, after, "1200") == 0 and _d(before, after, "3200") == 2_000_000


def test_a_seller_credit_turned_into_a_payable_goes_back_with_its_file(client):
    before = _ledger(client)
    b = _up(client, "customers", [CHEAD, [60601, "فروشندهشش(کامران)", None, 2_000_000]])
    _commit(client, b, choices={}, balances=True)
    cid = _by_tp(60601)
    _product(client, "کالای فروشندهشش")
    jr = _up(client, "journal", [JHEAD, ["فاکتور خرید", 6061, "1405/03/10", "کالا", "کالای فروشندهشش از فروشندهشش(کامران)", 4, 500_000, 2_000_000, 0],
                                 ["فاکتور خرید", 6061, "1405/03/10", "فروشندهشش(کامران)", "فاکتور 6061", None, None, 0, 2_000_000]])
    _commit(client, jr)
    found = [x for x in client.get("/api/import/tizpardaz/seller-credits").json() if x["customer_id"] == cid]
    assert len(found) == 1
    assert client.post("/api/import/tizpardaz/seller-credits", json={"deposit_ids": [found[0]["deposit_id"]]}).json()["converted"] == 1
    assert client.get(f"/api/customers/{cid}").json()["we_owe"] == 20_000_000
    u = _undo(client, b)
    assert u["payables"] == 1
    f = client.get(f"/api/customers/{cid}").json()  # kept: he has invoices brought over
    assert f["we_owe"] == 0 and f["balance"]["deposits_held"] == 0
    after = _ledger(client)
    assert _d(before, after, "2300") == 0 and _d(before, after, "3200") == 0 and _d(before, after, "2100") == 0


def test_stock_of_a_product_with_moves_is_opening_stock_at_tizpardaz_price(client):
    a = _product(client, "کرم دورچشم موجودیشش")
    assert client.post(f"/api/products/{a['id']}/opening", json={"qty": 3, "unit_cost": 100}).status_code == 200
    s = _product(client, "کرم بی موجودیشش")
    cust = _customer(client, "خریدار کرمشش", "09151060004")
    inv = client.post("/api/invoices", json={"customer_id": cust, "items": [{"product_id": s["id"], "quantity": 2, "unit_price": 30_000_000}]})
    assert inv.status_code == 200, inv.text
    before = _ledger(client)
    b = _up(client, "products", [PHEAD, [906101, "کرم دورچشم موجودیشش", 55, None, 1_670_000, 3_190_000, None],
                                 [906102, "کرم بی موجودیشش", 10, None, 2_000_000, 3_000_000, None]])
    assert [r["default"] for r in b["rows"]] == [f"p:{a['id']}", f"p:{s['id']}"]
    res = _commit(client, b, choices={})
    assert res["combined"] == 2 and res["opening_units"] == 52 + 12
    pa, ps = client.get(f"/api/products/{a['id']}").json(), client.get(f"/api/products/{s['id']}").json()
    # 3 @ 100 already here + 52 @ 16,700,000 (Tizpardaz's purchase price), against the opening balances
    assert (pa["stock_qty"], pa["stock_value"]) == (55, 300 + 52 * 16_700_000)
    # sold 2 before any stock was recorded: 12 were on the shelf; the 2 sold cost Tizpardaz's price, 10 stay
    assert (ps["stock_qty"], ps["stock_value"]) == (10, 10 * 20_000_000)
    after = _ledger(client)
    opening = 52 * 16_700_000 + 12 * 20_000_000
    assert _d(before, after, "3200") == -opening and _d(before, after, "5500") == 0
    assert _d(before, after, "5400") == 2 * 20_000_000 and _d(before, after, "1300") == opening - 2 * 20_000_000
    rep = next(x for x in client.get("/api/reports/products").json()["products"] if x["id"] == s["id"])
    assert (rep["revenue"], rep["cogs"], rep["estimated"]) == (60_000_000, 40_000_000, False)
    # undo: back to what it was (no stock moved since)
    u = _undo(client, b)
    assert u["moves"] == 2 and not u.get("moves_kept")
    assert client.get(f"/api/products/{a['id']}").json()["stock_value"] == 300
    back = _ledger(client)
    assert _d(before, back, "1300") == 0 and _d(before, back, "3200") == 0 and _d(before, back, "5400") == 0


def test_undo_keeps_opening_stock_that_was_sold_since(client):
    b = _up(client, "products", [PHEAD, [906111, "سرم فروخته شدهشش", 10, None, 100_000, 300_000, None]])
    _commit(client, b, choices={})
    p = next(x for x in client.get("/api/products?all=1").json() if x["code"] == "906111")
    cust = _customer(client, "خریدار سرمشش", "09151060005")
    assert client.post("/api/invoices", json={"customer_id": cust, "items": [{"product_id": p["id"], "quantity": 3, "unit_price": 3_000_000}]}).status_code == 200
    before = _ledger(client)
    u = _undo(client, b)
    assert u.get("moves_kept") == 1 and u.get("products_kept") == 1 and not u.get("moves")
    p = client.get(f"/api/products/{p['id']}").json()
    assert (p["stock_qty"], p["stock_value"]) == (7, 7 * 1_000_000)  # the 7 units on the shelf don't vanish
    assert _ledger(client) == before


def test_undoing_products_never_crashes_and_gives_back_codes_and_skus(client, monkeypatch):
    # (a) a later journal sold the new product: it stays
    b = _up(client, "products", [PHEAD, [906201, "ماسک تست آندوشش", 0, None, 100_000, 200_000, None]])
    _commit(client, b, choices={})
    jr = _up(client, "journal", [JHEAD, ["فاکتور فروش", 6081, "1405/03/12", "کالا", "ماسک تست آندوشش به متفرقه", 1, 200_000, 0, 200_000]])
    _commit(client, jr)
    u = _undo(client, b)
    assert u.get("products_kept") == 1 and not u.get("products")

    # (b) website on: the new product's stock waits to be sent; undo drops it with the product
    monkeypatch.setattr(woo_queue, "active", lambda db: True)
    b2 = _up(client, "products", [PHEAD, [906202, "سرم سایت آندوشش", 5, None, 100_000, 200_000, "G6-SITE-1"]])
    _commit(client, b2, choices={})
    with SessionLocal() as db:
        pid = db.scalar(select(Product.id).where(Product.code == "906202"))
        assert [(o.kind, o.qty) for o in db.scalars(select(WooOutbox).where(WooOutbox.product_id == pid))] == [("delta", 5)]
    u = _undo(client, b2)
    assert u["products"] == 1 and u["moves"] == 1
    with SessionLocal() as db:
        assert db.get(Product, pid) is None and not db.scalar(select(func.count(WooOutbox.id)).where(WooOutbox.product_id == pid))

    # (c) the website already got the stock: the product stays (archived) and the site gets it back
    b3 = _up(client, "products", [PHEAD, [906203, "سرم سایت دوشش", 4, None, 100_000, 200_000, "G6-SITE-2"]])
    _commit(client, b3, choices={})
    with SessionLocal() as db:
        pid3 = db.scalar(select(Product.id).where(Product.code == "906203"))
        for o in db.scalars(select(WooOutbox).where(WooOutbox.product_id == pid3)):
            o.status = "done"
        db.commit()
    u = _undo(client, b3)
    assert u.get("products_kept") == 1
    with SessionLocal() as db:
        assert db.get(Product, pid3).is_active is False and db.get(Product, pid3).stock_qty == 0
        assert [(o.kind, o.qty) for o in db.scalars(select(WooOutbox).where(WooOutbox.product_id == pid3, WooOutbox.status == "pending"))] == [("delta", -4)]
        for o in db.scalars(select(WooOutbox).where(WooOutbox.product_id == pid3)):  # not for the website tests
            db.delete(o)
        db.commit()
    monkeypatch.undo()

    # (d) a combined product's old code went to a new product of the same file, another product's code was taken,
    # another product's SKU was taken: undo gives every code and SKU back (it used to fail on UNIQUE products.code)
    pa = _product(client, "کالای کد قدیمیشش")
    py = _product(client, "کالای کد گرفتهشش", code="906206")
    px = _product(client, "کالای اسکیوشش", sku="G6-SKU-13")
    b4 = _up(client, "products", [PHEAD, [906204, "کالای کد قدیمیشش", 0, None, 0, 0, None], [int(pa["code"]), "سرم بشش", 0, None, 0, 0, None],
                                  [906206, "کالای جدید کدشش", 0, None, 0, 0, None], [906207, "کالای جدید اسکیوشش", 0, None, 0, 0, "G6-SKU-13"]])
    rows = {r["code"]: r for r in b4["rows"]}
    assert rows[pa["code"]]["default"] == "new"  # same number, other name, no SKU: not the same product
    choices = {str(rows["906204"]["row"]): f"p:{pa['id']}", str(rows[pa["code"]]["row"]): "new", str(rows["906206"]["row"]): "new",
               str(rows["906207"]["row"]): "new"}
    res = _commit(client, b4, choices=choices)
    assert res["combined"] == 1 and res["new"] == 3 and res["codes_moved"] == 1
    plist = {p["id"]: p for p in client.get("/api/products?all=1").json()}
    assert plist[pa["id"]]["code"] == "906204" and plist[py["id"]]["code"] != "906206" and plist[px["id"]]["sku"] is None
    u = _undo(client, b4)
    assert u["products"] == 3 and not u.get("codes_kept")
    plist = {p["id"]: p for p in client.get("/api/products?all=1").json()}
    assert plist[pa["id"]]["code"] == pa["code"] and plist[py["id"]]["code"] == "906206" and plist[px["id"]]["sku"] == "G6-SKU-13"


def test_invoice_discounts_are_spread_over_the_items(client):
    p = _product(client, "شامپو تخفیفشش")
    q = _product(client, "لوسیون تخفیفشش")
    naz = _customer(client, "نازنین تخفیفشش", "09151060010")
    rev_before = next((x["revenue"] for x in client.get("/api/reports/revenue?start=2026-05-20&end=2026-05-31").json()["lines"] if x["id"] == -1), 0)
    rows = [JHEAD,
            ["فاکتور خرید", 6091, "1405/03/01", "کالا", "شامپو تخفیفشش از پخش تخفیفشش", 10, 100_000, 1_000_000, 0],
            ["فاکتور خرید", 6091, "1405/03/01", "تخفیف خرید", "تخفیف", None, None, 0, 200_000],
            ["فاکتور خرید", 6091, "1405/03/01", "پخش تخفیفشش", "فاکتور خرید 6091", None, None, 0, 800_000],
            ["فاکتور فروش", 6092, "1405/03/05", "کالا", "شامپو تخفیفشش به تخفیفشش(نازنین)", 10, 300_000, 0, 3_000_000],
            ["فاکتور فروش", 6092, "1405/03/05", "تخفیف فروش", "تخفیف", None, None, 1_000_000, 0],
            ["فاکتور فروش", 6092, "1405/03/05", "تخفیفشش(نازنین)", "فاکتور 6092", None, None, 2_000_000, 0],
            # three equal items, 1,000,000 toman off 3,000,000: split to the Rial by largest remainder
            *[["فاکتور فروش", 6093, "1405/03/06", "کالا", "لوسیون تخفیفشش به تخفیفشش(نازنین)", 1, 1_000_000, 0, 1_000_000]] * 3,
            ["فاکتور فروش", 6093, "1405/03/06", "تخفیف فروش", "تخفیف", None, None, 1_000_000, 0],
            ["فاکتور فروش", 6093, "1405/03/06", "تخفیفشش(نازنین)", "فاکتور 6093", None, None, 2_000_000, 0]]
    _commit(client, _up(client, "journal", rows))
    rep = {x["id"]: x for x in client.get("/api/reports/products").json()["products"]}
    # bought 10 for 10,000,000 - 2,000,000 off; sold 10 for 30,000,000 - 10,000,000 off
    assert (rep[p["id"]]["revenue"], rep[p["id"]]["cogs"], rep[p["id"]]["profit"]) == (20_000_000, 8_000_000, 12_000_000)
    assert rep[q["id"]]["revenue"] == 20_000_000
    docs = {d["doc_no"]: d for d in client.get(f"/api/customers/{naz}").json()["docs"]}
    full = client.get(f"/api/trade-docs/{docs['6093']['id']}").json()
    assert [i["net_amount"] for i in full["items"]] == [6_666_666, 6_666_667, 6_666_667] and docs["6093"]["total"] == 20_000_000
    spent = next(x for x in client.get("/api/customers?q=نازنین تخفیفشش").json()["items"] if x["id"] == naz)
    assert spent["spent_old"] == 40_000_000  # what the invoices say she paid, not the items before discount
    rev = next(x["revenue"] for x in client.get("/api/reports/revenue?start=2026-05-20&end=2026-05-31").json()["lines"] if x["id"] == -1)
    assert rev - rev_before == 40_000_000
    # rows brought over before discounts were spread: done once at start, costs worked out again
    with SessionLocal() as db:
        for h in db.scalars(select(TradeHistory).where(TradeHistory.product_id.in_((p["id"], q["id"])))):
            h.net_amount, h.cost = None, None
        db.commit()
        tizpardaz.backfill_docs(db)
        db.commit()
        nets = sorted(int(h.net_amount) for h in db.scalars(select(TradeHistory).where(TradeHistory.product_id == q["id"])))
        assert nets == [6_666_666, 6_666_667, 6_666_667]
        assert db.scalar(select(func.sum(TradeHistory.cost)).where(TradeHistory.product_id == p["id"], TradeHistory.kind == "sale")) == 8_000_000


def test_journal_undo_refreshes_the_invoices_and_costs_left(client):
    _commit(client, _up(client, "products", [PHEAD, [906301, "کرم سابقهشش", 0, None, 50_000, 100_000, None],
                                            [906302, "ژل سابقهشش", 0, None, 0, 0, None]]), choices={})
    j1 = _up(client, "journal", [JHEAD, ["فاکتور خرید", 6101, "1405/03/10", "کالا", "کرم سابقهشش از پخش سابقهشش", 10, 100_000, 1_000_000, 0]])
    _commit(client, j1)
    j2 = _up(client, "journal", [JHEAD, ["فاکتور خرید", 6101, "1405/03/10", "کالا", "ژل سابقهشش از پخش سابقهشش", 2, 50_000, 100_000, 0],
                                 ["فاکتور فروش", 6102, "1405/03/15", "کالا", "کرم سابقهشش به متفرقه", 2, 300_000, 0, 600_000]])
    _commit(client, j2)

    def state():
        with SessionLocal() as db:
            cost = db.scalar(select(TradeHistory.cost).where(TradeHistory.doc_no == "6102", TradeHistory.kind == "sale"))
            doc = db.scalar(select(TradeDoc).where(TradeDoc.doc_no == "6101", TradeDoc.kind == "purchase"))
            return cost, doc.items_total, doc.total
    assert state() == (2_000_000, 11_000_000, 11_000_000)  # 2 sold at the 1,000,000 they were bought for
    _undo(client, j1)
    # that purchase is gone: the sale is costed at the product's purchase price, the invoice keeps only the gel
    assert state() == (2 * 500_000, 1_000_000, 1_000_000)


def test_a_journal_placeholder_hands_the_balance_to_the_real_person(client):
    parisa = _customer(client, "پریسا فرجشش", "09151060011")
    _commit(client, _up(client, "journal", [JHEAD, ["فاکتور فروش", 6111, "1405/03/20", "کالا", "کالای بی نامشش به 77061", 1, 100_000, 0, 100_000]]))
    ph = _by_tp(77061)
    assert ph and ph != parisa
    b = _up(client, "customers", [CHEAD, [77061, "فرجشش(پریسا)", 500_000, None]])
    row = b["rows"][0]
    assert (row["status"], row["customer_id"]) == ("found", parisa) and ph in [c["id"] for c in row["candidates"]]
    res = _commit(client, b, choices={}, balances=True)
    assert res["renamed"] == 1 and res["debts"] == 1
    assert _bal(client, parisa)["receivable"] == 5_000_000 and _bal(client, ph)["receivable"] == 0
    with SessionLocal() as db:
        assert db.get(Customer, parisa).tp_code == "77061"
        assert (db.get(Customer, ph).full_name, db.get(Customer, ph).tp_code) == ("پریسا فرجشش", None)
    groups = client.get("/api/customers/duplicates").json()
    assert any({parisa, ph} <= {c["id"] for c in g["customers"]} for g in groups)  # offered for merging
    _undo(client, b)
    assert _bal(client, parisa)["receivable"] == 0
    with SessionLocal() as db:
        assert db.get(Customer, parisa).tp_code is None
        assert (db.get(Customer, ph).full_name, db.get(Customer, ph).tp_code) == ("مشتری تیزپرداز 77061", "77061")


def test_chehreh_sales_moved_to_a_product_follow_their_chehreh_file(client):
    def appts(sid):
        with SessionLocal() as db:
            return db.scalar(select(func.count(Appointment.id)).where(Appointment.service_id == sid)) or 0

    def chehreh(code, name, service):
        hist = [["کد مشتری", "نام مشتری", "تاریخ", "خدمت", "مبلغ"], [code, name, "1404/11/01", service, 1_000_000]]
        pv = client.post("/api/import/legacy/preview", files={"file": ("h.xlsx", _xlsx(hist), "application/octet-stream")},
                         data={"kind": "history", "unit": "toman"})
        assert pv.status_code == 200, pv.text
        assert client.post(f"/api/import/legacy/{pv.json()['id']}/commit", json={}).status_code == 200
        return pv.json()["id"]

    line = client.post("/api/lines", json={"name": "لاین فروش قدیمیشش"}).json()
    # 1. Tizpardaz undone first: the sale goes back to its service, and its Chehreh file can still take it back
    s1 = client.post("/api/services", json={"line_id": line["id"], "name": "محلول قدیمیشش", "base_price": 1}).json()
    l1 = chehreh("990611", "مهتاب قدیمیشش", "محلول قدیمیشش")
    assert appts(s1["id"]) == 1
    b1 = _up(client, "products", [PHEAD, [906401, "محلول قدیمیشش", 0, None, 0, 0, None]])
    assert b1["rows"][0]["default"] == f"s:{s1['id']}"
    assert _commit(client, b1, choices={})["chehreh_sales"] == 1 and appts(s1["id"]) == 0
    _undo(client, b1)
    assert appts(s1["id"]) == 1
    assert client.post(f"/api/import/legacy/{l1}/undo").status_code == 200
    assert appts(s1["id"]) == 0
    # 2. Chehreh file undone first: undoing Tizpardaz doesn't bring its sales back
    s2 = client.post("/api/services", json={"line_id": line["id"], "name": "محلول دومشش", "base_price": 1}).json()
    l2 = chehreh("990612", "مهتاب دومشش", "محلول دومشش")
    b2 = _up(client, "products", [PHEAD, [906402, "محلول دومشش", 0, None, 0, 0, None]])
    _commit(client, b2, choices={})
    assert client.post(f"/api/import/legacy/{l2}/undo").status_code == 200
    _undo(client, b2)
    assert appts(s2["id"]) == 0


def _chehreh_history(client, code, name, service):
    hist = [["کد مشتری", "نام مشتری", "تاریخ", "خدمت", "مبلغ"], [code, name, "1404/11/02", service, 1_000_000]]
    pv = client.post("/api/import/legacy/preview", files={"file": ("h.xlsx", _xlsx(hist), "application/octet-stream")},
                     data={"kind": "history", "unit": "toman"})
    assert pv.status_code == 200, pv.text
    assert client.post(f"/api/import/legacy/{pv.json()['id']}/commit", json={}).status_code == 200
    return pv.json()["id"]


def _sold(pid):
    with SessionLocal() as db:
        return db.scalar(select(func.count(TradeHistory.id)).where(TradeHistory.product_id == pid)) or 0


def test_undoing_a_chehreh_file_takes_its_sales_moved_to_a_product_with_it(client):
    """(review) The Chehreh file is undone after a Tizpardaz file moved its receipts to a product: those sales go
    with it (they no longer count in revenue) and the customer it made goes too; undoing Tizpardaz then is clean."""
    line = client.post("/api/lines", json={"name": "لاین فروش بازبینیشش"}).json()
    s = client.post("/api/services", json={"line_id": line["id"], "name": "محلول بازبینیشش", "base_price": 1}).json()
    lb = _chehreh_history(client, "990621", "مهتاب بازبینیشش", "محلول بازبینیشش")
    with SessionLocal() as db:
        cust = db.scalar(select(Customer.id).where(Customer.legacy_code == "990621"))
    b = _up(client, "products", [PHEAD, [906501, "محلول بازبینیشش", 0, None, 0, 0, None]])
    assert _commit(client, b, choices={})["chehreh_sales"] == 1
    pid = next(x for x in client.get("/api/products?all=1").json() if x["code"] == "906501")["id"]
    assert _sold(pid) == 1
    assert next(x for x in client.get("/api/reports/products").json()["products"] if x["id"] == pid)["revenue"] == 10_000_000
    r = client.post(f"/api/import/legacy/{lb}/undo")
    assert r.status_code == 200, r.text
    assert _sold(pid) == 0 and client.get(f"/api/customers/{cust}").status_code == 404
    assert not any(x["id"] == pid and x["revenue"] for x in client.get("/api/reports/products").json()["products"])
    _undo(client, b)
    with SessionLocal() as db:
        assert not db.scalar(select(func.count(Appointment.id)).where(Appointment.service_id == s["id"]))


def test_undo_after_the_chehreh_customer_was_merged_gives_the_sale_back_to_the_kept_person(client):
    """(review) Undoing Tizpardaz products used to crash (FOREIGN KEY) when the person of a moved Chehreh sale had
    been merged into another record: the sale goes back to the person it belongs to now."""
    line = client.post("/api/lines", json={"name": "لاین ادغام بازبینیشش"}).json()
    s = client.post("/api/services", json={"line_id": line["id"], "name": "محلول ادغامشش", "base_price": 1}).json()
    _chehreh_history(client, "990622", "مهتاب ادغامشش", "محلول ادغامشش")
    with SessionLocal() as db:
        drop = db.scalar(select(Customer.id).where(Customer.legacy_code == "990622"))
    keep = _customer(client, "مهتاب ادغامشش", "09151060021")
    b = _up(client, "products", [PHEAD, [906502, "محلول ادغامشش", 0, None, 0, 0, None]])
    assert _commit(client, b, choices={})["chehreh_sales"] == 1
    r = client.post("/api/customers/merge", json={"keep_id": keep, "drop_ids": [drop]})
    assert r.status_code == 200, r.text
    _undo(client, b)
    with SessionLocal() as db:
        back = list(db.scalars(select(Appointment).where(Appointment.service_id == s["id"])))
        assert [(a.customer_id, a.quoted_price, a.status) for a in back] == [(keep, 10_000_000, "done")]


def test_reimporting_products_after_a_rename_here_is_the_same_product(client):
    """(review) A product brought over from Tizpardaz (no SKU) and renamed here: the same list again must link it by
    its Tizpardaz code - not make a second product with the stock counted twice."""
    b = _up(client, "products", [PHEAD, [906503, "کرم بازبینیشش", 5, None, 100_000, 200_000, None]])
    _commit(client, b, choices={})
    p = next(x for x in client.get("/api/products?all=1").json() if x["code"] == "906503")
    r = client.put(f"/api/products/{p['id']}", json={"name": "کرم بازبینیشش اصلاح‌شده", "code": "906503", "sale_price": 2_000_000})
    assert r.status_code == 200, r.text
    before = _ledger(client)
    again = _up(client, "products", [PHEAD, [906503, "کرم بازبینیشش", 5, None, 100_000, 200_000, None]])
    assert (again["rows"][0]["status"], again["rows"][0]["default"]) == ("linked", f"p:{p['id']}")
    res = _commit(client, again, choices={})
    assert res["combined"] == 1 and not res.get("new") and not res.get("codes_moved")
    same = [x for x in client.get("/api/products?all=1").json() if "کرم بازبینیشش" in x["name"]]
    assert [(x["id"], x["code"], x["stock_qty"], x["stock_value"]) for x in same] == [(p["id"], "906503", 5, 5_000_000)]
    assert _ledger(client) == before  # nothing new on the shelf, nothing posted


def test_product_page_history_is_after_the_invoice_discount_like_the_report(client):
    """(review) The product page's history shows what was really received (after the invoice's discount), so its
    totals agree with the product report."""
    p = _product(client, "ماسک صفحهشش")
    _commit(client, _up(client, "journal", [
        JHEAD, ["فاکتور فروش", 6121, "1405/04/01", "کالا", "ماسک صفحهشش به متفرقه", 4, 250_000, 0, 1_000_000],
        ["فاکتور فروش", 6121, "1405/04/01", "تخفیف فروش", "تخفیف", None, None, 100_000, 0],
        ["فاکتور فروش", 6121, "1405/04/01", "متفرقه", "فاکتور 6121", None, None, 900_000, 0]]))
    h = client.get(f"/api/products/{p['id']}").json()["history"]
    assert [(x["amount"], x["net_amount"]) for x in h] == [(10_000_000, 9_000_000)]
    assert next(x for x in client.get("/api/reports/products").json()["products"] if x["id"] == p["id"])["revenue"] == 9_000_000


def test_undo_counts_a_seller_payable_that_was_paid(client, accounts):
    """(review) A seller's opening payable that was paid since stays with the payment, and undo says so."""
    b = _up(client, "customers", [CHEAD, [60701, "فروشندهپرداختشش(بهرام)", None, 100_000]])
    _commit(client, b, choices={}, balances=True, credit_as={str(b["rows"][0]["row"]): "payable"})
    cid = _by_tp(60701)
    pur = next(x for x in client.get("/api/purchases").json() if x["supplier"] and "بهرام" in x["supplier"])
    r = client.post(f"/api/purchases/{pur['id']}/pay", json={"payment_account_id": next(iter(accounts.values())), "amount": 400_000})
    assert r.status_code == 200, r.text
    before = _ledger(client)
    u = _undo(client, b)
    assert u.get("payables_kept") == 1 and not u.get("payables")
    assert client.get(f"/api/customers/{cid}").json()["we_owe"] == 600_000
    assert _ledger(client) == before
