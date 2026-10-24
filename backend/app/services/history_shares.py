"""Staff work brought over from the previous software, shared like services here (1.23).

A service row of the previous software («چهره»: imported done appointments, `reports._old_service()`) dated before the
program started (`books_start`) gets, like an invoice row, its line's treasury of that month and the staff member's
share of the rest; the salon's share is the rest. Until its month is booked the figures are worked out live (the
month's treasury rate, today's percents); booking a month freezes them on the rows (line, treasury rate and amount,
percent and share) and posts one entry: Dr 3200 opening balances / Cr 2200 per staff member and line, dated at the
month's last moment - what the business owed its staff for work done before the program. The profit is unchanged
(that revenue was never in these books) and the staff statements and balances are right, so the owner can enter what
was paid to them in those months (payouts «پیش از شروع برنامه»: Dr 2200 / Cr 3200).

A booked month is booked again when its treasury rate changes (treasury.save), a row's staff member is set (relink),
its rows change (an import or its undo, a service turned into a product) or the start day moves.
Rows on or after the start day are not shared: from that day the invoices here hold the work (no double count).
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Appointment, HistoryMonth, Invoice, JournalEntry, Service, ServiceLine, Staff, local_now
from .jalali import J_MONTHS, jmonth_of, month_range

REF = "history_share"
STAFF_NOTE = re.compile(r"پرسنل: (.+?)(?: - |$)")


class HistoryError(ValueError):
    pass


def month_label(jy: int, jm: int) -> str:
    return f"{J_MONTHS[jm - 1]} {jy}"


def _employee(p: Staff | None) -> bool:
    return bool(p) and (p.kind or "") == "employee"


# ------------------------------------------------------------------ when the program started
def books_start(db: Session) -> datetime | None:
    """The day the program started being used (setting «books.start»), by default the day of the first invoice made
    here or of the first opening balance entered. Work and payments before it belong to the previous software."""
    from . import settings_store

    v = settings_store.get(db, "books.start")
    if v:
        try:
            return datetime.fromisoformat(str(v)[:10])
        except ValueError:
            pass
    return default_start(db)


def default_start(db: Session) -> datetime | None:
    firsts = [db.scalar(select(func.min(Invoice.issued_at))),
              db.scalar(select(func.min(JournalEntry.at)).where(JournalEntry.ref_type == "opening_balance"))]
    firsts = [f for f in firsts if f is not None]
    return datetime.combine(min(firsts).date(), datetime.min.time()) if firsts else None


# ------------------------------------------------------------------ the rows
def _old(db: Session, s: datetime, e: datetime, start: datetime | None = None, *, all_rows: bool = False):  # noqa: ANN202
    """The previous software's service rows in [s, e) - only those before the start day (all_rows: all of them)."""
    from .reports import _old_service

    if not all_rows:
        start = start if start is not None else books_start(db)
        if start is not None:
            e = min(e, start)
    if e <= s:
        return []
    return list(db.scalars(select(Appointment).where(_old_service(), Appointment.start_at >= s, Appointment.start_at < e)
                           .order_by(Appointment.start_at, Appointment.id)))


def _stored_percents(db: Session) -> dict[tuple[int, int], dict]:
    return {(h.jy, h.jm): (h.percents or {}) for h in db.scalars(select(HistoryMonth))}


def row_figures(db: Session, rows: list[Appointment], staff: dict[int, Staff] | None = None, rates: dict | None = None,
                percents: dict | None = None, month_percents: dict | None = None) -> list[dict]:
    """Each row's line, revenue, treasury and staff share: a booked (frozen) row as it was booked - with a new percent
    when one is given for its staff member (percents: {staff id: percent}) - and the others worked out live (the
    line of its service, the month's treasury rate, the percent the month was booked with or today's)."""
    from .treasury import commission_of, effective_bp, pct_bp

    staff = staff if staff is not None else {p.id: p for p in db.scalars(select(Staff))}
    svc_line = dict(db.execute(select(Service.id, Service.line_id)).all())
    rates = rates if rates is not None else {}
    month_percents = month_percents if month_percents is not None else _stored_percents(db)
    out = []
    for a in rows:
        net = int(a.quoted_price or 0)
        person = staff.get(a.staff_id)
        sid = a.staff_id if person is not None and not _employee(person) else None
        frozen = a.treasury_bp is not None
        if frozen:
            line_id, bp = a.line_id, a.treasury_bp
        else:
            line_id = svc_line.get(a.service_id)
            key = (line_id, *jmonth_of(a.start_at))
            if key not in rates:
                rates[key] = effective_bp(db, line_id, a.start_at)
            bp = rates[key]
        if sid is None:
            pct = None
        elif percents is not None and sid in percents:
            pct = percents[sid]
        elif frozen:
            pct = a.commission_percent
        else:
            stored = month_percents.get(jmonth_of(a.start_at), {})
            pct = stored.get(str(sid), person.commission_percent)
        t = pct_bp(net, bp)
        out.append({"appt": a, "line_id": line_id, "staff_id": a.staff_id, "share_staff_id": sid, "revenue": net, "bp": bp,
                    "treasury": t, "percent": pct, "commission": commission_of(net - t, pct) if pct else 0, "booked": frozen})
    return out


