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
# a transfer that did not go through (matched on the normalized text: a half-space counts as a space)
FAILED_WORDS = ("ناموفق", "نا موفق", "انجام نشد", "خطا در", "نامشخص", "رد شد", "عدم موفقیت", "موجودی کافی نیست",
                "در انتظار", "برگشت خورد", "لغو شد")


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
    """The last 4 digits of the card written after one of the labels («به کارت 6037-99**-****-4321», «6037 99** **** 4321»,
    «6037***4321», «6037-4321», «****4321», «4321»). The card is one run of digits / masks; more groups separated by spaces
    are taken only as 4-character card groups, never a date, a time or an amount written after it on the line."""
    m = re.search(rf"(?:{labels})[^\d\n]{{0,25}}([\d*xX×][\d*xX×\-]*(?:[ \t][\d*xX×]{{4}}(?![\d*xX×/:.,\-]))*)", raw)
    if not m:
        return None
    parts = m.group(1).split()
    if len(parts) > 1 and len(parts[0]) != 4:
        parts = parts[:1]  # «6037***4321 1405»: what follows a whole card is not part of it
    chars = re.sub(r"[\s\-]", "", "".join(parts))[:16]
    if len(chars) >= 4 and chars[-4:].isdigit():
        return chars[-4:]
    digits = re.sub(r"\D", "", chars)
    return digits[-4:] if len(digits) >= 4 else None


SOURCE_LABELS = r"مبد[اأ]|از\s*کارت|کارت\s*پرداخت\s*کننده|کارت\s*برداشت"
DEST_LABELS = r"مقصد|به\s*کارت|کارت\s*گیرنده|کارت\s*دریافت\s*کننده|کارت\s*ذینفع"


def _failed(raw: str) -> bool:
    t = normalize_text(raw)
    return any(normalize_text(w) in t for w in FAILED_WORDS)


def read_text(text: str) -> dict:
    """What the rule-based reader finds in a receipt's text (an SMS, the text of a bank app's receipt)."""
    raw = to_en_digits(text or "").replace("\u200c", " ")
    p = parse_receipt(raw)
    out = {"amount": p.get("amount"), "reference": p.get("reference"), "paid_at": p.get("datetime"), "bank": p.get("bank"),
           "source_card": _labelled_card(raw, SOURCE_LABELS), "dest_card": _labelled_card(raw, DEST_LABELS),
           "dest_name": None, "iban": None, "success": not _failed(raw),
           "unit_named": bool(p.get("unit"))}  # ریال / تومان written right after the amount (not just somewhere)
    # a single card and nothing saying whose: on a transfer receipt it is usually the receiver's; on a bank SMS of the
    # salon's own account (a withdrawal, the balance left) it is the payer's
    sms = p.get("direction") == "out" or bool(re.search(r"برداشت|مانده|موجودی", raw))
    if not out["dest_card"] and not out["source_card"] and p.get("card_last4"):
        out["source_card" if sms else "dest_card"] = p["card_last4"]
    if out["dest_card"] and out["dest_card"] == out["source_card"]:
        out["dest_card"] = None
    m = re.search(r"(?:نام\s*(?:دارنده|صاحب)\s*(?:کارت)?(?:\s*مقصد)?|به\s*نام|گیرنده|ذینفع|ذی\s*نفع)\s*[:：]?\s*([^\n\d:：]{3,40})", raw)
    if m:
        out["dest_name"] = m.group(1).strip(" -،,")
    m = re.search(r"IR\s?(\d[\d\s]{22,30})", raw, re.I)
    if m:
        out["iban"] = "IR" + re.sub(r"\D", "", m.group(1))[:24]
    return out


def _text_or_none(value) -> str | None:  # noqa: ANN001
    return value.strip()[:200] or None if isinstance(value, str) else None


