"""1.23: the staff work brought over from the previous software is shared like invoice rows month by month (revenue,
treasury, staff share, salon share); booking a month owes the staff their share (Dr 3200 / Cr 2200) and freezes it; a
new treasury rate, a staff member set later, an undone import or a moved start day book it again; payouts made before
the program started are Dr 2200 / Cr 3200; the importer never takes «مریم احمدی» for «مریم تاجیک»."""
import io
from datetime import date, datetime

from sqlalchemy import func, select

from app.services.jalali import jalali_to_gregorian
from app.services.treasury import commission_of, pct_bp

M1, M2 = (1390, 3), (1390, 4)


def _db():
    from app.core.db import SessionLocal

    return SessionLocal()


def _xlsx(rows) -> bytes:  # noqa: ANN001
    import openpyxl

    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def g(jy: int, jm: int, jd: int, hour: int = 11) -> datetime:
    return datetime(*jalali_to_gregorian(jy, jm, jd), hour)


def ref_sum(code: str, ref_type: str, staff_id: int | None = None) -> int:
    """credit - debit on an account by entries of one kind."""
    from app.models import JournalEntry, JournalLine, LedgerAccount

    with _db() as db:
        q = (select(func.coalesce(func.sum(JournalLine.credit - JournalLine.debit), 0)).join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
             .join(LedgerAccount, LedgerAccount.id == JournalLine.account_id)
             .where(LedgerAccount.code == code, JournalEntry.ref_type == ref_type))
        if staff_id is not None:
            q = q.where(JournalLine.staff_id == staff_id)
        return int(db.scalar(q) or 0)


def balanced(client) -> None:  # noqa: ANN001
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"]


def month_row(client, ym) -> dict:  # noqa: ANN001
    r = client.get("/api/history/months")
    assert r.status_code == 200, r.text
    return next(m for m in r.json()["months"] if (m["jy"], m["jm"]) == ym)


def shares(client, ym) -> dict:  # noqa: ANN001
    r = client.get("/api/reports/staff-shares", params={"jy": ym[0], "jm": ym[1]})
    assert r.status_code == 200, r.text
    return r.json()


def by(rows, key, value) -> dict:  # noqa: ANN001
    return next(x for x in rows if x[key] == value)


def test_history_months_are_shared_booked_and_kept_in_step(client, accounts):
    line = client.post("/api/lines", json={"name": "لاین سوابق قبلی"}).json()
    client.post("/api/services", json={"line_id": line["id"], "name": "خدمت سوابق قبلی", "base_price": 1})
    p1 = client.post("/api/staff", json={"full_name": "هیستوا رضایی", "line_id": line["id"], "commission_percent": 40}).json()
    p2 = client.post("/api/staff", json={"full_name": "هیستونا تاجیک", "line_id": line["id"], "commission_percent": 50}).json()
    assert client.put("/api/history/start", json={"day": date.today().isoformat()}).status_code == 200
    try:
        _run(client, accounts, line, p1, p2)
    finally:
        assert client.put("/api/history/start", json={"day": None}).status_code == 200


