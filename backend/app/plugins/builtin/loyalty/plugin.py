"""Loyalty points (باشگاه مشتریان) - a small example plugin showing the extension API.

Every invoice earns points; the AI can look up a customer's points. Use this file as a
template for new plugins: copy the folder into backend/plugins/<your_plugin>/.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ....api.deps import require
from ....core.db import get_db
from ....models import KnowledgeItem
from ...base import AITool, BasePlugin


def _points(db: Session, customer_id: int) -> KnowledgeItem:
    item = db.scalar(select(KnowledgeItem).where(KnowledgeItem.kind == "loyalty", KnowledgeItem.key == str(customer_id)))
    if item is None:
        item = KnowledgeItem(kind="loyalty", key=str(customer_id), value={"points": 0})
        db.add(item)
    return item


class Plugin(BasePlugin):
    name = "loyalty"
    title = "باشگاه مشتریان"
    description = "امتیازدهی خودکار به مشتریان به ازای هر خرید (نمونه افزونه)"
    version = "0.5.0"
    category = "operations"
    status = "beta"
    config_schema = {"type": "object", "properties": {
        "rial_per_point": {"type": "integer", "title": "هر چند ریال = ۱ امتیاز", "default": 100000}}}
    default_config = {"rial_per_point": 100000}

    def setup(self) -> None:
        def on_invoice(event: str, payload: dict) -> None:
            per = int(self.ctx.config.get("rial_per_point") or 100000)
            db: Session = payload["_db"]  # same transaction as the invoice
            item = _points(db, payload["customer_id"])
            item.value = {"points": int((item.value or {}).get("points", 0)) + payload["total"] // per}
            db.flush()

        self.events = {"invoice.issued": on_invoice}
        r = APIRouter()

        @r.get("/customers/{customer_id}")
        def get_points(customer_id: int, db: Session = Depends(get_db), _=Depends(require("read"))):
            return _points(db, customer_id).value

        self.router = r

    def ai_tools(self) -> list[AITool]:
        def points(db, user, customer_id: int):  # noqa: ANN001, ANN202
            return _points(db, customer_id).value
        return [AITool("loyalty_points", "Loyalty points balance of a customer.",
                       {"type": "object", "properties": {"customer_id": {"type": "integer"}}, "required": ["customer_id"]}, points)]