def history_rows(db: Session, s: datetime, e: datetime) -> list[dict]:
    """The shared rows of [s, e) (for the reports and the treasury)."""
    return row_figures(db, _old(db, s, e))


def line_month(db: Session, line_id: int, jy: int, jm: int) -> list[dict]:
    s, e = month_range(jy, jm)
    return [f for f in history_rows(db, s, e) if f["line_id"] == line_id]


def is_booked(db: Session, jy: int, jm: int) -> bool:
    return db.scalar(select(HistoryMonth.id).where(HistoryMonth.jy == jy, HistoryMonth.jm == jm)) is not None


# ------------------------------------------------------------------ a month in detail
def month_detail(db: Session, jy: int, jm: int, percents: dict[int, float] | None = None) -> dict:
    """A month of the previous software: per staff member and per line, as it is (or would be with these percents)."""
    _check(jy, jm)
    percents = _clean_percents(db, percents)
    start = books_start(db)
    s, e = month_range(jy, jm)
    staff = {p.id: p for p in db.scalars(select(Staff))}
    lines = {ln.id: ln.name for ln in db.scalars(select(ServiceLine))}
    figs = row_figures(db, _old(db, s, e, start), staff, percents=percents)
    hm = db.scalar(select(HistoryMonth).where(HistoryMonth.jy == jy, HistoryMonth.jm == jm))
    per_staff: dict[int, dict] = {}
    per_line: dict[int, dict] = {}
    for f in figs:
        sid = f["staff_id"] or 0
        person = staff.get(f["staff_id"])
        r = per_staff.setdefault(sid, {"staff_id": f["staff_id"], "name": person.full_name if person else "بدون پرسنل",
                                       "employee": _employee(person), "percent": f["percent"], "current_percent":
                                       person.commission_percent if person and not _employee(person) else None,
                                       "rows": 0, "revenue": 0, "treasury": 0, "staff_share": 0})
        r["rows"] += 1
        r["revenue"] += f["revenue"]
        r["treasury"] += f["treasury"]
        r["staff_share"] += f["commission"]
        lk = f["line_id"] or 0
        lr = per_line.setdefault(lk, {"line_id": f["line_id"], "name": lines.get(f["line_id"], "بدون لاین"), "bp": f["bp"],
                                      "rows": 0, "revenue": 0, "treasury": 0, "staff_share": 0})
        lr["rows"] += 1
        lr["revenue"] += f["revenue"]
        lr["treasury"] += f["treasury"]
        lr["staff_share"] += f["commission"]
    for r in per_staff.values():
        r["salon_share"] = r["revenue"] - r["treasury"] - r["staff_share"]
    for r in per_line.values():
        r["salon_share"] = r["revenue"] - r["treasury"] - r["staff_share"]
    totals = {k: sum(f[k2] for f in figs) for k, k2 in (("revenue", "revenue"), ("treasury", "treasury"),
                                                         ("staff_share", "commission"))}
    totals["salon_share"] = totals["revenue"] - totals["treasury"] - totals["staff_share"]
    after = len(_old(db, max(s, start), e, all_rows=True)) if start is not None and start < e else 0
    return {"jy": jy, "jm": jm, "month": month_label(jy, jm), "rows": len(figs), "rows_after_start": after,
            "start": start.date().isoformat() if start else None,
            "staff": sorted(per_staff.values(), key=lambda x: -x["revenue"]),
            "lines": sorted(per_line.values(), key=lambda x: -x["revenue"]), "totals": totals,
            "booked": hm is not None, "booked_at": hm.booked_at.isoformat(timespec="minutes") if hm else None,
            "booked_share": hm.staff_share if hm else None,
            "stale": bool(hm and (hm.rows != len(figs) or hm.staff_share != totals["staff_share"]
                                  or any(not f["booked"] for f in figs)))}


