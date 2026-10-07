import json

from app.core.security import sign_payload


def balanced(client):
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"]
    return {r["code"]: r for r in tb["rows"]}


def test_auth_security(client):
    from fastapi.testclient import TestClient
    from app.main import app
    anon = TestClient(app)
    assert anon.get("/api/customers").status_code == 401
    assert anon.post("/api/auth/setup", json={"username": "x", "password": "Secret123"}).status_code == 400
    for _ in range(5):
        assert anon.post("/api/auth/login", json={"username": "owner", "password": "wrong"}).status_code == 401
    assert anon.post("/api/auth/login", json={"username": "owner", "password": "Secret123"}).status_code == 423  # locked
    assert client.get("/api/audit/verify").json()["ok"]


def test_receptionist_cannot_see_finance(client):
    r = client.post("/api/auth/users", json={"username": "rec", "password": "Recept123", "role": "receptionist"})
    assert r.status_code == 200
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    tok = c.post("/api/auth/login", json={"username": "rec", "password": "Recept123"}).json()["access_token"]
    c.headers["Authorization"] = f"Bearer {tok}"
    assert c.get("/api/reports/summary").status_code == 403
    assert c.get("/api/customers").status_code == 200


def test_deposit_invoice_payment_flow(client, accounts, services):
    svc = services["کاشت ناخن"]
    r = client.post("/api/deposits", json={"customer_name": "سارا تستی", "customer_mobile": "۰۹۱۲۳۴۵۶۷۸۹",
                                           "amount": 2_000_000, "payment_account_id": accounts["کارت بانک ملی (مدیر)"],
                                           "notes": "برای کاشت ناخن"})
    assert r.status_code == 200, r.text
    dep = r.json()
    assert dep["service_guess"]["candidates"][0]["service"] == "کاشت ناخن"
    cid = dep["customer_id"]
    assert client.get(f"/api/customers/{cid}").json()["mobile"] == "09123456789"

    inv = client.post("/api/invoices", json={"customer_id": cid, "items": [{"service_id": svc["id"], "unit_price": 9_000_000}],
                                             "discount": 500_000, "payments": [{"payment_account_id": accounts["کارتخوان ملت"], "amount": 6_500_000}]})
    assert inv.status_code == 200, inv.text
    inv = inv.json()
    assert inv["total"] == 8_500_000 and inv["paid"] == 8_500_000 and inv["status"] == "paid"
    rows = balanced(client)
    assert rows["1200"]["balance"] == 0  # receivables settled
    bal = client.get(f"/api/customers/{cid}").json()["balance"]
    assert bal == {"receivable": 0, "deposits_held": 0, "net": 0}
    # overpayment rejected
    inv2 = client.post("/api/invoices", json={"customer_id": cid, "items": [{"description": "مانیکور", "unit_price": 1_000_000}],
                                              "payments": [{"payment_account_id": accounts["کارتخوان ملت"], "amount": 2_000_000}]})
    assert inv2.status_code == 400


def test_deposit_forfeit_and_expense(client, accounts):
    d = client.post("/api/deposits", json={"customer_name": "مشتری غایب", "customer_mobile": "09350000000",
                                           "amount": 1_000_000, "payment_account_id": accounts["کارت پاسارگاد"]}).json()
    assert client.post(f"/api/deposits/{d['id']}/close", json={"action": "forfeit"}).json()["status"] == "forfeited"
    assert client.post(f"/api/deposits/{d['id']}/close", json={"action": "refund"}).status_code == 400
    assert client.post("/api/expenses", json={"category": "اجاره", "amount": 5_000_000, "payment_account_id": accounts["کارت پاسارگاد"]}).status_code == 200
    rows = balanced(client)
    assert rows["4800"]["balance"] == 1_000_000


def test_receipt_bot_and_bank_matching(client, accounts):
    sender = "+989121112233"
    receipt_text = "کارت به کارت موفق\nمبلغ: 3,000,000 ریال\nشماره پیگیری: 555666\n1405/07/11 10:20"
    r = client.post("/api/plugins/messaging_receipts/simulate", json={"channel": "whatsapp", "sender": sender, "text": receipt_text}).json()
    assert r["receipt_status"] == "pending"  # no bank data yet
    assert "نام" in r["replies"][0]  # bot asks for name
    r2 = client.post("/api/plugins/messaging_receipts/simulate", json={"channel": "whatsapp", "sender": sender, "text": "نگار کریمی"}).json()
    assert r2["customer_id"] and r2["receipt_status"] == "pending"
    # bank SMS arrives on the salon phone via signed webhook -> auto match & register deposit
    body = json.dumps({"text": "بانک ملی\nواریز: +3,000,000\nپیگیری: 555666\n1405/07/11 10:21",
                       "payment_account_id": accounts["کارت بانک ملی (مدیر)"]}).encode()
    bad = client.post("/api/plugins/bank_sync/webhook/bank-sms", content=body, headers={"X-Hesabdar-Signature": "nope"})
    assert bad.status_code == 401
    ok = client.post("/api/plugins/bank_sync/webhook/bank-sms", content=body,
                     headers={"X-Hesabdar-Signature": sign_payload(body, "hook-secret"), "Content-Type": "application/json"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "matched"
    rec = client.get("/api/plugins/messaging_receipts/receipts").json()[0]
    assert rec["status"] == "registered" and rec["customer"] == "نگار کریمی" and rec["deposit_id"]
    cust = client.get(f"/api/customers/{rec['customer_id']}").json()
    assert cust["mobile"] == "09121112233" and cust["balance"]["deposits_held"] == 3_000_000
    balanced(client)


def test_fake_receipt_raises_alert(client, accounts):
    client.post("/api/plugins/bank_sync/bank-sms", json={"text": "واریز: +1,000,000\nپیگیری: 777888",
                                                         "payment_account_id": accounts["کارت پاسارگاد"]})
    r = client.post("/api/plugins/messaging_receipts/simulate",
                    json={"channel": "sms", "sender": "09121112233", "text": "واریز موفق مبلغ 10,000,000 ریال پیگیری 777888"}).json()
    assert r["receipt_status"] == "mismatch"
    assert any(a["kind"] == "receipt_mismatch" for a in client.get("/api/alerts").json())


def test_sales_book_import(client, accounts):
    text = "1405/07/10\nمریم احمدی | 09125550001 | کوتاهی مو | 350,000 | کارتخوان\nزهرا نوری | 09125550002 | رنگ مو | 1,800,000 | کارت\nجمع: 2,500,000"
    files = {"file": ("book.txt", text.encode(), "text/plain")}
    b = client.post("/api/plugins/sales_book_ocr/upload", files=files, data={"currency": "toman"}).json()
    assert b["summary"]["rows"] == 2
    assert "page_total_mismatch" in b["summary"]  # 2,150,000 != 2,500,000
    assert b["rows"][0]["items"][0]["service"] == "کوتاهی مو" and b["rows"][0]["total"] == 3_500_000
    c = client.post(f"/api/plugins/sales_book_ocr/batches/{b['id']}/commit").json()
    assert len(c["invoices"]) == 2
    balanced(client)


def test_reports_ai_tools_and_learning(client):
    s = client.get("/api/reports/summary").json()
    assert s["revenue"] > 0 and s["by_line"]
    tools = {t["name"] for t in client.get("/api/ai/tools").json()}
    assert {"financial_summary", "register_deposit", "unmatched_bank_transactions", "loyalty_points"} <= tools
    out = client.post("/api/ai/tools/guess_service", json={"arguments": {"text": "میخوام مژه هام رو اکستنشن کنم"}}).json()
    assert out[0]["service"] == "اکستنشن مژه"
    reply = client.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "وضعیت فروش؟"}]}).json()
    assert reply["mode"] == "offline" and "درآمد" in reply["reply"]
    assert client.post("/api/learning/retrain").status_code == 200


def test_backup_restore(client, accounts):
    m = client.post("/api/backups").json()
    assert client.post(f"/api/backups/{m['name']}/verify").json()["ok"]
    before = client.get("/api/customers").json()["total"]
    client.post("/api/customers", json={"full_name": "بعد از بکاپ", "mobile": "09199999999"})
    assert client.get("/api/customers").json()["total"] == before + 1
    r = client.post(f"/api/backups/{m['name']}/restore")
    assert r.status_code == 200, r.text
    assert client.get("/api/customers").json()["total"] == before


def test_mcp_server_protocol(client):
    import subprocess
    import sys
    import os
    client.post("/api/auth/users", json={"username": "ai", "password": "AgentPass1", "role": "ai_agent"})
    reqs = "\n".join(json.dumps(x) for x in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "open_deposits", "arguments": {}}},
    ]) + "\n"
    p = subprocess.run([sys.executable, "-m", "app.ai.mcp_server", "--user", "ai"], input=reqs, capture_output=True, text=True,
                       env=os.environ.copy(), timeout=60)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    assert lines[0]["result"]["serverInfo"]["name"] == "hesabdar"
    assert any(t["name"] == "financial_summary" for t in lines[1]["result"]["tools"])
    assert "isError" not in lines[2]["result"]


def test_price_warning_in_toman(client, services):
    svc = services["میکروبلیدینگ ابرو"]
    r = client.post("/api/invoices/preview", json={"items": [{"service_id": svc["id"], "unit_price": 75_000_000}]}).json()
    assert r["warnings"] and "7,500,000 تومان" in r["warnings"][0]


def test_delete_service_line_and_account(client):
    line = client.post("/api/lines", json={"name": "لاین آزمایشی"}).json()
    s1 = client.post("/api/services", json={"line_id": line["id"], "name": "خدمت ۱", "base_price": 1000}).json()
    s2 = client.post("/api/services", json={"line_id": line["id"], "name": "خدمت ۲", "base_price": 1000}).json()
    assert client.delete(f"/api/services/{s1['id']}").json() == {"ok": True, "archived": False, "line_removed": False}
    r = client.delete(f"/api/services/{s2['id']}").json()
    assert r["line_removed"] is True
    assert all(l["id"] != line["id"] for l in client.get("/api/lines").json())
    acc = client.post("/api/accounts", json={"kind": "pos", "name": "کارتخوان اضافه"}).json()
    assert client.delete(f"/api/accounts/{acc['id']}").json()["archived"] is False
    assert all(a["id"] != acc["id"] for a in client.get("/api/accounts").json())


