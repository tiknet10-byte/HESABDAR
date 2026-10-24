"""1.23 review fixes: settling two overdue months pays each once; a payout entered by hand is never booked again from
its bank line (another tracking number); a line's opening treasury replaces what came before it; the start day can't
move before what was entered as «before the program»; payouts for a month without work are listed; a line with
treasury rows is archived, not deleted."""
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select

from app.services.jalali import jmonth_of, month_range
from tests.test_bank_booking import bank_account, card_number, statement, tx_by_ref
from tests.test_staffpay import CUR, _db, invoice, setup

M0_END = month_range(*CUR)[0] - timedelta(days=1)
PREV = jmonth_of(M0_END)
PREV2 = jmonth_of(month_range(*PREV)[0] - timedelta(days=1))


def test_two_overdue_months_settled_one_after_the_other_pay_each_once(client, accounts):
    line, svc, person = setup(client, "دو ماه عقب", percent=50)
    for ym, price in ((PREV2, 2_000_000), (PREV, 4_000_000)):
        invoice(client, accounts, svc, person, price, at=datetime.combine(month_range(*ym)[0] + timedelta(days=3), time(11)))
    invoice(client, accounts, svc, person, 6_000_000)  # this month's work (not to be paid as last month's)
    cash = accounts["صندوق نقدی"]
    plan = lambda ym: next(r for r in client.get("/api/staff/settle-plan", params={"jy": ym[0], "jm": ym[1]}).json()["rows"]  # noqa: E731
                           if r["staff_id"] == person["id"])
    p2 = plan(PREV2)
    assert p2["due"] == 1_000_000
    assert client.post("/api/staff/settle", json={"jy": PREV2[0], "jm": PREV2[1], "payment_account_id": cash,
                                                  "rows": [{"staff_id": person["id"], "amount": 1_000_000, "expected": 1_000_000}]}).json()["ok"]
    p1 = plan(PREV)
    assert p1["due"] == 2_000_000  # last month's own share only, never this month's
    st = client.get(f"/api/staff/{person['id']}/statement", params={"jy": PREV[0], "jm": PREV[1]}).json()
    assert st["paid_older"] == 1_000_000 and st["remaining"] == 2_000_000


def test_a_hand_payout_is_not_booked_again_from_its_bank_line(client, accounts):
    line, svc, person = setup(client, "دستی و بانک", percent=40)
    acc = bank_account(client, "کارت سالن دستی و بانک", card_number(61))
    their = card_number(62)
    assert client.post(f"/api/staff/{person['id']}/pay-cards", json={"number": their}).status_code == 200
    hand = client.post(f"/api/staff/{person['id']}/payout", json={"amount": 2_345_000, "payment_account_id": acc, "kind": "advance",
                                                                 "reference": "111222"})
    assert hand.status_code == 200
    statement(client, acc, [(datetime.now() - timedelta(minutes=50), 2_345_000, None, f"انتقال به {their}", "999888")])
    tx = tx_by_ref("999888")
    assert tx.status == "unmatched"  # not booked by itself: maybe the same money, another tracking number
    from app.models import StaffPayout

    with _db() as db:
        assert db.scalar(select(func.count(StaffPayout.id)).where(StaffPayout.staff_id == person["id"], StaffPayout.status == "booked")) == 1
    row = next(x for x in client.get("/api/plugins/bank_sync/inbox").json()["rows"] if x["id"] == tx.id)
    link = next(s for s in row["suggestions"] if s["action"] == "link")
    assert link["id"] == hand.json()["payout"]["id"]
    r = client.post(f"/api/plugins/bank_sync/transactions/{tx.id}/book", json={"target": "staff", "staff_id": person["id"]})
    assert r.status_code == 409 and "ثبت شده" in r.json()["detail"]
    assert client.post(f"/api/plugins/bank_sync/transactions/{tx.id}/link", json={"type": "staff_payout", "id": link["id"]}).status_code == 200
    assert tx_by_ref("999888").matched_id == link["id"]


