"""Plugins: customer receipts from chats, bank SMS, sales-book scan (group G1). Mobiles start with 0915101."""
import hashlib
import hmac
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.security import sign_payload
from app.main import app
from app.models import BankTransaction, Deposit, ImportBatch, InboundReceipt, Invoice, Payment, PluginState
from app.plugins.builtin.bank_sync import plugin as bank_plugin
from app.plugins.builtin.messaging_receipts import plugin as receipts_plugin
from app.plugins.builtin.sales_book_ocr import plugin as ocr_plugin
from app.plugins.manager import manager
from app.services.jalali import gregorian_to_jalali
from app.services.receipt_parser import parse_receipt

SECRET = "g1-meta-app-secret"
WA = "/api/plugins/messaging_receipts/webhook/whatsapp"
IG = "/api/plugins/messaging_receipts/webhook/instagram"
SIM = "/api/plugins/messaging_receipts/simulate"


def _sig(raw: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _wa(sender: str, text: str) -> bytes:
    return json.dumps({"entry": [{"changes": [{"value": {"messages": [{"from": sender, "text": {"body": text}}]}}]}]}).encode()


def _post_wa(sender: str, text: str):
    raw = _wa(sender, text)
    return TestClient(app).post(WA, content=raw, headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sig(raw)})


def _count(model, *where) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model).where(*where))


def _get(model, oid):
    with SessionLocal() as db:
        obj = db.get(model, oid)
        db.expunge(obj)
        return obj


def _receipt(sender: str) -> InboundReceipt:
    with SessionLocal() as db:
        r = db.scalar(select(InboundReceipt).where(InboundReceipt.sender == sender).order_by(InboundReceipt.id.desc()).limit(1))
        db.expunge(r)
        return r


def _balanced(client):
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"]


def _customer(client, name: str, mobile: str) -> dict:
    r = client.post("/api/customers", json={"full_name": name, "mobile": mobile})
    assert r.status_code == 200, r.text
    return r.json()


def _jalali(d: date) -> str:
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    return f"{jy}/{jm:02d}/{jd:02d}"


@pytest.fixture(scope="module")
def receipts_on(client):
    """The receipts plugin is off on a new install: turn it on with a Meta App Secret for these tests."""
    was = manager.enabled.get("messaging_receipts", False)
    old = dict(manager.plugins["messaging_receipts"].ctx.config)
    r = client.put("/api/plugins/messaging_receipts", json={"enabled": True, "config": {**old, "app_secret": SECRET}})
    assert r.status_code == 200, r.text
    yield
    client.put("/api/plugins/messaging_receipts", json={"enabled": was, "config": old})


# ---------------------------------------------------------------- plugin on/off
def test_receipts_plugin_is_off_on_a_new_install_and_a_saved_choice_is_kept(client):
    from app.plugins.base import BasePlugin
    from app.plugins.manager import PluginManager

    assert manager.plugins["messaging_receipts"].enabled_by_default is False
    assert manager.plugins["bank_sync"].enabled_by_default and manager.plugins["sales_book_ocr"].enabled_by_default

    class Off(BasePlugin):
        name, status, enabled_by_default = "g1_off_plugin", "beta", False

    class On(BasePlugin):
        name, status = "g1_on_plugin", "beta"

    try:
        m = PluginManager()
        m.plugins = {"g1_off_plugin": Off(), "g1_on_plugin": On()}
        m.sync_state()
        assert m.enabled == {"g1_off_plugin": False, "g1_on_plugin": True}
        m.set_enabled("g1_off_plugin", True)  # the owner turns it on: kept at the next start
        m2 = PluginManager()
        m2.plugins = m.plugins
        m2.sync_state()
        assert m2.enabled == {"g1_off_plugin": True, "g1_on_plugin": True}
        # the periodic jobs of a turned-off plugin do nothing
        m2.set_enabled("g1_on_plugin", False)
        ran = []
        m2._only_enabled("g1_on_plugin", lambda: ran.append("on"))()
        m2._only_enabled("g1_off_plugin", lambda: ran.append("off"))()
        assert ran == ["off"]
    finally:
        with SessionLocal() as db:
            for name in ("g1_off_plugin", "g1_on_plugin"):
                st = db.get(PluginState, name)
                if st is not None:
                    db.delete(st)
            db.commit()


