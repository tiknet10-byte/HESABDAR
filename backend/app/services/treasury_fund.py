"""The treasury (خزانه) of each service line as a fund for the clinic's expenses of that line (1.23).

Every month a line's treasury grows by the treasury of its service rows (invoices sold, less the ones returned, each
with the treasury it was booked with), of its forfeited deposits and of the previous software's services (as the
staff shares page shows it); it is spent by the expenses paid from it (an expense «از خزانه» of the line, e.g. its
consumables: an ordinary expense of the clinic - it lowers the profit). The balance is carried from month to month and
may go negative (more spent than set aside). The same per staff member inside a line: the treasury of their services
and the expenses entered for them.

Nothing here is an account of its own: the treasury is part of the salon's income (revenue 41xx stays whole), so the
fund is a view over the books. Its balance when the program started (opening) and money spent from it before the program
started (the account balances entered at the start already reflect it) are kept as memo rows (TreasuryMove).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (Deposit, Expense, Invoice, InvoiceItem, JournalEntry, ServiceLine, Staff, TreasuryMove,
                      local_now)
from .jalali import J_MONTHS, jmonth_of, month_range, next_month

MOVE_KINDS = {"opening": "موجودی اول خزانه", "spent_before": "خرج از خزانه پیش از شروع برنامه"}
MAX_RIAL = 10**15


class FundError(ValueError):
    pass


def _label(ym: tuple[int, int]) -> str:
    return f"{J_MONTHS[ym[1] - 1]} {ym[0]}"


def _cells(db: Session) -> dict[tuple, dict]:
    """(line, staff, (jy, jm)) -> {"opening", "accrued", "spent"}: everything that moved a treasury. A line's opening
    balance is its treasury on that day: what the line had before it (the previous software's work, spending before the
    program) is in it, so it is not counted again."""
    from .history_shares import _old, books_start, row_figures

    cells: dict[tuple, dict] = defaultdict(lambda: {"opening": 0, "accrued": 0, "spent": 0})
    moves = list(db.scalars(select(TreasuryMove).where(TreasuryMove.status == "active")))
    cutoff: dict[int | None, datetime] = {}
    for m in moves:
        if m.kind == "opening":
            cutoff[m.line_id] = min(cutoff.get(m.line_id, m.at), m.at)

    def add(line_id: int | None, staff_id: int | None, at: datetime, field: str, amount: int) -> None:
        if line_id in cutoff and at < cutoff[line_id]:
            return  # before the line's opening balance: already in it
        cells[(line_id, staff_id, jmonth_of(at))][field] += amount

    # service rows of invoices: sold in their month, a return (voided after its day) taken back in the return's month
    undo = dict(db.execute(select(JournalEntry.ref_id, func.min(JournalEntry.at)).where(JournalEntry.ref_type == "invoice_void")
                           .group_by(JournalEntry.ref_id)).all())
    for line_id, staff_id, t, issued, status, inv_id in db.execute(
            select(InvoiceItem.line_id, InvoiceItem.staff_id, InvoiceItem.treasury_amount, Invoice.issued_at, Invoice.status,
                   Invoice.id).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
            .where(InvoiceItem.product_id.is_(None), func.coalesce(InvoiceItem.kind, "") != "extra",
                   InvoiceItem.treasury_amount.is_not(None), InvoiceItem.treasury_amount != 0)):
        at_undo = undo.get(inv_id)
        if status != "void" or (at_undo is not None and at_undo != issued):
            add(line_id, staff_id, issued, "accrued", int(t))
        if status == "void" and at_undo is not None and at_undo != issued:
            add(line_id, staff_id, at_undo, "accrued", -int(t))
    # forfeited deposits shared like services (1.22)
    employees = {p.id for p in db.scalars(select(Staff)) if (p.kind or "") == "employee"}
    for line_id, staff_id, t, at in db.execute(select(Deposit.forfeit_line_id, Deposit.staff_id, Deposit.treasury_amount, Deposit.closed_at)
                                               .where(Deposit.status == "forfeited", Deposit.treasury_bp.is_not(None),
                                                      Deposit.treasury_amount != 0)):
        if at is not None and t:
            add(line_id, None if staff_id in employees else staff_id, at, "accrued", int(t))
    # the previous software's services before the program started
    for f in row_figures(db, _old(db, datetime(1900, 1, 1), datetime(2200, 1, 1), books_start(db))):
        if f["treasury"]:
            add(f["line_id"], f["share_staff_id"], f["appt"].start_at, "accrued", f["treasury"])
    # spent: expenses paid from the treasury (voided ones never happened)
    for line_id, staff_id, amount, at in db.execute(select(Expense.line_id, Expense.staff_id, Expense.amount, Expense.spent_at)
                                                    .where(Expense.from_treasury.is_(True), Expense.voided_at.is_(None))):
        add(line_id, staff_id, at, "spent", int(amount))
    for m in moves:
        add(m.line_id, m.staff_id, m.at, "opening" if m.kind == "opening" else "spent", int(m.amount))
    return cells


def _months_between(lo: tuple[int, int], hi: tuple[int, int]) -> list[tuple[int, int]]:
    out, ym = [], lo
    while ym <= hi:
        out.append(ym)
        ym = next_month(*ym)
    return out


def fund(db: Session) -> dict:
    """Every line's treasury month by month (from its first move to this month) and per staff member to date."""
    cells = _cells(db)
    cur = jmonth_of(local_now())
    lines = {ln.id: ln for ln in db.scalars(select(ServiceLine).order_by(ServiceLine.id))}
    staff = {p.id: p.full_name for p in db.scalars(select(Staff))}
    per_line: dict[int, dict] = {}
    for (line_id, staff_id, ym), c in cells.items():
        if not (c["opening"] or c["accrued"] or c["spent"]):
            continue
        lk = line_id or 0
        ln = per_line.setdefault(lk, {"line_id": line_id, "name": lines[line_id].name if line_id in lines else "بدون لاین",
                                      "color": lines[line_id].color if line_id in lines else None,
                                      "months": defaultdict(lambda: {"opening": 0, "accrued": 0, "spent": 0}),
                                      "staff": {}})
        m = ln["months"][ym]
        for k in ("opening", "accrued", "spent"):
            m[k] += c[k]
        sk = staff_id or 0
        s = ln["staff"].setdefault(sk, {"staff_id": staff_id, "name": staff.get(staff_id, "عمومی لاین") if staff_id else "عمومی لاین",
                                        "opening": 0, "accrued": 0, "spent": 0})
        for k in ("opening", "accrued", "spent"):
            s[k] += c[k]
    out_lines = []
    all_months: dict[tuple[int, int], dict] = {}
    for ln in per_line.values():
        ms = sorted(ln["months"])
        rows, bal = [], 0
        for ym in _months_between(ms[0], max(ms[-1], cur)):
            m = ln["months"].get(ym, {"opening": 0, "accrued": 0, "spent": 0})
            start = bal
            bal += m["opening"] + m["accrued"] - m["spent"]
            rows.append({"jy": ym[0], "jm": ym[1], "month": _label(ym), "balance_start": start, **m, "balance_end": bal})
            t = all_months.setdefault(ym, {"jy": ym[0], "jm": ym[1], "month": _label(ym), "opening": 0, "accrued": 0, "spent": 0})
            for k in ("opening", "accrued", "spent"):
                t[k] += m[k]
        this = next((r for r in rows if (r["jy"], r["jm"]) == cur), None)
        staff_rows = sorted(({**s, "balance": s["opening"] + s["accrued"] - s["spent"]} for s in ln["staff"].values()),
                            key=lambda x: (x["staff_id"] is None, -x["accrued"]))
        out_lines.append({"line_id": ln["line_id"], "name": ln["name"], "color": ln["color"], "balance": bal,
                          "month_accrued": this["accrued"] if this else 0, "month_spent": this["spent"] if this else 0,
                          "accrued": sum(r["accrued"] for r in rows), "spent": sum(r["spent"] for r in rows),
                          "opening": sum(r["opening"] for r in rows), "months": rows[::-1], "staff": staff_rows})
    out_lines.sort(key=lambda x: (x["line_id"] is None, -x["accrued"]))
    totals, bal = [], 0
    for ym in sorted(all_months):
        m = all_months[ym]
        m["balance_start"] = bal
        bal += m["opening"] + m["accrued"] - m["spent"]
        m["balance_end"] = bal
        totals.append(m)
    return {"current": list(cur), "balance": bal, "lines": out_lines, "months": totals[::-1],
            "active_lines": [{"id": ln.id, "name": ln.name} for ln in lines.values() if ln.is_active]}