# ------------------------------------------------------------------ booking a month
def _check(jy: int, jm: int) -> None:
    if not (1 <= jm <= 12) or not (1300 <= jy <= 1500):
        raise HistoryError("ماه نامعتبر است")
    if (jy, jm) > jmonth_of(local_now()):
        raise HistoryError("ماه آینده هنوز نیامده است")


def _month_end(jy: int, jm: int) -> datetime:
    _, e = month_range(jy, jm)
    now = local_now()
    return now if now < e else e - timedelta(seconds=1)


def _post(db: Session, hm: HistoryMonth, figures: list[dict], at: datetime) -> None:
    from .accounting import OPENING, STAFF_PAYABLE, Leg, account, post

    per: dict[tuple, int] = defaultdict(int)
    for f in figures:
        if f["share_staff_id"] and f["commission"]:
            per[(f["line_id"], f["share_staff_id"])] += f["commission"]
    legs = []
    for (line_id, staff_id), amount in sorted(per.items(), key=lambda x: (x[0][0] or 0, x[0][1])):
        if amount > 0:
            legs += [Leg(account(db, OPENING), debit=amount, line_id=line_id, staff_id=staff_id),
                     Leg(account(db, STAFF_PAYABLE), credit=amount, line_id=line_id, staff_id=staff_id)]
        elif amount < 0:  # a negative row (a return written as a row): taken back
            legs += [Leg(account(db, STAFF_PAYABLE), debit=-amount, line_id=line_id, staff_id=staff_id),
                     Leg(account(db, OPENING), credit=-amount, line_id=line_id, staff_id=staff_id)]
    hm.entry_id = None
    if legs:
        entry = post(db, f"سهم پرسنل از کارکرد نرم‌افزار قبلی - {month_label(hm.jy, hm.jm)}", legs, REF, hm.id, at=at,
                     check_size=False)
        hm.entry_id = entry.id


def _delete_entries(db: Session, hm: HistoryMonth) -> datetime | None:
    old = list(db.scalars(select(JournalEntry).where(JournalEntry.ref_type == REF, JournalEntry.ref_id == hm.id)))
    at = min((e.at for e in old), default=None)
    for e in old:
        db.delete(e)  # through the ORM: its lines go with it
    db.flush()
    return at


def _unfreeze(a: Appointment) -> None:
    a.line_id = a.treasury_bp = a.treasury_amount = a.commission_percent = a.commission_amount = None


def _clean_percents(db: Session, percents: dict | None) -> dict[int, float] | None:
    if not percents:
        return None
    out = {}
    for k, v in percents.items():
        try:
            sid, pct = int(k), float(v)
        except (TypeError, ValueError) as exc:
            raise HistoryError("درصد پرسنل نامعتبر است") from exc
        person = db.get(Staff, sid)
        if person is None or _employee(person):
            raise HistoryError("درصد فقط برای پرسنل (نه کارمندان) تعیین می‌شود")
        if not (0 <= pct <= 100):
            raise HistoryError(f"درصد {person.full_name} باید بین ۰ تا ۱۰۰ باشد")
        out[sid] = pct
    return out


