"""Scan a sales book / invoice (اسکن دفتر فروش و فاکتور).

Upload a photo, PDF, text or CSV of the handwritten sales book. The page is converted to
structured rows (date, customer, mobile, services, amount, payment method), every row is
validated against the books and the learned knowledge, and shown for review. Nothing is
posted until a person confirms; then customers, invoices and payments are created and the
system learns the vocabulary used in the book.

Validation warnings:
  * service not recognised / price out of the learned range
  * invalid mobile number
  * row total != sum of service prices
  * page total written in the book != sum of rows
  * possible duplicate (same customer, amount and day already recorded)
  * unknown payment method / POS
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ....ai import claude
from ....api.deps import require
from ....core.db import get_db
from ....models import ImportBatch, Invoice, PaymentAccount, Service
from ....services import accounting, learning
from ....services.audit import audit
from ....services.jalali import jalali_to_gregorian
from ....services.textutil import normalize_mobile, normalize_text, parse_amount, to_en_digits, toman
from ...base import AITool, BasePlugin

log = logging.getLogger("hesabdar.ocr")

VISION_PROMPT = """This is a page of a beauty salon's handwritten/printed sales book (دفتر فروش) or an invoice, in Persian.
Extract every customer row as JSON:
{"date_jalali": "YYYY/MM/DD"|null, "rows": [{"date_jalali": str|null, "customer_name": str, "mobile": str|null,
 "services": [{"name": str, "price": int|null}], "total": int|null, "deposit": int|null,
 "payment_method": "pos"|"card"|"cash"|"transfer"|null, "pos_or_card_hint": str|null, "note": str|null}],
 "page_total": int|null, "currency": "toman"|"rial"}
