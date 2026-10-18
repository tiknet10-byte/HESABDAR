"""Treasury (خزانه): each service line's part of a month's revenue taken first, the rest split between the staff
member and the salon - booked in the staff commission (the ledger), recalculated for one month when its rate changes."""
import time as clock
from datetime import date, datetime, time, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.services.jalali import jmonth_of, month_range, next_month
from app.services.treasury import pct_bp

TODAY = date.today()
CUR = jmonth_of(TODAY)
PREV_START = month_range(*CUR)[0] - timedelta(days=1)
PREV = jmonth_of(PREV_START)
_n = [0]


def _db():
    from app.core.db import SessionLocal

    return SessionLocal()


def _mobile() -> str:
    _n[0] += 1
    return f"0915140{_n[0]:04d}"


def setup(client, name: str, percent: float = 30, price: int = 10_000_000):  # noqa: ANN201
    line = client.post("/api/lines", json={"name": f"لاین خزانه {name}"}).json()
    svc = client.post("/api/services", json={"line_id": line["id"], "name": f"خدمت خزانه {name}", "base_price": price}).json()
    person = client.post("/api/staff", json={"full_name": f"پرسنل خزانه {name}", "line_id": line["id"], "commission_percent": percent}).json()
    return line, svc, person


def invoice(client, accounts, svc, person, price: int, at: datetime | None = None, discount: int = 0, staff: bool = True) -> dict:
    body = {"customer_name": "مشتری خزانه", "customer_mobile": _mobile(), "discount": discount,
            "items": [{"service_id": svc["id"], "unit_price": price, **({"staff_id": person["id"]} if staff else {})}],
            "payments": [{"payment_account_id": accounts["صندوق نقدی"], "amount": price - discount}]}
    if at:
        body["issued_at"] = at.isoformat()
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def put_rate(client, line, ym, bp: int, expected: int | None = None, confirm: bool = True):  # noqa: ANN001, ANN201
    return client.put("/api/treasury", json={"line_id": line["id"], "jy": ym[0], "jm": ym[1], "rate_bp": bp,
                                             "expected_bp": expected, "confirm": confirm})


def month_report(client, ym) -> dict:  # noqa: ANN001
    r = client.get("/api/reports/staff-shares", params={"jy": ym[0], "jm": ym[1]})
    assert r.status_code == 200, r.text
    return r.json()


def line_row(rep: dict, line) -> dict:  # noqa: ANN001
    return next((x for x in rep["lines"] if x["line_id"] == line["id"]),
                {"revenue": 0, "treasury": 0, "staff_share": 0, "salon_share": 0})


def ledger_5200(line, ym) -> int:  # noqa: ANN001
    """Staff commission of the line booked in the month (debit - credit of 5200)."""
    from app.models import JournalEntry, JournalLine, LedgerAccount

    s, e = month_range(*ym)
    with _db() as db:
        acc = db.scalar(select(LedgerAccount.id).where(LedgerAccount.code == "5200"))
        return int(db.scalar(select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
                             .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                             .where(JournalLine.account_id == acc, JournalLine.line_id == line["id"],
                                    JournalEntry.at >= s, JournalEntry.at < e)) or 0)


def balance(client, person) -> int:  # noqa: ANN001
    rep = month_report(client, CUR)
    return next(x["balance"] for x in rep["staff"] if x["staff_id"] == person["id"])


def balanced(client) -> None:
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"]


# ---------------------------------------------------------------------------------------------- the rule
def test_integer_rounding_is_half_up_and_exact():
    assert pct_bp(41_000, 4445) == 18_225  # 18224.5 -> 18225 (a float 44.45 gives 18224.500000000004 or 18224)
    assert pct_bp(10_000_001, 4250) == 4_250_000  # 4250000.425
    assert pct_bp(0, 4500) == 0 and pct_bp(7, 10_000) == 7 and pct_bp(-41_000, 4445) == -18_225


def test_a_months_rate_is_taken_first_then_the_rest_is_split(client, accounts):
    line, svc, person = setup(client, "پایه", percent=30)
    assert put_rate(client, line, CUR, 4200).status_code == 200
    inv = invoice(client, accounts, svc, person, 10_000_000)
    other = invoice(client, accounts, svc, person, 3_000_001, discount=1)  # an odd amount: still exact to the Rial
    it = client.get(f"/api/invoices/{inv['id']}").json()["items"][0]
    assert (it["treasury_bp"], it["treasury_amount"], it["commission_amount"]) == (4200, 4_200_000, 1_740_000)
    rep = line_row(month_report(client, CUR), line)
    assert rep["revenue"] == 13_000_000
    assert rep["treasury"] == 4_200_000 + pct_bp(3_000_000, 4200)
    assert rep["treasury"] + rep["staff_share"] + rep["salon_share"] == rep["revenue"]
    assert rep["staff_share"] == ledger_5200(line, CUR)  # the books
    balanced(client)
    assert other["total"] == 3_000_000


