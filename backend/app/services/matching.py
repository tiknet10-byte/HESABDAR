"""Reconciliation engine: matches customer receipts <-> bank transactions <-> deposits/payments.

Flow
  1. A customer sends a receipt (SMS / WhatsApp / Instagram / manual upload) -> InboundReceipt
  2. Bank data arrives (bank API, statement import, or the bank's SMS on the salon phone) -> BankTransaction
  3. `reconcile_receipt` looks for a bank transaction with the same amount, time window,
     reference and card digits. On a match the deposit is registered for that customer.
     If the bank shows a different amount for the same reference, or nothing shows up
     within the grace period, an alert is raised (possible fake / edited receipt).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.events import bus
from ..models import BankTransaction, Customer, Deposit, InboundReceipt, Payment, PaymentAccount, local_now
from . import accounting, learning, settings_store
from .audit import audit, raise_alert
from .textutil import toman


def _receipt_time(r: InboundReceipt) -> datetime:
    dt = (r.parsed or {}).get("datetime")
    try:
        return datetime.fromisoformat(dt) if dt else r.created_at
    except ValueError:
        return r.created_at


def find_bank_match(db: Session, r: InboundReceipt) -> tuple[BankTransaction | None, float, str]:
    """Return (transaction, confidence, note)."""
    if not r.amount:
        return None, 0.0, "مبلغ رسید قابل تشخیص نیست"
    p = r.parsed or {}
    when = _receipt_time(r)
    window = timedelta(hours=int(settings_store.get(db, "matching.window_hours", 48)))

    # same reference but different amount -> strong evidence of a tampered receipt
    if p.get("reference"):
        same_ref = db.scalar(select(BankTransaction).where(BankTransaction.reference == p["reference"]))
        if same_ref and same_ref.amount != r.amount:
            return same_ref, -1.0, f"شماره پیگیری یکسان اما مبلغ بانک {toman(same_ref.amount)} است"

    candidates = db.scalars(select(BankTransaction).where(
        BankTransaction.direction == "in", BankTransaction.amount == r.amount, BankTransaction.status == "unmatched",
        BankTransaction.occurred_at >= when - window, BankTransaction.occurred_at <= when + window,
    )).all()
    best, best_score = None, 0.0
    for tx in candidates:
        score = 0.5  # amount + time window
        if p.get("reference") and tx.reference and p["reference"] == tx.reference:
            score += 0.4
        if p.get("card_last4") and tx.counterparty_card and tx.counterparty_card.endswith(p["card_last4"]):
            score += 0.2
        delta_h = abs((tx.occurred_at - when).total_seconds()) / 3600
        score += max(0.0, 0.1 - delta_h / 480)
        if score > best_score:
            best, best_score = tx, score
    if best is None:
        return None, 0.0, "تراکنش بانکی متناظر هنوز پیدا نشد"
    return best, min(best_score, 1.0), "تطابق مبلغ و زمان" + (" و شماره پیگیری" if best_score >= 0.9 else "")


def register_receipt(db: Session, r: InboundReceipt, customer: Customer, payment_account: PaymentAccount | None = None,
                     tx: BankTransaction | None = None, service_id: int | None = None, user=None) -> Deposit:
    if r.deposit_id:
        return db.get(Deposit, r.deposit_id)
    pa = payment_account or (db.get(PaymentAccount, tx.payment_account_id) if tx and tx.payment_account_id else None)
    if pa is None:
        pa = db.scalar(select(PaymentAccount).where(PaymentAccount.kind.in_(["card", "bank"]), PaymentAccount.is_active.is_(True)))
    if pa is None:
        raise accounting.AccountingError("هیچ حساب بانکی/کارتی برای ثبت دریافتی تعریف نشده است")
    text = " ".join(m for m in [(r.parsed or {}).get("note", ""), r.note] if m)
    guess = learning.guess_service_for_deposit(db, r.amount, customer.id, text)
    dep = accounting.record_deposit(
        db, customer=customer, amount=r.amount, payment_account=pa, received_at=tx.occurred_at if tx else _receipt_time(r),
        reference=(r.parsed or {}).get("reference") or (tx.reference if tx else None),
        service_id=service_id or (guess[0]["service_id"] if guess and guess[0]["score"] >= 0.6 else None),
        source=f"receipt_{r.channel}", service_guess={"candidates": guess}, user=user,
    )
    r.deposit_id = dep.id
    r.customer_id = customer.id
    r.status = "registered"
    if tx:
        tx.status, tx.matched_type, tx.matched_id = "matched", "deposit", dep.id
        r.bank_transaction_id = tx.id
    card = (r.parsed or {}).get("card_mask")
    if card and card not in (customer.known_cards or []):
        customer.known_cards = [*(customer.known_cards or []), card]
    bus.emit("receipt.matched", {"receipt_id": r.id, "deposit_id": dep.id, "customer_id": customer.id}, db=db)
    return dep


def reconcile_receipt(db: Session, r: InboundReceipt, user=None) -> InboundReceipt:
    tx, conf, note = find_bank_match(db, r)
    r.confidence = conf
    r.note = note
    if conf < 0:
        r.status = "mismatch"
        raise_alert(db, "receipt_mismatch", "عدم تطابق رسید با بانک", f"رسید ارسالی از {r.sender}: {note}",
                    level="danger", ref_type="receipt", ref_id=r.id)
        bus.emit("receipt.mismatch", {"receipt_id": r.id, "note": note}, db=db)
    elif tx is not None and conf >= 0.5:
        r.status = "matched"
        r.bank_transaction_id = tx.id
        tx.status, tx.matched_type, tx.matched_id = "matched", "receipt", r.id
        if r.customer_id:
            register_receipt(db, r, db.get(Customer, r.customer_id), tx=tx, user=user)
    else:
        require_bank = settings_store.get(db, "matching.require_bank_confirmation", True)
        if not require_bank and r.customer_id and r.amount:
            register_receipt(db, r, db.get(Customer, r.customer_id), user=user)
            r.note = "ثبت بدون تأیید بانک (تنظیمات)"
        else:
            r.status = "pending"
    audit(db, "receipt.reconcile", "receipt", r.id, {"status": r.status, "confidence": conf}, user=user, actor="matcher")
    return r


def expire_pending_receipts(db: Session) -> int:
    """Receipts that never appeared in the bank after the grace period -> mismatch alert."""
    grace = timedelta(hours=int(settings_store.get(db, "matching.grace_hours", 24)))
    count = 0
    for r in db.scalars(select(InboundReceipt).where(InboundReceipt.status == "pending")):
        if local_now() - r.created_at > grace:
            r.status = "mismatch"
            r.note = "پس از مهلت تعیین‌شده هیچ واریزی در بانک دیده نشد"
            raise_alert(db, "receipt_not_found", "رسید بدون واریز بانکی",
                        f"{r.sender} رسید {toman(r.amount)} فرستاده ولی واریز آن در بانک پیدا نشد",
                        level="danger", ref_type="receipt", ref_id=r.id)
            count += 1
    return count


def on_bank_transaction(db: Session, tx: BankTransaction) -> None:
    """Called after a bank transaction is imported: try pending receipts, then manual entries."""
    if tx.direction != "in" or tx.status != "unmatched":
        return
    for r in db.scalars(select(InboundReceipt).where(InboundReceipt.status == "pending", InboundReceipt.amount == tx.amount)):
        reconcile_receipt(db, r)
        if tx.status == "matched" or r.bank_transaction_id == tx.id:
            return
    # match manually recorded deposits / payments on the same account
    window = timedelta(hours=36)
    already = select(BankTransaction.matched_id).where(BankTransaction.matched_type == "deposit")
    dep = db.scalar(select(Deposit).where(
        Deposit.payment_account_id == tx.payment_account_id, Deposit.amount == tx.amount,
        Deposit.received_at.between(tx.occurred_at - window, tx.occurred_at + window), Deposit.id.not_in(already)))
    if dep:
        tx.status, tx.matched_type, tx.matched_id = "matched", "deposit", dep.id
        return
    already_p = select(BankTransaction.matched_id).where(BankTransaction.matched_type == "payment")
    pay = db.scalar(select(Payment).where(
        Payment.payment_account_id == tx.payment_account_id, Payment.amount == tx.amount,
        Payment.paid_at.between(tx.occurred_at - window, tx.occurred_at + window), Payment.id.not_in(already_p)))
    if pay:
        tx.status, tx.matched_type, tx.matched_id = "matched", "payment", pay.id


def import_bank_transaction(db: Session, *, payment_account_id: int | None, external_id: str, amount: int,
                            occurred_at: datetime, direction: str = "in", description: str = "",
                            reference: str | None = None, counterparty_card: str | None = None,
                            source: str = "bank_api") -> tuple[BankTransaction, bool]:
    existing = db.scalar(select(BankTransaction).where(BankTransaction.payment_account_id == payment_account_id,
                                                       BankTransaction.external_id == external_id))
    if existing:
        return existing, False
    tx = BankTransaction(payment_account_id=payment_account_id, external_id=external_id, amount=amount,
                         occurred_at=occurred_at, direction=direction, description=description, reference=reference,
                         counterparty_card=counterparty_card, source=source)
    db.add(tx)
    db.flush()
    bus.emit("bank_transaction.imported", {"id": tx.id, "amount": amount, "direction": direction}, db=db)
    on_bank_transaction(db, tx)
    return tx, True


def unreconciled_summary(db: Session) -> dict:
    return {
        "bank_unmatched": len(db.scalars(select(BankTransaction.id).where(BankTransaction.status == "unmatched", BankTransaction.direction == "in")).all()),
        "receipts_pending": len(db.scalars(select(InboundReceipt.id).where(InboundReceipt.status == "pending")).all()),
        "receipts_mismatch": len(db.scalars(select(InboundReceipt.id).where(InboundReceipt.status == "mismatch")).all()),
    }