def book_month(db: Session, jy: int, jm: int, percents: dict | None = None, user=None) -> HistoryMonth | None:  # noqa: ANN001
    """Freeze the month's rows (line, treasury, percent and share) and book what the staff earned. Booked again: the
    rows keep what they were booked with, except the percents given now; rows not frozen yet (a staff member set since,
    rows added by an import) are worked out. A booked month left without rows (an import undone, the start moved) keeps
    its booking with nothing owed, and its percents: when its rows come back it is booked again with them.
    The first booking pins the start day (when it was worked out from the first invoice): an invoice dated earlier later
    must not move rows out of a booked month."""
    from . import settings_store
    from .audit import audit, write_lock

    _check(jy, jm)
    pcts = _clean_percents(db, percents)
    write_lock(db)
    if not _start_setting(db):
        start = books_start(db)
        if start is not None:
            settings_store.set_value(db, "books.start", start.date().isoformat())
    s, e = month_range(jy, jm)
    figures = row_figures(db, _old(db, s, e), percents=pcts)
    hm = db.scalar(select(HistoryMonth).where(HistoryMonth.jy == jy, HistoryMonth.jm == jm))
    if not figures:
        if hm is None:
            raise HistoryError("در این ماه کارکردی از نرم‌افزار قبلی (پیش از شروع کار با برنامه) نیست")
        _delete_entries(db, hm)
        hm.entry_id, hm.rows, hm.revenue, hm.treasury, hm.staff_share = None, 0, 0, 0, 0
        db.flush()
        audit(db, "history.book", "history_month", hm.id, {"month": f"{jy}/{jm:02d}", "rows": 0, "staff_share": 0}, user=user)
        return hm
    for f in figures:
        a = f["appt"]
        a.line_id, a.treasury_bp, a.treasury_amount = f["line_id"], f["bp"], f["treasury"]
        a.commission_percent, a.commission_amount = f["percent"], f["commission"]
    if hm is None:
        hm = HistoryMonth(jy=jy, jm=jm)
        db.add(hm)
        db.flush()
    at = _delete_entries(db, hm) or _month_end(jy, jm)
    hm.rows, hm.revenue = len(figures), sum(f["revenue"] for f in figures)
    hm.treasury, hm.staff_share = sum(f["treasury"] for f in figures), sum(f["commission"] for f in figures)
    used: dict[str, float] = {}
    for f in figures:
        if f["share_staff_id"] and f["percent"] is not None:
            used[str(f["share_staff_id"])] = f["percent"]
    hm.percents = used  # a new dict (a JSON column changed in place is not saved)
    hm.booked_at, hm.booked_by = local_now(), getattr(user, "id", None)
    _post(db, hm, figures, at)
    db.flush()
    audit(db, "history.book", "history_month", hm.id, {"month": f"{jy}/{jm:02d}", "rows": hm.rows, "revenue": hm.revenue,
                                                       "treasury": hm.treasury, "staff_share": hm.staff_share,
                                                       "percents": used}, user=user)
    return hm


def rebook(db: Session, months: set[tuple[int, int]], user=None) -> int:  # noqa: ANN001
    """Book again the months that are booked (after their rows changed); months not booked stay live."""
    n = 0
    for jy, jm in sorted(months):
        if is_booked(db, jy, jm):
            book_month(db, jy, jm, user=user)
            n += 1
    return n


def unbook_month(db: Session, jy: int, jm: int, user=None) -> None:  # noqa: ANN001
    """Undo a month's booking: its entry is removed and its rows are worked out live again."""
    from .audit import audit, write_lock

    write_lock(db)
    hm = db.scalar(select(HistoryMonth).where(HistoryMonth.jy == jy, HistoryMonth.jm == jm))
    if hm is None:
        raise HistoryError("این ماه ثبت نشده است")
    _delete_entries(db, hm)
    s, e = month_range(jy, jm)
    for a in _old(db, s, e, all_rows=True):
        _unfreeze(a)
    audit(db, "history.unbook", "history_month", hm.id, {"month": f"{jy}/{jm:02d}"}, user=user)
    db.delete(hm)
    db.flush()


def rows_changed(db: Session, appointments: list[Appointment] | None = None, months: set | None = None,
                 user=None) -> int:  # noqa: ANN001
    """Rows of the previous software were added, removed or moved: their booked months are booked again."""
    ms = set(months or ())
    ms |= {jmonth_of(a.start_at) for a in appointments or () if a.start_at}
    return rebook(db, ms, user=user) if ms else 0


