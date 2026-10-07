"""Keeping the list of services clean: archive / restore, merge duplicates, codes that follow the line, health check.

A service that was ever sold can't be erased (its invoices and history point to it), so "delete" archives it: it is
hidden from the forms but stays in the reports and keeps its code. This module makes those archived services visible
and fixable, and finds the usual problems of a catalog built from an import (the same service twice with a typo,
a code that no longer matches the line the service was moved to, services left in the import line ...).
"""
from __future__ import annotations

import re

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..models import (
    Appointment,
    ConversationMessage,
    Deposit,
    Invoice,
    InvoiceItem,
    KnowledgeItem,
    Service,
    ServiceLine,
    WaitlistEntry,
)
from . import codes, learning
from .textutil import normalize_text

IMPORT_LINE = "خدمات انتقالی"
_EXTRA = str.maketrans({"ى": "ی", "أ": "ا", "إ": "ا", "ۀ": "ه", "آ": "ا", "ئ": "ی", "ؤ": "و"})  # رضائی = رضایی


def name_key(text: str | None) -> str:
    """Comparable form of a name: ی/ي, ک/ك, spaces and half-spaces don't matter."""
    return re.sub(r"[\s\-_‌.:،,()]+", "", normalize_text(text or "").translate(_EXTRA))


def _osa(a: str, b: str) -> int:
    """Edit distance counting a swap of two neighbouring letters as one edit (typos)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def keys_alike(ka: str, kb: str) -> bool:
    """Two name keys that are probably the same service written differently (a typo, two letters swapped)."""
    if ka == kb:
        return True
    short, long_ = sorted((ka, kb), key=len)
    if len(short) < 4 or len(long_) - len(short) > 2:
        return False
    if "".join(ch for ch in ka if ch.isdigit()) != "".join(ch for ch in kb if ch.isdigit()):
        return False  # «لیزر ۲ جلسه» and «لیزر ۳ جلسه» are different services
    if short in long_ and len(long_) - len(short) > 1:
        return False  # a whole word more: «مانیکور» / «مانیکور ژل»
    return _osa(ka, kb) <= (1 if len(short) <= 6 else 2)


def looks_alike(a: str, b: str) -> bool:
    return keys_alike(name_key(a), name_key(b))


def similar(db: Session, name: str, limit: int = 2) -> list[Service]:
    """Existing services whose name looks like `name` (for import suggestions)."""
    k = name_key(name)
    return [s for s in db.scalars(select(Service).order_by(Service.is_active.desc(), Service.id))
            if keys_alike(k, name_key(s.name))][:limit]


def find_by_name(db: Session, line_id: int, name: str, exclude: int | None = None) -> Service | None:
    key = name_key(name)
    for s in db.scalars(select(Service).where(Service.line_id == line_id).order_by(Service.is_active.desc(), Service.id)):
        if s.id != exclude and name_key(s.name) == key:
            return s
    return None


def code_fits_line(code: str | None, line: ServiceLine | None, line_codes: set[str] | None = None) -> bool:
    """Does the service code start with its own line's code (line 3 -> 3xx)?"""
    if not code or line is None or not line.code:
        return True
    return codes.owner_line_code(code, (line_codes or set()) | {line.code}) == line.code


def recode(db: Session, svc: Service) -> str:
    """Give the service the next free code of its own line (line 3 -> 3xx); its old code becomes free."""
    line = db.get(ServiceLine, svc.line_id)
    if line is None or not line.code:
        return svc.code or ""
    svc.code = None
    db.flush()
    svc.code = codes.next_service_code(db, line)
    db.flush()
    return svc.code


