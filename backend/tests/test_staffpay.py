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


def test_review_fixes_receipt_owner_dates_links_and_backups(client, accounts):
    """The receipt of someone else is pointed out (and its card not learned for the wrong person); a date with a unit in
    the words never breaks reading; a closed deposit is not linked to an appointment; a booked receipt's picture is
    kept in the database (in every backup), the waiting copy on disk is not needed any more."""
    line, svc, sara = setup(client, "رسید سارا", percent=50)
    negar = client.post("/api/staff", json={"full_name": "نگار رضایی‌فر", "line_id": line["id"], "commission_percent": 50}).json()
    text = RECEIPT.format(src="0000", name="نگار رضایی‌فر", ref="66001").replace("4321", "9876")
    r = client.post("/api/staff/payout-receipt", data={"text": text, "caption": "برداشتی نگار", "staff_id": str(sara["id"])}).json()
    assert r["suggest"]["staff_id"] == negar["id"]  # the form for Sara now warns: it looks like Negar's
    assert pay(client, sara, 2_500_000, kind="advance", payment_account_id=accounts["صندوق نقدی"], receipt=r["receipt"]).status_code == 200
    from app.models import ReceiptImage, Staff

    with _db() as db:
        assert "9876" not in (db.get(Staff, sara["id"]).cards or [])  # not learned for the wrong person
    # dates and odd numbers in the words
    for caption in ("تسویه مهر 1405/07/30 هزار", "برداشتی ۱.۵۰۰.۰۰۰ هزار", "مساعده ۲/۵/۱۴۰۵ م"):
        assert client.post("/api/staff/payout-receipt", data={"text": text, "caption": caption}).status_code == 200
    # a forfeited deposit can't be moved to another appointment (its line and shares are booked)
    dep = deposit(client, accounts, svc, sara, 1_000_000)
    forfeit(client, dep)
    from app.models import Appointment, Deposit

    with _db() as db:
        d = db.get(Deposit, dep["id"])
        a = Appointment(customer_id=d.customer_id, service_id=svc["id"], start_at=datetime.now() + timedelta(days=2))
        db.add(a)
        db.commit()
        aid = a.id
    assert client.post(f"/api/deposits/{dep['id']}/appointment", params={"appointment_id": aid}).status_code == 400
    # the picture of a booked receipt lives in the database
    png = b"\x89PNG\r\n\x1a\n" + b"backup-me"
    tok = client.post("/api/staff/payout-receipt", files={"file": ("r.png", png, "image/png")}, data={"caption": ""}).json()["token"]
    p = pay(client, sara, 700_000, payment_account_id=accounts["صندوق نقدی"], receipt_token=tok).json()["payout"]
    from app.services import payout_receipt

    for f in payout_receipt.receipts_dir().iterdir():
        f.unlink()
    with _db() as db:
        assert db.get(ReceiptImage, tok).data == png
    img = client.get(f"/api/staff/payouts/{p['id']}/image")
    assert img.status_code == 200 and img.content == png and img.headers["x-content-type-options"] == "nosniff"
    # a token whose picture is gone (or never was) is refused
    assert pay(client, sara, 1, payment_account_id=accounts["صندوق نقدی"], receipt_token="a" * 64).status_code == 400


def test_a_new_forfeit_keeps_the_line_it_was_booked_with(client, accounts):
    """A forfeit booked with no known line (4800) stays there when its staff member later gets a line: its shares were
    worked out without a line, so moving only its income would make the line's report disagree with the books."""
    person = client.post("/api/staff", json={"full_name": "پرسنل بی‌لاین سوخت", "commission_percent": 40}).json()
    body = {"customer_name": "غایب بی‌لاین", "customer_mobile": _mobile(), "amount": 1_000_000,
            "payment_account_id": accounts["صندوق نقدی"], "staff_id": person["id"]}
    dep = client.post("/api/deposits", json=body).json()
    forfeit(client, dep)
    line = client.post("/api/lines", json={"name": "لاین بعدی سوخت"}).json()
    client.put(f"/api/staff/{person['id']}", json={"full_name": person["full_name"], "line_id": line["id"], "commission_percent": 40})
    from app.services.accounting import reclass_forfeits

    with _db() as db:
        reclass_forfeits(db)
        db.commit()
    assert ledger("48", line_id=line["id"]) == 0  # still on 4800 (no line), with its share
    assert ledger("5200", staff_id=person["id"]) == 400_000
    balanced(client)


