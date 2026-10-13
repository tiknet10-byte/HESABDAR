"""Bringing over the clinic's product accounting from Tizpardaz (Excel exports): customers, products, journal.

1. Customers (کد حساب / عنوان حساب / بدهکار / بستانکار). Tizpardaz writes names as «نام‌خانوادگی(نام)»; its codes are
   not Chehreh's, so the only link to customers already here is the first + last name. Exact matches are linked,
   several customers with the same name are a question for the user, the rest become new customers (often
   without a mobile). Debit / credit balances become opening balances (owed by the customer / customer credit).
2. Products (کد کالا, نام کالا, تعداد بدهکار/بستانکار, فی آخرین خرید/فروش ...). Tizpardaz is the reference: its code
   becomes the product's sales code, the SKU comes from the clinic's code -> SKU list, and its final quantity
   is the stock here. A product that already exists here - or that was sold in Chehreh as a "service" - is
   suggested for combining and combined only when the user agrees.
3. Journal (نوع سند, شماره سند, تاریخ, عنوان حساب, شرح, مقدار, فی, بدهکار, بستانکار): sales, purchases and expenses
   become history (TradeHistory) for the reports and the customer files. They don't move money or stock here -
   the balances and the stock above already carry their result - so nothing is counted twice. The cost of each
   historical sale is worked out by the costing engine from the historical purchases.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, time
from types import SimpleNamespace

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..models import (
    Appointment,
    Customer,
    Deposit,
    ImportBatch,
    InvoiceItem,
    JournalEntry,
    JournalLine,
    PaymentAccount,
    Product,
    PurchaseItem,
    Service,
    Staff,
    StockMove,
    Supplier,
    TradeDoc,
    TradeHistory,
    WooOutbox,
    WooProduct,
    local_now,
)
from . import accounting, inventory, settings_store, woo_queue
from .customer_merge import has_records, person_key
from .legacy_import import ImportProblem, _amount, _key, _text, parse_date, read_table
from .service_catalog import keys_alike, name_key
from .textutil import normalize_mobile, to_en_digits
from .tizpardaz_defaults import SKU_MAP

SOURCE = "tizpardaz"
KINDS = {"customers": "مشتریان (طرف حساب‌ها)", "products": "کالاها", "journal": "دفتر روزنامه (فروش، خرید، هزینه)"}
MAX_ROWS = 50_000

SYN = {
    "customers": {"code": ["کد حساب", "کد طرف حساب", "کد شخص", "کد مشتری", "کد"], "name": ["عنوان حساب", "نام طرف حساب", "نام شخص", "نام مشتری", "نام"],
                  "debit": ["بدهکار", "مانده بدهکار"], "credit": ["بستانکار", "مانده بستانکار"],
                  "mobile": ["موبایل", "تلفن همراه", "همراه", "شماره موبایل", "تلفن"]},
    "products": {"code": ["کد کالا", "کد"], "tech": ["کد فنی کالا", "کد فنی"], "group": ["گروه اصلی"], "subgroup": ["زیرگروه اصلی", "زیر گروه اصلی"],
                 "subgroup2": ["زیرگروه فرعی", "زیر گروه فرعی"], "name": ["نام کالا", "شرح کالا", "عنوان کالا"],
                 "qty_dr": ["تعداد بدهکار", "موجودی"], "qty_cr": ["تعداد بستانکار"], "buy": ["قیمت خرید"],
                 "sell": ["قیمت فروش1", "قیمت فروش"], "last_sell": ["فی آخرین فروش"], "last_buy": ["فی آخرین خرید"], "sku": ["sku"]},
    "journal": {"doc_type": ["نوع سند"], "doc_no": ["شماره سند"], "inner_no": ["شماره سند داخلی"], "date": ["تاریخ"],
                "account": ["عنوان حساب", "شرح حساب"], "account_code": ["کد حساب", "کد تفصیلی"], "desc": ["شرح"], "qty": ["مقدار", "تعداد"], "price": ["فی"],
                "debit": ["بدهکار"], "credit": ["بستانکار"]},
}
# accounts that are not a person (walk-in sales, cash, bank): not made into customers unless the user says so
GENERIC = {"متفرقه", "مشتری متفرقه", "مشتریان متفرقه", "فروش متفرقه", "نقدی", "فروش نقدی", "مشتری نقدی", "مشتری عمومی", "عمومی",
           "مشتری گذری", "گذری", "صندوق", "بانک", "سایت", "فروش سایت", "فروش اینترنتی", "مشتری سایت"}
NEED = {"customers": ["name"], "products": ["code", "name"], "journal": ["doc_type", "date", "desc"]}
_AR = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "‌": " "})


# ------------------------------------------------------------------ reading
def _fa(text: object) -> str:
    """Display form: Persian ی/ک, single spaces."""
    return re.sub(r"\s+", " ", _text(text).translate(_AR)).strip()


def tp_name(raw: object) -> str:
    """«محمودي(سياوش)» -> «سیاوش محمودی»; a name without parentheses stays as it is."""
    s = _fa(raw)
    m = re.fullmatch(r"(.+?)\s*[(（]\s*(.+?)\s*[)）]", s)
    return f"{m.group(2)} {m.group(1)}" if m else s


def _mapping(header: list[object], kind: str) -> dict[str, int]:
    keys = [_key(h) for h in header]
    out: dict[str, int] = {}
    for exact in (True, False):
        for field, words in SYN[kind].items():
            if field in out:
                continue
            for w in words:
                wk = _key(w)
                hit = next((i for i, k in enumerate(keys) if k and i not in out.values() and (k == wk if exact else len(wk) >= 3 and wk in k)), None)
                if hit is not None:
                    out[field] = hit
                    break
    return out


def _table(filename: str, data: bytes, kind: str) -> tuple[list[list[object]], dict[str, int]]:
    rows = read_table(filename, data)
    if not rows:
        raise ImportProblem("فایل خالی است")
    best = max(range(min(15, len(rows))), key=lambda i: len(_mapping(rows[i], kind)))
    mapping = _mapping(rows[best], kind)
    missing = [w for w in NEED[kind] if w not in mapping]
    if missing:
        raise ImportProblem("ستون‌های لازم پیدا نشد: " + "، ".join(SYN[kind][m][0] for m in missing) + " - فایل درست انتخاب شده؟")
    return rows[best + 1: best + 1 + MAX_ROWS], mapping


def _get(r: list[object], mapping: dict[str, int], f: str) -> object:
    i = mapping.get(f)
    return r[i] if i is not None and i < len(r) else None


def _int(value: object) -> int:
    t = to_en_digits(_text(value)).replace(",", "").replace("٬", "")
    neg = t.startswith("-") or t.startswith("(")
    t = re.sub(r"[^\d.]", "", t)
    if not t:
        return 0
    n = int(float(t))
    return -n if neg else n


def _money(value: object, unit: str) -> int:
    n = _amount(value, unit)
    return int(n or 0)


def parse_sku_map(text: str) -> dict[str, str]:
    """Lines «کد<tab>SKU»: the code is a number, the SKU starts with an English letter."""
    out = {}
    for line in (text or "").splitlines():
        parts = to_en_digits(line).replace("‏", "").split()
        code = next((p for p in parts if p.isdigit()), None)
        sku = next((p for p in parts if re.match(r"[A-Za-z]", p)), None)
        if code and sku:
            out[code] = sku.strip()
    return out


def sku_map_text(db: Session) -> str:
    return settings_store.get(db, "tizpardaz.sku_map") or SKU_MAP


# ------------------------------------------------------------------ customers
def is_generic(name: str) -> bool:
    return person_key(name) in {person_key(g) for g in GENERIC}


def _customer_index(db: Session) -> dict[str, list[Customer]]:
    idx: dict[str, list[Customer]] = defaultdict(list)
    for c in db.scalars(select(Customer)):
        k = person_key(c.full_name)
        if k:
            idx[k].append(c)
    return idx


def _cbrief(c: Customer, spent: dict[int, int]) -> dict:
    return {"id": c.id, "full_name": c.full_name, "code": c.legacy_code, "mobile": c.mobile, "tp_code": c.tp_code,
            "source": c.source, "spent": spent.get(c.id, 0)}


def _spent(db: Session, ids: list[int]) -> dict[int, int]:
    from ..models import Invoice
    if not ids:
        return {}
    out: dict[int, int] = defaultdict(int)
    for cid, amt in db.execute(select(Appointment.customer_id, func.sum(Appointment.quoted_price))
                               .where(Appointment.customer_id.in_(ids), Appointment.status == "done").group_by(Appointment.customer_id)):
        out[cid] += int(amt or 0)
    for cid, amt in db.execute(select(Invoice.customer_id, func.sum(Invoice.total)).where(Invoice.customer_id.in_(ids), Invoice.status != "void")
                               .group_by(Invoice.customer_id)):
        out[cid] += int(amt or 0)
    return out


def _placeholder(c: Customer | None) -> bool:
    """A person a journal made from a bare Tizpardaz code («مشتری تیزپرداز 7701») before the customers list came."""
    return c is not None and c.source == SOURCE and (c.full_name or "").startswith("مشتری تیزپرداز")


def _by_tp(db: Session) -> dict[str, Customer]:
    """Customers by Tizpardaz account code; a merged duplicate's code is kept as «tp:<code>» among the other codes."""
    out: dict[str, Customer] = {}
    for c in db.scalars(select(Customer).where(Customer.other_codes.is_not(None))):
        for x in c.other_codes or []:
            if str(x).startswith("tp:"):
                out.setdefault(str(x)[3:], c)
    out.update({c.tp_code: c for c in db.scalars(select(Customer).where(Customer.tp_code.is_not(None)))})
    return out


