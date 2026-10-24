"""Starting the new version on a database made by an older one: new columns appear, old figures are worked out
again once, nothing crashes and the books still balance."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

SETUP = r'''
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as c:
    t = c.post("/api/auth/setup", json={"username": "owner", "password": "Secret123", "full_name": "x", "salon_name": "x"}).json()
    c.headers["Authorization"] = "Bearer " + t["access_token"]
    cash = [a for a in c.get("/api/accounts").json() if a["kind"] == "cash"][0]["id"]
    p = c.post("/api/products", json={"name": "کرم ارتقا", "sale_price": 300000}).json()
    d = (datetime.now() - timedelta(days=3)).replace(microsecond=0)
    c.post(f"/api/products/{p['id']}/opening", json={"qty": 2, "unit_cost": 100000, "at": d.isoformat()})
    inv = c.post("/api/invoices", json={"customer_name": "مشتری ارتقا", "customer_mobile": "09159991111",
                                       "items": [{"product_id": p["id"], "quantity": 1}], "issued_at": (d + timedelta(days=1)).isoformat(),
                                       "payments": [{"payment_account_id": cash, "amount": 300000}]}).json()
    assert c.post(f"/api/invoices/{inv['id']}/void?payments=cancel").status_code == 200
    c.post("/api/deposits", json={"customer_name": "مشتری ارتقا", "customer_mobile": "09159991111", "amount": 50000,
                                  "payment_account_id": cash, "notes": "ماندهٔ بستانکار تیزپرداز"})
from app.core.db import SessionLocal
from app.models import Deposit
with SessionLocal() as db:  # 1.19 split a partly used credit: the rest has only "باقیمانده بیعانه #id" in its notes
    d = db.query(Deposit).first()
    rest = Deposit(customer_id=d.customer_id, amount=20000, payment_account_id=d.payment_account_id, received_at=d.received_at,
                   source="import", notes=f"باقیمانده بیعانه #{d.id}")
    db.add(rest)
    db.flush()
    db.add(Deposit(customer_id=d.customer_id, amount=5000, payment_account_id=d.payment_account_id, received_at=d.received_at,
                   source="import", notes=f"باقیمانده بیعانه #{rest.id}"))
    db.commit()
from app.core.db import SessionLocal
from app.models import Deposit, Product
from app.services import settings_store
with SessionLocal() as db:  # as an older version left it
    settings_store.set_value(db, "products.engine", 1)
    db.query(Product).update({Product.stock_value: 123})
    db.query(Deposit).update({Deposit.source: "import"})
    db.commit()
from app.models import BankTransaction, PaymentAccount, Setting
with SessionLocal() as db:  # 1.22 kept bank lines as they came (whole card numbers), a plain token, row-index statement ids
    pa = PaymentAccount(kind="card", name="کارت ارتقا", card_mask="603799******1111", provider="generic_http",
                        provider_config={"url": "https://bank.example/{account}", "token": "plain-token-xyz"})
    db.add(pa)
    db.flush()
    old = datetime.now() - timedelta(days=100)
    db.add_all([
        BankTransaction(payment_account_id=pa.id, external_id="stmt-202601011000-5000000-0", amount=5000000, direction="out",
                        occurred_at=datetime.now() - timedelta(days=2), source="statement",
                        description="انتقال کارت به کارت به 6219861012345673 بنام زهرا احمدی"),
        BankTransaction(payment_account_id=pa.id, external_id="api-1", amount=900000, direction="out", occurred_at=old,
                        source="bank_api", description="برداشت", counterparty_card="6219861012345673"),
    ])
    db.query(Setting).filter(Setting.key.in_(["privacy.sealed_1_23", "bank.key_check"])).delete()
    db.commit()
'''

START = r'''
from app.main import init_db
init_db()
init_db()  # a second start changes nothing
'''


def _run(code: str, env: dict) -> None:
    r = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]


def test_start_on_an_older_database(tmp_path):
    db = tmp_path / "old.db"
    env = {**os.environ, "HESABDAR_DATABASE_URL": f"sqlite:///{db}", "HESABDAR_BACKUP_DIR": str(tmp_path / "backups"),
           "HESABDAR_ENVIRONMENT": "test", "HESABDAR_SECRET_KEY": "upgrade-test-secret-0123456789-0123456789",
           "HESABDAR_FRONTEND_DIST": str(tmp_path / "no-dist"), "HESABDAR_AI_ENABLED": "false"}
    _run(SETUP, env)
    con = sqlite3.connect(db)
    for table, col in (("users", "token_version"), ("trade_history", "net_amount"), ("deposits", "kind"),
                       ("invoice_items", "treasury_bp"), ("invoice_items", "treasury_amount"), ("woo_orders", "site")):
        if col in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
    con.execute("DROP TABLE IF EXISTS treasury_rates")
    con.commit()
    con.close()
    _run(START, env)
    con = sqlite3.connect(db)
    try:
        for table, col in (("users", "token_version"), ("trade_history", "net_amount"), ("deposits", "kind"),
                       ("invoice_items", "treasury_bp"), ("invoice_items", "treasury_amount"), ("woo_orders", "site")):
            assert col in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}, (table, col)
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'treasury_rates'").fetchone()[0] == 1
        dr, cr = con.execute("SELECT SUM(debit), SUM(credit) FROM journal_lines").fetchone()
        assert dr == cr
        ledger_1300 = con.execute("SELECT COALESCE(SUM(l.debit - l.credit), 0) FROM journal_lines l JOIN ledger_accounts a "
                                  "ON a.id = l.account_id WHERE a.code = '1300'").fetchone()[0]
        assert con.execute("SELECT SUM(stock_value) FROM products").fetchone()[0] == ledger_1300 == 200_000
        # a Tizpardaz balance is a credit, and so is what is left of it after it was split (twice)
        assert [k for (k,) in con.execute("SELECT kind FROM deposits ORDER BY id")] == ["credit", "credit", "credit"]
        # 1.23: no whole card number left, the other side known by hash + mask, the token encrypted, an old statement
        # row's id in the new form (the same file downloaded again is the same line), old money out not in the inbox
        rows = con.execute("SELECT external_id, description, counterparty_card, counterparty_hash, counterparty_mask, status, source "
                           "FROM bank_transactions ORDER BY id").fetchall()
        assert all("6219861012345673" not in (r[1] or "") + (r[2] or "") for r in rows)
        stmt = next(r for r in rows if r[6] == "statement")
        assert stmt[0].startswith("stmt-") and not stmt[0].startswith("stmt-2026") and stmt[3] and stmt[4].endswith("5673")
        assert stmt[5] == "unmatched"
        assert next(r for r in rows if r[6] == "bank_api")[5] == "ignored"
        token = con.execute("SELECT provider_config FROM payment_accounts WHERE name = 'کارت ارتقا'").fetchone()[0]
        assert "plain-token-xyz" not in token and '"enc:' in token
    finally:
        con.close()