def test_deposit_with_booking_and_slots(client, accounts, services):
    svc = services["کاشت ناخن"]
    slots = client.get(f"/api/appointments/suggest?service_id={svc['id']}").json()
    assert len(slots) >= 2 and slots[0]["start_at"] < slots[1]["start_at"]
    d = client.post("/api/deposits", json={"customer_name": "نوبت دار", "customer_mobile": "09127770000", "amount": 1_000_000,
                                           "payment_account_id": accounts["کارت پاسارگاد"], "service_id": svc["id"],
                                           "received_at": "2026-10-01T09:30:00", "book_at": slots[0]["start_at"]}).json()
    assert d["appointment_at"] == slots[0]["start_at"] and d["received_at"] == "2026-10-01T09:30"
    nxt = client.get(f"/api/appointments/suggest?service_id={svc['id']}").json()
    assert nxt[0]["start_at"] != slots[0]["start_at"]  # slot is taken now (single capacity)
    # deposit without booking, linked later
    d2 = client.post("/api/deposits", json={"customer_id": d["customer_id"], "amount": 500_000,
                                            "payment_account_id": accounts["کارت پاسارگاد"], "service_id": svc["id"]}).json()
    assert d2["appointment_id"] is None
    linked = client.post(f"/api/deposits/{d2['id']}/appointment?appointment_id={d['appointment_id']}").json()
    assert linked["appointment_id"] == d["appointment_id"]
    appt = client.get(f"/api/appointments/{d['appointment_id']}").json()
    assert len(appt["deposits"]) == 2


def test_ai_key_saved_encrypted(client):
    r = client.put("/api/settings", json={"ai.api_key": "sk-ant-test-123", "ai.model": "claude-opus-5-5"}).json()
    assert r["ai.api_key"] == "••••"
    from app.core.db import SessionLocal
    from app.models import Setting
    with SessionLocal() as db:
        raw = db.get(Setting, "ai.api_key").value
    assert raw.startswith("enc:") and "sk-ant" not in raw
    client.put("/api/settings", json={"ai.api_key": ""})


def test_zz_reset_and_optimize(client):
    assert client.post("/api/admin/reset", json={"scope": "transactions", "password": "bad", "confirm": "حذف"}).status_code == 403
    r = client.post("/api/admin/reset", json={"scope": "transactions", "password": "Secret123", "confirm": "حذف"}).json()
    assert r["ok"] and r["safety_backup"]
    st = client.get("/api/admin/data").json()["stats"]
    assert st["invoices"] == 0 and st["deposits"] == 0 and st["customers"] > 0
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"] == 0
    r = client.post("/api/admin/reset", json={"scope": "factory", "password": "Secret123", "confirm": "حذف"}).json()
    st = client.get("/api/admin/data").json()["stats"]
    assert st["customers"] == 0 and st["services"] > 0  # defaults re-seeded
    assert client.post("/api/admin/optimize").json()["ok"]


def test_custom_duration_and_version(client, accounts, services):
    from app import __version__
    assert client.get("/api/health").json()["version"] == __version__
    svc = services["پدیکور"]  # default 60 minutes
    from datetime import datetime, timedelta
    # from tomorrow morning, so the two suggestions are on the same day whatever time the tests run
    after = (datetime.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0).isoformat()
    s30 = client.get(f"/api/appointments/suggest?service_id={svc['id']}&duration=30&count=2&after={after}").json()
    a, b = (datetime.fromisoformat(x["start_at"]) for x in s30)
    assert (b - a).seconds == 30 * 60  # suggestions follow the chosen length
    r = client.post("/api/appointments", json={"customer_name": "مدت سفارشی", "customer_mobile": "09128880000", "service_id": svc["id"],
                                               "start_at": s30[0]["start_at"], "duration_minutes": 30}).json()
    assert r["duration_minutes"] == 30 and r["custom_duration"] is True


def test_auto_migration_adds_columns(tmp_path):
    import sqlite3
    from sqlalchemy import create_engine, inspect
    from app.core import db as dbmod
    from app.main import _add_missing_columns
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE appointments (id INTEGER PRIMARY KEY, customer_id INTEGER, start_at DATETIME)")
    con.commit(); con.close()
    old = dbmod.engine
    dbmod.engine = create_engine(f"sqlite:///{path}")
    try:
        _add_missing_columns()
        cols = {c["name"] for c in inspect(dbmod.engine).get_columns("appointments")}
    finally:
        dbmod.engine.dispose()
        dbmod.engine = old
    assert "duration_minutes" in cols


def test_backup_leaves_no_open_files(client, monkeypatch):
    """Windows cannot delete open files: every temp database must be closed before its folder is removed."""
    import os
    import tempfile

    from app.services import backup as backup_mod

    leaks = []
    real = tempfile.TemporaryDirectory

    class Checked(real):  # type: ignore[misc, valid-type]
        def __exit__(self, *exc):
            for fd in os.listdir("/proc/self/fd"):
                try:
                    target = os.readlink(f"/proc/self/fd/{fd}")
                except OSError:
                    continue
                if target.startswith(self.name):
                    leaks.append(target)
            return super().__exit__(*exc)

    monkeypatch.setattr(backup_mod.tempfile, "TemporaryDirectory", Checked)
    m = client.post("/api/backups").json()
    assert client.post(f"/api/backups/{m['name']}/verify").json()["ok"]
    assert client.post(f"/api/backups/{m['name']}/restore").status_code == 200
    r = client.post("/api/admin/reset", json={"scope": "transactions", "password": "Secret123", "confirm": "حذف"})
    assert r.status_code == 200, r.text
    assert leaks == []


def test_staff_line_commission_and_payout(client, accounts):
    line = client.post("/api/lines", json={"name": "آرایش دائم"}).json()
    svc = client.post("/api/services", json={"line_id": line["id"], "name": "فیبروز ابرو", "base_price": 50_000_000}).json()
    leila = client.post("/api/staff", json={"full_name": "لیلا تاجیک", "line_id": line["id"], "commission_percent": 40}).json()
    assert [p["full_name"] for p in client.get(f"/api/staff?service_id={svc['id']}").json()] == ["لیلا تاجیک"]
    # staff is picked automatically from the service's line (only one person in that line)
    d = client.post("/api/deposits", json={"customer_name": "مشتری سهم", "customer_mobile": "09126660000", "amount": 10_000_000,
                                           "payment_account_id": accounts["کارت پاسارگاد"], "service_id": svc["id"]}).json()
    assert d["staff"] == "لیلا تاجیک" and d["line"] == "آرایش دائم"
    inv = client.post("/api/invoices", json={"customer_id": d["customer_id"], "items": [{"service_id": svc["id"], "unit_price": 50_000_000}],
                                             "discount": 10_000_000, "deposit_ids": [d["id"]],
                                             "payments": [{"payment_account_id": accounts["کارتخوان ملت"], "amount": 30_000_000}]}).json()
    it = inv["items"][0]
    assert it["staff"] == "لیلا تاجیک" and it["net_amount"] == 40_000_000 and it["commission_amount"] == 16_000_000
    rep = client.get("/api/reports/staff-shares").json()
    row = next(r for r in rep["staff"] if r["name"] == "لیلا تاجیک")
    assert (row["revenue"], row["staff_share"], row["salon_share"], row["balance"]) == (40_000_000, 16_000_000, 24_000_000, 16_000_000)
    assert next(l for l in rep["lines"] if l["name"] == "آرایش دائم")["salon_share"] == 24_000_000
    r = client.post(f"/api/staff/{leila['id']}/payout", json={"amount": 6_000_000, "payment_account_id": accounts["کارت پاسارگاد"]}).json()
    assert r["balance"] == 10_000_000
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"]


def _future_slot(client, service_id, days):
    from datetime import datetime, timedelta
    after = (datetime.now() + timedelta(days=days)).replace(hour=8, minute=0, second=0, microsecond=0).isoformat()
    return client.get(f"/api/appointments/suggest?service_id={service_id}&after={after}&count=1").json()[0]["start_at"]


def test_invoice_settles_customer_appointment_and_date(client, accounts, services):
    from datetime import datetime, timedelta
    svc = services["مانیکور"]
    a1 = client.post("/api/appointments", json={"customer_name": "میلاد تهمتن", "customer_mobile": "09171353630", "service_id": svc["id"],
                                                "start_at": _future_slot(client, svc["id"], 1)}).json()
    a2 = client.post("/api/appointments", json={"customer_id": a1["customer_id"], "service_id": svc["id"],
                                                "start_at": _future_slot(client, svc["id"], 15)}).json()
    mine = client.get(f"/api/appointments?customer_id={a1['customer_id']}&status=booked").json()
    assert [x["id"] for x in mine] == [a1["id"], a2["id"]]
    body = {"customer_id": a1["customer_id"], "items": [{"service_id": svc["id"], "unit_price": 3_000_000}], "appointment_ids": [a1["id"]],
            "payments": [{"payment_account_id": accounts["کارتخوان ملت"], "amount": 3_000_000}]}
    # money cannot be received in the future
    future = client.post("/api/invoices", json={**body, "issued_at": (datetime.now() + timedelta(days=1)).isoformat()})
    assert future.status_code == 400 and "آینده" in future.json()["detail"]
    issued = (datetime.now() - timedelta(minutes=30)).replace(second=0, microsecond=0)
    inv = client.post("/api/invoices", json={**body, "issued_at": issued.isoformat()}).json()
    assert inv["issued_at"].startswith(issued.isoformat()[:16])
    left = client.get(f"/api/appointments?customer_id={a1['customer_id']}&status=booked").json()
    assert [x["id"] for x in left] == [a2["id"]]  # the other appointment is kept for its own time
    done = client.get(f"/api/appointments/{a1['id']}").json()
    assert done["status"] == "done" and done["invoice_id"] == inv["id"]
    # done a day early: it moves to when it really happened and its reserved time is free again
    assert done["original_start_at"] == a1["start_at"] and done["start_at"] == issued.isoformat()[:16]
    assert [f["start_at"] for f in inv["freed"]] == [a1["start_at"]]
    chk = client.get(f"/api/appointments/check?service_id={svc['id']}&start_at={a1['start_at']}"
                     + (f"&staff_id={a1['staff_id']}" if a1["staff_id"] else "")).json()
    assert chk["ok"], chk


def test_money_dates_cannot_be_in_the_future(client, accounts):
    from datetime import datetime, timedelta
    tomorrow = (datetime.now() + timedelta(days=1)).isoformat()
    r = client.post("/api/deposits", json={"customer_name": "تست تاریخ", "customer_mobile": "09125550000", "amount": 1_000_000,
                                           "payment_account_id": accounts["کارتخوان ملت"], "received_at": tomorrow})
    assert r.status_code == 400 and "آینده" in r.json()["detail"]
    r = client.post("/api/expenses", json={"category": "اجاره", "amount": 1_000_000, "payment_account_id": accounts["کارتخوان ملت"],
                                           "spent_at": tomorrow})
    assert r.status_code == 400
    yesterday = (datetime.now() - timedelta(days=1)).isoformat()
    r = client.post("/api/deposits", json={"customer_name": "تست تاریخ", "customer_mobile": "09125550000", "amount": 1_000_000,
                                           "payment_account_id": accounts["کارتخوان ملت"], "received_at": yesterday})
    assert r.status_code == 200