def preview_customers(db: Session, rows: list[list[object]], mapping: dict[str, int], unit: str) -> dict:
    idx = _customer_index(db)
    by_tp = _by_tp(db)
    parsed = []
    for n, r in enumerate(rows, start=1):
        raw = _text(_get(r, mapping, "name"))
        code = to_en_digits(_text(_get(r, mapping, "code"))).strip()
        if not raw or re.fullmatch(r"(جمع|جمع کل).*", raw):
            continue
        name = tp_name(raw)
        debit, credit = _money(_get(r, mapping, "debit"), unit), _money(_get(r, mapping, "credit"), unit)
        row = {"row": n, "code": code, "raw": _fa(raw), "name": name, "mobile": normalize_mobile(_text(_get(r, mapping, "mobile"))),
               "debit": debit, "credit": credit, "balance": debit - credit}
        linked = by_tp.get(code) if code else None
        matches = idx.get(person_key(name), [])
        # the code is held by a placeholder a journal made, but the real person is here too: they are the answer
        # (the placeholder is offered as well, and gets the real name so it can be merged with them afterwards)
        placeholder = linked if _placeholder(linked) and any(c.id != linked.id for c in matches) else None
        if placeholder is not None:
            linked, matches = None, [c for c in matches if c.id != placeholder.id]
        if linked is None and is_generic(name):  # «متفرقه», «صندوق» ...: not a person
            row.update(status="generic", customer_id=None, candidates=[])
        elif linked is not None:
            row.update(status="linked", customer_id=linked.id, candidates=[linked])
        elif len(matches) == 1:
            row.update(status="found", customer_id=matches[0].id, candidates=matches)
        elif len(matches) > 1:
            # several customers with this name: one already holding this person's mobile is the answer, otherwise ask
            same_mobile = [c for c in matches if row["mobile"] and c.mobile == row["mobile"]]
            row.update(status="found" if len(same_mobile) == 1 else "ambiguous", customer_id=same_mobile[0].id if len(same_mobile) == 1 else None,
                       candidates=matches)
        else:
            row.update(status="new", customer_id=None, candidates=[])
        if placeholder is not None:
            row["candidates"] = [*row["candidates"], placeholder]
        parsed.append(row)
    ids = list({c.id for p in parsed for c in p["candidates"]})
    spent = _spent(db, ids)
    sellers = _sellers(db)
    used = defaultdict(int)
    for p in parsed:
        if p.get("customer_id"):
            used[p["customer_id"]] += 1
    for p in parsed:
        # two accounts of the file found as the same customer: both balances are brought over (added up) - check it
        p["shared"] = used.get(p.get("customer_id"), 0) > 1
        p["candidates"] = [_cbrief(c, spent) for c in p["candidates"]]
        # a credit balance is what we owe: to a seller that is a debt of ours, to a customer it is prepaid credit
        p["seller"] = bool(p.get("customer_id") and p["customer_id"] in sellers)
        p["credit_as"] = "payable" if p["seller"] else "deposit"
    count = defaultdict(int)
    for p in parsed:
        count[p["status"]] += 1
    return {"rows": parsed, "summary": {"total": len(parsed), **count, "debit": sum(p["debit"] for p in parsed),
                                        "credit": sum(p["credit"] for p in parsed), "shared": sum(1 for p in parsed if p["shared"])}}


def _sellers(db: Session) -> set[int]:
    """People we buy from: linked to a supplier, or the seller of a purchase invoice brought over."""
    return {x for x in db.scalars(select(Supplier.customer_id).where(Supplier.customer_id.is_not(None)))} | \
        {x for x in db.scalars(select(TradeDoc.customer_id).where(TradeDoc.customer_id.is_not(None), TradeDoc.kind.in_(("purchase", "purchase_return"))))}


def _had_opening(db: Session, cid: int) -> bool:
    """A Tizpardaz balance was already brought over for this customer. Found through the records themselves (ledger
    lines, credits, the seller's payables), which move with the person when two records are merged."""
    from ..models import Purchase
    return bool(
        db.scalar(select(JournalLine.id).join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                  .where(JournalEntry.ref_type == "tp_opening", JournalLine.customer_id == cid).limit(1))
        or db.scalar(select(Deposit.id).where(Deposit.customer_id == cid, Deposit.source == "import", Deposit.notes.like("%تیزپرداز%")).limit(1))
        or db.scalar(select(Purchase.id).where(Purchase.supplier_id.in_(select(Supplier.id).where(Supplier.customer_id == cid)),
                                               Purchase.notes.like("%تیزپرداز%")).limit(1)))


def commit_customers(db: Session, batch: ImportBatch, choices: dict[str, int | str], *, balances: bool, at: datetime | None,
                     account_id: int | None, user=None, credit_as: dict[str, str] | None = None) -> dict:  # noqa: ANN001
    if balances:
        accounting.ensure_not_future(at, "تاریخ مانده‌ها")
    at = at or local_now()
    created = {"customers": [], "entries": [], "deposits": [], "linked": [], "purchases": [], "suppliers": [], "renamed": []}
    credit_as = credit_as or {}
    counts = defaultdict(int)
    account = db.get(PaymentAccount, account_id) if account_id else db.scalar(select(PaymentAccount).order_by(PaymentAccount.id))
    # customer -> a balance came over in an EARLIER file (asked before this file posts anything for them, so two
    # accounts of this file that are the same customer are both brought over - added up - and none is dropped)
    prior: dict[int, bool] = {}

    def real_name(x: Customer, name: str) -> None:
        if _placeholder(x) and name:  # the placeholder of a journal gets the person's name: offered for merging
            created["renamed"].append([x.id, x.full_name])
            x.full_name = name
            counts["renamed"] += 1

    for row in batch.rows:
        key = str(row["row"])
        choice = choices.get(key, row.get("customer_id") or {"ambiguous": "", "generic": "skip"}.get(row["status"], "new"))
        if choice == "skip":
            counts["skipped"] += 1
            continue
        if choice == "":  # several customers with this name and the user didn't say which one
            counts["unresolved"] += 1
            continue
        if choice == "new" or not str(choice).isdigit():
            # the code is the new person's: one held already (e.g. by a journal's placeholder) moves to them, and a
            # placeholder gets the real name, so it is offered for merging - never two people with one code
            for other in (db.scalars(select(Customer).where(Customer.tp_code == row["code"])).all() if row["code"] else []):
                created["linked"].append([other.id, other.tp_code])
                other.tp_code = None
                real_name(other, row["name"])
            c = Customer(full_name=row["name"], mobile=row["mobile"] if row["mobile"] and not accounting.find_customer(db, row["mobile"]) else None,
                         legacy_code=accounting.next_customer_code(db), source=SOURCE, tp_code=row["code"] or None)
            if not c.mobile:
                c.mobile_issue = "missing"
            db.add(c)
            db.flush()
            created["customers"].append(c.id)
            counts["new"] += 1
        else:
            c = db.get(Customer, int(choice))
            if c is None:
                raise ImportProblem(f"مشتری انتخاب‌شده برای ردیف {row['row']} پیدا نشد")
            # (a merged duplicate's code, kept as «tp:<code>», already points here)
            if row["code"] and c.tp_code != row["code"] and f"tp:{row['code']}" not in (c.other_codes or []):
                other = db.scalar(select(Customer).where(Customer.tp_code == row["code"], Customer.id != c.id))
                if other is not None:
                    created["linked"].append([other.id, other.tp_code])
                    other.tp_code = None
                    real_name(other, row["name"])
                created["linked"].append([c.id, c.tp_code])
                c.tp_code = row["code"]
            real_name(c, row["name"])
            if row["mobile"] and not c.mobile and not accounting.find_customer(db, row["mobile"]):
                c.mobile, c.mobile_issue = row["mobile"], None
            counts["linked"] += 1
        if not balances:
            continue
        if c.id not in prior:
            prior[c.id] = _had_opening(db, c.id)
        if prior[c.id] and (row["debit"] or row["credit"]):
            counts["balance_already"] += 1
            continue
        if row["debit"] > 0:  # owed by the customer: opening receivable
            e = accounting.post(db, f"ماندهٔ بدهی انتقالی از تیزپرداز - {c.full_name}", [
                accounting.Leg(accounting.account(db, accounting.AR), debit=row["debit"], customer_id=c.id),
                accounting.Leg(accounting.account(db, accounting.OPENING), credit=row["debit"], customer_id=c.id),
            ], "tp_opening", c.id, at=at)
            created["entries"].append(e.id)
            counts["debts"] += 1
            counts["debt_amount"] += row["debit"]
        if row["credit"] > 0 and credit_as.get(key, row.get("credit_as", "deposit")) == "payable":  # we owe this seller
            sup = inventory.supplier_for(db, c.full_name, c.id, created)
            pur = inventory.opening_payable(db, sup, row["credit"], at=at, notes=f"ماندهٔ بستانکار تیزپرداز (کد {row['code']}) - طلب فروشنده",
                                            user=user)
            created["purchases"].append(pur.id)
            counts["payables"] += 1
            counts["payable_amount"] += row["credit"]
        elif row["credit"] > 0:
            # the customer's credit from the PRODUCT software: a customer credit (اعتبار), not a service deposit
            # (بیعانه) - it pays anything on a next invoice and is never forfeited
            if account is None:
                raise ImportProblem("ابتدا در تنظیمات یک حساب دریافت (کارتخوان، کارت یا صندوق) تعریف کنید؛ "
                                    "بستانکاری مشتریان روی آن نگه داشته می‌شود")
            dep = Deposit(customer_id=c.id, amount=row["credit"], payment_account_id=account.id, received_at=at, source="import",
                          kind="credit", notes=f"ماندهٔ بستانکار تیزپرداز (کد {row['code']})")
            db.add(dep)
            db.flush()
            accounting.post(db, f"ماندهٔ بستانکار انتقالی از تیزپرداز - {c.full_name}", [
                accounting.Leg(accounting.account(db, accounting.OPENING), debit=dep.amount, customer_id=c.id),
                accounting.Leg(accounting.account(db, accounting.DEPOSITS), credit=dep.amount, customer_id=c.id),
            ], "deposit", dep.id, at=at)
            created["deposits"].append(dep.id)
            counts["credits"] += 1
            counts["credit_amount"] += row["credit"]
    batch.summary = {**batch.summary, "created": created, "result": dict(counts)}
    return dict(counts)


