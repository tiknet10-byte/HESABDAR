"""Deposits only for services, forfeits as income of their line, the owner's capital, gold/dollars, the position page."""
from datetime import datetime, timedelta

from sqlalchemy import select


def tb(client):
    t = client.get("/api/ledger/trial-balance").json()
    assert t["total_debit"] == t["total_credit"]
    return {r["code"]: r for r in t["rows"]}


def by_name(client):
    t = client.get("/api/ledger/trial-balance").json()
    return {r["name"]: r for r in t["rows"]}


def position_ok(client):
    p = client.get("/api/capital/position").json()
    assert p["check"] == 0, p
    assert p["worth"] == p["assets_total"] - p["liabilities_total"]
    assert sum(f["value"] for f in p["flow"]) == p["worth"]  # start + in - out + profit (+ openings) = now
    return p


def test_deposit_pays_only_services(client, accounts, services):
    svc = services["کاشت ناخن"]
    cash = accounts["صندوق نقدی"]
    dep = client.post("/api/deposits", json={"customer_name": "بیعانه فقط خدمت", "customer_mobile": "09159990001", "amount": 5_000_000,
                                             "payment_account_id": cash, "service_id": svc["id"]}).json()
    cid = dep["customer_id"]
    prod = client.post("/api/products", json={"name": "کرم بیعانه‌تست", "sale_price": 4_000_000}).json()
    # products only + the deposit ticked: refused (a deposit is for services)
    r = client.post("/api/invoices", json={"customer_id": cid, "items": [{"product_id": prod["id"], "quantity": 1}],
                                           "deposit_ids": [dep["id"]]})
    assert r.status_code == 400 and "خدمت" in r.json()["detail"]
    # service 3m + product 4m, discount 700k: shares 300k / 400k (exact); the deposit pays only the service's 2.7m
    inv = client.post("/api/invoices", json={"customer_id": cid, "discount": 700_000, "deposit_ids": [dep["id"]],
                                             "items": [{"service_id": svc["id"], "unit_price": 3_000_000},
                                                       {"product_id": prod["id"], "quantity": 1, "unit_price": 4_000_000}],
                                             "payments": [{"payment_account_id": cash, "amount": 3_600_000}]})
    assert inv.status_code == 200, inv.text
    inv = inv.json()
    assert inv["total"] == 6_300_000 and inv["paid"] == 6_300_000 and inv["status"] == "paid"
    held = client.get(f"/api/deposits?customer_id={cid}&status=held").json()
    assert [d["amount"] for d in held] == [2_300_000]
    assert held[0]["service_id"] == svc["id"]  # the rest stays a deposit of the same service
    full = client.get(f"/api/invoices/{inv['id']}").json()
    nets = sorted(i["net_amount"] for i in full["items"])
    assert nets == [2_700_000, 3_600_000] and sum(nets) == inv["total"]


def test_discount_split_is_exact_and_zero_items_get_nothing(client, accounts, services):
    svc = services["کاشت ناخن"]
    inv = client.post("/api/invoices", json={"customer_name": "تخفیف دقیق", "customer_mobile": "09159990002", "discount": 100,
                                             "apply_deposits": False,
                                             "items": [{"service_id": svc["id"], "unit_price": 1_000_001},
                                                       {"service_id": svc["id"], "unit_price": 1_000_001},
                                                       {"service_id": svc["id"], "unit_price": 1_000_001},
                                                       {"description": "هدیه", "unit_price": 0}]}).json()
    nets = [i["net_amount"] for i in client.get(f"/api/invoices/{inv['id']}").json()["items"]]
    assert sum(nets) == inv["total"] == 3_000_003 - 100
    assert nets[3] == 0 and all(n >= 0 for n in nets)