def test_a_row_without_a_staff_member_gives_its_treasury_and_the_rest_to_the_salon(client, accounts):
    line, svc, person = setup(client, "بی‌پرسنل", percent=30)
    client.post("/api/staff", json={"full_name": "پرسنل دوم بی‌پرسنل", "line_id": line["id"], "commission_percent": 40})  # two: none is chosen
    assert put_rate(client, line, CUR, 4200).status_code == 200
    alone = invoice(client, accounts, svc, person, 1_000_000, staff=False)
    item = client.get(f"/api/invoices/{alone['id']}").json()["items"][0]
    assert item["staff_id"] is None
    assert (item["treasury_amount"], item["commission_amount"]) == (420_000, 0)
    rep = line_row(month_report(client, CUR), line)
    assert (rep["revenue"], rep["treasury"], rep["staff_share"], rep["salon_share"]) == (1_000_000, 420_000, 0, 580_000)


def test_no_rate_is_as_before_and_a_month_without_its_own_rate_uses_the_last_one(client, accounts):
    line, svc, person = setup(client, "پیش‌فرض", percent=40)
    inv = invoice(client, accounts, svc, person, 5_000_000, at=datetime.combine(PREV_START - timedelta(days=2), time(10)))
    it = client.get(f"/api/invoices/{inv['id']}").json()["items"][0]
    assert (it["treasury_amount"], it["commission_amount"]) == (0, 2_000_000)  # rate 0: exactly as before 1.21
    rates = {x["line_id"]: x for x in client.get("/api/treasury", params={"jy": CUR[0], "jm": CUR[1]}).json()["lines"]}
    assert rates[line["id"]]["set"] is False and rates[line["id"]]["bp"] == 0
    assert put_rate(client, line, PREV, 4000).status_code == 200
    rates = {x["line_id"]: x for x in client.get("/api/treasury", params={"jy": CUR[0], "jm": CUR[1]}).json()["lines"]}
    assert rates[line["id"]]["bp"] == 4000 and not rates[line["id"]]["own"] and rates[line["id"]]["from"] == list(PREV)
    now = invoice(client, accounts, svc, person, 5_000_000)
    it = client.get(f"/api/invoices/{now['id']}").json()["items"][0]
    assert (it["treasury_bp"], it["treasury_amount"], it["commission_amount"]) == (4000, 2_000_000, 1_200_000)


# ---------------------------------------------------------------------------------------------- changing a month
def test_changing_a_past_month_books_that_month_again_and_only_it(client, accounts):
    line, svc, person = setup(client, "تغییر", percent=30)
    p_at = datetime.combine(PREV_START - timedelta(days=1), time(11))
    a = invoice(client, accounts, svc, person, 10_000_000, at=p_at)
    b = invoice(client, accounts, svc, person, 4_000_000, at=p_at)
    c = invoice(client, accounts, svc, person, 6_000_000)  # this month: no rate of its own, takes the last one (none: 0)
    bal0 = balance(client, person)
    pv = client.post("/api/treasury/preview", json={"line_id": line["id"], "jy": PREV[0], "jm": PREV[1], "rate_bp": 4500}).json()
    assert pv["old_bp"] == 0 and pv["new_bp"] == 4500 and pv["invoices"] == 2
    me = next(x for x in pv["staff"] if x["staff_id"] == person["id"])
    assert (me["before"], me["after"]) == (4_200_000, 2_310_000) and me["balance_after"] == bal0 - 1_890_000
    assert pv["later_kept"] == [{"jy": CUR[0], "jm": CUR[1], "bp": 0}]  # this month keeps the rate it was booked with
    r = put_rate(client, line, PREV, 4500, expected=0)
    assert r.status_code == 200, r.text
    rep = line_row(month_report(client, PREV), line)
    assert (rep["revenue"], rep["treasury"], rep["staff_share"]) == (14_000_000, 6_300_000, 2_310_000)
    assert rep["staff_share"] == ledger_5200(line, PREV) and rep["salon_share"] == 14_000_000 - 6_300_000 - 2_310_000
    assert balance(client, person) == bal0 - 1_890_000
    # this month: untouched, and its rate is now its own (kept at what it used)
    cur = line_row(month_report(client, CUR), line)
    assert (cur["treasury"], cur["staff_share"]) == (0, 1_800_000) == (0, ledger_5200(line, CUR))
    rates = {x["line_id"]: x for x in client.get("/api/treasury", params={"jy": CUR[0], "jm": CUR[1]}).json()["lines"]}
    assert rates[line["id"]]["own"] and rates[line["id"]]["source"] == "auto" and rates[line["id"]]["bp"] == 0
    # saving the old value again gives back exactly what was there
    assert put_rate(client, line, PREV, 0, expected=4500).status_code == 200
    assert line_row(month_report(client, PREV), line)["staff_share"] == 4_200_000 == ledger_5200(line, PREV)
    assert balance(client, person) == bal0
    for x in (a, b, c):
        it = client.get(f"/api/invoices/{x['id']}").json()["items"][0]
        assert it["commission_amount"] == round(x["total"] * 0.3)
    balanced(client)