# ------------------------------------------------------------------ products
def _pbrief(p: Product) -> dict:
    return {"type": "product", "id": p.id, "code": p.code, "sku": p.sku, "name": p.name, "stock_qty": p.stock_qty}


def _sbrief(s: Service, sold: int) -> dict:
    return {"type": "service", "id": s.id, "code": s.code, "name": s.name, "line": s.line.name if s.line else None, "sold": sold}


def preview_products(db: Session, rows: list[list[object]], mapping: dict[str, int], unit: str, sku_text: str) -> dict:
    skus = parse_sku_map(sku_text)
    products = list(db.scalars(select(Product)))
    by_code = {p.code: p for p in products}
    by_sku = {(p.sku or "").lower(): p for p in products if p.sku}
    services = list(db.scalars(select(Service)))
    sold = dict(db.execute(select(Appointment.service_id, func.count(Appointment.id)).where(Appointment.status == "done", Appointment.invoice_id.is_(None))
                           .group_by(Appointment.service_id)).all())
    pkeys = [(p, name_key(p.name)) for p in products]
    skeys = [(s, name_key(s.name)) for s in services]
    # products that took their code from an earlier Tizpardaz file: that code is Tizpardaz's own (even if renamed here)
    from_tp: set[int] = set()
    for b in db.scalars(select(ImportBatch).where(ImportBatch.kind == "tp_products", ImportBatch.status == "committed")):
        cr = (b.summary or {}).get("created") or {}
        from_tp.update(int(x) for x in [*cr.get("products", []), *(cr.get("snapshots") or {})])
    parsed = []
    for n, r in enumerate(rows, start=1):
        code = to_en_digits(_text(_get(r, mapping, "code"))).strip()
        name = _fa(_get(r, mapping, "name"))
        if not code or not name or not code.isdigit():
            continue
        qty = _int(_get(r, mapping, "qty_dr")) - _int(_get(r, mapping, "qty_cr"))
        buy = _money(_get(r, mapping, "last_buy"), unit) or _money(_get(r, mapping, "buy"), unit)
        sell = _money(_get(r, mapping, "sell"), unit) or _money(_get(r, mapping, "last_sell"), unit)
        sku = _text(_get(r, mapping, "sku")) or skus.get(code)
        group = " / ".join(x for x in (_fa(_get(r, mapping, "group")), _fa(_get(r, mapping, "subgroup")), _fa(_get(r, mapping, "subgroup2"))) if x)
        k = name_key(name)
        row = {"row": n, "code": code, "tech": _text(_get(r, mapping, "tech")), "name": name, "group": group, "qty": qty, "buy": buy,
               "sell": sell, "sku": sku, "warnings": []}
        if qty < 0:
            row["warnings"].append("موجودی در تیزپرداز منفی است؛ موجودی اینجا صفر می‌ماند")
        if not sku:
            row["warnings"].append("SKU ندارد")
        cands, default = [], "new"
        linked = by_code.get(code)
        # the same code is the same product only with the same SKU or name, or when an earlier Tizpardaz file gave it
        # that code (both programs number products 1, 2, 3...)
        if linked is not None and (linked.id in from_tp or sku and (linked.sku or "").lower() == sku.lower() or name_key(linked.name) == k):
            cands, default = [_pbrief(linked)], f"p:{linked.id}"
            row["status"] = "linked"
        else:
            seen = set()
            for p in [by_sku.get((sku or "").lower())] + [p for p, pk in pkeys if pk == k] + [p for p, pk in pkeys if pk != k and (keys_alike(pk, k) or (len(pk) > 4 and (pk in k or k in pk)))]:
                if p is not None and p.id not in seen:
                    seen.add(p.id)
                    cands.append(_pbrief(p))
            for s, sk in skeys:
                if sk == k or keys_alike(sk, k) or (len(sk) > 4 and (sk in k or k in sk)):
                    cands.append(_sbrief(s, int(sold.get(s.id, 0))))
            exact = next((c for c in cands if name_key(c["name"]) == k or (c["type"] == "product" and sku and (c.get("sku") or "").lower() == sku.lower())), None)
            if exact:
                default = f"{exact['type'][0]}:{exact['id']}"
            row["status"] = "match" if exact else ("similar" if cands else "new")
        row["candidates"], row["default"] = cands, default
        parsed.append(row)
    count = defaultdict(int)
    for p in parsed:
        count[p["status"]] += 1
    return {"rows": parsed, "summary": {"total": len(parsed), **count, "units": sum(max(0, p["qty"]) for p in parsed),
                                        "value": sum(max(0, p["qty"]) * p["buy"] for p in parsed),
                                        "no_sku": sum(1 for p in parsed if not p["sku"])}}