def test_forfeit_is_income_of_its_line(client, accounts, services):
    svc = services["کاشت ناخن"]
    dep = client.post("/api/deposits", json={"customer_name": "غایب لاین", "customer_mobile": "09159990003", "amount": 1_500_000,
                                             "payment_account_id": accounts["صندوق نقدی"], "service_id": svc["id"]}).json()
    before = by_name(client)
    r = client.post(f"/api/deposits/{dep['id']}/close", json={"action": "forfeit"})
    assert r.status_code == 200, r.text
    line = next(x for x in client.get("/api/lines").json() if x["id"] == svc["line_id"])
    after = by_name(client)
    name = f"بیعانه سوخت‌شده {line['name']}"
    assert after[name]["balance"] - (before[name]["balance"] if name in before else 0) == 1_500_000
    assert after[name]["code"].startswith("4800") or after[name]["code"].startswith("48")
    # in the revenue report, under its own line
    today = datetime.now().date().isoformat()
    rep = client.get(f"/api/reports/revenue?start={today}&end={today}").json()
    row = next(x for x in rep["lines"] if x["id"] == svc["line_id"])
    assert row["forfeits"] >= 1_500_000
    s = client.get(f"/api/reports/summary?start={today}&end={today}").json()
    assert s["forfeited"] >= 1_500_000


def test_old_forfeits_are_moved_to_their_line(client, accounts, services):
    from app.core.db import SessionLocal
    from app.models import Deposit, JournalEntry, JournalLine
    from app.services import accounting

    svc = services["کاشت ناخن"]
    dep = client.post("/api/deposits", json={"customer_name": "سوخت قدیمی", "customer_mobile": "09159990004", "amount": 700_000,
                                             "payment_account_id": accounts["صندوق نقدی"], "service_id": svc["id"]}).json()
    client.post(f"/api/deposits/{dep['id']}/close", json={"action": "forfeit"})
    with SessionLocal() as db:  # as it was booked before: everything on 4800, no line
        plain = accounting.account(db, accounting.FORFEITED)
        legs = db.scalars(select(JournalLine).join(JournalEntry).where(JournalEntry.ref_type == "deposit",
                                                                      JournalEntry.ref_id == dep["id"], JournalEntry.description.like("سوخت%"))).all()
        for ln in legs:
            ln.line_id = None
            if ln.credit:
                ln.account_id = plain.id
        db.commit()
        assert accounting.reclass_forfeits(db) == 1
        assert accounting.reclass_forfeits(db) == 0  # once only
        db.commit()
        legs = db.scalars(select(JournalLine).join(JournalEntry).where(JournalEntry.ref_type == "deposit",
                                                                      JournalEntry.ref_id == dep["id"], JournalEntry.description.like("سوخت%"))).all()
        income = next(ln for ln in legs if ln.credit)
        assert income.account_id != plain.id and income.line_id == svc["line_id"]
        assert db.get(Deposit, dep["id"]).status == "forfeited"
    tb(client)


def test_owner_capital_in_out_and_void(client, accounts):
    cash = accounts["صندوق نقدی"]
    p0 = position_ok(client)
    m = client.post("/api/capital/moves", json={"kind": "add", "amount": 100_000_000, "payment_account_id": cash, "notes": "سرمایه اول"})
    assert m.status_code == 200, m.text
    w = client.post("/api/capital/moves", json={"kind": "withdraw", "amount": 20_000_000, "payment_account_id": cash}).json()
    assert client.post("/api/capital/moves", json={"kind": "add", "amount": 0, "payment_account_id": cash}).status_code == 400
    future = (datetime.now() + timedelta(days=3)).isoformat()
    assert client.post("/api/capital/moves", json={"kind": "add", "amount": 1, "payment_account_id": cash, "at": future}).status_code == 400
    p1 = position_ok(client)
    assert p1["worth"] - p0["worth"] == 80_000_000
    assert p1["equity"]["capital"] - p0["equity"]["capital"] == 80_000_000
    flow = {f["key"]: f["value"] for f in p1["flow"]}
    assert flow["put_in"] - {f["key"]: f["value"] for f in p0["flow"]}["put_in"] == 100_000_000
    cash_row = next(x for x in p1["cash"] if x["id"] == cash)
    assert cash_row["balance"] - next(x for x in p0["cash"] if x["id"] == cash)["balance"] == 80_000_000
    # a mistake is voided: as if it never happened
    assert client.post(f"/api/capital/moves/{w['id']}/void", json={}).json()["status"] == "void"
    p2 = position_ok(client)
    assert p2["worth"] - p0["worth"] == 100_000_000
    tb(client)


