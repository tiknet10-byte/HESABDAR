"""Integration (group G9): the data reset reaches every table, customer merge carries what is remembered by customer
id, the database page counts the newer tables. Mobiles start with 0915109.

The full resets run on a database of their own (data in every table), so the shared test database is not touched."""
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import db as dbmod
from app.core.db import Base, SessionLocal
from app.models import (
    Alert, Appointment, AuditLog, BankTransaction, CapitalMove, ConversationMessage, ConversationState, Customer, Deposit,
    Expense, Holding, HoldingMove, ImportBatch, InboundReceipt, Invoice, InvoiceItem, JournalEntry, JournalLine, KnowledgeItem,
    LedgerAccount, Payment, PaymentAccount, PluginState, Product, Purchase, PurchaseItem, Service, ServiceLine, Setting, Staff,
    StockMove, Supplier, SupplierPayment, TradeDoc, TradeHistory, User, WaitlistEntry, WooLog, WooOrder, WooOutbox, WooProduct,
)
from app.plugins.manager import manager
from app.services import capital, customer_merge, inventory, maintenance
from app.services.audit import audit, verify_audit_chain
from app.seed import seed_base


# ---------------------------------------------------------------- a database of its own, with data in every table
def _new_db() -> tuple[Session, object]:
    path = Path(tempfile.mkdtemp(prefix="g9-reset-")) / "g9.db"
    engine = dbmod._make_engine(f"sqlite:///{path}")  # same pragmas as the app: foreign keys ON
    Base.metadata.create_all(engine)
    db = Session(engine, autoflush=False, expire_on_commit=False)
    seed_base(db)
    inventory.ensure_chart(db)
    capital.ensure_chart(db)
    db.commit()
    return db, engine


def _count(db: Session, table: str) -> int:
    return db.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()