def commit_products(db: Session, batch: ImportBatch, choices: dict[str, str], *, at: datetime | None, user=None) -> dict:  # noqa: ANN001
    accounting.ensure_not_future(at, "تاریخ موجودی")
    at = at or local_now()
    # recoded / unsku: other products whose code or website SKU this file took (given back on undo)
    created = {"products": [], "snapshots": {}, "moves": [], "history": [], "services": [], "appointments": [], "recoded": {}, "unsku": {}}
    counts = defaultdict(int)
    incoming = {r["code"] for r in batch.rows}  # Tizpardaz's codes: a product moved aside never gets one of them
    for row in batch.rows:
        choice = choices.get(str(row["row"]), row["default"])
        if choice == "skip":
            counts["skipped"] += 1
            continue
        service = None
        if choice.startswith("p:"):
            p = db.get(Product, int(choice[2:]))
            if p is None:
                raise ImportProblem(f"کالای انتخاب‌شده برای ردیف {row['row']} پیدا نشد")
            # (two rows combined with one product: the first snapshot is what it was before this file)
            created["snapshots"].setdefault(str(p.id), {k: getattr(p, k) for k in ("code", "sku", "name", "category", "sale_price",
                                                                                   "last_purchase_cost", "is_active")})
            counts["combined"] += 1
        else:
            if choice.startswith("s:"):
                service = db.get(Service, int(choice[2:]))
            p = Product(code=f"tmp-{row['row']}", name=row["name"])
            db.add(p)
            db.flush()
            created["products"].append(p.id)
            counts["new"] += 1
        # Tizpardaz is the reference: its code, name, group, prices and SKU
        other = db.scalar(select(Product).where(Product.code == row["code"], Product.id != p.id))
        if other is not None:  # a product made here took that number: it gets the next free one
            created["recoded"].setdefault(str(other.id), other.code)
            other.code = f"x{other.id}"
            db.flush()
            other.code = inventory.next_product_code(db, incoming)
            counts["codes_moved"] += 1
        if row["sku"]:
            for o in db.scalars(select(Product).where(func.lower(Product.sku) == row["sku"].lower(), Product.id != p.id)):
                created["unsku"].setdefault(str(o.id), o.sku)
                o.sku = None
        old_name = p.name
        p.code, p.name, p.sku = row["code"], row["name"], row["sku"] or p.sku
        p.category = row["group"] or p.category
        p.sale_price = row["sell"] or p.sale_price
        p.last_purchase_cost = row["buy"] or p.last_purchase_cost
        p.is_active = True
        if old_name and old_name != row["name"] and not old_name.startswith("tmp-") and choice.startswith("p:"):
            p.notes = "\n".join(x for x in (p.notes, f"نام قبلی: {old_name}") if x)
        db.flush()
        # stock: the final quantity of Tizpardaz
        target = max(0, row["qty"])
        has_moves = db.scalar(select(func.count(StockMove.id)).where(StockMove.product_id == p.id))
        # stock here on that date (a product that already has moves: what is missing to reach Tizpardaz's quantity
        # is stock that was already on the shelf - opening stock at Tizpardaz's purchase price, against the opening
        # balances, never a "found stock" profit at the old cost)
        here = int(db.scalar(select(func.coalesce(func.sum(StockMove.qty), 0))
                             .where(StockMove.product_id == p.id, StockMove.at <= at.replace(tzinfo=None))) or 0) if has_moves else 0
        if target > here:
            m = inventory.opening_stock(db, p, target - here, row["buy"], at, user=user)
            created["moves"].append(m.id)
            counts["opening_units"] += target - here
        elif target < here:  # less than here: a real shortage (costed by the method)
            m = inventory.count_stock(db, p, target, at, note="هم‌سان‌سازی با موجودی تیزپرداز", user=user)
            if m is not None:
                created["moves"].append(m.id)
        counts["units"] += target
        if service is not None:  # sold as a "service" in Chehreh: that history becomes this product's sales
            n = _service_to_product(db, service, p, batch.id, created)
            counts["chehreh_sales"] += n
    batch.summary = {**batch.summary, "created": created, "result": dict(counts)}
    return dict(counts)


def _chehreh_batches(db: Session) -> dict[int, int]:
    """Appointment -> the Chehreh file that brought it (still committed)."""
    out: dict[int, int] = {}
    for b in db.scalars(select(ImportBatch).where(ImportBatch.kind.like("legacy_%"), ImportBatch.status == "committed")):
        for aid in ((b.summary or {}).get("created") or {}).get("appointments", []):
            out[aid] = b.id
    return out


def _relink_chehreh(db: Session, bid: int, old: int, new: int) -> None:
    """An appointment moved back from a product has a new id: its Chehreh file must still know it (for its undo)."""
    b = db.get(ImportBatch, bid)
    s = (b.summary or {}) if b is not None else {}
    cr = s.get("created") or {}
    if b is None or old not in cr.get("appointments", []):
        return
    b.summary = {**s, "created": {**cr, "appointments": [new if x == old else x for x in cr["appointments"]]},
                 # a history file's rows each made an appointment (the record its re-import check follows)
                 **({"fp_rec": [new if x == old else x for x in s["fp_rec"]]} if "fp_rec" in s and b.kind == "legacy_history" else {})}


def _service_to_product(db: Session, service: Service, product: Product, batch_id: int, created: dict) -> int:
    n = 0
    source = _chehreh_batches(db)
    for a in list(db.scalars(select(Appointment).where(Appointment.service_id == service.id, Appointment.status == "done",
                                                       Appointment.invoice_id.is_(None)))):
        h = TradeHistory(batch_id=batch_id, source="chehreh", kind="sale", at=a.start_at, product_id=product.id, customer_id=a.customer_id,
                         description=service.name, qty=1, unit_price=int(a.quoted_price or 0), amount=int(a.quoted_price or 0),
                         fp=f"chehreh|appt|{a.id}")
        db.add(h)
        created["appointments"].append({"id": a.id, "customer_id": a.customer_id, "service_id": a.service_id, "staff_id": a.staff_id,
                                        "start_at": a.start_at.isoformat(), "quoted_price": a.quoted_price, "notes": a.notes,
                                        "status": a.status, "duration_minutes": a.duration_minutes, "batch": source.get(a.id)})
        db.delete(a)
        n += 1
    db.flush()
    created["history"] += [h.id for h in db.scalars(select(TradeHistory).where(TradeHistory.batch_id == batch_id, TradeHistory.source == "chehreh",
                                                                             TradeHistory.product_id == product.id))]
    if not db.scalar(select(func.count(InvoiceItem.id)).where(InvoiceItem.service_id == service.id)):
        service.is_active = False  # now a product; kept (archived) only for its code / old records
        created["services"].append(service.id)
    backfill_docs(db)  # each moved receipt is an invoice in the customer's file
    recost_history(db, [product.id])
    return n


# ------------------------------------------------------------------ journal
def _doc_kind(text: str) -> str | None:
    t = _fa(text)
    if "فروش" in t:
        return "sale_return" if "برگشت" in t else "sale"
    if "خرید" in t:
        return "purchase_return" if "برگشت" in t else "purchase"
    if "هزینه" in t:
        return "expense"
    return None


def _split_desc(desc: str, kind: str) -> tuple[str, str]:
    """«آبرسان لوشن ...  به  تاجيک(سپيده)» -> (product, party). Sales say «به», purchases «از»."""
    d = _fa(desc)
    seps = [" به ", " از "] if kind in ("sale", "purchase_return") else [" از ", " به "]
    for sep in seps:
        i = d.rfind(sep)
        if i > 0:
            return d[:i].strip(), d[i + len(sep):].strip()
    return d, ""


def _product_index(db: Session) -> tuple[dict[str, int], list[tuple[str, int, str]]]:
    exact, all_ = {}, []
    for p in db.scalars(select(Product)):
        k = name_key(p.name)
        exact.setdefault(k, p.id)
        all_.append((k, p.id, p.name))
    return exact, all_


def _party_customer(db: Session, party: str, idx: dict[str, list[Customer]], by_tp: dict[str, Customer]) -> tuple[int | None, list]:
    """A party written as a Tizpardaz code (digits) or as «نام‌خانوادگی(نام)»."""
    code = to_en_digits(party).strip()
    if code.isdigit():
        c = by_tp.get(code)
        return (c.id if c else None), ([c] if c else [])
    if is_generic(tp_name(party)):
        return None, []
    matches = idx.get(person_key(tp_name(party)), [])
    if len(matches) == 1:
        return matches[0].id, matches
    linked = [c for c in matches if c.tp_code]  # several namesakes: the one linked to Tizpardaz is the one
    if len(linked) == 1:
        return linked[0].id, matches
    return None, matches


DOC_KINDS = ("sale", "sale_return", "purchase", "purchase_return")
KIND_FA = {"sale": "فاکتور فروش", "sale_return": "برگشت از فروش", "purchase": "فاکتور خرید", "purchase_return": "برگشت از خرید"}
# on the person's row of an invoice: (the side holding the invoice amount, the side holding what was settled on it)
PARTY_SIDES = {"sale": ("debit", "credit"), "sale_return": ("credit", "debit"),
               "purchase": ("credit", "debit"), "purchase_return": ("debit", "credit")}


# words of accounting rows that are never the person of an invoice
ACCOUNT_WORDS = ("کالا", "صندوق", "بانک", "تخفیف", "مالیات", "ارزش افزوده", "عوارض", "حمل", "فروش", "خرید", "موجودی", "تنخواه",
                 "چک", "کارتخوان", "درآمد", "هزینه", "برگشت", "گرد")


def doc_key(source: str, kind: str, doc_no: str, day: str, fallback: str = "") -> str:
    return f"{source}|{kind}|{doc_no or fallback}|{day}"[:200]


def _same_party(account: str, code: str, party: str) -> bool:
    if not party:
        return False
    p = to_en_digits(party).strip()
    if p.isdigit():
        return p == (code or "") or p == to_en_digits(account).strip()
    return bool(account) and (account == party or person_key(tp_name(account)) == person_key(tp_name(party)) != "")


