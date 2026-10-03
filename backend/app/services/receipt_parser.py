"""Parse Iranian bank SMS messages and payment receipts (card-to-card, POS, transfer).

Works with text. Screenshot receipts are first converted to text by an OCR
provider (see plugins/builtin/sales_book_ocr) or Claude vision, then parsed here.
The rule-based parser is deterministic and offline; AI parsing is an optional upgrade.
"""
from __future__ import annotations

import re
from datetime import datetime

from .jalali import jalali_to_gregorian
from .textutil import normalize_text, parse_amount, to_en_digits

AMOUNT_PATTERNS = [
    r"(?:مبلغ|واریز|انتقال|برداشت|خرید|مبلغ تراکنش|مبلغ انتقال)\s*[:：]?\s*([+\-]?[\d,.\s]{3,})\s*(ریال|تومان)?",
    r"([+\-][\d,]{4,})\s*(ریال|تومان)?",
    r"([\d,]{5,})\s*(ریال|تومان)",
]
REF_PATTERNS = [
    r"(?:شماره\s*پیگیری|کد\s*پیگیری|پیگیری|شماره\s*مرجع|مرجع|ارجاع|رهگیری|سند|trace|ref)\s*[:：#]?\s*(\d{4,20})",
]
CARD_PATTERNS = [
    r"(\d{4}[\s\-*xX]*\d{2}[\s\-*xX]*[*xX]{2,}[\s\-*xX]*\d{4})",  # 6037-99**-****-1234
    r"(\d{6}[*xX]{2,6}\d{4})",
    r"(?:کارت|card)\s*[:：]?\s*[*xX.]*\s*(\d{4})\b",
]
JDATE = r"(1[34]\d{2})[/\-.](\d{1,2})[/\-.](\d{1,2})"
TIME = r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
BANKS = ["ملت", "ملی", "صادرات", "تجارت", "سپه", "پاسارگاد", "سامان", "پارسیان", "رسالت", "مسکن", "کشاورزی",
         "رفاه", "آینده", "اقتصاد نوین", "شهر", "دی", "سینا", "بلو", "توسعه تعاون", "کارآفرین", "خاورمیانه", "ایران زمین"]


def parse_receipt(text: str) -> dict:
    raw = to_en_digits(text or "")
    norm = normalize_text(raw)
    result: dict = {"amount": None, "currency": "rial", "direction": None, "reference": None,
                    "card_last4": None, "card_mask": None, "datetime": None, "bank": None, "confidence": 0.0}

    for pat in AMOUNT_PATTERNS:
        m = re.search(pat, raw)
        if m:
            amt_text = m.group(1).strip()
            amount = parse_amount(amt_text)
            if amount:
                if (m.lastindex or 0) >= 2 and m.group(2) == "تومان":
                    amount *= 10
                    result["currency"] = "toman_converted"
                result["amount"] = amount
                if amt_text.startswith("-") or "برداشت" in m.group(0):
                    result["direction"] = "out"
                elif amt_text.startswith("+") or "واریز" in m.group(0):
                    result["direction"] = "in"
                break
    if result["direction"] is None:
        result["direction"] = "out" if "برداشت" in norm else "in"

    for pat in REF_PATTERNS:
        m = re.search(pat, raw, re.IGNORECASE)
        if m:
            result["reference"] = m.group(1)
            break

    for pat in CARD_PATTERNS:
        m = re.search(pat, raw, re.IGNORECASE)
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            result["card_last4"] = digits[-4:]
            result["card_mask"] = m.group(1).replace(" ", "")
            break

    d = re.search(JDATE, raw)
    t = re.search(TIME, raw)
    if d:
        jy, jm, jd = (int(x) for x in d.groups())
        try:
            gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
            hh, mm = (int(t.group(1)), int(t.group(2))) if t else (12, 0)
            result["datetime"] = datetime(gy, gm, gd, hh, mm).isoformat()
        except ValueError:
            pass

    for bank in BANKS:
        if f"بانک {bank}" in raw or f"{bank}" in raw.split("\n")[0]:
            result["bank"] = bank
            break

    score = 0.0
    score += 0.5 if result["amount"] else 0
    score += 0.2 if result["reference"] else 0
    score += 0.15 if result["datetime"] else 0
    score += 0.15 if result["card_last4"] else 0
    result["confidence"] = round(score, 2)
    return result


def looks_like_receipt(text: str) -> bool:
    p = parse_receipt(text)
    keywords = ("واریز", "انتقال", "پیگیری", "مرجع", "رسید", "کارت به کارت", "موفق", "تراکنش")
    return bool(p["amount"]) and any(k in (text or "") for k in keywords)
