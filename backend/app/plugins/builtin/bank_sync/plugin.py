"""Bank connection (اتصال به بانک).

Three ways to get bank data into the system, all feeding the same reconciliation engine:
  1. Open-banking / bank API provider (periodic sync) - generic HTTP adapter, configure per account.
  2. Bank SMS forwarded from the salon's phone (an SMS-forwarder app posts to the webhook).
  3. Statement file import (CSV exported from internet banking).
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ....api.deps import require
from ....core.db import SessionLocal, get_db
from ....core.security import verify_signature
from ....models import BankTransaction, PaymentAccount
from ....services import matching
from ....services.jalali import jalali_to_gregorian
from ....services.receipt_parser import parse_receipt
from ....services.textutil import parse_amount, to_en_digits
from ...base import AITool, BasePlugin

log = logging.getLogger("hesabdar.bank_sync")


# ------------------------------------------------------------------ providers
class BankProvider:
    """Implement fetch() for a real bank / aggregator API."""
    key = "base"

    def fetch(self, account: PaymentAccount, since: datetime) -> list[dict]:
        raise NotImplementedError


class GenericHttpProvider(BankProvider):
    """Configurable adapter for open-banking aggregators.

    account.provider_config = {
      "url": "https://api.example-bank/v1/accounts/{account}/transactions?from={since}",
      "token": "...", "account": "IR..",
      "items_path": "data.transactions",
      "fields": {"id": "id", "amount": "amount", "date": "date", "direction": "type",
                 "reference": "trace_no", "description": "description", "card": "source_card"}
    }
    """
    key = "generic_http"

    def fetch(self, account: PaymentAccount, since: datetime) -> list[dict]:
        cfg = account.provider_config or {}
        url = cfg["url"].format(account=cfg.get("account", ""), since=since.isoformat())
        resp = httpx.get(url, headers={"Authorization": f"Bearer {cfg.get('token', '')}"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for part in (cfg.get("items_path") or "").split("."):
            if part:
                data = data[part]
        f = {"id": "id", "amount": "amount", "date": "date", "direction": "direction", "reference": "reference",
             "description": "description", "card": "card", **cfg.get("fields", {})}
        out = []
        for it in data:
            amount = int(it[f["amount"]])
            direction = str(it.get(f["direction"], "in")).lower()
            out.append({"external_id": str(it[f["id"]]), "amount": abs(amount),
                        "direction": "out" if amount < 0 or direction in ("out", "debit", "withdraw", "برداشت") else "in",
                        "occurred_at": datetime.fromisoformat(str(it[f["date"]]).replace("Z", "")),
                        "reference": it.get(f["reference"]), "description": it.get(f["description"], ""),
                        "counterparty_card": it.get(f["card"])})
        return out


class DemoProvider(BankProvider):
    """Returns nothing; placeholder to show how accounts are linked to providers."""
    key = "demo"

    def fetch(self, account: PaymentAccount, since: datetime) -> list[dict]:
        return []


PROVIDERS: dict[str, BankProvider] = {p.key: p for p in (GenericHttpProvider(), DemoProvider())}


def sync_account(db: Session, account: PaymentAccount) -> dict:
    provider = PROVIDERS.get(account.provider or "")
    if provider is None:
        return {"account": account.id, "error": "no provider"}
    last = db.scalar(select(BankTransaction.occurred_at).where(BankTransaction.payment_account_id == account.id)
                     .order_by(BankTransaction.occurred_at.desc()).limit(1))
    since = (last or datetime.now() - timedelta(days=7)) - timedelta(hours=6)
    new = 0
    for item in provider.fetch(account, since):
        _, created = matching.import_bank_transaction(db, payment_account_id=account.id, source="bank_api", **item)
        new += int(created)
    db.commit()
    return {"account": account.id, "new": new}


def find_account_for_sms(db: Session, parsed: dict, sender: str | None) -> PaymentAccount | None:
    accounts = db.scalars(select(PaymentAccount).where(PaymentAccount.is_active.is_(True), PaymentAccount.kind.in_(["card", "bank", "pos"]))).all()
    last4 = parsed.get("card_last4")
    for a in accounts:
        if last4 and a.card_mask and a.card_mask.endswith(last4):
            return a
    for a in accounts:
        if parsed.get("bank") and parsed["bank"] in (a.bank_name or ""):
            return a
    return None


def ingest_bank_sms(db: Session, text: str, sender: str | None = None, account_id: int | None = None) -> dict:
    p = parse_receipt(text)
    if not p["amount"]:
        return {"ok": False, "reason": "amount not found"}
    account = db.get(PaymentAccount, account_id) if account_id else find_account_for_sms(db, p, sender)
    when = datetime.fromisoformat(p["datetime"]) if p["datetime"] else datetime.now()
    ext = p["reference"] or f"sms-{when:%Y%m%d%H%M}-{p['amount']}-{p['direction']}"
    tx, created = matching.import_bank_transaction(
        db, payment_account_id=account.id if account else None, external_id=ext, amount=p["amount"], occurred_at=when,
        direction=p["direction"], description=text[:500], reference=p["reference"], source="bank_sms")
    db.commit()
    return {"ok": True, "transaction_id": tx.id, "created": created, "status": tx.status, "parsed": p}


def parse_statement_csv(content: str) -> list[dict]:
    """Flexible CSV: detects columns by Persian/English header names."""
    reader = csv.DictReader(io.StringIO(to_en_digits(content)))
    aliases = {
        "date": ["date", "تاریخ", "تاریخ تراکنش"], "time": ["time", "ساعت", "زمان"],
        "deposit": ["deposit", "credit", "واریز", "بستانکار"], "withdraw": ["withdraw", "debit", "برداشت", "بدهکار"],
        "amount": ["amount", "مبلغ"], "description": ["description", "شرح", "توضیحات"],
        "reference": ["reference", "شماره پیگیری", "پیگیری", "شماره مرجع", "سند", "شماره سند"],
    }
    cols = {}
    for key, names in aliases.items():
        for h in reader.fieldnames or []:
            if h and h.strip() in names:
                cols[key] = h
                break
    rows = []
    for i, r in enumerate(reader):
        dep = parse_amount(r.get(cols.get("deposit", ""), "") or "") or 0
        wd = parse_amount(r.get(cols.get("withdraw", ""), "") or "") or 0
        amt = parse_amount(r.get(cols.get("amount", ""), "") or "") or 0
        if not (dep or wd or amt):
            continue
        direction = "in" if dep or (amt and not wd and not str(r.get(cols.get("amount", ""), "")).strip().startswith("-")) else "out"
        amount = dep or wd or amt
        ds = (r.get(cols.get("date", "")) or "").strip()
        ts = (r.get(cols.get("time", "")) or "00:00").strip()
        try:
            y, m, d = (int(x) for x in ds.replace("-", "/").split("/")[:3])
            if y < 1700:
                y, m, d = jalali_to_gregorian(y, m, d)
            hh, mm = (int(x) for x in ts.split(":")[:2])
            when = datetime(y, m, d, hh, mm)
        except (ValueError, TypeError):
            when = datetime.now()
        ref = (r.get(cols.get("reference", "")) or "").strip() or None
        rows.append({"external_id": ref or f"stmt-{when:%Y%m%d%H%M}-{amount}-{i}", "amount": amount, "direction": direction,
                     "occurred_at": when, "reference": ref, "description": (r.get(cols.get("description", "")) or "").strip()})
    return rows


class SmsIn(BaseModel):
    text: str
    sender: str | None = None
    payment_account_id: int | None = None


class Plugin(BasePlugin):
    name = "bank_sync"
    title = "اتصال به بانک"
    description = "دریافت خودکار تراکنش‌ها از API بانک، پیامک بانک یا فایل صورتحساب و تطبیق با دریافتی‌ها"
    version = "1.0.0"
    category = "integration"
    config_schema = {"type": "object", "properties": {
        "sync_interval_minutes": {"type": "integer", "title": "فاصله همگام‌سازی (دقیقه)", "default": 15}}}
    default_config = {"sync_interval_minutes": 15}

    def setup(self) -> None:
        r = APIRouter()

        @r.get("/providers")
        def providers(_=Depends(require("finance"))):
            return [{"key": k, "doc": (p.__doc__ or "").strip()} for k, p in PROVIDERS.items()]

        @r.post("/sync")
        def sync_all(db: Session = Depends(get_db), _=Depends(require("finance"))):
            accounts = db.scalars(select(PaymentAccount).where(PaymentAccount.provider.is_not(None), PaymentAccount.is_active.is_(True))).all()
            out = []
            for a in accounts:
                try:
                    out.append(sync_account(db, a))
                except Exception as exc:
                    db.rollback()
                    out.append({"account": a.id, "error": str(exc)})
            return out

        @r.post("/bank-sms")
        def bank_sms(body: SmsIn, db: Session = Depends(get_db), _=Depends(require("finance"))):
            return ingest_bank_sms(db, body.text, body.sender, body.payment_account_id)

        @r.post("/webhook/bank-sms")
        async def bank_sms_webhook(request: Request, db: Session = Depends(get_db)):
            raw = await request.body()
            if not verify_signature(raw, request.headers.get("X-Hesabdar-Signature")):
                raise HTTPException(401, "invalid signature")
            body = SmsIn.model_validate_json(raw)
            return ingest_bank_sms(db, body.text, body.sender, body.payment_account_id)

        @r.post("/import-statement")
        async def import_statement(payment_account_id: int = Form(...), file: UploadFile = File(...),
                                   db: Session = Depends(get_db), _=Depends(require("finance"))):
            if db.get(PaymentAccount, payment_account_id) is None:
                raise HTTPException(404, "account not found")
            raw = await file.read()
            content = raw.decode("utf-8-sig", errors="replace")
            new = matched = 0
            for row in parse_statement_csv(content):
                tx, created = matching.import_bank_transaction(db, payment_account_id=payment_account_id, source="statement", **row)
                new += int(created)
                matched += int(created and tx.status == "matched")
            db.commit()
            return {"imported": new, "auto_matched": matched}

        @r.get("/transactions")
        def transactions(status: str | None = None, limit: int = 200, db: Session = Depends(get_db), _=Depends(require("finance"))):
            q = select(BankTransaction).order_by(BankTransaction.occurred_at.desc()).limit(min(limit, 1000))
            if status:
                q = q.where(BankTransaction.status == status)
            return [{"id": t.id, "account_id": t.payment_account_id, "amount": t.amount, "direction": t.direction,
                     "occurred_at": t.occurred_at.isoformat(), "reference": t.reference, "description": t.description,
                     "status": t.status, "matched_type": t.matched_type, "matched_id": t.matched_id, "source": t.source}
                    for t in db.scalars(q)]

        @r.post("/transactions/{tx_id}/ignore")
        def ignore(tx_id: int, db: Session = Depends(get_db), _=Depends(require("finance"))):
            tx = db.get(BankTransaction, tx_id)
            if not tx:
                raise HTTPException(404)
            tx.status = "ignored"
            db.commit()
            return {"ok": True}

        self.router = r

    def jobs(self):  # noqa: ANN201
        def run() -> None:
            with SessionLocal() as db:
                for a in db.scalars(select(PaymentAccount).where(PaymentAccount.provider.is_not(None), PaymentAccount.is_active.is_(True))):
                    try:
                        sync_account(db, a)
                    except Exception:
                        db.rollback()
                        log.exception("bank sync failed for account %s", a.id)
        return [(int(self.ctx.config.get("sync_interval_minutes", 15)) * 60, run)]

    def ai_tools(self) -> list[AITool]:
        def unmatched(db, user, limit: int = 50):  # noqa: ANN001, ANN202
            q = select(BankTransaction).where(BankTransaction.status == "unmatched").order_by(BankTransaction.occurred_at.desc()).limit(limit)
            return [{"id": t.id, "amount": t.amount, "direction": t.direction, "at": t.occurred_at.isoformat(),
                     "reference": t.reference, "description": t.description} for t in db.scalars(q)]
        return [AITool("unmatched_bank_transactions", "Bank transactions not yet matched to any deposit, payment or receipt.",
                       {"type": "object", "properties": {"limit": {"type": "integer"}}}, unmatched, "finance")]
