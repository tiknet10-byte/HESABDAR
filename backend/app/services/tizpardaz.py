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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Appointment,
    Customer,
    Deposit,
    ImportBatch,
    InvoiceItem,
    JournalEntry,
    PaymentAccount,
    Product,
    Service,
    StockMove,
    TradeHistory,
    local_now,
)
from . import accounting, inventory, settings_store
from .customer_merge import person_key
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
                "account": ["عنوان حساب"], "account_code": ["کد حساب"], "desc": ["شرح"], "qty": ["مقدار", "تعداد"], "price": ["فی"],
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


def preview_customers(db: Session, rows: list[list[object]], mapping: dict[str, int], unit: str) -> dict:
    idx = _customer_index(db)
    by_tp = {c.tp_code: c for c in db.scalars(select(Customer).where(Customer.tp_code.is_not(None)))}
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
        parsed.append(row)
    ids = list({c.id for p in parsed for c in p["candidates"]})
    spent = _spent(db, ids)
    for p in parsed:
        p["candidates"] = [_cbrief(c, spent) for c in p["candidates"]]
    count = defaultdict(int)
    for p in parsed:
        count[p["status"]] += 1
    return {"rows": parsed, "summary": {"total": len(parsed), **count, "debit": sum(p["debit"] for p in parsed),
                                        "credit": sum(p["credit"] for p in parsed)}}


def commit_customers(db: Session, batch: ImportBatch, choices: dict[str, int | str], *, balances: bool, at: datetime | None,
                     account_id: int | None, user=None) -> dict:  # noqa: ANN001
    at = at or local_now()
    created = {"customers": [], "entries": [], "deposits": [], "linked": []}
    counts = defaultdict(int)
    account = db.get(PaymentAccount, account_id) if account_id else db.scalar(select(PaymentAccount).order_by(PaymentAccount.id))
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
            if row["code"] and c.tp_code != row["code"]:
                other = db.scalar(select(Customer).where(Customer.tp_code == row["code"], Customer.id != c.id))
                if other is not None:
                    other.tp_code = None
                created["linked"].append([c.id, c.tp_code])
                c.tp_code = row["code"]
            if row["mobile"] and not c.mobile and not accounting.find_customer(db, row["mobile"]):
                c.mobile, c.mobile_issue = row["mobile"], None
            counts["linked"] += 1
        if not balances:
            continue
        already = db.scalar(select(func.count(JournalEntry.id)).where(JournalEntry.ref_type == "tp_opening", JournalEntry.ref_id == c.id)) or \
            db.scalar(select(func.count(Deposit.id)).where(Deposit.customer_id == c.id, Deposit.source == "import", Deposit.notes.like("%تیزپرداز%")))
        if already and (row["debit"] or row["credit"]):
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
        if row["credit"] > 0 and account is not None:  # customer's credit: kept as an open deposit to use on a next invoice
            dep = Deposit(customer_id=c.id, amount=row["credit"], payment_account_id=account.id, received_at=at, source="import",
                          notes=f"ماندهٔ بستانکار تیزپرداز (کد {row['code']})")
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
        if linked is not None and (linked.sku or "").lower() == (sku or "").lower() or (linked is not None and name_key(linked.name) == k):
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
    at = at or local_now()
    created = {"products": [], "snapshots": {}, "moves": [], "history": [], "services": [], "appointments": []}
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
            created["snapshots"][str(p.id)] = {k: getattr(p, k) for k in ("code", "sku", "name", "category", "sale_price", "last_purchase_cost", "is_active")}
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
            other.code = f"x{other.id}"
            db.flush()
            other.code = inventory.next_product_code(db, incoming)
            counts["codes_moved"] += 1
        if row["sku"]:
            for o in db.scalars(select(Product).where(func.lower(Product.sku) == row["sku"].lower(), Product.id != p.id)):
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
        if not has_moves:
            if target > 0:
                m = inventory.opening_stock(db, p, target, row["buy"], at, user=user)
                created["moves"].append(m.id)
        else:
            m = inventory.count_stock(db, p, target, at, note="هم‌سان‌سازی با موجودی تیزپرداز", user=user)
            if m is not None:
                created["moves"].append(m.id)
        counts["units"] += target
        if service is not None:  # sold as a "service" in Chehreh: that history becomes this product's sales
            n = _service_to_product(db, service, p, batch.id, created)
            counts["chehreh_sales"] += n
    batch.summary = {**batch.summary, "created": created, "result": dict(counts)}
    return dict(counts)