def _fill(db: Session) -> dict:
    now = datetime(2026, 1, 10, 10, 0)
    line = db.scalars(select(ServiceLine).order_by(ServiceLine.id)).first()
    svc = db.scalars(select(Service).where(Service.line_id == line.id)).first()
    pa = db.scalars(select(PaymentAccount).order_by(PaymentAccount.id)).first()
    cash_acc = db.get(LedgerAccount, pa.ledger_account_id)
    other_acc = db.scalar(select(LedgerAccount).where(LedgerAccount.code == "3100"))
    db.add(User(username="g9-reset-user", password_hash="x", role="receptionist"))
    db.add(AuditLog(action="g9.test", entity="g9"))
    db.add(Setting(key="woo.state", value={"cursor": "2026-01-09T08:00:00", "methods": {"cod": "پرداخت در محل"}}))
    db.add(Setting(key="salon.name", value="سالن g9"))
    staff = Staff(full_name="پرسنل g9", line_id=line.id)
    c1, c2 = Customer(full_name="مشتری g9 یک", mobile="09151090101"), Customer(full_name="مشتری g9 دو", mobile="09151090102")
    product = Product(code="g9-1", name="کالای g9", stock_qty=4, stock_value=400, unit_cost=100, last_purchase_cost=100,
                      last_sale_price=150, last_sale_at=now, last_online_price=160, last_online_at=now)
    svc.learned_avg_price, svc.learned_count = 150, 5  # learned from the sales below
    db.add_all([staff, c1, c2, product])
    db.flush()
    inv = Invoice(number="G9-1", customer_id=c1.id, issued_at=now, subtotal=150, total=150, paid=150)
    db.add(inv)
    db.flush()
    item = InvoiceItem(invoice_id=inv.id, service_id=svc.id, product_id=product.id, line_id=line.id, staff_id=staff.id, unit_price=150)
    appt = Appointment(customer_id=c1.id, service_id=svc.id, staff_id=staff.id, start_at=now, invoice_id=inv.id)
    db.add_all([item, appt])
    db.flush()
    dep = Deposit(customer_id=c1.id, appointment_id=appt.id, service_id=svc.id, payment_account_id=pa.id, amount=50,
                  applied_invoice_id=inv.id, staff_id=staff.id, refund_account_id=pa.id, status="applied")
    bt = BankTransaction(payment_account_id=pa.id, external_id="g9-tx", amount=50, occurred_at=now)
    supplier = Supplier(name="فروشنده g9", customer_id=c2.id)
    entry = JournalEntry(description="سند g9", at=now)
    db.add_all([dep, bt, supplier, entry,
                WaitlistEntry(customer_id=c1.id, service_id=svc.id, staff_id=staff.id, appointment_id=appt.id),
                Payment(invoice_id=inv.id, customer_id=c1.id, payment_account_id=pa.id, amount=100, paid_at=now),
                Expense(category="هزینه g9", amount=10, payment_account_id=pa.id, staff_id=staff.id, spent_at=now),
                Alert(kind="g9", title="هشدار g9"),
                ConversationMessage(channel="sms", peer="g9", customer_id=c1.id, direction="in", text="سلام", service_id=svc.id),
                ConversationState(channel="sms", peer="g9"),
                KnowledgeItem(kind="loyalty", key=str(c1.id), value={"points": 3}),
                KnowledgeItem(kind="customer_not_same", key=f"{c1.id}-{c2.id}", value={}),
                ImportBatch(kind="g9"),
                PluginState(name="g9_plugin", enabled=True, config={"token": "راز"}),
                WooProduct(id=909001, product_id=product.id, sku="G9SKU"),
                WooOutbox(product_id=product.id, kind="delta", qty=1),
                WooLog(message="g9")])
    db.flush()
    db.add_all([JournalLine(entry_id=entry.id, account_id=cash_acc.id, debit=10, customer_id=c1.id, line_id=line.id),
                JournalLine(entry_id=entry.id, account_id=other_acc.id, credit=10),
                InboundReceipt(channel="sms", sender="g9", customer_id=c1.id, bank_transaction_id=bt.id, deposit_id=dep.id),
                WooOrder(order_id=909001, invoice_id=inv.id, customer_id=c1.id)])
    pur = Purchase(number="G9-P1", supplier_id=supplier.id, at=now, subtotal=100, total=100)
    doc = TradeDoc(key="g9|sale|1", kind="sale", at=now, customer_id=c1.id, supplier_id=supplier.id)
    hold = Holding(name="طلای g9", qty_m=1500, cost=900, market_price=700,
                   ledger_account_id=db.scalar(select(LedgerAccount.id).where(LedgerAccount.code == capital.HOLDINGS)))
    db.add_all([pur, doc, hold])
    db.flush()
    pi = PurchaseItem(purchase_id=pur.id, product_id=product.id, quantity=1, unit_price=100, cost=100)
    db.add_all([pi, TradeHistory(kind="sale", at=now, product_id=product.id, customer_id=c1.id, doc_id=doc.id),
                SupplierPayment(supplier_id=supplier.id, purchase_id=pur.id, payment_account_id=pa.id, amount=100, paid_at=now),
                CapitalMove(kind="add", amount=10, payment_account_id=pa.id, purchase_id=pur.id, deposit_id=dep.id, customer_id=c2.id, at=now),
                HoldingMove(holding_id=hold.id, kind="buy", qty_m=1500, amount=900, cost=900, payment_account_id=pa.id, at=now)])
    db.flush()
    db.add(StockMove(product_id=product.id, kind="sale", qty=-1, invoice_item_id=item.id, purchase_item_id=pi.id, at=now))
    db.commit()
    empty = [t.name for t in Base.metadata.sorted_tables if not _count(db, t.name)]
    assert not empty, f"the test data must reach every table: {empty}"
    return {"c1": c1.id, "c2": c2.id, "product": product.id, "holding": hold.id, "supplier": supplier.id, "service": svc.id}


def _tables(models) -> set[str]:
    return {m.__tablename__ for m in models}


def _plugin_snapshot():
    return dict(manager.enabled), {n: dict(p.ctx.config) for n, p in manager.plugins.items()}


def _plugin_restore(snap) -> None:
    enabled, configs = snap
    manager.enabled.clear()
    manager.enabled.update(enabled)
    for n, cfg in configs.items():
        manager.plugins[n].ctx.config = cfg