def test_cancel_offers_time_to_vip_waitlist(client, services):
    svc = services["مانیکور"]
    when = _future_slot(client, svc["id"], 4)
    a = client.post("/api/appointments", json={"customer_name": "کنسلی", "customer_mobile": "09126660001", "service_id": svc["id"],
                                               "start_at": when}).json()
    normal = client.post("/api/waitlist", json={"customer_name": "منتظر عادی", "customer_mobile": "09126660002",
                                                "service_id": svc["id"], "vip": False}).json()
    vip = client.post("/api/waitlist", json={"customer_name": "منتظر ویژه", "customer_mobile": "09126660003",
                                             "service_id": svc["id"], "vip": True}).json()
    assert client.post("/api/waitlist", json={"customer_id": vip["customer_id"], "service_id": svc["id"]}).status_code == 409
    r = client.patch(f"/api/appointments/{a['id']}?status=cancelled").json()
    assert r["freed"]["start_at"] == when and r["freed"]["waiting"] >= 2
    offers = client.get(f"/api/waitlist/offers?start_at={when}&service_id={svc['id']}").json()
    ids = [o["id"] for o in offers]
    assert ids.index(vip["id"]) < ids.index(normal["id"]) and offers[ids.index(vip["id"])]["fits"]
    booked = client.post(f"/api/waitlist/{vip['id']}/book", json={"start_at": when})
    assert booked.status_code == 200 and booked.json()["customer_id"] == vip["customer_id"]
    assert vip["id"] not in [w["id"] for w in client.get("/api/waitlist").json()]
    # the time is taken again
    again = client.post(f"/api/waitlist/{normal['id']}/book", json={"start_at": when, "staff_id": booked.json()["staff_id"]})
    assert again.status_code == 409 or booked.json()["staff_id"] is None


def test_booking_rules_are_strict(client, accounts):
    from datetime import datetime, timedelta
    client.put("/api/settings", json={"booking.open": "09:00", "booking.close": "21:00", "booking.days_off": []})
    line = client.post("/api/lines", json={"name": "لاین تست نوبت"}).json()
    svc = client.post("/api/services", json={"line_id": line["id"], "name": "خدمت تست نوبت", "base_price": 1000, "duration_minutes": 120}).json()
    staff = client.post("/api/staff", json={"full_name": "پرسنل تست نوبت", "line_id": line["id"]}).json()
    day = (datetime.now() + timedelta(days=3)).replace(hour=13, minute=0, second=0, microsecond=0)
    t = lambda h, m=0: day.replace(hour=h, minute=m).isoformat()  # noqa: E731
    book = lambda **kw: client.post("/api/appointments", json={"service_id": svc["id"], **kw})  # noqa: E731

    past = book(customer_name="الف", customer_mobile="09120000001", start_at=(datetime.now() - timedelta(hours=2)).isoformat())
    assert past.status_code == 409 and "گذشته" in past.json()["detail"]

    a = book(customer_name="الف", customer_mobile="09120000001", start_at=t(13))
    assert a.status_code == 200 and a.json()["staff"] == "پرسنل تست نوبت"
    a = a.json()
    # same staff, overlapping time, another customer
    r = book(customer_name="ب", customer_mobile="09120000002", start_at=t(14))
    assert r.status_code == 409
    # the live check reports the same problem
    chk = client.get(f"/api/appointments/check?service_id={svc['id']}&start_at={t(14)}").json()
    assert not chk["ok"] and chk["errors"]
    # a completed appointment still occupies its time
    client.patch(f"/api/appointments/{a['id']}?status=done")
    assert book(customer_name="ب", customer_mobile="09120000002", start_at=t(13)).status_code == 409
    sugg = [s["start_at"] for s in client.get(f"/api/appointments/suggest?service_id={svc['id']}&after={t(9)}&count=10").json()]
    assert all(not (t(13)[:16] <= s < t(15)[:16]) for s in sugg)
    # back-to-back is fine
    b = book(customer_name="ب", customer_mobile="09120000002", start_at=t(15))
    assert b.status_code == 200, b.text
    b = b.json()
    # same customer cannot be in two places at once (different line, no staff conflict)
    other = client.get("/api/services").json()[0]
    r = client.post("/api/appointments", json={"customer_id": b["customer_id"], "service_id": other["id"], "start_at": t(15, 30)})
    assert r.status_code == 409 and "مشتری" in r.json()["detail"]
    # outside working hours needs explicit confirmation
    r = book(customer_name="ج", customer_mobile="09120000003", start_at=t(20))
    assert r.status_code == 409 and "ساعت کاری" in r.json()["detail"]
    assert book(customer_name="ج", customer_mobile="09120000003", start_at=t(20), allow_outside_hours=True).status_code == 200
    # cancelling frees the slot; re-activating then conflicts
    client.patch(f"/api/appointments/{b['id']}?status=cancelled")
    c = book(customer_name="د", customer_mobile="09120000004", start_at=t(15))
    assert c.status_code == 200
    assert client.patch(f"/api/appointments/{b['id']}?status=booked").status_code == 409
    # rescheduling onto a taken time is refused; a done appointment cannot be moved
    assert client.put(f"/api/appointments/{c.json()['id']}", json={"start_at": t(13)}).status_code == 409
    assert client.put(f"/api/appointments/{a['id']}", json={"start_at": t(17)}).status_code == 400
    # a deposit with an invalid booking is not saved at all
    before = len(client.get("/api/deposits?status=").json())
    r = client.post("/api/deposits", json={"customer_name": "ه", "customer_mobile": "09120000005", "amount": 1000,
                                           "payment_account_id": accounts["کارت پاسارگاد"], "service_id": svc["id"], "book_at": t(13, 30)})
    assert r.status_code == 409
    assert len(client.get("/api/deposits?status=").json()) == before
    client.put("/api/settings", json={"booking.open": "10:00", "booking.close": "20:00", "booking.days_off": [4]})


def test_line_calendar_shows_empty_partial_full_days(client):
    from datetime import datetime, timedelta
    client.put("/api/settings", json={"booking.open": "10:00", "booking.close": "14:00", "booking.days_off": []})
    line = client.post("/api/lines", json={"name": "لاین تقویم"}).json()
    svc = client.post("/api/services", json={"line_id": line["id"], "name": "خدمت تقویم", "base_price": 1000, "duration_minutes": 120}).json()
    client.post("/api/staff", json={"full_name": "پرسنل تقویم", "line_id": line["id"]})
    day = (datetime.now() + timedelta(days=5)).replace(hour=10, minute=0, second=0, microsecond=0)
    for h, mob in ((10, "09130000001"), (12, "09130000002")):  # fills the whole 10-14 day
        r = client.post("/api/appointments", json={"customer_name": "تقویم", "customer_mobile": mob, "service_id": svc["id"],
                                                   "start_at": day.replace(hour=h).isoformat()})
        assert r.status_code == 200, r.text
    half = day + timedelta(days=1)
    client.post("/api/appointments", json={"customer_name": "تقویم", "customer_mobile": "09130000003", "service_id": svc["id"],
                                           "start_at": half.isoformat()})
    cal = client.get(f"/api/appointments/calendar?line_id={line['id']}&days=10").json()
    days = {d["date"]: d for d in cal["days"]}
    assert cal["capacity"] == 1 and cal["min_duration"] == 120
    assert days[day.date().isoformat()]["status"] == "full" and days[day.date().isoformat()]["count"] == 2
    assert days[half.date().isoformat()]["status"] == "partial" and days[half.date().isoformat()]["first_free"] == "12:00"
    assert days[(day + timedelta(days=2)).date().isoformat()]["status"] == "empty"
    listed = client.get(f"/api/appointments?line_id={line['id']}&start={day.date()}&end={day.date()}").json()
    assert len(listed) == 2 and listed[0]["customer_mobile"] == "09130000001"
    client.put("/api/settings", json={"booking.open": "10:00", "booking.close": "20:00"})


def test_ledger_balances_and_account_statement(client, accounts):
    """An expense paid in cash moves money between accounts: turnover grows on both sides, closing balances don't."""
    cash_id = accounts["صندوق نقدی"]
    r = client.post("/api/deposits", json={"customer_name": "مشتری دفاتر", "customer_mobile": "09351112233",
                                           "amount": 300_000_000, "payment_account_id": cash_id})
    assert r.status_code == 200, r.text
    before = client.get("/api/ledger/trial-balance").json()
    r = client.post("/api/expenses", json={"category": "تست دفاتر", "amount": 100_000_000, "payment_account_id": cash_id, "description": "قبض برق"})
    assert r.status_code == 200, r.text
    after = client.get("/api/ledger/trial-balance").json()
    assert after["turnover_debit"] - before["turnover_debit"] == 100_000_000
    assert after["turnover_debit"] == after["turnover_credit"]
    assert after["balance_debit"] == after["balance_credit"]
    # cash goes down by exactly the expense; profit drops by it too
    assert after["cash"] - before["cash"] == -100_000_000
    assert after["net_profit"] - before["net_profit"] == -100_000_000
    assert after["balance_debit"] == before["balance_debit"]  # cash still positive -> closing totals unchanged
    for r_ in after["rows"]:
        assert r_["debit"] - r_["credit"] == r_["debit_balance"] - r_["credit_balance"]
    a = after["by_type"]
    assert a["asset"] + a["expense"] == a["liability"] + a["equity"] + a["revenue"]

    bal2 = {b["id"]: b for b in client.get("/api/accounts/balances").json()}[cash_id]
    st = client.get(f"/api/ledger/accounts/{bal2['ledger_account_id']}/statement").json()
    assert st["closing"] == bal2["balance"] == st["total_debit"] - st["total_credit"]
    assert st["count"] == bal2["count"]
    running = 0
    for row in st["rows"]:
        running += row["debit"] - row["credit"]
        assert row["balance"] == running
    last = st["rows"][-1]
    assert last["credit"] == 100_000_000 and last["ref_type"] == "expense" and last["detail"] == "قبض برق"
    assert "تست دفاتر" in last["counterpart"]

    j = client.get("/api/ledger/journal?ref_type=expense&limit=5").json()
    assert j["total"] >= 1 and all(e["amount"] == e["credit_total"] for e in j["entries"])
    assert j["entries"][0]["lines"][0]["debit"] > 0  # debit lines come first


def test_child_account_codes_never_collide():
    from app.core.db import SessionLocal
    from app.services import accounting
    with SessionLocal() as db:
        codes = set()
        for i in range(105):
            codes.add(accounting.expense_account(db, f"دسته تستی {i}").code)
        assert len(codes) == 105 and accounting.COMMISSION not in codes
        assert accounting.account(db, accounting.COMMISSION).name == "پورسانت پرسنل"
        db.rollback()


