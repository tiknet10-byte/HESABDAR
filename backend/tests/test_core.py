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
    s30 = client.get(f"/api/appointments/suggest?service_id={svc['id']}&duration=30&count=2").json()
    from datetime import datetime
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