# ---------------------------------------------------------------- tests
def test_every_table_has_its_place_in_the_reset():
    """A table added later and forgotten in the reset fails here (it would stay full, or block the deletes)."""
    places = [_tables(maintenance.TRANSACTIONS), {"customers"}, _tables(maintenance.CATALOG), _tables(maintenance.KEPT)]
    every = {t.name for t in Base.metadata.sorted_tables}
    assert set().union(*places) == every, every ^ set().union(*places)
    assert sum(len(p) for p in places) == len(every)  # each table in exactly one place
    assert {"capital_moves", "holding_moves"} <= _tables(maintenance.TRANSACTIONS) and "holdings" in _tables(maintenance.CATALOG)


def test_reset_transactions_on_a_database_with_data_in_every_table():
    db, engine = _new_db()
    try:
        ids = _fill(db)
        catalog_before = {t: _count(db, t) for t in _tables(maintenance.CATALOG)}
        counts = maintenance.reset(db, "transactions")
        db.commit()
        assert counts["capital_moves"] == 1 and counts["holding_moves"] == 1 and counts["stock_moves"] == 1
        assert all(_count(db, t) == 0 for t in _tables(maintenance.TRANSACTIONS))
        assert _count(db, "customers") == 2 and _count(db, "users") == 1
        loyalty_gone = 1
        for t, n in catalog_before.items():  # the catalog stays, row by row (only the loyalty points go)
            assert _count(db, t) == n - (loyalty_gone if t == "knowledge_items" else 0), t
        db.expire_all()
        p, h = db.get(Product, ids["product"]), db.get(Holding, ids["holding"])
        assert (p.stock_qty, p.stock_value, p.unit_cost, p.last_sale_price, p.last_online_price) == (0, 0, None, None, None)
        assert (h.qty_m, h.cost, h.market_price) == (0, 0, 700)  # nothing held; the name and the day's price stay
        s = db.get(Service, ids["service"])  # no price learned from sales that are gone (it would warn about new prices)
        assert (s.learned_avg_price, s.learned_count, s.is_active) == (None, 0, True)
        assert db.get(Supplier, ids["supplier"]).customer_id == ids["c2"]  # the seller stays linked to the person
        kinds = set(db.scalars(select(KnowledgeItem.kind)))
        assert "loyalty" not in kinds and {"customer_not_same", "token_service"} <= kinds
        # the website keeps its reading place: the orders read before are not read again unless they change on the website
        assert db.get(Setting, "woo.state").value == {"cursor": "2026-01-09T08:00:00", "methods": {"cod": "پرداخت در محل"}}
        assert db.get(Setting, "salon.name").value == "سالن g9"
        assert db.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    finally:
        db.close()
        engine.dispose()


def test_reset_customers_on_a_database_with_data_in_every_table():
    db, engine = _new_db()
    try:
        ids = _fill(db)
        maintenance.reset(db, "customers")
        db.commit()
        assert all(_count(db, t) == 0 for t in _tables(maintenance.TRANSACTIONS) | {"customers"})
        db.expire_all()
        assert db.get(Supplier, ids["supplier"]).customer_id is None
        # «not the same person» answers named customer ids that the next customers get again (SQLite reuses them)
        assert not db.scalar(select(func.count()).select_from(KnowledgeItem).where(KnowledgeItem.kind.in_(("customer_not_same", "loyalty"))))
        assert db.scalar(select(func.count()).select_from(KnowledgeItem).where(KnowledgeItem.kind == "token_service"))
        assert _count(db, "products") == 1 and _count(db, "staff") == 1 and _count(db, "holdings") == 1
        nc = Customer(full_name="مشتری تازه g9", mobile="09151090103")
        db.add(nc)
        db.commit()
        assert nc.id == 1 and not customer_merge.not_same_pairs(db)  # the old id 1 is used again, with nothing attached
        assert db.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    finally:
        db.close()
        engine.dispose()


