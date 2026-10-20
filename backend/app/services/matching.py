"""Reconciliation engine: matches customer receipts <-> bank transactions <-> deposits/payments.

Flow
  1. A customer sends a receipt (SMS / WhatsApp / Instagram / manual upload) -> InboundReceipt
  2. Bank data arrives (bank API, statement import, or the bank's SMS on the salon phone) -> BankTransaction
  3. `reconcile_receipt` looks for a bank transaction with the same amount, time window,
     reference and card digits. The deposit is registered for that customer by itself only when
     the bank shows the same tracking number too; a match on amount + time alone (anyone can send
     a receipt for a common amount) waits as 'matched' until a person presses «ثبت».
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

STRONG_MATCH = 0.9  # amount + time + the same tracking number: safe to register without a person


def _weak_holder(db: Session, tx: BankTransaction) -> InboundReceipt | None:
    """The receipt that holds this bank transaction on amount + time alone (not registered yet), if any."""
    if tx.status != "matched" or tx.matched_type != "receipt":
        return None
    other = db.get(InboundReceipt, tx.matched_id) if tx.matched_id else None
    if other is not None and not other.deposit_id and (other.confidence or 0) < STRONG_MATCH:
        return other
    return None


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

    in_window = (BankTransaction.direction == "in", BankTransaction.amount == r.amount,
                 BankTransaction.occurred_at >= when - window, BankTransaction.occurred_at <= when + window)
    candidates = list(db.scalars(select(BankTransaction).where(BankTransaction.status == "unmatched", *in_window)))
    if p.get("reference"):
        # a transaction another receipt holds on amount + time alone goes to the receipt with its tracking number
        candidates += [tx for tx in db.scalars(select(BankTransaction).where(
            BankTransaction.reference == p["reference"], BankTransaction.matched_type == "receipt",
            BankTransaction.matched_id != r.id, *in_window)) if _weak_holder(db, tx)]
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
    if tx is not None and tx.status == "matched" and not (tx.matched_type == "receipt" and tx.matched_id == r.id):
        raise accounting.AccountingError("این واریز بانکی قبلاً برای مورد دیگری ثبت شده است")
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


def _auto_register(db: Session, r: InboundReceipt, tx: BankTransaction | None, user=None) -> None:
    """Register a receipt the engine matched; a problem (no bank account, a wrong date) leaves it for a person."""
    try:
        register_receipt(db, r, db.get(Customer, r.customer_id), tx=tx, user=user)
    except accounting.AccountingError as exc:
        r.note = f"خودکار ثبت نشد: {exc}"


def reconcile_receipt(db: Session, r: InboundReceipt, user=None) -> InboundReceipt:
    if r.deposit_id or r.status == "rejected":
        return r  # already registered / rejected: nothing to look for
    held = db.get(BankTransaction, r.bank_transaction_id) if r.bank_transaction_id else None
    if held is not None and held.matched_type == "receipt" and held.matched_id == r.id:
        # already matched to its bank transaction (e.g. before the customer told us their name): keep that match
        if r.customer_id and (r.confidence or 0) >= STRONG_MATCH:
            _auto_register(db, r, held, user)
        audit(db, "receipt.reconcile", "receipt", r.id, {"status": r.status, "confidence": r.confidence}, user=user, actor="matcher")
        return r
    tx, conf, note = find_bank_match(db, r)
    r.confidence = conf
    r.note = note
    if conf < 0:
        r.status = "mismatch"
        raise_alert(db, "receipt_mismatch", "عدم تطابق رسید با بانک", f"رسید ارسالی از {r.sender}: {note}",
                    level="danger", ref_type="receipt", ref_id=r.id)
        bus.emit("receipt.mismatch", {"receipt_id": r.id, "note": note}, db=db)
    elif tx is not None and conf >= 0.5:
        other = _weak_holder(db, tx)  # held by another receipt on amount + time only: this one has the tracking number
        if other is not None:
            other.status, other.bank_transaction_id = "pending", None
            other.note = "واریز بانکی آن به رسیدی با همان شماره پیگیری داده شد"
        r.status = "matched"
        r.bank_transaction_id = tx.id
        tx.status, tx.matched_type, tx.matched_id = "matched", "receipt", r.id
        if conf < STRONG_MATCH:
            # amount + time only: anyone could send such a receipt, so a person confirms it («ثبت»)
            r.note = f"{note}؛ شماره پیگیری با بانک نخواند - پس از بررسی «ثبت» را بزنید"
        elif r.customer_id:
            _auto_register(db, r, tx, user)
        if other is not None:
            reconcile_receipt(db, other, user=user)  # it may still find its own transaction
    else:
        require_bank = settings_store.get(db, "matching.require_bank_confirmation", True)
        r.status = "pending"
        if not require_bank and r.customer_id and r.amount:
            r.note = "ثبت بدون تأیید بانک (تنظیمات)"
            _auto_register(db, r, None, user)
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
    # matched only on amount and time: they hold a bank credit until a person confirms them; remind once
    for r in db.scalars(select(InboundReceipt).where(InboundReceipt.status == "matched", InboundReceipt.customer_id.is_not(None))):
        if local_now() - r.created_at > grace and not (r.parsed or {}).get("reminded"):
            r.parsed = {**(r.parsed or {}), "reminded": True}  # a new dict, so the change is saved
            raise_alert(db, "receipt_needs_review", "رسید منتظر تأیید",
                        f"رسید {toman(r.amount)} از {r.sender} با واریز بانک جور شده ولی هنوز کسی «ثبت» یا «رد» نزده است",
                        level="warning", ref_type="receipt", ref_id=r.id)
            count += 1
    return count


def on_bank_transaction(db: Session, tx: BankTransaction) -> None:
    """Called after a bank transaction is imported: try pending receipts, then manual entries; a line that is not a
    customer's money (or going out) is then linked to an entry made by hand or booked by a rule (bank_booking)."""
    if tx.status != "unmatched":
        return
    if tx.direction != "in":
        from .bank_booking import on_unmatched

        on_unmatched(db, tx)
        return
    _customer_money(db, tx)
    if tx.status == "unmatched":
        from .bank_booking import on_unmatched

        on_unmatched(db, tx)


