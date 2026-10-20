"""Who a bank line's money went to or came from, without keeping card numbers (1.23).

* A full card number (16 digits) or IBAN (IR + 24 digits) is kept only as a keyed hash (to know it again: the staff
  member whose card it is) and a mask (6037-99**-****-1234, IR12…3456) - never the number itself; the description of a
  bank line has every such number masked before it is saved.
* The direction of a bank SMS: the sign written with the amount, the card-to-card cards (the salon's card is the
  source: money went out), then the words (واریز / برداشت, خرید ...); unclear = "unknown" (never matched or booked by
  itself: the inbox asks).
"""
from __future__ import annotations

import hashlib
import hmac
import re

from .textutil import normalize_text, to_en_digits

CARD = re.compile(r"(?<![\d*])(\d{4})[ \-]?(\d{4})[ \-]?(\d{4})[ \-]?(\d{4})(?![\d*])")
IBAN = re.compile(r"(?i)(?<![A-Z\d])IR[ \-]?((?:\d[ \-]?){23}\d)(?!\d)")
# a card with masked middle digits as banks print them: 6037-99**-****-1234, 603799******1234
MASKED = re.compile(r"(?<![\d*])(\d{4}[ \-]?\d{2})[\d*xX× \-]{4,9}?(\d{4})(?![\d*])")
NAME = re.compile(r"(?:به\s*نام|بنام|نام\s*گیرنده|نام\s*صاحب\s*(?:کارت|حساب)|گیرنده|ذینفع|صاحب\s*حساب)\s*[:：]?\s*"
                  r"((?:خانم|آقای|آقا|شرکت)?\s*[ء-يپچژکگیۀ‌]+"
                  r"(?:\s+[ء-يپچژکگیۀ‌]+){0,3})")
NAME_STOP = {"شماره", "مبلغ", "کارت", "حساب", "به", "از", "بانک", "شبا", "پیگیری", "تاریخ", "ساعت", "با", "در", "مانده", "ریال",
             "تومان", "انتقال", "واریز", "برداشت", "موفق", "شد"}


def _secret() -> bytes:
    from ..core.config import get_settings

    return hashlib.sha256(("hesabdar-cards:" + get_settings().secret_key).encode()).digest()


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def iban_ok(iban: str) -> bool:
    s = iban[4:] + iban[:4]
    n = "".join(str(int(ch, 36)) for ch in s)
    return int(n) % 97 == 1


def normalize_number(text: str) -> tuple[str, str] | None:
    """("card", 16 digits) or ("iban", "IR" + 24 digits) of what a person typed, else None."""
    t = to_en_digits(text or "").upper().replace(" ", "").replace("-", "")
    if re.fullmatch(r"\d{16}", t):
        return ("card", t)
    if re.fullmatch(r"IR\d{24}", t):
        return ("iban", t)
    if re.fullmatch(r"\d{24}", t):
        return ("iban", "IR" + t)
    return None