def _run(client, accounts, line, p1, p2):  # noqa: ANN001
    head = ["نام مشتری", "موبایل", "تاریخ", "ساعت", "خدمت", "نام پرسنل", "قابل پرداخت", "درصد پرسنل"]
    rows = [["سابقه یک", "09151230001", "1390/03/05", "10:00", "خدمت سوابق قبلی", "دکتر هیستوا رضایی", 10_000_001, 0],
            ["سابقه دو", "09151230002", "1390/03/06", "10:00", "خدمت سوابق قبلی", "هیستونا", 3_000_000, 0],
            ["سابقه سه", "09151230003", "1390/03/07", "10:00", "خدمت سوابق قبلی", "هیستونا احمدی", 2_000_000, 0],
            ["سابقه چهار", "09151230004", "1390/03/08", "10:00", "خدمت سوابق قبلی", "", 1_000_000, 0],
            ["سابقه یک", "09151230001", "1390/04/05", "10:00", "خدمت سوابق قبلی", "هیستوا رضایی", 6_000_000, 0],
            ["سابقه دو", "09151230002", "1390/04/20", "10:00", "خدمت سوابق قبلی", "هیستونا", 4_000_000, 0]]
    pv = client.post("/api/import/legacy/preview", files={"file": ("h.xlsx", _xlsx([head, *rows]), "application/octet-stream")},
                     data={"kind": "history", "unit": "rial"}).json()
    assert pv["mapping"]["staff"] == 5 and 7 not in pv["mapping"].values()
    r = client.post(f"/api/import/legacy/{pv['id']}/commit", json={})
    assert r.status_code == 200 and r.json()["result"]["appointments"] == 6, r.text
    from app.models import Appointment

    with _db() as db:  # the matching: title left out, a unique first name, but never another last name
        got = {a.quoted_price: a.staff_id for a in db.scalars(select(Appointment).where(Appointment.customer_id.in_(
            select(Appointment.customer_id).where(Appointment.quoted_price.in_([10_000_001, 2_000_000, 3_000_000, 1_000_000])))))}
    assert got[10_000_001] == p1["id"] and got[3_000_000] == p2["id"] and got[2_000_000] is None and got[1_000_000] is None
    names = client.get("/api/history/staff-names").json()
    hit = by(names["names"], "name", "هیستونا احمدی")
    assert hit["rows"] == 1 and hit["suggest"] is None  # another last name: not suggested
    assert by(names["lines"], "line_id", line["id"])["rows"] == 1

    # live (not booked): the month's treasury (none yet), today's percents; owed nothing yet
    m1 = month_row(client, M1)
    assert (m1["rows"], m1["revenue"], m1["without_staff"], m1["booked"]) == (4, 16_000_001, 2, False)
    assert m1["staff_share"] == commission_of(10_000_001, 40) + commission_of(3_000_000, 50)
    assert client.put("/api/treasury", json={"line_id": line["id"], "jy": M1[0], "jm": M1[1], "rate_bp": 4000,
                                             "confirm": True}).status_code == 200
    t1 = pct_bp(10_000_001, 4000)
    share1 = commission_of(10_000_001 - t1, 40)
    share2 = commission_of(3_000_000 - pct_bp(3_000_000, 4000), 50)
    rep = shares(client, M1)
    lr = by(rep["lines"], "line_id", line["id"])
    assert lr["history"] == lr["revenue"] == 16_000_001
    assert lr["treasury"] == sum(pct_bp(x, 4000) for x in (10_000_001, 3_000_000, 2_000_000, 1_000_000))
    assert lr["revenue"] == lr["treasury"] + lr["staff_share"] + lr["salon_share"]
    assert by(rep["staff"], "staff_id", p1["id"])["staff_share"] == share1
    assert rep["totals"]["history_unbooked"] >= share1 + share2
    assert by(rep["staff"], "staff_id", p1["id"])["balance"] == 0  # not booked: owed nothing
    assert month_row(client, M2)["treasury"] == pct_bp(10_000_000, 4000)  # the next month takes the rate on

    # booking: with this month's percent of one person (30, not today's 40); frozen; owed on 2200
    d = client.post("/api/history/month", json={"jy": M1[0], "jm": M1[1], "percents": {str(p1["id"]): 30}}).json()
    share1 = commission_of(10_000_001 - t1, 30)
    assert by(d["staff"], "staff_id", p1["id"])["staff_share"] == share1 and not d["booked"]
    r = client.post("/api/history/book", json={"months": [{"jy": M1[0], "jm": M1[1], "percents": {str(p1["id"]): 30}}]})
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert ref_sum("2200", "history_share", p1["id"]) == share1 and ref_sum("2200", "history_share", p2["id"]) == share2
    assert ref_sum("3200", "history_share") == -(share1 + share2)  # the opening balances carry it: profit unchanged
    rep = shares(client, M1)
    assert by(rep["staff"], "staff_id", p1["id"])["staff_share"] == share1
    assert by(rep["staff"], "staff_id", p1["id"])["balance"] == share1
    st = client.get(f"/api/staff/{p1['id']}/statement", params={"jy": M1[0], "jm": M1[1]}).json()
    assert st["earned"]["history"] == share1 and st["closing"] == share1
    m1 = month_row(client, M1)
    assert m1["booked"] and not m1["stale"]
    client.post("/api/staff", json={"full_name": "هیستوای دوم", "commission_percent": 10})  # (no effect on the booked)
    assert client.put(f"/api/staff/{p1['id']}", json={"full_name": "هیستوا رضایی", "line_id": line["id"],
                                                      "commission_percent": 45}).status_code == 200
    assert by(shares(client, M1)["staff"], "staff_id", p1["id"])["staff_share"] == share1  # booked: as booked

    # a new rate of a booked month books it again (the percent stays 30); the next month (not booked) takes it on
    pv = client.post("/api/treasury/preview", json={"line_id": line["id"], "jy": M1[0], "jm": M1[1], "rate_bp": 5000}).json()
    assert pv["history_rows"] == 4 and pv["history_booked"]
    new1 = commission_of(10_000_001 - pct_bp(10_000_001, 5000), 30)
    assert by(pv["staff"], "staff_id", p1["id"])["balance_change"] == new1 - share1
    assert client.put("/api/treasury", json={"line_id": line["id"], "jy": M1[0], "jm": M1[1], "rate_bp": 5000,
                                             "expected_bp": 4000, "confirm": True}).status_code == 200
    share1 = new1
    share2 = commission_of(3_000_000 - pct_bp(3_000_000, 5000), 50)
    assert ref_sum("2200", "history_share", p1["id"]) == share1 and ref_sum("2200", "history_share", p2["id"]) == share2
    assert month_row(client, M2)["treasury"] == pct_bp(10_000_000, 5000)

    # the next month booked: a later change of the first month leaves it at the rate it was booked with
    assert client.post("/api/history/book", json={"months": [{"jy": M2[0], "jm": M2[1]}]}).json()["ok"]
    m2_share = commission_of(6_000_000 - pct_bp(6_000_000, 5000), 45) + commission_of(4_000_000 - pct_bp(4_000_000, 5000), 50)
    assert month_row(client, M2)["staff_share"] == m2_share
    pv = client.post("/api/treasury/preview", json={"line_id": line["id"], "jy": M1[0], "jm": M1[1], "rate_bp": 4500}).json()
    assert {"jy": M2[0], "jm": M2[1], "bp": 5000} in pv["later_kept"]

    # a name not matched at import set to a staff member: the booked month is booked again with that row
    r = client.post("/api/history/relink", json={"name": "هیستونا احمدی", "staff_id": p2["id"]})
    assert r.status_code == 200 and r.json()["rows"] == 1, r.text
    share2 += commission_of(2_000_000 - pct_bp(2_000_000, 5000), 50)
    assert ref_sum("2200", "history_share", p2["id"]) == share2 + commission_of(4_000_000 - pct_bp(4_000_000, 5000), 50)
    assert not month_row(client, M1)["stale"]
    # the line's rows without anyone: given to one person
    assert client.post("/api/history/relink", json={"line_id": line["id"], "staff_id": p2["id"]}).json()["rows"] == 1
    assert month_row(client, M1)["without_staff"] == 0

    # paid in those months (before the program): Dr 2200 / Cr 3200, no money account; not after the start
    r = client.post(f"/api/staff/{p1['id']}/payout", json={"amount": 1_000_003, "kind": "settlement", "jy": M1[0], "jm": M1[1],
                                                           "paid_at": g(1390, 4, 2).isoformat(), "before_start": True})
    assert r.status_code == 200, r.text
    assert r.json()["payout"]["before_start"] and r.json()["payout"]["account"] == "پیش از شروع برنامه"
    from app.models import JournalEntry, LedgerAccount, StaffPayout

    with _db() as db:
        p = db.get(StaffPayout, r.json()["payout"]["id"])
        codes = {db.get(LedgerAccount, ln.account_id).code: (ln.debit, ln.credit) for ln in db.get(JournalEntry, p.entry_id).lines}
    assert codes == {"2200": (1_000_003, 0), "3200": (0, 1_000_003)}
    st = client.get(f"/api/staff/{p1['id']}/statement", params={"jy": M1[0], "jm": M1[1]}).json()
    assert st["remaining"] == share1 - 1_000_003
    late = client.post(f"/api/staff/{p1['id']}/payout", json={"amount": 5_000, "paid_at": datetime.now().isoformat(),
                                                              "before_start": True})
    assert late.status_code == 400 and "پیش از شروع" in late.json()["detail"]
    balanced(client)

    # the start day moved into the second month: its later rows are the invoices' (not shared), booked again
    assert client.put("/api/history/start", json={"day": date(*jalali_to_gregorian(1390, 4, 10)).isoformat()}).json()["rebooked"] >= 1
    m2 = month_row(client, M2)
    assert m2["rows"] == 1 and m2["rows_after_start"] == 1 and not m2["stale"]
    assert month_row(client, M1)["rows"] == 4
    assert client.put("/api/history/start", json={"day": date.today().isoformat()}).status_code == 200
    assert month_row(client, M2)["rows"] == 2

    # unbooked: its entry goes, the rows are live again
    assert client.post("/api/history/unbook", json={"jy": M2[0], "jm": M2[1]}).status_code == 200
    assert not month_row(client, M2)["booked"]
    # the import taken back: the booked month has no rows left and is unbooked (nothing owed for it any more)
    u = client.post(f"/api/import/legacy/{pv_id(client)}/undo")
    assert u.status_code == 200, u.text
    assert ref_sum("2200", "history_share", p1["id"]) == 0 and ref_sum("2200", "history_share", p2["id"]) == 0
    from app.models import HistoryMonth

    with _db() as db:  # the bookings stay (nothing owed, the percents kept for when the rows come back)
        assert {(h.rows, h.staff_share, h.entry_id) for h in db.scalars(select(HistoryMonth).where(HistoryMonth.jy == 1390))} <= {(0, 0, None)}
    balanced(client)