# ---------------------------------------------------------------------------------------------- review of 1.22 (2)
def test_receipt_cards_failures_and_words_read_carefully(client, accounts):
    from app.services.payout_receipt import _caption_amount, read_caption, read_text

    # the salon's own card on a bank SMS is where the money came from, not where it went
    card = client.post("/api/accounts", json={"kind": "card", "name": "کارت پیامک سالن", "card_number": "6219861900005566"}).json()
    sms = "بانک سامان\nکارت:5566\nبرداشت:5,000,000\n1405/07/20 10:45\nمانده:12,000,000"
    r = client.post("/api/staff/payout-receipt", data={"text": sms, "caption": ""}).json()
    assert r["receipt"]["dest_card"] is None and r["receipt"]["source_card"] == "5566"
    assert r["suggest"]["payment_account_id"] == card["id"]
    # labels with a half-space, short masks, a date after the card on the same line
    assert read_text("کارت پرداخت‌کننده: 6104-33**-****-1111\nکارت دریافت‌کننده: 6037-99**-****-2222")["dest_card"] == "2222"
    assert read_text("به کارت 6037***4321 مبلغ 1,000,000 ریال")["dest_card"] == "4321"
    assert read_text("به کارت 6037-4321")["dest_card"] == "4321"
    assert read_text("به کارت 6037-99**-****-4321 1405/07/20")["dest_card"] == "4321"
    # transfers that did not go through
    for status in ("تراکنش نا‌موفق", "وضعیت: نامشخص", "تراکنش رد شد", "انتقال انجام نشد", "موجودی کافی نیست"):
        assert read_text(f"انتقال وجه\nمبلغ: 1,000,000 ریال\n{status}")["success"] is False, status
    # amounts in the words: Persian decimal sign, dots between thousands, tracking / card numbers, very long numbers
    assert 25_000_000 in _caption_amount("مساعده ۲٫۵ میلیون")
    assert 25_000_000 in _caption_amount("مساعده ۲.۵۰۰ هزار تومان")
    assert _caption_amount("تسویه سارا پیگیری ۱۲۳۴۵۶۷۸") == [] and _caption_amount("به کارت ۶۰۳۷۹۹۱۲۳۴۵۶۷۸۹۰") == []
    assert _caption_amount("مساعده " + "9" * 400) == []
    assert _caption_amount("تسویه حساب ۵ میلیون") == [50_000_000]
    # month names are whole words («دیروز», «احمدی», «مهرناز» are not months)
    assert read_caption("برداشتی دیروز")["month"] is None and read_caption("تسویه خانم احمدی")["month"] is None
    assert read_caption("برداشتی مهرناز")["month"] is None and read_caption("تسویه مهر")["month"][1] == 7
    # a settlement read without words, early in a month: last month's (the form's own kind is used)
    early = month_range(*CUR)[0] + timedelta(days=1, hours=10)
    rec = f"انتقال وجه موفق\nمبلغ: 1,000,000 ریال\n{to_j(early)}"
    s = client.post("/api/staff/payout-receipt", data={"text": rec, "caption": "", "kind": "settlement"}).json()["suggest"]
    assert (s["kind"], s["jy"], s["jm"], s["month_from"]) == ("settlement", PREV[0], PREV[1], "date")
    s = client.post("/api/staff/payout-receipt", data={"text": rec, "caption": "", "kind": "advance"}).json()["suggest"]
    assert (s["kind"], s["jy"], s["jm"]) == ("advance", CUR[0], CUR[1])


def to_j(d: datetime) -> str:
    from app.services.jalali import gregorian_to_jalali

    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    return f"{jy}/{jm:02d}/{jd:02d} {d.hour:02d}:{d.minute:02d}"