Use English digits. Keep Persian names as written. If unsure of a value use null."""

PAY_WORDS = {"pos": ["کارتخوان", "پوز", "pos", "دستگاه"], "card": ["کارت به کارت", "کارت", "واریز", "انتقال"],
             "cash": ["نقد", "نقدی", "کش"], "transfer": ["شبا", "پایا", "ساتنا"]}


def detect_payment(text: str) -> str | None:
    t = normalize_text(text)
    for method, words in PAY_WORDS.items():
        if any(w in t for w in words):
            return method
    return None


def _jdate(s: str | None) -> date | None:
    if not s:
        return None
    m = re.search(r"(1[34]\d{2})[/\-.](\d{1,2})[/\-.](\d{1,2})", to_en_digits(s))
    if not m:
        return None
    try:
        return date(*jalali_to_gregorian(*(int(x) for x in m.groups())))
    except ValueError:
        return None


def parse_text_lines(db: Session, text: str) -> dict:
    """Heuristic parser for typed books: one customer per line, fields separated by | , tab or -."""
    rows = []
    page_total = None
    current_date = None
    for line in to_en_digits(text).splitlines():
        line = line.strip()
        if not line:
            continue
        d = _jdate(line)
        if re.search(r"(جمع|مجموع|total)", line, re.I):
            page_total = max((parse_amount(x) or 0 for x in re.findall(r"[\d,]{4,}", line)), default=None)
            continue
        parts = [p.strip() for p in re.split(r"\s*[|\t،,؛;]\s*|\s+-\s+", line) if p.strip()]
        if d and len(parts) <= 1:
            current_date = d
            continue
        mobile = next((normalize_mobile(p) for p in parts if normalize_mobile(p)), None)
        amounts = [parse_amount(p) for p in parts if re.fullmatch(r"[\d,.]{4,}", p.replace(" ", ""))]
        amounts = [a for a in amounts if a and not (mobile and str(a) in mobile)]
        words = [p for p in parts if not re.search(r"\d", p)]
        services, name_parts = [], []
        for w in words:
            g = learning.classify_text(db, w, top=1)
            if detect_payment(w):
                continue
            if g and g[0]["score"] >= 0.5 and any("نام" in r for r in g[0]["reasons"]):
                services.append({"name": w, "price": None})
            else:
                name_parts.append(w)
        if not services and len(name_parts) > 1:
            services.append({"name": name_parts.pop(), "price": None})
        if len(services) == 1 and amounts:
            services[0]["price"] = amounts[-1]
        rows.append({"date_jalali": None, "date": (d or current_date).isoformat() if (d or current_date) else None,
                     "customer_name": " ".join(name_parts) or "?", "mobile": mobile, "services": services,
                     "total": amounts[-1] if amounts else None, "payment_method": detect_payment(line),
                     "raw": line})
    return {"rows": rows, "page_total": page_total, "currency": "toman"}


def parse_csv(text: str) -> dict:
    reader = csv.DictReader(io.StringIO(to_en_digits(text)))
    rows = []
    for r in reader:
        get = lambda *keys: next((r[k] for k in keys if k in r and r[k]), None)  # noqa: E731
        svc = get("service", "خدمت", "خدمات") or ""
        rows.append({"date": (_jdate(get("date", "تاریخ")) or date.today()).isoformat(),
                     "customer_name": get("customer", "name", "نام", "مشتری") or "?", "mobile": get("mobile", "موبایل", "تلفن"),
                     "services": [{"name": s.strip(), "price": None} for s in re.split(r"[+،,]", svc) if s.strip()],
                     "total": parse_amount(get("amount", "total", "مبلغ", "جمع") or ""), "payment_method": detect_payment(get("payment", "پرداخت") or ""),
                     "raw": ",".join(f"{k}={v}" for k, v in r.items())})
    return {"rows": rows, "page_total": None, "currency": "toman"}


def validate(db: Session, parsed: dict) -> list[dict]:
    factor = 10 if parsed.get("currency", "toman") == "toman" else 1
    accounts = db.scalars(select(PaymentAccount).where(PaymentAccount.is_active.is_(True))).all()
    default_by_kind = {}
    for a in accounts:
        default_by_kind.setdefault(a.kind, a.id)
    out = []
    for i, r in enumerate(parsed.get("rows", [])):
        warnings: list[str] = []
        row_date = r.get("date") or (_jdate(r.get("date_jalali")) or _jdate(parsed.get("date_jalali")) or date.today()).isoformat()
        mobile = normalize_mobile(r.get("mobile"))
        if r.get("mobile") and not mobile:
            warnings.append("شماره موبایل نامعتبر است")
        items = []
        for s in r.get("services") or []:
            match = learning.classify_text(db, s.get("name") or "", top=1)
            svc = db.get(Service, match[0]["service_id"]) if match and match[0]["score"] >= 0.5 else None
            price = (s.get("price") or 0) * factor or None
            if svc is None:
                warnings.append(f"خدمت «{s.get('name')}» شناسایی نشد")
            elif price:
                w = learning.price_check(svc, price)
                if w:
                    warnings.append(w)
            items.append({"text": s.get("name"), "service_id": svc.id if svc else None, "service": svc.name if svc else None,
                          "unit_price": price or (svc.learned_avg_price or svc.base_price if svc else 0), "price_from_book": bool(price)})
        total = (r.get("total") or 0) * factor
        items_sum = sum(it["unit_price"] or 0 for it in items)
        if len(items) == 1 and total and not items[0]["price_from_book"]:
            items[0]["unit_price"] = total
            items_sum = total
            if items[0]["service_id"]:
                w = learning.price_check(db.get(Service, items[0]["service_id"]), total)
                if w:
                    warnings.append(w)
        if total and items_sum and total != items_sum:
            warnings.append(f"جمع ردیف ({toman(total)}) با جمع خدمات ({toman(items_sum)}) برابر نیست")
        method = r.get("payment_method")
        acc_id = default_by_kind.get(method) if method else None
        if not method:
            warnings.append("روش پرداخت مشخص نیست")
        elif acc_id is None:
            warnings.append(f"حسابی از نوع {method} تعریف نشده")
        cust = accounting.find_customer(db, mobile) if mobile else None
        if cust and total:
            d0 = datetime.fromisoformat(row_date)
            dup = db.scalar(select(func.count(Invoice.id)).where(Invoice.customer_id == cust.id, Invoice.total == total,
                                                                Invoice.issued_at.between(d0, d0 + timedelta(days=1))))
            if dup:
                warnings.append("احتمال ثبت تکراری: فاکتور مشابه برای این مشتری در همین روز وجود دارد")
        out.append({"index": i, "date": row_date, "customer_name": r.get("customer_name"), "mobile": mobile,
                    "customer_id": cust.id if cust else None, "is_new_customer": cust is None,
                    "items": items, "total": total or items_sum, "deposit": (r.get("deposit") or 0) * factor,
                    "payment_method": method, "payment_account_id": acc_id, "warnings": warnings,
                    "include": True, "raw": r.get("raw") or r.get("note")})
    return out


def summarize(parsed: dict, rows: list[dict]) -> dict:
    factor = 10 if parsed.get("currency", "toman") == "toman" else 1
    total = sum(r["total"] for r in rows)
    page_total = (parsed.get("page_total") or 0) * factor
    s = {"rows": len(rows), "total": total, "page_total": page_total or None,
         "warnings": sum(len(r["warnings"]) for r in rows), "new_customers": sum(1 for r in rows if r["is_new_customer"])}
    if page_total and page_total != total:
        s["page_total_mismatch"] = f"جمع نوشته‌شده در دفتر ({toman(page_total)}) با جمع ردیف‌ها ({toman(total)}) برابر نیست"
    return s


class RowsIn(BaseModel):
    rows: list[dict]


class Plugin(BasePlugin):
    name = "sales_book_ocr"
    title = "اسکن هوشمند دفتر فروش و فاکتور"
    description = "تبدیل عکس/PDF دفتر فروش به اسناد حسابداری با کنترل مغایرت و یادگیری خودکار"
    version = "1.0.0"
    category = "ai"
    ui = [{"path": "/scan", "title": "اسکن دفتر فروش", "icon": "camera"}]

    def setup(self) -> None:
        r = APIRouter()

        @r.post("/upload")
        async def upload(file: UploadFile = File(...), currency: str = Form("toman"), db: Session = Depends(get_db),
                         user=Depends(require("finance"))):
            raw = await file.read()
            ctype = file.content_type or ""
            name = file.filename or "upload"
            provider = "text"
            if ctype.startswith("image/") or ctype == "application/pdf":
                if not claude.available():
                    raise HTTPException(400, "برای خواندن تصویر، هوش مصنوعی را فعال کنید یا فایل متنی/CSV بارگذاری کنید")
                parsed = claude.vision_extract(raw, ctype, VISION_PROMPT)
                provider = "claude_vision"
                text = ""
            else:
                text = raw.decode("utf-8-sig", errors="replace")
                parsed = parse_csv(text) if name.lower().endswith(".csv") else parse_text_lines(db, text)
                provider = "csv" if name.lower().endswith(".csv") else "text"
            parsed["currency"] = parsed.get("currency") or currency
            rows = validate(db, parsed)
            batch = ImportBatch(file_name=name, provider=provider, raw_text=text[:20000], rows=rows, summary=summarize(parsed, rows))
            db.add(batch)
            db.commit()
            return _batch(batch)

        @r.get("/batches")
        def batches(db: Session = Depends(get_db), _=Depends(require("finance"))):
            return [_batch(b, rows=False) for b in db.scalars(select(ImportBatch).order_by(ImportBatch.id.desc()).limit(100))]

        @r.get("/batches/{bid}")
        def batch(bid: int, db: Session = Depends(get_db), _=Depends(require("finance"))):
            b = db.get(ImportBatch, bid) or _404()
            return _batch(b)

        @r.put("/batches/{bid}/rows")
        def update_rows(bid: int, body: RowsIn, db: Session = Depends(get_db), _=Depends(require("finance"))):
            b = db.get(ImportBatch, bid) or _404()
            if b.status != "review":
                raise HTTPException(400, "batch already processed")
            b.rows = body.rows
            b.summary = {**b.summary, "total": sum(int(r.get("total") or 0) for r in body.rows if r.get("include", True))}
            db.commit()
            return _batch(b)

        @r.post("/batches/{bid}/commit")
        def commit(bid: int, db: Session = Depends(get_db), user=Depends(require("finance"))):
            b = db.get(ImportBatch, bid) or _404()
            if b.status != "review":
                raise HTTPException(400, "batch already processed")
            created = []
            try:
                for row in b.rows:
                    if not row.get("include", True):
                        continue
                    if row.get("customer_id"):
                        from ....models import Customer
                        cust = db.get(Customer, row["customer_id"])
                    else:
                        cust, _ = accounting.find_or_create_customer(db, row.get("customer_name"), row.get("mobile"), source="ocr", user=user)
                    items = [{"service_id": it.get("service_id"), "description": it.get("service") or it.get("text"),
                              "unit_price": int(it.get("unit_price") or 0)} for it in row.get("items") or []]
                    if not items:
                        items = [{"description": "خدمت ثبت‌شده از دفتر", "unit_price": int(row.get("total") or 0)}]
                    issued = datetime.fromisoformat(row["date"]) + timedelta(hours=12) if row.get("date") else None
                    pays = []
                    total = sum(i["unit_price"] for i in items)
                    if row.get("payment_account_id") and total:
                        pays = [{"payment_account_id": row["payment_account_id"], "amount": total}]
                    inv = accounting.issue_invoice(db, customer=cust, items=items, apply_all_deposits=True, payments=[],
                                                   issued_at=issued, source="ocr", notes=f"از دفتر فروش ({b.file_name})", user=user)
                    due = inv.total - inv.paid
                    if pays and due > 0:
                        accounting.record_payment(db, invoice=inv, payment_account=db.get(PaymentAccount, pays[0]["payment_account_id"]),
                                                  amount=due, paid_at=issued, source="ocr", user=user)
                    for it in row.get("items") or []:  # learn the book's vocabulary
                        if it.get("service_id") and it.get("text"):
                            learning.learn_text(db, it["text"], it["service_id"])
                    created.append(inv.number)
                b.status = "committed"
                b.summary = {**b.summary, "invoices": created}
                audit(db, "import.commit", "import_batch", b.id, {"invoices": len(created)}, user=user)
                learning.refresh_price_stats(db)
                db.commit()
            except accounting.AccountingError as exc:
                db.rollback()
                raise HTTPException(400, str(exc)) from exc
            return {"ok": True, "invoices": created}

        @r.post("/batches/{bid}/discard")
        def discard(bid: int, db: Session = Depends(get_db), _=Depends(require("finance"))):
            b = db.get(ImportBatch, bid) or _404()
            b.status = "discarded"
            db.commit()
            return {"ok": True}

        self.router = r

    def ai_tools(self) -> list[AITool]:
        def pending(db, user):  # noqa: ANN001, ANN202
            return [_batch(b, rows=False) for b in db.scalars(select(ImportBatch).where(ImportBatch.status == "review"))]
        return [AITool("sales_book_batches_pending", "Scanned sales-book pages waiting for review, with warning counts.",
                       {"type": "object", "properties": {}}, pending, "finance")]


def _batch(b: ImportBatch, rows: bool = True) -> dict:
    d = {"id": b.id, "file_name": b.file_name, "status": b.status, "provider": b.provider, "summary": b.summary,
         "created_at": b.created_at.isoformat()}
    if rows:
        d["rows"] = b.rows
    return d


def _404():  # noqa: ANN202
    raise HTTPException(404, "not found")
