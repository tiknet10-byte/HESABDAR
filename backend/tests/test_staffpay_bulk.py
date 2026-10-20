"""1.23: settling everyone at once - the list says what is still to be paid for the month (never more than owed
today), books each person once (a second click books nothing), says which row changed meanwhile, and employees
without the month's salary are pointed out."""
from datetime import datetime, time, timedelta

from tests.test_staffpay import PREV, PREV_START, _db, invoice, pay, setup, user_client


def plan(client, ym, who="all") -> dict:  # noqa: ANN001
    r = client.get("/api/staff/settle-plan", params={"jy": ym[0], "jm": ym[1], "who": who})
    assert r.status_code == 200, r.text
    return r.json()


def row_of(p, person) -> dict:  # noqa: ANN001
    return next(x for x in p["rows"] if x["staff_id"] == person["id"])


def test_settle_everyone_at_once(client, accounts):
    line, svc, a = setup(client, "تسویه گروهی", percent=40)
    b = client.post("/api/staff", json={"full_name": "پرسنل دوم تسویه گروهی", "line_id": line["id"], "commission_percent": 50}).json()
    emp = client.post("/api/staff", json={"full_name": "کارمند تسویه گروهی", "kind": "employee", "salary": 90_000_000}).json()
    at = datetime.combine(PREV_START - timedelta(days=3), time(12))
    invoice(client, accounts, svc, a, 10_000_000, at=at)
    invoice(client, accounts, svc, b, 6_000_000, at=at)
    cash = accounts["صندوق نقدی"]
    assert pay(client, a, 1_000_000, kind="advance", payment_account_id=cash, jy=PREV[0], jm=PREV[1],
               paid_at=(at + timedelta(hours=2)).isoformat()).status_code == 200
    p = plan(client, PREV)
    ra, rb, re_ = row_of(p, a), row_of(p, b), row_of(p, emp)
    assert (ra["due"], rb["due"]) == (3_000_000, 3_000_000)
    assert re_["due"] == 0 and re_["salary_missing"] and "حقوق" in re_["why"]
    assert row_of(plan(client, PREV, "staff"), a) and not any(x["staff_id"] == emp["id"] for x in plan(client, PREV, "staff")["rows"])

    # an accountant can settle, not from the owner's pocket
    acc = user_client(client, "acc_bulk", "accountant")
    body = {"jy": PREV[0], "jm": PREV[1], "payment_account_id": cash,
            "rows": [{"staff_id": a["id"], "amount": 3_000_000, "expected": 3_000_000},
                     {"staff_id": b["id"], "amount": 2_500_000, "expected": 3_000_000}]}
    assert acc.post("/api/staff/settle", json={**body, "from_owner": True}).status_code == 403
    r = acc.post("/api/staff/settle", json=body)
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert r.json()["total"] == 5_500_000 and {x["staff_id"] for x in r.json()["done"]} == {a["id"], b["id"]}
    p = plan(client, PREV)
    assert row_of(p, a)["due"] == 0 and row_of(p, a)["why"] == "تسویه شده"
    assert row_of(p, b)["due"] == 500_000
    # the same click again: nothing booked twice (the amount to pay changed)
    again = acc.post("/api/staff/settle", json=body).json()
    assert not again["done"] and len(again["errors"]) == 2 and "عوض شد" in again["errors"][0]["error"]
    st = client.get(f"/api/staff/{a['id']}/statement", params={"jy": PREV[0], "jm": PREV[1]}).json()
    assert st["remaining"] == 0 and st["balance_now"] == 0
    from sqlalchemy import func, select

    from app.models import StaffPayout

    with _db() as db:
        assert db.scalar(select(func.count(StaffPayout.id)).where(StaffPayout.staff_id == a["id"], StaffPayout.source == "bulk")) == 1