def _amount_of(value) -> int | None:  # noqa: ANN001
    """A positive whole Rial amount from the assistant's answer (a number or digits), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(value)
    elif isinstance(value, str) and re.fullmatch(r"\s*[\d,٬]+\s*", to_en_digits(value)):
        n = int(re.sub(r"\D", "", to_en_digits(value)))
    else:
        return None
    return n if 0 < n < 10**15 else None


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
    raw = v.get("raw_text") if isinstance(v.get("raw_text"), str) else ""
    base = read_text(raw)
    when = None
    if isinstance(v.get("datetime_jalali"), str):
        when = parse_receipt(v["datetime_jalali"]).get("datetime")
    source = _last4(v.get("source_card_last4")) if isinstance(v.get("source_card_last4"), (str, int)) else None
    source = source or base["source_card"]
    dest = _last4(v.get("dest_card_last4")) if isinstance(v.get("dest_card_last4"), (str, int)) else None
    if not dest and base["dest_card"] != source:
        dest = base["dest_card"]
    ok = v.get("status_success", True) in (True, "true", "True") and v.get("is_receipt", True) not in (False, "false", "False")
    return {"amount": _amount_of(v.get("amount_rial")) or base["amount"], "text_amount": base["amount"],
            "unit_named": base["unit_named"],
            "reference": _text_or_none(str(v["reference"]) if isinstance(v.get("reference"), (str, int)) else None) or base["reference"],
            "paid_at": when or base["paid_at"], "bank": _text_or_none(v.get("bank")) or base["bank"],
            "source_card": source, "dest_card": dest if dest != source else None,
            "dest_name": _text_or_none(v.get("dest_name")) or base["dest_name"],
            "iban": _text_or_none(v.get("dest_iban")) or base["iban"],
            "success": ok and base["success"], "raw_text": raw[:4000], "description": _text_or_none(v.get("description"))}


# ------------------------------------------------------------------ the words sent with it
def _caption_amount(text: str) -> list[int]:
    """Every amount written in the message («۵ میلیون», «۲٫۵ م», «۸۰۰ هزار تومان», «۲.۵۰۰ هزار»), as the Rial amounts each
    can mean (a bare number is Toman or Rial); only to compare with the receipt. A tracking, card or phone number, a
    date or a very long number is not an amount."""
    t = normalize_text(text).replace("٫", ".")
    out: list[int] = []
    for m in re.finditer(r"(\d+(?:[.,/]\d+)*)\s*(میلیون|ملیون|م\b|تومن|تومان|هزار|ریال)?", t):
        digits, unit = m.group(1), m.group(2) or ""
        plain = re.sub(r"\D", "", digits)
        if not unit and re.search(r"(?<!\w)(?:پیگیری|مرجع|کارت|شبا|شماره|کد|موبایل|تلفن)(?:\s*(?:کارت|ملی|حساب|پیگیری))?\s*[:：]?\s*$",
                                  t[:m.start()]):
            continue  # «پیگیری ۱۲۳۴۵۶۷۸», «کارت ۶۰۳۷...»: a number right after its label (one with a money unit is an amount)
        if len(plain) >= 13 or normalize_mobile(digits) or (not unit and len(plain) < 5):
            continue  # a card, a mobile number, a year or a day
        if re.fullmatch(r"\d{1,3}(,\d{3})+", digits):
            digits = digits.replace(",", "")
        elif unit == "هزار" and re.fullmatch(r"\d{1,3}(\.\d{3})+", digits):
            digits = digits.replace(".", "")  # ۲.۵۰۰ هزار = 2,500 thousand
        if len(re.findall(r"[.,/]", digits)) > 1:
            continue  # a date (1405/07/30) or a number written another way: not an amount
        try:
            n = float(digits.replace(",", ".").replace("/", ".")) if unit in ("میلیون", "ملیون", "م", "هزار") \
                else float(re.sub(r"\D", "", digits))
            toman = n * (1_000_000 if unit in ("میلیون", "ملیون", "م") else 1_000 if unit == "هزار" else 1)
            if unit == "ریال":
                out.append(int(round(n)))
            else:
                out += [int(round(toman * 10))] + ([int(round(n))] if not unit else [])
        except (ValueError, OverflowError):
            continue
    return out


def _word(name: str, t: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", t) is not None


def read_caption(text: str, now: datetime | None = None) -> dict:
    """Kind (advance / settlement), month and a mobile number from the words sent with the receipt."""
    t = normalize_text(text or "")
    kind = None
    if any(_word(w, t) for w in SETTLE_WORDS):
        kind = "settlement"
    if any(_word(w, t) for w in ADVANCE_WORDS):
        kind = "advance"  # «برداشتی از سهم مهر» is still an advance
    month = None
    for i, name in enumerate(J_MONTHS):
        # a whole word, «ماه» may be joined to it («شهریورماه»): «احمدی» is not «دی», «مهرناز» is not «مهر»
        if re.search(rf"(?<!\w){re.escape(normalize_text(name))}(?:ماه)?(?!\w)", t):
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
                        "employee": (p.kind or "") == "employee", "card": "کارت مقصد" in why, "full_name": "نام" in why})
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


def _salon_cards(db: Session) -> set[str]:
    """The last 4 digits of the salon's own cards / accounts (money is paid FROM them, never to a staff member)."""
    return {c for c in (_last4(pa.card_mask) for pa in db.scalars(select(PaymentAccount))) if c}