def _service_to_product(db: Session, service: Service, product: Product, batch_id: int, created: dict) -> int:
    n = 0
    for a in list(db.scalars(select(Appointment).where(Appointment.service_id == service.id, Appointment.status == "done",
                                                       Appointment.invoice_id.is_(None)))):
        h = TradeHistory(batch_id=batch_id, source="chehreh", kind="sale", at=a.start_at, product_id=product.id, customer_id=a.customer_id,
                         description=service.name, qty=1, unit_price=int(a.quoted_price or 0), amount=int(a.quoted_price or 0),
                         fp=f"chehreh|appt|{a.id}")
        db.add(h)
        created["appointments"].append({"id": a.id, "customer_id": a.customer_id, "service_id": a.service_id, "staff_id": a.staff_id,
                                        "start_at": a.start_at.isoformat(), "quoted_price": a.quoted_price, "notes": a.notes,
                                        "status": a.status, "duration_minutes": a.duration_minutes})
        db.delete(a)
        n += 1
    db.flush()
    created["history"] += [h.id for h in db.scalars(select(TradeHistory).where(TradeHistory.batch_id == batch_id, TradeHistory.source == "chehreh",
                                                                             TradeHistory.product_id == product.id))]
    if not db.scalar(select(func.count(InvoiceItem.id)).where(InvoiceItem.service_id == service.id)):
        service.is_active = False  # now a product; kept (archived) only for its code / old records
        created["services"].append(service.id)
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