def test_a_lines_opening_treasury_replaces_what_came_before_it(client, accounts):
    line, svc, person = setup(client, "موجودی اول خزانه", percent=40)
    for ym in (PREV, CUR):
        client.put("/api/treasury", json={"line_id": line["id"], "jy": ym[0], "jm": ym[1], "rate_bp": 5000, "confirm": True})
    invoice(client, accounts, svc, person, 2_000_000, at=datetime.combine(month_range(*PREV)[0] + timedelta(days=2), time(11)))
    invoice(client, accounts, svc, person, 4_000_000)
    fund = lambda: next(x for x in client.get("/api/treasury/fund").json()["lines"] if x["line_id"] == line["id"])  # noqa: E731
    assert fund()["balance"] == 3_000_000
    opening_at = datetime.combine(month_range(*CUR)[0], time(0, 1))
    assert client.post("/api/treasury/moves", json={"kind": "opening", "line_id": line["id"], "amount": 700_000,
                                                    "at": opening_at.isoformat() + "+03:30"}).status_code == 200
    assert fund()["balance"] == 700_000 + 2_000_000  # last month's 1,000,000 is inside the opening balance


def test_the_start_day_cannot_move_before_a_payout_made_before_the_program(client, accounts):
    line, svc, person = setup(client, "روز شروع و پرداخت", percent=40)
    assert client.put("/api/history/start", json={"day": date.today().isoformat()}).status_code == 200
    try:
        paid = datetime.now() - timedelta(days=20)
        r = client.post(f"/api/staff/{person['id']}/payout", json={"amount": 300_000, "paid_at": paid.isoformat(), "before_start": True})
        assert r.status_code == 200, r.text
        bad = client.put("/api/history/start", json={"day": (paid - timedelta(days=1)).date().isoformat()})
        assert bad.status_code == 400 and "پیش از شروع" in bad.json()["detail"]
        assert client.post(f"/api/staff/payouts/{r.json()['payout']['id']}/void", json={"reason": "آزمایش"}).status_code == 200
    finally:
        client.put("/api/history/start", json={"day": None})


def test_payouts_for_a_month_without_work_are_listed_and_a_used_line_is_archived(client, accounts):
    line, svc, person = setup(client, "پرداخت بدون کارکرد", percent=40)
    assert client.post(f"/api/staff/{person['id']}/payout", json={"amount": 450_000, "payment_account_id": accounts["صندوق نقدی"],
                                                                 "kind": "advance", "jy": PREV[0], "jm": PREV[1]}).status_code == 200
    months = client.get("/api/reports/staff-shares/months").json()["months"]
    m = next(x for x in months if (x["jy"], x["jm"]) == PREV)
    assert any(s["staff_id"] == person["id"] and s["paid_for_month"] == 450_000 for s in m["staff"])
    lone = client.post("/api/lines", json={"name": "لاین فقط موجودی خزانه"}).json()
    assert client.post("/api/treasury/moves", json={"kind": "opening", "line_id": lone["id"], "amount": 10_000}).status_code == 200
    r = client.delete(f"/api/lines/{lone['id']}")
    assert r.status_code == 200 and r.json()["archived"] is True


# ---------------------------------------------------------------------------------------------- second review round
def _waiting(ref):  # noqa: ANN001, ANN202
    return tx_by_ref(ref)


def test_booking_one_bank_line_never_takes_another_waiting_line(client, accounts):
    acc = bank_account(client, "کارت سالن دو خط یکسان", card_number(71))
    now = datetime.now()
    statement(client, acc, [(now - timedelta(hours=3), 2_000_000, None, "پرداخت اجاره", "710001"),
                            (now - timedelta(hours=2), 2_000_000, None, "پرداخت قبض", "710002")])
    a, b = _waiting("710001"), _waiting("710002")
    r = client.post(f"/api/plugins/bank_sync/transactions/{a.id}/book", json={"target": "expense", "category": "اجاره"})
    assert r.status_code == 200, r.text
    assert _waiting("710002").status == "unmatched"  # still waiting: its own money
    # the same expense entered again by hand: pointed out (it was booked from the bank)
    dup = client.post("/api/expenses", json={"category": "اجاره", "amount": 2_000_000, "payment_account_id": acc})
    assert dup.status_code == 409 and "بانک" in dup.json()["detail"]
    assert b.id != a.id