# ------------------------------------------------------------------ usage
def usage(db: Session, ids: list[int] | None = None) -> dict[int, dict]:
    """Per service: invoices here (not void), history from the previous software, deposits, open appointments."""
    out: dict[int, dict] = {}

    def row(sid: int) -> dict:
        return out.setdefault(sid, {"invoices": 0, "invoice_amount": 0, "old": 0, "old_amount": 0, "deposits": 0,
                                    "appointments": 0, "last": None})

    amt = func.coalesce(InvoiceItem.net_amount, InvoiceItem.unit_price * InvoiceItem.quantity - InvoiceItem.discount)
    q = (select(InvoiceItem.service_id, func.count(func.distinct(InvoiceItem.invoice_id)), func.sum(amt), func.max(Invoice.issued_at))
         .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
         .where(InvoiceItem.service_id.is_not(None), Invoice.status != "void").group_by(InvoiceItem.service_id))
    if ids is not None:
        q = q.where(InvoiceItem.service_id.in_(ids))
    for sid, n, total, last in db.execute(q):
        r = row(sid)
        r["invoices"], r["invoice_amount"], r["last"] = int(n or 0), int(total or 0), last
    q = (select(Appointment.service_id, func.count(Appointment.id), func.sum(Appointment.quoted_price), func.max(Appointment.start_at))
         .where(Appointment.service_id.is_not(None), Appointment.status == "done", Appointment.invoice_id.is_(None))
         .group_by(Appointment.service_id))
    if ids is not None:
        q = q.where(Appointment.service_id.in_(ids))
    for sid, n, total, last in db.execute(q):
        r = row(sid)
        r["old"], r["old_amount"] = int(n or 0), int(total or 0)
        r["last"] = max(r["last"], last) if r["last"] and last else (r["last"] or last)
    q = select(Deposit.service_id, func.count(Deposit.id)).where(Deposit.service_id.is_not(None)).group_by(Deposit.service_id)
    if ids is not None:
        q = q.where(Deposit.service_id.in_(ids))
    for sid, n in db.execute(q):
        row(sid)["deposits"] = int(n or 0)
    q = (select(Appointment.service_id, func.count(Appointment.id))
         .where(Appointment.service_id.is_not(None), Appointment.status.in_(("booked", "confirmed", "arrived")))
         .group_by(Appointment.service_id))
    if ids is not None:
        q = q.where(Appointment.service_id.in_(ids))
    for sid, n in db.execute(q):
        row(sid)["appointments"] = int(n or 0)
    for r in out.values():
        r["last"] = r["last"].isoformat(timespec="minutes") if r["last"] else None
        r["amount"] = r["invoice_amount"] + r["old_amount"]
    return out


# ------------------------------------------------------------------ merge
def merge(db: Session, src: Service, dst: Service) -> dict:
    """Move everything of `src` (invoices, history, deposits, appointments, waiting list, what the system learned)
    to `dst`, keep src's name as another name of dst, and remove src."""
    if src.id == dst.id:
        raise ValueError("یک خدمت را نمی‌توان با خودش ادغام کرد")
    moved = {}
    for model in (InvoiceItem, Appointment, Deposit, WaitlistEntry, ConversationMessage):
        res = db.execute(update(model).where(model.service_id == src.id).values(service_id=dst.id))
        moved[model.__tablename__] = res.rowcount or 0
    # invoice lines of the merged service keep the line they were sold in (the books don't change)
    for item in db.scalars(select(KnowledgeItem).where(KnowledgeItem.kind == "token_service")):
        counts = dict(item.value or {})
        if str(src.id) in counts:
            counts[str(dst.id)] = counts.get(str(dst.id), 0) + counts.pop(str(src.id))
            item.value = counts
    names = [src.name, *(src.aliases or [])]
    known = {name_key(dst.name), *(name_key(a) for a in dst.aliases or [])}
    extra = []
    for n in names:
        if n and name_key(n) not in known:
            known.add(name_key(n))
            extra.append(n.strip())
    if extra:
        dst.aliases = [*(dst.aliases or []), *extra]
    if src.is_active and not dst.is_active:
        dst.is_active = True  # merging an active service into an archived one brings that one back
    db.flush()
    db.delete(src)
    db.flush()
    learning.refresh_price_stats(db)
    return moved