def test_returns_take_back_exactly_what_was_given(client, accounts):
    """Invoices of last month: one cancelled as a mistake, one returned today (money back), one kept as credit today;
    then last month's rate changes: each return still takes back exactly its (new) staff share, and this month's
    staff share in the report equals the books."""
    line, svc, person = setup(client, "برگشت", percent=50)
    at = datetime.combine(PREV_START - timedelta(days=3), time(12))
    keep = invoice(client, accounts, svc, person, 8_000_000, at=at)
    gone = [invoice(client, accounts, svc, person, 2_000_000, at=at) for _ in range(3)]
    for inv, mode in zip(gone, ("cancel", "refund", "deposit")):
        assert client.post(f"/api/invoices/{inv['id']}/void", params={"payments": mode, "reason": "خزانه"}).status_code == 200
    bal0 = balance(client, person)
    assert put_rate(client, line, PREV, 4000).status_code == 200
    prev, cur = line_row(month_report(client, PREV), line), line_row(month_report(client, CUR), line)
    # last month: the kept invoice and the two returned later (sold then); the cancelled one never happened
    assert prev["revenue"] == 12_000_000 and prev["treasury"] == 4_800_000 and prev["staff_share"] == 3_600_000
    assert prev["staff_share"] == ledger_5200(line, PREV)
    # this month: the two returns, each with last month's treasury
    assert cur["revenue"] == -4_000_000 and cur["treasury"] == -1_600_000 and cur["staff_share"] == -1_200_000
    assert cur["staff_share"] == ledger_5200(line, CUR) and cur["returned"] == 4_000_000
    assert balance(client, person) == bal0 - 1_600_000  # only the kept invoice's share changed what is owed
    # a return made after the change copies the new share
    late = invoice(client, accounts, svc, person, 2_000_000, at=at)
    assert client.get(f"/api/invoices/{late['id']}").json()["items"][0]["commission_amount"] == 600_000
    client.post(f"/api/invoices/{late['id']}/void", params={"payments": "refund"})
    assert line_row(month_report(client, CUR), line)["staff_share"] == -1_800_000 == ledger_5200(line, CUR)
    balanced(client)


def test_a_change_after_a_payout_needs_a_confirmation_and_a_stale_page_is_refused(client, accounts):
    line, svc, person = setup(client, "پرداخت", percent=30)
    invoice(client, accounts, svc, person, 10_000_000)
    assert client.post(f"/api/staff/{person['id']}/payout", json={"amount": 3_000_000, "payment_account_id": accounts["صندوق نقدی"]}).status_code == 200
    pv = client.post("/api/treasury/preview", json={"line_id": line["id"], "jy": CUR[0], "jm": CUR[1], "rate_bp": 4500}).json()
    me = next(x for x in pv["staff"] if x["staff_id"] == person["id"])
    assert pv["needs_confirm"] and me["paid_since"] == 3_000_000 and me["balance_after"] == 3_000_000 - 3_000_000 - 1_350_000
    r = put_rate(client, line, CUR, 4500, expected=0, confirm=False)
    assert r.status_code == 409 and "تأیید" in r.json()["detail"]
    assert put_rate(client, line, CUR, 4500, expected=4200, confirm=True).status_code == 409  # the page showed another rate
    assert put_rate(client, line, CUR, 4500, expected=0, confirm=True).status_code == 200
    assert balance(client, person) == -1_350_000  # paid more than the new share: the salon is owed back