def test_cards_are_learned_only_for_their_owner_and_forgotten_on_void(client, accounts):
    line, svc, ahmadi = setup(client, "کارت مشترک", percent=50)
    client.put(f"/api/staff/{ahmadi['id']}", json={"full_name": "سارا احمدی‌نیا", "line_id": line["id"], "commission_percent": 50})
    karimi = client.post("/api/staff", json={"full_name": "سارا کریمی‌پور", "line_id": line["id"], "commission_percent": 50}).json()
    text = RECEIPT.format(src="0000", name="سارا احمدی‌نیا", ref="77001").replace("4321", "7788")
    r = client.post("/api/staff/payout-receipt", data={"text": text, "caption": "", "staff_id": str(karimi["id"])}).json()
    assert r["suggest"]["staff_id"] == ahmadi["id"]  # the form for Karimi warns: it is Ahmadi's
    p = pay(client, karimi, 500_000, kind="advance", payment_account_id=accounts["صندوق نقدی"], receipt=r["receipt"]).json()["payout"]
    from app.models import Staff

    with _db() as db:
        assert "7788" not in (db.get(Staff, karimi["id"]).cards or [])  # a shared first name is not enough
    # booked for the right person: learned; voided: forgotten
    p2 = pay(client, ahmadi, 600_000, kind="advance", payment_account_id=accounts["صندوق نقدی"], receipt=r["receipt"]).json()["payout"]
    with _db() as db:
        assert "7788" in (db.get(Staff, ahmadi["id"]).cards or [])
    assert client.post(f"/api/staff/payouts/{p2['id']}/void", json={}).status_code == 200
    with _db() as db:
        assert "7788" not in (db.get(Staff, ahmadi["id"]).cards or [])
    assert client.post(f"/api/staff/payouts/{p['id']}/void", json={}).status_code == 200
    # a card learned for one person and another person's whole name printed: nobody is guessed
    pay(client, karimi, 700_000, kind="advance", payment_account_id=accounts["صندوق نقدی"],
        receipt={"dest_card": "9911", "dest_name": None})
    named = RECEIPT.format(src="0000", name="سارا احمدی‌نیا", ref="77002").replace("4321", "9911")
    s = client.post("/api/staff/payout-receipt", data={"text": named, "caption": ""}).json()["suggest"]
    assert s["staff_id"] is None


def test_odd_answers_from_the_assistant_never_break_reading(client, accounts, monkeypatch):
    from app.ai import claude

    answers = []
    monkeypatch.setattr(claude, "available", lambda: True)
    monkeypatch.setattr(claude, "vision_extract", lambda *a, **k: answers[-1])
    png = b"\x89PNG\r\n\x1a\n" + b"assistant-answers"
    def read(answer: dict) -> dict:  # noqa: E306
        answers.append(answer)
        r = client.post("/api/staff/payout-receipt", files={"file": ("a.png", png, "image/png")}, data={"caption": ""})
        assert r.status_code == 200, r.text
        return r.json()
    r = read({"amount_rial": 1_000_000, "dest_name": ["سارا", "احمدی"], "description": {"x": 1}, "raw_text": ""})
    assert r["receipt"]["dest_name"] is None and r["receipt"]["description"] is None
    assert read({"amount_rial": True, "raw_text": ""})["receipt"]["amount"] is None
    assert read({"amount_rial": -5_000_000, "raw_text": ""})["receipt"]["amount"] is None
    assert read({"amount_rial": 1_000, "status_success": "false", "raw_text": ""})["failed"] is True
    # Toman not converted by the assistant, while the text says تومان: what is written wins, with a warning
    r = read({"amount_rial": 500_000, "raw_text": "مبلغ: 500,000 تومان"})
    assert r["receipt"]["amount"] == 5_000_000 and any("ریال یا تومان" in w for w in r["warnings"])
    # the payer's card given by the assistant is never the receiver's
    r = read({"amount_rial": 1_000_000, "source_card_last4": "8765", "dest_card_last4": None, "raw_text": "کارت:8765 مبلغ: 1,000,000 ریال"})
    assert r["receipt"]["dest_card"] is None and r["receipt"]["source_card"] == "8765"