def test_owner_pays_supplier_and_purchase_void(client, accounts):
    prod = client.post("/api/products", json={"name": "کالای پرداخت مدیر", "sale_price": 1}).json()
    pur = client.post("/api/purchases", json={"supplier_name": "فروشنده جیب مدیر", "items": [{"product_id": prod["id"], "quantity": 10,
                                                                                             "unit_price": 1_000_000}]}).json()
    assert pur["due"] == 10_000_000
    before = tb(client)
    r = client.post("/api/capital/supplier", json={"purchase_id": pur["id"], "amount": 4_000_000})
    assert r.status_code == 200, r.text
    assert client.post("/api/capital/supplier", json={"purchase_id": pur["id"], "amount": 7_000_000}).status_code == 400  # > due
    p = client.get(f"/api/purchases/{pur['id']}").json()
    assert p["due"] == 6_000_000 and p["status"] == "partial"
    after = tb(client)
    assert before["2300"]["balance"] - after["2300"]["balance"] == 4_000_000
    assert after["3100"]["balance"] - before["3100"]["balance"] == 4_000_000
    # the purchase is voided: what the owner paid goes back to them
    assert client.post(f"/api/purchases/{pur['id']}/void", json={"reason": "اشتباه"}).status_code == 200
    end = tb(client)
    assert end["3100"]["balance"] == before["3100"]["balance"]
    assert end["2300"]["balance"] == before["2300"]["balance"] - 10_000_000
    assert client.get("/api/capital/moves").json()[0]["status"] == "void"


def test_convert_capital_customer(client, accounts):
    dep = client.post("/api/deposits", json={"customer_name": "سرمایه", "customer_mobile": "09159990005", "amount": 50_000_000,
                                             "payment_account_id": accounts["صندوق نقدی"]}).json()
    lst = client.get("/api/capital/convertible").json()
    me = next(p for p in lst if p["customer_id"] == dep["customer_id"])
    assert me["suggested"] and me["total"] == 50_000_000 and me["deposits"][0]["id"] == dep["id"]
    assert client.get("/api/capital/convertible?q=غایب").json() is not None  # search any name
    before = tb(client)
    r = client.post("/api/capital/convert", json={"deposit_ids": [dep["id"]]})
    assert r.status_code == 200 and r.json() == {"converted": 1, "amount": 50_000_000}
    after = tb(client)
    assert after["3100"]["balance"] - before["3100"]["balance"] == 50_000_000
    assert before["2100"]["balance"] - after["2100"]["balance"] == 50_000_000
    cust = client.get(f"/api/customers/{dep['customer_id']}").json()
    assert cust["balance"]["deposits_held"] == 0
    assert dep["customer_id"] not in [p["customer_id"] for p in client.get("/api/capital/convertible").json()]
    move = next(m for m in client.get("/api/capital/moves").json() if m["deposit_id"] == dep["id"])
    client.post(f"/api/capital/moves/{move['id']}/void", json={})
    assert client.get(f"/api/customers/{dep['customer_id']}").json()["balance"]["deposits_held"] == 50_000_000
    assert tb(client)["3100"]["balance"] == before["3100"]["balance"]


