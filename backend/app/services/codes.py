"""Codes of service lines and services.

Every line gets a number (1, 2, 3 ...) and every service the line's number followed by a two-digit sequence
(line 3 -> 301, 302 ...). Codes are given automatically to anything created without one - in any part of the
program (settings, import, seed) - and stay fixed afterwards, so renaming never breaks anything. They can be
edited by hand, but never duplicated.
"""
from __future__ import annotations

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from ..models import Service, ServiceLine


def _num(code: str | None) -> int | None:
    return int(code) if code and code.isdigit() else None


def next_line_code(db: Session, taken: set[str] | None = None) -> str:
    used = {c for c in db.scalars(select(ServiceLine.code)) if c} | (taken or set())
    return str(max((_num(c) or 0 for c in used), default=0) + 1)


def next_service_code(db: Session, line: ServiceLine, taken: set[str] | None = None) -> str:
    prefix = line.code or ""
    used = {c for c in db.scalars(select(Service.code)) if c} | (taken or set())
    seqs = [int(c[len(prefix):]) for c in used if prefix and c.startswith(prefix) and c[len(prefix):].isdigit()
            and len(c) - len(prefix) >= 2]
    n = max(seqs, default=0) + 1
    code = f"{prefix}{n:02d}"
    while code in used:  # e.g. a hand-made code in the way
        n += 1
        code = f"{prefix}{n:02d}"
    return code


def assign_missing(db: Session) -> int:
    """Number lines and services that have no code yet (existing data after an update)."""
    count = 0
    with db.no_autoflush:
        taken_l: set[str] = set()
        for line in db.scalars(select(ServiceLine).where(ServiceLine.code.is_(None)).order_by(ServiceLine.id)):
            line.code = next_line_code(db, taken_l)
            taken_l.add(line.code)
            count += 1
        db.flush()
        taken_s: set[str] = set()
        for svc in db.scalars(select(Service).where(Service.code.is_(None)).order_by(Service.line_id, Service.id)):
            line = db.get(ServiceLine, svc.line_id)
            if line is None:
                continue
            svc.code = next_service_code(db, line, taken_s)
            taken_s.add(svc.code)
            count += 1
    db.flush()
    return count


@event.listens_for(Session, "before_flush")
def _give_codes(session: Session, _ctx, _instances) -> None:  # noqa: ANN001
    new_lines = [o for o in session.new if isinstance(o, ServiceLine) and not o.code]
    new_services = [o for o in session.new if isinstance(o, Service) and not o.code]
    if not new_lines and not new_services:
        return
    with session.no_autoflush:
        taken_l = {o.code for o in session.new if isinstance(o, ServiceLine) and o.code}
        for line in new_lines:
            line.code = next_line_code(session, taken_l)
            taken_l.add(line.code)
        taken_s = {o.code for o in session.new if isinstance(o, Service) and o.code}
        for svc in new_services:
            line = svc.line if svc.line is not None else (session.get(ServiceLine, svc.line_id) if svc.line_id else None)
            if line is None or not line.code:
                continue  # numbered on the next start-up
            svc.code = next_service_code(session, line, taken_s)
            taken_s.add(svc.code)