def party_name(party: str, customer: Customer | None = None) -> str:
    """How the person of an invoice is shown: the customer's name, else «نام نام‌خانوادگی» from the Tizpardaz form."""
    return customer.full_name if customer else (tp_name(party) if party else "")


def _supplier_for(db: Session, name: str, customer_id: int | None, created: dict | None = None) -> Supplier | None:
    return inventory.supplier_for(db, name, customer_id, created)


def _build_docs(parsed: list[dict], others: dict[str, list[dict]]) -> dict[str, dict]:
    """Group the item rows into invoices and read each invoice's person row: its amount is the invoice total (after
    the invoice's discount / charges), the other side is what was settled on it right away."""
    docs: dict[str, dict] = {}
    for row in parsed:
        if row["kind"] not in DOC_KINDS:
            continue
        d = docs.setdefault(row["doc_key"], {"key": row["doc_key"], "kind": row["kind"], "doc_no": row["doc_no"], "date": row["date"],
                                             "parties": defaultdict(int), "items_total": 0, "lines": 0})
        d["items_total"] += row["amount"]
        d["lines"] += 1
        if row.get("party"):
            d["parties"][row["party"]] += 1
    for k, d in docs.items():
        rows_o = others.get(k, [])
        party = max(d["parties"], key=d["parties"].get) if d["parties"] else ""
        side, settle = PARTY_SIDES[d["kind"]]
        mine = [o for o in rows_o if _same_party(o["account"], o["code"], party)]
        if not mine and not party:  # the person is only on its own row («تاجیک(ساناز)», or a company name)
            names = {o["account"] for o in rows_o if "(" in o["account"] and o[side] > 0}
            if not names:  # not an accounting row (cash, bank, discount, tax...): the person
                names = {o["account"] for o in rows_o if o[side] > 0 and o["account"] and not any(w in o["account"] for w in ACCOUNT_WORDS)}
            if len(names) == 1:
                party = names.pop()
                mine = [o for o in rows_o if o["account"] == party]
        d.pop("parties")
        d.update(party=party, found=bool(mine), total=sum(o[side] for o in mine) or d["items_total"], paid=sum(o[settle] for o in mine),
                 extras=[{"account": o["account"], "amount": o["debit"] - o["credit"], "description": o["desc"]} for o in rows_o if o not in mine])
    return docs