def test_gold_and_dollars(client, accounts):
    cash = accounts["صندوق نقدی"]
    h = client.post("/api/capital/holdings", json={"name": "طلای آب‌شده", "kind": "gold", "unit": "گرم"}).json()
    assert client.post("/api/capital/holdings", json={"name": "طلای آب‌شده"}).status_code == 400
    p0 = position_ok(client)
    t = (datetime.now() - timedelta(hours=5)).replace(microsecond=0)

    def move(**kw):
        return client.post(f"/api/capital/holdings/{h['id']}/moves", json=kw)

    assert move(kind="buy", qty="10.5", amount=105_000_000, payment_account_id=cash, at=t.isoformat()).status_code == 200
    r = move(kind="sell", qty="۳٫۲۵", amount=40_000_000, payment_account_id=cash, at=(t + timedelta(hours=1)).isoformat())
    assert r.status_code == 200, r.text
    sold = r.json()
    assert sold["cost"] == -32_500_000 and sold["gain"] == 7_500_000  # 105m x 3.25 / 10.5
    out = move(kind="owner_out", qty="1", at=(t + timedelta(hours=2)).isoformat()).json()
    assert out["cost"] == -10_000_000  # 72.5m x 1 / 7.25
    assert move(kind="sell", qty="7", amount=1, payment_account_id=cash).status_code == 400  # only 6.25 left
    assert move(kind="buy", qty="1.0001", amount=1, payment_account_id=cash).status_code == 400  # 3 decimals at most
    # a buy dated before a sale would change what that sale cost: refused
    assert move(kind="buy", qty="1", amount=1, payment_account_id=cash, at=t.isoformat()).status_code == 400
    hs = next(x for x in client.get("/api/capital/holdings").json() if x["id"] == h["id"])
    assert hs["qty"] == "6.25" and hs["cost"] == 62_500_000
    client.post(f"/api/capital/holdings/{h['id']}/price", json={"price": 12_000_000})
    hs = next(x for x in client.get("/api/capital/holdings").json() if x["id"] == h["id"])
    assert hs["market_value"] == 75_000_000 and hs["unrealized"] == 12_500_000
    p1 = position_ok(client)
    assert p1["worth_market"] - p1["worth"] == p1["unrealized"]
    assert next(a for a in p1["assets"] if a["key"] == "holdings")["value"] - \
        next(a for a in p0["assets"] if a["key"] == "holdings")["value"] == 62_500_000
    gain = next(r for r in p1["profit"]["rows"] if r["key"] == "holding_gain")
    assert gain["value"] >= 7_500_000
    # only the latest move can be undone
    first = client.get(f"/api/capital/holdings/{h['id']}/moves").json()[-1]
    assert client.post(f"/api/capital/holding-moves/{first['id']}/void", json={}).status_code == 400
    assert client.post(f"/api/capital/holding-moves/{out['id']}/void", json={}).json()["status"] == "void"
    hs = next(x for x in client.get("/api/capital/holdings").json() if x["id"] == h["id"])
    assert hs["qty"] == "7.25" and hs["cost"] == 72_500_000
    rows = tb(client)
    assert rows["1400"]["balance"] if "1400" in rows else True
    position_ok(client)


def test_capital_is_management_only(client):
    from fastapi.testclient import TestClient

    from app.main import app

    r = client.post("/api/auth/users", json={"username": "acc_cap", "password": "Account123", "role": "accountant"})
    assert r.status_code == 200, r.text
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": "acc_cap", "password": "Account123"}).json()["access_token"]
    assert c.get("/api/capital/position").status_code == 403
    assert c.post("/api/capital/moves", json={"kind": "withdraw", "amount": 1, "payment_account_id": 1}).status_code == 403