def line_staff_months(db: Session, line_id: int | None) -> dict:
    """One line's treasury month by month per staff member (their services' treasury, the expenses entered for them;
    «عمومی لاین»: spent for the line as a whole), each with its balance carried forward."""
    cells = _cells(db)
    cur = jmonth_of(local_now())
    staff = {p.id: p.full_name for p in db.scalars(select(Staff))}
    mine = {(sid, ym): c for (lid, sid, ym), c in cells.items() if lid == line_id and (c["opening"] or c["accrued"] or c["spent"])}
    if not mine:
        return {"line_id": line_id, "staff": [], "months": []}
    people = sorted({sid for sid, _ in mine}, key=lambda x: (x is None, staff.get(x, "")))
    ms = sorted({ym for _, ym in mine})
    bal = {sid: 0 for sid in people}
    months = []
    for ym in _months_between(ms[0], max(ms[-1], cur)):
        row = {"jy": ym[0], "jm": ym[1], "month": _label(ym), "staff": []}
        for sid in people:
            c = mine.get((sid, ym), {"opening": 0, "accrued": 0, "spent": 0})
            bal[sid] += c["opening"] + c["accrued"] - c["spent"]
            row["staff"].append({"staff_id": sid, **c, "balance_end": bal[sid]})
        months.append(row)
    return {"line_id": line_id, "staff": [{"staff_id": sid, "name": staff.get(sid, "عمومی لاین") if sid else "عمومی لاین",
                                           "balance": bal[sid]} for sid in people], "months": months[::-1]}


