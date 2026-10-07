"""Database maintenance: optimize, and reset (wipe) data in controlled scopes.

Every reset takes an encrypted safety backup first, so it can be undone from Settings -> Backups.
Users (logins) are always kept.
"""
from __future__ import annotations

from sqlalchemy import delete, text, update
from sqlalchemy.orm import Session

from ..core import db as dbmod
from ..models import (
    Alert,
    Appointment,
    AuditLog,
    BankTransaction,
    ConversationMessage,
    ConversationState,
    Customer,
    Deposit,
    Expense,
    ImportBatch,
    InboundReceipt,
    Invoice,
    InvoiceItem,
    JournalEntry,
    JournalLine,
    KnowledgeItem,
    LedgerAccount,
    Payment,
    PaymentAccount,
    PluginState,
    Product,
    Purchase,
    PurchaseItem,
    Service,
    ServiceLine,
    Setting,
    Staff,
    StockMove,
    Supplier,
    SupplierPayment,
    TradeDoc,
    TradeHistory,
    WaitlistEntry,
    WooLog,
    WooOrder,
    WooOutbox,
    WooProduct,
)

# order matters (foreign keys): children first. Stock moves, website orders and purchases point at invoices and
# products; history rows point at their invoice headers; appointments point at the invoice that settled them.
TRANSACTIONS = [WooOutbox, WooOrder, WooLog, StockMove, SupplierPayment, PurchaseItem, Purchase, TradeHistory, TradeDoc,
                WaitlistEntry, InboundReceipt, BankTransaction, Payment, Deposit, Appointment, InvoiceItem, Invoice, Expense,
                JournalLine, JournalEntry, Alert, ConversationMessage, ConversationState, ImportBatch]
SCOPES = {
    "transactions": "همه تراکنش‌ها (فاکتور، بیعانه، دریافت، هزینه، نوبت، رسید، اسناد) - مشتریان، خدمات و تنظیمات می‌مانند",
    "customers": "تراکنش‌ها + همه مشتریان - خدمات، کارتخوان‌ها و تنظیمات می‌مانند",
    "factory": "بازگشت به تنظیمات کارخانه: همه داده‌ها پاک می‌شود (فقط کاربران می‌مانند)",
}


def reset(db: Session, scope: str) -> dict:
    if scope not in SCOPES:
        raise ValueError("invalid scope")
    counts: dict[str, int] = {}

    def wipe(model, where=None) -> None:  # noqa: ANN001
        stmt = delete(model) if where is None else delete(model).where(where)
        counts[model.__tablename__] = counts.get(model.__tablename__, 0) + (db.execute(stmt).rowcount or 0)

    for m in TRANSACTIONS:
        wipe(m)
    wipe(KnowledgeItem, KnowledgeItem.kind == "loyalty")
    # products stay (catalog), with no stock and no price remembered from sales that no longer exist
    db.execute(update(Product).values(stock_qty=0, stock_value=0, unit_cost=None, last_purchase_cost=None, last_sale_price=None,
                                      last_sale_at=None, last_online_price=None, last_online_at=None))
    if scope in ("customers", "factory"):
        db.execute(update(Supplier).values(customer_id=None))
        wipe(Customer)
    if scope == "factory":
        for m in (WooProduct, Product, Supplier, Service, Staff, PaymentAccount, ServiceLine, KnowledgeItem, LedgerAccount, Setting,
                  PluginState, AuditLog):
            wipe(m)
    db.flush()
    if scope == "factory":
        from ..seed import seed_base
        seed_base(db)
    return counts


def optimize() -> dict:
    """Integrity check + statistics + VACUUM (reclaims space after deletes)."""
    url = str(dbmod.engine.url)
    if not url.startswith("sqlite"):
        with dbmod.engine.connect() as con:
            con.execute(text("ANALYZE"))
        return {"ok": True, "engine": "postgresql"}
    from pathlib import Path

    path = Path(dbmod.engine.url.database)
    before = path.stat().st_size
    with dbmod.engine.connect() as con:
        integrity = con.execute(text("PRAGMA integrity_check")).scalar()
        con.execute(text("PRAGMA optimize"))
        con.execute(text("ANALYZE"))
        con.commit()
    import sqlite3
    from contextlib import closing

    dbmod.engine.dispose()  # release pooled connections so VACUUM can get exclusive access
    vacuumed = True
    with closing(sqlite3.connect(path, timeout=30)) as con:
        try:
            con.execute("VACUUM")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            vacuumed = False  # database busy - integrity check and statistics still done
    return {"ok": integrity == "ok", "integrity": integrity, "vacuumed": vacuumed, "size_before": before,
            "size_after": path.stat().st_size}


def stats(db: Session) -> dict:
    from sqlalchemy import func, select

    models = [Customer, Invoice, Deposit, Payment, Appointment, Expense, JournalEntry, BankTransaction, InboundReceipt,
              Service, PaymentAccount, KnowledgeItem, AuditLog]
    out = {m.__tablename__: db.scalar(select(func.count()).select_from(m)) or 0 for m in models}
    url = str(dbmod.engine.url)
    if url.startswith("sqlite"):
        from pathlib import Path
        out["_size_bytes"] = Path(dbmod.engine.url.database).stat().st_size
    return out
