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
from ....services import bank_booking, matching
from ....services.jalali import jalali_to_gregorian
from ....services.bank_parties import mask_text, statement_ext
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

    OUT = ("out", "debit", "withdraw", "withdrawal", "dr", "برداشت", "بدهکار")
    IN = ("in", "credit", "deposit", "cr", "واریز", "بستانکار")

    def fetch(self, account: PaymentAccount, since: datetime) -> list[dict]:
        from ....core.security import decrypt_secret

        cfg = account.provider_config or {}
        url = cfg["url"].format(account=cfg.get("account", ""), since=since.isoformat())
        resp = httpx.get(url, headers={"Authorization": f"Bearer {decrypt_secret(cfg.get('token', ''))}"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for part in (cfg.get("items_path") or "").split("."):
            if part:
                data = data[part]
        f = {"id": "id", "amount": "amount", "date": "date", "direction": "direction", "reference": "reference",
             "description": "description", "card": "card", "iban": "iban", "name": "name", **cfg.get("fields", {})}
        out = []
        for it in data:
            try:  # one row the bank wrote oddly is left out (and logged), never the whole sync
                raw = str(it[f["amount"]]).strip()
                amount = parse_amount(raw.lstrip("+-")) or 0
                if not amount:
                    raise ValueError("no amount")
                d = str(it.get(f["direction"], "")).strip().lower()
                direction = "out" if raw.startswith("-") or d in self.OUT else "in" if d in self.IN or not d else "unknown"
                out.append({"external_id": str(it[f["id"]]), "amount": abs(int(amount)), "direction": direction,
                            "occurred_at": datetime.fromisoformat(str(it[f["date"]]).replace("Z", "")),
                            "reference": it.get(f["reference"]), "description": str(it.get(f["description"]) or ""),
                            "counterparty_card": str(it.get(f["card"]) or "") or None,
                            "counterparty_iban": str(it.get(f["iban"]) or "") or None,
                            "counterparty_name": str(it.get(f["name"]) or "") or None})
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("bank row skipped for account %s: %s", account.id, exc)
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


def _salon_last4(db: Session) -> set[str]:
    out = set()
    for (m,) in db.execute(select(PaymentAccount.card_mask).where(PaymentAccount.card_mask.is_not(None))):
        d = "".join(ch for ch in (m or "") if ch.isdigit())
        if len(d) >= 4:
            out.add(d[-4:])
    return out


def ingest_bank_sms(db: Session, text: str, sender: str | None = None, account_id: int | None = None) -> dict:
    from ....services.bank_parties import mask_text, sms_direction

    p = parse_receipt(text)
    if not p["amount"]:
        return {"ok": False, "reason": "amount not found"}
    # the sign with the amount, the card-to-card cards, then the words; unclear: "unknown" (the inbox asks)
    p["direction"] = sms_direction(text, _salon_last4(db))
    account = db.get(PaymentAccount, account_id) if account_id else find_account_for_sms(db, p, sender)
    if not p["reference"] and not p["datetime"]:
        # no tracking number and no date in the SMS: the same text again within a day is the same SMS sent again
        # (a forwarder retry or a replayed request), not new money
        same = db.scalar(select(BankTransaction).where(
            BankTransaction.source == "bank_sms", BankTransaction.description == mask_text(text[:500]),
            BankTransaction.payment_account_id == account.id if account else BankTransaction.payment_account_id.is_(None),
            BankTransaction.created_at >= datetime.now() - timedelta(hours=24)).limit(1))
        if same is not None:
            return {"ok": True, "transaction_id": same.id, "created": False, "status": same.status, "parsed": p}
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
        "card": ["card", "شماره کارت", "کارت مقصد", "کارت مبدا", "کارت طرف حساب"],
        "iban": ["iban", "شبا", "شماره شبا", "شبای مقصد"],
        "name": ["name", "نام", "نام طرف حساب", "صاحب حساب", "نام صاحب حساب", "طرف حساب", "گیرنده", "واریز کننده"],
    }
    cols = {}
    for key, names in aliases.items():
        for h in reader.fieldnames or []:
            if h and h.strip() in names:
                cols[key] = h
                break
    rows = []
    seen: dict[str, int] = {}
    for r in reader:
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
        desc = (r.get(cols.get("description", "")) or "").strip()
        party = {k: (r.get(cols[k]) or "").strip() or None for k in ("card", "iban", "name") if k in cols}
        # the same row in another download of the statement (longer, shorter, sorted otherwise) is the same line: its id
        # is made of what it says (and how many identical rows came before it), not of where it is in the file
        key = f"{when:%Y%m%d%H%M}|{amount}|{direction}|{ref or ''}|{' '.join(mask_text(desc).split())}"
        seen[key] = seen.get(key, 0) + 1
        ext = ref or statement_ext(when, amount, direction, ref, desc, seen[key])
        rows.append({"external_id": ext, "amount": amount, "direction": direction, "occurred_at": when, "reference": ref,
                     "description": desc, "counterparty_card": party.get("card"), "counterparty_iban": party.get("iban"),
                     "counterparty_name": party.get("name")})
    return rows


def _sender_key(sender: str) -> str:
    """A sender as the phone shows it, compared loosely: «+98 9123…», «0912…» and «BMI» / «bmi» are the same."""
    s = to_en_digits(str(sender or "")).strip().lower().replace(" ", "").replace("-", "")
    digits = s.lstrip("+")
    if digits.isdigit():
        for p in ("0098", "98", "0"):
            if digits.startswith(p) and len(digits) - len(p) >= 7:
                return digits[len(p):]
        return digits
    return s


_no_senders_alert: list = [None]


def _warn_no_senders() -> None:
    from ....models import local_now
    from ....services.audit import raise_alert

    now = local_now()
    if _no_senders_alert[0] is not None and now - _no_senders_alert[0] < timedelta(days=1):
        return
    _no_senders_alert[0] = now
    with SessionLocal() as db:
        raise_alert(db, "bank_sms_no_senders", "پیامک‌های بانک ثبت نمی‌شوند",
                    "برای امنیت، فقط پیامکی که از شماره/نام خود بانک آمده واریز حساب می‌شود (نه پیامکی که هر کسی بفرستد). در «تنظیمات ← "
                    "افزونه‌ها ← همگام‌سازی بانک» فرستندهٔ پیامک‌های بانک را وارد کنید (همان شماره یا نامی که بالای پیامک بانک دیده می‌شود).",
                    level="danger")
        db.commit()


class BookIn(BaseModel):
    target: str  # staff | expense | owner | ignore
    staff_id: int | None = None
    kind: str | None = None  # advance | settlement (default: worked out)
    jy: int | None = None
    jm: int | None = None
    category: str | None = None
    line_id: int | None = None
    from_treasury: bool = False
    description: str = ""
    remember: bool = False  # next time a line to / from this card is this too
    auto: bool = False  # ... and booked by itself (only an exact card / IBAN)
    force: bool = False


class LinkIn(BaseModel):
    type: str
    id: int


class DirectionIn(BaseModel):
    direction: str  # in | out


class UndoIn(BaseModel):
    then: str = "inbox"  # inbox | ignore


class RuleIn(BaseModel):
    auto: bool | None = None
    auto_cap: int | None = None
    active: bool | None = None


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
        "sync_interval_minutes": {"type": "integer", "title": "فاصله همگام‌سازی (دقیقه)", "default": 15},
        "bank_senders": {"type": "string", "title": "فرستندهٔ پیامک‌های بانک (شماره یا نام، با ویرگول جدا)",
                         "description": "فقط پیامک این فرستنده‌ها واریز بانک حساب می‌شود؛ مثلاً: 9830003, BMI, +98200004567"}}}
    default_config = {"sync_interval_minutes": 15, "bank_senders": ""}

    def setup(self) -> None:
        r = APIRouter()
        plugin = self

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
        def bank_sms(body: SmsIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
            from ....services.audit import audit

            out = ingest_bank_sms(db, body.text, body.sender, body.payment_account_id)
            if out.get("transaction_id") and out.get("created"):
                audit(db, "bank_tx.sms_typed", "bank_transaction", out["transaction_id"], {"status": out.get("status")}, user=user)
                db.commit()
            return out

        @r.post("/webhook/bank-sms")
        async def bank_sms_webhook(request: Request, db: Session = Depends(get_db)):
            raw = await request.body()
            if not verify_signature(raw, request.headers.get("X-Hesabdar-Signature")):
                raise HTTPException(401, "invalid signature")
            body = SmsIn.model_validate_json(raw)
            allowed = {_sender_key(x) for x in str(plugin.ctx.config.get("bank_senders") or "").replace("،", ",").split(",") if x.strip()}
            if not allowed:  # anyone's SMS (a customer's fake «واریز» text) must never become bank money
                _warn_no_senders()
                raise HTTPException(403, "فرستندهٔ پیامک‌های بانک در تنظیمات افزونهٔ بانک وارد نشده است")
            if _sender_key(body.sender or "") not in allowed:
                return {"ok": False, "ignored": "not a bank sender"}  # e.g. a customer's message forwarded too
            return ingest_bank_sms(db, body.text, body.sender, body.payment_account_id)

        @r.post("/import-statement")
        async def import_statement(payment_account_id: int = Form(...), file: UploadFile = File(...),
                                   db: Session = Depends(get_db), user=Depends(require("finance"))):
            from ....services.audit import audit

            if db.get(PaymentAccount, payment_account_id) is None:
                raise HTTPException(404, "account not found")
            raw = await file.read()
            content = raw.decode("utf-8-sig", errors="replace")
            new = matched = 0
            for row in parse_statement_csv(content):
                tx, created = matching.import_bank_transaction(db, payment_account_id=payment_account_id, source="statement", **row)
                new += int(created)
                matched += int(created and tx.status == "matched")
            audit(db, "bank.statement_import", "payment_account", payment_account_id,
                  {"file": (file.filename or "")[:120], "imported": new, "matched": matched}, user=user)
            db.commit()
            return {"imported": new, "auto_matched": matched}

        @r.get("/transactions")
        def transactions(status: str | None = None, direction: str | None = None, account_id: int | None = None, limit: int = 200,
                         db: Session = Depends(get_db), _=Depends(require("finance"))):
            q = select(BankTransaction).order_by(BankTransaction.occurred_at.desc()).limit(max(1, min(limit, 1000)))
            if status:
                q = q.where(BankTransaction.status == status)
            if direction:
                q = q.where(BankTransaction.direction == direction)
            if account_id:
                q = q.where(BankTransaction.payment_account_id == account_id)
            accounts = {pa.id: pa.name for pa in db.scalars(select(PaymentAccount))}
            return [bank_booking.tx_row(db, t, accounts) for t in db.scalars(q)]

        # ---------------------------------------------- the inbox: lines that are not a customer's money (1.23)
        @r.get("/inbox")
        def inbox(db: Session = Depends(get_db), user=Depends(require("finance"))):
            """Lines waiting for a decision (newest first), each with what it most likely is."""
            from ....core.security import has_permission

            accounts = {pa.id: pa.name for pa in db.scalars(select(PaymentAccount))}
            capital_ok = has_permission(user.role, "capital")
            rows = [bank_booking.tx_row(db, t, accounts, with_suggestions=True, capital_ok=capital_ok) for t in db.scalars(
                select(BankTransaction).where(BankTransaction.status == "unmatched")
                .order_by(BankTransaction.occurred_at.desc()).limit(300))]
            return {"rows": rows, "out": sum(1 for x in rows if x["direction"] == "out"),
                    "unknown": sum(1 for x in rows if x["direction"] == "unknown")}

        @r.get("/booked")
        def booked(days: int = 30, db: Session = Depends(get_db), user=Depends(require("finance"))):
            """What was booked or linked from bank lines lately (by itself first), to look over and take back."""
            from ....models import local_now

            since = local_now() - timedelta(days=max(1, min(days, 365)))
            accounts = {pa.id: pa.name for pa in db.scalars(select(PaymentAccount))}
            q = (select(BankTransaction).where(BankTransaction.booked_at >= since, BankTransaction.status.in_(("matched", "ignored")))
                 .order_by(BankTransaction.booked_at.desc()).limit(500))
            from ....core.security import has_permission

            may_void = has_permission(user.role, "void")
            return [bank_booking.tx_row(db, t, accounts, may_void=may_void) for t in db.scalars(q)]


        def _tx(db: Session, tx_id: int) -> BankTransaction:
            tx = db.get(BankTransaction, tx_id)
            if tx is None:
                raise HTTPException(404, "تراکنش پیدا نشد")
            return tx

        @r.post("/transactions/{tx_id}/book")
        def book(tx_id: int, body: BookIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
            from ....core.security import has_permission
            from ....services.staffpay import Duplicate

            from ....services.accounting import DuplicateExpense
            from ....services.capital import DuplicateMove

            tx = _tx(db, tx_id)
            if body.target == "owner" and not has_permission(user.role, "capital"):
                raise HTTPException(403, "پول خود مدیر را فقط مدیر ثبت می‌کند")
            owner_admin = has_permission(user.role, "settings")
            if body.remember and not owner_admin and body.auto:
                body.auto = False  # booking by itself: set by the owner / admin
            try:
                out = bank_booking.book(db, tx, target=body.target, staff_id=body.staff_id, kind=body.kind, jy=body.jy, jm=body.jm,
                                        category=body.category, line_id=body.line_id, from_treasury=body.from_treasury,
                                        description=body.description, force=body.force, user=user)
            except (Duplicate, DuplicateMove, DuplicateExpense) as exc:
                db.rollback()
                raise HTTPException(409, str(exc)) from exc
            except (bank_booking.BankBookError, ValueError) as exc:
                db.rollback()
                raise HTTPException(400, str(exc)) from exc
            rule, note = None, None
            if body.remember and body.target in ("staff", "expense", "owner", "ignore"):
                try:  # remembering never undoes the booking: if it can't be remembered, the person is told why
                    with db.begin_nested():
                        rule = bank_booking.remember(db, tx, target=body.target, staff_id=body.staff_id, category=out.get("category"),
                                                     line_id=body.line_id, from_treasury=body.from_treasury, auto=body.auto,
                                                     may_change=owner_admin, user=user)
                except bank_booking.BankBookError as exc:
                    note = str(exc)
            db.commit()
            return {"ok": True, **out, "remembered": bool(rule), "rule_auto": bool(rule and rule.auto), "remember_note": note,
                    "transaction": bank_booking.tx_row(db, tx, may_void=has_permission(user.role, "void"))}


        @r.post("/transactions/{tx_id}/link")
        def link(tx_id: int, body: LinkIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
            tx = _tx(db, tx_id)
            try:
                bank_booking.link(db, tx, body.type, body.id, user=user)
            except bank_booking.BankBookError as exc:
                db.rollback()
                raise HTTPException(400, str(exc)) from exc
            db.commit()
            return {"ok": True}


        @r.post("/transactions/{tx_id}/direction")
        def set_direction(tx_id: int, body: DirectionIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
            """An SMS whose direction was not clear: the person says it; then it is matched like any other line."""
            from ....services.audit import audit

            tx = _tx(db, tx_id)
            if body.direction not in ("in", "out"):
                raise HTTPException(400, "واریز یا برداشت؟")
            if tx.status != "unmatched" or tx.direction != "unknown":
                raise HTTPException(400, "فقط جهت تراکنشی که معلوم نبود را می‌شود گفت")
            tx.direction = body.direction
            tx.manual_only = True  # a person said it: matched with a customer's money, else a person books it
            audit(db, "bank_tx.direction", "bank_transaction", tx.id, {"direction": body.direction}, user=user)
            matching.mark_same_money(db, tx)  # the same money from another source: one line stays
            if tx.status == "unmatched":
                matching.on_bank_transaction(db, tx)
            db.commit()
            return {"ok": True, "transaction": bank_booking.tx_row(db, tx, with_suggestions=True)}


        @r.post("/transactions/{tx_id}/undo")
        def undo(tx_id: int, body: UndoIn, db: Session = Depends(get_db), user=Depends(require("finance"))):
            """Take back what was booked from a line: one booked by itself (or a link) by anyone who moves money; one a
            person booked from the inbox voids a payout / expense: owner / admin."""
            from ....core.security import has_permission

            tx = _tx(db, tx_id)
            voids = tx.status == "matched" and tx.auto_booked is not None  # booked from the line: its record is voided
            if voids and not has_permission(user.role, "void"):
                raise HTTPException(403, "باطل کردن چیزی که از روی بانک ثبت شده با مدیر است (اتصال به ثبت دستی را خودتان می‌توانید بردارید)")
            if voids and tx.matched_type == "capital" and not has_permission(user.role, "capital"):
                raise HTTPException(403, "پول خود مدیر را فقط مدیر باطل می‌کند")
            try:
                bank_booking.undo(db, tx, user=user, then=body.then)
            except (bank_booking.BankBookError, ValueError) as exc:
                db.rollback()
                raise HTTPException(400, str(exc)) from exc
            db.commit()
            return {"ok": True, "transaction": bank_booking.tx_row(db, tx, may_void=has_permission(user.role, "void"))}

        @r.post("/transactions/{tx_id}/not-duplicate")
        def not_duplicate(tx_id: int, db: Session = Depends(get_db), user=Depends(require("finance"))):
            """Taken for the same money seen from another source, but it is another movement: it waits like any other."""
            from ....services.audit import audit

            tx = _tx(db, tx_id)
            if tx.status != "duplicate":
                raise HTTPException(400, "این تراکنش تکراری علامت نخورده است")
            tx.status, tx.duplicate_of_id = "unmatched", None
            audit(db, "bank_tx.not_duplicate", "bank_transaction", tx.id, {}, user=user)
            matching.on_bank_transaction(db, tx)
            db.commit()
            return {"ok": True}

        # ---------------------------------------------- rules (who a card is)
        @r.get("/rules")
        def rules(db: Session = Depends(get_db), _=Depends(require("finance"))):
            from ....models import BankRule, Staff

            names = {p.id: p.full_name for p in db.scalars(select(Staff))}
            return [{"id": x.id, "kind": x.match_kind, "text": x.match_text, "direction": x.direction, "target": x.target_type,
                     "staff_id": x.staff_id, "staff": names.get(x.staff_id), "category": x.category, "line_id": x.line_id,
                     "from_treasury": bool(x.from_treasury), "auto": x.auto, "auto_cap": x.auto_cap, "hits": x.hits,
                     "undos": x.undos, "source": x.source, "active": x.is_active,
                     "last_used_at": x.last_used_at.isoformat(timespec="minutes") if x.last_used_at else None}
                    for x in db.scalars(select(BankRule).where(BankRule.is_active.is_(True)).order_by(BankRule.id.desc()))]


        @r.put("/rules/{rule_id}")
        def update_rule(rule_id: int, body: RuleIn, db: Session = Depends(get_db), user=Depends(require("settings"))):
            from ....models import BankRule
            from ....services.audit import audit

            rule = db.get(BankRule, rule_id)
            if rule is None:
                raise HTTPException(404, "قانون پیدا نشد")
            if body.auto is not None:
                rule.auto = bool(body.auto) and rule.match_kind != "name"
            if body.auto_cap is not None:
                rule.auto_cap = body.auto_cap if body.auto_cap > 0 else None
            if body.active is not None:
                rule.is_active = bool(body.active)
            audit(db, "bank_rule.update", "bank_rule", rule.id, body.model_dump(exclude_none=True), user=user)
            db.commit()
            return {"ok": True}

        @r.post("/transactions/{tx_id}/ignore")
        def ignore(tx_id: int, db: Session = Depends(get_db), user=Depends(require("finance"))):
            from ....services.audit import audit

            tx = db.get(BankTransaction, tx_id)
            if not tx:
                raise HTTPException(404)
            if tx.status != "unmatched":  # one that already paid a receipt / deposit stays as it is
                raise HTTPException(400, "فقط تراکنشی که به چیزی وصل نیست را می‌شود نادیده گرفت")
            tx.status = "ignored"
            audit(db, "bank_tx.ignore", "bank_transaction", tx.id, {"amount": tx.amount}, user=user)
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

        def again() -> None:  # waiting lines looked at again: an entry made by hand since, a card set since
            with SessionLocal() as db:
                try:
                    if bank_booking.rematch(db):
                        db.commit()
                    else:
                        db.rollback()
                except Exception:
                    db.rollback()
                    log.exception("bank rematch failed")
        return [(int(self.ctx.config.get("sync_interval_minutes", 15)) * 60, run), (3600, again)]

    def ai_tools(self) -> list[AITool]:
        def unmatched(db, user, limit: int = 50):  # noqa: ANN001, ANN202
            q = (select(BankTransaction).where(BankTransaction.status == "unmatched").order_by(BankTransaction.occurred_at.desc())
                 .limit(max(1, min(int(limit or 50), 200))))
            return [{"id": t.id, "amount": t.amount, "direction": t.direction, "at": t.occurred_at.isoformat(),
                     "reference": t.reference, "description": t.description} for t in db.scalars(q)]
        return [AITool("unmatched_bank_transactions", "Bank transactions not yet matched to any deposit, payment or receipt.",
                       {"type": "object", "properties": {"limit": {"type": "integer"}}}, unmatched, "reports")]  # it only reads