# ------------------------------------------------------------------ the months, at a glance
def months(db: Session) -> dict:
    from .reports import _old_service

    start = books_start(db)
    booked = {(h.jy, h.jm): h for h in db.scalars(select(HistoryMonth))}
    rows = list(db.scalars(select(Appointment).where(_old_service()).order_by(Appointment.start_at, Appointment.id)))
    by_month: dict[tuple[int, int], list[Appointment]] = defaultdict(list)
    after: dict[tuple[int, int], int] = defaultdict(int)
    for a in rows:
        if start is not None and a.start_at >= start:
            after[jmonth_of(a.start_at)] += 1
        else:
            by_month[jmonth_of(a.start_at)].append(a)
    staff = {p.id: p for p in db.scalars(select(Staff))}
    rates: dict = {}
    mp = {k: (h.percents or {}) for k, h in booked.items()}
    out = []
    for (jy, jm) in sorted(set(by_month) | set(after) | set(booked)):
        rs = by_month.get((jy, jm), [])
        figs = row_figures(db, rs, staff, rates, month_percents=mp)
        h = booked.get((jy, jm))
        share = sum(f["commission"] for f in figs)
        out.append({"jy": jy, "jm": jm, "month": month_label(jy, jm), "rows": len(rs),
                    "revenue": sum(f["revenue"] for f in figs), "treasury": sum(f["treasury"] for f in figs),
                    "staff_share": share, "salon_share": sum(f["revenue"] - f["treasury"] - f["commission"] for f in figs),
                    "without_staff": sum(1 for f in figs if not f["share_staff_id"]),
                    "without_line": sum(1 for f in figs if not f["line_id"]),
                    "rows_after_start": after.get((jy, jm), 0),
                    "booked": h is not None, "booked_at": h.booked_at.isoformat(timespec="minutes") if h else None,
                    "stale": bool(h and (h.rows != len(rs) or h.staff_share != share or any(not f["booked"] for f in figs)))})
    return {"start": start.date().isoformat() if start else None,
            "start_set": bool(_start_setting(db)), "default_start": (d.date().isoformat() if (d := default_start(db)) else None),
            "months": out, "totals": {k: sum(m[k] for m in out) for k in ("rows", "revenue", "treasury", "staff_share", "salon_share")},
            "unmatched": len(staff_names(db)["names"])}


def _start_setting(db: Session) -> str | None:
    from . import settings_store

    return settings_store.get(db, "books.start") or None


# ------------------------------------------------------------------ who did it: staff names not matched at import
def staff_names(db: Session) -> dict:
    """Names written in the previous software that were not matched to a staff member at import (kept in the row's
    notes as «پرسنل: X»), with a suggestion; and lines whose rows have no staff member (the line's only one suggested)."""
    from .reports import _old_service

    people = [p for p in db.scalars(select(Staff).order_by(Staff.id)) if not _employee(p)]
    svc_line = dict(db.execute(select(Service.id, Service.line_id)).all())
    lines = {ln.id: ln.name for ln in db.scalars(select(ServiceLine))}
    names: dict[str, dict] = {}
    per_line: dict[int, dict] = {}
    for a in db.scalars(select(Appointment).where(_old_service(), Appointment.staff_id.is_(None))):
        m = STAFF_NOTE.search(a.notes or "")
        if m:
            name = m.group(1).strip()
            n = names.setdefault(name, {"name": name, "rows": 0, "revenue": 0, "first": a.start_at, "last": a.start_at})
            n["rows"] += 1
            n["revenue"] += int(a.quoted_price or 0)
            n["first"], n["last"] = min(n["first"], a.start_at), max(n["last"], a.start_at)
            continue
        lid = a.line_id if a.treasury_bp is not None else svc_line.get(a.service_id)
        if not lid:
            continue
        r = per_line.setdefault(lid, {"line_id": lid, "line": lines.get(lid), "rows": 0, "revenue": 0})
        r["rows"] += 1
        r["revenue"] += int(a.quoted_price or 0)
    for n in names.values():
        n["suggest"] = suggest_staff(n["name"], people)
        n["first"], n["last"] = n["first"].date().isoformat(), n["last"].date().isoformat()
    for r in per_line.values():
        only = [p for p in people if p.line_id == r["line_id"] and p.is_active]
        r["sole_staff"] = {"id": only[0].id, "name": only[0].full_name} if len(only) == 1 else None
    return {"names": sorted(names.values(), key=lambda x: -x["rows"]), "lines": sorted(per_line.values(), key=lambda x: -x["rows"]),
            "staff": [{"id": p.id, "name": p.full_name, "line_id": p.line_id, "active": p.is_active} for p in people]}


TITLES = re.compile(r"^(?:دکتر|خانم|آقای|آقا|سرکار|استاد|مهندس)\s+")


def plain_name(name: str) -> str:
    from .textutil import normalize_text

    n = normalize_text((name or "").strip())
    while True:
        m = TITLES.match(n)
        if not m:
            return n
        n = n[m.end():].strip()


def suggest_staff(name: str, people: list[Staff]) -> dict | None:
    """The staff member a name written in the previous software most likely is: the same full name (titles like
    «دکتر» / «خانم» left out), or the only one with that first name (and, when a last name is written, the same)."""
    n = plain_name(name)
    if not n:
        return None
    full = [p for p in people if plain_name(p.full_name) == n]
    if len(full) == 1:
        return {"id": full[0].id, "name": full[0].full_name, "why": "همان نام"}
    if full:
        return None
    words = n.split()
    same = [p for p in people if plain_name(p.full_name).split()[:1] == words[:1]]
    if len(same) == 1 and (len(words) == 1 or plain_name(same[0].full_name).split()[-1:] == words[-1:]):
        return {"id": same[0].id, "name": same[0].full_name, "why": "نام کوچک"}
    return None


