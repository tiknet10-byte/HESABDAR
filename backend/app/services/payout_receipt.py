"""Reading a payout receipt to fill the payout form (1.22).

The owner pays a staff member or an employee (card to card, a bank app) and sends them the receipt with a few words on
WhatsApp («برداشتی مهر سارا», «تسویه شهریور»). The same picture (or the receipt's text) and those words are given here:
the amount, date, tracking number and cards come from the receipt (Claude vision when the assistant is on, else the
text rules), the person, the kind (advance / settlement) and the month from the words and the cards. Nothing is
booked here: the form is filled and a person checks it and confirms (staffpay.pay).
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PaymentAccount, Staff, StaffPayout, local_now
from .jalali import J_MONTHS, gregorian_to_jalali, jmonth_of
from .receipt_parser import parse_receipt
from .textutil import normalize_mobile, normalize_text, to_en_digits

log = logging.getLogger("hesabdar.payouts")

MAX_BYTES = 10 * 1024 * 1024
# a saved receipt gets its extension from this list only (never from the file name it was sent with)
IMAGE_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "application/pdf": "pdf"}

PAYOUT_VISION_PROMPT = (
    "This is a screenshot of an Iranian bank transfer receipt (card to card, پایا / ساتنا, or a mobile bank app) for "
    "money SENT to a person. Extract: "
    '{"is_receipt": bool, "status_success": bool, "amount_rial": int|null, "reference": str|null, '
    '"datetime_jalali": "YYYY/MM/DD HH:MM"|null, "source_card_last4": str|null, "dest_card_last4": str|null, '
    '"dest_name": str|null, "dest_iban": str|null, "bank": str|null, "description": str|null, "raw_text": str}. '
    "dest_* is the receiving side (کارت مقصد / نام دارنده / گیرنده / ذینفع). If the amount is shown in Toman convert to "
    "Rial (x10). Use English digits."
)

ADVANCE_WORDS = ("برداشتی", "برداشت", "مساعده", "علی الحساب", "علیالحساب", "پیش پرداخت", "قرض", "قسط")
SETTLE_WORDS = ("تسویه", "باقیمانده", "باقی مانده", "مانده", "سهم", "کارکرد", "پورسانت", "حقوق", "دستمزد", "پایان ماه")
FAILED_WORDS = ("ناموفق", "نا موفق", "تراکنش انجام نشد", "خطا در")


class ReceiptError(ValueError):
    pass


# ------------------------------------------------------------------ the saved picture
# A picture read for the form waits on disk (data/receipts/staff/pending, cleaned after two days); once its payout is
# booked it is kept in the database (ReceiptImage), so every backup and restore carries it.
PENDING_DAYS = 2


def receipts_dir() -> Path:
    from ..core.config import get_settings

    d = get_settings().backup_dir.parent / "receipts" / "staff" / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clean_pending(d: Path) -> None:
    import time

    limit = time.time() - PENDING_DAYS * 86400
    for f in d.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < limit:
                f.unlink()
        except OSError:
            pass  # in use / already gone: next time


def save_image(data: bytes, media_type: str) -> tuple[str, Path]:
    """Keep the picture under its sha256 until its payout is booked (the same receipt twice is one file)."""
    if media_type not in IMAGE_EXT:
        raise ReceiptError("فقط عکس (jpg / png / webp) یا PDF رسید را بفرستید")
    if not data:
        raise ReceiptError("فایل خالی است")
    if len(data) > MAX_BYTES:
        raise ReceiptError("فایل رسید بیش از ۱۰ مگابایت است")
    sha = hashlib.sha256(data).hexdigest()
    d = receipts_dir()
    _clean_pending(d)
    path = d / f"{sha}.{IMAGE_EXT[media_type]}"
    if path.resolve().parent != d.resolve():
        raise ReceiptError("نام فایل نامعتبر است")
    path.write_bytes(data)  # (again: its date is now, so it is not cleaned while the form is open)
    return sha, path


def _pending(sha: str) -> tuple[bytes, str] | None:
    d = receipts_dir()
    for media, ext in IMAGE_EXT.items():
        p = d / f"{sha}.{ext}"
        if p.is_file() and p.resolve().parent == d.resolve():
            return p.read_bytes(), media
    return None


def keep_image(db: Session, sha: str | None) -> bool:
    """The payout is booked: its picture goes into the database (once per picture). False: no such picture."""
    from ..models import ReceiptImage

    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        return False
    if db.get(ReceiptImage, sha) is not None:
        return True
    found = _pending(sha)
    if found is None:
        return False
    data, media = found
    if hashlib.sha256(data).hexdigest() != sha:
        return False
    db.add(ReceiptImage(sha256=sha, media_type=media, data=data))
    return True


def image(db: Session, sha: str | None) -> tuple[bytes, str] | None:
    """The picture of a receipt (from the database, else still waiting on disk), with its media type."""
    from ..models import ReceiptImage

    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        return None
    row = db.get(ReceiptImage, sha)
    if row is not None:
        return row.data, row.media_type
    return _pending(sha)


# ------------------------------------------------------------------ the receipt itself
def _last4(value) -> str | None:  # noqa: ANN001
    digits = re.sub(r"\D", "", to_en_digits(str(value or "")))
    return digits[-4:] if len(digits) >= 4 else None


def _labelled_card(raw: str, labels: str) -> str | None:
    m = re.search(rf"(?:{labels})[^\d\n]{{0,25}}([\d\s\-*xX]{{8,25}}\d{{4}}|\d{{4}})", raw)
    return _last4(m.group(1)) if m else None


def read_text(text: str) -> dict:
    """What the rule-based reader finds in a receipt's text (an SMS, the text of a bank app's receipt)."""
    raw = to_en_digits(text or "")
    p = parse_receipt(raw)
    out = {"amount": p.get("amount"), "reference": p.get("reference"), "paid_at": p.get("datetime"), "bank": p.get("bank"),
           "source_card": _labelled_card(raw, "مبدا|مبدأ|از کارت|کارت پرداخت کننده"),
           "dest_card": _labelled_card(raw, "مقصد|به کارت|کارت گیرنده|کارت دریافت کننده"),
           "dest_name": None, "iban": None, "success": not any(w in raw for w in FAILED_WORDS)}
    if not out["dest_card"] and not out["source_card"] and p.get("card_last4"):
        out["dest_card"] = p["card_last4"]  # a single card on a payout receipt is usually the receiver's
    m = re.search(r"(?:نام\s*(?:دارنده|صاحب)\s*(?:کارت)?(?:\s*مقصد)?|به\s*نام|گیرنده|ذینفع|ذی\s*نفع)\s*[:：]?\s*([^\n\d:：]{3,40})", raw)
    if m:
        out["dest_name"] = m.group(1).strip(" -،,")
    m = re.search(r"IR\s?(\d[\d\s]{22,30})", raw, re.I)
    if m:
        out["iban"] = "IR" + re.sub(r"\D", "", m.group(1))[:24]
    return out


def read_image(data: bytes, media_type: str) -> dict | None:
    """The receipt read by Claude (None when the assistant is off or could not read it)."""
    from ..ai import claude

    if not claude.available():
        return None
    try:
        v = claude.vision_extract(data, media_type, PAYOUT_VISION_PROMPT, timeout=90)
    except Exception:
        log.exception("payout receipt: vision failed")
        return None
    if not isinstance(v, dict):
        return None
    raw = str(v.get("raw_text") or "")
    base = read_text(raw)
    when = None
    if v.get("datetime_jalali"):
        when = parse_receipt(str(v["datetime_jalali"])).get("datetime")
    amount = v.get("amount_rial")
    try:
        amount = int(amount) if amount not in (None, "") else None
    except (TypeError, ValueError):
        amount = None
    return {"amount": amount or base["amount"], "reference": (str(v["reference"]) if v.get("reference") else base["reference"]),
            "paid_at": when or base["paid_at"], "bank": v.get("bank") or base["bank"],
            "source_card": _last4(v.get("source_card_last4")) or base["source_card"],
            "dest_card": _last4(v.get("dest_card_last4")) or base["dest_card"],
            "dest_name": v.get("dest_name") or base["dest_name"], "iban": v.get("dest_iban") or base["iban"],
            "success": bool(v.get("status_success", True)) and base["success"] and v.get("is_receipt", True) is not False,
            "raw_text": raw, "description": v.get("description")}


# ------------------------------------------------------------------ the words sent with it
def _caption_amount(text: str) -> list[int]:
    """An amount written in the message («۵ میلیون», «2.5م», «800 هزار تومان»), as the Rial amounts it can mean (a bare
    number is Toman or Rial); only to compare with the receipt."""
    t = normalize_text(text)
    for m in re.finditer(r"(\d+(?:[.,/]\d+)*)\s*(میلیون|ملیون|م\b|تومن|تومان|هزار|ریال)?", t):
        digits, unit = m.group(1), m.group(2) or ""
        if normalize_mobile(digits) or (not unit and len(re.sub(r"\D", "", digits)) < 5):
            continue  # a mobile number, a year or a day
        if re.fullmatch(r"\d{1,3}(,\d{3})+", digits):
            digits = digits.replace(",", "")
        if len(re.findall(r"[.,/]", digits)) > 1:
            continue  # a date (1405/07/30) or a number written another way: not an amount
        try:
            n = float(digits.replace(",", ".").replace("/", ".")) if unit in ("میلیون", "ملیون", "م", "هزار") \
                else float(re.sub(r"\D", "", digits))
        except ValueError:
            continue
        toman = n * (1_000_000 if unit in ("میلیون", "ملیون", "م") else 1_000 if unit == "هزار" else 1)
        if unit == "ریال":
            return [int(round(n))]
        return [int(round(toman * 10))] + ([int(round(n))] if not unit else [])
    return []


def read_caption(text: str, now: datetime | None = None) -> dict:
    """Kind (advance / settlement), month and a mobile number from the words sent with the receipt."""
    t = normalize_text(text or "")
    kind = None
    if any(w in t for w in SETTLE_WORDS):
        kind = "settlement"
    if any(w in t for w in ADVANCE_WORDS):
        kind = "advance"  # «برداشتی از سهم مهر» is still an advance
    month = None
    for i, name in enumerate(J_MONTHS):
        if normalize_text(name) in t:
            jm = i + 1
            y = re.search(r"\b(1[34]\d{2})\b", t)
            cy, cm = jmonth_of(now or local_now())
            jy = int(y.group(1)) if y else (cy if jm <= cm else cy - 1)
            month = [jy, jm]
            break
    mobile = None
    for tok in re.findall(r"[\d+]{10,14}", t):
        if normalize_mobile(tok):
            mobile = normalize_mobile(tok)
            break
    return {"kind": kind, "month": month, "mobile": mobile, "amount": _caption_amount(text or "")}


def match_people(db: Session, *, mobile: str | None, dest_card: str | None, texts: list[str]) -> list[dict]:
    """Whose payout it is, best first: their mobile, a card they were paid on before, their name in the words."""
    people = [p for p in db.scalars(select(Staff).where(Staff.is_active.is_(True)).order_by(Staff.id))]
    words = " ".join(normalize_text(x or "") for x in texts if x)
    out = []
    for p in people:
        score, why = 0, []
        if mobile and normalize_mobile(p.mobile) == mobile:
            score += 100
            why.append("شمارهٔ موبایل")
        if dest_card and dest_card in (p.cards or []):
            score += 80
            why.append("کارت مقصد")
        name = normalize_text(p.full_name)
        parts = [x for x in name.split() if len(x) > 1]
        hits = sum(1 for x in parts if re.search(rf"(?<!\w){re.escape(x)}(?!\w)", words))
        if name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", words):
            score += 60
            why.append("نام")
        elif hits:  # a first or last name alone: always below the whole name of someone else
            score += min(50, 50 * hits // (len(parts) + 1))
            why.append("بخشی از نام")
        if score:
            out.append({"staff_id": p.id, "name": p.full_name, "score": score, "why": "، ".join(why),
                        "employee": (p.kind or "") == "employee"})
    out.sort(key=lambda x: -x["score"])
    return out


def _account_for_card(db: Session, last4: str | None) -> int | None:
    if not last4:
        return None
    for pa in db.scalars(select(PaymentAccount).where(PaymentAccount.is_active.is_(True)).order_by(PaymentAccount.id)):
        if _last4(pa.card_mask) == last4:
            return pa.id
    return None


def _when(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def read(db: Session, *, image: bytes | None = None, media_type: str | None = None, text: str = "", caption: str = "",
         staff_id: int | None = None) -> dict:
    """Everything that can be read from one receipt, and the payout it suggests (nothing is booked)."""
    token, via, warnings = None, "none", []
    receipt = None
    if image:
        token, _ = save_image(image, media_type or "")
        receipt = read_image(image, media_type or "")
        if receipt is not None:
            via = "ai"
        elif not text:
            warnings.append("دستیار هوشمند روشن نیست یا رسید را نخواند؛ مبلغ و تاریخ را از روی عکس وارد کنید")
    if receipt is None and text:
        receipt = {**read_text(text), "raw_text": text, "description": None}
        via = "text"
    receipt = receipt or {"amount": None, "reference": None, "paid_at": None, "bank": None, "source_card": None, "dest_card": None,
                          "dest_name": None, "iban": None, "success": True, "raw_text": text or "", "description": None}
    if not receipt["success"]:
        warnings.append("این رسید «ناموفق» به نظر می‌رسد؛ اگر پولی جابه‌جا نشده، آن را ثبت نکنید")
    paid_at = _when(receipt["paid_at"])
    if paid_at and paid_at > local_now():
        warnings.append("تاریخ رسید در آینده خوانده شد؛ تاریخ را درست کنید")
        paid_at = None
    cap = read_caption(caption, paid_at or local_now())
    if cap["amount"] and receipt["amount"] and receipt["amount"] not in cap["amount"]:
        warnings.append("مبلغی که در پیام نوشته شده با مبلغ رسید یکی نیست؛ مبلغ رسید پیشنهاد شد، بررسی کنید")
    people = match_people(db, mobile=cap["mobile"], dest_card=receipt["dest_card"],
                          texts=[caption, receipt.get("dest_name") or "", receipt.get("description") or ""])
    # whose it looks like, from the receipt and the words alone; the person the form was opened for only when nothing
    # points at someone else (the form then warns: «به نظر می‌رسد این رسید مال ... است»)
    best = people[0]["staff_id"] if people and (len(people) == 1 or people[0]["score"] > people[1]["score"]) else None
    if staff_id and best and best != staff_id:
        mine = next((p["score"] for p in people if p["staff_id"] == staff_id), 0)
        if mine >= people[0]["score"]:
            best = staff_id
    chosen = best or staff_id
    when = paid_at or local_now()
    kind = cap["kind"]
    if kind is None:  # no word for it: in the middle of the month an advance, around its end the settlement
        jd = gregorian_to_jalali(when.year, when.month, when.day)[2]
        kind = "advance" if 6 <= jd <= 24 else "settlement"
    if cap["month"]:
        jy, jm = cap["month"]
    else:
        jy, jm = jmonth_of(when)
        jd = gregorian_to_jalali(when.year, when.month, when.day)[2]
        if kind == "settlement" and jd <= 15:  # the settlement of last month, paid at the start of this one
            jy, jm = (jy, jm - 1) if jm > 1 else (jy - 1, 12)
    duplicate = None
    if token:
        dup = db.scalar(select(StaffPayout).where(StaffPayout.image_sha256 == token, StaffPayout.status == "booked").limit(1))
        if dup is not None:
            who = db.get(Staff, dup.staff_id)
            duplicate = {"id": dup.id, "staff": who.full_name if who else None, "amount": dup.amount,
                         "paid_at": dup.paid_at.isoformat(timespec="minutes")}
            warnings.insert(0, "این رسید قبلاً ثبت شده است")
    if receipt["reference"] and not duplicate:
        dup = db.scalar(select(StaffPayout).where(StaffPayout.reference == receipt["reference"], StaffPayout.status == "booked").limit(1))
        if dup is not None and (not receipt["amount"] or dup.amount == receipt["amount"]):
            who = db.get(Staff, dup.staff_id)
            duplicate = {"id": dup.id, "staff": who.full_name if who else None, "amount": dup.amount,
                         "paid_at": dup.paid_at.isoformat(timespec="minutes")}
            warnings.insert(0, "پرداختی با همین شمارهٔ پیگیری قبلاً ثبت شده است")
    description = " ".join((caption or "").split())[:500]
    return {"token": token, "via": via, "receipt": receipt, "warnings": warnings, "duplicate": duplicate, "people": people[:5],
            "suggest": {"staff_id": chosen, "kind": kind, "jy": jy, "jm": jm, "amount": receipt["amount"],
                        "paid_at": paid_at.isoformat(timespec="minutes") if paid_at else None,
                        "reference": receipt["reference"], "payment_account_id": _account_for_card(db, receipt["source_card"]),
                        "description": description}}


def may_learn(db: Session, person: Staff, receipt: dict) -> bool:
    """Learn the receipt's card for the person only when nothing on it points at someone else (a card already known
    as another person's, or another person's name as the receiver)."""
    card = _last4(receipt.get("dest_card"))
    if not card:
        return False
    if any(card in (p.cards or []) for p in db.scalars(select(Staff).where(Staff.id != person.id))):
        return False
    name = receipt.get("dest_name")
    if name:
        named = match_people(db, mobile=None, dest_card=None, texts=[str(name)])
        if named and all(x["staff_id"] != person.id for x in named):
            return False
    return True


def learn_card(person: Staff, last4: str | None) -> None:
    """A card a person was paid on: the next receipt to it is theirs (a new list, so it is saved)."""
    last4 = _last4(last4)
    if last4 and last4 not in (person.cards or []):
        person.cards = [*(person.cards or [])[-4:], last4]
