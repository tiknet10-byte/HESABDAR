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
