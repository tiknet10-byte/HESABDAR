"""1.22: a forfeited deposit is shared like a service of its line; advances / settlements of staff and employees (with
voids, duplicates, receipts and the month statement); employees' salaries are an expense; expenses from the books."""
from datetime import date, datetime, time, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.services.jalali import jmonth_of, month_range
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
    return f"0915160{_n[0]:04d}"


def setup(client, name: str, percent: float = 40, price: int = 10_000_000):  # noqa: ANN201
    line = client.post("/api/lines", json={"name": f"لاین پرداخت {name}"}).json()
    svc = client.post("/api/services", json={"line_id": line["id"], "name": f"خدمت پرداخت {name}", "base_price": price}).json()
    person = client.post("/api/staff", json={"full_name": f"پرسنل پرداخت {name}", "line_id": line["id"],
                                             "commission_percent": percent}).json()
    return line, svc, person


def invoice(client, accounts, svc, person, price: int, at: datetime | None = None) -> dict:
    body = {"customer_name": "مشتری پرداخت", "customer_mobile": _mobile(),
            "items": [{"service_id": svc["id"], "unit_price": price, "staff_id": person["id"]}],
            "payments": [{"payment_account_id": accounts["صندوق نقدی"], "amount": price}]}
    if at:
        body["issued_at"] = at.isoformat()
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def deposit(client, accounts, svc, person, amount: int, at: datetime | None = None) -> dict:
    body = {"customer_name": "غایب پرداخت", "customer_mobile": _mobile(), "amount": amount,
            "payment_account_id": accounts["صندوق نقدی"], "service_id": svc["id"], "staff_id": person["id"]}
    if at:
        body["received_at"] = at.isoformat()
    r = client.post("/api/deposits", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def forfeit(client, dep: dict, at: datetime | None = None) -> None:
    if at is None:
        assert client.post(f"/api/deposits/{dep['id']}/close", json={"action": "forfeit"}).status_code == 200
        return
    from app.models import Deposit
    from app.services import accounting

    with _db() as db:  # a forfeit of an earlier day (the page always forfeits now)
        accounting.close_deposit(db, db.get(Deposit, dep["id"]), "forfeit", at=at)
        db.commit()


def report(client, ym) -> dict:  # noqa: ANN001
    r = client.get("/api/reports/staff-shares", params={"jy": ym[0], "jm": ym[1]})
    assert r.status_code == 200, r.text
    return r.json()


def line_row(rep, line) -> dict:  # noqa: ANN001
    return next(x for x in rep["lines"] if x["line_id"] == line["id"])


def staff_row(rep, person) -> dict:  # noqa: ANN001
    return next(x for x in rep["staff"] if x["staff_id"] == person["id"])


def ledger(code_prefix: str, *, line_id=None, staff_id=None, ym=None, natural: int = 1) -> int:  # noqa: ANN001
    """debit - credit (x natural) of the accounts whose code starts with code_prefix."""
    from app.models import JournalEntry, JournalLine, LedgerAccount

    with _db() as db:
        q = (select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
             .join(JournalEntry, JournalEntry.id == JournalLine.entry_id).join(LedgerAccount, LedgerAccount.id == JournalLine.account_id)
             .where(LedgerAccount.code.like(f"{code_prefix}%")))
        if line_id is not None:
            q = q.where(JournalLine.line_id == line_id)
        if staff_id is not None:
            q = q.where(JournalLine.staff_id == staff_id)
        if ym is not None:
            s, e = month_range(*ym)
            q = q.where(JournalEntry.at >= s, JournalEntry.at < e)
        return natural * int(db.scalar(q) or 0)


def balanced(client) -> None:
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"]


def pay(client, person, amount: int, **kw) -> object:  # noqa: ANN001
    return client.post(f"/api/staff/{person['id']}/payout", json={"amount": amount, **kw})


def statement(client, person, ym) -> dict:  # noqa: ANN001
    r = client.get(f"/api/staff/{person['id']}/statement", params={"jy": ym[0], "jm": ym[1]})
    assert r.status_code == 200, r.text
    return r.json()


def user_client(client, name: str, role: str) -> TestClient:
    client.post("/api/auth/users", json={"username": name, "password": "Passw0rd1", "role": role})
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": name, "password": "Passw0rd1"}).json()["access_token"]
    return c


# ---------------------------------------------------------------------------------------------- forfeits
def test_a_forfeit_is_shared_like_a_service_of_its_line(client, accounts):
    line, svc, person = setup(client, "سوخت", percent=40)
    assert client.put("/api/treasury", json={"line_id": line["id"], "jy": CUR[0], "jm": CUR[1], "rate_bp": 4250,
                                             "confirm": True}).status_code == 200
    invoice(client, accounts, svc, person, 10_000_000)
    dep = deposit(client, accounts, svc, person, 2_000_001)
    forfeit(client, dep)
    t = pct_bp(2_000_001, 4250)
    share = round((2_000_001 - t) * 40 / 100)
    rep = report(client, CUR)
    lr, me = line_row(rep, line), staff_row(rep, person)
    assert lr["forfeits"] == 2_000_001 and me["forfeits"] == 2_000_001
    assert lr["treasury"] == 4_250_000 + t
    assert me["staff_share"] == round(5_750_000 * 0.4) + share
    assert lr["revenue"] + lr["forfeits"] == lr["treasury"] + lr["staff_share"] + lr["salon_share"]  # to the Rial
    assert lr["staff_share"] == ledger("5200", line_id=line["id"], ym=CUR)  # the books
    assert me["balance"] == me["staff_share"]  # owed to them on 2200
    # what the deposit keeps (so a later change of the month's rate books it again)
    from app.models import Deposit, JournalEntry

    with _db() as db:
        d = db.get(Deposit, dep["id"])
        assert (d.forfeit_line_id, d.treasury_bp, d.treasury_amount, d.commission_amount) == (line["id"], 4250, t, share)
        booked = db.scalar(select(JournalEntry.at).where(JournalEntry.ref_type == "forfeit_commission", JournalEntry.ref_id == dep["id"]))
        assert d.closed_at == booked  # the same moment as its entries (its month is theirs)
    # its service can't be changed any more (its line and shares are booked)
    other = client.post("/api/services", json={"line_id": line["id"], "name": "خدمت دیگر سوخت", "base_price": 1}).json()
    assert client.post(f"/api/deposits/{dep['id']}/service", params={"service_id": other["id"]}).status_code == 400
    balanced(client)


def test_a_new_treasury_rate_books_the_months_forfeits_again(client, accounts):
    line, svc, person = setup(client, "سوخت ماه قبل", percent=50)
    d10 = datetime.combine(PREV_START - timedelta(days=5), time(11))
    dep = deposit(client, accounts, svc, person, 4_000_000, at=d10 - timedelta(days=3))
    forfeit(client, dep, at=d10)
    assert staff_row(report(client, PREV), person)["staff_share"] == 2_000_000  # no rate yet: half of all of it
    pv = client.post("/api/treasury/preview", json={"line_id": line["id"], "jy": PREV[0], "jm": PREV[1], "rate_bp": 4000}).json()
    assert pv["forfeits"] == 4_000_000 and pv["treasury_after"] == 1_600_000 and pv["forfeits_changed"] == 1
    me = next(x for x in pv["staff"] if x["staff_id"] == person["id"])
    assert (me["before"], me["after"], me["balance_change"]) == (2_000_000, 1_200_000, -800_000)
    assert client.put("/api/treasury", json={"line_id": line["id"], "jy": PREV[0], "jm": PREV[1], "rate_bp": 4000,
                                             "confirm": True}).status_code == 200
    rep = report(client, PREV)
    lr = line_row(rep, line)
    assert (lr["treasury"], lr["staff_share"], lr["salon_share"]) == (1_600_000, 1_200_000, 1_200_000)
    assert ledger("5200", line_id=line["id"], ym=PREV) == 1_200_000 and ledger("5200", line_id=line["id"], ym=CUR) == 0
    # the share stays on the forfeit's own day
    from app.models import JournalEntry

    with _db() as db:
        e = db.scalar(select(JournalEntry).where(JournalEntry.ref_type == "forfeit_commission", JournalEntry.ref_id == dep["id"]))
        assert e.at == d10
    # a month that has only a forfeit keeps the rate it was booked with when an earlier month changes
    line2, svc2, person2 = setup(client, "فقط سوخت", percent=50)
    dep2 = deposit(client, accounts, svc2, person2, 1_000_000)
    assert client.put("/api/treasury", json={"line_id": line2["id"], "jy": PREV[0], "jm": PREV[1], "rate_bp": 3000,
                                             "confirm": True}).status_code == 200
    forfeit(client, dep2)  # this month: 30% (from last month)
    assert line_row(report(client, CUR), line2)["treasury"] == 300_000
    pv2 = client.post("/api/treasury/preview", json={"line_id": line2["id"], "jy": PREV[0], "jm": PREV[1], "rate_bp": 4500}).json()
    assert pv2["later_kept"] == [{"jy": CUR[0], "jm": CUR[1], "bp": 3000}]
    lines = client.get("/api/treasury", params={"jy": CUR[0], "jm": CUR[1]}).json()["lines"]
    assert any(x["line_id"] == line2["id"] for x in lines)
    balanced(client)


def test_a_forfeit_from_before_1_22_stays_the_salons(client, accounts):
    line, svc, person = setup(client, "سوخت قدیمی", percent=50)
    dep = deposit(client, accounts, svc, person, 3_000_000)
    forfeit(client, dep)
    from app.models import Deposit, JournalEntry

    with _db() as db:  # as a forfeit booked by an older version: no shares kept, no share entry
        d = db.get(Deposit, dep["id"])
        d.forfeit_line_id = d.treasury_bp = d.treasury_amount = d.commission_percent = d.commission_amount = None
        for e in db.scalars(select(JournalEntry).where(JournalEntry.ref_type == "forfeit_commission", JournalEntry.ref_id == dep["id"])):
            db.delete(e)
        db.commit()
    assert staff_row(report(client, CUR), person)["staff_share"] == 0
    assert client.put("/api/treasury", json={"line_id": line["id"], "jy": CUR[0], "jm": CUR[1], "rate_bp": 4000,
                                             "confirm": True}).status_code == 200
    lr = line_row(report(client, CUR), line)
    assert (lr["forfeits"], lr["treasury"], lr["staff_share"], lr["salon_share"]) == (3_000_000, 0, 0, 3_000_000)
    balanced(client)


# ---------------------------------------------------------------------------------------------- advances and settlements
def test_advances_settlement_and_a_debt_carried_to_next_month(client, accounts):
    line, svc, person = setup(client, "تسویه", percent=50)
    d_prev = datetime.combine(PREV_START - timedelta(days=10), time(10))
    invoice(client, accounts, svc, person, 8_000_000, at=d_prev)  # earned 4,000,000 last month
    cash = accounts["صندوق نقدی"]
    r = pay(client, person, 1_000_000, kind="advance", payment_account_id=cash, paid_at=(d_prev + timedelta(days=1)).isoformat(),
            description="برداشتی")
    assert r.status_code == 200, r.text
    assert r.json()["payout"]["jy"] == PREV[0] and r.json()["payout"]["jm"] == PREV[1]
    # the settlement of last month, paid at the start of this one
    r = pay(client, person, 3_500_000, kind="settlement", payment_account_id=cash, jy=PREV[0], jm=PREV[1], reference="777001")
    assert r.status_code == 200, r.text
    st = statement(client, person, PREV)
    assert (st["opening"], st["earned"]["services"], st["given"], st["closing"]) == (0, 4_000_000, 1_000_000, 3_000_000)
    assert st["advances"] == 1_000_000 and st["paid_later"] == 3_500_000 and st["remaining"] == -500_000
    cur = statement(client, person, CUR)
    assert cur["opening"] == 3_000_000 and cur["given"] == 3_500_000 and cur["closing"] == -500_000  # owes 500,000
    assert cur["balance_now"] == -500_000
    # this month's work takes the debt: next settlement is what is left
    invoice(client, accounts, svc, person, 2_000_000)
    assert statement(client, person, CUR)["closing"] == 500_000
    rep = report(client, CUR)
    assert staff_row(rep, person)["paid"] == 3_500_000 and staff_row(rep, person)["balance"] == 500_000
    balanced(client)


def test_a_wrong_payout_is_voided_and_duplicates_are_asked(client, accounts):
    line, svc, person = setup(client, "ابطال", percent=50)
    invoice(client, accounts, svc, person, 4_000_000)
    cash = accounts["صندوق نقدی"]
    r = pay(client, person, 1_500_000, kind="advance", payment_account_id=cash)
    assert r.status_code == 200
    pid = r.json()["payout"]["id"]
    # the same amount to the same person a moment later: asked first
    again = pay(client, person, 1_500_000, kind="advance", payment_account_id=cash)
    assert again.status_code == 409
    assert pay(client, person, 1_500_000, kind="advance", payment_account_id=cash, force=True).status_code == 200
    assert statement(client, person, CUR)["closing"] == -1_000_000
    # the same tracking number and amount: asked too
    assert pay(client, person, 200_000, payment_account_id=cash, reference="R-99").status_code == 200
    assert pay(client, person, 200_000, payment_account_id=cash, reference="R-99").status_code == 409
    cash_before = ledger("11")
    v = client.post(f"/api/staff/payouts/{pid}/void", json={"reason": "اشتباه"})
    assert v.status_code == 200, v.text
    assert ledger("11") == cash_before + 1_500_000  # the money is back in the cash box
    assert client.post(f"/api/staff/payouts/{pid}/void", json={}).status_code == 400  # once
    st = statement(client, person, CUR)
    assert st["closing"] == 2_000_000 - 1_500_000 - 200_000 and st["given"] == 1_700_000
    assert [p["status"] for p in st["payouts"]].count("void") == 1
    assert staff_row(report(client, CUR), person)["paid"] == 1_700_000
    # a void needs management; a receptionist can't pay
    acc = user_client(client, "pay-accountant", "accountant")
    rec = user_client(client, "pay-reception", "receptionist")
    p2 = pay(acc, person, 100_000, payment_account_id=cash).json()["payout"]["id"]
    assert acc.post(f"/api/staff/payouts/{p2}/void", json={}).status_code == 403
    assert pay(rec, person, 100_000, payment_account_id=cash).status_code == 403
    assert rec.get(f"/api/staff/{person['id']}/statement").status_code == 403
    # from the owner's own pocket: their capital, and only management may say so
    assert pay(acc, person, 100_000, from_owner=True).status_code == 403
    cap_before = ledger("3100", natural=-1)
    assert pay(client, person, 300_000, from_owner=True, kind="advance").status_code == 200
    assert ledger("3100", natural=-1) == cap_before + 300_000
    # an archived account is refused, and a future date
    assert pay(client, person, 10_000, payment_account_id=cash, paid_at=(datetime.now() + timedelta(days=2)).isoformat()).status_code == 400
    balanced(client)


def test_payouts_from_before_1_22_get_their_rows(client, accounts):
    line, svc, person = setup(client, "قدیمی", percent=50)
    invoice(client, accounts, svc, person, 2_000_000)
    from app.models import JournalEntry, PaymentAccount, Staff, StaffPayout
    from app.services import accounting, staffpay

    with _db() as db:  # the way 1.21 booked it: an entry whose ref_id is the staff id, no row
        p = db.get(Staff, person["id"])
        pa = db.get(PaymentAccount, accounts["صندوق نقدی"])
        e = accounting.post(db, f"پرداخت سهم {p.full_name}", [
            accounting.Leg(accounting.account(db, accounting.STAFF_PAYABLE), debit=600_000, staff_id=p.id, line_id=p.line_id),
            accounting.Leg(accounting.cash_account_for(db, pa), credit=600_000, staff_id=p.id)], "staff_payout", p.id)
        db.commit()
        assert staffpay.backfill_payouts(db) >= 1
        db.commit()
        row = db.scalar(select(StaffPayout).where(StaffPayout.entry_id == e.id))
        assert (row.staff_id, row.amount, row.source, row.payment_account_id) == (p.id, 600_000, "legacy", pa.id)
        assert db.get(JournalEntry, e.id).ref_id == row.id
        assert staffpay.backfill_payouts(db) == 0  # once
        rid = row.id
    assert client.post(f"/api/staff/payouts/{rid}/void", json={}).status_code == 200
    assert statement(client, person, CUR)["closing"] == 1_000_000
    balanced(client)


# ---------------------------------------------------------------------------------------------- receipts
RECEIPT = """بانک ملت
انتقال وجه موفق
از کارت: 6104-33**-****-{src}
به کارت: 6037-99**-****-4321
نام دارنده کارت مقصد: {name}
مبلغ: 2,500,000 ریال
شماره پیگیری: {ref}"""


def test_a_payout_receipt_fills_the_form(client, accounts):
    line, svc, person = setup(client, "رسید", percent=50)
    client.put(f"/api/staff/{person['id']}", json={"full_name": person["full_name"], "line_id": line["id"],
                                                   "commission_percent": 50, "mobile": "۰۹۱۲۷۷۷۸۸۹۹"})
    card = client.post("/api/accounts", json={"kind": "card", "name": "کارت رسید", "card_number": "6104337711118765"})
    assert card.status_code == 200, card.text
    text = RECEIPT.format(src="8765", name=person["full_name"], ref="55001")
    r = client.post("/api/staff/payout-receipt", data={"text": text, "caption": "برداشتی مهر"})
    assert r.status_code == 200, r.text
    out = r.json()
    sg = out["suggest"]
    assert (sg["staff_id"], sg["kind"], sg["amount"], sg["reference"]) == (person["id"], "advance", 2_500_000, "55001")
    assert sg["jm"] == 7 and sg["payment_account_id"] == card.json()["id"]
    assert out["receipt"]["dest_card"] == "4321" and out["via"] == "text"
    # a picture without the assistant: kept (its token), the amount typed by hand
    png = b"\x89PNG\r\n\x1a\n" + b"receipt-picture-" + person["full_name"].encode()
    r = client.post("/api/staff/payout-receipt", files={"file": ("r.png", png, "image/png")},
                    data={"caption": f"تسویه {person['full_name']}"})
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    assert len(tok) == 64 and r.json()["warnings"] and r.json()["suggest"]["staff_id"] == person["id"]
    r = pay(client, person, 2_500_000, kind="advance", payment_account_id=card.json()["id"], receipt_token=tok,
            reference="55001", receipt=out["receipt"], description="برداشتی مهر")
    assert r.status_code == 200, r.text
    payout = r.json()["payout"]
    assert payout["has_image"] and payout["source"] == "receipt"
    img = client.get(f"/api/staff/payouts/{payout['id']}/image")
    assert img.status_code == 200 and img.content == png
    # the same picture again: already booked
    again = client.post("/api/staff/payout-receipt", files={"file": ("r.png", png, "image/png")}, data={"caption": ""}).json()
    assert again["duplicate"]["id"] == payout["id"]
    assert pay(client, person, 1_000_000, payment_account_id=card.json()["id"], receipt_token=tok).status_code == 409
    # the card it went to is now theirs: the next receipt to it is recognised without a name
    nxt = client.post("/api/staff/payout-receipt", data={"text": RECEIPT.format(src="0000", name="", ref="55002"),
                                                         "caption": "تسویه"}).json()
    assert nxt["suggest"]["staff_id"] == person["id"]
    # only for finance; a bad file is refused
    rec = user_client(client, "receipt-reception", "receptionist")
    assert rec.post("/api/staff/payout-receipt", data={"text": text}).status_code == 403
    assert client.post("/api/staff/payout-receipt", files={"file": ("x.exe", b"MZ", "application/x-msdownload")}).status_code == 400
    assert client.post("/api/staff/payout-receipt", data={}).status_code == 400
    assert pay(client, person, 1, payment_account_id=card.json()["id"], receipt_token="../../etc/passwd").status_code == 400


# ---------------------------------------------------------------------------------------------- employees
def test_employees_salary_is_an_expense_with_advances_and_carry(client, accounts):
    r = client.post("/api/staff", json={"full_name": "منشی پرداخت", "kind": "employee", "salary": 150_000_000, "job_title": "منشی",
                                        "line_id": None, "commission_percent": 30, "mobile": "09121112233"})
    assert r.status_code == 200, r.text
    emp = r.json()
    assert emp["commission_percent"] == 0 and emp["line_id"] is None  # never a share of services
    assert all(x["id"] != emp["id"] for x in client.get("/api/staff").json())  # not offered for services
    assert any(x["id"] == emp["id"] for x in client.get("/api/staff", params={"kind": "employee"}).json())
    cash = accounts["صندوق نقدی"]
    exp_before = ledger("51")
    profit_before = client.get("/api/reports/summary").json()["net_profit"]
    assert pay(client, emp, 40_000_000, kind="advance", payment_account_id=cash).status_code == 200  # مساعده
    s = client.post("/api/employees/salaries", json={"jy": CUR[0], "jm": CUR[1], "rows": [{"staff_id": emp["id"], "amount": 150_000_000}]})
    assert s.status_code == 200 and s.json()["ok"], s.text
    assert ledger("51") == exp_before + 150_000_000  # an expense of the clinic
    assert client.get("/api/reports/summary").json()["net_profit"] == profit_before - 150_000_000
    # once a month
    again = client.post("/api/employees/salaries", json={"jy": CUR[0], "jm": CUR[1], "rows": [{"staff_id": emp["id"], "amount": 1}]}).json()
    assert not again["ok"] and again["errors"]
    page = client.get("/api/employees", params={"jy": CUR[0], "jm": CUR[1]}).json()
    me = next(x for x in page["rows"] if x["staff_id"] == emp["id"])
    assert (me["earned"]["salary"], me["advances"], me["closing"]) == (150_000_000, 40_000_000, 110_000_000)
    assert pay(client, emp, 110_000_000, kind="settlement", payment_account_id=cash).status_code == 200
    assert statement(client, emp, CUR)["closing"] == 0
    # the expenses of the month include it, and not on the staff shares page
    month = client.get("/api/expenses", params={"jy": CUR[0], "jm": CUR[1]}).json()
    assert month["salaries"] >= 150_000_000 and any(x["name"] == "حقوق و دستمزد" for x in month["by_category"])
    assert all(x["staff_id"] != emp["id"] for x in report(client, CUR)["staff"])
    # a salary entered wrong: voided, then entered again
    sid = me["salaries"][0]["id"]
    acc = user_client(client, "salary-accountant", "accountant")
    assert acc.post(f"/api/employees/salaries/{sid}/void").status_code == 403
    assert client.post(f"/api/employees/salaries/{sid}/void").status_code == 200
    assert ledger("51") == exp_before
    assert statement(client, emp, CUR)["closing"] == -150_000_000  # paid, salary not entered: they owe it
    # a practitioner can't get a salary; a future month is refused
    line, svc, person = setup(client, "حقوق", percent=50)
    bad = client.post("/api/employees/salaries", json={"jy": CUR[0], "jm": CUR[1], "rows": [{"staff_id": person["id"], "amount": 1}]}).json()
    assert bad["errors"]
    fut = (CUR[0] + 1, CUR[1])
    assert client.post("/api/employees/salaries", json={"jy": fut[0], "jm": fut[1],
                                                         "rows": [{"staff_id": emp["id"], "amount": 1}]}).json()["errors"]
    # a person with money history keeps their kind
    r = client.put(f"/api/staff/{emp['id']}", json={"full_name": "منشی پرداخت", "kind": None, "line_id": line["id"], "commission_percent": 10})
    assert r.status_code == 409
    # the reception sees the employee but not the salary
    rec = user_client(client, "salary-reception", "receptionist")
    row = next(x for x in rec.get("/api/staff", params={"kind": "all"}).json() if x["id"] == emp["id"])
    assert row["salary"] is None
    balanced(client)


def test_who_owes_us_is_an_asset_not_less_owed_to_others(client, accounts):
    line, svc, person = setup(client, "طلب", percent=50)
    line2, svc2, person2 = setup(client, "طلب ۲", percent=50)
    invoice(client, accounts, svc2, person2, 4_000_000)  # we owe person2 2,000,000
    before = client.get("/api/capital/position").json()
    assert pay(client, person, 700_000, kind="advance", payment_account_id=accounts["صندوق نقدی"]).status_code == 200  # owes 700,000
    pos = client.get("/api/capital/position").json()
    adv = next((x["value"] for x in pos["assets"] if x["key"] == "staff_advances"), 0)
    prev_adv = next((x["value"] for x in before["assets"] if x["key"] == "staff_advances"), 0)
    assert adv - prev_adv == 700_000
    assert pos["assets_total"] == sum(x["value"] for x in pos["assets"])
    assert pos["liabilities_total"] == sum(x["value"] for x in pos["liabilities"])
    assert pos["worth"] == before["worth"] and pos["check"] == 0


# ---------------------------------------------------------------------------------------------- expenses
def test_expenses_one_spelling_duplicates_void_and_the_books(client, accounts):
    cash = accounts["صندوق نقدی"]
    before = client.get("/api/expenses").json()
    a = client.post("/api/expenses", json={"category": " پذيرايي ", "amount": 1_200_000, "payment_account_id": cash, "description": "قهوه"})
    assert a.status_code == 200 and a.json()["category"] == "پذیرایی"
    assert client.post("/api/expenses", json={"category": "پذیرایی", "amount": 1_200_000, "payment_account_id": cash}).status_code == 409
    assert client.post("/api/expenses", json={"category": "پذیرایی", "amount": 1_200_000, "payment_account_id": cash,
                                              "force": True}).status_code == 200
    assert client.post("/api/expenses", json={"category": "  ", "amount": 1, "payment_account_id": cash}).status_code == 400
    assert client.post("/api/expenses", json={"category": "x", "amount": 1, "payment_account_id": cash, "staff_id": 999_999}).status_code == 400
    month = client.get("/api/expenses").json()
    assert month["total"] - before["total"] == 2_400_000
    cat = next(x for x in month["by_category"] if x["name"] == "پذیرایی")
    assert cat["value"] >= 2_400_000
    summary = client.get("/api/reports/summary", params={"start": TODAY.isoformat(), "end": TODAY.isoformat()}).json()
    assert summary["expenses"] == ledger("51", ym=None) - _ledger_before_today("51")
    # entered by mistake: voided on its own date, out of the totals and the profit
    profit = client.get("/api/reports/summary").json()["net_profit"]
    assert client.post(f"/api/expenses/{a.json()['id']}/void", json={"reason": "دوبار"}).status_code == 200
    assert client.post(f"/api/expenses/{a.json()['id']}/void", json={}).status_code == 400
    after = client.get("/api/expenses").json()
    assert after["total"] == month["total"] - 1_200_000
    assert next(x for x in after["rows"] if x["id"] == a.json()["id"])["voided_at"]
    assert client.get("/api/reports/summary").json()["net_profit"] == profit + 1_200_000
    found = client.get("/api/expenses", params={"q": "قهوه"}).json()["rows"]
    assert [x["id"] for x in found] == [a.json()["id"]]
    # quick categories: a setting (management)
    cats = client.get("/api/expenses/categories").json()
    assert "پذیرایی" in cats["quick"] and "پورسانت پرسنل" not in cats["quick"]
    assert client.put("/api/expenses/categories", json={"names": ["پذیرايي", "قهوه", "قهوه", ""]}).json()["quick"] == ["پذیرایی", "قهوه"]
    acc = user_client(client, "exp-accountant", "accountant")
    assert acc.put("/api/expenses/categories", json={"names": []}).status_code == 403
    assert acc.post(f"/api/expenses/{a.json()['id']}/void", json={}).status_code == 403
    balanced(client)


def _ledger_before_today(prefix: str) -> int:
    from app.models import JournalEntry, JournalLine, LedgerAccount

    with _db() as db:
        return int(db.scalar(select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
                             .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                             .join(LedgerAccount, LedgerAccount.id == JournalLine.account_id)
                             .where(LedgerAccount.code.like(f"{prefix}%"),
                                    (JournalEntry.at < datetime.combine(TODAY, time.min))
                                    | (JournalEntry.at > datetime.combine(TODAY, time.max)))) or 0)
