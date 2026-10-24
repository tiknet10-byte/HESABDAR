"""1.23: each line's treasury is a fund for the clinic's expenses of that line: set aside by its services (the treasury
the staff shares page shows), spent by expenses «از خزانه» (ordinary expenses: the profit falls the same), carried from
month to month, per staff member too; its opening balance and what was spent before the program are memo rows."""
from datetime import datetime, timedelta

from sqlalchemy import select

from app.services.jalali import jmonth_of, month_range
from app.services.treasury import pct_bp

from tests.test_staffpay import CUR, PREV, PREV_START, _db, deposit, forfeit, invoice, setup


def fund(client) -> dict:  # noqa: ANN001
    r = client.get("/api/treasury/fund")
    assert r.status_code == 200, r.text
    return r.json()


def line_of(f, line) -> dict:  # noqa: ANN001
    return next(x for x in f["lines"] if x["line_id"] == line["id"])


def month_of(rows, ym) -> dict:  # noqa: ANN001
    return next(x for x in rows if (x["jy"], x["jm"]) == tuple(ym))


def expense(client, accounts, amount, **kw):  # noqa: ANN001, ANN201
    return client.post("/api/expenses", json={"category": "مواد مصرفی", "amount": amount, "payment_account_id": accounts["صندوق نقدی"],
                                              **kw})


def test_a_lines_treasury_is_set_aside_spent_and_carried(client, accounts):
    line, svc, person = setup(client, "خزانه صندوق", percent=40)
    other = client.post("/api/staff", json={"full_name": "پرسنل دوم خزانه صندوق", "line_id": line["id"], "commission_percent": 30}).json()
    for ym in (PREV, CUR):
        assert client.put("/api/treasury", json={"line_id": line["id"], "jy": ym[0], "jm": ym[1], "rate_bp": 4000,
                                                 "confirm": True}).status_code == 200
    invoice(client, accounts, svc, person, 10_000_000, at=datetime.combine(PREV_START, datetime.min.time()) + timedelta(hours=11))
    invoice(client, accounts, svc, other, 5_000_001)
    dep = deposit(client, accounts, svc, person, 2_000_000)
    forfeit(client, dep)
    f = line_of(fund(client), line)
    prev, cur = month_of(f["months"], PREV), month_of(f["months"], CUR)
    assert prev["accrued"] == 4_000_000 and prev["balance_end"] == 4_000_000
    assert cur["accrued"] == pct_bp(5_000_001, 4000) + 800_000 and cur["balance_start"] == 4_000_000

    # spent from it: an ordinary expense of the clinic (the profit falls the same) - the voided one never happened
    def profit() -> int:
        from app.services.capital import profit_breakdown

        with _db() as db:
            s, e = month_range(*CUR)
            return profit_breakdown(db, s, e)["total"]

    p0 = profit()
    assert expense(client, accounts, 1_000_000, from_treasury=True).status_code == 400  # which line's treasury?
    r = expense(client, accounts, 1_000_000, line_id=line["id"], from_treasury=True, description="ژل لاین")
    assert r.status_code == 200, r.text
    r2 = expense(client, accounts, 300_000, line_id=line["id"], staff_id=other["id"], from_treasury=True)
    assert r2.status_code == 200
    bad = expense(client, accounts, 777_000, line_id=line["id"], from_treasury=True)
    assert client.post(f"/api/expenses/{bad.json()['id']}/void", json={"reason": "اشتباه"}).status_code == 200
    assert profit() == p0 - 1_300_000
    f = line_of(fund(client), line)
    cur = month_of(f["months"], CUR)
    assert cur["spent"] == 1_300_000
    assert f["balance"] == 4_000_000 + pct_bp(5_000_001, 4000) + 800_000 - 1_300_000
    me = next(x for x in f["staff"] if x["staff_id"] == person["id"])
    two = next(x for x in f["staff"] if x["staff_id"] == other["id"])
    general = next(x for x in f["staff"] if x["staff_id"] is None)
    assert (me["accrued"], me["spent"]) == (4_800_000, 0)
    assert (two["accrued"], two["spent"], two["balance"]) == (pct_bp(5_000_001, 4000), 300_000, pct_bp(5_000_001, 4000) - 300_000)
    assert general["spent"] == 1_000_000
    per = client.get(f"/api/treasury/fund/{line['id']}").json()
    assert {x["staff_id"]: x["balance"] for x in per["staff"]}[other["id"]] == two["balance"]

    # an expense entered before without the tick: set later (no money moves, its entry carries the line)
    plain = expense(client, accounts, 200_000).json()
    p1 = profit()
    assert client.post(f"/api/expenses/{plain['id']}/treasury", json={"line_id": line["id"], "from_treasury": True}).status_code == 200
    assert profit() == p1
    assert line_of(fund(client), line)["balance"] == f["balance"] - 200_000
    from sqlalchemy import select

    from app.models import JournalEntry, JournalLine, LedgerAccount

    with _db() as db:
        e = db.scalar(select(JournalEntry).where(JournalEntry.ref_type == "expense", JournalEntry.ref_id == plain["id"]))
        legs = {db.get(LedgerAccount, ln.account_id).parent_code: ln.line_id
                for ln in db.scalars(select(JournalLine).where(JournalLine.entry_id == e.id))}
    assert legs.get("5100") == line["id"]
    exp = client.get("/api/expenses", params={"jy": CUR[0], "jm": CUR[1]}).json()
    assert exp["treasury_spent"] >= 1_500_000
    assert next(x for x in exp["rows"] if x["id"] == plain["id"])["from_treasury"]

    # the balance when the program started (owner / admin) and money spent from it before the start: memo rows
    from app.services.history_shares import books_start

    with _db() as db:
        start = books_start(db)
    before = (start or datetime.now()) - timedelta(days=400)
    assert client.post("/api/treasury/moves", json={"kind": "opening", "line_id": line["id"], "amount": 2_500_000,
                                                    "at": before.isoformat()}).status_code == 200
    sb = client.post("/api/treasury/moves", json={"kind": "spent_before", "line_id": line["id"], "amount": 400_000,
                                                  "at": (before + timedelta(days=3)).isoformat(), "category": "تعمیر دستگاه"})
    assert sb.status_code == 200, sb.text
    late = client.post("/api/treasury/moves", json={"kind": "spent_before", "line_id": line["id"], "amount": 1,
                                                    "at": datetime.now().isoformat()})
    assert late.status_code == 400 and "هزینه‌ها" in late.json()["detail"]
    f2 = line_of(fund(client), line)
    assert f2["balance"] == f["balance"] - 200_000 + 2_500_000 - 400_000
    assert month_of(f2["months"], jmonth_of(before))["opening"] == 2_500_000
    assert client.post(f"/api/treasury/moves/{sb.json()['id']}/void").status_code == 200
    assert line_of(fund(client), line)["balance"] == f2["balance"] + 400_000
    spent = client.get("/api/treasury/spending", params={"jy": CUR[0], "jm": CUR[1], "line_id": line["id"]}).json()["rows"]
    assert {x["amount"] for x in spent if not x["void"]} == {1_000_000, 300_000, 200_000}