def _xlsx(rows):
    import io

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_legacy_import_history_deposits_and_undo(client, accounts, services):
    from datetime import datetime, timedelta

    from app.services.jalali import gregorian_to_jalali
    j = lambda d: "%04d/%02d/%02d" % gregorian_to_jalali(d.year, d.month, d.day)  # noqa: E731
    past, future = datetime.now() - timedelta(days=40), datetime.now() + timedelta(days=9)
    # history export: title row above the header, mobile without its leading zero, Persian digits, unknown service
    history = _xlsx([
        ["گزارش سوابق مشتریان - نرم افزار چهره"],
        ["ردیف", "نام و نام خانوادگی", "شماره همراه", "تاریخ مراجعه", "شرح خدمت", "آرایشگر", "مبلغ (ریال)"],
        [1, "مینا قدیمی", 9121230001, j(past), "مانیکور", "", 2_000_000],
        [2, "مینا قدیمی", 9121230001, j(past - timedelta(days=30)), "لیفت مژه ویژه", "", "۳,۵۰۰,۰۰۰"],
        [3, "", "", "", "", "", ""],
        [4, "بدون تاریخ", "09121230002", "", "مانیکور", "", 1],
        ["جمع کل", "", "", "", "", "", 5_500_000],
    ])
    files = {"file": ("chehreh-history.xlsx", history, "application/octet-stream")}
    p = client.post("/api/import/legacy/preview", files=files, data={"kind": "history", "unit": "rial"})
    assert p.status_code == 200, p.text
    pv = p.json()
    assert pv["summary"]["ok"] == 2 and pv["summary"]["errors"] == 1 and pv["summary"]["new_customers"] == 1
    assert [u["name"] for u in pv["unknown_services"]] == ["لیفت مژه ویژه"]
    assert pv["sample"][0]["mobile"] == "09121230001"
    r = client.post(f"/api/import/legacy/{pv['id']}/commit", json={"service_map": {"لیفت مژه ویژه": "new"}}).json()
    assert r["result"]["appointments"] == 2 and r["result"]["customers_new"] == 1
    cust = client.get("/api/customers?q=09121230001").json()["items"][0]
    done = client.get(f"/api/appointments?customer_id={cust['id']}&status=done").json()
    assert {a["service"] for a in done} == {"مانیکور", "لیفت مژه ویژه"}
    assert any(a["start_at"][:10] == past.date().isoformat() for a in done)
    hist = client.get(f"/api/customers/{cust['id']}").json()["history"]
    assert [h["service"] for h in hist] == ["مانیکور", "لیفت مژه ویژه"] and hist[0]["source"] == "import"
    # importing the same file again does not duplicate anything
    p2 = client.post("/api/import/legacy/preview", files=files, data={"kind": "history", "unit": "rial"}).json()
    again = client.post(f"/api/import/legacy/{p2['id']}/commit", json={"service_map": {"لیفت مژه ویژه": "new"}}).json()
    assert again["result"]["appointments"] == 0 and again["result"]["skipped_duplicates"] == 2

    # deposits for future appointments, amounts in Toman
    cash_before = {a["code"]: a["balance"] for a in client.get("/api/ledger/trial-balance").json()["rows"]}
    deposits = _xlsx([
        ["نام مشتری", "موبایل", "تاریخ دریافت", "مبلغ بیعانه", "خدمت", "تاریخ نوبت", "ساعت نوبت"],
        ["مینا قدیمی", "09121230001", j(datetime.now() - timedelta(days=3)), 500_000, "مانیکور", j(future), "15:30"],
    ])
    p3 = client.post("/api/import/legacy/preview", files={"file": ("dep.xlsx", deposits, "application/octet-stream")},
                     data={"kind": "deposits", "unit": "toman"}).json()
    assert p3["summary"]["amount"] == 5_000_000 and p3["summary"]["future_appointments"] == 1
    c3 = client.post(f"/api/import/legacy/{p3['id']}/commit", json={}).json()
    assert c3["result"]["deposits"] == 1 and c3["result"]["appointments"] == 1
    held = client.get(f"/api/deposits?status=held&customer_id={cust['id']}").json()
    assert held[0]["amount"] == 5_000_000 and held[0]["appointment_at"] == future.date().isoformat() + "T15:30"
    tbj = client.get("/api/ledger/trial-balance").json()
    tb = {a["code"]: a["balance"] for a in tbj["rows"]}
    assert tb.get("3200") == -5_000_000 and tbj["total_debit"] == tbj["total_credit"]  # opening balance account, cash untouched
    cash_codes = [c for c in tb if c.startswith("11") and c != "1100"]
    assert all(tb[c] == cash_before.get(c) for c in cash_codes)

    # undo the deposit import -> gone again, books still balance
    u = client.post(f"/api/import/legacy/{p3['id']}/undo").json()
    assert u["removed"]["deposits"] == 1 and u["removed"]["appointments"] == 1
    assert client.get(f"/api/deposits?status=held&customer_id={cust['id']}").json() == []


def test_legacy_import_rejects_old_xls_and_missing_columns(client):
    r = client.post("/api/import/legacy/preview", files={"file": ("old.xls", b"\xd0\xcf\x11\xe0", "application/vnd.ms-excel")},
                    data={"kind": "history"})
    assert r.status_code == 400 and "xlsx" in r.json()["detail"]
    bad = "نام مشتری,موبایل\nسارا,09120000000\n".encode()
    r = client.post("/api/import/legacy/preview", files={"file": ("x.csv", bad, "text/csv")}, data={"kind": "deposits"})
    assert r.status_code == 400 and "مبلغ" in r.json()["detail"]


def test_chehreh_deposits_and_receipts_files(client, accounts):
    """The two real export layouts of «چهره»: open deposits (صندوق ودیعه) and grouped receipts (شماره پذیرش)."""
    from datetime import datetime, timedelta

    from app.services.jalali import gregorian_to_jalali
    j = lambda d: "%04d/%02d/%02d" % gregorian_to_jalali(d.year, d.month, d.day)  # noqa: E731
    line = client.post("/api/lines", json={"name": "آرایش دائم چهره"}).json()
    client.post("/api/services", json={"line_id": line["id"], "name": "فیبروز ابرو", "base_price": 45_000_000, "duration_minutes": 120})
    client.post("/api/staff", json={"full_name": "لیلا تاجیک چهره", "line_id": line["id"]})
    today, soon, past = datetime.now(), datetime.now() + timedelta(days=6), datetime.now() - timedelta(days=20)
    hdr = ["", "صندوقدار", "کد مشتری", "کد اشتراک", "نام مشتری", "تلفن", "تاریخ پرداخت", "تاریخ مراجعه", "تاریخ تسویه", "وضعیت",
           "مبلغ بیعانه", "غیرنقدی", "کدپیگیری", "عروس", "پکیج", "شماره قرارگاه", "", "پرسنل", "تاریخ ثبت", "ساعت ثبت"]
    deposits = _xlsx([hdr,
        ["", "مدیر", 93500, 9305206, "رقیه پارسا چهره", "09179305206", j(today), j(soon), "", "صندوق ودیعه", 500000, 500000, "", False,
         "", "", "لاین آرایش دائم چهره", "لیلا تاجیک چهره", j(today), "10:57"],
        ["", "مدیر", 93522, 3395946, "فاطمه حقیقی چهره", "0903395946", j(today), j(soon), "", "صندوق ودیعه", 1000000, 1000000, "", False,
         "", "", "لاین آرایش دائم چهره", "لیلا تاجیک چهره", j(today), "11:34"],
        ["", "مدیر", 91619, 395946, "فاطمه حقیقی دوم", "09030395946", j(past), j(past), j(past), "تسویه", 500000, 500000, "", False,
         "", "", "لاین آرایش دائم چهره", "لیلا تاجیک چهره", j(past), "12:04"],
    ])
    p = client.post("/api/import/legacy/preview", files={"file": ("bey.xlsx", deposits, "application/octet-stream")},
                    data={"kind": "deposits", "unit": "toman"})
    assert p.status_code == 200, p.text
    pv = p.json()
    m = pv["mapping"]
    assert pv["headers"][m["date"]] == "تاریخ پرداخت" and pv["headers"][m["appt_date"]] == "تاریخ مراجعه"
    assert pv["headers"][m["customer_code"]] == "کد مشتری" and pv["headers"][m["staff"]] == "پرسنل" and "time" not in m
    assert m["line"] == 16 and pv["summary"]["ok"] == 2 and pv["summary"]["skipped_settled"] == 1
    assert pv["summary"]["amount"] == 15_000_000
    assert any("ناقص" in w for r in pv["sample"] for w in r["warnings"])  # 0903395946 has a digit missing
    r = client.post(f"/api/import/legacy/{pv['id']}/commit", json={}).json()["result"]
    assert r["deposits"] == 2 and r["appointments"] == 2 and r["customers_new"] == 3
    # a customer who only had a settled deposit still comes over with code and mobile (but no deposit)
    settled = client.get("/api/customers?q=91619").json()["items"][0]
    assert settled["mobile"] == "09030395946" and settled["deposits_held"] == 0
    held = client.get("/api/deposits?status=held").json()
    mine = [d for d in held if d["customer"] == "رقیه پارسا چهره"][0]
    assert mine["service"] == "فیبروز ابرو" and mine["staff"] == "لیلا تاجیک چهره"  # the line's only service
    appt = client.get(f"/api/appointments/{mine['appointment_id']}").json()
    assert appt["time_unknown"] and appt["start_at"][:10] == soon.date().isoformat()
    cal = client.get(f"/api/appointments/calendar?line_id={line['id']}&days=10").json()
    day = [d for d in cal["days"] if d["date"] == soon.date().isoformat()][0]
    assert day["unknown_time"] == 2 and day["status"] != "full"  # listed, but no slot taken
    # setting the real hour makes it a normal appointment
    when = soon.replace(hour=11, minute=0, second=0, microsecond=0).isoformat(timespec="minutes")
    e = client.put(f"/api/appointments/{appt['id']}", json={"start_at": when, "allow_outside_hours": True})
    assert e.status_code == 200 and not e.json()["time_unknown"]

    # receipts: grouped by «شماره پذیرش», no mobile - linked to the deposit customer by customer code
    rh = ["", "", "نام پرسنل", "نام مشتری", "شماره فیش", "صندوقدار", "تاریخ", "ساعت", "کد مشتری", "کد اشتراک", "مبلغ کل", "درصد",
          "سهم پرسنل", "تخفیف پرسنل", "تخفیف سالن", "قابل پرداخت", "خدمت", "توضیحات", "دستیار", "درصد دستیار", "کارتخوان",
          "مسترد کننده", "تاریخ استرداد"]
    group = lambda no: ["شماره پذیرش: %s (مبلغ کل فیش: ۸,۰۰۰,۰۰۰) (مبلغ بیعانه : ۵۰۰,۰۰۰)" % no] + [""] * 22  # noqa: E731
    item = lambda staff, cust, code, svc, amt, refund="": ["", "", staff, cust, 1, "مدیر", j(past), "19:15", code, "", amt, 0, 0, 0, 0,  # noqa: E731
                                                         amt, svc, "", "", 0, "پوز صادرات", "", refund]
    receipts = _xlsx([rh, group("1403060034"),
                      item("لیلا تاجیک چهره", "رقیه پارسا چهره", 93500, "فیبروز ابرو", 4500000),
                      item("لیلا تاجیک چهره", "رقیه پارسا چهره", 93500, "بن مژه چهره", 3500000),
                      group("1403060035"),
                      item("مریم ناشناس", "مشتری بدون موبایل", 90408, "اوزون تراپی", 200000),
                      item("مریم ناشناس", "مشتری بدون موبایل", 90408, "اوزون تراپی", 200000),
                      group("1403060036"),
                      item("لیلا تاجیک چهره", "مسترد شده", 90409, "فیبروز ابرو", 4500000, j(past))])
    p = client.post("/api/import/legacy/preview", files={"file": ("fish.xlsx", receipts, "application/octet-stream")},
                    data={"kind": "history", "unit": "toman"}).json()
    hm = p["mapping"]
    assert p["headers"][hm["staff"]] == "نام پرسنل" and p["headers"][hm["amount"]] == "قابل پرداخت" and "mobile" not in hm
    assert p["summary"]["ok"] == 4 and p["summary"]["skipped_refunded"] == 1 and p["sample"][0]["receipt"] == "1403060034"
    r = client.post(f"/api/import/legacy/{p['id']}/commit", json={}).json()["result"]
    assert r["appointments"] == 4 and r["customers_new"] == 2  # رقیه is matched by her code; the refunded line only brings its customer
    cust = client.get("/api/customers?q=09179305206").json()["items"][0]
    # the list counts the previous software's receipts: purchases, visits, last visit
    assert cust["total_spent"] == 80_000_000 and cust["spent_old"] == 80_000_000 and cust["visits"] == 1
    assert cust["last_visit"][:10] == past.date().isoformat() and cust["deposits_held"] == 5_000_000
    top = client.get("/api/customers?sort=spent&limit=5").json()["items"]
    assert top[0]["total_spent"] >= top[-1]["total_spent"]
    assert all(c["deposits_held"] > 0 for c in client.get("/api/customers?filter=held").json()["items"])
    hist = client.get(f"/api/customers/{cust['id']}").json()["history"]
    assert {h["service"] for h in hist} == {"فیبروز ابرو", "بن مژه چهره"} and hist[0]["amount"] in (45_000_000, 35_000_000)
    # the same file again: nothing new (two identical ozone lines of one receipt stay two)
    p2 = client.post("/api/import/legacy/preview", files={"file": ("fish.xlsx", receipts, "application/octet-stream")},
                     data={"kind": "history", "unit": "toman"}).json()
    r2 = client.post(f"/api/import/legacy/{p2['id']}/commit", json={}).json()["result"]
    assert r2["appointments"] == 0 and r2["skipped_duplicates"] == 4


