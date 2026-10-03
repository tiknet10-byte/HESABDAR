"""AI assistant: answers questions about the business and performs actions via tools.

Uses Claude tool use when available; otherwise falls back to an offline
assistant that understands a few common questions, so the UI always works.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy.orm import Session

from ..services import reports, settings_store
from . import claude
from .tools import invoke, schema, tools_for

SYSTEM = """You are the accounting & business assistant of a beauty salon ("{salon}").
Today is {today} (Gregorian). Users speak Persian; answer in fluent, concise Persian.
All amounts in the database are in Rial; when talking to users convert to Toman (divide by 10) and format with thousands separators.
Use the tools to look up real data before answering - never invent numbers. For analysis, compare periods,
explain drivers (service lines, staff, payment accounts, deposits) and give 2-4 actionable recommendations.
Before any write action (creating customers, registering deposits) make sure the user asked for it.
{extra}"""

MAX_STEPS = 8


def chat(db: Session, user, messages: list[dict]) -> dict:
    """messages: [{role: user|assistant, content: str}] - returns {reply, tool_calls, mode}."""
    if not claude.available():
        return _offline(db, messages)
    tools = tools_for(user)
    system = SYSTEM.format(salon=settings_store.get(db, "salon.name"), today=date.today().isoformat(),
                           extra=settings_store.get(db, "ai.assistant_instructions") or "")
    convo: list[dict] = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("content")]
    calls: list[dict] = []
    for _ in range(MAX_STEPS):
        resp = claude.create(system=system, tools=[schema(t) for t in tools], messages=convo,
                             thinking={"type": "adaptive"}, output_config={"effort": "medium"})
        if resp.stop_reason == "refusal":
            return {"reply": "متأسفانه امکان پاسخ به این درخواست وجود ندارد.", "tool_calls": calls, "mode": "ai"}
        convo.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return {"reply": claude.text_of(resp), "tool_calls": calls, "mode": "ai"}
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            try:
                out = invoke(db, user, block.name, dict(block.input or {}))
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(out, ensure_ascii=False)[:60000]})
                calls.append({"tool": block.name, "input": block.input, "ok": True})
            except Exception as exc:  # report errors back to the model
                db.rollback()
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(exc), "is_error": True})
                calls.append({"tool": block.name, "input": block.input, "ok": False, "error": str(exc)})
        convo.append({"role": "user", "content": results})
    return {"reply": "پاسخ کامل نشد؛ لطفاً سؤال را دقیق‌تر بپرسید.", "tool_calls": calls, "mode": "ai"}


def _toman(rial: int) -> str:
    return f"{rial // 10:,} تومان"


def _offline(db: Session, messages: list[dict]) -> dict:
    q = (messages[-1]["content"] if messages else "").strip()
    s = reports.summary(db)
    lines = []
    if any(k in q for k in ("پیش‌بینی", "پیش بینی", "آینده", "forecast")):
        f = reports.forecast(db, 30)
        if f["points"]:
            lines.append(f"پیش‌بینی درآمد ۳۰ روز آینده: حدود {_toman(f['total'])}")
            if f.get("change_vs_last_period") is not None:
                lines.append(f"تغییر نسبت به ۳۰ روز گذشته: {f['change_vs_last_period'] * 100:+.0f}٪")
        else:
            lines.append(f["message"])
    elif any(k in q for k in ("بیعانه", "deposit")):
        lines.append(f"{s['deposits_held_count']} بیعانه باز به مجموع {_toman(s['deposits_held'])} دارید.")
    else:
        lines += [
            f"خلاصه ۳۰ روز اخیر: درآمد {_toman(s['revenue'])}، هزینه {_toman(s['expenses'])}، سود {_toman(s['net_profit'])}.",
            f"تعداد فاکتور {s['invoice_count']}، میانگین هر فاکتور {_toman(s['avg_ticket'])}.",
        ]
        if s["by_line"]:
            lines.append("پردرآمدترین لاین: " + s["by_line"][0]["name"])
    for ins in reports.insights(db)[:3]:
        lines.append("• " + ins["text"])
    lines.append("\n(دستیار هوش مصنوعی فعال نیست؛ برای تحلیل پیشرفته کلید API را در تنظیمات قرار دهید.)")
    return {"reply": "\n".join(lines), "tool_calls": [], "mode": "offline"}