def relink(db: Session, name: str, staff_id: int, user=None) -> int:  # noqa: ANN001
    """Every row whose staff member was written «name» in the previous software is this staff member's; booked months
    are booked again. Returns how many rows."""
    from .audit import audit, write_lock
    from .reports import _old_service

    person = db.get(Staff, staff_id)
    if person is None or _employee(person):
        raise HistoryError("پرسنل را انتخاب کنید")
    write_lock(db)
    changed: list[Appointment] = []
    for a in db.scalars(select(Appointment).where(_old_service(), Appointment.staff_id.is_(None))):
        m = STAFF_NOTE.search(a.notes or "")
        if m and m.group(1).strip() == (name or "").strip():
            a.staff_id = person.id
            _unfreeze(a)
            changed.append(a)
    if not changed:
        raise HistoryError("ردیفی با این نام پیدا نشد")
    db.flush()
    rows_changed(db, changed, user=user)
    audit(db, "history.relink", "staff", person.id, {"name": name, "rows": len(changed)}, user=user)
    return len(changed)


def assign_line(db: Session, line_id: int, staff_id: int, user=None) -> int:  # noqa: ANN001
    """The rows of a line that have no staff member (and no name written) are this staff member's (a line with one)."""
    from .audit import audit, write_lock
    from .reports import _old_service

    person = db.get(Staff, staff_id)
    if person is None or _employee(person):
        raise HistoryError("پرسنل را انتخاب کنید")
    write_lock(db)
    svc_line = dict(db.execute(select(Service.id, Service.line_id)).all())
    changed: list[Appointment] = []
    for a in db.scalars(select(Appointment).where(_old_service(), Appointment.staff_id.is_(None))):
        lid = a.line_id if a.treasury_bp is not None else svc_line.get(a.service_id)
        if lid == line_id and not STAFF_NOTE.search(a.notes or ""):
            a.staff_id = person.id
            _unfreeze(a)
            changed.append(a)
    if not changed:
        raise HistoryError("ردیفی بدون پرسنل در این لاین نیست")
    db.flush()
    rows_changed(db, changed, user=user)
    audit(db, "history.assign_line", "staff", person.id, {"line": line_id, "rows": len(changed)}, user=user)
    return len(changed)


def set_start(db: Session, day: date | None, user=None) -> int:  # noqa: ANN001
    """Set the day the program started (None: the first invoice / opening balance). The rows that move across it are
    taken out of (or into) the shares; their booked months are booked again. Returns how many months."""
    from . import settings_store
    from .audit import audit, write_lock

    if day is not None and day > local_now().date():
        raise HistoryError("روز شروع نمی‌تواند در آینده باشد")
    write_lock(db)
    old = books_start(db)
    settings_store.set_value(db, "books.start", day.isoformat() if day else "")
    new = books_start(db)
    if new is not None:  # what was entered as «before the program» must stay before it
        from ..models import StaffPayout, TreasuryMove
        from .jalali import to_jalali_str

        last = max([d for d in (
            db.scalar(select(func.max(StaffPayout.paid_at)).where(StaffPayout.before_start.is_(True), StaffPayout.status == "booked")),
            db.scalar(select(func.max(TreasuryMove.at)).where(TreasuryMove.kind == "spent_before", TreasuryMove.status == "active")),
        ) if d is not None], default=None)
        if last is not None and last >= new:
            raise HistoryError(f"پرداخت یا خرجی «پیش از شروع برنامه» با تاریخ {to_jalali_str(last)} ثبت شده است؛ روز شروع باید بعد از آن باشد "
                               "(یا اول آن را باطل کنید)")
    audit(db, "books.start", "setting", "books.start", {"date": day.isoformat() if day else None}, user=user)
    if old == new:
        return 0
    lo, hi = min(x for x in (old, new) if x is not None), max((x for x in (old, new) if x is not None), default=None)
    if old is None or new is None:
        hi = local_now() + timedelta(days=1)
    moved = _old(db, lo, hi, all_rows=True)
    for a in moved:
        if new is not None and a.start_at >= new:
            _unfreeze(a)  # now after the start: the invoices here hold that work
    db.flush()
    return rows_changed(db, moved, user=user)