def read(db: Session, *, image: bytes | None = None, media_type: str | None = None, text: str = "", caption: str = "",
         staff_id: int | None = None, kind: str | None = None) -> dict:
    """Everything that can be read from one receipt, and the payout it suggests (nothing is booked). kind: the kind of
    payment the form was opened for (used when the words don't say)."""
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
        receipt = {**read_text(text), "raw_text": text[:4000], "description": None}
        via = "text"
    receipt = receipt or {"amount": None, "reference": None, "paid_at": None, "bank": None, "source_card": None, "dest_card": None,
                          "dest_name": None, "iban": None, "success": True, "raw_text": (text or "")[:4000], "description": None}
    # a card of the salon itself is where the money came from, never where it went
    salon = _salon_cards(db)
    if receipt.get("dest_card") in salon:
        receipt["source_card"] = receipt.get("source_card") or receipt["dest_card"]
        receipt["dest_card"] = None
    if not receipt["success"]:
        warnings.append("این رسید «ناموفق» یا نامعلوم به نظر می‌رسد؛ اگر پولی جابه‌جا نشده، آن را ثبت نکنید")
    t_amount = receipt.get("text_amount")
    needs_check = False
    if via == "ai" and t_amount and receipt["amount"] and t_amount != receipt["amount"]:
        if receipt.get("unit_named") and (t_amount == receipt["amount"] * 10 or receipt["amount"] == t_amount * 10):
            receipt["amount"] = t_amount  # the unit is written right after the amount: what is written there wins
        needs_check = True
        warnings.append("مبلغ رسید را یک بار با عکس مقایسه کنید (ریال یا تومان ممکن است اشتباه خوانده شده باشد)")
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
    if best and people[0]["card"] and not people[0]["full_name"] and any(x["full_name"] for x in people[1:]):
        best = None  # a card learned for one person, another person's whole name printed on it: not guessed
    if staff_id and best and best != staff_id:
        mine = next((p["score"] for p in people if p["staff_id"] == staff_id), 0)
        if mine >= people[0]["score"]:
            best = staff_id
    chosen = best or staff_id
    when = paid_at or local_now()
    jd = gregorian_to_jalali(when.year, when.month, when.day)[2]
    kind_from = "words" if cap["kind"] else "form" if kind in ("advance", "settlement") else "date"
    kind = cap["kind"] or (kind if kind in ("advance", "settlement") else None)
    if kind is None:  # no word for it: in the middle of the month an advance, around its end the settlement
        kind = "advance" if 6 <= jd <= 24 else "settlement"
    if cap["month"]:
        jy, jm = cap["month"]
        month_from = "words"
    else:
        jy, jm = jmonth_of(when)
        month_from = "date"
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
            "failed": not receipt["success"], "needs_check": needs_check,
            "suggest": {"staff_id": chosen, "kind": kind, "kind_from": kind_from, "jy": jy, "jm": jm, "month_from": month_from,
                        "amount": receipt["amount"],
                        "paid_at": paid_at.isoformat(timespec="minutes") if paid_at else None,
                        "reference": receipt["reference"], "payment_account_id": _account_for_card(db, receipt["source_card"]),
                        "description": description}}


def may_learn(db: Session, person: Staff, receipt: dict) -> bool:
    """Learn the receipt's card for the person only when nothing on it points at someone else: not a card of the salon,
    not a card already known as another person's, and the receiver's name (when printed) is best this person's."""
    card = _last4(receipt.get("dest_card"))
    if not card or card == _last4(receipt.get("source_card")) or card in _salon_cards(db):
        return False
    if any(card in (p.cards or []) for p in db.scalars(select(Staff).where(Staff.id != person.id))):
        return False
    name = receipt.get("dest_name")
    if isinstance(name, str) and name.strip():
        named = match_people(db, mobile=None, dest_card=None, texts=[name])
        mine = next((x["score"] for x in named if x["staff_id"] == person.id), 0)
        if named and (named[0]["staff_id"] != person.id and named[0]["score"] >= mine):
            return False
    return True


def forget_card(db: Session, person: Staff, payout: StaffPayout) -> None:
    """A payout voided (entered by mistake): its card is forgotten unless another booked payout of the person used it."""
    card = _last4((payout.parsed or {}).get("dest_card")) if isinstance(payout.parsed, dict) else None
    if not card or card not in (person.cards or []):
        return
    for other in db.scalars(select(StaffPayout).where(StaffPayout.staff_id == person.id, StaffPayout.status == "booked",
                                                      StaffPayout.id != payout.id, StaffPayout.parsed.is_not(None))):
        if isinstance(other.parsed, dict) and _last4(other.parsed.get("dest_card")) == card:
            return
    person.cards = [c for c in (person.cards or []) if c != card]  # a new list, so it is saved


def learn_card(person: Staff, last4: str | None) -> None:
    """A card a person was paid on: the next receipt to it is theirs (a new list, so it is saved)."""
    last4 = _last4(last4)
    if last4 and last4 not in (person.cards or []):
        person.cards = [*(person.cards or [])[-4:], last4]
