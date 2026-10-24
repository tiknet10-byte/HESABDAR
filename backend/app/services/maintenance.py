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
    BankRule,
    BankTransaction,
    CapitalMove,
    ConversationMessage,
    ConversationState,
    Customer,
    Deposit,
    Expense,
    HistoryMonth,
    Holding,
    HoldingMove,
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
    ReceiptImage,
    SalaryAccrual,
    StaffPayout,
    StockMove,
    Supplier,
    SupplierPayment,
    TradeDoc,
    TradeHistory,
    User,
    WaitlistEntry,
    TreasuryMove,
    TreasuryRate,
    WooLog,
    WooOrder,
    WooOutbox,
    WooProduct,
)

# order matters (foreign keys): children first. Stock moves, website orders and purchases point at invoices and
# products; history rows point at their invoice headers; appointments point at the invoice that settled them.
TRANSACTIONS = [WooOutbox, WooOrder, WooLog, StockMove, CapitalMove, HoldingMove, SupplierPayment, PurchaseItem, Purchase, TradeHistory, TradeDoc,
                WaitlistEntry, InboundReceipt, BankTransaction, Payment, Deposit, Appointment, InvoiceItem, Invoice, Expense,
                StaffPayout, SalaryAccrual, ReceiptImage, HistoryMonth, TreasuryMove,
                JournalLine, JournalEntry, Alert, ConversationMessage, ConversationState, ImportBatch]
# kept by "transactions" and "customers", wiped by "factory" (then the defaults are seeded again); children first too
CATALOG = [BankRule, TreasuryRate, WooProduct, Product, Supplier, Holding, Service, Staff, PaymentAccount, ServiceLine, KnowledgeItem, LedgerAccount, Setting,
           PluginState, AuditLog]
KEPT = [User]  # logins are never wiped. Every table is in exactly one of TRANSACTIONS, [Customer], CATALOG, KEPT (tested)
SCOPES = {
    "transactions": "همه تراکنش‌ها (فاکتور، بیعانه، دریافت، هزینه، نوبت، رسید، اسناد، خرید و موجودی کالا، سرمایه و طلا/ارز، "
                    "پرداخت به پرسنل و حقوق کارمندان، "
                    "سفارش‌های سایت و سوابق تیزپرداز) - مشتریان، خدمات، کالاها، فروشندگان و تنظیمات می‌مانند",
    "customers": "تراکنش‌ها + همه مشتریان - خدمات، کالاها، فروشندگان، کارتخوان‌ها و تنظیمات می‌مانند",
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
    # gold / dollars stay as names with nothing held (their moves were transactions)
    db.execute(update(Holding).values(qty_m=0, cost=0))
    # services stay, without the average price learned from the sales that are gone (it warns about prices, fills OCR rows)
    db.execute(update(Service).values(learned_avg_price=None, learned_count=0))
    # (the website keeps its reading place: orders read before are not read again - unless they change on the website
    # later, then they are booked as new; to start the website clean too, set a new start date in its settings)
    from . import settings_store

    if settings_store.get(db, "books.start"):  # the invoices / months it was worked out from are gone
        settings_store.set_value(db, "books.start", "")
    if scope in ("customers", "factory"):
        db.execute(update(Supplier).values(customer_id=None))
        wipe(Customer)
        # «not the same person» answers name customer ids, which the next customers would get again
        wipe(KnowledgeItem, KnowledgeItem.kind == "customer_not_same")
    if scope == "factory":
        for m in CATALOG:
            wipe(m)
    db.flush()
    if scope == "factory":
        from ..seed import seed_base
        from . import capital, inventory
        seed_base(db)
        inventory.ensure_chart(db)  # the whole chart at once, as at start-up (inventory, payables, gold/currency...)
        capital.ensure_chart(db)
        _plugins_to_defaults(db)
    return counts


def _plugins_to_defaults(db: Session) -> None:
    """Factory reset: every plugin back to its default on/off and settings (bank tokens, messaging keys... are gone).
    The running plugins follow once the reset is saved, not before (a failed reset changes nothing)."""
    from sqlalchemy import event

    from ..plugins.manager import manager

    fresh = {}
    for name, plugin in manager.plugins.items():
        on = plugin.status != "concept" and plugin.enabled_by_default
        db.add(PluginState(name=name, enabled=on, config=dict(plugin.default_config)))
        fresh[name] = on

    def apply(_session) -> None:  # noqa: ANN001
        for name, on in fresh.items():
            plugin = manager.plugins.get(name)
            if plugin is None:
                continue
            plugin.ctx.config = dict(plugin.default_config)
            was, manager.enabled[name] = manager.enabled.get(name), on
            if bool(was) != on:
                (plugin.on_enable if on else plugin.on_disable)()

    event.listen(db, "after_commit", apply, once=True)


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
              Product, Purchase, Supplier, TradeDoc, WooOrder, CapitalMove, Holding,
              Service, PaymentAccount, KnowledgeItem, AuditLog]
    out = {m.__tablename__: db.scalar(select(func.count()).select_from(m)) or 0 for m in models}
    url = str(dbmod.engine.url)
    if url.startswith("sqlite"):
        from pathlib import Path
        out["_size_bytes"] = Path(dbmod.engine.url.database).stat().st_size
    return out