# ---------------------------------------------------------------- webhooks
def test_whatsapp_and_instagram_webhooks_need_meta_signature(client, receipts_on, monkeypatch):
    anon = TestClient(app)
    raw = _wa("989151010001", "کارت به کارت موفق\nمبلغ: 7,700,010 ریال")
    ig = json.dumps({"entry": [{"messaging": [{"sender": {"id": "g1.ig.sig"}, "message": {"text": "واریز 7,700,010 ریال موفق"}}]}]}).encode()
    before = _count(InboundReceipt)
    # no App Secret in the settings -> nothing gets in, even with a signature
    monkeypatch.setitem(manager.plugins["messaging_receipts"].ctx.config, "app_secret", "")
    from app.plugins.builtin.messaging_receipts import plugin as mr

    monkeypatch.setattr(mr, "_no_secret_alert", [None])
    alerts = len(client.get("/api/alerts").json())
    assert anon.post(WA, content=raw, headers={"X-Hub-Signature-256": _sig(raw)}).status_code == 401
    assert anon.post(WA, content=raw, headers={"X-Hub-Signature-256": _sig(raw)}).status_code == 401
    # the owner is told once (not on every message) how to fix it
    new = client.get("/api/alerts").json()
    assert len(new) == alerts + 1 and any("App Secret" in (a.get("message") or "") for a in new)
    monkeypatch.undo()
    for url, body in ((WA, raw), (IG, ig)):
        assert anon.post(url, content=body, headers={"Content-Type": "text/plain"}).status_code == 401  # a web page / LAN post
        assert anon.post(url, content=body, headers={"X-Hub-Signature-256": _sig(body, "wrong")}).status_code == 401
        assert anon.post(url, content=body + b" ", headers={"X-Hub-Signature-256": _sig(body)}).status_code == 401  # body changed
    assert _count(InboundReceipt) == before
    # Meta's signed delivery is accepted
    r = anon.post(WA, content=raw, headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sig(raw)})
    assert r.status_code == 200 and r.json() == {"handled": 1}
    assert _count(InboundReceipt) == before + 1


def test_chat_receipt_on_amount_and_time_alone_does_not_take_the_money(client, accounts, receipts_on):
    """The audit's attack: a real payer's bank credit sits unmatched; someone sends a receipt for the same amount."""
    card = accounts["کارت بانک ملی (مدیر)"]
    bank = client.post("/api/plugins/bank_sync/bank-sms", json={"text": "واریز: +7,777,010\nپیگیری: 51010001", "payment_account_id": card}).json()
    assert bank["ok"] and bank["status"] == "unmatched"
    attacker = _customer(client, "G1 فرستنده جعلی", "09151010002")
    assert _post_wa("989151010002", "کارت به کارت موفق\nمبلغ: 7,777,010 ریال").status_code == 200
    fake = _receipt("09151010002")
    assert fake.status == "matched" and fake.deposit_id is None and fake.bank_transaction_id == bank["transaction_id"]
    assert "ثبت" in fake.note
    assert client.get(f"/api/customers/{attacker['id']}").json()["balance"]["deposits_held"] == 0
    tx = _get(BankTransaction, bank["transaction_id"])
    assert (tx.status, tx.matched_type, tx.matched_id) == ("matched", "receipt", fake.id)

    # the real payer's receipt carries the bank's tracking number: it gets the money and its deposit, exactly
    payer = _customer(client, "G1 پرداخت کننده واقعی", "09151010003")
    assert _post_wa("989151010003", "کارت به کارت موفق\nمبلغ: 7,777,010 ریال\nشماره پیگیری: 51010001").status_code == 200
    real = _receipt("09151010003")
    assert real.status == "registered" and real.deposit_id
    dep = _get(Deposit, real.deposit_id)
    assert (dep.customer_id, dep.amount, dep.status, dep.payment_account_id) == (payer["id"], 7_777_010, "held", card)
    tx = _get(BankTransaction, bank["transaction_id"])
    assert (tx.status, tx.matched_type, tx.matched_id) == ("matched", "deposit", dep.id)
    fake = _get(InboundReceipt, fake.id)
    assert fake.status == "pending" and fake.bank_transaction_id is None and fake.deposit_id is None
    assert client.get(f"/api/customers/{payer['id']}").json()["balance"]["deposits_held"] == 7_777_010
    assert client.get(f"/api/customers/{attacker['id']}").json()["balance"]["deposits_held"] == 0
    _balanced(client)


