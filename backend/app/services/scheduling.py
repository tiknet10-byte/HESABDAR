"""Appointment scheduling: finds the first free slots for a service.

A slot is free when the chosen staff member is not busy, and the service line still has
capacity (= number of active staff working in that line, at least 1). Working hours, days
off and slot step come from settings (booking.*). Each service's duration comes from
Service.duration_minutes, set by the manager.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

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


class _Calendar:
    """Booked appointments + line capacity for one service, used to test candidate times."""

    def __init__(self, db: Session, svc: Service, start: datetime, end: datetime, exclude_id: int | None = None):
        self.line_staff = [p.id for p in db.scalars(select(Staff).where(Staff.is_active.is_(True), Staff.line_id == svc.line_id))]
        self.capacity = max(1, len(self.line_staff))
        self.line_services = set(db.scalars(select(Service.id).where(Service.line_id == svc.line_id)))
        cache: dict[int, int] = {}
        q = select(Appointment).where(Appointment.status == "booked", Appointment.start_at >= start - timedelta(days=1),
                                      Appointment.start_at <= end)
        if exclude_id:
            q = q.where(Appointment.id != exclude_id)
        self.intervals = [(a.start_at, a.start_at + timedelta(minutes=appointment_minutes(db, a, cache)), a) for a in db.scalars(q)]

    def free_staff(self, t: datetime, t_end: datetime, staff_id: int | None) -> tuple[bool, int | None]:
        overlapping = [a for s, e, a in self.intervals if s < t_end and e > t]
        if len([a for a in overlapping if a.service_id in self.line_services]) >= self.capacity:
            return False, None
        busy = {a.staff_id for a in overlapping if a.staff_id}
        if staff_id:
            return staff_id not in busy, staff_id
        if self.line_staff:
            free = [p for p in self.line_staff if p not in busy]
            return bool(free), (free[0] if free else None)
        return True, None


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
    step_min = int(step.total_seconds() // 60)
    start = (after or local_now()).replace(second=0, microsecond=0)
    minutes = start.hour * 60 + start.minute
    start = start.replace(hour=0, minute=0) + timedelta(minutes=((minutes + step_min - 1) // step_min) * step_min)
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


def check_slot(db: Session, service_id: int | None, staff_id: int | None, start_at: datetime,
               exclude_id: int | None = None, duration_minutes: int | None = None) -> str | None:
    """Return a warning if the time is outside working hours or already full, else None."""
    if not service_id:
        return None
    svc = db.get(Service, service_id)
    if svc is None:
        return None
    open_t, close_t, _, days_off = _hours(db)
    end = start_at + timedelta(minutes=duration_minutes or svc.duration_minutes or 60)
    if start_at.weekday() in days_off:
        return "این روز تعطیل سالن است"
    if start_at.time() < open_t or end.time() > close_t or end.date() != start_at.date():
        return "این زمان خارج از ساعت کاری سالن است"
    ok, _ = _Calendar(db, svc, start_at, end, exclude_id).free_staff(start_at, end, staff_id)
    return None if ok else "در این زمان ظرفیت لاین یا پرسنل انتخاب‌شده پر است"