def line_balance(db: Session, line_id: int | None, staff_id: int | None = None) -> int:
    """A line's treasury today (or one staff member's inside it)."""
    total = 0
    for (lid, sid, _), c in _cells(db).items():
        if lid == line_id and (staff_id is None or sid == staff_id):
            total += c["opening"] + c["accrued"] - c["spent"]
    return total


# ------------------------------------------------------------------ memo rows: the opening balance, spent before the start
def add_move(db: Session, *, kind: str, line_id: int | None, amount: int, at: datetime | None = None, staff_id: int | None = None,
             category: str = "", description: str = "", user=None) -> TreasuryMove:  # noqa: ANN001
    from .accounting import ensure_not_future
    from .audit import audit, write_lock
    from .history_shares import books_start

    if kind not in MOVE_KINDS:
        raise FundError("نوع نامعتبر است")
    if line_id is None or db.get(ServiceLine, line_id) is None:
        raise FundError("لاین را انتخاب کنید")
    if staff_id is not None:
        person = db.get(Staff, staff_id)
        if person is None:
            raise FundError("پرسنل پیدا نشد")
        if person.line_id and person.line_id != line_id:
            raise FundError("این پرسنل در لاین انتخاب‌شده نیست؛ خزانهٔ همان لاین یا «کل لاین» را انتخاب کنید")
    amount = int(amount)
    if kind == "spent_before" and amount <= 0:
        raise FundError("مبلغ خرج باید مثبت باشد")
    if abs(amount) > MAX_RIAL:
        raise FundError("مبلغ بیش از حد بزرگ است؛ عدد را دوباره نگاه کنید")
    if not amount:
        raise FundError("مبلغ را بنویسید")
    ensure_not_future(at, "تاریخ")
    when = at.replace(tzinfo=None) if at is not None else local_now()  # (a time sent with its zone: as written)
    if kind == "spent_before":
        start = books_start(db)
        if start is not None and when >= start:
            raise FundError("خرجِ بعد از شروع کار با برنامه را در «هزینه‌ها» با تیک «از خزانه» ثبت کنید "
                            "(تا از حساب کم شود و در سود بیاید)")
    write_lock(db)
    m = TreasuryMove(kind=kind, line_id=line_id, staff_id=staff_id, amount=amount, at=when, category=(category or "").strip()[:64],
                     description=(description or "").strip(), created_by=getattr(user, "id", None))
    db.add(m)
    db.flush()
    audit(db, "treasury.move", "treasury_move", m.id, {"kind": kind, "line": line_id, "staff": staff_id, "amount": amount,
                                                       "at": when.isoformat(timespec="minutes")}, user=user)
    return m


def void_move(db: Session, m: TreasuryMove, user=None) -> TreasuryMove:  # noqa: ANN001
    from sqlalchemy import update

    from .audit import audit

    db.flush()
    if m.status != "active" or not db.execute(update(TreasuryMove).where(TreasuryMove.id == m.id, TreasuryMove.status == "active")
                                              .values(status="void", voided_at=local_now())
                                              .execution_options(synchronize_session=False)).rowcount:
        raise FundError("این ردیف قبلاً باطل شده است")
    m.status, m.voided_at = "void", local_now()
    audit(db, "treasury.move_void", "treasury_move", m.id, {"kind": m.kind, "amount": m.amount}, user=user)
    return m


def spending(db: Session, jy: int, jm: int, line_id: int | None = None) -> list[dict]:
    """What was spent from the treasuries in a month: expenses «از خزانه» and memo rows, newest first."""
    from ..models import PaymentAccount

    s, e = month_range(jy, jm)
    lines = {ln.id: ln.name for ln in db.scalars(select(ServiceLine))}
    staff = {p.id: p.full_name for p in db.scalars(select(Staff))}
    accounts = {pa.id: pa.name for pa in db.scalars(select(PaymentAccount))}
    out = []
    q = select(Expense).where(Expense.from_treasury.is_(True), Expense.spent_at >= s, Expense.spent_at < e)
    if line_id is not None:
        q = q.where(Expense.line_id == line_id)
    for x in db.scalars(q):
        out.append({"type": "expense", "id": x.id, "at": x.spent_at.isoformat(timespec="minutes"), "line_id": x.line_id,
                    "line": lines.get(x.line_id), "staff_id": x.staff_id, "staff": staff.get(x.staff_id), "amount": x.amount,
                    "category": x.category, "description": x.description, "account": accounts.get(x.payment_account_id),
                    "void": x.voided_at is not None})
    q = select(TreasuryMove).where(TreasuryMove.at >= s, TreasuryMove.at < e)
    if line_id is not None:
        q = q.where(TreasuryMove.line_id == line_id)
    for m in db.scalars(q):
        out.append({"type": m.kind, "id": m.id, "at": m.at.isoformat(timespec="minutes"), "line_id": m.line_id,
                    "line": lines.get(m.line_id), "staff_id": m.staff_id, "staff": staff.get(m.staff_id), "amount": m.amount,
                    "category": m.category or MOVE_KINDS[m.kind], "description": m.description, "account": None,
                    "void": m.status != "active"})
    out.sort(key=lambda x: x["at"], reverse=True)
    return out