def test_planted_receipt_waits_for_a_person_and_reject_frees_the_money(client, accounts, receipts_on):
    card = accounts["کارت بانک ملی (مدیر)"]
    _customer(client, "G1 کاشت رسید", "09151010004")
    assert _post_wa("989151010004", "کارت به کارت موفق\nمبلغ: 6,666,010 ریال").status_code == 200
    planted = _receipt("09151010004")
    assert planted.status == "pending"
    # the next real bank credit of that amount is held for it, but no deposit is booked
    bank = client.post("/api/plugins/bank_sync/bank-sms", json={"text": "واریز: +6,666,010\nپیگیری: 51010004", "payment_account_id": card}).json()
    planted = _get(InboundReceipt, planted.id)
    assert planted.status == "matched" and planted.deposit_id is None and planted.bank_transaction_id == bank["transaction_id"]
    assert _count(Deposit, Deposit.amount == 6_666_010) == 0
    # the owner rejects it: the bank money is free again
    assert client.post(f"/api/plugins/messaging_receipts/receipts/{planted.id}/reject").json() == {"ok": True}
    tx = _get(BankTransaction, bank["transaction_id"])
    assert (tx.status, tx.matched_type, tx.matched_id) == ("unmatched", None, None)
    assert _get(InboundReceipt, planted.id).bank_transaction_id is None

    # a known customer's receipt without tracking number: matched, and «ثبت» by a person books it (exact amount)
    known = _customer(client, "G1 تایید دستی", "09151010014")
    r = client.post(SIM, json={"channel": "sms", "sender": "09151010014", "text": "رسید کارت به کارت موفق مبلغ: 6,666,010 ریال"}).json()
    assert r["receipt_status"] == "matched"
    a = client.post(f"/api/plugins/messaging_receipts/receipts/{r['receipt_id']}/assign", json={}).json()
    assert a["status"] == "registered"
    dep = _get(Deposit, a["deposit_id"])
    assert (dep.customer_id, dep.amount, dep.status) == (known["id"], 6_666_010, "held")
    tx = _get(BankTransaction, bank["transaction_id"])
    assert (tx.status, tx.matched_type, tx.matched_id) == ("matched", "deposit", dep.id)
    # a registered receipt cannot be rejected (its deposit is refunded from the deposits page instead)
    assert client.post(f"/api/plugins/messaging_receipts/receipts/{r['receipt_id']}/reject").status_code == 400
    _balanced(client)