def test_absurd_amounts_and_old_data_after_the_upgrade(client, accounts):
    line, svc, person = setup(client, "سقف مبلغ", percent=50)
    cash = accounts["صندوق نقدی"]
    assert pay(client, person, 10**16, payment_account_id=cash).status_code == 400
    assert pay(client, person, 10**20, payment_account_id=cash).status_code == 400
    assert client.post("/api/expenses", json={"category": "سایر", "amount": 10**20, "payment_account_id": cash}).status_code == 400
    emp = client.post("/api/staff", json={"full_name": "کارمند سقف", "kind": "employee", "salary": 10**20}).status_code
    assert emp == 400
    # a phone saved by an older version that is not a mobile: the person can still be edited / deactivated
    from app.models import Staff

    with _db() as db:
        db.get(Staff, person["id"]).mobile = "05138001122"
        db.commit()
    r = client.put(f"/api/staff/{person['id']}", json={"full_name": person["full_name"], "mobile": "05138001122", "line_id": line["id"],
                                                       "commission_percent": 50, "is_active": False})
    assert r.status_code == 200, r.text
    # a payout made before 1.22 early in a month while still owed from the month before: last month's settlement
    d_prev = datetime.combine(PREV_START - timedelta(days=8), time(10))
    invoice(client, accounts, svc, person, 4_000_000, at=d_prev)
    from app.models import JournalEntry, PaymentAccount, StaffPayout
    from app.services import accounting, staffpay

    early = month_range(*CUR)[0] + timedelta(days=1, hours=9)
    with _db() as db:
        p = db.get(Staff, person["id"])
        e = accounting.post(db, f"پرداخت سهم {p.full_name}", [
            accounting.Leg(accounting.account(db, accounting.STAFF_PAYABLE), debit=1_000_000, staff_id=p.id),
            accounting.Leg(accounting.cash_account_for(db, db.get(PaymentAccount, cash)), credit=1_000_000, staff_id=p.id)],
            "staff_payout", p.id, at=early)
        db.commit()
        staffpay.backfill_payouts(db)
        db.commit()
        row = db.scalar(select(StaffPayout).where(StaffPayout.entry_id == e.id))
        assert (row.jy, row.jm) == PREV
        assert db.get(JournalEntry, e.id).ref_id == row.id
    st = statement(client, person, PREV)
    assert st["paid_later"] == 1_000_000 and st["remaining"] == 1_000_000


def test_paid_from_the_owners_pocket_shows_on_the_capital_page(client, accounts):
    line, svc, person = setup(client, "جیب مدیر", percent=50)
    before = client.get("/api/capital/position").json()
    put_in = lambda p: next(x["value"] for x in p["flow"] if x["key"] == "put_in")  # noqa: E731
    r = pay(client, person, 1_500_000, kind="advance", from_owner=True)
    assert r.status_code == 200
    pos = client.get("/api/capital/position").json()
    assert put_in(pos) == put_in(before) + 1_500_000
    assert not any(x["key"] == "other" and x["value"] == 1_500_000 for x in pos["flow"])
    assert any(m["kind"] == "staff" and m["customer"] == person["full_name"] for m in pos["moves"])
    assert any(m["kind"] == "staff" for m in client.get("/api/capital/moves").json())
    assert client.post(f"/api/staff/payouts/{r.json()['payout']['id']}/void", json={}).status_code == 200
    assert put_in(client.get("/api/capital/position").json()) == put_in(before)


