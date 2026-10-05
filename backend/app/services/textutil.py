"""Persian text helpers: digits, phone normalization, amount parsing, tokenizing."""
from __future__ import annotations

import re

_FA = "۰۱۲۳۴۵۶۷۸۹"
_AR = "٠١٢٣٤٥٦٧٨٩"
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate(_FA)} | {ord(c): str(i) for i, c in enumerate(_AR)}
_CHAR_MAP = {ord("ي"): "ی", ord("ك"): "ک", ord("ة"): "ه", ord("‌"): " ", ord("٬"): ",", ord("،"): ","}


def to_en_digits(text: str) -> str:
    return (text or "").translate(_DIGIT_MAP)


def normalize_text(text: str) -> str:
    text = to_en_digits(text or "").translate(_CHAR_MAP)
    text = re.sub(r"[ً-ْـ]", "", text)  # harakat / kashida
    return re.sub(r"\s+", " ", text).strip().lower()


def normalize_mobile(value: str | None) -> str | None:
    """Return Iranian mobile in 09xxxxxxxxx form, or None if not a mobile."""
    if not value:
        return None
    digits = re.sub(r"\D", "", to_en_digits(value))
    if digits.startswith("0098"):
        digits = "0" + digits[4:]
    elif digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]
    elif digits.startswith("9") and len(digits) == 10:
        digits = "0" + digits
    return digits if re.fullmatch(r"09\d{9}", digits) else None


def parse_amount(text: str) -> int | None:
    """Parse '1,500,000' / '۱.۵۰۰.۰۰۰' / '1500000' into an int."""
    if text is None:
        return None
    t = to_en_digits(str(text)).replace(",", "").replace("٬", "").replace(" ", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    m = re.search(r"\d+", t)
    return int(m.group()) if m else None


_STOP = {"و", "به", "از", "با", "برای", "که", "را", "این", "اون", "آن", "من", "تو", "هم", "یه", "یک", "سلام", "ممنون", "مرسی", "لطفا", "در", "تا", "است", "هست"}


def tokens(text: str) -> list[str]:
    words = re.findall(r"[\w]+", normalize_text(text))
    return [w for w in words if len(w) > 1 and not w.isdigit() and w not in _STOP]


def toman(rial: int | None) -> str:
    """User-facing money text: amounts are stored in Rial, shown in Toman."""
    return f"{(rial or 0) // 10:,} تومان"