def test_bank_sms_before_the_new_customer_gives_a_name(client, accounts, receipts_on):
    card = accounts["کارت بانک ملی (مدیر)"]
    bank = client.post("/api/plugins/bank_sync/bank-sms", json={"text": "واریز: +3,300,010 پیگیری: 51010005", "payment_account_id": card}).json()
    r = client.post(SIM, json={"channel": "whatsapp", "sender": "09151010005",
                               "text": "کارت به کارت موفق مبلغ: 3,300,010 ریال پیگیری: 51010005"}).json()
    assert r["receipt_status"] == "matched" and r["customer_id"] is None and "نام" in r["replies"][0]
    r2 = client.post(SIM, json={"channel": "whatsapp", "sender": "09151010005", "text": "سارا گروه یک"}).json()
    assert r2["receipt_status"] == "registered" and r2["customer_id"]
    rec = _get(InboundReceipt, r["receipt_id"])
    dep = _get(Deposit, rec.deposit_id)
    assert (dep.customer_id, dep.amount, dep.status) == (r2["customer_id"], 3_300_010, "held")
    tx = _get(BankTransaction, bank["transaction_id"])
    assert (tx.status, tx.matched_type, tx.matched_id) == ("matched", "deposit", dep.id)
    assert client.get(f"/api/customers/{r2['customer_id']}").json()["mobile"] == "09151010005"
    _balanced(client)


def test_same_receipt_twice_is_one_receipt(client, receipts_on):
    text = "رسید کارت به کارت موفق مبلغ 1,230,010 ریال"
    a = client.post(SIM, json={"channel": "sms", "sender": "09151010012", "text": text}).json()
    b = client.post(SIM, json={"channel": "sms", "sender": "09151010012", "text": text}).json()
    assert a["receipt_id"] == b["receipt_id"]
    assert _count(InboundReceipt, InboundReceipt.sender == "09151010012") == 1


def test_instagram_sender_cannot_rename_an_existing_customer(client, receipts_on):
    c = _customer(client, "G1 نام واقعی", "09151010013")
    client.post(SIM, json={"channel": "instagram", "sender": "g1.someone", "text": "رسید کارت به کارت موفق مبلغ 1,240,010 ریال"})
    r = client.post(SIM, json={"channel": "instagram", "sender": "g1.someone", "text": "هکر 09151010013"}).json()
    # a mobile typed in the chat is no proof: the Instagram account is NOT tied to the customer who owns that number
    assert r["customer_id"] != c["id"]
    owner = client.get(f"/api/customers/{c['id']}").json()
    assert owner["full_name"] == "G1 نام واقعی" and not owner.get("instagram")
    other = client.get(f"/api/customers/{r['customer_id']}").json()
    assert other["full_name"] == "هکر" and other["instagram"] == "g1.someone" and "09151010013" in other["notes"]
    assert _get(InboundReceipt, r["receipt_id"]).customer_id == r["customer_id"]
    # «ثبت» on a receipt whose sender is known needs no name again (no empty new customer is made)
    n = _count(InboundReceipt)
    assert n
    before = client.get("/api/customers").json()["total"]
    client.post(f"/api/plugins/messaging_receipts/receipts/{r['receipt_id']}/assign", json={})
    assert client.get("/api/customers").json()["total"] == before
    assert _get(InboundReceipt, r["receipt_id"]).customer_id == r["customer_id"]


def test_receipt_file_name_never_comes_from_the_sender(client, receipts_on):
    import base64

    img = base64.b64encode(b"not really a picture").decode()
    bad = "image/x\\..\\..\\..\\..\\start.bat"
    for image_type, ext in ((bad, "jpg"), ("image/png", "png"), ("application/pdf", "pdf"), ("image/jpg", "jpg")):
        r = client.post(SIM, json={"channel": "sms", "sender": "09151010006", "text": "", "image_base64": img, "image_type": image_type})
        assert r.status_code == 200, r.text
        p = Path(_get(InboundReceipt, r.json()["receipt_id"]).image_path)
        assert p.name == f"{r.json()['receipt_id']}.{ext}" and p.parent.name == "receipts"
        assert "\\" not in str(p) and ".." not in str(p)
    assert client.post(SIM, json={"channel": "sms", "sender": "09151010006", "image_base64": "abc"}).status_code == 400