def test_legacy_import_reads_html_table_saved_as_xls(client):
    html = ("<html><body><table><tr><th>نام مشتری</th><th>تلفن</th><th>تاریخ</th><th>خدمت</th></tr>"
            "<tr><td>آزمون اچ‌تی‌ام‌ال</td><td>09125556677</td><td>1404/01/15</td><td>مانیکور</td></tr></table></body></html>").encode()
    r = client.post("/api/import/legacy/preview", files={"file": ("report.xls", html, "application/vnd.ms-excel")},
                    data={"kind": "history"})
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["ok"] == 1 and r.json()["sample"][0]["date"].startswith("2025-04-04")


def test_customer_codes_mobile_warnings_and_cleanup(client, accounts):
    # every new customer gets the next free code; a taken code is refused
    a = client.post("/api/customers", json={"full_name": "کددار دستی", "mobile": "09127770001"}).json()
    assert a["code"] and a["code"].isdigit()
    b = client.post("/api/customers", json={"full_name": "کددار دوم", "code": "880077"}).json()
    assert b["code"] == "880077"
    assert client.post("/api/customers", json={"full_name": "تکراری", "code": "880077"}).status_code == 409
    assert client.get("/api/customers?q=880077").json()["items"][0]["id"] == b["id"]

    # the standard customers template: code, name, mobile
    tpl = client.get("/api/import/legacy/template?kind=customers")
    assert tpl.status_code == 200 and tpl.content[:2] == b"PK"
    rows = [["کد مشتری", "نام مشتری", "موبایل"],
            ["880077", "مشتری چهره ۷۷", "09127770077"],      # code given by hand here to someone else -> theirs moves
            ["880078", "کددار دستی", "09127770001"],         # same person entered by hand -> gets the old code
            ["880079", "بدون موبایل درست", "0912777"],       # incomplete number -> warning only
            ["880080", "هم‌شماره یک", "09127770099"],
            ["880081", "هم‌شماره دو", "09127770099"]]        # duplicate mobile -> warning only
    p = client.post("/api/import/legacy/preview", files={"file": ("c.xlsx", _xlsx(rows), "application/octet-stream")},
                    data={"kind": "customers"}).json()
    assert p["summary"]["ok"] == 5 and p["summary"]["errors"] == 0 and p["summary"]["mobile_issues"] >= 3
    warns = {r["code"]: " ".join(r["warnings"]) for r in p["sample"]}
    assert "ناقص" in warns["880079"] and "تکراری" in warns["880080"] and "تکراری" in warns["880081"]
    r = client.post(f"/api/import/legacy/{p['id']}/commit", json={}).json()["result"]
    assert r["codes_moved"] == 1 and r["customers_new"] == 4
    moved = client.get(f"/api/customers/{b['id']}").json()
    assert moved["code"] not in ("880077", None) and int(moved["code"]) > 880081
    assert client.get(f"/api/customers/{a['id']}").json()["code"] == "880078"
    two = client.get("/api/customers?q=09127770099").json()["items"]
    assert {c["full_name"] for c in two} == {"هم‌شماره یک", "هم‌شماره دو"}
    dup = [c for c in two if c["full_name"] == "هم‌شماره دو"][0]
    assert dup["mobile"] is None and dup["mobile_issue"] == "duplicate" and dup["mobile_raw"] == "09127770099"

    # cleanup: customers without any service and a wrong/duplicate number, deleted together
    client.post("/api/deposits", json={"customer_id": dup["id"], "amount": 1_000_000, "payment_account_id": accounts["کارتخوان ملت"]})
    cand = client.get("/api/customers/cleanup?issues=invalid,duplicate").json()["items"]
    names = {c["full_name"] for c in cand}
    assert "بدون موبایل درست" in names and "هم‌شماره دو" not in names  # has a deposit -> kept out
    res = client.post("/api/customers/bulk-delete", json={"ids": [c["id"] for c in cand] + [dup["id"]]}).json()
    assert res["deleted"] == len(cand) and res["skipped"] == 1
    assert client.get("/api/customers?q=880079").json()["items"] == []


def test_import_templates_are_recognised(client):
    for kind in ("customers", "deposits", "history"):
        data = client.get(f"/api/import/legacy/template?kind={kind}").content
        p = client.post("/api/import/legacy/preview", files={"file": (f"{kind}.xlsx", data, "application/octet-stream")},
                        data={"kind": kind, "unit": "toman"})
        assert p.status_code == 200, (kind, p.text)
        pv = p.json()
        titles = {pv["headers"][i] for i in pv["mapping"].values()}
        assert titles == {h for h in pv["headers"] if h}, (kind, set(pv["headers"]) - titles)
        assert pv["summary"]["ok"] == 1 and pv["sample"][0]["code"] == "1001", kind
        client.delete(f"/api/import/legacy/{pv['id']}")


def test_persian_ye_kaf_search_and_deposits_page(client, accounts, services):
    arabic = client.post("/api/customers", json={"full_name": "مريم كاظمي‌نژاد", "mobile": "09126543210"}).json()
    for q in ("مریم", "مريم", "کاظمی", "كاظمي نژاد", "کاظمی‌نژاد"):
        found = [c["id"] for c in client.get("/api/customers", params={"q": q}).json()["items"]]
        assert arabic["id"] in found, q
    # deposits page: search (by code / name in either spelling / mobile), filters, sorting, totals, detail
    acc = accounts["کارتخوان ملت"]
    d1 = client.post("/api/deposits", json={"customer_id": arabic["id"], "amount": 3_000_000, "payment_account_id": acc,
                                            "service_id": services["مانیکور"]["id"], "reference": "778899"}).json()
    d2 = client.post("/api/deposits", json={"customer_id": arabic["id"], "amount": 1_000_000, "payment_account_id": acc}).json()
    r = client.get("/api/deposits/search", params={"q": "مریم کاظمی"}).json()
    assert {x["id"] for x in r["items"]} >= {d1["id"], d2["id"]} and r["amount"] >= 4_000_000
    assert client.get("/api/deposits/search", params={"q": arabic["code"]}).json()["total"] >= 2
    assert [x["id"] for x in client.get("/api/deposits/search", params={"q": "778899"}).json()["items"]] == [d1["id"]]
    by_amount = client.get("/api/deposits/search", params={"q": "مريم", "sort": "amount_asc"}).json()["items"]
    assert [x["amount"] for x in by_amount] == sorted(x["amount"] for x in by_amount)
    no_svc = client.get("/api/deposits/search", params={"q": "مريم", "filter": "no_service"}).json()["items"]
    assert [x["id"] for x in no_svc] == [d2["id"]]
    page = client.get("/api/deposits/search", params={"limit": 1, "offset": 0}).json()
    assert len(page["items"]) == 1 and page["total"] >= 2 and page["stats"]["held"][0] >= 2
    # detail + edit + refund
    det = client.get(f"/api/deposits/{d2['id']}").json()
    assert det["customer_code"] == arabic["code"] and det["account"] and det["timeline"] and det["others"][0]["id"] == d1["id"]
    e = client.put(f"/api/deposits/{d2['id']}", json={"service_id": services["مانیکور"]["id"], "notes": "اصلاح شد"}).json()
    assert e["service_id"] == services["مانیکور"]["id"]
    client.post(f"/api/deposits/{d2['id']}/close", json={"action": "refund", "refund_account_id": acc})
    det = client.get(f"/api/deposits/{d2['id']}").json()
    assert det["status"] == "refunded" and len(det["timeline"]) == 2
    assert client.get("/api/deposits/search", params={"q": "مريم", "status": "refunded"}).json()["total"] == 1