def pv_id(client) -> int:  # noqa: ANN001
    from app.models import ImportBatch

    with _db() as db:
        return db.scalar(select(ImportBatch.id).where(ImportBatch.kind == "legacy_history", ImportBatch.status == "committed")
                         .order_by(ImportBatch.id.desc()).limit(1))


def test_reception_sees_no_history_money_and_cannot_book(client):
    client.post("/api/auth/users", json={"username": "rec_hist", "password": "Passw0rd1", "role": "receptionist"})
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": "rec_hist", "password": "Passw0rd1"}).json()["access_token"]
    assert c.get("/api/history/months").status_code == 403
    assert c.post("/api/history/book", json={"months": [{"jy": 1390, "jm": 1}]}).status_code == 403
    assert c.put("/api/history/start", json={"day": None}).status_code == 403


def test_suggest_staff_never_takes_another_last_name():
    from app.models import Staff
    from app.services.history_shares import suggest_staff

    people = [Staff(id=1, full_name="مریم تاجیک"), Staff(id=2, full_name="سپیده رضایی"), Staff(id=3, full_name="لیلا کریمی"),
              Staff(id=4, full_name="لیلا احمدی")]
    assert suggest_staff("مریم احمدی", people) is None
    assert suggest_staff("مریم", people)["id"] == 1
    assert suggest_staff("دکتر سپیده رضایی", people)["id"] == 2
    assert suggest_staff("خانم دکتر سپیده  رضایی", people)["id"] == 2
    assert suggest_staff("لیلا", people) is None  # two of them
    assert suggest_staff("لیلا احمدی", people)["id"] == 4