def _customer_money(db: Session, tx: BankTransaction) -> None:
    pending = list(db.scalars(select(InboundReceipt).where(InboundReceipt.status == "pending", InboundReceipt.amount == tx.amount)
                              .order_by(InboundReceipt.id)))
    # the receipt with this transaction's tracking number first, then the oldest
    pending.sort(key=lambda r: not (tx.reference and (r.parsed or {}).get("reference") == tx.reference))
    for r in pending:
        if r.status != "pending":
            continue  # taken care of a moment ago (a receipt that lost its transaction looks again by itself)
        reconcile_receipt(db, r)
        if tx.status == "matched" or r.bank_transaction_id == tx.id:
            return
    # match manually recorded deposits / payments on the same account
    window = timedelta(hours=36)
    already = select(BankTransaction.matched_id).where(BankTransaction.matched_type == "deposit")
    dep = db.scalar(select(Deposit).where(
        Deposit.payment_account_id == tx.payment_account_id, Deposit.amount == tx.amount, Deposit.source != "import",
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


SAME_MONEY_WINDOW = timedelta(minutes=15)


def import_bank_transaction(db: Session, *, payment_account_id: int | None, external_id: str, amount: int,
                            occurred_at: datetime, direction: str = "in", description: str = "",
                            reference: str | None = None, counterparty_card: str | None = None,
                            counterparty_iban: str | None = None, counterparty_name: str | None = None,
                            source: str = "bank_api") -> tuple[BankTransaction, bool]:
    """Save a bank line once (the same id of the same account is the same line). Card numbers and IBANs are never
    kept: the other side is kept as a keyed hash + mask (+ the name), the description with every number masked. The
    same money seen from another source (an SMS and the bank API) is kept as a duplicate that nothing acts on."""
    from .bank_parties import counterparty, mask_text, own_ibans, own_masks

    existing = db.scalar(select(BankTransaction).where(BankTransaction.payment_account_id == payment_account_id,
                                                       BankTransaction.external_id == external_id))
    if existing:
        return existing, False
    party = counterparty(description, card=counterparty_card, iban=counterparty_iban, name=counterparty_name,
                         own=own_masks(db), own_iban=own_ibans(db))
    reference = (str(reference).strip()[:64] or None) if reference else None
    tx = BankTransaction(payment_account_id=payment_account_id, external_id=external_id[:128], amount=amount,
                         occurred_at=occurred_at, direction=direction if direction in ("in", "out") else "unknown",
                         description=mask_text(description or "")[:2000], reference=reference,
                         counterparty_card=party["mask"], counterparty_hash=party["hash"], counterparty_mask=party["mask"],
                         counterparty_name=party["name"], source=source)
    if payment_account_id and tx.direction in ("in", "out"):
        same = db.scalar(select(BankTransaction).where(
            BankTransaction.payment_account_id == payment_account_id, BankTransaction.source != source,
            BankTransaction.direction == tx.direction, BankTransaction.amount == amount, BankTransaction.duplicate_of_id.is_(None),
            BankTransaction.occurred_at.between(occurred_at - SAME_MONEY_WINDOW, occurred_at + SAME_MONEY_WINDOW))
            .order_by(BankTransaction.id).limit(1))
        if same is not None and (not reference or not same.reference or same.reference == reference):
            tx.duplicate_of_id, tx.status = same.id, "duplicate"
            if same.counterparty_hash is None and party["hash"]:  # the other source knew who it was: kept on the first
                same.counterparty_hash, same.counterparty_mask = party["hash"], party["mask"]
                same.counterparty_name = same.counterparty_name or party["name"]
    db.add(tx)
    db.flush()
    bus.emit("bank_transaction.imported", {"id": tx.id, "amount": amount, "direction": tx.direction}, db=db)
    on_bank_transaction(db, tx)
    return tx, True


def unreconciled_summary(db: Session) -> dict:
    return {
        "bank_unmatched": len(db.scalars(select(BankTransaction.id).where(BankTransaction.status == "unmatched")).all()),
        "receipts_pending": len(db.scalars(select(InboundReceipt.id).where(InboundReceipt.status == "pending")).all()),
        "receipts_mismatch": len(db.scalars(select(InboundReceipt.id).where(InboundReceipt.status == "mismatch")).all()),
    }