def test_the_funds_month_is_the_staff_pages_treasury(client, accounts):
    f = fund(client)
    from app.models import TreasuryMove

    with _db() as db:  # a line with an opening balance starts from it (what came before is in it): compared apart
        opened = {m.line_id for m in db.scalars(select(TreasuryMove).where(TreasuryMove.kind == "opening", TreasuryMove.status == "active"))}
    for ym in (PREV, CUR):
        rep = client.get("/api/reports/staff-shares", params={"jy": ym[0], "jm": ym[1]}).json()
        for lr in rep["lines"]:
            if lr["line_id"] in opened or not lr["treasury"]:
                continue
            line_fund = next(x for x in f["lines"] if x["line_id"] == lr["line_id"])
            assert month_of(line_fund["months"], ym)["accrued"] == lr["treasury"], lr["name"]
        if not opened:
            assert month_of(f["months"], ym)["accrued"] == rep["totals"]["treasury"]
    months = client.get("/api/reports/staff-shares/months").json()
    assert months["months"][0]["jy"] == CUR[0] and months["months"][0]["jm"] == CUR[1] and len(months["months"]) == 12
    cur = months["months"][0]
    rep = client.get("/api/reports/staff-shares", params={"jy": CUR[0], "jm": CUR[1]}).json()
    assert (cur["revenue"], cur["treasury"], cur["staff_share"]) == (rep["totals"]["revenue"], rep["totals"]["treasury"],
                                                                     rep["totals"]["staff_share"])


def test_reception_cannot_see_or_set_the_treasury(client):
    from tests.test_staffpay import user_client

    rec = user_client(client, "rec_fund", "receptionist")
    assert rec.get("/api/treasury/fund").status_code == 403
    assert rec.post("/api/treasury/moves", json={"kind": "opening", "line_id": 1, "amount": 1}).status_code == 403
    acc = user_client(client, "acc_fund", "accountant")
    lines = client.get("/api/lines").json()
    r = acc.post("/api/treasury/moves", json={"kind": "opening", "line_id": lines[0]["id"], "amount": 1})
    assert r.status_code == 403  # the opening balance: owner / admin