class _Resp:
    def __init__(self, chunks, status=200, ctype="image/png"):
        self.chunks, self.status_code, self.headers = chunks, status, {"content-type": ctype}

    def iter_bytes(self):
        yield from self.chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_instagram_pictures_only_from_meta_servers(client, receipts_on, monkeypatch):
    calls = []
    resp = {"r": _Resp([b"x" * 10])}

    def fake_stream(method, url, **kw):
        calls.append((str(url), kw))
        return resp["r"]

    monkeypatch.setattr(receipts_plugin.httpx, "stream", fake_stream)
    for url in ("http://127.0.0.1:8000/api/backups", "https://127.0.0.1/x", "https://192.168.1.1/x", "https://evil.example/x.jpg",
                "https://scontent.fbcdn.net.evil.example/x", "http://scontent.xx.fbcdn.net/x.jpg",
                "https://scontent.xx.fbcdn.net:8443/x.jpg", "file:///etc/passwd", "not a url", ""):
        assert receipts_plugin.fetch_media(url) is None, url
    assert calls == []
    ok = "https://scontent.xx.fbcdn.net/v/t1/receipt.jpg"
    assert receipts_plugin.fetch_media(ok) == (b"x" * 10, "image/png")
    assert calls[-1][1]["follow_redirects"] is False
    assert receipts_plugin.fetch_media("https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=1")
    monkeypatch.setattr(receipts_plugin, "MAX_MEDIA", 5)  # too big -> dropped
    assert receipts_plugin.fetch_media(ok) is None
    monkeypatch.setattr(receipts_plugin, "MAX_MEDIA", 8 * 1024 * 1024)
    resp["r"] = _Resp([b"x"], status=302)
    assert receipts_plugin.fetch_media(ok) is None

    # through the signed Instagram webhook: an address on the clinic's network is never fetched
    calls.clear()
    body = json.dumps({"entry": [{"messaging": [
        {"sender": {"id": "g1.ig.pic"}, "message": {"attachments": [{"type": "image", "payload": {"url": "http://127.0.0.1:9/admin"}}]}},
        {"sender": {"id": "g1.clinic"}, "message": {"is_echo": True, "text": "بیعانه 100,000 تومانی شما با موفقیت ثبت شد"}},
    ]}]}).encode()
    before = _count(InboundReceipt)
    r = TestClient(app).post(IG, content=body, headers={"X-Hub-Signature-256": _sig(body)})
    assert r.status_code == 200 and r.json() == {"handled": 1}  # the clinic's own echoed reply is skipped
    assert calls == [] and _count(InboundReceipt) == before


# ---------------------------------------------------------------- bank SMS
def test_bank_sms_sent_again_is_not_new_money(client, monkeypatch):
    anon = TestClient(app)
    body = json.dumps({"text": "واریز 2,000,010 ریال"}).encode()  # no date, no tracking number
    h = {"X-Hesabdar-Signature": sign_payload(body, "hook-secret"), "Content-Type": "application/json"}
    url = "/api/plugins/bank_sync/webhook/bank-sms"
    r1 = anon.post(url, content=body, headers=h).json()
    assert r1["ok"] and r1["created"] is True

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(minutes=7)

    monkeypatch.setattr(bank_plugin, "datetime", Later)  # the same request again, minutes later
    r2 = anon.post(url, content=body, headers=h).json()
    assert r2["created"] is False and r2["transaction_id"] == r1["transaction_id"]
    assert _count(BankTransaction, BankTransaction.amount == 2_000_010) == 1


# ---------------------------------------------------------------- receipt parser
@pytest.mark.parametrize("text,amount", [
    ("کارت به کارت\nمبلغ ۱۵۰٬۰۰۰ تومان", 1_500_000),
    ("مبلغ: ۱٬۵۰۰٬۰۰۰ ریال", 1_500_000),
    ("مبلغ ۱،۵۰۰،۰۰۰ ریال", 1_500_000),
    ("واریز 1,500,000 1403/05/12", 1_500_000),
    ("مبلغ 1,500,000 12:30", 1_500_000),
    ("برداشت 1403/05/12 مبلغ 500,000 ریال", 500_000),
    ("واریز: +3,000,000\nپیگیری: 555666", 3_000_000),
    ("مبلغ: 3.000.000 ریال", 3_000_000),
    ("مبلغ 1500000ریال", 1_500_000),
])
def test_receipt_parser_amounts(text, amount):
    assert parse_receipt(text)["amount"] == amount