def test_customer_credit_pays_products_and_is_never_forfeited(client, accounts):
    from app.core.db import SessionLocal
    from app.models import Deposit
    from app.services import accounting

    cash = accounts["صندوق نقدی"]
    prod = client.post("/api/products", json={"name": "کرم اعتبار تست", "sale_price": 1_000_000}).json()
    inv = client.post("/api/invoices", json={"customer_name": "اعتبار دار", "customer_mobile": "09159990006",
                                             "items": [{"product_id": prod["id"], "quantity": 2}],
                                             "payments": [{"payment_account_id": cash, "amount": 2_000_000}]}).json()
    # voided, money kept for the customer: a credit (not a service deposit)
    assert client.post(f"/api/invoices/{inv['id']}/void?payments=deposit").status_code == 200
    credit = client.get(f"/api/deposits?customer_id={inv['customer_id']}&status=held").json()[0]
    assert credit["kind"] == "credit" and credit["amount"] == 2_000_000
    assert client.post(f"/api/deposits/{credit['id']}/close", json={"action": "forfeit"}).status_code == 400
    # it pays a products-only invoice
    inv2 = client.post("/api/invoices", json={"customer_id": inv["customer_id"], "deposit_ids": [credit["id"]],
                                              "items": [{"product_id": prod["id"], "quantity": 1, "unit_price": 1_500_000}]})
    assert inv2.status_code == 200, inv2.text
    assert inv2.json()["paid"] == 1_500_000 and inv2.json()["status"] == "paid"
    left = client.get(f"/api/deposits?customer_id={inv['customer_id']}&status=held").json()
    assert [(d["amount"], d["kind"]) for d in left] == [(500_000, "credit")]
    tb(client)
    # balances brought over from Tizpardaz (product software) become credits at start
    with SessionLocal() as db:
        c = db.get(Deposit, credit["id"])
        d = Deposit(customer_id=c.customer_id, amount=10, payment_account_id=cash, source="import", notes="ماندهٔ بستانکار تیزپرداز (کد 9)")
        db.add(d)
        db.commit()
        assert accounting.classify_credits(db) >= 1
        db.commit()
        db.expire_all()
        assert db.get(Deposit, d.id).kind == "credit"
        db.delete(db.get(Deposit, d.id))
        db.commit()


def test_two_requests_cannot_pay_or_void_twice(client, accounts, services):
    """Two people (or a double click) act on the same invoice with stale data: only the first one is booked."""
    import pytest

    from app.core.db import SessionLocal
    from app.models import Deposit, Invoice, PaymentAccount
    from app.services import accounting

    cash = accounts["صندوق نقدی"]
    svc = services["کاشت ناخن"]
    inv = client.post("/api/invoices", json={"customer_name": "دوبار کلیک", "customer_mobile": "09159990007",
                                             "items": [{"service_id": svc["id"], "unit_price": 1_000_000}]}).json()
    a, b = SessionLocal(), SessionLocal()
    try:
        ia, ib = a.get(Invoice, inv["id"]), b.get(Invoice, inv["id"])  # both loaded "unpaid"
        pa_a, pa_b = a.get(PaymentAccount, cash), b.get(PaymentAccount, cash)
        accounting.record_payment(a, payment_account=pa_a, amount=1_000_000, invoice=ia)
        a.commit()
        with pytest.raises(accounting.AccountingError):
            accounting.record_payment(b, payment_account=pa_b, amount=1_000_000, invoice=ib)
        b.rollback()
        # void twice with stale objects: the second one is refused
        ia, ib = a.get(Invoice, inv["id"]), b.get(Invoice, inv["id"])
        a.refresh(ia), b.refresh(ib)
        accounting.void_invoice(a, ia, payments="refund")
        a.commit()
        with pytest.raises(accounting.AccountingError):
            accounting.void_invoice(b, ib, payments="refund")
        b.rollback()
        # a deposit refunded twice
        dep = client.post("/api/deposits", json={"customer_name": "دوبار کلیک", "customer_mobile": "09159990007", "amount": 300_000,
                                                 "payment_account_id": cash}).json()
        da, db_ = a.get(Deposit, dep["id"]), b.get(Deposit, dep["id"])
        accounting.close_deposit(a, da, "refund")
        a.commit()
        with pytest.raises(accounting.AccountingError):
            accounting.close_deposit(b, db_, "refund")
        b.rollback()
    finally:
        a.close()
        b.close()
    full = client.get(f"/api/invoices/{inv['id']}").json()
    assert full["status"] == "void" and sum(p["amount"] for p in full["payments"]) == 0  # paid once, refunded once
    tb(client)
