"""Jalali (Shamsi) <-> Gregorian conversion (algorithm by Kazimierz Borkowski / jdf)."""
from __future__ import annotations


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1]
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    if not (1 <= jm <= 12 and 1 <= jd <= 31):
        raise ValueError("invalid jalali date")
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    days += (jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    month_days = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    for gm in range(1, 13):
        if gd <= month_days[gm]:
            break
        gd -= month_days[gm]
    return gy, gm, gd


def month_start(d):  # noqa: ANN001, ANN201
    """The first day (00:00) of the Jalali month that contains datetime/date d, as a datetime."""
    from datetime import date, datetime

    day = d.date() if isinstance(d, datetime) else d
    jy, jm, _ = gregorian_to_jalali(day.year, day.month, day.day)
    return datetime.combine(date(*jalali_to_gregorian(jy, jm, 1)), datetime.min.time())


def jmonth_of(d) -> tuple[int, int]:  # noqa: ANN001
    """(Jalali year, month) of a date or datetime."""
    from datetime import datetime

    day = d.date() if isinstance(d, datetime) else d
    jy, jm, _ = gregorian_to_jalali(day.year, day.month, day.day)
    return jy, jm


def next_month(jy: int, jm: int) -> tuple[int, int]:
    return (jy + 1, 1) if jm == 12 else (jy, jm + 1)


def month_range(jy: int, jm: int):  # noqa: ANN201
    """[start, next start) of a Jalali month as datetimes (from the 1st of each month only: no month-length guess)."""
    from datetime import date, datetime

    ny, nm = next_month(jy, jm)
    return (datetime.combine(date(*jalali_to_gregorian(jy, jm, 1)), datetime.min.time()),
            datetime.combine(date(*jalali_to_gregorian(ny, nm, 1)), datetime.min.time()))
