"""In-process event bus. Core modules emit events; plugins subscribe to them.

Event names used by the core (payloads are plain dicts):
  customer.created, deposit.created, deposit.applied, payment.created,
  invoice.issued, expense.created, bank_transaction.imported,
  receipt.received, receipt.matched, receipt.mismatch, alert.created,
  backup.created
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger("hesabdar.events")
Handler = Callable[[str, dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler) -> None:
        """Subscribe to an event name; use "*" to receive every event."""
        self._handlers[event].append(handler)

    def unsubscribe_all(self, owner_prefix: str) -> None:
        for name, handlers in self._handlers.items():
            self._handlers[name] = [h for h in handlers if not getattr(h, "__module__", "").startswith(owner_prefix)]

    def emit(self, event: str, payload: dict[str, Any], db: Session | None = None) -> None:
        """Handlers run inside the caller's transaction (payload["_db"]) within a SAVEPOINT,
        so a plugin can write related rows atomically, and a failing plugin is rolled back alone."""
        for handler in [*self._handlers.get(event, []), *self._handlers.get("*", [])]:
            try:
                if db is not None:
                    with db.begin_nested():
                        handler(event, {**payload, "_db": db})
                else:
                    handler(event, payload)
            except Exception:  # a broken plugin must never break accounting
                log.exception("event handler failed for %s", event)


bus = EventBus()