def test_factory_reset_on_a_database_with_data_in_every_table():
    fresh, fresh_engine = _new_db()  # what a new install has
    seeded = {t: _count(fresh, t) for t in ("service_lines", "services", "payment_accounts", "ledger_accounts", "knowledge_items")}
    fresh_codes = set(fresh.scalars(select(LedgerAccount.code)))
    fresh.close()
    fresh_engine.dispose()
    db, engine = _new_db()
    snap = _plugin_snapshot()
    try:
        _fill(db)
        for p in manager.plugins.values():  # the owner had set them up (tokens...) before the reset
            p.ctx.config = {**p.ctx.config, "g9_token": "قدیمی"}
        maintenance.reset(db, "factory")
        audit(db, "admin.reset", "database", "factory")  # as the reset endpoint does: the audit trail starts again
        defaults = {n: p.status != "concept" and p.enabled_by_default for n, p in manager.plugins.items()}
        # not saved yet: the running plugins are untouched (a failed reset changes nothing)
        assert all(p.ctx.config.get("g9_token") == "قدیمی" for p in manager.plugins.values())
        db.commit()
        assert verify_audit_chain(db) == {"ok": True, "checked": 1}
        assert all("g9_token" not in p.ctx.config for p in manager.plugins.values())
        assert {n: manager.enabled[n] for n in defaults} == defaults
        assert {s.name: s.enabled for s in db.scalars(select(PluginState))} == defaults  # g9_plugin is gone
        for t in {t.name for t in Base.metadata.sorted_tables}:
            want = seeded.get(t, len(defaults) if t == "plugin_states" else 1 if t in ("users", "audit_logs") else 0)
            assert _count(db, t) == want, t
        # the whole chart at once, as after a start: inventory, supplier payables, product sales, gold/currency
        codes = set(db.scalars(select(LedgerAccount.code)))
        assert codes == fresh_codes and {inventory.INVENTORY, inventory.PAYABLE, inventory.COGS, capital.HOLDINGS,
                                         capital.HOLDING_GAIN} <= codes
        assert db.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    finally:
        _plugin_restore(snap)
        db.close()
        engine.dispose()


def test_failed_factory_reset_leaves_the_running_plugins_alone():
    db, engine = _new_db()
    snap = _plugin_snapshot()
    try:
        for p in manager.plugins.values():
            p.ctx.config = {**p.ctx.config, "g9_token": "می‌ماند"}
        maintenance.reset(db, "factory")
        db.rollback()
        assert all(p.ctx.config.get("g9_token") == "می‌ماند" for p in manager.plugins.values())
    finally:
        _plugin_restore(snap)
        db.close()
        engine.dispose()


def test_merge_moves_every_table_that_belongs_to_a_customer():
    """A table added later with a customer_id and forgotten in the merge would keep pointing at the removed record."""
    with_customer = {t.name for t in Base.metadata.sorted_tables if "customer_id" in t.columns}
    assert with_customer == _tables(customer_merge.LINKED)
    assert {"capital_moves", "suppliers", "trade_docs", "woo_orders"} <= with_customer
    # what keeps a customer from being deleted: everything except the loosely attached messages and receipts
    assert _tables(customer_merge.RECORDS) == with_customer - {"inbound_receipts", "conversation_messages"}


def _points(cid: int) -> int | None:
    with SessionLocal() as db:
        item = db.scalar(select(KnowledgeItem).where(KnowledgeItem.kind == "loyalty", KnowledgeItem.key == str(cid)))
        return None if item is None else int(item.value["points"])


def _set_points(cid: int, points: int) -> None:
    with SessionLocal() as db:
        item = db.scalar(select(KnowledgeItem).where(KnowledgeItem.kind == "loyalty", KnowledgeItem.key == str(cid)))
        if item is None:
            db.add(KnowledgeItem(kind="loyalty", key=str(cid), value={"points": points}))
        else:
            item.value = {"points": points}
        db.commit()


def _customer(client, name: str, mobile: str) -> int:
    r = client.post("/api/customers", json={"full_name": name, "mobile": mobile})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_merge_adds_up_loyalty_points_and_keeps_not_same_answers(client):
    name = "ژینا همنام‌جی‌نه"
    a, b, c = (_customer(client, name, f"0915109001{i}") for i in (1, 2, 3))
    assert client.post("/api/customers/not-same", json={"ids": [a, c]}).json()["pairs"] == 1  # a is not c
    _set_points(b, 7)
    _set_points(c, 5)
    r = client.post("/api/customers/merge", json={"keep_id": b, "drop_ids": [c]})
    assert r.status_code == 200, r.text
    assert _points(b) == 12 and _points(c) is None  # 7 + 5, nothing left under the removed id
    with SessionLocal() as db:
        pairs = customer_merge.not_same_pairs(db)
    assert f"{min(a, b)}-{max(a, b)}" in pairs and f"{min(a, c)}-{max(a, c)}" not in pairs
    # c was b, and a is not c: a and b are not offered as the same person again
    groups = client.get("/api/customers/duplicates").json()
    assert not any({a, b} <= {m["id"] for m in g["customers"]} for g in groups)


