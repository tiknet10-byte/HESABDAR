"""Appointment scheduling: free-slot search and strict validation of every booking.

A time is bookable only when ALL of these hold (the server enforces them; the UI mirrors them):
  * it is not in the past;
  * it is a working day and inside working hours (manager may override this one explicitly);
  * the service line still has capacity (= number of active staff in that line, at least 1);
  * the chosen staff member is free (also across other lines);
  * the customer has no other appointment at the same time.
Appointments that are booked OR already done occupy their time; cancelled / no-show free it, and so does an
appointment settled earlier than its booked day (its reserved time is released for other customers).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Appointment, Service, Staff, local_now
from . import settings_store


def _hm(value: str, default: time) -> time:
    try:
        h, m = (int(x) for x in str(value).split(":")[:2])
        return time(h, m)
    except (ValueError, TypeError):
        return default


def appointment_minutes(db: Session, a: Appointment, cache: dict[int, int] | None = None) -> int:
    return a.duration_minutes or _duration(db, a.service_id, cache if cache is not None else {})


def _duration(db: Session, service_id: int | None, cache: dict[int, int]) -> int:
    if not service_id:
        return 60
    if service_id not in cache:
        s = db.get(Service, service_id)
        cache[service_id] = (s.duration_minutes if s and s.duration_minutes else 60)
    return cache[service_id]


OCCUPYING = ("booked", "done")  # statuses that keep their time slot taken
PAST_GRACE = timedelta(minutes=5)


class _Calendar:
    """Booked appointments + line capacity for one service, used to test candidate times."""

    def __init__(self, db: Session, svc: Service, start: datetime, end: datetime, exclude_id: int | None = None):
        self.line_staff = [p.id for p in db.scalars(select(Staff).where(Staff.is_active.is_(True), Staff.line_id == svc.line_id))]
        self.capacity = max(1, len(self.line_staff))
        self.line_services = set(db.scalars(select(Service.id).where(Service.line_id == svc.line_id)))
        cache: dict[int, int] = {}
        # an appointment done earlier than booked (original_start_at set) is history: it holds no time any more
        # an appointment whose hour is not known yet holds no slot either (it is listed for the day, marked "time unknown")
        q = select(Appointment).where(Appointment.status.in_(OCCUPYING), Appointment.original_start_at.is_(None),
                                      Appointment.time_unknown.is_not(True),
                                      Appointment.start_at >= start - timedelta(days=1), Appointment.start_at <= end)
        if exclude_id:
            q = q.where(Appointment.id != exclude_id)
        self.intervals = [(a.start_at, a.start_at + timedelta(minutes=appointment_minutes(db, a, cache)), a) for a in db.scalars(q)]

    def free_staff(self, t: datetime, t_end: datetime, staff_id: int | None, customer_id: int | None = None) -> tuple[bool, int | None]:
        return self.why_busy(t, t_end, staff_id, customer_id)[0] is None, self._chosen

    def why_busy(self, t: datetime, t_end: datetime, staff_id: int | None, customer_id: int | None = None) -> tuple[str | None, str]:
        """(reason code or None, message)."""
        self._chosen = staff_id
        overlapping = [a for s, e, a in self.intervals if s < t_end and e > t]
        if customer_id and any(a.customer_id == customer_id for a in overlapping):
            return "customer_busy", "این مشتری در همین زمان نوبت دیگری دارد"
        busy = {a.staff_id for a in overlapping if a.staff_id}
        if staff_id and staff_id in busy:
            return "staff_busy", "پرسنل انتخاب‌شده در این زمان نوبت دیگری دارد"
        if len([a for a in overlapping if a.service_id in self.line_services]) >= self.capacity:
            return "full", "ظرفیت این لاین در این زمان پر است"
        if not staff_id and self.line_staff:
            free = [p for p in self.line_staff if p not in busy]
            if not free:
                return "full", "همه پرسنل این لاین در این زمان مشغول‌اند"
            self._chosen = free[0]
        return None, ""


STEP = timedelta(minutes=5)  # granularity used to search for the next free start time


def _hours(db: Session) -> tuple[time, time, timedelta, set]:
    return (_hm(settings_store.get(db, "booking.open"), time(10, 0)), _hm(settings_store.get(db, "booking.close"), time(20, 0)),
            STEP, set(settings_store.get(db, "booking.days_off") or []))


def find_slots(db: Session, service_id: int, staff_id: int | None = None, after: datetime | None = None,
               count: int = 6, max_days: int = 60, duration_minutes: int | None = None) -> list[dict]:
    """Free start times. Consecutive suggestions are one service-duration apart (each service has its own length)."""
    svc = db.get(Service, service_id)
    if svc is None:
        return []
    duration = timedelta(minutes=duration_minutes or svc.duration_minutes or 60)
    open_t, close_t, step, days_off = _hours(db)
    start = (after or local_now()).replace(second=0, microsecond=0)
    minutes = start.hour * 60 + start.minute
    # start searching at the next quarter hour (tidy times); later candidates move in 5-minute steps
    start = start.replace(hour=0, minute=0) + timedelta(minutes=((minutes + 14) // 15) * 15)
    horizon = start + timedelta(days=max_days)
    cal = _Calendar(db, svc, start, horizon + timedelta(days=1))

    out: list[dict] = []
    day = start.date()
    while day <= horizon.date() and len(out) < count:
        if day.weekday() not in days_off:  # Python weekday: Mon=0 ... Fri=4
            t = max(datetime.combine(day, open_t), start)
            end_of_day = datetime.combine(day, close_t)
            while t + duration <= end_of_day and len(out) < count:
                ok, chosen = cal.free_staff(t, t + duration, staff_id)
                if ok:
                    out.append({"start_at": t.isoformat(timespec="minutes"), "end_at": (t + duration).isoformat(timespec="minutes"),
                                "staff_id": chosen})
                    t += duration
                else:
                    t += step
        day += timedelta(days=1)
    return out


def validate_slot(db: Session, service_id: int | None, staff_id: int | None, start_at: datetime, *,
                  duration_minutes: int | None = None, exclude_id: int | None = None, customer_id: int | None = None,
                  allow_outside_hours: bool = False, now: datetime | None = None) -> list[dict]:
    """All problems with booking this time; empty list = bookable. Each item: {code, message, overridable}."""
    errors: list[dict] = []
    svc = db.get(Service, service_id) if service_id else None
    if svc is None:
        return [{"code": "no_service", "message": "خدمت نوبت را انتخاب کنید", "overridable": False}]
    start_at = start_at.replace(second=0, microsecond=0)
    end = start_at + timedelta(minutes=duration_minutes or svc.duration_minutes or 60)
    if start_at < (now or local_now()) - PAST_GRACE:
        errors.append({"code": "past", "message": "زمان انتخاب‌شده گذشته است؛ نوبت فقط برای زمان‌های آینده ثبت می‌شود",
                       "overridable": False})
    open_t, close_t, _, days_off = _hours(db)
    if not allow_outside_hours:
        if start_at.weekday() in days_off:
            errors.append({"code": "closed_day", "message": "این روز تعطیل سالن است", "overridable": True})
        elif start_at.time() < open_t or end.time() > close_t or end.date() != start_at.date():
            errors.append({"code": "outside_hours", "message": f"خارج از ساعت کاری ({open_t:%H:%M} تا {close_t:%H:%M})",
                           "overridable": True})
    code, msg = _Calendar(db, svc, start_at, end, exclude_id).why_busy(start_at, end, staff_id, customer_id)
    if code:
        errors.append({"code": code, "message": msg, "overridable": False})
    return errors


def check_slot(db: Session, service_id: int | None, staff_id: int | None, start_at: datetime,
               exclude_id: int | None = None, duration_minutes: int | None = None) -> str | None:
    """Backwards-compatible: first problem as text, or None."""
    errs = validate_slot(db, service_id, staff_id, start_at, duration_minutes=duration_minutes, exclude_id=exclude_id)
    return errs[0]["message"] if errs else None


def line_calendar(db: Session, line_id: int, start: date, days: int = 182, now: datetime | None = None) -> dict:
    """Day-by-day occupancy of one service line, for the booking calendar.

    status per day: past | closed | empty | partial | full. "full" means not even the line's shortest service
    fits anywhere in the working hours any more (taking line capacity and the staff's other bookings into account).
    """
    now = now or local_now()
    services = list(db.scalars(select(Service).where(Service.line_id == line_id, Service.is_active.is_(True))))
    open_t, close_t, step, days_off = _hours(db)
    end = start + timedelta(days=days)
    out = {"line_id": line_id, "open": open_t.strftime("%H:%M"), "close": close_t.strftime("%H:%M"), "days_off": sorted(days_off),
           "capacity": 0, "min_duration": 0, "days": []}
    if not services:
        return out
    min_dur = timedelta(minutes=min(s.duration_minutes or 60 for s in services))
    cal = _Calendar(db, services[0], datetime.combine(start, time()), datetime.combine(end, time()))
    out["capacity"], out["min_duration"] = cal.capacity, int(min_dur.total_seconds() // 60)
    by_day: dict[date, list] = {}
    for iv in cal.intervals:
        by_day.setdefault(iv[0].date(), []).append(iv)
    # appointments whose hour is unknown (e.g. from the previous software): counted per day, no slot held
    unknown: dict[date, int] = {}
    line_staff = set(cal.line_staff) | set(db.scalars(select(Staff.id).where(Staff.line_id == line_id)))
    for a in db.scalars(select(Appointment).where(Appointment.status == "booked", Appointment.time_unknown.is_(True),
                                                  Appointment.start_at >= datetime.combine(start, time()),
                                                  Appointment.start_at < datetime.combine(end, time()))):
        if a.service_id in cal.line_services or (a.service_id is None and a.staff_id in line_staff):
            unknown[a.start_at.date()] = unknown.get(a.start_at.date(), 0) + 1
    day_minutes = max(1, (datetime.combine(start, close_t) - datetime.combine(start, open_t)).total_seconds() / 60) * cal.capacity
    for i in range(days):
        d = start + timedelta(days=i)
        cal.intervals = by_day.get(d, []) + [iv for iv in by_day.get(d - timedelta(days=1), []) if iv[1].date() >= d]
        mine = [(s, e) for s, e, a in cal.intervals if a.service_id in cal.line_services and s.date() == d]
        booked = sum((e - s).total_seconds() / 60 for s, e in mine)
        info = {"date": d.isoformat(), "count": len(mine) + unknown.get(d, 0), "unknown_time": unknown.get(d, 0),
                "booked_minutes": int(booked), "fill": round(min(1.0, booked / day_minutes), 2),
                "first_free": None}
        if d < now.date():
            info["status"] = "past"
        elif d.weekday() in days_off:
            info["status"] = "closed"
        else:
            t = datetime.combine(d, open_t)
            if d == now.date():
                n = now.replace(second=0, microsecond=0)
                t = max(t, n + timedelta(minutes=(-n.minute) % 5))
            close = datetime.combine(d, close_t)
            while t + min_dur <= close:
                if cal.why_busy(t, t + min_dur, None)[0] is None:
                    info["first_free"] = t.strftime("%H:%M")
                    break
                t += step
            info["status"] = "full" if info["first_free"] is None else ("empty" if not info["count"] else "partial")
        out["days"].append(info)
    return out