def test_money_to_one_persons_card_is_never_linked_to_anothers_payout(client, accounts):
    line, svc, a = setup(client, "کارت الف", percent=40)
    b = client.post("/api/staff", json={"full_name": "پرسنل کارت ب", "line_id": line["id"], "commission_percent": 40}).json()
    acc = bank_account(client, "کارت سالن الف و ب", card_number(72))
    card_a, card_b = card_number(73), card_number(74)
    for p, c in ((a, card_a), (b, card_b)):
        assert client.post(f"/api/staff/{p['id']}/pay-cards", json={"number": c}).status_code == 200
    hand = client.post(f"/api/staff/{a['id']}/payout", json={"amount": 5_000_000, "payment_account_id": acc, "kind": "advance",
                                                            "paid_at": (datetime.now() - timedelta(days=1)).isoformat()})
    assert hand.status_code == 200
    statement(client, acc, [(datetime.now() - timedelta(minutes=30), 5_000_000, None, f"انتقال به {card_b}", "720001")])
    tx = _waiting("720001")
    from app.models import StaffPayout

    with _db() as db:  # B's money is B's payout (or waits), never A's hand payout
        if tx.matched_type == "staff_payout":
            assert db.get(StaffPayout, tx.matched_id).staff_id == b["id"]
        assert db.get(StaffPayout, hand.json()["payout"]["id"]).bank_transaction_id is None


def test_an_undone_link_is_not_made_again_and_direction_is_said_only_when_unclear(client, accounts):
    from app.services import bank_booking

    line, svc, person = setup(client, "پیوند برگشتی", percent=40)
    acc = bank_account(client, "کارت سالن پیوند برگشتی", card_number(75))
    hand = client.post(f"/api/staff/{person['id']}/payout", json={"amount": 3_210_000, "payment_account_id": acc, "kind": "advance"})
    assert hand.status_code == 200
    statement(client, acc, [(datetime.now() - timedelta(minutes=20), 3_210_000, None, "انتقال وجه", "730001")])
    tx = _waiting("730001")
    assert tx.matched_type == "staff_payout"
    assert client.post(f"/api/plugins/bank_sync/transactions/{tx.id}/undo", json={}).status_code == 200
    with _db() as db:
        bank_booking.rematch(db)
        db.commit()
    assert _waiting("730001").status == "unmatched"
    # an outgoing line can't be called money in (only an unclear one gets a direction)
    r = client.post(f"/api/plugins/bank_sync/transactions/{tx.id}/direction", json={"direction": "in"})
    assert r.status_code == 400


def test_an_accountant_cannot_change_a_remembered_card(client, accounts):
    line, svc, person = setup(client, "کارت حسابدار", percent=40)
    acc = bank_account(client, "کارت سالن کارت حسابدار", card_number(76))
    card = card_number(77)
    assert client.post(f"/api/staff/{person['id']}/pay-cards", json={"number": card}).status_code == 200
    hand = client.post(f"/api/staff/{person['id']}/payout", json={"amount": 1_111_000, "payment_account_id": acc, "kind": "advance",
                                                                 "reference": "1"})
    assert hand.status_code == 200
    statement(client, acc, [(datetime.now() - timedelta(minutes=10), 1_111_000, None, f"به {card}", "760001")])
    tx = _waiting("760001")
    assert tx.status == "unmatched"
    from tests.test_staffpay import user_client

    accnt = user_client(client, "acc_rule_change", "accountant")
    r = accnt.post(f"/api/plugins/bank_sync/transactions/{tx.id}/book", json={"target": "expense", "category": "سایر", "remember": True})
    assert r.status_code == 200 and not r.json()["remembered"] and r.json()["remember_note"]
    cards = client.get(f"/api/staff/{person['id']}/pay-cards").json()
    assert cards and cards[0]["auto"] is True  # still theirs


