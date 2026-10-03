from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Alert, AuditLog, utcnow
from ..core.events import bus


def _digest(prev: str, row: AuditLog) -> str:
    body = json.dumps(
        [prev, row.at.isoformat(), row.user_id, row.actor, row.action, row.entity, row.entity_id, row.data],
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def audit(db: Session, action: str, entity: str = "", entity_id: object = "", data: dict | None = None,
          user=None, actor: str | None = None) -> AuditLog:
    last = db.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
    row = AuditLog(
        at=utcnow(), user_id=getattr(user, "id", None),
        actor=actor or getattr(user, "username", None) or "system",
        action=action, entity=entity, entity_id=str(entity_id or ""), data=data or {},
        prev_hash=last.hash if last else "",
    )
    row.hash = _digest(row.prev_hash, row)
    db.add(row)
    return row


def verify_audit_chain(db: Session) -> dict:
    prev = ""
    count = 0
    for row in db.scalars(select(AuditLog).order_by(AuditLog.id)):
        if row.prev_hash != prev or _digest(prev, row) != row.hash:
            return {"ok": False, "broken_at": row.id, "checked": count}
        prev = row.hash
        count += 1
    return {"ok": True, "checked": count}


def raise_alert(db: Session, kind: str, title: str, message: str = "", level: str = "warning",
                ref_type: str | None = None, ref_id: int | None = None) -> Alert:
    alert = Alert(kind=kind, title=title, message=message, level=level, ref_type=ref_type, ref_id=ref_id)
    db.add(alert)
    db.flush()
    bus.emit("alert.created", {"id": alert.id, "kind": kind, "title": title, "level": level}, db=db)
    return alert
