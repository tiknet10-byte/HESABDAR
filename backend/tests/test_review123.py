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