def test_merge_moves_points_to_a_customer_without_any_and_drops_a_said_different_pair(client):
    name = "ترمه همنام‌جی‌نه"
    d, e = (_customer(client, name, f"0915109002{i}") for i in (1, 2))
    assert client.post("/api/customers/not-same", json={"ids": [d, e]}).json()["pairs"] == 1
    _set_points(e, 40)
    r = client.post("/api/customers/merge", json={"keep_id": d, "drop_ids": [e]})  # merged after all
    assert r.status_code == 200, r.text
    assert _points(d) == 40 and _points(e) is None
    with SessionLocal() as db:
        assert not any(str(e) in p.split("-") for p in customer_merge.not_same_pairs(db))


def test_forget_drops_what_is_remembered_for_deleted_customers():
    gone, other = 9_910_001, 9_910_002  # ids no real customer has
    with SessionLocal() as db:
        db.add_all([KnowledgeItem(kind="loyalty", key=str(gone), value={"points": 9}),
                    KnowledgeItem(kind="loyalty", key=str(other), value={"points": 2}),
                    KnowledgeItem(kind="customer_not_same", key=f"{gone}-{other}", value={})])
        db.commit()
        assert customer_merge.forget(db, [gone]) == 2
        db.commit()
    assert _points(gone) is None and _points(other) == 2
    with SessionLocal() as db:
        assert f"{gone}-{other}" not in customer_merge.not_same_pairs(db)
        customer_merge.forget(db, [other])
        db.commit()


def test_database_page_counts_the_newer_tables(client):
    st = client.get("/api/admin/data").json()
    for t in ("products", "purchases", "suppliers", "trade_docs", "woo_orders", "capital_moves", "holdings"):
        assert t in st["stats"], t
    assert "سرمایه" in st["scopes"]["transactions"] and "سفارش‌های سایت" in st["scopes"]["transactions"]


def test_users_page_calls_lock_unlock_edit_and_own_password(client):
    """What the users table in Settings calls: the «قفل» badge, «باز کردن قفل», the edit window, «رمز عبور من»."""
    from datetime import timedelta

    from app.models import User, local_now

    r = client.post("/api/auth/users", json={"username": "g9-lock-user", "full_name": "همکار g9", "password": "G9secret1",
                                              "role": "receptionist"})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    with SessionLocal() as db:  # closed after wrong passwords
        db.get(User, uid).locked_until = local_now() + timedelta(minutes=10)
        db.commit()
    row = next(x for x in client.get("/api/auth/users").json() if x["id"] == uid)
    assert row["locked"] is True
    assert client.post(f"/api/auth/users/{uid}/unlock").json()["locked"] is False
    assert next(x for x in client.get("/api/auth/users").json() if x["id"] == uid)["locked"] is False
    # their own password, from «رمز عبور من»: the answer carries the new login of this device
    tok = client.post("/api/auth/login", json={"username": "g9-lock-user", "password": "G9secret1"}).json()["access_token"]
    mine = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/auth/change-password", json={"current_password": "G9secret1", "new_password": "G9secret2"}, headers=mine)
    assert r.status_code == 200, r.text
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()["id"] == uid
    assert client.get("/api/auth/me", headers=mine).status_code == 401  # the old login ends
    # the edit window (owner): new password and not active any more
    body = {"username": "g9-lock-user", "full_name": "همکار g9", "role": "staff", "is_active": False, "password": "G9secret3"}
    r = client.put(f"/api/auth/users/{uid}", json=body)
    assert r.status_code == 200, r.text
    assert (r.json()["role"], r.json()["is_active"], "access_token" in r.json()) == ("staff", False, False)
    assert client.post("/api/auth/login", json={"username": "g9-lock-user", "password": "G9secret3"}).status_code == 401


def test_unknown_scope_is_refused():
    with SessionLocal() as db, pytest.raises(ValueError):
        maintenance.reset(db, "nope")
