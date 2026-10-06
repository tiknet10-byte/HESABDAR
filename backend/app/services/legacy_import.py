"""Bring data over from the salon's previous software (e.g. «چهره») via its Excel / CSV exports.

Three kinds of file:
  * customers - name, mobile (+ birth date, notes)
  * history   - services already done: customer, date, service (+ staff, amount). Stored as completed
                appointments for the customer's history; NO money is posted (it was accounted in the old system).
  * deposits  - deposits held for future appointments: customer, amount, received date (+ service, appointment
                date). Posted as an opening balance (Dr opening equity / Cr customer deposits) - the cash itself
                is not counted again - and applied to the invoice like any other deposit later.

Each import is a batch: preview first (nothing written), then commit, and it can be undone completely.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Appointment,
    Customer,
    Deposit,
    ImportBatch,
    Invoice,
    JournalEntry,
    Payment,
    PaymentAccount,
    Service,
    ServiceLine,
    Staff,
    WaitlistEntry,
    local_now,
)
from . import accounting
from .jalali import jalali_to_gregorian
from .textutil import normalize_mobile, normalize_text, parse_amount, to_en_digits

KINDS = {"customers": "مشتریان", "history": "سوابق خدمات انجام‌شده", "deposits": "بیعانه‌های نوبت‌های آینده"}
IMPORT_NOTE = "انتقال از نرم‌افزار قبلی"
MAX_ROWS = 50_000

# column titles seen in Persian salon software exports -> field
SYNONYMS: dict[str, list[str]] = {
    "name": ["نام مشتری", "مشتری", "نام و نام خانوادگی", "نام ونام خانوادگی", "نام کامل", "اسم", "مراجعه کننده", "نام مراجعه کننده",
             "خانم", "customer", "name"],
    "first_name": ["نام"],
    "last_name": ["نام خانوادگی", "فامیل", "فامیلی"],
    "mobile": ["موبایل", "تلفن همراه", "همراه", "شماره همراه", "شماره موبایل", "شماره تماس", "تلفن", "تماس", "شماره", "mobile", "phone"],
    "date": ["تاریخ", "تاریخ مراجعه", "تاریخ خدمت", "تاریخ انجام", "تاریخ دریافت", "تاریخ پرداخت", "تاریخ ثبت", "تاریخ فاکتور", "date"],
    "time": ["ساعت", "زمان", "ساعت مراجعه", "ساعت نوبت", "time"],
    "appt_date": ["تاریخ نوبت", "نوبت", "زمان نوبت", "تاریخ رزرو", "تاریخ مراجعه بعدی"],
    "service": ["خدمت", "خدمات", "نام خدمت", "عنوان خدمت", "شرح خدمت", "نوع خدمت", "شرح", "عنوان", "service"],
    "staff": ["پرسنل", "آرایشگر", "کارشناس", "اپراتور", "نام پرسنل", "انجام دهنده", "متخصص", "کارمند", "staff"],
    "amount": ["مبلغ", "مبلغ بیعانه", "بیعانه", "پیش پرداخت", "مبلغ کل", "قیمت", "مبلغ پرداختی", "مبلغ دریافتی", "جمع", "amount"],
    "notes": ["توضیحات", "یادداشت", "ملاحظات", "توضیح", "notes"],
    "birth_date": ["تاریخ تولد", "تولد"],
}
FIELD_LABELS = {"name": "نام مشتری", "first_name": "نام", "last_name": "نام خانوادگی", "mobile": "موبایل", "date": "تاریخ",
                "time": "ساعت", "appt_date": "تاریخ نوبت", "service": "خدمت", "staff": "پرسنل", "amount": "مبلغ",
                "notes": "توضیحات", "birth_date": "تاریخ تولد"}
TEMPLATES = {
    "customers": ["نام مشتری", "موبایل", "تاریخ تولد", "توضیحات"],
    "history": ["نام مشتری", "موبایل", "تاریخ", "ساعت", "خدمت", "پرسنل", "مبلغ", "توضیحات"],
    "deposits": ["نام مشتری", "موبایل", "تاریخ دریافت", "مبلغ بیعانه", "خدمت", "تاریخ نوبت", "ساعت نوبت", "پرسنل", "توضیحات"],
}


class ImportProblem(ValueError):
    pass


def _key(text: object) -> str:
    """Comparable form of a header / name: Persian letters unified, no spaces or half-spaces."""
    return re.sub(r"[\s\-_‌.:]+", "", normalize_text(str(text or "")))


_SYN = {field: [_key(s) for s in words] for field, words in SYNONYMS.items()}


# ------------------------------------------------------------------ reading files
def read_table(filename: str, data: bytes) -> list[list[object]]:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
        except ImportError as exc:  # pragma: no cover - installed by requirements.txt
            raise ImportProblem("کتابخانه خواندن اکسل نصب نیست؛ update.bat را اجرا کنید") from exc
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        best: list[list[object]] = []
        for ws in wb.worksheets:  # the sheet with the most filled rows
            rows = [list(r) for r in ws.iter_rows(values_only=True) if any(c not in (None, "") for c in r)]
            if len(rows) > len(best):
                best = rows
        wb.close()
        return best[:MAX_ROWS + 20]
    if name.endswith(".xls"):
        raise ImportProblem("این فایل اکسل قدیمی (xls) است. آن را در Excel باز کنید و با «Save As» به صورت "
                            "«Excel Workbook (.xlsx)» یا «CSV UTF-8» ذخیره کنید، بعد همان را بارگذاری کنید.")
    for enc in ("utf-8-sig", "cp1256", "utf-16"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportProblem("فایل قابل خواندن نیست؛ فایل Excel (.xlsx) یا CSV بفرستید")
    sample = text[:4000]
    delim = max([",", ";", "\t", "|"], key=sample.count)
    return [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)][:MAX_ROWS + 20]


def detect_mapping(header: list[object]) -> dict[str, int]:
    """field -> column index, from the column titles."""
    keys = [_key(h) for h in header]
    mapping: dict[str, int] = {}
    # exact titles first, then titles that contain a known word (longest words first)
    for exact in (True, False):
        for field, words in _SYN.items():
            if field in mapping:
                continue
            for w in sorted(words, key=len, reverse=True):
                hit = next((i for i, k in enumerate(keys) if k and i not in mapping.values()
                            and (k == w if exact else (len(w) >= 3 and w in k))), None)
                if hit is not None:
                    mapping[field] = hit
                    break
    if "first_name" in mapping and "last_name" not in mapping and "name" not in mapping:
        mapping["name"] = mapping.pop("first_name")
    return mapping


def _header_row(rows: list[list[object]]) -> int:
    scores = [len(detect_mapping(r)) for r in rows[:15]]
    return max(range(len(scores)), key=lambda i: scores[i]) if scores else 0


# ------------------------------------------------------------------ values
def parse_date(value: object) -> tuple[date | None, time | None]:
    if value in (None, ""):
        return None, None
    if isinstance(value, datetime):
        return value.date(), (value.time() if (value.hour or value.minute) else None)
    if isinstance(value, date):
        return value, None
    s = to_en_digits(str(value)).strip()
    t = None
    tm = re.search(r"(\d{1,2}):(\d{2})", s)
    if tm and 0 <= int(tm.group(1)) < 24 and 0 <= int(tm.group(2)) < 60:
        t = time(int(tm.group(1)), int(tm.group(2)))
        s = s.replace(tm.group(0), " ")
    m = re.search(r"(\d{1,4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,4})", s)
    if m:
        a, b, c = (int(x) for x in m.groups())
        y, mo, d = (c, b, a) if c > 31 else (a, b, c)  # dd/mm/yyyy or yyyy/mm/dd
    else:
        m = re.search(r"\b(1[34]\d{2}|20\d{2})(\d{2})(\d{2})\b", s)  # 14030512
        if not m:
            return None, t
        y, mo, d = (int(x) for x in m.groups())
    if y < 100:
        y += 1400 if y < 50 else 1300
    try:
        if y < 1700:
            if not (1 <= mo <= 12 and 1 <= d <= 31):
                return None, t
            return date(*jalali_to_gregorian(y, mo, d)), t
        return date(y, mo, d), t
    except ValueError:
        return None, t


def parse_time(value: object) -> time | None:
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    _, t = parse_date(f"0 {value}") if value not in (None, "") else (None, None)
    if t is None and value not in (None, ""):
        m = re.fullmatch(r"\s*(\d{1,2})\s*", to_en_digits(str(value)))
        if m and int(m.group(1)) < 24:
            t = time(int(m.group(1)), 0)
    return t


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def _amount(value: object, unit: str) -> int | None:
    if value in (None, ""):
        return None
    n = int(value) if isinstance(value, (int, float)) else parse_amount(_text(value))
    if n is None:
        return None
    return int(n) * (10 if unit == "toman" else 1)


def _mobile(value: object) -> str | None:
    s = _text(value)
    if re.fullmatch(r"9\d{9}", s):  # Excel dropped the leading zero
        s = "0" + s
    return normalize_mobile(s)


# ------------------------------------------------------------------ lookups
class _Catalog:
    def __init__(self, db: Session):
        self.services: dict[str, int] = {}
        for s in db.scalars(select(Service)):
            for n in [s.name, *(s.aliases or [])]:
                self.services.setdefault(_key(n), s.id)
            if s.line:  # "line / service" or "line - service" as written by some exports
                self.services.setdefault(_key(f"{s.line.name}{s.name}"), s.id)
        self.staff: dict[str, int] = {}
        for p in db.scalars(select(Staff)):
            self.staff.setdefault(_key(p.full_name), p.id)
            first = p.full_name.split()[0] if p.full_name.split() else ""
            if first:
                self.staff.setdefault(_key(first), p.id)

    def service(self, name: str) -> int | None:
        return self.services.get(_key(name)) if name else None

    def person(self, name: str) -> int | None:
        if not name:
            return None
        k = _key(name)
        return self.staff.get(k) or self.staff.get(_key(name.split()[0]))


# ------------------------------------------------------------------ preview
def parse_rows(db: Session, rows: list[list[object]], kind: str, unit: str, mapping: dict[str, int] | None = None) -> dict:
    if kind not in KINDS:
        raise ImportProblem("نوع اطلاعات نامعتبر است")
    if not rows:
        raise ImportProblem("فایل خالی است")
    hi = _header_row(rows)
    header = [_text(h) for h in rows[hi]]
    mapping = {k: int(v) for k, v in (mapping or detect_mapping(rows[hi])).items() if v is not None and int(v) >= 0}
    if kind == "history" and "date" not in mapping and "appt_date" in mapping:
        mapping["date"] = mapping.pop("appt_date")
    need = {"customers": [], "history": ["date"], "deposits": ["amount"]}[kind]
    if not ({"name", "mobile", "first_name", "last_name"} & mapping.keys()):
        raise ImportProblem("ستون نام یا موبایل مشتری پیدا نشد؛ ستون‌ها را دستی مشخص کنید")
    missing = [FIELD_LABELS[f] for f in need if f not in mapping]
    if missing:
        raise ImportProblem(f"ستون «{'، '.join(missing)}» پیدا نشد؛ ستون‌ها را دستی مشخص کنید")

    cat = _Catalog(db)
    now = local_now()
    get = lambda r, f: r[mapping[f]] if f in mapping and mapping[f] < len(r) else None  # noqa: E731
    parsed: list[dict] = []
    for n, r in enumerate(rows[hi + 1: hi + 1 + MAX_ROWS], start=hi + 2):
        name = _text(get(r, "name")) or " ".join(x for x in (_text(get(r, "first_name")), _text(get(r, "last_name"))) if x)
        if re.fullmatch(r"(جمع|جمع کل|total|مجموع).*", name or "", flags=re.I):
            continue
        raw_mobile = _text(get(r, "mobile"))
        row = {"row": n, "name": name, "mobile": _mobile(get(r, "mobile")), "errors": [], "warnings": []}
        if raw_mobile and not row["mobile"]:
            row["warnings"].append(f"موبایل «{raw_mobile}» معتبر نیست")
        if not row["name"] and not row["mobile"]:
            # blank lines, row numbers only, or a totals line ("جمع کل") - not a record
            if any(re.match(r"\s*(جمع|مجموع|total)", _text(c), flags=re.I) for c in r) or \
                    not (_text(get(r, "date")) or _text(get(r, "service")) or _text(get(r, "appt_date"))):
                continue
            row["errors"].append("نام و موبایل مشتری خالی است")
        d, t = parse_date(get(r, "date"))
        t = parse_time(get(r, "time")) or t
        if kind != "customers" and get(r, "date") not in (None, "") and d is None:
            row["errors"].append(f"تاریخ «{_text(get(r, 'date'))}» قابل خواندن نیست")
        row["date"] = datetime.combine(d, t or time(12, 0)).isoformat(timespec="minutes") if d else None
        ad, at_ = parse_date(get(r, "appt_date"))
        if kind == "deposits" and "time" in mapping and "appt_date" in mapping:
            at_ = parse_time(get(r, "time")) or at_
        row["appt_date"] = datetime.combine(ad, at_ or time(12, 0)).isoformat(timespec="minutes") if ad else None
        row["appt_time_known"] = bool(at_)
        row["service_name"] = _text(get(r, "service"))
        row["service_id"] = cat.service(row["service_name"])
        row["staff_name"] = _text(get(r, "staff"))
        row["staff_id"] = cat.person(row["staff_name"])
        if row["staff_name"] and not row["staff_id"]:
            row["warnings"].append(f"پرسنل «{row['staff_name']}» در سیستم نیست (در توضیحات ثبت می‌شود)")
        row["amount"] = _amount(get(r, "amount"), unit)
        row["notes"] = _text(get(r, "notes"))
        bd, _ = parse_date(get(r, "birth_date"))
        row["birth_date"] = bd.isoformat() if bd else None
        if kind == "history":
            if not d:
                row["errors"].append("تاریخ خدمت خالی است")
            if not row["service_name"]:
                row["warnings"].append("نام خدمت خالی است")
            if d and d > now.date():
                row["warnings"].append("تاریخ آینده است؛ به‌صورت نوبت رزرو ثبت می‌شود")
        if kind == "deposits":
            if not row["amount"] or row["amount"] <= 0:
                row["errors"].append("مبلغ بیعانه خالی یا نامعتبر است")
            if d and d > now.date() and not ad:  # a single future date is the appointment, not the payment
                row["appt_date"], row["date"] = row["date"], None
                row["appt_time_known"] = bool(t)
            if row["appt_date"] and datetime.fromisoformat(row["appt_date"]) < now:
                row["warnings"].append("تاریخ نوبت گذشته است؛ فقط بیعانه ثبت می‌شود")
        parsed.append(row)

    unknown: dict[str, int] = {}
    unknown_staff: dict[str, int] = {}
    for p in parsed:
        if p["service_name"] and not p["service_id"] and not p["errors"]:
            unknown[p["service_name"]] = unknown.get(p["service_name"], 0) + 1
        if p["staff_name"] and not p["staff_id"]:
            unknown_staff[p["staff_name"]] = unknown_staff.get(p["staff_name"], 0) + 1
    ok = [p for p in parsed if not p["errors"]]
    mobiles = {p["mobile"] for p in ok if p["mobile"]}
    existing = set(db.scalars(select(Customer.mobile).where(Customer.mobile.in_(mobiles)))) if mobiles else set()
    people = {p["mobile"] or _key(p["name"]) for p in ok}
    dates = sorted(p["date"] for p in ok if p["date"])
    return {
        "kind": kind, "kind_label": KINDS[kind], "unit": unit, "header_row": hi + 1, "headers": header, "mapping": mapping,
        "fields": FIELD_LABELS, "rows": parsed,
        "summary": {"total": len(parsed), "ok": len(ok), "errors": len(parsed) - len(ok),
                    "warnings": sum(1 for p in ok if p["warnings"]), "customers": len(people),
                    "existing_customers": len(existing), "new_customers": max(0, len(people) - len(existing)),
                    "amount": sum(p["amount"] or 0 for p in ok), "first_date": dates[0] if dates else None,
                    "last_date": dates[-1] if dates else None,
                    "future_appointments": sum(1 for p in ok if p.get("appt_date") and p["appt_date"] >= now.isoformat()[:16])},
        "unknown_services": sorted(({"name": k, "count": v} for k, v in unknown.items()), key=lambda x: -x["count"]),
        "unknown_staff": sorted(({"name": k, "count": v} for k, v in unknown_staff.items()), key=lambda x: -x["count"]),
    }


def preview(db: Session, filename: str, data: bytes, kind: str, unit: str = "rial", mapping: dict | None = None) -> ImportBatch:
    result = parse_rows(db, read_table(filename, data), kind, unit, mapping)
    batch = ImportBatch(kind=f"legacy_{kind}", file_name=filename[:250], status="review", provider="legacy",
                        rows=result.pop("rows"), summary=result)
    db.add(batch)
    db.flush()
    return batch


# ------------------------------------------------------------------ commit
def commit(db: Session, batch: ImportBatch, *, service_map: dict[str, int | str] | None = None, new_line_id: int | None = None,
           payment_account_id: int | None = None, user=None) -> dict:  # noqa: ANN001
    if batch.status != "review":
        raise ImportProblem("این فایل قبلاً ثبت یا لغو شده است")
    kind = batch.kind.removeprefix("legacy_")
    now = local_now()
    service_map = service_map or {}
    created: dict[str, list[int]] = {"customers": [], "appointments": [], "deposits": [], "services": []}
    counts = {"customers_new": 0, "customers_updated": 0, "appointments": 0, "deposits": 0, "skipped_duplicates": 0, "skipped_errors": 0}

    # services that were not found: map to an existing one, create, or leave the name in the notes
    new_services: dict[str, int] = {}

    def service_for(row: dict) -> int | None:
        if row.get("service_id"):
            return row["service_id"]
        name = row.get("service_name")
        if not name:
            return None
        choice = service_map.get(name, "new")
        if isinstance(choice, int) or (isinstance(choice, str) and choice.isdigit()):
            return int(choice)
        if choice != "new":
            return None
        if name not in new_services:
            line_id = new_line_id or _import_line(db).id
            svc = Service(line_id=line_id, name=name[:120], base_price=0, duration_minutes=60, is_active=True)
            db.add(svc)
            db.flush()
            new_services[name] = svc.id
            created["services"].append(svc.id)
        return new_services[name]

    customers: dict[str, Customer] = {}

    def customer_for(row: dict) -> Customer:
        key = row["mobile"] or "name:" + _key(row["name"])
        if key in customers:
            return customers[key]
        c = accounting.find_customer(db, row["mobile"]) if row["mobile"] else db.scalar(
            select(Customer).where(Customer.mobile.is_(None), Customer.full_name == row["name"]))
        if c is None:
            c = Customer(full_name=row["name"] or f"مشتری {row['mobile']}", mobile=row["mobile"], source="import")
            db.add(c)
            db.flush()
            created["customers"].append(c.id)
            counts["customers_new"] += 1
        else:
            if row["name"] and (not c.full_name or c.full_name.startswith("مشتری ")):
                c.full_name = row["name"]
            counts["customers_updated"] += 1
        if row.get("birth_date") and not c.birth_date:
            c.birth_date = row["birth_date"]
        if kind == "customers" and row.get("notes") and row["notes"] not in (c.notes or ""):
            c.notes = ((c.notes + "\n") if c.notes else "") + row["notes"]
        customers[key] = c
        return c

    def note(row: dict, svc_id: int | None) -> str:
        parts = [IMPORT_NOTE]
        if row.get("service_name") and not svc_id:
            parts.append(f"خدمت: {row['service_name']}")
        if row.get("staff_name") and not row.get("staff_id"):
            parts.append(f"پرسنل: {row['staff_name']}")
        if row.get("notes"):
            parts.append(row["notes"])
        return " - ".join(parts)

    account = None
    if kind == "deposits":
        account = db.get(PaymentAccount, payment_account_id) if payment_account_id else db.scalar(
            select(PaymentAccount).where(PaymentAccount.is_active.is_(True)).order_by(PaymentAccount.id))
        if account is None:
            raise ImportProblem("ابتدا در تنظیمات یک حساب دریافت (کارتخوان/کارت/صندوق) تعریف کنید")

    for row in batch.rows:
        if row.get("errors"):
            counts["skipped_errors"] += 1
            continue
        c = customer_for(row)
        if kind == "customers":
            continue
        svc_id = service_for(row)
        if kind == "history":
            at = datetime.fromisoformat(row["date"])
            day_start = datetime.combine(at.date(), time())
            dup = db.scalar(select(Appointment.id).where(
                Appointment.customer_id == c.id, Appointment.service_id.is_(svc_id) if svc_id is None else Appointment.service_id == svc_id,
                Appointment.start_at >= day_start, Appointment.start_at < datetime.combine(at.date(), time.max)))
            if dup:
                counts["skipped_duplicates"] += 1
                continue
            a = Appointment(customer_id=c.id, service_id=svc_id, staff_id=row.get("staff_id"), start_at=at,
                            status="done" if at <= now else "booked", quoted_price=row.get("amount") or 0, notes=note(row, svc_id))
            db.add(a)
            db.flush()
            created["appointments"].append(a.id)
            counts["appointments"] += 1
        elif kind == "deposits":
            received = datetime.fromisoformat(row["date"]) if row.get("date") else now
            received = min(received, now)
            dup = db.scalar(select(Deposit.id).where(
                Deposit.customer_id == c.id, Deposit.amount == row["amount"], Deposit.source == "import",
                func.date(Deposit.received_at) == received.date().isoformat()))
            if dup:
                counts["skipped_duplicates"] += 1
                continue
            appt_id = None
            if row.get("appt_date") and datetime.fromisoformat(row["appt_date"]) >= now:
                a = Appointment(customer_id=c.id, service_id=svc_id, staff_id=accounting.default_staff_id(db, svc_id, row.get("staff_id")),
                                start_at=datetime.fromisoformat(row["appt_date"]), status="booked",
                                quoted_price=(db.get(Service, svc_id).base_price if svc_id else 0), notes=note(row, svc_id))
                db.add(a)
                db.flush()
                appt_id = a.id
                created["appointments"].append(a.id)
                counts["appointments"] += 1
            svc = db.get(Service, svc_id) if svc_id else None
            dep = Deposit(customer_id=c.id, amount=row["amount"], payment_account_id=account.id, received_at=received,
                          service_id=svc_id, appointment_id=appt_id, source="import", notes=note(row, svc_id),
                          staff_id=accounting.default_staff_id(db, svc_id, row.get("staff_id")))
            db.add(dep)
            db.flush()
            # opening balance: the money was received in the old system, so cash is not counted again
            accounting.post(db, f"بیعانه انتقالی {c.full_name}", [
                accounting.Leg(accounting.account(db, accounting.OPENING), debit=dep.amount, customer_id=c.id),
                accounting.Leg(accounting.account(db, accounting.DEPOSITS), credit=dep.amount, customer_id=c.id,
                               line_id=svc.line_id if svc else None, staff_id=dep.staff_id),
            ], "deposit", dep.id, at=received)
            created["deposits"].append(dep.id)
            counts["deposits"] += 1
    batch.status = "committed"
    batch.summary = {**batch.summary, "created": created, "result": counts, "committed_at": now.isoformat(timespec="minutes")}
    return counts


def _import_line(db: Session) -> ServiceLine:
    name = "خدمات انتقالی"
    line = db.scalar(select(ServiceLine).where(ServiceLine.name == name))
    if line is None:
        line = ServiceLine(name=name, color="#94a3b8")
        db.add(line)
        db.flush()
    return line


# ------------------------------------------------------------------ undo
def undo(db: Session, batch: ImportBatch) -> dict:
    if batch.status != "committed":
        raise ImportProblem("فقط فایل‌های ثبت‌شده قابل برگشت هستند")
    created = (batch.summary or {}).get("created", {})
    kept = {"deposits": 0, "appointments": 0, "customers": 0, "services": 0}
    removed = {"deposits": 0, "appointments": 0, "customers": 0, "services": 0}
    for did in created.get("deposits", []):
        d = db.get(Deposit, did)
        if d is None:
            continue
        if d.status != "held":  # already used on an invoice / refunded: leave it
            kept["deposits"] += 1
            continue
        for e in db.scalars(select(JournalEntry).where(JournalEntry.ref_type == "deposit", JournalEntry.ref_id == did)):
            db.delete(e)
        db.delete(d)
        removed["deposits"] += 1
    db.flush()
    for aid in created.get("appointments", []):
        a = db.get(Appointment, aid)
        if a is None:
            continue
        if a.invoice_id or db.scalar(select(func.count(Deposit.id)).where(Deposit.appointment_id == aid)):
            kept["appointments"] += 1
            continue
        db.delete(a)
        removed["appointments"] += 1
    db.flush()
    for sid in created.get("services", []):
        s = db.get(Service, sid)
        if s is None:
            continue
        used = db.scalar(select(func.count(Appointment.id)).where(Appointment.service_id == sid)) or \
            db.scalar(select(func.count(Deposit.id)).where(Deposit.service_id == sid))
        if used:
            kept["services"] += 1
            continue
        db.delete(s)
        removed["services"] += 1
    for cid in created.get("customers", []):
        c = db.get(Customer, cid)
        if c is None:
            continue
        used = any(db.scalar(select(func.count(m.id)).where(m.customer_id == cid)) for m in (Appointment, Deposit, Invoice, Payment, WaitlistEntry))
        if used:
            kept["customers"] += 1
            continue
        db.delete(c)
        removed["customers"] += 1
    batch.status = "undone"
    batch.summary = {**batch.summary, "undo": {"removed": removed, "kept": kept}}
    return {"removed": removed, "kept": kept}
