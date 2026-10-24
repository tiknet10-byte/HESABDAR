"""Bank lines that are not a customer's money: what they are, booked with one tap or by themselves (1.23).

A line going out of a salon account (or coming in, not a customer's payment) is, in this order:
  1. an entry already made by hand (a payout, an expense, the owner's withdrawal: same account and amount within 3
     days, the same tracking number when both have one): linked to it, nothing is booked again - in both orders (the
     entry made after the bank line looks for it too);
  2. money to a card / IBAN a rule knows (a staff member's card entered on the staff page): booked by itself only
     when the full card / IBAN is the same (never a masked card or a name), exactly one rule says what it is, the rule
     is automatic, the line is recent (45 days) and not the same money seen from another source - a staff payout
     (advance or settlement and its month from the date, the words and what was owed), an expense or the owner's own;
  3. otherwise it waits in the inbox («صندوق بانک») with suggestions; booking it there can remember the card for next
     time. Anything booked from a line can be taken back (its record voided on its own date, the rule stops booking by
     itself until it is confirmed again).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..models import BankRule, BankTransaction, CapitalMove, Expense, PaymentAccount, Staff, StaffPayout, local_now

LINK_WINDOW = timedelta(days=3)
AUTO_DAYS = 45
TARGETS = ("staff", "expense", "owner", "ignore")
LABELS = {"staff_payout": "پرداخت به پرسنل", "expense": "هزینه", "capital": "سرمایهٔ مدیر", "deposit": "بیعانه",
          "payment": "دریافت فاکتور", "receipt": "رسید مشتری"}


class BankBookError(ValueError):
    pass


def _claim(db: Session, tx: BankTransaction, old: str = "unmatched", new: str = "matched") -> bool:
    db.flush()
    done = db.execute(update(BankTransaction).where(BankTransaction.id == tx.id, BankTransaction.status == old).values(status=new)
                      .execution_options(synchronize_session=False)).rowcount
    if done:
        tx.status = new
    return bool(done)


def _refs_ok(a: str | None, b: str | None) -> bool:
    return not a or not b or a.strip() == b.strip()


def _linked(db: Session, kind: str) -> set[int]:
    return {i for (i,) in db.execute(select(BankTransaction.matched_id).where(BankTransaction.matched_type == kind,
                                                                              BankTransaction.status == "matched"))}


# ------------------------------------------------------------------ 1. an entry already made for it
def link_candidates(db: Session, tx: BankTransaction, strict: bool = True) -> list[dict]:
    """Entries made by hand that this line may be: same account and amount within 3 days, not linked to another line;
    strict (to link by itself): the same tracking number when both have one."""
    if not tx.payment_account_id or tx.direction not in ("in", "out"):
        return []
    lo, hi = tx.occurred_at - LINK_WINDOW, tx.occurred_at + LINK_WINDOW
    out: list[dict] = []
    if tx.direction == "out":
        taken = _linked(db, "staff_payout")
        for p in db.scalars(select(StaffPayout).where(
                StaffPayout.status == "booked", StaffPayout.payment_account_id == tx.payment_account_id,
                StaffPayout.amount == tx.amount, StaffPayout.paid_at.between(lo, hi), StaffPayout.bank_transaction_id.is_(None))):
            if p.id not in taken and (not strict or _refs_ok(p.reference, tx.reference)):
                who = db.get(Staff, p.staff_id)
                out.append({"type": "staff_payout", "id": p.id, "at": p.paid_at, "label": f"پرداخت به {who.full_name if who else '—'}"})
        taken = _linked(db, "expense")
        for e in db.scalars(select(Expense).where(Expense.voided_at.is_(None), Expense.payment_account_id == tx.payment_account_id,
                                                  Expense.amount == tx.amount, Expense.spent_at.between(lo, hi))):
            if e.id not in taken:
                out.append({"type": "expense", "id": e.id, "at": e.spent_at, "label": f"هزینهٔ {e.category}"})
    kind = "withdraw" if tx.direction == "out" else "add"
    taken = _linked(db, "capital")
    for m in db.scalars(select(CapitalMove).where(CapitalMove.status == "active", CapitalMove.kind == kind,
                                                  CapitalMove.payment_account_id == tx.payment_account_id,
                                                  CapitalMove.amount == tx.amount, CapitalMove.at.between(lo, hi))):
        if m.id not in taken:
            out.append({"type": "capital", "id": m.id, "at": m.at,
                        "label": "برداشت مدیر از سرمایه" if kind == "withdraw" else "آوردن سرمایه"})
    out.sort(key=lambda c: abs((c["at"] - tx.occurred_at).total_seconds()))
    return out


def _consistent(db: Session, tx: BankTransaction, kind: str, rec) -> bool:  # noqa: ANN001
    """May this line be linked to this record without a person? Only when nothing says the money was someone else's:
    the card / IBAN of the line belongs (by a rule) to this record's person or kind, or is unknown and the person has no
    known card. Money coming in is never linked by itself (it may be a customer's)."""
    if tx.direction != "out":
        return False
    hash_rules = [r for r in rules_for(db, tx) if r.match_hash] if tx.counterparty_hash else []
    if hash_rules:
        r = hash_rules[0]
        if kind == "staff_payout":
            return r.target_type == "staff" and r.staff_id == rec.staff_id
        return r.target_type == {"expense": "expense", "capital": "owner"}.get(kind)
    if kind == "staff_payout" and tx.counterparty_hash:
        known = db.scalar(select(BankRule.id).where(BankRule.is_active.is_(True), BankRule.target_type == "staff",
                                                    BankRule.staff_id == rec.staff_id, BankRule.match_hash.is_not(None)).limit(1))
        return known is None  # their card is known and this is another one: not theirs
    return True


def _record(db: Session, kind: str, rec_id: int):  # noqa: ANN202
    return db.get({"staff_payout": StaffPayout, "expense": Expense, "capital": CapitalMove}[kind], rec_id)


def link(db: Session, tx: BankTransaction, kind: str, rec_id: int, user=None, auto: bool = False) -> None:  # noqa: ANN001
    from .audit import audit

    cands = {(c["type"], c["id"]) for c in link_candidates(db, tx, strict=auto)}
    if (kind, rec_id) not in cands:
        raise BankBookError("این ردیف با این تراکنش جور نیست (حساب، مبلغ یا تاریخ فرق دارد، یا به تراکنش دیگری وصل است)")
    if not _claim(db, tx):
        raise BankBookError("این تراکنش همین الان ثبت شد")
    tx.matched_type, tx.matched_id, tx.booked_at = kind, rec_id, local_now()
    tx.auto_booked = None  # linked, nothing booked from it
    if kind == "staff_payout":
        db.get(StaffPayout, rec_id).bank_transaction_id = tx.id
    audit(db, "bank_tx.link", "bank_transaction", tx.id, {"type": kind, "id": rec_id, "auto": auto}, user=user,
          actor="bank" if auto else None)


def after_record(db: Session, kind: str, rec) -> bool:  # noqa: ANN001
    """An entry made by hand (payout, expense, owner's withdrawal / money in): the bank line it is, if already here."""
    if kind == "staff_payout":
        account_id, amount, at, ref, direction = rec.payment_account_id, rec.amount, rec.paid_at, rec.reference, "out"
    elif kind == "expense":
        account_id, amount, at, ref, direction = rec.payment_account_id, rec.amount, rec.spent_at, None, "out"
    elif kind == "capital" and rec.kind in ("add", "withdraw"):
        account_id, amount, at, ref, direction = rec.payment_account_id, rec.amount, rec.at, None, "out" if rec.kind == "withdraw" else "in"
    else:
        return False
    if not account_id or direction != "out" or db.info.get("bank_booking"):
        return False  # (money in may be a customer's; a record made from a bank line is that line's)
    found = [t for t in db.scalars(select(BankTransaction).where(
        BankTransaction.manual_only.is_not(True),
        BankTransaction.payment_account_id == account_id, BankTransaction.status == "unmatched", BankTransaction.direction == direction,
        BankTransaction.amount == amount, BankTransaction.occurred_at.between(at - LINK_WINDOW, at + LINK_WINDOW)))
             if _refs_ok(ref, t.reference) and _consistent(db, t, kind, rec)]
    if len(found) != 1:
        return False  # none, or several alike: the inbox asks
    try:
        link(db, found[0], kind, rec.id, auto=True)
    except BankBookError:
        return False
    return True


# ------------------------------------------------------------------ 2. rules
def plain(name: str | None) -> str:
    from .history_shares import plain_name

    return plain_name(name or "")


def rules_for(db: Session, tx: BankTransaction) -> list[BankRule]:
    conds = []
    if tx.counterparty_hash:
        conds.append(BankRule.match_hash == tx.counterparty_hash)
    if tx.counterparty_name:
        conds.append((BankRule.match_kind == "name") & (BankRule.match_text == plain(tx.counterparty_name)))
    if not conds:
        return []
    return list(db.scalars(select(BankRule).where(BankRule.is_active.is_(True), BankRule.direction == tx.direction, or_(*conds))
                           .order_by(BankRule.id)))


def guess_kind_month(db: Session, person: Staff, amount: int, when: datetime, text: str = "") -> dict:
    """Advance or settlement, and for which month: the words with the line first, then what was owed at the month's
    start (paid by the 15th and not more than that: last month's settlement), then the day of the month."""
    from .jalali import gregorian_to_jalali, jmonth_of, month_range
    from .payout_receipt import read_caption
    from .staffpay import balance_at

    cap = read_caption(text or "", now=when)
    jd = gregorian_to_jalali(when.year, when.month, when.day)[2]
    jy, jm = jmonth_of(when)
    prev = (jy, jm - 1) if jm > 1 else (jy - 1, 12)
    kind, why = cap["kind"], "words" if cap["kind"] else None
    if kind is None:
        owed = balance_at(db, person.id, month_range(jy, jm)[0])
        if jd <= 15 and 0 < amount <= owed:
            kind, why = "settlement", "owed"
        else:
            kind, why = ("advance" if 6 <= jd <= 24 else "settlement"), "date"
    if cap["month"]:
        month = tuple(cap["month"])
    elif kind == "settlement" and jd <= 15:
        month = prev
    else:
        month = (jy, jm)
    return {"kind": kind, "jy": month[0], "jm": month[1], "why": why}


def _auto_ok(tx: BankTransaction) -> bool:
    now = local_now()
    return (tx.direction in ("in", "out") and tx.payment_account_id is not None and tx.counterparty_hash is not None
            and tx.duplicate_of_id is None and now - timedelta(days=AUTO_DAYS) <= tx.occurred_at <= now + timedelta(minutes=5))


def on_unmatched(db: Session, tx: BankTransaction) -> str | None:
    """A new (or still waiting) bank line that no customer's money took: linked or booked by itself, else it waits."""
    if tx.status != "unmatched" or tx.direction not in ("in", "out") or tx.manual_only:
        return None
    cands = [c for c in link_candidates(db, tx) if _consistent(db, tx, c["type"], _record(db, c["type"], c["id"]))]
    if len(cands) == 1 and len(link_candidates(db, tx, strict=False)) == 1:
        try:
            link(db, tx, cands[0]["type"], cands[0]["id"], auto=True)
            return "linked"
        except BankBookError:
            return None
    if link_candidates(db, tx, strict=False):
        return None  # entries alike (several, another tracking number, maybe someone else's): a person picks
    rules = rules_for(db, tx)
    exact = [r for r in rules if r.match_hash and r.match_hash == tx.counterparty_hash]
    # by itself only a staff payout or an expense (the owner's own money and setting aside: always a person)
    if len(exact) != 1 or len(rules) != 1 or not exact[0].auto or exact[0].target_type not in ("staff", "expense") or not _auto_ok(tx):
        return None
    rule = exact[0]
    if rule.auto_cap and tx.amount > rule.auto_cap:
        return None
    try:
        with db.begin_nested():
            book(db, tx, target=rule.target_type, staff_id=rule.staff_id, category=rule.category, line_id=rule.line_id,
                 from_treasury=bool(rule.from_treasury), rule=rule, auto=True)
        return "booked"
    except Exception:  # noqa: BLE001 - a line that can't be booked by itself (a duplicate payout...) waits in the inbox
        return None


# ------------------------------------------------------------------ booking a line
def book(db: Session, tx: BankTransaction, *, target: str, staff_id: int | None = None, kind: str | None = None,
         jy: int | None = None, jm: int | None = None, category: str | None = None, line_id: int | None = None,
         from_treasury: bool = False, description: str = "", rule: BankRule | None = None, auto: bool = False,
         force: bool = False, user=None) -> dict:  # noqa: ANN001
    """Book what the line is, from its own account, amount, date and tracking number. Returns what was booked."""
    from .audit import audit

    if target not in TARGETS:
        raise BankBookError("نوع ثبت نامعتبر است")
    if tx.status != "unmatched":
        raise BankBookError("این تراکنش قبلاً ثبت شده یا کنار گذاشته شده است")
    if tx.direction not in ("in", "out"):
        raise BankBookError("اول بگویید این تراکنش واریز بوده یا برداشت")
    pa = db.get(PaymentAccount, tx.payment_account_id) if tx.payment_account_id else None
    if target != "ignore" and pa is None:
        raise BankBookError("حساب این تراکنش معلوم نیست")
    text = (description or tx.description or "").strip()
    rec_type = rec_id = None
    result: dict = {"target": target}
    db.info["bank_booking"] = True  # the record made here is this line's: it must not take another waiting line
    try:
        rec_type, rec_id = _book_record(db, tx, pa, result, target=target, staff_id=staff_id, kind=kind, jy=jy, jm=jm, category=category,
                                        line_id=line_id, from_treasury=from_treasury, description=description, text=text,
                                        force=force, user=user)
    finally:
        db.info.pop("bank_booking", None)
    tx.matched_type, tx.matched_id = rec_type, rec_id
    tx.booked_at, tx.auto_booked = local_now(), True if auto else (False if rec_type else None)
    tx.rule_id = rule.id if rule is not None else None
    if rule is not None:
        rule.hits = (rule.hits or 0) + 1
        rule.last_used_at = local_now()
    audit(db, "bank_tx.book", "bank_transaction", tx.id, {"target": target, "record": rec_id, "amount": tx.amount, "auto": auto,
                                                          "rule": rule.id if rule else None}, user=user,
          actor="bank" if auto else None)
    return result


def _book_record(db: Session, tx: BankTransaction, pa: PaymentAccount | None, result: dict, *, target: str, staff_id: int | None,
                 kind: str | None, jy: int | None, jm: int | None, category: str | None, line_id: int | None, from_treasury: bool,
                 description: str, text: str, force: bool, user) -> tuple[str | None, int | None]:  # noqa: ANN001
    from . import accounting, capital, staffpay

    rec_type = rec_id = None
    if target == "staff":
        if tx.direction != "out":
            raise BankBookError("پرداخت به پرسنل، برداشت از حساب است")
        person = db.get(Staff, staff_id or 0)
        if person is None:
            raise BankBookError("پرسنل را انتخاب کنید")
        g = guess_kind_month(db, person, tx.amount, tx.occurred_at, tx.description or "")
        kind = kind if kind in ("advance", "settlement") else g["kind"]
        jy, jm = (jy, jm) if jy and jm else (g["jy"], g["jm"])
        if not _claim(db, tx):
            raise BankBookError("این تراکنش همین الان ثبت شد")
        p = staffpay.pay(db, person, tx.amount, kind=kind, payment_account=pa, paid_at=min(tx.occurred_at, local_now()), jy=jy, jm=jm,
                         reference=(tx.reference or "")[:64], description=(description or "").strip(), source="bank",
                         bank_transaction_id=tx.id, force=force, user=user)
        rec_type, rec_id = "staff_payout", p.id
        result.update(staff_id=person.id, name=person.full_name, mobile=person.mobile, kind=kind, jy=jy, jm=jm,
                      kind_label=staffpay.kind_label(person, kind), month=staffpay.month_label(jy, jm), payout_id=p.id,
                      balance=staffpay.balance_at(db, person.id))
    elif target == "expense":
        if tx.direction != "out":
            raise BankBookError("هزینه، برداشت از حساب است")
        if not _claim(db, tx):
            raise BankBookError("این تراکنش همین الان ثبت شد")
        e = accounting.record_expense(db, category=category or "", amount=tx.amount, payment_account=pa,
                                      spent_at=min(tx.occurred_at, local_now()), description=text[:500], line_id=line_id,
                                      from_treasury=from_treasury, force=True, staff_id=staff_id if from_treasury else None, user=user)
        rec_type, rec_id = "expense", e.id
        result.update(expense_id=e.id, category=e.category)
    elif target == "owner":
        if not _claim(db, tx):
            raise BankBookError("این تراکنش همین الان ثبت شد")
        m = capital.add_or_withdraw(db, kind="withdraw" if tx.direction == "out" else "add", amount=tx.amount, payment_account=pa,
                                    at=min(tx.occurred_at, local_now()), notes=(description or "از روی تراکنش بانک").strip(),
                                    force=True, user=user)
        rec_type, rec_id = "capital", m.id
        result.update(capital_id=m.id)
    else:  # ignore
        if not _claim(db, tx, "unmatched", "ignored"):
            raise BankBookError("این تراکنش همین الان ثبت شد")
    return rec_type, rec_id


def undo(db: Session, tx: BankTransaction, user=None, then: str = "inbox") -> None:  # noqa: ANN001
    """Take back what was booked from a line (its record voided on its own date) or the link to an entry; the line
    waits in the inbox again (or is set aside). A rule that booked it stops booking by itself until confirmed."""
    from . import accounting, capital, staffpay
    from .audit import audit

    if tx.status == "ignored":
        if not _claim(db, tx, "ignored", "unmatched"):
            raise BankBookError("وضعیت این تراکنش همین الان عوض شد")
        tx.manual_only = True  # a person decides it now (never linked or booked by itself again)
        audit(db, "bank_tx.unignore", "bank_transaction", tx.id, {}, user=user)
        return
    if tx.status != "matched" or tx.matched_type not in ("staff_payout", "expense", "capital"):
        raise BankBookError("این تراکنش از اینجا برگشت داده نمی‌شود")
    booked_here = tx.auto_booked is not None  # booked from the line (by itself or by a person); None: only linked
    kind, rec_id = tx.matched_type, tx.matched_id
    if not _claim(db, tx, "matched", "ignored" if then == "ignore" else "unmatched"):
        raise BankBookError("وضعیت این تراکنش همین الان عوض شد")
    if kind == "staff_payout":
        p = db.get(StaffPayout, rec_id)
        if p is not None:
            p.bank_transaction_id = None
            if booked_here and p.status == "booked":
                staffpay.void_payout(db, p, "برگشت ثبت از روی بانک", user=user)
    elif kind == "expense" and booked_here:
        e = db.get(Expense, rec_id)
        if e is not None and e.voided_at is None:
            accounting.void_expense(db, e, "برگشت ثبت از روی بانک", user=user)
    elif kind == "capital" and booked_here:
        m = db.get(CapitalMove, rec_id)
        if m is not None:
            capital.void_move(db, m, "برگشت ثبت از روی بانک", user=user)
    if tx.rule_id:
        rule = db.get(BankRule, tx.rule_id)
        if rule is not None:
            rule.undos = (rule.undos or 0) + 1
            rule.auto = False  # it booked something wrong: suggested only, until a person turns it on again
    tx.matched_type = tx.matched_id = tx.rule_id = tx.booked_at = tx.auto_booked = None
    tx.manual_only = True  # taken back: the hourly look-again must not link or book it the same way again
    audit(db, "bank_tx.undo", "bank_transaction", tx.id, {"type": kind, "id": rec_id, "voided": booked_here, "then": then}, user=user)


# ------------------------------------------------------------------ remembering who a card is
def add_rule(db: Session, *, match_kind: str, match_hash: str | None, match_text: str, target: str, direction: str = "out",
             staff_id: int | None = None, category: str | None = None, line_id: int | None = None, from_treasury: bool = False,
             auto: bool = False, source: str = "manual", may_change: bool = True, user=None) -> BankRule:  # noqa: ANN001
    """A new rule, or the rule of the same card / name changed (only by the owner / admin: may_change; a card known as
    a staff member's is never given to someone or something else - it is removed on their statement first)."""
    from .audit import audit

    if target not in TARGETS:
        raise BankBookError("نوع نامعتبر است")
    if target == "staff" and db.get(Staff, staff_id or 0) is None:
        raise BankBookError("پرسنل را انتخاب کنید")
    if target == "expense" and not (category or "").strip():
        raise BankBookError("دستهٔ هزینه را بنویسید")
    if match_kind == "name":
        auto = False  # a name is never enough to book money by itself
    same = db.scalar(select(BankRule).where(BankRule.is_active.is_(True), BankRule.direction == direction,
                                            (BankRule.match_hash == match_hash) if match_hash else
                                            ((BankRule.match_kind == "name") & (BankRule.match_text == match_text))))
    before = None
    if same is not None:
        if same.target_type == "staff" and (target != "staff" or same.staff_id != staff_id):
            other = db.get(Staff, same.staff_id or 0)
            raise BankBookError(f"این کارت/شبا مال «{other.full_name if other else '—'}» ثبت شده است؛ اگر اشتباه است، اول در "
                                "صورت‌حساب او برش دارید")
        if not may_change:
            raise BankBookError("این کارت قبلاً به چیز دیگری وصل شده؛ تغییرش با مدیر است (تطبیق ← کارت‌های شناخته‌شده)")
        before = {"target": same.target_type, "staff": same.staff_id, "category": same.category, "auto": same.auto}
        rule = same
    else:
        rule = BankRule(match_kind=match_kind, match_hash=match_hash, match_text=match_text[:128], direction=direction,
                        created_by=getattr(user, "id", None), source=source)
        db.add(rule)
    rule.target_type, rule.staff_id, rule.category = target, staff_id if target == "staff" else None, (category or None)
    rule.line_id, rule.from_treasury, rule.auto = line_id, True if from_treasury else None, bool(auto)
    db.flush()
    audit(db, "bank_rule.save", "bank_rule", rule.id, {"kind": match_kind, "mask": match_text, "target": target, "staff": staff_id,
                                                       "category": category, "auto": bool(auto), "before": before}, user=user)
    return rule


def remember(db: Session, tx: BankTransaction, *, target: str, staff_id: int | None = None, category: str | None = None,
             line_id: int | None = None, from_treasury: bool = False, auto: bool = False, may_change: bool = True,
             user=None) -> BankRule | None:  # noqa: ANN001
    if target == "ignore":
        auto = False  # setting aside is never done by itself
    if tx.counterparty_hash:
        kind = "iban" if (tx.counterparty_mask or "").upper().startswith("IR") else "card"
        return add_rule(db, match_kind=kind, match_hash=tx.counterparty_hash, match_text=tx.counterparty_mask or "", target=target,
                        direction=tx.direction, staff_id=staff_id, category=category, line_id=line_id, from_treasury=from_treasury,
                        auto=auto, source="learned", may_change=may_change, user=user)
    if tx.counterparty_name:
        return add_rule(db, match_kind="name", match_hash=None, match_text=plain(tx.counterparty_name), target=target,
                        direction=tx.direction, staff_id=staff_id, category=category, line_id=line_id, from_treasury=from_treasury,
                        auto=False, source="learned", may_change=may_change, user=user)
    return None


def staff_card(db: Session, person: Staff, number: str, user=None) -> BankRule:  # noqa: ANN001
    """A staff member's card / IBAN entered on the staff page: payments to it are booked as theirs by themselves.
    Earlier bank lines to it (waiting in the inbox) are looked at again (rule.booked_now: how many were booked)."""
    from .bank_parties import check_number, mask, number_hash

    try:
        kind, value = check_number(number)
    except ValueError as exc:
        raise BankBookError(str(exc)) from exc
    rule = add_rule(db, match_kind=kind, match_hash=number_hash(kind, value), match_text=mask(kind, value), target="staff",
                    staff_id=person.id, auto=True, source="staff_form", user=user)
    n = 0
    for tx in list(db.scalars(select(BankTransaction).where(BankTransaction.status == "unmatched",
                                                            BankTransaction.counterparty_hash == rule.match_hash))):
        n += bool(on_unmatched(db, tx))
    rule.booked_now = n  # (not a column: told to the person)
    return rule


# ------------------------------------------------------------------ the inbox
def suggestions(db: Session, tx: BankTransaction) -> list[dict]:
    """What a waiting line most likely is, best first (each a one-tap booking)."""
    from .history_shares import suggest_staff

    out: list[dict] = []
    for c in link_candidates(db, tx, strict=False):
        out.append({"action": "link", "type": c["type"], "id": c["id"], "label": f"همان «{c['label']}» که ثبت شده",
                    "why": "همین مبلغ و حساب، چند روز این طرف یا آن طرف"})
    for r in rules_for(db, tx):
        if r.target_type == "staff":
            person = db.get(Staff, r.staff_id or 0)
            if person is None:
                continue
            g = guess_kind_month(db, person, tx.amount, tx.occurred_at, tx.description or "")
            from .staffpay import kind_label, month_label

            out.append({"action": "book", "target": "staff", "staff_id": person.id, "kind": g["kind"], "jy": g["jy"], "jm": g["jm"],
                        "label": f"{kind_label(person, g['kind'])} {person.full_name} - {month_label(g['jy'], g['jm'])}",
                        "why": "کارت/شبای همین شخص" if r.match_hash else "نامِ همین شخص", "rule_id": r.id})
        elif r.target_type == "expense":
            out.append({"action": "book", "target": "expense", "category": r.category, "line_id": r.line_id,
                        "from_treasury": bool(r.from_treasury), "label": f"هزینهٔ {r.category}", "why": "مثل دفعه‌های قبل", "rule_id": r.id})
        elif r.target_type == "owner":
            out.append({"action": "book", "target": "owner", "label": "پول خود مدیر" if tx.direction == "out" else "آوردن سرمایه",
                        "why": "مثل دفعه‌های قبل", "rule_id": r.id})
        elif r.target_type == "ignore":
            out.append({"action": "book", "target": "ignore", "label": "کنار گذاشتن", "why": "مثل دفعه‌های قبل", "rule_id": r.id})
    from .textutil import normalize_text

    if tx.direction == "out" and not any(s.get("target") == "expense" for s in out) and \
            any(w in normalize_text(tx.description or "") for w in ("کارمزد", "هزینه خدمات", "حق اشتراک")):
        out.append({"action": "book", "target": "expense", "category": "کارمزد بانکی", "line_id": None, "from_treasury": False,
                    "label": "هزینهٔ کارمزد بانکی", "why": "کلمهٔ «کارمزد» در شرح"})
    if tx.direction == "out" and not any(s.get("target") == "staff" for s in out):
        people = [p for p in db.scalars(select(Staff).where(Staff.is_active.is_(True)).order_by(Staff.id))]
        hit = suggest_staff(tx.counterparty_name, people) if tx.counterparty_name else None
        by_card = [p for p in people if tx.counterparty_mask and tx.counterparty_mask[-4:] in (p.cards or [])]
        for person, why in ([(db.get(Staff, hit["id"]), "نامِ روی تراکنش")] if hit else []) + [(p, "کارتی که قبلاً با رسید به او پرداخت شده") for p in by_card]:
            if person is None or any(s.get("staff_id") == person.id for s in out):
                continue
            from .staffpay import kind_label, month_label

            g = guess_kind_month(db, person, tx.amount, tx.occurred_at, tx.description or "")
            out.append({"action": "book", "target": "staff", "staff_id": person.id, "kind": g["kind"], "jy": g["jy"], "jm": g["jm"],
                        "label": f"{kind_label(person, g['kind'])} {person.full_name} - {month_label(g['jy'], g['jm'])}", "why": why})
    return out[:6]


def tx_row(db: Session, tx: BankTransaction, accounts: dict[int, str] | None = None, with_suggestions: bool = False,
           may_void: bool = True, capital_ok: bool = True) -> dict:
    accounts = accounts if accounts is not None else {pa.id: pa.name for pa in db.scalars(select(PaymentAccount))}
    row = {"id": tx.id, "account_id": tx.payment_account_id, "account": accounts.get(tx.payment_account_id), "amount": tx.amount,
           "direction": tx.direction, "occurred_at": tx.occurred_at.isoformat(timespec="minutes"), "reference": tx.reference,
           "description": tx.description, "status": tx.status, "matched_type": tx.matched_type, "matched_id": tx.matched_id,
           "matched_label": LABELS.get(tx.matched_type or ""), "source": tx.source, "counterparty": tx.counterparty_mask,
           "counterparty_name": tx.counterparty_name, "has_card": tx.counterparty_hash is not None,
           "auto_booked": tx.auto_booked, "rule_id": tx.rule_id,
           "duplicate_of_id": tx.duplicate_of_id, "booked_at": tx.booked_at.isoformat(timespec="minutes") if tx.booked_at else None,
           # a link or a set-aside: anyone who moves money; voiding what was booked from the line: owner / admin
           "can_undo": tx.status == "ignored" or (tx.status == "matched" and tx.matched_type in ("staff_payout", "expense", "capital")
                                                  and (tx.auto_booked is None or may_void))}
    if tx.status == "matched" and tx.matched_type == "staff_payout":
        p = db.get(StaffPayout, tx.matched_id or 0)
        who = db.get(Staff, p.staff_id) if p else None
        if p is not None and who is not None:
            from .staffpay import kind_label, month_label

            row["matched_label"] = f"{kind_label(who, p.kind)} {who.full_name} - {month_label(p.jy, p.jm)}"
            row["staff_id"], row["staff_mobile"] = who.id, who.mobile
    elif tx.status == "matched" and tx.matched_type == "expense":
        e = db.get(Expense, tx.matched_id or 0)
        if e is not None:
            row["matched_label"] = f"هزینهٔ {e.category}" + (" (از خزانه)" if e.from_treasury else "")
    if with_suggestions:
        row["suggestions"] = [x for x in suggestions(db, tx) if capital_ok or (x.get("target") != "owner" and x.get("type") != "capital")]
    return row


def rematch(db: Session, days: int = 30) -> int:
    """Look at the waiting lines again (an entry made since, a card set since)."""
    n = 0
    since = local_now() - timedelta(days=days)
    for tx in list(db.scalars(select(BankTransaction).where(BankTransaction.status == "unmatched", BankTransaction.occurred_at >= since)
                              .order_by(BankTransaction.occurred_at))):
        from .matching import on_bank_transaction

        on_bank_transaction(db, tx)  # a customer's money first (a deposit / payment entered since), then the rest
        n += tx.status != "unmatched"
    return n