def test_same_money_from_two_sources_two_real_payments_stay_two(client, accounts):
    from tests.test_bank_booking import jdate

    acc = bank_account(client, "کارت سالن دو پرداخت", card_number(78))
    at = datetime.now() - timedelta(minutes=50)
    client.post("/api/plugins/bank_sync/bank-sms", json={"text": f"برداشت: 1,500,000\n{jdate(at)} {at:%H:%M}", "payment_account_id": acc})
    statement(client, acc, [(at + timedelta(minutes=1), 1_500_000, None, "انتقال", "S8A"), (at + timedelta(minutes=7), 1_500_000, None, "انتقال", "S8B")])
    statuses = sorted(_waiting(r).status for r in ("S8A", "S8B"))
    assert statuses.count("duplicate") <= 1
    # an unclear SMS and the statement row of the same money: the clear one stays
    at2 = datetime.now() - timedelta(minutes=40)
    sms = client.post("/api/plugins/bank_sync/bank-sms", json={"text": f"مبلغ 2,750,000 ریال\n{jdate(at2)} {at2:%H:%M}",
                                                                "payment_account_id": acc}).json()
    statement(client, acc, [(at2 + timedelta(minutes=2), 2_750_000, None, "انتقال", "S9A")])
    from app.models import BankTransaction

    with _db() as db:
        assert db.get(BankTransaction, sms["transaction_id"]).status == "duplicate"
    assert _waiting("S9A").status != "duplicate"


def test_review_small_things(client, accounts):
    from app.services.bank_parties import counterparty, mask_text, sms_direction

    p = counterparty("شماره پیگیری 1234567812345678 انتقال به کارت 6219861012345673")
    assert p["mask"].endswith("5673")  # a 16-digit tracking number is not a card
    assert "1234567812345678" in mask_text("پیگیری 1234567812345678")
    assert sms_direction("از کارت 6219-86**-****-5678\nبه کارت 6037-99**-****-1234\nمبلغ 5,000,000 واریز شد", {"1234"}) == "in"
    assert sms_direction("برداشت 1,500,000 تماس +982161111111") == "out"
    assert counterparty("", card="603799******4321")["mask"].endswith("4321")
    # notes of a service of the previous software only grow; the mark can't be put on another visit
    from app.models import Appointment, Customer
    from app.services.legacy_import import IMPORT_NOTE

    with _db() as db:
        c = Customer(full_name="یادداشت سابقه", mobile="09151239977")
        db.add(c)
        db.flush()
        old = Appointment(customer_id=c.id, start_at=datetime(2020, 1, 1, 10), status="done", quoted_price=1, notes=f"{IMPORT_NOTE} - پرسنل: الف")
        hand = Appointment(customer_id=c.id, start_at=datetime(2020, 1, 2, 10), status="done", quoted_price=1, notes="")
        db.add_all([old, hand])
        db.commit()
        old_id, hand_id = old.id, hand.id
    from tests.test_staffpay import user_client

    rec = user_client(client, "rec_notes_123", "receptionist")
    assert rec.put(f"/api/appointments/{old_id}", json={"notes": f"{IMPORT_NOTE} - پرسنل: ب"}).status_code == 400
    assert rec.put(f"/api/appointments/{old_id}", json={"notes": f"{IMPORT_NOTE} - پرسنل: الف - تماس گرفت"}).status_code == 200
    assert rec.put(f"/api/appointments/{hand_id}", json={"notes": f"{IMPORT_NOTE} - x"}).status_code == 400
    # deleting a line's last service keeps a line its treasury names
    line = client.post("/api/lines", json={"name": "لاین سرویس آخر"}).json()
    svc = client.post("/api/services", json={"line_id": line["id"], "name": "سرویس آخر", "base_price": 1}).json()
    assert client.post("/api/treasury/moves", json={"kind": "opening", "line_id": line["id"], "amount": 5_000}).status_code == 200
    assert client.delete(f"/api/services/{svc['id']}").status_code == 200
    assert any(x["id"] == line["id"] for x in client.get("/api/lines?all=true").json())