def preview_journal(db: Session, rows: list[list[object]], mapping: dict[str, int], unit: str) -> dict:
    p_exact, p_all = _product_index(db)
    idx = _customer_index(db)
    by_tp = _by_tp(db)
    done = set(db.scalars(select(TradeHistory.fp).where(TradeHistory.source == SOURCE)))
    parsed, ignored = [], defaultdict(int)
    others: dict[str, list[dict]] = defaultdict(list)  # the person / discount / cash rows of each invoice
    unknown_p: dict[str, dict] = {}
    unknown_c: dict[str, dict] = {}
    seen: dict[str, int] = defaultdict(int)
    for n, r in enumerate(rows, start=1):
        doc_type = _fa(_get(r, mapping, "doc_type"))
        if not doc_type:
            continue
        kind = _doc_kind(doc_type)
        d, _t = parse_date(_get(r, mapping, "date"))
        qty = _int(_get(r, mapping, "qty"))
        price = _money(_get(r, mapping, "price"), unit)
        debit, credit = _money(_get(r, mapping, "debit"), unit), _money(_get(r, mapping, "credit"), unit)
        desc = _fa(_get(r, mapping, "desc"))
        account = _fa(_get(r, mapping, "account"))
        doc_no = _text(_get(r, mapping, "doc_no"))
        if kind is None or d is None:
            ignored[doc_type or "بدون نوع"] += 1
            continue
        if kind == "expense":
            amount = debit
            if amount <= 0:
                ignored[f"{doc_type} (طرف پرداخت)"] += 1  # the cash side of the expense document
                continue
            row = {"row": n, "kind": kind, "date": d.isoformat(), "doc_no": doc_no, "account": account,
                   "description": desc, "amount": amount, "qty": 0, "unit_price": 0}
        else:
            key = doc_key(SOURCE, kind, doc_no, d.isoformat(), fallback=f"r{n}")
            if qty <= 0:  # the person's row, a discount, cash...: part of the invoice's header
                others[key].append({"account": account, "code": to_en_digits(_text(_get(r, mapping, "account_code"))).strip(),
                                    "debit": debit, "credit": credit, "desc": desc, "row": n, "type": doc_type})
                continue
            amount = (credit if kind in ("sale", "purchase_return") else debit) or qty * price
            product, party = _split_desc(desc, kind)
            pid = p_exact.get(name_key(product))
            row = {"row": n, "kind": kind, "date": d.isoformat(), "doc_no": doc_no, "doc_key": key, "account": account,
                   "description": desc, "product_name": product, "product_id": pid, "party": party, "qty": qty,
                   "unit_price": price or (amount // qty if qty else 0), "amount": amount, "customer_id": None}
            if pid is None:
                u = unknown_p.setdefault(product, {"name": product, "count": 0, "similar": []})
                u["count"] += 1
                if not u["similar"]:
                    k = name_key(product)
                    u["similar"] = [{"id": i, "name": nm} for kk, i, nm in p_all if keys_alike(kk, k) or (len(kk) > 4 and (kk in k or k in kk))][:3]
        base = "|".join(str(x) for x in (row["kind"], row["doc_no"], row["date"], row["description"], row["qty"], row["amount"]))
        seen[base] += 1
        row["fp"] = f"{base}|{seen[base]}"[:160]
        row["duplicate"] = row["fp"] in done
        parsed.append(row)
    docs = _build_docs(parsed, others)
    for k, rows_o in others.items():
        if k not in docs:
            for o in rows_o:
                ignored[f"{o['type']} (ردیف بدون کالا)"] += 1
    # the person of each invoice (buyer of a sale, seller of a purchase): a customer here, by Tizpardaz code or name
    existing = {k for chunk in _chunks(list(docs), 500) for k in db.scalars(select(TradeDoc.key).where(TradeDoc.key.in_(chunk)))}
    for d in docs.values():
        cid, cands = _party_customer(db, d["party"], idx, by_tp) if d["party"] else (None, [])
        d["customer_id"], d["exists"] = cid, d["key"] in existing
        if d["party"] and cid is None:
            u = unknown_c.setdefault(d["party"], {"party": d["party"], "name": tp_name(d["party"]), "count": 0, "seller": False,
                                                  "generic": is_generic(tp_name(d["party"])),
                                                  "candidates": [{"id": c.id, "full_name": c.full_name, "code": c.legacy_code, "mobile": c.mobile} for c in cands]})
            u["count"] += 1
            u["seller"] = u["seller"] or d["kind"].startswith("purchase")
    for row in parsed:
        if row.get("doc_key") in docs:
            d = docs[row["doc_key"]]
            row["party"] = row.get("party") or d["party"]
            row["customer_id"] = d["customer_id"]
    fresh = [p for p in parsed if not p["duplicate"]]
    tot = defaultdict(int)
    cnt = defaultdict(int)
    for p in fresh:
        tot[p["kind"]] += p["amount"]
        cnt[p["kind"]] += 1
    doc_counts = defaultdict(int)
    for d in docs.values():
        doc_counts[d["kind"]] += 1
    dates = sorted(p["date"] for p in fresh)
    return {"rows": parsed, "summary": {"total": len(parsed), "new": len(fresh), "duplicates": len(parsed) - len(fresh), "counts": dict(cnt),
                                        "amounts": dict(tot), "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
                                        "ignored": dict(ignored), "doc_counts": dict(doc_counts),
                                        "docs_existing": sum(1 for d in docs.values() if d["exists"])},
            "docs": list(docs.values()),
            "unknown_products": sorted(unknown_p.values(), key=lambda x: -x["count"]),
            "unknown_customers": sorted(unknown_c.values(), key=lambda x: -x["count"])}


def _chunks(items: list, n: int):  # noqa: ANN202
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _new_party_customer(db: Session, party: str, created: dict) -> Customer:
    code = to_en_digits(party).strip()
    c = Customer(full_name=tp_name(party) if not code.isdigit() else f"مشتری تیزپرداز {code}", source=SOURCE,
                 legacy_code=accounting.next_customer_code(db), mobile_issue="missing", tp_code=code if code.isdigit() else None)
    db.add(c)
    db.flush()
    created["customers"].append(c.id)
    return c


def commit_journal(db: Session, batch: ImportBatch, product_map: dict[str, int | str], customer_map: dict[str, int | str], user=None) -> dict:  # noqa: ANN001
    created = {"history": [], "customers": [], "docs": [], "suppliers": []}
    counts = defaultdict(int)
    new_customers: dict[str, int] = {}
    done = set(db.scalars(select(TradeHistory.fp).where(TradeHistory.source == SOURCE)))
    metas = {d["key"]: d for d in (batch.summary or {}).get("docs", [])}
    docs: dict[str, TradeDoc] = {}

    def person(party: str, preset: int | None) -> int | None:
        if preset or not party:
            return preset
        # a written-out person («نام‌خانوادگی(نام)» or a code) becomes a customer; a word like «متفرقه» stays a name
        is_person = ("(" in party or to_en_digits(party).strip().isdigit()) and not is_generic(tp_name(party))
        choice = customer_map.get(party, "new" if is_person else "skip")
        if isinstance(choice, int) or str(choice).isdigit():
            return int(choice)
        if choice == "new":
            if party not in new_customers:
                new_customers[party] = _new_party_customer(db, party, created).id
            return new_customers[party]
        return None

    def doc_for(row: dict) -> TradeDoc | None:
        key = row.get("doc_key")
        if not key or row["kind"] not in DOC_KINDS:
            return None
        if key in docs:
            return docs[key]
        meta = metas.get(key) or {"party": row.get("party") or "", "customer_id": row.get("customer_id"), "found": False}
        doc = db.scalar(select(TradeDoc).where(TradeDoc.key == key))
        if doc is None:
            doc = TradeDoc(key=key, batch_id=batch.id, source=SOURCE, kind=row["kind"], doc_no=row["doc_no"][:32],
                           at=datetime.combine(datetime.fromisoformat(row["date"]).date(), time(12, 0)), extras=[])
            db.add(doc)
            db.flush()
            created["docs"].append(doc.id)
            counts["docs"] += 1
        doc.party = (meta.get("party") or doc.party or "")[:128]
        doc.customer_id = doc.customer_id or person(doc.party, meta.get("customer_id"))
        if meta.get("found"):  # the invoice's own amount (after its discount / charges) and what was settled on it
            doc.total, doc.paid, doc.extras = meta["total"], meta["paid"], meta["extras"]
        if doc.kind.startswith("purchase") and not doc.supplier_id:
            cust = db.get(Customer, doc.customer_id) if doc.customer_id else None
            sup = _supplier_for(db, party_name(doc.party, cust), doc.customer_id, created)
            doc.supplier_id = sup.id if sup else None
        docs[key] = doc
        return doc

    for row in batch.rows:
        doc = doc_for(row)
        if row["fp"] in done:
            counts["duplicates"] += 1
            if doc is not None:  # brought over before invoices were kept: attach it to its invoice now
                for h in db.scalars(select(TradeHistory).where(TradeHistory.fp == row["fp"], TradeHistory.source == SOURCE)):
                    h.doc_id = h.doc_id or doc.id
                    h.customer_id = h.customer_id or doc.customer_id
            continue
        pid = row.get("product_id")
        if row["kind"] != "expense" and pid is None:
            choice = product_map.get(row.get("product_name", ""), "skip")
            pid = int(choice) if isinstance(choice, int) or str(choice).isdigit() else None
        cid = doc.customer_id if doc is not None else row.get("customer_id")
        d = datetime.combine(datetime.fromisoformat(row["date"]).date(), time(12, 0))
        h = TradeHistory(batch_id=batch.id, source=SOURCE, kind=row["kind"], at=d, doc_no=row["doc_no"][:32], product_id=pid, customer_id=cid,
                         party=(row.get("party") or "")[:128], account=(row.get("account") or "")[:128], description=row["description"][:256],
                         qty=row["qty"], unit_price=row["unit_price"], amount=row["amount"], fp=row["fp"], doc_id=doc.id if doc else None)
        db.add(h)
        done.add(row["fp"])
        counts[row["kind"]] += 1
        if pid is None and row["kind"] != "expense":
            counts["without_product"] += 1
    db.flush()
    for doc in docs.values():
        _refresh_doc(db, doc)
    created["history"] = list(db.scalars(select(TradeHistory.id).where(TradeHistory.batch_id == batch.id)))
    products = set(db.scalars(select(TradeHistory.product_id).where(TradeHistory.batch_id == batch.id, TradeHistory.product_id.is_not(None))))
    recost_history(db, products)
    batch.summary = {**batch.summary, "created": created, "result": dict(counts)}
    return dict(counts)


def _net(h: TradeHistory) -> int:
    """What the item really sold / was bought for: after its share of the invoice's discount (or charges)."""
    return int(h.net_amount if h.net_amount is not None else h.amount or 0)


def _refresh_doc(db: Session, doc: TradeDoc) -> None:
    """Sum of the invoice's items; without its own amount from the file, the invoice total is that sum. The invoice's
    discount (or its charges) is spread over its items (largest remainder, to the Rial): each item's net amount is
    what it really sold / cost for, so the reports agree with the invoice total."""
    lines = list(db.scalars(select(TradeHistory).where(TradeHistory.doc_id == doc.id).order_by(TradeHistory.id)))
    items = sum(int(h.amount or 0) for h in lines)
    if not doc.total or doc.total == doc.items_total:
        doc.total = items
    doc.items_total = items
    diff = doc.total - items
    for h, part in zip(lines, inventory._share(abs(diff), [int(h.amount or 0) for h in lines])):
        h.net_amount = int(h.amount or 0) + (part if diff > 0 else -part)


def seller_credits(db: Session) -> list[dict]:
    """Credit balances brought over as a customer's open deposit although the person is a seller (we owe them)."""
    sellers = _sellers(db)
    out = []
    for d in db.scalars(select(Deposit).where(Deposit.status == "held", Deposit.source == "import", Deposit.notes.like("%تیزپرداز%"),
                                              Deposit.customer_id.in_(sellers or {-1})).order_by(Deposit.id)):
        c = db.get(Customer, d.customer_id)
        n = db.scalar(select(func.count(TradeDoc.id)).where(TradeDoc.customer_id == c.id, TradeDoc.kind.in_(("purchase", "purchase_return"))))
        out.append({"deposit_id": d.id, "customer_id": c.id, "name": c.full_name, "code": c.legacy_code, "amount": d.amount,
                    "received_at": d.received_at.isoformat(timespec="minutes"), "purchases": int(n or 0), "notes": d.notes})
    return out


def credit_to_payable(db: Session, deposit_ids: list[int], user=None) -> dict:  # noqa: ANN001
    """Turn those deposits into what we owe the seller (an opening payable). Only deposits that were never used and
    whose only entry is the opening one (no money moved) are changed."""
    out = defaultdict(int)
    for did in deposit_ids:
        d = db.get(Deposit, did)
        if d is None or d.status != "held" or d.source != "import" or "تیزپرداز" not in (d.notes or ""):
            out["skipped"] += 1
            continue
        entries = list(db.scalars(select(JournalEntry).where(JournalEntry.ref_type == "deposit", JournalEntry.ref_id == d.id)))
        opening = accounting.account(db, accounting.OPENING).id
        if not entries or any(all(ln.account_id != opening for ln in e.lines) for e in entries):
            out["skipped"] += 1  # money really moved on it: leave it to the user
            continue
        c = db.get(Customer, d.customer_id)
        for e in entries:
            db.delete(e)
        # the customers file that brought the credit: its undo takes back the payable (and a seller made for it) instead
        batch = next((b for b in db.scalars(select(ImportBatch).where(ImportBatch.kind == "tp_customers", ImportBatch.status == "committed"))
                      if did in ((b.summary or {}).get("created") or {}).get("deposits", [])), None)
        made: dict = {}
        sup = inventory.supplier_for(db, c.full_name, c.id, made)
        pur = inventory.opening_payable(db, sup, d.amount, at=d.received_at, notes=f"{d.notes} - طلب فروشنده", user=user)
        if batch is not None:
            cr = batch.summary.get("created") or {}
            batch.summary = {**batch.summary, "created": {**cr, "purchases": [*cr.get("purchases", []), pur.id],
                                                          "suppliers": [*cr.get("suppliers", []), *made.get("suppliers", [])]}}
        db.delete(d)
        db.flush()
        from .audit import audit
        audit(db, "tizpardaz.credit_to_payable", "purchase", pur.id, {"deposit": did, "customer": c.id, "amount": pur.total}, user=user)
        out["converted"] += 1
        out["amount"] += pur.total
    return dict(out)


def doc_out(db: Session, doc: TradeDoc, full: bool = False, people: dict | None = None) -> dict:
    """An invoice brought over, as shown in the person's file and the purchases list (with its items when full)."""
    cust = (people or {}).get(doc.customer_id) if people is not None else (db.get(Customer, doc.customer_id) if doc.customer_id else None)
    sup = db.get(Supplier, doc.supplier_id) if doc.supplier_id else None
    out = {"id": doc.id, "kind": doc.kind, "kind_label": KIND_FA.get(doc.kind, doc.kind), "doc_no": doc.doc_no, "source": doc.source,
           "at": doc.at.isoformat(timespec="minutes"), "party": doc.party, "person": party_name(doc.party, cust) or (sup.name if sup else ""),
           "customer_id": doc.customer_id, "customer_code": cust.legacy_code if cust else None, "supplier_id": doc.supplier_id,
           "supplier": sup.name if sup else None, "items_total": doc.items_total, "total": doc.total, "paid": doc.paid,
           # what the invoice took off (discount) or added (shipping, tax...) compared with its items
           "discount": max(0, doc.items_total - doc.total), "additions": max(0, doc.total - doc.items_total)}
    if full:
        lines = list(db.scalars(select(TradeHistory).where(TradeHistory.doc_id == doc.id).order_by(TradeHistory.id)))
        products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_({h.product_id for h in lines if h.product_id} or {-1})))}
        sale = doc.kind in ("sale", "sale_return")
        out["items"] = [{"id": h.id, "product_id": h.product_id, "code": products[h.product_id].code if h.product_id in products else None,
                         "name": products[h.product_id].name if h.product_id in products else (h.description or "—"),
                         "description": h.description, "qty": h.qty, "unit_price": h.unit_price, "amount": h.amount,
                         "net_amount": _net(h),  # after its share of the invoice's discount
                         "cost": h.cost if sale else None, "profit": _net(h) - (h.cost or 0) if sale and h.cost is not None else None}
                        for h in lines]
        out["extras"] = doc.extras or []
        out["lines"] = len(lines)
    return out


