from __future__ import annotations

import hashlib
import json

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import Alert, AuditLog, local_now
from ..core.events import bus


def _digest(prev: str, row: AuditLog) -> str:
    body = json.dumps(
        [prev, row.at.isoformat(), row.user_id, row.actor, row.action, row.entity, row.entity_id, row.data],
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def write_lock(db: Session) -> None:
    """Take the database's write lock now (a write that changes nothing); it is kept until commit or rollback, so
    what is read after it is the latest and nobody else can write in between (two requests at the same moment)."""
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("UPDATE audit_logs SET id = id WHERE id = -1"))


def audit(db: Session, action: str, entity: str = "", entity_id: object = "", data: dict | None = None,
          user=None, actor: str | None = None) -> AuditLog:
    pending = db.info.get("audit_last")
    if pending is not None and pending in db.new:  # this request's previous audit row, not written yet: link to it
        prev = pending.hash
    else:
        write_lock(db)  # two audits at once must not both link to the same last row (the chain would look tampered)
        last = db.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
        prev = last.hash if last else ""
    row = AuditLog(
        at=local_now(), user_id=getattr(user, "id", None),
        actor=actor or getattr(user, "username", None) or "system",
        action=action, entity=entity, entity_id=str(entity_id or ""), data=data or {},
        prev_hash=prev,
    )
    row.hash = _digest(row.prev_hash, row)
    db.add(row)
    db.info["audit_last"] = row
    return row


def verify_audit_chain(db: Session) -> dict:
    """Check every row (not only up to the first break, so a later change is still found): its own hash and its
    link to the row before."""
    prev = ""
    count = 0
    breaks: list[int] = []
    for row in db.scalars(select(AuditLog).order_by(AuditLog.id)):
        if row.prev_hash != prev or _digest(row.prev_hash, row) != row.hash:
            breaks.append(row.id)
        prev = row.hash
        count += 1
    if breaks:
        return {"ok": False, "broken_at": breaks[0], "breaks": breaks[:100], "checked": count}
    return {"ok": True, "checked": count}


def raise_alert(db: Session, kind: str, title: str, message: str = "", level: str = "warning",
                ref_type: str | None = None, ref_id: int | None = None) -> Alert:
    alert = Alert(kind=kind, title=title, message=message, level=level, ref_type=ref_type, ref_id=ref_id)
    db.add(alert)
    db.flush()
    bus.emit("alert.created", {"id": alert.id, "kind": kind, "title": title, "level": level}, db=db)
    return alert
