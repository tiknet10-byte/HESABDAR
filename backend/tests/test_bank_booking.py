"""1.23: bank lines that are not a customer's money. A staff member's card entered once books money sent to it as
their payout by itself (and can be taken back); an entry made by hand is linked, never booked twice, in both orders;
the inbox books the rest with one tap and can remember the card; an outgoing card-to-card SMS is never taken for money
in; the same money from two sources is one line; no whole card number is kept; the bank token is encrypted."""
import io
import random
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.services.jalali import gregorian_to_jalali
from tests.test_staffpay import _db, setup, user_client

BASE = "/api/plugins/bank_sync"


def card_number(seed: int) -> str:
    """A 16-digit card number that passes the check digit."""
    rnd = random.Random(seed)
    body = "621986" + "".join(str(rnd.randint(0, 9)) for _ in range(9))
    for last in range(10):
        n = body + str(last)
        total = 0
        for i, ch in enumerate(reversed(n)):
            d = int(ch) * (2 if i % 2 else 1)
            total += d - 9 if d > 9 else d
        if total % 10 == 0:
            return n
    raise AssertionError


def jdate(d: datetime) -> str:
    return "%04d/%02d/%02d" % gregorian_to_jalali(d.year, d.month, d.day)


def statement(client, account_id: int, rows: list[tuple]) -> dict:  # noqa: ANN001
    """rows: (datetime, withdraw, deposit, description, reference)"""
    lines = ["تاریخ,ساعت,برداشت,واریز,شرح,شماره پیگیری"]
    for at, out, inn, desc, ref in rows:
        lines.append(f"{jdate(at)},{at:%H:%M},{out or ''},{inn or ''},{desc},{ref or ''}")
    r = client.post(f"{BASE}/import-statement", data={"payment_account_id": str(account_id)},
                    files={"file": ("s.csv", io.BytesIO("\n".join(lines).encode()), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def tx_by_ref(ref: str):  # noqa: ANN201
    from app.models import BankTransaction

    with _db() as db:
        return db.scalar(select(BankTransaction).where(BankTransaction.reference == ref))


def bank_account(client, name: str, card: str) -> int:  # noqa: ANN001
    r = client.post("/api/accounts", json={"kind": "card", "name": name, "bank_name": "سامان", "card_number": card})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_a_staff_card_books_their_payouts_by_itself_and_can_be_taken_back(client, accounts):
    line, svc, person = setup(client, "کارت بانک", percent=40)
    acc = bank_account(client, "کارت سالن بانک خودکار", card_number(1))
    their = card_number(2)
    assert client.post(f"/api/staff/{person['id']}/pay-cards", json={"number": their[:-1] + str((int(their[-1]) + 1) % 10)}).status_code == 400
    r = client.post(f"/api/staff/{person['id']}/pay-cards", json={"number": " ".join(their[i:i + 4] for i in range(0, 16, 4))})
    assert r.status_code == 200, r.text
    assert r.json()["mask"].endswith(their[-4:]) and their not in r.json()["mask"]
    other = client.post("/api/staff", json={"full_name": "دیگری کارت بانک", "commission_percent": 10}).json()
    assert client.post(f"/api/staff/{other['id']}/pay-cards", json={"number": their}).status_code == 400  # someone else's card
    acc_user = user_client(client, "acc_bank_card", "accountant")
    assert acc_user.post(f"/api/staff/{person['id']}/pay-cards", json={"number": card_number(3)}).status_code == 403

    at = datetime.now() - timedelta(hours=3)
    out = statement(client, acc, [(at, 3_500_000, None, f"انتقال کارت به کارت به {their} بنام پرسنل پرداخت کارت بانک", "880001")])
    assert out["imported"] == 1
    tx = tx_by_ref("880001")
    assert tx.status == "matched" and tx.matched_type == "staff_payout" and tx.auto_booked
    assert their not in tx.description and tx.counterparty_mask.endswith(their[-4:])
    from app.models import BankTransaction, StaffPayout

    with _db() as db:
        p = db.get(StaffPayout, tx.matched_id)
        assert (p.staff_id, p.amount, p.source, p.payment_account_id, p.reference, p.bank_transaction_id) == (
            person["id"], 3_500_000, "bank", acc, "880001", tx.id)
        assert p.kind in ("advance", "settlement")
        # no whole card number anywhere in the bank lines
        assert not db.scalar(select(func.count(BankTransaction.id)).where(
            (BankTransaction.description.contains(their)) | (BankTransaction.counterparty_card.contains(their))))
    assert client.get(f"/api/staff/{person['id']}/statement").json()["balance_now"] == -3_500_000

    # the same statement downloaded again (one more row on top): nothing twice
    later = at + timedelta(hours=1)
    again = statement(client, acc, [(later, None, 900_000, "واریز", "880002"),
                                    (at, 3_500_000, None, f"انتقال کارت به کارت به {their} بنام پرسنل پرداخت کارت بانک", "880001")])
    assert again["imported"] == 1
    # the payout entered again by hand (from the WhatsApp receipt): pointed out, not booked twice
    dup = client.post(f"/api/staff/{person['id']}/payout", json={"amount": 3_500_000, "payment_account_id": acc, "kind": "advance"})
    assert dup.status_code == 409 and "بانک" in dup.json()["detail"]

    # taken back (by an accountant: it was booked by itself): the payout is voided, the line waits, the card asks first
    booked = acc_user.get(f"{BASE}/booked").json()
    assert any(x["id"] == tx.id and x["auto_booked"] and x["can_undo"] for x in booked)
    r = acc_user.post(f"{BASE}/transactions/{tx.id}/undo", json={})
    assert r.status_code == 200, r.text
    with _db() as db:
        assert db.get(StaffPayout, p.id).status == "void"
        assert db.get(BankTransaction, tx.id).status == "unmatched"
    cards = client.get(f"/api/staff/{person['id']}/pay-cards").json()
    assert cards[0]["auto"] is False and cards[0]["undos"] == 1
    assert client.get(f"/api/staff/{person['id']}/statement").json()["balance_now"] == 0
    # in the inbox now, with the person suggested (their card)
    inbox = client.get(f"{BASE}/inbox").json()["rows"]
    row = next(x for x in inbox if x["id"] == tx.id)
    sug = next(s for s in row["suggestions"] if s.get("staff_id") == person["id"])
    r = client.post(f"{BASE}/transactions/{tx.id}/book", json={"target": "staff", "staff_id": person["id"], "kind": "settlement",
                                                              "jy": sug["jy"], "jm": sug["jm"], "remember": True, "auto": True})
    assert r.status_code == 200 and r.json()["rule_auto"], r.text
    assert client.get(f"/api/staff/{person['id']}/pay-cards").json()[0]["auto"] is True
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"]


def test_an_entry_made_by_hand_is_linked_in_both_orders(client, accounts):
    line, svc, person = setup(client, "پیوند بانک", percent=40)
    acc = bank_account(client, "کارت سالن پیوند", card_number(11))
    # the payout first, then the bank line (no card on it): linked, still one payout
    r = client.post(f"/api/staff/{person['id']}/payout", json={"amount": 7_000_001, "payment_account_id": acc, "kind": "advance"})
    assert r.status_code == 200
    statement(client, acc, [(datetime.now() - timedelta(minutes=30), 7_000_001, None, "انتقال وجه", "990001")])
    tx = tx_by_ref("990001")
    assert (tx.status, tx.matched_type, tx.matched_id, tx.auto_booked) == ("matched", "staff_payout", r.json()["payout"]["id"], None)
    # the bank line first, then the expense entered by hand: linked to it
    statement(client, acc, [(datetime.now() - timedelta(minutes=20), 1_234_567, None, "خرید فروشگاه لوازم", "990002")])
    assert tx_by_ref("990002").status == "unmatched"
    e = client.post("/api/expenses", json={"category": "مواد مصرفی", "amount": 1_234_567, "payment_account_id": acc})
    assert e.status_code == 200
    tx = tx_by_ref("990002")
    assert (tx.status, tx.matched_type, tx.matched_id) == ("matched", "expense", e.json()["id"])
    # unlinking keeps the entry (it was not booked from the line)
    assert client.post(f"{BASE}/transactions/{tx.id}/undo", json={}).status_code == 200
    from app.models import Expense

    with _db() as db:
        assert db.get(Expense, e.json()["id"]).voided_at is None


def test_the_inbox_books_an_expense_and_remembers_the_card(client, accounts):
    line, svc, person = setup(client, "صندوق بانک", percent=40)
    acc = bank_account(client, "کارت سالن صندوق", card_number(21))
    shop = card_number(22)
    statement(client, acc, [(datetime.now() - timedelta(hours=5), 2_000_000, None, f"انتقال به {shop} بنام فروشگاه مواد", "770001")])
    tx = tx_by_ref("770001")
    assert tx.status == "unmatched" and tx.counterparty_name
    r = client.post(f"{BASE}/transactions/{tx.id}/book", json={"target": "expense", "category": "مواد مصرفی", "line_id": line["id"],
                                                              "from_treasury": True, "remember": True, "auto": True})
    assert r.status_code == 200, r.text
    from app.models import Expense

    with _db() as db:
        e = db.get(Expense, r.json()["expense_id"])
        assert (e.amount, e.line_id, e.from_treasury, e.payment_account_id) == (2_000_000, line["id"], True, acc)
    statement(client, acc, [(datetime.now() - timedelta(hours=1), 650_000, None, f"انتقال به {shop}", "770002")])
    tx2 = tx_by_ref("770002")
    assert (tx2.status, tx2.matched_type, tx2.auto_booked) == ("matched", "expense", True)
    rules = client.get(f"{BASE}/rules").json()
    assert any(x["target"] == "expense" and x["hits"] >= 1 for x in rules)
    # set aside, and back
    statement(client, acc, [(datetime.now() - timedelta(hours=2), 55_000, None, "کارمزد", "770003")])
    tx3 = tx_by_ref("770003")
    assert client.post(f"{BASE}/transactions/{tx3.id}/book", json={"target": "ignore"}).status_code == 200
    assert tx_by_ref("770003").status == "ignored"
    assert client.post(f"{BASE}/transactions/{tx3.id}/undo", json={}).status_code == 200
    assert tx_by_ref("770003").status == "unmatched"
    # the owner's own money: owner / admin only
    acc_user = user_client(client, "acc_bank_inbox", "accountant")
    assert acc_user.post(f"{BASE}/transactions/{tx3.id}/book", json={"target": "owner"}).status_code == 403


def test_an_outgoing_card_to_card_sms_is_never_money_in(client, accounts):
    salon = card_number(31)
    acc = bank_account(client, "کارت سالن پیامک", salon)
    dep = client.post("/api/deposits", json={"customer_name": "بیعانه پیامک خروجی", "customer_mobile": "09151239901", "amount": 5_000_000,
                                             "payment_account_id": acc})
    assert dep.status_code == 200
    masked = f"{salon[:4]}-{salon[4:6]}**-****-{salon[-4:]}"
    sms = f"کارت به کارت\nاز کارت {masked}\nبه کارت 6219-86**-****-5678\nمبلغ: 5,000,000 ریال"
    r = client.post(f"{BASE}/bank-sms", json={"text": sms, "payment_account_id": acc})
    assert r.status_code == 200 and r.json()["created"]
    from app.models import BankTransaction

    with _db() as db:
        tx = db.get(BankTransaction, r.json()["transaction_id"])
        assert tx.direction == "out" and tx.matched_type != "deposit"
    # an SMS that says neither: asked in the inbox
    r = client.post(f"{BASE}/bank-sms", json={"text": "مبلغ 3,330,000 ریال\nپیگیری 445566", "payment_account_id": acc})
    tx_id = r.json()["transaction_id"]
    row = next(x for x in client.get(f"{BASE}/inbox").json()["rows"] if x["id"] == tx_id)
    assert row["direction"] == "unknown"
    assert client.post(f"{BASE}/transactions/{tx_id}/direction", json={"direction": "out"}).status_code == 200


def test_the_same_money_from_two_sources_is_one_line_and_the_token_is_encrypted(client, accounts):
    acc = bank_account(client, "کارت سالن دو منبع", card_number(41))
    at = datetime.now() - timedelta(minutes=40)
    r = client.post(f"{BASE}/bank-sms", json={"text": f"برداشت: 2,220,000\n{jdate(at)} {at:%H:%M}", "payment_account_id": acc})
    first = r.json()["transaction_id"]
    statement(client, acc, [(at + timedelta(minutes=2), 2_220_000, None, "برداشت", None)])
    from app.models import BankTransaction, PaymentAccount

    with _db() as db:
        second = db.scalar(select(BankTransaction).where(BankTransaction.payment_account_id == acc, BankTransaction.source == "statement"))
        assert second.status == "duplicate" and second.duplicate_of_id == first
    assert client.post(f"{BASE}/transactions/{second.id}/not-duplicate").status_code == 200
    # a bank API token is kept encrypted (and never shown)
    r = client.put(f"/api/accounts/{acc}", json={"kind": "card", "name": "کارت سالن دو منبع", "provider": "generic_http",
                                                 "provider_config": {"url": "https://bank.example/{account}", "token": "SECRET-123"}})
    assert r.status_code == 200 and r.json()["provider_config"]["token"] == "••••"
    with _db() as db:
        assert db.get(PaymentAccount, acc).provider_config["token"].startswith("enc:")
    from app.core.security import decrypt_secret

    with _db() as db:
        assert decrypt_secret(db.get(PaymentAccount, acc).provider_config["token"]) == "SECRET-123"


def test_sms_direction_and_counterparty():
    from app.services.bank_parties import counterparty, mask_text, sms_direction

    assert sms_direction("بانک ملی\nانتقال از 0101234\nمبلغ:5,000,000-") == "out"
    assert sms_direction("واریز 2,000,000+ مانده 3,000,000") == "in"
    assert sms_direction("واریز به حساب 1,000,000 ریال موجودی قابل برداشت 5,000,000") == "in"
    assert sms_direction("خرید 450,000 ریال 1402/05/01-12:30") == "out"
    assert sms_direction("مبلغ 1,000,000 ریال") == "unknown"
    n = card_number(51)
    assert n not in mask_text(f"به {n}") and n[-4:] in mask_text(f"به {n}")
    p = counterparty(f"انتقال به {n} بنام زهرا احمدی شماره پیگیری 1")
    assert p["hash"] and p["mask"].endswith(n[-4:]) and p["name"] == "زهرا احمدی"