# ---------------------------------------------------------------- sales-book scan
def test_scan_routes_only_touch_sales_book_batches(client):
    with SessionLocal() as db:
        b = ImportBatch(kind="tp_customers", file_name="g1-owner-only.xlsx", status="review",
                        rows=[{"row": 1, "credit": 100}], summary={"rows": 1})
        db.add(b)
        db.commit()
        bid = b.id
    try:
        base = "/api/plugins/sales_book_ocr/batches"
        assert bid not in [x["id"] for x in client.get(base).json()]
        assert client.get(f"{base}/{bid}").status_code == 404
        assert client.put(f"{base}/{bid}/rows", json={"rows": [{"row": 1, "credit": 900_000_000}]}).status_code == 404
        assert client.post(f"{base}/{bid}/commit").status_code == 404
        assert client.post(f"{base}/{bid}/discard").status_code == 404
        ai = client.post("/api/ai/tools/sales_book_batches_pending", json={"arguments": {}}).json()
        assert bid not in [x["id"] for x in ai]
        b = _get(ImportBatch, bid)
        assert b.status == "review" and b.rows == [{"row": 1, "credit": 100}]
    finally:
        with SessionLocal() as db:
            db.delete(db.get(ImportBatch, bid))
            db.commit()


def _upload(client, text: str) -> dict:
    r = client.post("/api/plugins/sales_book_ocr/upload", files={"file": ("g1-book.txt", text.encode(), "text/plain")},
                    data={"currency": "toman"})
    assert r.status_code == 200, r.text
    return r.json()


def test_scan_reads_amounts_with_thousands_commas(client):
    day = _jalali(date.today() - timedelta(days=2))
    b = _upload(client, f"{day}\nپریا اسکنی | 09151010015 | رنگ مو | 1,800,000 | کارت\nلیلا اسکنی, 09151010016, کوتاهی مو, ۲۷۰٬۰۰۰, نقد\n"
                        "جمع: 2,070,000")
    assert [r["total"] for r in b["rows"]] == [18_000_000, 2_700_000]  # the book's amounts (toman x 10), not learned prices
    assert [r["items"][0]["unit_price"] for r in b["rows"]] == [18_000_000, 2_700_000]
    assert b["summary"]["total"] == 20_700_000 and "page_total_mismatch" not in b["summary"]
    assert client.post(f"/api/plugins/sales_book_ocr/batches/{b['id']}/discard").json() == {"ok": True}