def preview_journal(db: Session, rows: list[list[object]], mapping: dict[str, int], unit: str) -> dict:
    p_exact, p_all = _product_index(db)
    idx = _customer_index(db)
    by_tp = {c.tp_code: c for c in db.scalars(select(Customer).where(Customer.tp_code.is_not(None)))}
    done = set(db.scalars(select(TradeHistory.fp).where(TradeHistory.source == SOURCE)))
    parsed, ignored = [], defaultdict(int)
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
        if kind is None or d is None:
            ignored[doc_type or "بدون نوع"] += 1
            continue
        if kind == "expense":
            amount = debit
            if amount <= 0:
                ignored[f"{doc_type} (طرف پرداخت)"] += 1  # the cash side of the expense document
                continue
            row = {"row": n, "kind": kind, "date": d.isoformat(), "doc_no": _text(_get(r, mapping, "doc_no")), "account": account,
                   "description": desc, "amount": amount, "qty": 0, "unit_price": 0}
        else:
            if qty <= 0:
                ignored[f"{doc_type} (ردیف بدون کالا)"] += 1  # customer / cash / discount side of the document
                continue
            amount = (credit if kind in ("sale", "purchase_return") else debit) or qty * price
            product, party = _split_desc(desc, kind)
            pid = p_exact.get(name_key(product))
            row = {"row": n, "kind": kind, "date": d.isoformat(), "doc_no": _text(_get(r, mapping, "doc_no")), "account": account,
                   "description": desc, "product_name": product, "product_id": pid, "party": party, "qty": qty,
                   "unit_price": price or (amount // qty if qty else 0), "amount": amount, "customer_id": None}
            if pid is None:
                u = unknown_p.setdefault(product, {"name": product, "count": 0, "similar": []})
                u["count"] += 1
                if not u["similar"]:
                    k = name_key(product)
                    u["similar"] = [{"id": i, "name": nm} for kk, i, nm in p_all if keys_alike(kk, k) or (len(kk) > 4 and (kk in k or k in kk))][:3]
            if kind in ("sale", "sale_return") and party:
                cid, cands = _party_customer(db, party, idx, by_tp)
                row["customer_id"] = cid
                if cid is None:
                    u = unknown_c.setdefault(party, {"party": party, "name": tp_name(party), "count": 0, "generic": is_generic(tp_name(party)),
                                                     "candidates": [{"id": c.id, "full_name": c.full_name, "code": c.legacy_code, "mobile": c.mobile} for c in cands]})
                    u["count"] += 1
        base = "|".join(str(x) for x in (row["kind"], row["doc_no"], row["date"], row["description"], row["qty"], row["amount"]))
        seen[base] += 1
        row["fp"] = f"{base}|{seen[base]}"[:160]
        row["duplicate"] = row["fp"] in done
        parsed.append(row)
    fresh = [p for p in parsed if not p["duplicate"]]
    tot = defaultdict(int)
    cnt = defaultdict(int)
    for p in fresh:
        tot[p["kind"]] += p["amount"]
        cnt[p["kind"]] += 1
    dates = sorted(p["date"] for p in fresh)
    return {"rows": parsed, "summary": {"total": len(parsed), "new": len(fresh), "duplicates": len(parsed) - len(fresh), "counts": dict(cnt),
                                        "amounts": dict(tot), "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
                                        "ignored": dict(ignored)},
            "unknown_products": sorted(unknown_p.values(), key=lambda x: -x["count"]),
            "unknown_customers": sorted(unknown_c.values(), key=lambda x: -x["count"])}


def commit_journal(db: Session, batch: ImportBatch, product_map: dict[str, int | str], customer_map: dict[str, int | str], user=None) -> dict:  # noqa: ANN001
    created = {"history": [], "customers": []}
    counts = defaultdict(int)
    new_customers: dict[str, int] = {}
    done = set(db.scalars(select(TradeHistory.fp).where(TradeHistory.source == SOURCE)))
    for row in batch.rows:
        if row["fp"] in done:
            counts["duplicates"] += 1
            continue
        pid = row.get("product_id")
        if row["kind"] != "expense" and pid is None:
            choice = product_map.get(row.get("product_name", ""), "skip")
            pid = int(choice) if isinstance(choice, int) or str(choice).isdigit() else None
        cid = row.get("customer_id")
        if row["kind"] in ("sale", "sale_return") and cid is None and row.get("party"):
            # a written-out person («نام‌خانوادگی(نام)» or a code) becomes a customer; a word like «متفرقه» stays a name
            person = ("(" in row["party"] or to_en_digits(row["party"]).strip().isdigit()) and not is_generic(tp_name(row["party"]))
            choice = customer_map.get(row["party"], "new" if person else "skip")
            if isinstance(choice, int) or str(choice).isdigit():
                cid = int(choice)
            elif choice == "new":
                if row["party"] not in new_customers:
                    party = row["party"]
                    code = to_en_digits(party).strip()
                    c = Customer(full_name=tp_name(party) if not code.isdigit() else f"مشتری تیزپرداز {code}", source=SOURCE,
                                 legacy_code=accounting.next_customer_code(db), mobile_issue="missing", tp_code=code if code.isdigit() else None)
                    db.add(c)
                    db.flush()
                    new_customers[party] = c.id
                    created["customers"].append(c.id)
                cid = new_customers[row["party"]]
        d = datetime.combine(datetime.fromisoformat(row["date"]).date(), time(12, 0))
        h = TradeHistory(batch_id=batch.id, source=SOURCE, kind=row["kind"], at=d, doc_no=row["doc_no"][:32], product_id=pid, customer_id=cid,
                         party=(row.get("party") or "")[:128], account=(row.get("account") or "")[:128], description=row["description"][:256],
                         qty=row["qty"], unit_price=row["unit_price"], amount=row["amount"], fp=row["fp"])
        db.add(h)
        done.add(row["fp"])
        counts[row["kind"]] += 1
        if pid is None and row["kind"] != "expense":
            counts["without_product"] += 1
    db.flush()
    created["history"] = list(db.scalars(select(TradeHistory.id).where(TradeHistory.batch_id == batch.id)))
    products = set(db.scalars(select(TradeHistory.product_id).where(TradeHistory.batch_id == batch.id, TradeHistory.product_id.is_not(None))))
    recost_history(db, products)
    batch.summary = {**batch.summary, "created": created, "result": dict(counts)}
    return dict(counts)


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
        unit = p.last_purchase_cost or next((h.amount // h.qty for h in hist if h.kind == "purchase" and h.qty), 0)
        if low < 0:  # sold before any purchase in the history: there was stock at the start
            moves.append(SimpleNamespace(id=-1, at=hist[0].at, kind="opening", qty=-low, cost=-low * unit, ref_move_id=None))
        kinds = {"purchase": "purchase", "sale": "sale", "sale_return": "adjust_in", "purchase_return": "adjust_out"}
        for h in hist:
            moves.append(SimpleNamespace(id=h.id, at=h.at, kind=kinds[h.kind], qty=sign[h.kind] * h.qty,
                                         cost=h.amount if h.kind == "purchase" else 0, ref_move_id=None))
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


def undo(db: Session, batch: ImportBatch) -> dict:
    """Take back what a committed file added (customers that got other records since are kept)."""
    if batch.status != "committed":
        raise ImportProblem("فقط فایل‌های ثبت‌شده قابل برگشت هستند")
    c = (batch.summary or {}).get("created", {})
    out = defaultdict(int)
    for hid in c.get("history", []):
        h = db.get(TradeHistory, hid)
        if h:
            db.delete(h)
            out["history"] += 1
    for a in c.get("appointments", []):  # Chehreh history that had moved to a product goes back to its service
        db.add(Appointment(customer_id=a["customer_id"], service_id=a["service_id"], staff_id=a["staff_id"],
                           start_at=datetime.fromisoformat(a["start_at"]), quoted_price=a["quoted_price"], notes=a["notes"],
                           status=a["status"], duration_minutes=a["duration_minutes"]))
    for sid in c.get("services", []):
        s = db.get(Service, sid)
        if s:
            s.is_active = True
    db.flush()
    for mid in c.get("moves", []):
        m = db.get(StockMove, mid)
        if m:
            for e in db.scalars(select(JournalEntry).where(JournalEntry.ref_type.in_(("stock_move", "stock_opening")), JournalEntry.ref_id == m.id)):
                db.delete(e)
            pid = m.product_id
            db.delete(m)
            db.flush()
            inventory.recost(db, pid)
            out["moves"] += 1
    for pid, snap in (c.get("snapshots") or {}).items():
        p = db.get(Product, int(pid))
        if p:
            for k, v in snap.items():
                setattr(p, k, v)
    for pid in c.get("products", []):
        p = db.get(Product, pid)
        if p and not db.scalar(select(func.count(StockMove.id)).where(StockMove.product_id == pid)) \
                and not db.scalar(select(func.count(InvoiceItem.id)).where(InvoiceItem.product_id == pid)):
            db.delete(p)
            out["products"] += 1
    for eid in c.get("entries", []):
        e = db.get(JournalEntry, eid)
        if e:
            db.delete(e)
            out["balances"] += 1
    for did in c.get("deposits", []):
        d = db.get(Deposit, did)
        if d and d.status == "held":
            for e in db.scalars(select(JournalEntry).where(JournalEntry.ref_type == "deposit", JournalEntry.ref_id == did)):
                db.delete(e)
            db.delete(d)
            out["credits"] += 1
    for cid, old in c.get("linked", []):
        cust = db.get(Customer, cid)
        if cust:
            cust.tp_code = old
    db.flush()
    unused = []
    for cid in c.get("customers", []):
        cust = db.get(Customer, cid)
        if cust is None:
            continue
        from ..models import Invoice, Payment
        if any(db.scalar(select(func.count(m.id)).where(m.customer_id == cid)) for m in (Appointment, Deposit, Invoice, Payment, TradeHistory)):
            continue
        unused.append(cust)
    out["customers"] = accounting.delete_customers(db, unused)
    batch.status = "undone"
    batch.summary = {**batch.summary, "undo": dict(out)}
    return dict(out)