def backfill_docs(db: Session) -> int:
    """Rows brought over before invoices were kept: group them into their invoices (number + date), and link the
    seller of each purchase to the person (customer) and supplier. Runs at start; does nothing once done."""
    # items brought over before the invoices' discounts were spread over them: their net amounts (and the costs of
    # the sales, worked out from the purchases' net cost)
    stale = list(db.scalars(select(TradeDoc).where(TradeDoc.id.in_(
        select(TradeHistory.doc_id).where(TradeHistory.doc_id.is_not(None), TradeHistory.net_amount.is_(None))))))
    for doc in stale:
        _refresh_doc(db, doc)
    if stale:
        db.flush()
        recost_history(db, set(db.scalars(select(TradeHistory.product_id).where(
            TradeHistory.doc_id.in_([d.id for d in stale if d.total != d.items_total]), TradeHistory.product_id.is_not(None)))))
    rows = list(db.scalars(select(TradeHistory).where(TradeHistory.doc_id.is_(None), TradeHistory.kind.in_(DOC_KINDS))
                           .order_by(TradeHistory.id)))
    if not rows:
        return 0
    idx = _customer_index(db)
    by_tp = _by_tp(db)
    groups: dict[str, list[TradeHistory]] = defaultdict(list)
    for h in rows:
        groups[doc_key(h.source, h.kind, h.doc_no, h.at.date().isoformat(), fallback=f"h{h.id}")].append(h)
    n = 0
    for key, lines in groups.items():
        first = lines[0]
        doc = db.scalar(select(TradeDoc).where(TradeDoc.key == key))
        if doc is None:
            doc = TradeDoc(key=key, batch_id=first.batch_id, source=first.source, kind=first.kind, doc_no=first.doc_no, at=first.at,
                           party=first.party, total=0, items_total=0, paid=0, extras=[])
            db.add(doc)
            db.flush()
            n += 1
        cid = next((h.customer_id for h in lines if h.customer_id), None)
        if cid is None and first.party:
            cid, _ = _party_customer(db, first.party, idx, by_tp)
        doc.customer_id = doc.customer_id or cid
        for h in lines:
            h.doc_id = doc.id
            h.customer_id = h.customer_id or doc.customer_id
        if doc.kind.startswith("purchase") and not doc.supplier_id and (doc.party or doc.customer_id):
            cust = db.get(Customer, doc.customer_id) if doc.customer_id else None
            sup = _supplier_for(db, party_name(doc.party, cust), doc.customer_id)
            doc.supplier_id = sup.id if sup else None
        db.flush()
        _refresh_doc(db, doc)
    db.flush()
    return n


