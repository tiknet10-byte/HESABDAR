"""Search helpers for Persian text.

Names typed on different keyboards/software use Arabic «ي» / «ك» or Persian «ی» / «ک», and a half-space or a
space between word parts. Both the stored value and the query are compared in one unified form, so «مریم» finds
«مريم» and the other way round - the stored data itself is left unchanged.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

from .textutil import normalize_text

_PAIRS = [("ي", "ی"), ("ى", "ی"), ("ك", "ک"), ("ة", "ه"), ("‌", " "), ("‏", ""), ("أ", "ا"), ("إ", "ا")]


def fa_norm(col) -> ColumnElement:  # noqa: ANN001
    """SQL expression: the column in unified Persian form (lower-cased)."""
    expr = col
    for a, b in _PAIRS:
        expr = func.replace(expr, a, b)
    return func.lower(expr)


def fa_like(col, q: str) -> ColumnElement:  # noqa: ANN001
    """col contains q, ignoring ی/ي, ک/ك, half-spaces and Persian/Arabic digits."""
    return fa_norm(col).like(f"%{fa_query(q)}%")


def fa_query(q: str) -> str:
    out = normalize_text(q or "")
    for a, b in _PAIRS:
        out = out.replace(a, b)
    return out.replace("%", "").replace("_", " ").strip()
