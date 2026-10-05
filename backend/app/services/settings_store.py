from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import Setting

DEFAULTS: dict[str, Any] = {
    "salon.name": "سالن زیبایی",
    "salon.phone": "",
    "salon.address": "",
    "matching.window_hours": 48,
    "matching.grace_hours": 24,
    "matching.require_bank_confirmation": True,
    "bot.auto_reply": True,
    "bot.ask_name_message": "سلام 🌸 رسید شما دریافت شد. لطفاً نام و نام خانوادگی خود را بفرمایید تا بیعانه به نام شما ثبت شود.",
    "bot.confirm_message": "{name} عزیز، بیعانه {amount} تومانی شما با موفقیت ثبت شد. 💐",
    "bot.mismatch_message": "رسید شما دریافت شد و پس از تأیید بانک ثبت می‌شود. 🙏",
    "ai.assistant_instructions": "",
    "backup.mirror_dir": "",
    "booking.open": "10:00",
    "booking.close": "20:00",
    "booking.slot_minutes": 15,
    "booking.days_off": [4],  # Python weekday numbers: 4 = Friday
    "booking.auto": False,  # automatically book the first free slot when a deposit is registered
    "ai.api_key": "",
    "ai.model": "",
    "ai.base_url": "",
}


def get(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(Setting, key)
    if row is not None:
        return row.value
    return DEFAULTS.get(key, default)


def set_value(db: Session, key: str, value: Any) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def all_settings(db: Session) -> dict[str, Any]:
    out = dict(DEFAULTS)
    for row in db.query(Setting).all():
        out[row.key] = row.value
    return out
