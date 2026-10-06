"""AI tool registry: the single list of capabilities shared by
  * the built-in assistant (Claude tool use),
  * the MCP server (Claude Desktop / Claude Code / any MCP client),
  * the REST endpoint /api/ai/tools (any function-calling LLM / automation).

Plugins contribute more tools via BasePlugin.ai_tools().
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.security import has_permission
from ..models import Alert, Customer, Deposit, Invoice, PaymentAccount, Service
from ..plugins.base import AITool
from ..services import accounting, learning, matching, reports
from ..services.search import fa_like


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def search_customers(db: Session, user, query: str, limit: int = 10) -> list[dict]:
    q = f"%{query}%"
    rows = db.scalars(select(Customer).where(or_(fa_like(Customer.full_name, query), Customer.mobile.like(q), Customer.instagram.like(q))).limit(limit))
    return [{"id": c.id, "name": c.full_name, "mobile": c.mobile, "instagram": c.instagram} for c in rows]


def customer_profile(db: Session, user, customer_id: int) -> dict:
    c = db.get(Customer, customer_id)
    if not c:
        return {"error": "customer not found"}
    invoices = db.scalars(select(Invoice).where(Invoice.customer_id == c.id).order_by(Invoice.issued_at.desc()).limit(10)).all()
    deposits = db.scalars(select(Deposit).where(Deposit.customer_id == c.id).order_by(Deposit.received_at.desc()).limit(10)).all()
    return {
        "id": c.id, "name": c.full_name, "mobile": c.mobile, "instagram": c.instagram, "notes": c.notes,
        "balance_rial": accounting.customer_balance(db, c.id),
        "recent_invoices": [{"number": i.number, "date": i.issued_at.date().isoformat(), "total": i.total, "status": i.status,
                             "services": [it.description for it in i.items]} for i in invoices],
        "deposits": [{"id": d.id, "amount": d.amount, "status": d.status, "date": d.received_at.date().isoformat()} for d in deposits],
    }


def list_services(db: Session, user) -> list[dict]:
    return reports.services_catalog(db)


def financial_summary(db: Session, user, start: str | None = None, end: str | None = None) -> dict:
    return reports.summary(db, _d(start), _d(end))


def revenue_timeseries(db: Session, user, start: str | None = None, end: str | None = None) -> list[dict]:
    return reports.daily_series(db, _d(start), _d(end))


def revenue_forecast(db: Session, user, days: int = 30) -> dict:
    return reports.forecast(db, days=min(int(days), 180))


def business_insights(db: Session, user) -> dict:
    return {"insights": reports.insights(db), "top_customers": reports.customer_rfm(db, 15)}


def open_deposits(db: Session, user) -> list[dict]:
    rows = db.execute(select(Deposit, Customer.full_name).join(Customer, Customer.id == Deposit.customer_id)
                      .where(Deposit.status == "held").order_by(Deposit.received_at)).all()
    return [{"id": d.id, "customer": n, "amount": d.amount, "date": d.received_at.date().isoformat(), "service_id": d.service_id} for d, n in rows]


def alerts(db: Session, user, unread_only: bool = True) -> list[dict]:
    q = select(Alert).order_by(Alert.at.desc()).limit(30)
    if unread_only:
        q = q.where(Alert.is_read.is_(False))
    return [{"id": a.id, "level": a.level, "title": a.title, "message": a.message, "at": a.at.isoformat()} for a in db.scalars(q)]


def reconciliation_status(db: Session, user) -> dict:
    return matching.unreconciled_summary(db)


def guess_service(db: Session, user, text: str = "", amount: int | None = None, customer_id: int | None = None) -> list[dict]:
    if amount:
        return learning.guess_service_for_deposit(db, int(amount), customer_id, text)
    return learning.classify_text(db, text)


def create_customer(db: Session, user, full_name: str, mobile: str | None = None, instagram: str | None = None) -> dict:
    c, created = accounting.find_or_create_customer(db, full_name, mobile, instagram, source="ai", user=user)
    db.commit()
    return {"id": c.id, "name": c.full_name, "created": created}


def register_deposit(db: Session, user, customer_id: int, amount_rial: int, payment_account_id: int,
                     service_id: int | None = None, reference: str | None = None, note: str = "") -> dict:
    c = db.get(Customer, customer_id)
    pa = db.get(PaymentAccount, payment_account_id)
    if not c or not pa:
        return {"error": "customer or payment account not found"}
    dep = accounting.record_deposit(db, customer=c, amount=int(amount_rial), payment_account=pa, service_id=service_id,
                                    reference=reference, source="ai", notes=note, user=user)
    db.commit()
    return {"deposit_id": dep.id, "status": dep.status}


def payment_accounts(db: Session, user) -> list[dict]:
    return accounting.account_balances(db)


def teach_service_alias(db: Session, user, service_id: int, alias: str) -> dict:
    svc = db.get(Service, service_id)
    if not svc:
        return {"error": "service not found"}
    learning.add_alias(db, svc, alias)
    db.commit()
    return {"ok": True, "aliases": svc.aliases}


_OBJ = {"type": "object"}
CORE_TOOLS: list[AITool] = [
    AITool("search_customers", "Search customers by name, mobile or Instagram handle.",
           {**_OBJ, "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}, search_customers),
    AITool("customer_profile", "Full profile of one customer: balance, deposits, recent invoices and services.",
           {**_OBJ, "properties": {"customer_id": {"type": "integer"}}, "required": ["customer_id"]}, customer_profile),
    AITool("list_services", "Salon service catalog with lines, list prices and learned real prices (amounts in Rial).",
           {**_OBJ, "properties": {}}, list_services),
    AITool("financial_summary", "Revenue, expenses, profit, cash-in, deposits, breakdown by line/account/staff/service for a date range (ISO dates, Gregorian). Amounts in Rial.",
           {**_OBJ, "properties": {"start": {"type": "string"}, "end": {"type": "string"}}}, financial_summary, "reports"),
    AITool("revenue_timeseries", "Daily revenue, expenses and cash-in between two ISO dates.",
           {**_OBJ, "properties": {"start": {"type": "string"}, "end": {"type": "string"}}}, revenue_timeseries, "reports"),
    AITool("revenue_forecast", "Forecast daily revenue for the next N days (trend + weekday seasonality) plus booked appointments.",
           {**_OBJ, "properties": {"days": {"type": "integer"}}}, revenue_forecast, "reports"),
    AITool("business_insights", "Automatic insights (growth, churn, no-shows) and top customers segmentation.",
           {**_OBJ, "properties": {}}, business_insights, "reports"),
    AITool("open_deposits", "List deposits (بیعانه) still held and not yet used.", {**_OBJ, "properties": {}}, open_deposits),
    AITool("alerts", "System alerts such as receipt/bank mismatches.",
           {**_OBJ, "properties": {"unread_only": {"type": "boolean"}}}, alerts),
    AITool("reconciliation_status", "Counts of unmatched bank transactions and pending/mismatched receipts.",
           {**_OBJ, "properties": {}}, reconciliation_status),
    AITool("payment_accounts", "POS terminals, bank cards and accounts with current ledger balances.",
           {**_OBJ, "properties": {}}, payment_accounts, "finance"),
    AITool("guess_service", "Guess which salon service a text (chat message) or deposit amount refers to, using learned knowledge.",
           {**_OBJ, "properties": {"text": {"type": "string"}, "amount": {"type": "integer"}, "customer_id": {"type": "integer"}}}, guess_service),
    AITool("create_customer", "Create a customer (or return the existing one with the same mobile/Instagram).",
           {**_OBJ, "properties": {"full_name": {"type": "string"}, "mobile": {"type": "string"}, "instagram": {"type": "string"}},
            "required": ["full_name"]}, create_customer, "write"),
    AITool("register_deposit", "Record a deposit (بیعانه) received from a customer into a payment account. Amount in Rial.",
           {**_OBJ, "properties": {"customer_id": {"type": "integer"}, "amount_rial": {"type": "integer"},
                                   "payment_account_id": {"type": "integer"}, "service_id": {"type": "integer"},
                                   "reference": {"type": "string"}, "note": {"type": "string"}},
            "required": ["customer_id", "amount_rial", "payment_account_id"]}, register_deposit, "write"),
    AITool("teach_service_alias", "Teach the system another name customers use for a service (continuous learning).",
           {**_OBJ, "properties": {"service_id": {"type": "integer"}, "alias": {"type": "string"}}, "required": ["service_id", "alias"]},
           teach_service_alias, "write"),
]


def all_tools() -> list[AITool]:
    from ..plugins.manager import manager

    return [*CORE_TOOLS, *manager.ai_tools()]


def tools_for(user) -> list[AITool]:
    return [t for t in all_tools() if has_permission(user.role, t.permission)]


def schema(tool: AITool) -> dict:
    return {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}


def invoke(db: Session, user, name: str, args: dict[str, Any]) -> Any:
    tool = next((t for t in all_tools() if t.name == name), None)
    if tool is None:
        raise KeyError(f"unknown tool {name}")
    if not has_permission(user.role, tool.permission):
        raise PermissionError(f"role {user.role} cannot use {name}")
    result = tool.handler(db, user, **(args or {}))
    return _jsonable(result)


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v