# ------------------------------------------------------------------ health check
def health(db: Session) -> dict:
    """Problems in the list of services, each with the fix the settings page offers."""
    services = list(db.scalars(select(Service).order_by(Service.line_id, Service.id)))
    lines = {l.id: l for l in db.scalars(select(ServiceLine))}
    use = usage(db)

    def brief(s: Service) -> dict:
        ln = lines.get(s.line_id)
        u = use.get(s.id) or {}
        return {"id": s.id, "code": s.code, "name": s.name, "line_id": s.line_id, "line": ln.name if ln else None,
                "line_code": ln.code if ln else None, "line_active": bool(ln and ln.is_active), "is_active": s.is_active,
                "sales": (u.get("invoices") or 0) + (u.get("old") or 0), "amount": u.get("amount") or 0,
                "last": u.get("last"), "base_price": s.base_price}

    issues: list[dict] = []
    # 1) archived but sold: hidden from the list, still in the reports, code still taken
    hidden = [brief(s) for s in services if not s.is_active and s.id in use]
    if hidden:
        issues.append({"kind": "archived_used", "level": "warning", "title": "خدمات بایگانی‌شده‌ای که سابقه فروش دارند",
                       "help": "این خدمات قبلاً «حذف» شده‌اند ولی چون فاکتور یا سابقه دارند فقط پنهان شده‌اند؛ در گزارش‌ها دیده "
                               "می‌شوند و کدشان آزاد نیست. اگر هنوز ارائه می‌شوند «بازگردانی»، اگر تکراری‌اند «ادغام» کنید.",
                       "items": hidden})
    # 2) active services whose line is archived: the line doesn't show, so they look orphaned
    orphan = [brief(s) for s in services if s.is_active and not (lines.get(s.line_id) and lines[s.line_id].is_active)]
    if orphan:
        issues.append({"kind": "line_archived", "level": "warning", "title": "خدمات فعال در لاین بایگانی‌شده",
                       "help": "لاین این خدمات حذف (بایگانی) شده ولی خودشان فعال‌اند. لاین را بازگردانید یا خدمت را به لاین دیگری ببرید.",
                       "items": orphan})
    # 3) the same service twice (exact after unifying letters, or a typo / swapped letters)
    groups: list[list[Service]] = []
    seen: set[int] = set()
    keys = {s.id: name_key(s.name) for s in services}
    imp_lines = {l.id for l in lines.values() if l.name == IMPORT_LINE}
    for i, a in enumerate(services):
        if a.id in seen:
            continue
        group = [a]
        for b in services[i + 1:]:
            if b.id in seen:
                continue
            # in the same line, or one of them still in the import line (the same name in two real lines -
            # e.g. «رنگ» for hair and for brows - is a different service)
            near = a.line_id == b.line_id or a.line_id in imp_lines or b.line_id in imp_lines
            if near and keys_alike(keys[a.id], keys[b.id]):
                group.append(b)
        if len(group) > 1:
            seen.update(x.id for x in group)
            groups.append(group)
    if groups:
        issues.append({"kind": "duplicates", "level": "warning", "title": "خدمات تکراری یا با املای متفاوت",
                       "help": "به نظر می‌رسد این‌ها یک خدمت‌اند که دو بار (مثلاً با غلط تایپی در نرم‌افزار قبلی) ثبت شده‌اند. "
                               "با «ادغام»، همهٔ فاکتورها، سوابق، بیعانه‌ها و نوبت‌ها به خدمت اصلی منتقل و نام دیگر به عنوان "
                               "«نام دیگر» آن ذخیره می‌شود.",
                       "groups": [sorted((brief(x) for x in g), key=lambda x: (-x["sales"], not x["is_active"], x["id"])) for g in groups]})
    # 4) code doesn't follow the line (service moved to another line after it got its code)
    line_codes = {l.code for l in lines.values() if l.code}
    wrong = [brief(s) for s in services if s.code and not code_fits_line(s.code, lines.get(s.line_id), line_codes)]
    if wrong:
        issues.append({"kind": "code_mismatch", "level": "info", "title": "کد خدمت با کد لاین آن هماهنگ نیست",
                       "help": "کد هر خدمت با کد لاینش شروع می‌شود (لاین ۳ ← ۳۰۱). این خدمات بعد از گرفتن کد به لاین دیگری "
                               "منتقل شده‌اند. «اصلاح کد» شمارهٔ بعدی لاین خودشان را می‌دهد (فقط کد عوض می‌شود، سوابق دست نمی‌خورد).",
                       "items": wrong})
    # 5) still in the import line
    imp = [l.id for l in lines.values() if l.name == IMPORT_LINE]
    left = [brief(s) for s in services if s.line_id in imp and s.is_active]
    if left:
        issues.append({"kind": "import_line", "level": "info", "title": f"خدمات باقی‌مانده در لاین «{IMPORT_LINE}»",
                       "help": "این خدمات هنگام انتقال از نرم‌افزار قبلی ساخته شده‌اند. هر کدام را ویرایش و به لاین درستش "
                               "منتقل کنید، یا با خدمت موجود ادغام کنید.",
                       "items": left})
    # 6) no base price (services made by the import start at 0)
    free = [brief(s) for s in services if s.is_active and not s.base_price]
    if free:
        issues.append({"kind": "no_price", "level": "info", "title": "خدمات فعال بدون قیمت پایه",
                       "help": "قیمت پایه برای پیشنهاد مبلغ در فاکتور و هشدار قیمت غیرعادی لازم است.",
                       "items": free})
    return {"issues": issues, "count": sum(len(i.get("items") or i.get("groups") or []) for i in issues if i["level"] == "warning")}