# ---------------------------------------------------------------------------------------------- review of the fixes
def test_fixes_of_the_fixes(client, accounts, monkeypatch):
    from app.ai import claude
    from app.services.payout_receipt import DEST_LABELS, _caption_amount, _labelled_card, read_caption

    # the assistant's right amount is kept when the unit is not written right after the number
    monkeypatch.setattr(claude, "available", lambda: True)
    answer = {"amount_rial": 5_000_000, "raw_text": "کارت به کارت موفق\nمبلغ: 500,000\nپانصد هزار تومان"}
    monkeypatch.setattr(claude, "vision_extract", lambda *a, **k: answer)
    r = client.post("/api/staff/payout-receipt", files={"file": ("u.png", b"\x89PNG\r\n\x1a\nunit-apart", "image/png")},
                    data={"caption": ""}).json()
    assert r["receipt"]["amount"] == 5_000_000 and r["needs_check"] is True
    # a short card followed by a date or an amount on the same line
    for text in ("به کارت ****4321 1405/07/20 10:45", "به کارت 4321 1405/07/20", "به کارت 6037***4321 1,000,000 ریال",
                 "به کارت 6037***4321 1405/07/20", "به کارت 6037 9912 3456 4321 1405/07/20", "به کارت 6037 99** **** 4321 مبلغ"):
        assert _labelled_card(text, DEST_LABELS) == "4321", text
    # «شهریورماه»; amounts after «کارت به کارت» or inside «یکدفعه»
    assert read_caption("تسویه شهریورماه")["month"][1] == 6 and read_caption("تسویه مهرناز")["month"] is None
    assert _caption_amount("کارت به کارت ۵ میلیون برداشتی سارا") == [50_000_000]
    assert _caption_amount("مساعده یکدفعه ۳ میلیون") == [30_000_000]


def test_an_absurd_amount_from_before_the_limit_can_still_be_voided(client, accounts):
    from app.models import Expense, PaymentAccount
    from app.services import accounting

    with _db() as db:  # entered by an older version, before amounts had a limit
        pa = db.get(PaymentAccount, accounts["صندوق نقدی"])
        exp = Expense(category="سایر", amount=2 * 10**15, payment_account_id=pa.id, spent_at=datetime.now() - timedelta(hours=1))
        db.add(exp)
        db.flush()
        accounting.post(db, "هزینه: سایر", [accounting.Leg(accounting.expense_account(db, "سایر"), debit=exp.amount),
                                            accounting.Leg(accounting.cash_account_for(db, pa), credit=exp.amount)],
                        "expense", exp.id, at=exp.spent_at, check_size=False)
        db.commit()
        eid = exp.id
    assert client.post("/api/expenses", json={"category": "سایر", "amount": 2 * 10**15, "payment_account_id": accounts["صندوق نقدی"]}).status_code == 400
    assert client.post(f"/api/expenses/{eid}/void", json={"reason": "عدد اشتباه"}).status_code == 200
    balanced(client)


def test_old_payouts_count_for_the_month_before_only_up_to_what_was_owed(client, accounts):
    line, svc, person = setup(client, "قدیمی سقف", percent=50)
    invoice(client, accounts, svc, person, 2_000_000, at=datetime.combine(PREV_START - timedelta(days=8), time(10)))  # owed 1,000,000
    from app.models import PaymentAccount, Staff, StaffPayout
    from app.services import accounting, staffpay

    start = month_range(*CUR)[0]
    with _db() as db:
        p = db.get(Staff, person["id"])
        pa = db.get(PaymentAccount, accounts["صندوق نقدی"])
        ids = []
        for amount, day in ((1_000_000, 1), (3_000_000, 8)):
            e = accounting.post(db, f"پرداخت سهم {p.full_name}", [
                accounting.Leg(accounting.account(db, accounting.STAFF_PAYABLE), debit=amount, staff_id=p.id),
                accounting.Leg(accounting.cash_account_for(db, pa), credit=amount, staff_id=p.id)],
                "staff_payout", p.id, at=start + timedelta(days=day, hours=9))
            ids.append(e.id)
        db.commit()
        staffpay.backfill_payouts(db)
        db.commit()
        months = [(r.jy, r.jm) for r in (db.scalar(select(StaffPayout).where(StaffPayout.entry_id == i)) for i in ids)]
    assert months == [PREV, CUR]
    assert statement(client, person, PREV)["remaining"] == 0