def recost_history(db: Session, product_ids) -> None:  # noqa: ANN001
    """Cost of each historical sale with the chosen costing method, from the historical purchases. Stock that
    was already on the shelf before the history starts is valued at the product's purchase price."""
    how = inventory.method(db)
    for pid in product_ids:
        p = db.get(Product, pid)
        hist = list(db.scalars(select(TradeHistory).where(TradeHistory.product_id == pid, TradeHistory.kind != "expense")
                               .order_by(TradeHistory.at, TradeHistory.id)))
        if not hist:
            continue
        sign = {"purchase": 1, "sale_return": 1, "sale": -1, "purchase_return": -1}
        run, low = 0, 0
        for h in hist:
            run += sign[h.kind] * h.qty
            low = min(low, run)
        moves = []
        unit = p.last_purchase_cost or next((_net(h) // h.qty for h in hist if h.kind == "purchase" and h.qty), 0)
        if low < 0:  # sold before any purchase in the history: there was stock at the start
            moves.append(SimpleNamespace(id=-1, at=hist[0].at, kind="opening", qty=-low, cost=-low * unit, ref_move_id=None))
        kinds = {"purchase": "purchase", "sale": "sale", "sale_return": "adjust_in", "purchase_return": "adjust_out"}
        for h in hist:  # a purchase costs what was paid for it: after its share of the invoice's discount
            moves.append(SimpleNamespace(id=h.id, at=h.at, kind=kinds[h.kind], qty=sign[h.kind] * h.qty,
                                         cost=_net(h) if h.kind == "purchase" else 0, ref_move_id=None))
        r = inventory.replay(moves, how)
        for h in hist:
            if h.kind in ("sale", "sale_return"):
                h.cost = r["costs"][h.id][0]
    db.flush()


# ------------------------------------------------------------------ batches
def preview(db: Session, filename: str, data: bytes, kind: str, unit: str = "toman", sku_text: str = "") -> ImportBatch:
    if kind not in KINDS:
        raise ImportProblem("نوع فایل نامعتبر است")
    rows, mapping = _table(filename, data, kind)
    if kind == "customers":
        result = preview_customers(db, rows, mapping, unit)
    elif kind == "products":
        result = preview_products(db, rows, mapping, unit, sku_text or sku_map_text(db))
    else:
        result = preview_journal(db, rows, mapping, unit)
    if not result["rows"]:
        raise ImportProblem("ردیف قابل استفاده‌ای در فایل پیدا نشد")
    rows_out = result.pop("rows")
    batch = ImportBatch(kind=f"tp_{kind}", file_name=filename[:250], status="review", provider=SOURCE, rows=rows_out,
                        summary={"kind": kind, "kind_label": KINDS[kind], "unit": unit, "mapping": mapping, **result})
    db.add(batch)
    db.flush()
    return batch


def _opening_left(db: Session, cid: int) -> int:
    """What the customer still owes beyond their unpaid invoices here: what is left of their opening debts."""
    from ..models import Invoice
    unpaid = db.scalar(select(func.coalesce(func.sum(Invoice.total - Invoice.paid), 0))
                       .where(Invoice.customer_id == cid, Invoice.status != "void")) or 0
    return accounting.customer_balance(db, cid)["receivable"] - int(unpaid)


def _restore_codes(db: Session, want: dict[int, str]) -> int:
    """Products get their old codes back; a code that another product holds now stays with it (and this product keeps
    its current code). Returns how many could not be given back."""
    todo = []
    for pid, code in want.items():
        p = db.get(Product, pid)
        if p is not None and code and p.code != code:
            todo.append((p, p.code, code))
    for p, _, _ in todo:  # free their codes first, so two of them can swap
        p.code = f"x{p.id}"
    db.flush()
    kept = 0
    for p, _, code in todo:
        if db.scalar(select(Product.id).where(Product.code == code).limit(1)) is None:
            p.code = code
            db.flush()
        else:
            kept += 1
    for p, now, _ in todo:
        if p.code == f"x{p.id}":
            p.code = now if db.scalar(select(Product.id).where(Product.code == now).limit(1)) is None else inventory.next_product_code(db)
            db.flush()
    return kept


def undo(db: Session, batch: ImportBatch) -> dict:
    """Take back what a committed file added. What was used since stays, with what used it (counted as *_kept): a
    debt (partly) received, a credit used on an invoice, stock sold or counted after it, a payable paid, a product
    sold or in a later file, a customer with other records."""
    if batch.status != "committed":
        raise ImportProblem("فقط فایل‌های ثبت‌شده قابل برگشت هستند")
    c = (batch.summary or {}).get("created", {})
    out = defaultdict(int)
    docs_left, hist_products = set(), set()  # invoices that keep items of other files, products whose history changes
    owner: dict[str, int | None] = {}  # Chehreh sales moved to a product -> whose they are now (a merge moves them)
    for hid in c.get("history", []):
        h = db.get(TradeHistory, hid)
        if h:
            if h.source == "chehreh":
                owner[h.fp] = h.customer_id
            docs_left.add(h.doc_id)
            hist_products.add(h.product_id)
            db.delete(h)
            out["history"] += 1
    db.flush()
    for doc in db.scalars(select(TradeDoc).where(or_(TradeDoc.id.in_(c.get("docs") or [-1]), TradeDoc.batch_id == batch.id))):
        if not db.scalar(select(TradeHistory.id).where(TradeHistory.doc_id == doc.id).limit(1)):  # no item left
            db.delete(doc)
            out["docs"] += 1
    db.flush()
    from ..models import Purchase, SupplierPayment
    for pid in c.get("purchases", []):  # sellers' balances, unless something was already paid on them
        pur = db.get(Purchase, pid)
        if pur is None:
            continue
        if pur.paid or pur.items or db.scalar(select(SupplierPayment.id).where(SupplierPayment.purchase_id == pid).limit(1)):
            out["payables_kept"] += 1
            continue
        for e in db.scalars(select(JournalEntry).where(JournalEntry.ref_type.in_(("purchase", "purchase_void")), JournalEntry.ref_id == pid)):
            db.delete(e)
        db.delete(pur)
        out["payables"] += 1
    db.flush()
    for sid in c.get("suppliers", []):
        sup = db.get(Supplier, sid)
        if sup and not db.scalar(select(TradeDoc.id).where(TradeDoc.supplier_id == sid).limit(1)) \
                and not db.scalar(select(Purchase.id).where(Purchase.supplier_id == sid).limit(1)):
            db.delete(sup)
    for a in c.get("appointments", []):  # Chehreh history that had moved to a product goes back to its service
        src = db.get(ImportBatch, a["batch"]) if a.get("batch") else None
        if src is not None and src.status != "committed":  # its Chehreh file was taken back meanwhile: it stays gone
            continue
        cid = owner.get(f"chehreh|appt|{a['id']}", a["customer_id"])
        if cid is None or db.get(Customer, cid) is None:  # nobody to give it back to
            continue
        appt = Appointment(customer_id=cid, service_id=a["service_id"] if db.get(Service, a["service_id"] or 0) else None,
                           staff_id=a["staff_id"] if db.get(Staff, a["staff_id"] or 0) else None,
                           start_at=datetime.fromisoformat(a["start_at"]), quoted_price=a["quoted_price"], notes=a["notes"],
                           status=a["status"], duration_minutes=a["duration_minutes"])
        db.add(appt)
        db.flush()
        if src is not None:
            _relink_chehreh(db, src.id, a["id"], appt.id)
    for sid in c.get("services", []):
        s = db.get(Service, sid)
        if s:
            s.is_active = True
    db.flush()
    mine = set(c.get("moves", []))
    for mid in c.get("moves", []):
        m = db.get(StockMove, mid)
        if m is None:
            continue
        # stock sold, counted or bought after it (or a later file): it stays, or the stock and its value would go wrong
        if db.scalar(select(StockMove.id).where(StockMove.product_id == m.product_id, StockMove.id > m.id,
                                                StockMove.id.not_in(mine)).limit(1)) is not None:
            out["moves_kept"] += 1
            continue
        for e in db.scalars(select(JournalEntry).where(JournalEntry.ref_type.in_(("stock_move", "stock_opening")), JournalEntry.ref_id == m.id)):
            db.delete(e)
        pid, kind, qty = m.product_id, m.kind, m.qty
        db.delete(m)
        db.flush()
        inventory.recost(db, pid)
        p = db.get(Product, pid)
        if kind == "opening":  # the website got this stock: it is taken back there too
            woo_queue.add(db, pid, -qty, f"برگشت انتقال تیزپرداز - {p.name}")
        else:
            woo_queue.set_to_stock(db, pid, f"برگشت انتقال تیزپرداز - {p.name}")
        out["moves"] += 1
    db.flush()
    # products this file made (before the old codes are given back: one of them may hold such a code)
    for pid in c.get("products", []):
        p = db.get(Product, pid)
        if p is None:
            continue
        used = any(db.scalar(select(m.id).where(m.product_id == pid).limit(1)) is not None
                   for m in (StockMove, InvoiceItem, PurchaseItem, TradeHistory))
        sent = db.scalar(select(WooOutbox.id).where(WooOutbox.product_id == pid, WooOutbox.status != "pending").limit(1)) is not None
        if not used and not sent:  # nothing of it reached the website: what waits to be sent goes, its site link is let go
            for o in db.scalars(select(WooOutbox).where(WooOutbox.product_id == pid)):
                db.delete(o)
            db.execute(update(WooProduct).where(WooProduct.product_id == pid).values(product_id=None)
                       .execution_options(synchronize_session=False))
            db.flush()
        if inventory.product_in_use(db, pid):  # sold, in a later file, or the website already got its stock: archived
            if not used:
                p.is_active = False
            out["products_kept"] += 1
            continue
        db.delete(p)
        out["products"] += 1
    db.flush()
    snaps = c.get("snapshots") or {}
    for pid, snap in snaps.items():  # combined products: back to what they were (their code below)
        p = db.get(Product, int(pid))
        if p:
            for k, v in snap.items():
                if k != "code":
                    setattr(p, k, v)
    db.flush()
    for pid, sku in (c.get("unsku") or {}).items():  # the website SKU this file took from another product
        p = db.get(Product, int(pid))
        if p is not None and sku and not p.sku and pid not in snaps \
                and db.scalar(select(Product.id).where(func.lower(Product.sku) == sku.lower()).limit(1)) is None:
            p.sku = sku
    codes_kept = _restore_codes(db, {**{int(k): v for k, v in (c.get("recoded") or {}).items()},
                                     **{int(k): s.get("code") for k, s in snaps.items()}})
    if codes_kept:
        out["codes_kept"] = codes_kept
    # opening debts: what was received on them since stays (with the debt), only what is still owed is taken back
    ar = accounting.account(db, accounting.AR).id
    left: dict[int, int] = {}
    for eid in c.get("entries", []):
        e = db.get(JournalEntry, eid)
        if e is None:
            continue
        ln = next((x for x in e.lines if x.account_id == ar and x.debit > 0), None)
        if ln is not None and ln.customer_id is not None:
            if ln.customer_id not in left:
                left[ln.customer_id] = _opening_left(db, ln.customer_id)
            if ln.debit > left[ln.customer_id]:  # (part of) it was received: taking it back would leave a false credit
                out["balances_kept"] += 1
                continue
            left[ln.customer_id] -= ln.debit
        db.delete(e)
        out["balances"] += 1
    for did in c.get("deposits", []):
        d = db.get(Deposit, did)
        if d is None:
            continue
        # used on an invoice (also one voided since: open again, but its settlement entry stays in the books),
        # refunded or forfeited: it stays with all its entries
        if d.status != "held" or db.scalar(select(JournalEntry.id).where(JournalEntry.ref_type == "deposit_apply",
                                                                         JournalEntry.ref_id == did).limit(1)) is not None:
            out["credits_kept"] += 1
            continue
        for e in db.scalars(select(JournalEntry).where(JournalEntry.ref_type == "deposit", JournalEntry.ref_id == did)):
            db.delete(e)
        db.delete(d)
        out["credits"] += 1
    for cid, old in reversed(c.get("linked", [])):  # last change first: each code goes back to where it was
        cust = db.get(Customer, cid)
        if cust:
            cust.tp_code = old
    for cid, old in reversed(c.get("renamed", [])):
        cust = db.get(Customer, cid)
        if cust:
            cust.full_name = old
    db.flush()
    unused = []
    for cid in c.get("customers", []):
        cust = db.get(Customer, cid)
        if cust is None:
            continue
        if has_records(db, cid):
            continue
        unused.append(cust)
    out["customers"] = accounting.delete_customers(db, unused)
    # invoices that keep items of other files: their sums and discounts; sales whose cost came from what was removed
    for doc in db.scalars(select(TradeDoc).where(TradeDoc.id.in_([x for x in docs_left if x] or [-1]))):
        _refresh_doc(db, doc)
    db.flush()
    recost_history(db, [x for x in hist_products if x and db.get(Product, x) is not None])
    batch.status = "undone"
    batch.summary = {**batch.summary, "undo": dict(out)}
    return dict(out)