def test_scan_commit_takes_no_deposit_and_records_what_the_book_says(client, accounts):
    dep = client.post("/api/deposits", json={"customer_name": "نسرین اسکنی", "customer_mobile": "09151010009", "amount": 2_000_000,
                                             "payment_account_id": accounts["کارت پاسارگاد"]}).json()
    cid = dep["customer_id"]
    day = _jalali(date.today() - timedelta(days=3))
    b = _upload(client, f"{day}\nنسرین اسکنی | 09151010009 | کوتاهی مو | 300,000 | کارتخوان")
    row = b["rows"][0]
    assert row["customer_id"] == cid and row["total"] == 3_000_000 and row["payment_account_id"]
    c = client.post(f"/api/plugins/sales_book_ocr/batches/{b['id']}/commit").json()
    assert len(c["invoices"]) == 1 and c["left_open"] == []
    with SessionLocal() as db:
        inv = db.scalar(select(Invoice).where(Invoice.number == c["invoices"][0]))
        assert (inv.total, inv.paid, inv.status) == (3_000_000, 3_000_000, "paid")
        pays = [(p.payment_account_id, p.amount) for p in db.scalars(select(Payment).where(Payment.invoice_id == inv.id))]
        assert pays == [(row["payment_account_id"], 3_000_000)]  # the whole sale on the POS, as the book says
        d = db.get(Deposit, dep["id"])
        assert (d.status, d.amount, d.applied_invoice_id) == ("held", 2_000_000, None)  # the deposit is left alone
    assert client.get(f"/api/customers/{cid}").json()["balance"] == {"receivable": 0, "deposits_held": 2_000_000, "net": 2_000_000}

    # a deposit written in the book is shown as a warning, is not taken from the customer's deposits, and stays owed
    b2 = _upload(client, f"{day}\nنسرین اسکنی | 09151010009 | کوتاهی مو | 350,000 | کارتخوان")
    rows = b2["rows"]
    rows[0]["deposit"] = 500_000
    client.put(f"/api/plugins/sales_book_ocr/batches/{b2['id']}/rows", json={"rows": rows})
    c2 = client.post(f"/api/plugins/sales_book_ocr/batches/{b2['id']}/commit").json()
    with SessionLocal() as db:
        inv = db.scalar(select(Invoice).where(Invoice.number == c2["invoices"][0]))
        assert (inv.total, inv.paid, inv.status) == (3_500_000, 3_000_000, "partial")
        assert [p.amount for p in db.scalars(select(Payment).where(Payment.invoice_id == inv.id))] == [3_000_000]
        assert "بیعانه" in inv.notes
        assert db.get(Deposit, dep["id"]).status == "held"
    assert c2["left_open"] == [{"invoice": c2["invoices"][0], "amount": 500_000}]
    assert client.get(f"/api/customers/{cid}").json()["balance"] == {"receivable": 500_000, "deposits_held": 2_000_000, "net": 1_500_000}
    _balanced(client)


def test_scan_of_today_works_before_noon_and_future_rows_are_refused(client, monkeypatch):
    real = datetime.now().replace(microsecond=0)
    fake_now = min(real, real.replace(hour=9, minute=0, second=0))  # 09:00 (or earlier) today
    monkeypatch.setattr(ocr_plugin, "local_now", lambda: fake_now)
    b = _upload(client, f"{_jalali(date.today())}\nمهتاب اسکنی | 09151010010 | کوتاهی مو | 200,000 | کارتخوان")
    c = client.post(f"/api/plugins/sales_book_ocr/batches/{b['id']}/commit")
    assert c.status_code == 200, c.text
    with SessionLocal() as db:
        inv = db.scalar(select(Invoice).where(Invoice.number == c.json()["invoices"][0]))
        assert inv.issued_at == fake_now and inv.paid == 2_000_000  # not 12:00 (that would be in the future)
    monkeypatch.undo()

    before = _count(Invoice)
    b = _upload(client, f"{_jalali(date.today() + timedelta(days=1))}\nفردا اسکنی | 09151010011 | کوتاهی مو | 200,000 | کارتخوان")
    assert any("آینده" in w for w in b["rows"][0]["warnings"])
    r = client.post(f"/api/plugins/sales_book_ocr/batches/{b['id']}/commit")
    assert r.status_code == 400 and "آینده" in r.json()["detail"]
    assert _count(Invoice) == before and _get(ImportBatch, b["id"]).status == "review"
    # discard works once, only while the page waits for review
    assert client.post(f"/api/plugins/sales_book_ocr/batches/{b['id']}/discard").json() == {"ok": True}
    assert client.post(f"/api/plugins/sales_book_ocr/batches/{b['id']}/discard").status_code == 400


# ---------------------------------------------------------------- review follow-ups
def _login_as(client, name: str, role: str) -> TestClient:
    r = client.post("/api/auth/users", json={"username": name, "password": "Passw0rd1", "role": role})
    assert r.status_code == 200, r.text
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": name, "password": "Passw0rd1"}).json()["access_token"]
    return c