def test_who_may_see_and_change_the_treasury(client):
    line, _, _ = setup(client, "دسترسی")
    reception = TestClient(app)
    r = client.post("/api/auth/users", json={"username": "treasury-reception", "password": "Passw0rd1", "role": "receptionist"})
    assert r.status_code == 200, r.text
    reception.headers["Authorization"] = "Bearer " + reception.post("/api/auth/login", json={"username": "treasury-reception",
                                                                                             "password": "Passw0rd1"}).json()["access_token"]
    assert reception.get("/api/treasury").status_code == 403
    assert reception.get("/api/reports/staff-shares", params={"jy": CUR[0], "jm": CUR[1]}).status_code == 403
    acc = TestClient(app)
    client.post("/api/auth/users", json={"username": "treasury-accountant", "password": "Passw0rd1", "role": "accountant"})
    acc.headers["Authorization"] = "Bearer " + acc.post("/api/auth/login", json={"username": "treasury-accountant",
                                                                                 "password": "Passw0rd1"}).json()["access_token"]
    assert acc.get("/api/treasury").status_code == 200
    assert acc.put("/api/treasury", json={"line_id": line["id"], "jy": CUR[0], "jm": CUR[1], "rate_bp": 4000}).status_code == 403
    # a month that has not come yet, or a rate above 100%, is refused
    assert put_rate(client, line, next_month(*CUR), 4000).status_code == 400
    assert put_rate(client, line, CUR, 10_001).status_code == 400


def test_reception_does_not_see_staff_shares_on_an_invoice(client, accounts):
    line, svc, person = setup(client, "پذیرش", percent=30)
    inv = invoice(client, accounts, svc, person, 2_000_000)
    r = client.post("/api/auth/users", json={"username": "treasury-reception2", "password": "Passw0rd1", "role": "receptionist"})
    assert r.status_code == 200
    rec = TestClient(app)
    rec.headers["Authorization"] = "Bearer " + rec.post("/api/auth/login", json={"username": "treasury-reception2",
                                                                                 "password": "Passw0rd1"}).json()["access_token"]
    it = rec.get(f"/api/invoices/{inv['id']}").json()["items"][0]
    assert it["commission_amount"] is None and it["treasury_amount"] is None
    assert client.get(f"/api/invoices/{inv['id']}").json()["items"][0]["commission_amount"] == 600_000


def test_a_line_with_only_a_rate_can_be_deleted(client):
    from app.models import ServiceLine, TreasuryRate

    line = client.post("/api/lines", json={"name": "لاین خزانه حذف"}).json()  # no service, no staff: only a rate
    assert put_rate(client, line, CUR, 4000).status_code == 200
    r = client.delete(f"/api/lines/{line['id']}")
    assert r.status_code == 200 and r.json()["archived"] is False, r.text
    with _db() as db:
        assert db.get(ServiceLine, line["id"]) is None
        assert not db.scalars(select(TreasuryRate).where(TreasuryRate.line_id == line["id"])).all()


def test_rows_from_before_the_staff_share_was_recorded_are_left_alone(client, accounts):
    from app.models import InvoiceItem

    line, svc, person = setup(client, "قدیمی", percent=30)
    at = datetime.combine(PREV_START - timedelta(days=4), time(10))
    inv = invoice(client, accounts, svc, person, 3_000_000, at=at)
    with _db() as db:  # as in invoices of the very first versions: no commission recorded and no commission entry
        it = db.scalar(select(InvoiceItem).where(InvoiceItem.invoice_id == inv["id"]))
        it.commission_percent = it.commission_amount = None
        from app.models import JournalEntry

        for e in db.scalars(select(JournalEntry).where(JournalEntry.ref_type == "commission", JournalEntry.ref_id == inv["id"])):
            db.delete(e)
        db.commit()
    assert put_rate(client, line, PREV, 4500).status_code == 200
    with _db() as db:
        it = db.scalar(select(InvoiceItem).where(InvoiceItem.invoice_id == inv["id"]))
        assert it.commission_amount is None and it.treasury_amount == 0
        from app.models import JournalEntry

        assert not db.scalars(select(JournalEntry).where(JournalEntry.ref_type == "commission", JournalEntry.ref_id == inv["id"])).all()


def test_a_busy_month_is_booked_again_quickly(client, accounts):
    """Other users wait for the database at most 8 seconds (busy timeout): a month of 1,500 invoices is booked again
    well within that."""
    from app.models import Customer
    from app.services import accounting

    line, svc, person = setup(client, "شلوغ", percent=35)
    at = datetime.combine(PREV_START - timedelta(days=5), time(9))
    with _db() as db:
        c = Customer(full_name="مشتری شلوغ", mobile=_mobile())
        db.add(c)
        db.flush()
        for i in range(1500):
            accounting.issue_invoice(db, customer=c, items=[{"service_id": svc["id"], "unit_price": 1_000_000 + i * 7,
                                                             "staff_id": person["id"]}], issued_at=at)
        db.commit()
    t = clock.time()
    assert put_rate(client, line, PREV, 4350).status_code == 200
    assert clock.time() - t < 5
    rep = line_row(month_report(client, PREV), line)
    assert rep["staff_share"] == ledger_5200(line, PREV)
    assert rep["treasury"] + rep["staff_share"] + rep["salon_share"] == rep["revenue"]
    balanced(client)