def number_hash(kind: str, value: str) -> str:
    return hmac.new(_secret(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def mask(kind: str, value: str) -> str:
    if kind == "card":
        return f"{value[:4]}-{value[4:6]}**-****-{value[-4:]}"
    return f"{value[:4]}…{value[-4:]}"


def check_number(text: str) -> tuple[str, str]:
    """A card or IBAN typed on the staff page: (kind, value), or ValueError in Persian."""
    got = normalize_number(text)
    if got is None:
        raise ValueError("شمارهٔ کارت ۱۶ رقم است و شبا با IR و ۲۴ رقم؛ شماره را دوباره نگاه کنید")
    kind, value = got
    if kind == "card" and not luhn_ok(value):
        raise ValueError("این شمارهٔ کارت درست نیست (یک رقم اشتباه است)")
    if kind == "iban" and not iban_ok(value):
        raise ValueError("این شمارهٔ شبا درست نیست (یک رقم اشتباه است)")
    return kind, value


def mask_text(text: str | None) -> str:
    """The text with every full card number and IBAN masked (descriptions of bank lines are kept like this)."""
    if not text:
        return text or ""
    t = to_en_digits(text)
    t = IBAN.sub(lambda m: mask("iban", "IR" + re.sub(r"\D", "", m.group(1))), t)
    return CARD.sub(lambda m: mask("card", "".join(m.groups())), t)


def own_masks(db) -> set[tuple[str, str]]:  # noqa: ANN001
    """(first 6, last 4) of the salon's own cards (an own card in a line is not the other side)."""
    from sqlalchemy import select

    from ..models import PaymentAccount

    out = set()
    for (m,) in db.execute(select(PaymentAccount.card_mask).where(PaymentAccount.card_mask.is_not(None))):
        d = re.sub(r"\D", "", m or "")
        if len(d) >= 10:
            out.add((d[:6], d[-4:]))
    return out


def own_ibans(db) -> set[str]:  # noqa: ANN001
    from sqlalchemy import select

    from ..models import PaymentAccount

    return {re.sub(r"[^\dIR]", "", (i or "").upper()) for (i,) in db.execute(select(PaymentAccount.iban).where(PaymentAccount.iban.is_not(None)))}


def counterparty(text: str | None, *, card: str | None = None, iban: str | None = None, name: str | None = None,
                 own: set[tuple[str, str]] | None = None, own_iban: set[str] | None = None) -> dict:
    """{hash, mask, name, last4} of the other side: an explicit card / IBAN (a statement column, the bank API) first,
    else the first full card / IBAN in the text that is not the salon's own; a masked card only gives a mask (no hash:
    too weak to know a person by)."""
    own = own or set()
    own_iban = own_iban or set()
    out: dict = {"hash": None, "mask": None, "name": None, "last4": None}
    t = to_en_digits(text or "")
    found: list[tuple[str, str]] = []
    for raw in (card, iban):
        got = normalize_number(raw or "")
        if got:
            found.append(got)
    for m in IBAN.finditer(t):
        found.append(("iban", "IR" + re.sub(r"\D", "", m.group(1))))
    for m in CARD.finditer(t):
        found.append(("card", "".join(m.groups())))
    for kind, value in found:
        if (kind == "card" and (value[:6], value[-4:]) in own) or (kind == "iban" and value in own_iban):
            continue
        out.update(hash=number_hash(kind, value), mask=mask(kind, value), last4=value[-4:])
        break
    if out["mask"] is None:
        for m in MASKED.finditer(t):
            first, last = re.sub(r"\D", "", m.group(1)), m.group(2)
            if (first[:6], last) in own:
                continue
            out.update(mask=f"{first[:4]}-{first[4:6]}**-****-{last}", last4=last)
            break
    nm = (name or "").strip()
    if not nm:
        m = NAME.search(text or "")
        if m:
            words = []
            for w in m.group(1).split():
                if w in NAME_STOP:
                    break
                words.append(w)
            nm = " ".join(words)
    out["name"] = normalize_text(nm)[:128] if nm and len(nm) >= 3 else None
    return out


# ------------------------------------------------------------------ direction of a bank SMS
STRONG_IN = ("واریز", "بستانکار", "افزایش موجودی", "واریزی")
STRONG_OUT = ("برداشت", "بدهکار", "خرید", "کسر", "برداشتی")
WEAK_IN = ("سود", "انتقال به حساب شما", "دریافت")
WEAK_OUT = ("انتقال از", "پرداخت", "انتقال وجه", "کارت به کارت", "پایا", "ساتنا")


def sms_direction(text: str, salon_last4: set[str] | None = None) -> str:
    """in | out | unknown."""
    raw = to_en_digits(text or "").replace("٬", ",")
    # the sign written with the amount: «1,500,000-», «-1,500,000», «+1500000» (not a date like 1402/05/01-12:30, a
    # card 6037-9912-... or a phone 0912-...)
    num = r"(?:\d{1,3}(?:,\d{3})+|\d{4,})"
    m = re.search(rf"(?<!\d)([+\-])\s?{num}(?![\d,/:])|(?<![\d,/:]){num}\s?([+\-])(?!\d)", raw)
    if m:
        return "out" if (m.group(1) or m.group(2)) == "-" else "in"
    from .payout_receipt import DEST_LABELS, SOURCE_LABELS, _labelled_card

    salon = salon_last4 or set()
    src, dest = _labelled_card(raw, SOURCE_LABELS), _labelled_card(raw, DEST_LABELS)
    if src and src in salon and dest not in salon:
        return "out"
    if dest and dest in salon and src not in salon:
        return "in"
    t = normalize_text(raw)
    for phrase in ("قابل برداشت", "کارت برداشت", "قابل پرداخت", "شناسه پرداخت", "شماره پرداخت"):
        t = t.replace(normalize_text(phrase), " ")  # «موجودی قابل برداشت» in an SMS of money that came in
    has = lambda words: any(normalize_text(w) in t for w in words)  # noqa: E731
    sin, sout = has(STRONG_IN), has(STRONG_OUT)
    if sin != sout:
        return "in" if sin else "out"
    if sin and sout:
        return "unknown"
    win, wout = has(WEAK_IN), has(WEAK_OUT)
    if win != wout:
        return "in" if win else "out"
    return "unknown"


def seal_old(db) -> int:  # noqa: ANN001
    """Once (1.23): bank lines and payout receipts saved before keep no whole card number / IBAN any more (the other
    side as hash + mask), and a bank connection's token is encrypted."""
    from sqlalchemy import select

    from ..core.security import encrypt_secret
    from ..models import BankTransaction, PaymentAccount, StaffPayout
    from . import settings_store

    if settings_store.get(db, "privacy.sealed_1_23"):
        return 0
    n = 0
    own, own_i = own_masks(db), own_ibans(db)
    for tx in db.scalars(select(BankTransaction)):
        raw_card = tx.counterparty_card or ""
        if tx.counterparty_hash is None and (CARD.search(to_en_digits(tx.description or "")) or IBAN.search(tx.description or "")
                                             or len(re.sub(r"\D", "", raw_card)) >= 16):
            party = counterparty(tx.description, card=raw_card, own=own, own_iban=own_i)
            tx.counterparty_hash, tx.counterparty_mask = party["hash"], party["mask"]
            tx.counterparty_name = tx.counterparty_name or party["name"]
            n += 1
        masked = mask_text(tx.description or "")
        if masked != (tx.description or ""):
            tx.description = masked
        if raw_card and len(re.sub(r"\D", "", raw_card)) >= 10:
            from ..core.security import mask_card

            tx.counterparty_card = tx.counterparty_mask or mask_card(raw_card)
    for p in db.scalars(select(StaffPayout).where(StaffPayout.parsed.is_not(None))):
        parsed = dict(p.parsed or {})
        new = {k: (mask_text(v) if isinstance(v, str) else v) for k, v in parsed.items()}
        if new != parsed:
            p.parsed = new  # a new dict (a JSON column changed in place is not saved)
    for a in db.scalars(select(PaymentAccount).where(PaymentAccount.provider_config.is_not(None))):
        cfg = dict(a.provider_config or {})
        sealed = {k: (encrypt_secret(v) if any(x in k for x in ("token", "secret", "password", "key")) and isinstance(v, str)
                      and v and not v.startswith("enc:") else v) for k, v in cfg.items()}
        if sealed != cfg:
            a.provider_config = sealed
    settings_store.set_value(db, "privacy.sealed_1_23", True)
    return n