def test_plugin_read_tools_are_reports_tools_the_ai_agent_keeps(client):
    from app.ai.tools import READ_ONLY, all_tools

    perms = {t.name: t.permission for t in all_tools()}
    assert perms["unmatched_bank_transactions"] == "reports" and perms["sales_book_batches_pending"] == "reports"
    assert "finance" not in READ_ONLY  # a 'finance' tool would move money: the chat may only propose it
    ai = _login_as(client, "g1_ai_agent", "ai_agent")
    names = {t["name"] for t in ai.get("/api/ai/tools").json()}
    assert {"unmatched_bank_transactions", "sales_book_batches_pending"} <= names
    for tool in ("unmatched_bank_transactions", "sales_book_batches_pending"):
        r = ai.post(f"/api/ai/tools/{tool}", json={"arguments": {}})
        assert r.status_code == 200 and isinstance(r.json(), list), r.text
    assert len(ai.post("/api/ai/tools/unmatched_bank_transactions", json={"arguments": {"limit": 10**9}}).json()) <= 200
    rec = _login_as(client, "g1_reception", "receptionist")  # money lists stay with the owner, manager and accountant
    assert rec.post("/api/ai/tools/unmatched_bank_transactions", json={"arguments": {}}).status_code == 403


def test_scan_upload_runs_off_the_event_loop_and_refuses_huge_files(client, monkeypatch):
    import inspect

    route = next(r for r in manager.plugins["sales_book_ocr"].router.routes if getattr(r, "path", "") == "/upload")
    assert not inspect.iscoroutinefunction(route.endpoint)
    monkeypatch.setattr(ocr_plugin, "MAX_BYTES", 200)
    before = _count(ImportBatch)
    big = ("x" * 201).encode()
    r = client.post("/api/plugins/sales_book_ocr/upload", files={"file": ("g1-big.txt", big, "text/plain")}, data={"currency": "toman"})
    assert r.status_code == 400 and "مگابایت" in r.json()["detail"]
    assert _count(ImportBatch) == before
    ok = _upload(client, "پگاه اسکنی | 09151010017 | کوتاهی مو | 200,000 | کارتخوان")  # a page under the limit still works
    assert ok["rows"][0]["total"] == 2_000_000
    client.post(f"/api/plugins/sales_book_ocr/batches/{ok['id']}/discard")


def test_reject_is_recorded_and_sms_webhook_and_secret_with_spaces_work(client, receipts_on):
    from app.models import AuditLog

    # the SMS forwarder's webhook (signed) still books the receipt, now off the event loop
    body = json.dumps({"sender": "09151010018", "text": "رسید کارت به کارت موفق مبلغ 4,440,010 ریال"}).encode()
    h = {"X-Hesabdar-Signature": sign_payload(body, "hook-secret"), "Content-Type": "application/json"}
    r = TestClient(app).post("/api/plugins/messaging_receipts/webhook/sms", content=body, headers=h)
    assert r.status_code == 200 and r.json()["receipt_id"], r.text
    rid = r.json()["receipt_id"]
    assert client.post(f"/api/plugins/messaging_receipts/receipts/{rid}/reject").json() == {"ok": True}
    with SessionLocal() as db:
        row = db.scalar(select(AuditLog).where(AuditLog.action == "receipt.reject", AuditLog.entity_id == str(rid)))
        assert row is not None and row.actor == "owner" and row.data["amount"] == 4_440_010
    assert _get(InboundReceipt, rid).status == "rejected"

    # an App Secret pasted with a space or a new line still checks Meta's signature
    cfg = dict(manager.plugins["messaging_receipts"].ctx.config)
    try:
        assert client.put("/api/plugins/messaging_receipts", json={"config": {**cfg, "app_secret": f"  {SECRET}\n"}}).status_code == 200
        raw = _wa("989151010019", "سلام")
        ok = TestClient(app).post(WA, content=raw, headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sig(raw)})
        assert ok.status_code == 200 and ok.json() == {"handled": 1}
    finally:
        client.put("/api/plugins/messaging_receipts", json={"config": {**cfg, "app_secret": SECRET}})