def test_invoices_search_and_dashboard_ignores_imported_money(client, accounts, services):
    svc = services["مانیکور"]
    c = client.post("/api/customers", json={"full_name": "علي فاکتوري", "mobile": "09124443322"}).json()
    inv = client.post("/api/invoices", json={"customer_id": c["id"], "items": [{"service_id": svc["id"], "unit_price": 2_000_000}],
                                             "payments": [{"payment_account_id": accounts["کارتخوان ملت"], "amount": 500_000}]}).json()
    r = client.get("/api/invoices/search", params={"q": "علی فاکتوری"}).json()
    assert [x["id"] for x in r["items"]] == [inv["id"]] and r["items"][0]["code"] == c["code"]
    assert r["amount"] == 2_000_000 and r["due"] == 1_500_000
    assert inv["id"] in [x["id"] for x in client.get("/api/invoices/search", params={"q": "مانيکور"}).json()["items"]]
    assert inv["id"] in [x["id"] for x in client.get("/api/invoices/search", params={"status": "unpaid"}).json()["items"]]
    assert client.get("/api/invoices/search", params={"q": inv["number"]}).json()["total"] == 1
    page = client.get("/api/invoices/search", params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["stats"]["today"][0] >= 1 and page["stats"]["unpaid"][0] >= 1
    # dashboard: an imported (old-system) deposit is not money received today
    before = client.get("/api/dashboard").json()
    rows = [["کد مشتری", "نام مشتری", "موبایل", "تاریخ پرداخت", "مبلغ بیعانه"], ["771100", "قدیمی داشبورد", "09124440011", "", 9_000_000]]
    p = client.post("/api/import/legacy/preview", files={"file": ("d.xlsx", _xlsx(rows), "application/octet-stream")},
                    data={"kind": "deposits", "unit": "rial"}).json()
    client.post(f"/api/import/legacy/{p['id']}/commit", json={})
    after = client.get("/api/dashboard").json()
    assert after["today"]["cash_in"] == before["today"]["cash_in"]
    assert after["today"]["new_customers"] == before["today"]["new_customers"]
    assert after["today"]["deposits_held"] == before["today"]["deposits_held"] + 9_000_000
    ag = after["agenda"]
    assert "today" in ag and ag["todo"]["unpaid_invoices"][0] >= 1 and "previous" in after
    # deposits page shows real service names
    d = client.post("/api/deposits", json={"customer_id": c["id"], "amount": 1_000_000, "payment_account_id": accounts["کارتخوان ملت"],
                                           "service_id": svc["id"]}).json()
    found = [x for x in client.get("/api/deposits/search", params={"q": c["code"]}).json()["items"] if x["id"] == d["id"]][0]
    assert found["service"] == "مانیکور" and found["line"]


def test_void_paid_invoice_refund_or_keep_as_deposit(client, accounts, services):
    svc = services["مانیکور"]
    acc = accounts["کارتخوان ملت"]
    c = client.post("/api/customers", json={"full_name": "ابطال آزمون", "mobile": "09125556600"}).json()
    dep = client.post("/api/deposits", json={"customer_id": c["id"], "amount": 1_000_000, "payment_account_id": acc}).json()
    when = _future_slot(client, svc["id"], 6)
    appt = client.post("/api/appointments", json={"customer_id": c["id"], "service_id": svc["id"], "start_at": when}).json()
    tb0 = client.get("/api/ledger/trial-balance").json()
    inv = client.post("/api/invoices", json={"customer_id": c["id"], "items": [{"service_id": svc["id"], "unit_price": 3_000_000}],
                                             "deposit_ids": [dep["id"]], "appointment_ids": [appt["id"]],
                                             "payments": [{"payment_account_id": acc, "amount": 2_000_000}]}).json()
    assert inv["status"] == "paid"
    assert client.get(f"/api/appointments/{appt['id']}").json()["status"] == "done"
    # a paid invoice can now be voided: money refunded, deposit open again, appointment booked again at its time
    v = client.post(f"/api/invoices/{inv['id']}/void", params={"payments": "refund"})
    assert v.status_code == 200 and v.json()["status"] == "void", v.text
    assert client.get(f"/api/deposits/{dep['id']}").json()["status"] == "held"
    a = client.get(f"/api/appointments/{appt['id']}").json()
    assert a["status"] == "booked" and a["start_at"] == when and not a["invoice_id"]
    tb1 = client.get("/api/ledger/trial-balance").json()
    bal = lambda tb: {r["code"]: r["balance"] for r in tb["rows"]}  # noqa: E731
    for code in ("1200", "2100"):  # receivables and deposits back where they were
        assert bal(tb1).get(code, 0) == bal(tb0).get(code, 0), code
    assert tb1["total_debit"] == tb1["total_credit"]
    # keep the money as a deposit instead
    inv2 = client.post("/api/invoices", json={"customer_id": c["id"], "items": [{"service_id": svc["id"], "unit_price": 1_500_000}],
                                              "apply_deposits": False, "payments": [{"payment_account_id": acc, "amount": 1_500_000}]}).json()
    client.post(f"/api/invoices/{inv2['id']}/void", params={"payments": "deposit"})
    held = client.get(f"/api/deposits?status=held&customer_id={c['id']}").json()
    assert sorted(d["amount"] for d in held) == [1_000_000, 1_500_000]


def test_opening_balances_and_cash_count(client):
    from datetime import datetime, timedelta
    box = client.post("/api/accounts", json={"kind": "cash", "name": "صندوق آزمون افتتاحیه", "opening_balance": 50_000_000,
                                             "opening_at": (datetime.now() - timedelta(days=3)).isoformat()}).json()
    rows = {r["id"]: r for r in client.get("/api/accounts/opening").json()}
    assert rows[box["id"]]["opening"] == 50_000_000 and rows[box["id"]]["balance"] == 50_000_000
    # correcting the opening balance replaces it (no double counting)
    assert client.put(f"/api/accounts/{box['id']}/opening", json={"amount": 42_000_000}).status_code == 200
    rows = {r["id"]: r for r in client.get("/api/accounts/opening").json()}
    assert rows[box["id"]]["opening"] == 42_000_000 and rows[box["id"]]["balance"] == 42_000_000
    # future date is refused
    r = client.put(f"/api/accounts/{box['id']}/opening", json={"amount": 1, "at": (datetime.now() + timedelta(days=2)).isoformat()})
    assert r.status_code == 400
    # cash count: 41.5M actually in the box -> 0.5M shortage booked, balance follows the count
    d = client.post(f"/api/accounts/{box['id']}/adjust", json={"actual": 41_500_000, "note": "شمارش"}).json()
    assert d["difference"] == -500_000
    rows = {r["id"]: r for r in client.get("/api/accounts/opening").json()}
    assert rows[box["id"]]["balance"] == 41_500_000 and rows[box["id"]]["opening"] == 42_000_000
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"]


def test_line_and_service_codes(client):
    lines = client.get("/api/lines").json()
    svcs = client.get("/api/services").json()
    assert all(l["code"] and l["code"].isdigit() for l in lines)
    assert all(s["code"] and s["code"].startswith(s["line_code"]) and len(s["code"]) == len(s["line_code"]) + 2 for s in svcs)
    assert len({s["code"] for s in svcs}) == len(svcs) and len({l["code"] for l in lines}) == len(lines)
    # a new line gets the next number, its services line-code + 01, 02 ...
    line = client.post("/api/lines", json={"name": "لاین کددار"}).json()
    assert int(line["code"]) == max(int(l["code"]) for l in lines) + 1
    a = client.post("/api/services", json={"line_id": line["id"], "name": "خدمت کددار یک", "base_price": 1}).json()
    b = client.post("/api/services", json={"line_id": line["id"], "name": "خدمت کددار دو", "base_price": 1}).json()
    assert a["code"] == f"{line['code']}01" and b["code"] == f"{line['code']}02"
    # codes stay when renaming; can be set by hand; duplicates refused
    r = client.put(f"/api/services/{a['id']}", json={"line_id": line["id"], "name": "نام تازه", "base_price": 1}).json()
    assert r["code"] == a["code"]
    assert client.put(f"/api/services/{a['id']}", json={"line_id": line["id"], "name": "x", "code": b["code"]}).status_code == 409
    assert client.put(f"/api/services/{a['id']}", json={"line_id": line["id"], "name": "x", "code": "۹۹۰۱"}).json()["code"] == "9901"
    # services created by the import also get codes
    rows = [["کد مشتری", "نام مشتری", "موبایل", "تاریخ", "خدمت"], ["660011", "کد خدمت انتقالی", "09120066011", "1404/01/10", "خدمت کاملاً تازه"]]
    p = client.post("/api/import/legacy/preview", files={"file": ("h.xlsx", _xlsx(rows), "application/octet-stream")}, data={"kind": "history"}).json()
    client.post(f"/api/import/legacy/{p['id']}/commit", json={"service_map": {"خدمت کاملاً تازه": "new"}})
    new = [s for s in client.get("/api/services").json() if s["name"] == "خدمت کاملاً تازه"][0]
    assert new["code"] and new["code"].startswith(new["line_code"])


def test_revenue_by_line_staff_service_with_previous_software(client, accounts):
    from datetime import datetime, timedelta

    from app.services.jalali import gregorian_to_jalali
    line = client.post("/api/lines", json={"name": "لاین گزارش درآمد"}).json()
    s1 = client.post("/api/services", json={"line_id": line["id"], "name": "خدمت گزارش یک", "base_price": 1_000_000}).json()
    s2 = client.post("/api/services", json={"line_id": line["id"], "name": "خدمت گزارش دو", "base_price": 2_000_000}).json()
    boss = client.post("/api/staff", json={"full_name": "مدیر لاین گزارش", "line_id": line["id"], "commission_percent": 40}).json()
    # new-system invoice (staff share is NOT deducted in this report)
    c = client.post("/api/customers", json={"full_name": "مشتری گزارش", "mobile": "09121239900"}).json()
    client.post("/api/invoices", json={"customer_id": c["id"], "items": [{"service_id": s1["id"], "unit_price": 1_000_000, "staff_id": boss["id"]}],
                                       "payments": [{"payment_account_id": accounts["کارتخوان ملت"], "amount": 1_000_000}]})
    # previous-software receipts two months ago, without staff (single-person line -> attributed to its manager)
    old = datetime.now() - timedelta(days=62)
    j = "%04d/%02d/%02d" % gregorian_to_jalali(old.year, old.month, old.day)
    rows = [["کد مشتری", "نام مشتری", "تاریخ", "خدمت", "مبلغ"], ["881100", "قدیمی گزارش", j, "خدمت گزارش دو", 3_000_000],
            ["881100", "قدیمی گزارش", j, "خدمت گزارش دو", 3_000_000]]
    p = client.post("/api/import/legacy/preview", files={"file": ("r.xlsx", _xlsx(rows), "application/octet-stream")},
                    data={"kind": "history", "unit": "rial"}).json()
    client.post(f"/api/import/legacy/{p['id']}/commit", json={})
    r = client.get("/api/reports/revenue", params={"line_id": line["id"]}).json()
    assert r["totals"]["revenue"] == 7_000_000 and r["totals"]["old"] == 6_000_000 and r["totals"]["new"] == 1_000_000
    assert [x["name"] for x in r["lines"]] == ["لاین گزارش درآمد"] and r["lines"][0]["revenue"] == 7_000_000
    st = {x["name"]: x for x in r["staff"]}
    assert st["مدیر لاین گزارش"]["revenue"] == 7_000_000 and st["مدیر لاین گزارش"]["inferred"] == 2
    sv = {x["name"]: x for x in r["services"]}
    assert sv["خدمت گزارش دو"]["revenue"] == 6_000_000 and sv["خدمت گزارش دو"]["count"] == 2 and sv["خدمت گزارش دو"]["code"] == s2["code"]
    assert len(r["months"]) >= 2 and sum(m["total"] for m in r["months"]) == 7_000_000
    assert client.get("/api/reports/revenue", params={"line_id": line["id"], "source": "old"}).json()["totals"]["revenue"] == 6_000_000
    d = client.get(f"/api/reports/revenue/staff/{boss['id']}").json()
    assert d["totals"]["revenue"] == 7_000_000 and d["records_total"] == 3
    assert {x["source"] for x in d["records"]} == {"old", "new"} and d["records"][0]["source"] == "new"
    # settings > services: which services were ever sold (here or in the previous software)
    s3 = client.post("/api/services", json={"line_id": line["id"], "name": "خدمت گزارش بی‌فروش"}).json()
    u = {x["service_id"]: x for x in client.get("/api/services/usage").json()}
    assert u[s1["id"]]["invoices"] == 1 and u[s1["id"]]["invoice_amount"] == 1_000_000 and u[s1["id"]]["old"] == 0
    assert u[s2["id"]]["invoices"] == 0 and u[s2["id"]]["old"] == 2 and u[s2["id"]]["old_amount"] == 6_000_000
    assert s3["id"] not in u


def test_void_mistake_cancels_on_original_date_and_day_details(client, accounts, services):
    from datetime import date, datetime, timedelta
    svc = services["مانیکور"]
    acc = accounts["کارتخوان ملت"]
    c = client.post("/api/customers", json={"full_name": "ابطال روز قبل", "mobile": "09125557700"}).json()
    yesterday = (datetime.now() - timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)
    inv = client.post("/api/invoices", json={"customer_id": c["id"], "items": [{"service_id": svc["id"], "unit_price": 2_000_000}],
                                             "apply_deposits": False, "issued_at": yesterday.isoformat(),
                                             "payments": [{"payment_account_id": acc, "amount": 2_000_000}]}).json()
    today_before = client.get("/api/dashboard/day").json()["money_total"]
    y = (date.today() - timedelta(days=1)).isoformat()
    y_before = client.get("/api/dashboard/day", params={"day": y}).json()["money_total"]
    # default void = the invoice was a mistake: its payment is cancelled on its own date, today is untouched
    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200
    assert client.get("/api/dashboard/day").json()["money_total"] == today_before
    assert client.get("/api/dashboard/day", params={"day": y}).json()["money_total"] == y_before - 2_000_000
    # day details lists invoices, money and customers
    d = client.get("/api/dashboard/day", params={"day": y}).json()
    assert any(r["kind"] == "void_cancel" for r in d["money"]) and any(i["number"] == inv["number"] for i in d["invoices"])


def test_archived_service_restore_merge_and_health(client, accounts):
    """A sold service that was deleted is archived: hidden from the list, still in the reports, code taken -
    it must be findable, restorable, mergeable, and its code conflict explained."""
    skin = client.post("/api/lines", json={"name": "لاین پوست بایگانی"}).json()
    carb = client.post("/api/services", json={"line_id": skin["id"], "name": "کربوکسی", "base_price": 2_000_000}).json()
    c = client.post("/api/customers", json={"full_name": "مشتری کربوکسی", "mobile": "09125550672"}).json()
    client.post("/api/invoices", json={"customer_id": c["id"], "items": [{"service_id": carb["id"], "unit_price": 2_000_000}],
                                       "payments": [{"payment_account_id": accounts["کارتخوان ملت"], "amount": 2_000_000}]})
    other = client.post("/api/services", json={"line_id": skin["id"], "name": "پاکسازی پوست", "base_price": 1}).json()
    assert client.delete(f"/api/services/{carb['id']}").json()["archived"] is True
    assert carb["id"] not in [s["id"] for s in client.get("/api/services").json()]
    hidden = [s for s in client.get("/api/services?all=1").json() if s["id"] == carb["id"]][0]
    assert hidden["is_active"] is False and hidden["code"] == carb["code"]
    # the report still has it
    rev = client.get("/api/reports/revenue").json()
    assert any(s["id"] == carb["id"] for s in rev["services"])
    # the health check names it; adding a service with its code says where the code is
    health = client.get("/api/services/health").json()
    archived = [i for i in health["issues"] if i["kind"] == "archived_used"][0]
    assert carb["id"] in [x["id"] for x in archived["items"]]
    r = client.post("/api/services", json={"line_id": skin["id"], "name": "کربوکسی جدید", "code": carb["code"]})
    assert r.status_code == 409 and "بایگانی" in r.json()["detail"] and "کربوکسی" in r.json()["detail"]
    # adding it again by name brings the same service back (no duplicate, same code and history)
    back = client.post("/api/services", json={"line_id": skin["id"], "name": "كربوكسي", "base_price": 2_500_000}).json()
    assert back["id"] == carb["id"] and back["restored"] is True and back["code"] == carb["code"]
    assert client.post("/api/services", json={"line_id": skin["id"], "name": "کربوکسی"}).status_code == 409
    # a typo of the old software (letters swapped) is reported as a duplicate and merged with all its records
    typo = client.post("/api/services", json={"line_id": skin["id"], "name": "کروبکسی", "base_price": 1}).json()
    client.post("/api/invoices", json={"customer_id": c["id"], "items": [{"service_id": typo["id"], "unit_price": 1_500_000}],
                                       "payments": [{"payment_account_id": accounts["کارتخوان ملت"], "amount": 1_500_000}]})
    groups = [i for i in client.get("/api/services/health").json()["issues"] if i["kind"] == "duplicates"][0]["groups"]
    assert any({carb["id"], typo["id"]} <= {x["id"] for x in g} for g in groups)
    assert not any(other["id"] in {x["id"] for x in g} for g in groups)
    m = client.post(f"/api/services/{typo['id']}/merge", json={"into_id": carb["id"]}).json()
    assert m["moved"]["invoice_items"] == 1 and "کروبکسی" in m["aliases"]
    assert typo["id"] not in [s["id"] for s in client.get("/api/services?all=1").json()]
    usage = client.get(f"/api/services/{carb['id']}/usage").json()
    assert usage["invoices"] == 2
    # restore after a plain delete
    client.delete(f"/api/services/{carb['id']}")
    assert client.post(f"/api/services/{carb['id']}/restore").json()["is_active"] is True
    # moved to another line: the automatic code follows the new line; a hand-made odd code is fixed on request
    hair = client.post("/api/lines", json={"name": "لاین مقصد کد"}).json()
    moved = client.put(f"/api/services/{other['id']}", json={**other, "line_id": hair["id"], "code": None}).json()
    assert moved["code"] == f"{hair['code']}01" and moved["old_code"] == other["code"]
    odd = client.put(f"/api/services/{other['id']}", json={**other, "line_id": hair["id"], "code": "98765"}).json()
    assert odd["code"] == "98765"
    assert other["id"] in [x["id"] for i in client.get("/api/services/health").json()["issues"] if i["kind"] == "code_mismatch" for x in i["items"]]
    fixed = client.post("/api/services/recode", json={"ids": [other["id"]]}).json()["changed"]
    assert fixed[0]["old"] == "98765" and fixed[0]["new"].startswith(hair["code"])


def test_service_codes_with_ten_or_more_lines():
    from app.services.codes import owner_line_code
    assert owner_line_code("1201", {"1", "12"}) == "12"
    assert owner_line_code("110", {"1", "11"}) == "1"
    assert owner_line_code("672", {"3", "6"}) == "6"


def test_import_suggests_lookalike_service_and_uses_file_line(client):
    line = client.post("/api/lines", json={"name": "پوست واردات"}).json()
    client.post("/api/services", json={"line_id": line["id"], "name": "میکرونیدلینگ", "base_price": 1}).json()
    rows = [["کد مشتری", "نام مشتری", "تاریخ", "لاین", "خدمت", "مبلغ"],
            ["770001", "واردات یک", "1404/01/10", "پوست واردات", "میکرونیدلنیگ", 1000],
            ["770002", "واردات دو", "1404/01/11", "پوست واردات", "مزوتراپی واردات", 1000]]
    p = client.post("/api/import/legacy/preview", files={"file": ("h.xlsx", _xlsx(rows), "application/octet-stream")}, data={"kind": "history"}).json()
    unk = {u["name"]: u for u in p["unknown_services"]}
    assert unk["میکرونیدلنیگ"]["similar"][0]["name"] == "میکرونیدلینگ"
    assert unk["مزوتراپی واردات"]["line_id"] == line["id"]
    client.post(f"/api/import/legacy/{p['id']}/commit", json={"service_map": {"میکرونیدلنیگ": unk["میکرونیدلنیگ"]["similar"][0]["id"],
                                                                              "مزوتراپی واردات": "new"}})
    new = [s for s in client.get("/api/services").json() if s["name"] == "مزوتراپی واردات"][0]
    assert new["line_id"] == line["id"] and new["code"].startswith(line["code"])


def test_refunded_deposit_cancels_its_appointment_and_easy_appointment_changes(client, accounts, services):
    """A deposit refunded -> its appointment is cancelled (the bug: it stayed booked). Appointments can be moved to
    another customer, cancelled with a choice for the deposit, brought back and deleted when registered by mistake."""
    svc = services["پدیکور"]
    slots = client.get(f"/api/appointments/suggest?service_id={svc['id']}&count=6").json()
    acc = accounts["کارتخوان ملت"]
    d = client.post("/api/deposits", json={"customer_name": "میلاد تهمتن", "customer_mobile": "09121110099", "amount": 500_000,
                                           "payment_account_id": acc, "service_id": svc["id"], "book_at": slots[0]["start_at"]}).json()
    r = client.post(f"/api/deposits/{d['id']}/close", json={"action": "refund"}).json()
    assert r["status"] == "refunded" and r["appointment_change"]["status"] == "cancelled"
    assert client.get(f"/api/appointments/{d['appointment_id']}").json()["status"] == "cancelled"
    # the appointment can be brought back (the deposit stays refunded) - it is then flagged on the dashboard
    client.patch(f"/api/appointments/{d['appointment_id']}?status=booked")
    listed = [a for a in client.get("/api/appointments?q=تهمتن").json() if a["id"] == d["appointment_id"]][0]
    assert listed["deposit_gone"] is True and listed["customer_code"]
    assert client.get("/api/dashboard").json()["agenda"]["todo"]["deposit_gone_appointments"] >= 1
    # keep: refund without touching the appointment
    d2 = client.post("/api/deposits", json={"customer_id": d["customer_id"], "amount": 300_000, "payment_account_id": acc,
                                            "service_id": svc["id"], "book_at": slots[1]["start_at"]}).json()
    assert client.post(f"/api/deposits/{d2['id']}/close", json={"action": "refund", "appointment": "keep"}).json()["appointment_change"] is None
    assert client.get(f"/api/appointments/{d2['appointment_id']}").json()["status"] == "booked"
    # cancelling an appointment: its open deposit stays with the customer (unlinked) or is refunded
    d3 = client.post("/api/deposits", json={"customer_id": d["customer_id"], "amount": 200_000, "payment_account_id": acc,
                                            "service_id": svc["id"], "book_at": slots[2]["start_at"]}).json()
    c = client.patch(f"/api/appointments/{d3['appointment_id']}?status=cancelled&deposits=keep").json()
    assert c["deposits"][0]["result"] == "keep"
    dep3 = client.get(f"/api/deposits/{d3['id']}").json()
    assert dep3["status"] == "held" and dep3["appointment"] is None
    # wrong customer chosen: move the appointment to the right one
    other = client.post("/api/customers", json={"full_name": "مشتری درست", "mobile": "09121110098"}).json()
    moved = client.put(f"/api/appointments/{d2['appointment_id']}", json={"customer_id": other["id"]}).json()
    assert moved["customer_id"] == other["id"] and moved["customer"] == "مشتری درست"
    # registered by mistake: delete; a deposit on it is kept as the customer's open deposit
    a4 = client.post("/api/appointments", json={"customer_id": d["customer_id"], "service_id": svc["id"], "start_at": slots[3]["start_at"],
                                                "deposit_ids": [d3["id"]]}).json()
    gone = client.delete(f"/api/appointments/{a4['id']}").json()
    assert gone["ok"] and gone["deposits_kept"] == 1
    assert client.get(f"/api/appointments/{a4['id']}").status_code == 404
    assert client.get(f"/api/deposits/{d3['id']}").json()["status"] == "held"
    # done appointments (history) are not deleted
    hist = client.post("/api/appointments", json={"customer_id": d["customer_id"], "service_id": svc["id"], "start_at": slots[4]["start_at"]}).json()
    client.patch(f"/api/appointments/{hist['id']}?status=done")
    assert client.delete(f"/api/appointments/{hist['id']}").status_code == 400


def test_refunded_deposit_is_money_out_on_the_dashboard(client, accounts, services):
    """A deposit received and then refunded is not money kept: per account the dashboard shows it in, back out,
    and a net of zero; the manager's list says who got it back, from which account and which user did it."""
    acc_name = "کارت پاسارگاد"

    def snap():
        d = client.get("/api/dashboard").json()
        t = d["today"]
        row = next((a for a in t["by_account"] if a["name"] == acc_name), {"in": 0, "out": 0, "value": 0})
        return t, row, d["money_returned"]

    t0, a0, r0 = snap()
    d = client.post("/api/deposits", json={"customer_name": "بیعانه برگشتی", "customer_mobile": "09125557788", "amount": 5_000_000,
                                           "payment_account_id": accounts[acc_name], "service_id": services["پدیکور"]["id"]}).json()
    client.post(f"/api/deposits/{d['id']}/close", json={"action": "refund"})
    t1, a1, r1 = snap()
    assert a1["in"] - a0["in"] == 5_000_000 and a1["out"] - a0["out"] == 5_000_000 and a1["value"] == a0["value"]
    assert t1["cash_in"] == t0["cash_in"] and t1["cash_paid_back"] - t0["cash_paid_back"] == 5_000_000
    item = next(x for x in r1["items"] if x["deposit_id"] == d["id"])
    assert item["kind"] == "deposit_refund" and item["amount"] == 5_000_000 and item["account"] == acc_name and item["by"]
    assert r1["paid_back"] - r0["paid_back"] == 5_000_000
    day = client.get("/api/dashboard/day").json()
    assert any(m["kind"] == "deposit_refund" and m["amount"] == -5_000_000 for m in day["money"])
    # deposits refunded before the refund date was recorded get it back from their journal entry
    from app.core.db import SessionLocal
    from app.models import Deposit
    from app.services.accounting import backfill_deposit_closures
    with SessionLocal() as db:
        dep = db.get(Deposit, d["id"])
        dep.closed_at, dep.refund_account_id = None, None
        db.commit()
        assert backfill_deposit_closures(db) >= 1
        db.commit()
        dep = db.get(Deposit, d["id"])
        assert dep.closed_at is not None and dep.refund_account_id == accounts[acc_name]


def test_same_name_customers_ask_and_merge_everything(client, accounts, services):
    """Same first and last name, different mobile: offered as a possible duplicate; merged on request with all
    invoices, deposits, appointments and balance; or remembered as different people."""
    acc = accounts["کارتخوان ملت"]
    a = client.post("/api/customers", json={"full_name": "سارا  محمدی", "mobile": "09121110001", "code": "77001"}).json()
    b = client.post("/api/customers", json={"full_name": "سارا محمدي", "mobile": "09121110002", "code": "77002"}).json()
    c = client.post("/api/customers", json={"full_name": "سارا محمدی", "mobile": "09121110003"}).json()
    # asked when adding: same name (ی/ي, extra spaces don't matter)
    same = client.get("/api/customers/same-name", params={"name": "سارا محمدی"}).json()
    assert {a["id"], b["id"], c["id"]} <= {x["id"] for x in same}
    # history on both records: an unpaid invoice on a, an open deposit and an appointment on b
    svc = services["پدیکور"]
    client.post("/api/invoices", json={"customer_id": a["id"], "items": [{"service_id": svc["id"], "unit_price": 2_000_000}],
                                       "payments": [{"payment_account_id": acc, "amount": 500_000}]})
    client.post("/api/deposits", json={"customer_id": b["id"], "amount": 700_000, "payment_account_id": acc, "service_id": svc["id"]})
    slots = client.get(f"/api/appointments/suggest?service_id={svc['id']}&count=1").json()
    client.post("/api/appointments", json={"customer_id": b["id"], "service_id": svc["id"], "start_at": slots[0]["start_at"]})
    group = next(g for g in client.get("/api/customers/duplicates").json() if a["id"] in [x["id"] for x in g["customers"]])
    assert {a["id"], b["id"], c["id"]} == {x["id"] for x in group["customers"]}
    # c is a different person
    client.post("/api/customers/not-same", json={"ids": [a["id"], c["id"]]})
    client.post("/api/customers/not-same", json={"ids": [b["id"], c["id"]]})
    before_a = client.get(f"/api/customers/{a['id']}").json()
    before_b = client.get(f"/api/customers/{b['id']}").json()
    r = client.post("/api/customers/merge", json={"keep_id": a["id"], "drop_ids": [b["id"]]}).json()
    assert r["other_mobiles"] == ["09121110002"] and r["other_codes"] == ["77002"]
    assert client.get(f"/api/customers/{b['id']}").status_code == 404
    after = client.get(f"/api/customers/{a['id']}").json()
    for k in ("receivable", "deposits_held", "net"):  # what they owe and their open deposits simply add up
        assert after["balance"][k] == before_a["balance"][k] + before_b["balance"][k], k
    assert after["balance"]["receivable"] == 1_500_000 and after["balance"]["deposits_held"] == 700_000
    assert len(after["invoices"]) == len(before_a["invoices"]) + len(before_b["invoices"])
    assert len(after["deposits"]) == len(before_a["deposits"]) + len(before_b["deposits"])
    # the merged record's number and code still find the person
    found = client.get("/api/customers", params={"q": "09121110002"}).json()["items"]
    assert [x["id"] for x in found] == [a["id"]]
    assert client.get("/api/customers", params={"q": "77002"}).json()["items"][0]["id"] == a["id"]
    assert client.post("/api/customers", json={"full_name": "کسی دیگر", "mobile": "09121110002"}).status_code == 409
    # the group is resolved: a+c were marked different
    assert not any(a["id"] in [x["id"] for x in g["customers"]] for g in client.get("/api/customers/duplicates").json())
    # the books still balance
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"] and tb["total_debit"] > 0


def test_each_line_deposits_and_products_have_their_pos_and_card(client):
    accs = client.get("/api/accounts").json()
    pos = [a["id"] for a in accs if a["kind"] == "pos"]
    card = [a["id"] for a in accs if a["kind"] in ("card", "bank", "cash")]
    assert pos and card
    r = client.get("/api/accounts/routing").json()
    assert r["problems"]  # nothing set yet
    lines = {str(l["id"]): {"pos": [pos[0]], "card": [card[0]]} for l in r["lines"]}
    # a line without a card is refused
    bad = {**lines, next(iter(lines)): {"pos": [pos[0]], "card": []}}
    res = client.put("/api/accounts/routing", json={"lines": bad, "deposits": {"pos": [pos[0]], "card": [card[0]]}, "products": {"pos": [pos[0]], "card": [card[0]]}})
    assert res.status_code == 400 and "کارت" in res.json()["detail"]
    # a POS id in the card list (wrong kind) is dropped, so it doesn't count as a card
    wrong = {"pos": [pos[0]], "card": [pos[0]]}
    assert client.put("/api/accounts/routing", json={"lines": lines, "deposits": wrong, "products": wrong}).status_code == 400
    many = {"pos": pos[::-1], "card": card}
    ok = client.put("/api/accounts/routing", json={"lines": lines, "deposits": many, "products": {"pos": [pos[0]], "card": [card[-1]]}}).json()
    assert ok.get("problems") == [], ok
    assert ok["deposits"]["pos"][0] == pos[-1] and ok["products"]["card"] == [card[-1]]
    from app.core.db import SessionLocal
    from app.services import routing
    with SessionLocal() as db:
        assert routing.default_account(db, "deposits") == pos[-1]
        assert routing.default_account(db, "products", prefer="card") == card[-1]
        assert routing.default_account(db, int(next(iter(lines)))) == pos[0]
