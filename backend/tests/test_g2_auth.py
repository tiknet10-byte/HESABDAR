"""Login, tokens, Host check, audit chain and AI permissions (group G2). Users start with g2_, mobiles with 0915102."""
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.api.auth import BAD_LOGIN
from app.core import db as dbmod
from app.core.security import create_access_token, host_allowed
from app.main import app


def _user(client, name, role="receptionist", password="Passw0rd1"):
    r = client.post("/api/auth/users", json={"username": name, "password": password, "role": role})
    assert r.status_code == 200, r.text
    return r.json()


def _login(name, password="Passw0rd1"):
    return TestClient(app).post("/api/auth/login", json={"username": name, "password": password})


def _as(token):
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


@contextmanager
def _fresh_db():
    """An empty database of our own (setup and the audit chain need one), served instead of the shared one."""
    from app import models  # noqa: F401
    from app.core.db import Base, get_db

    eng = dbmod._make_engine(f"sqlite:///{tempfile.mkdtemp(prefix='g2-')}/fresh.db")
    Base.metadata.create_all(eng)
    make = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

    def _get_db():
        db = make()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    try:
        yield make
    finally:
        app.dependency_overrides.pop(get_db, None)
        eng.dispose()


# ---------------------------------------------------------------- login
def test_refused_logins_all_look_the_same_and_owner_can_unlock(client):
    u = _user(client, "g2_lock")
    unknown = _login("g2_nobody_here", "Whatever1")
    assert unknown.status_code == 401 and unknown.json()["detail"] == BAD_LOGIN
    for _ in range(5):
        r = _login("g2_lock", "WrongPass9")
        assert r.status_code == 401 and r.json()["detail"] == BAD_LOGIN
    # locked now: even the right password gets exactly the same answer (no 423 telling the name exists)
    r = _login("g2_lock")
    assert r.status_code == 401 and r.json()["detail"] == BAD_LOGIN
    assert next(x for x in client.get("/api/auth/users").json() if x["id"] == u["id"])["locked"] is True
    # the owner opens it without waiting
    assert client.post(f"/api/auth/users/{u['id']}/unlock").json()["locked"] is False
    assert _login("g2_lock").status_code == 200


def test_owner_saving_a_user_opens_the_lock(client):
    u = _user(client, "g2_lock2")
    for _ in range(5):
        _login("g2_lock2", "WrongPass9")
    assert _login("g2_lock2").status_code == 401
    r = client.put(f"/api/auth/users/{u['id']}", json={"username": "g2_lock2", "full_name": "x", "role": "receptionist",
                                                       "is_active": True, "password": "NewPassw0rd"})
    assert r.status_code == 200 and r.json()["locked"] is False and "access_token" not in r.json()
    assert _login("g2_lock2", "NewPassw0rd").status_code == 200


def test_tries_at_the_same_moment_still_lock_the_account(client):
    """Before: 30 wrong passwords at once left failed_logins=3 and no lock (each request wrote its stale count + 1)."""
    from app.models import AuditLog, User

    u = _user(client, "g2_race")
    with ThreadPoolExecutor(20) as pool:
        codes = list(pool.map(lambda _: _login("g2_race", "WrongPass9").status_code, range(20)))
    assert codes == [401] * 20
    with dbmod.SessionLocal() as db:
        assert db.get(User, u["id"]).locked_until is not None
    assert _login("g2_race").status_code == 401  # right password, but locked
    assert next(x for x in client.get("/api/auth/users").json() if x["id"] == u["id"])["locked"] is True
    with dbmod.SessionLocal() as db:
        n = db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "auth.login_failed", AuditLog.entity_id == str(u["id"])))
        assert n == 21  # every try is in the audit log
    assert client.get("/api/audit/verify").json()["ok"]  # and the chain was not broken by the parallel writes


def test_username_is_trimmed_and_small_letters(client):
    r = client.post("/api/auth/users", json={"username": "  G2_Space ", "password": "Passw0rd1", "role": "staff"})
    assert r.status_code == 200 and r.json()["username"] == "g2_space"
    again = client.post("/api/auth/users", json={"username": "g2_SPACE ", "password": "Passw0rd1", "role": "staff"})
    assert again.status_code == 400 and again.json()["detail"] == "این نام کاربری وجود دارد"  # was a 500 with SQL in it
    assert client.post("/api/auth/users", json={"username": "   ", "password": "Passw0rd1", "role": "staff"}).status_code == 400
    assert _login(" G2_SPACE  ").status_code == 200


# ---------------------------------------------------------------- tokens
def test_new_password_or_deactivation_ends_old_logins(client):
    u = _user(client, "g2_tok")
    t1 = _login("g2_tok").json()["access_token"]
    assert _as(t1).get("/api/auth/me").status_code == 200
    r = _as(t1).post("/api/auth/change-password", json={"current_password": "Passw0rd1", "new_password": "Changed123"})
    assert r.status_code == 200
    t2 = r.json()["access_token"]
    assert _as(t1).get("/api/auth/me").status_code == 401  # the old token (maybe copied elsewhere) is dead
    assert _as(t2).get("/api/auth/me").status_code == 200  # this device stays in
    # the owner sets a new password: every login of that user ends
    client.put(f"/api/auth/users/{u['id']}", json={"username": "g2_tok", "role": "receptionist", "is_active": True,
                                                   "password": "ByOwner123"})
    assert _as(t2).get("/api/auth/me").status_code == 401
    t3 = _login("g2_tok", "ByOwner123").json()["access_token"]
    assert _as(t3).get("/api/customers").status_code == 200
    # deactivated, then active again: the old token does not come back to life
    body = {"username": "g2_tok", "role": "receptionist", "is_active": False}
    client.put(f"/api/auth/users/{u['id']}", json=body)
    client.put(f"/api/auth/users/{u['id']}", json=body | {"is_active": True})
    assert _as(t3).get("/api/auth/me").status_code == 401
    assert _login("g2_tok", "ByOwner123").status_code == 200
    # a role change alone keeps the login (the role is read from the database on every request)
    t4 = _login("g2_tok", "ByOwner123").json()["access_token"]
    client.put(f"/api/auth/users/{u['id']}", json=body | {"is_active": True, "role": "staff"})
    assert _as(t4).get("/api/auth/me").json()["role"] == "staff"


def test_tokens_from_before_the_update_still_work(client):
    """Tokens made before token_version existed have no 'ver': they count as 0, like a user who never changed."""
    import jwt
    from app.core.config import get_settings

    u = _user(client, "g2_oldtok")
    tok = create_access_token(u["id"], u["role"])
    payload = jwt.decode(tok, get_settings().secret_key, algorithms=["HS256"])
    payload.pop("ver")
    old = jwt.encode(payload, get_settings().secret_key, algorithm="HS256")
    assert _as(old).get("/api/auth/me").status_code == 200


def test_owner_new_own_password_keeps_owner_logged_in(client):
    me = _user(client, "g2_owner2", role="owner", password="Owner1234")  # not the shared test owner
    old = _login("g2_owner2", "Owner1234").json()["access_token"]
    r = _as(old).put(f"/api/auth/users/{me['id']}", json={"username": "g2_owner2", "role": "owner", "is_active": True,
                                                          "password": "Owner5678"})
    assert r.status_code == 200 and r.json()["access_token"]
    assert _as(r.json()["access_token"]).get("/api/auth/me").status_code == 200  # this page goes on
    assert _as(old).get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------- first-run setup
def test_setup_at_the_same_moment_makes_one_owner(client):
    """Before: 8 setups at once all returned 200 and left 8 owners."""
    from app.models import User

    with _fresh_db() as make:
        def go(i):
            return TestClient(app).post("/api/auth/setup", json={"username": f"g2_owner{i}", "password": "Secret123"}).status_code
        with ThreadPoolExecutor(8) as pool:
            codes = list(pool.map(go, range(8)))
        assert sorted(codes) == [200] + [400] * 7
        with make() as db:
            assert db.scalar(select(func.count(User.id))) == 1
            assert db.scalar(select(func.count(User.id)).where(User.role == "owner")) == 1


def test_setup_name_is_trimmed(client):
    from app.models import User

    with _fresh_db() as make:
        assert TestClient(app).post("/api/auth/setup", json={"username": " ", "password": "Secret123"}).status_code == 400
        r = TestClient(app).post("/api/auth/setup", json={"username": " G2_Boss ", "password": "Secret123"})
        assert r.status_code == 200 and r.json()["user"]["username"] == "g2_boss"
        with make() as db:
            assert [u.username for u in db.scalars(select(User))] == ["g2_boss"]


# ---------------------------------------------------------------- errors and Host check
def test_unexpected_error_shows_a_code_not_the_details(client, caplog):
    def boom():
        raise RuntimeError("[SQL: INSERT INTO users ...] [parameters: ('$argon2id$v=19$secret',)]")

    app.add_api_route("/api/_g2_boom", boom, methods=["GET"])
    r = TestClient(app, raise_server_exceptions=False).get("/api/_g2_boom")
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert "argon2" not in detail and "SQL" not in detail and "RuntimeError" not in detail
    code = detail.split("کد ")[1].split(")")[0]
    assert len(code) == 6
    assert any(code in rec.getMessage() and rec.exc_info for rec in caplog.records)  # the real error is in the log


def test_host_check_blocks_other_domain_names(client, monkeypatch):
    from app.core.config import get_settings

    c = TestClient(app)
    assert c.get("/api/health").status_code == 200  # TestClient's own "testserver" (test mode only)
    for host in ("127.0.0.1:8000", "localhost:8000", "LOCALHOST", "[::1]:8000", "192.168.1.20:8000", "10.0.0.5"):
        assert c.get("/api/health", headers={"host": host}).status_code == 200, host
    for host in ("attacker.example", "attacker.example:8000", "127.0.0.1.attacker.example", "salon-pc:8000"):
        r = c.get("/api/auth/status", headers={"host": host})
        assert r.status_code == 400 and "127.0.0.1" in r.json()["detail"], host
    assert c.post("/api/auth/login", json={"username": "owner", "password": "x"}, headers={"host": "evil.example"}).status_code == 400
    monkeypatch.setattr(get_settings(), "allowed_hosts", "Salon-PC, hesabdar.example.com")
    assert c.get("/api/health", headers={"host": "salon-pc:8000"}).status_code == 200
    assert c.get("/api/health", headers={"host": "hesabdar.example.com"}).status_code == 200
    assert c.get("/api/health", headers={"host": "evil.example"}).status_code == 400
    monkeypatch.setattr(get_settings(), "environment", "local")
    assert not host_allowed("testserver")  # only the test client may use that name
    assert host_allowed("127.0.0.1:8000") and host_allowed(None)


# ---------------------------------------------------------------- audit chain
def test_audit_chain_stays_whole_with_parallel_writes_and_reports_every_break():
    from app.models import AuditLog
    from app.services.audit import audit, verify_audit_chain

    with _fresh_db() as make:
        def one(i):
            with make() as db:
                audit(db, "g2.parallel", "test", i)
                db.commit()
        with ThreadPoolExecutor(16) as pool:
            list(pool.map(one, range(36)))
        with make() as db:  # several audits in one request before anything is written (e.g. a deposit refund that
            for i in range(4):  # also cancels its appointment): each links to the one before, not all to the same row
                audit(db, "g2.same_request", "test", i)
            db.commit()
        with make() as db:
            assert verify_audit_chain(db) == {"ok": True, "checked": 40}
            rows = db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
            assert len({r.prev_hash for r in rows}) == 40  # no two rows linked to the same one
            assert [r.action for r in rows[-4:]] == ["g2.same_request"] * 4
            # two edits far apart: both are reported, not only the first
            rows[5].action, rows[30].action = "edited", "edited"
            db.commit()
            out = verify_audit_chain(db)
            assert out["ok"] is False and out["broken_at"] == rows[5].id and out["breaks"] == [rows[5].id, rows[30].id]
            assert out["checked"] == 40


def test_old_bug_links_are_mended_once_but_real_edits_stay_reported(client):
    from app.models import AuditLog, Setting, local_now
    from app.services import settings_store
    from app.services.audit import RECHAIN_FLAG, RECHAIN_MARK, _digest, audit, rechain_once, verify_audit_chain

    # the shared database went through start-up: the flag is set, and a second run does nothing
    with dbmod.SessionLocal() as db:
        assert settings_store.get(db, RECHAIN_FLAG) == RECHAIN_MARK
        assert rechain_once(db) == []

    with _fresh_db() as make:
        with make() as db:
            def add(n, action="g2.rechain"):
                for i in range(n):
                    audit(db, action, "test", i)
                    db.commit()
            add(3)
            # what the old bug wrote: two records of one request, both linked to the same earlier record
            last = db.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
            pair = [AuditLog(at=local_now(), actor="system", action="g2.old_bug", entity="test", entity_id=str(i), data={"i": i},
                             prev_hash=last.hash) for i in range(2)]
            for row in pair:
                row.hash = _digest(row.prev_hash, row)
                db.add(row)
            db.commit()
            add(7)
            rows = db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
            assert len(rows) == 12
            edited, deleted, after_deleted = rows[7], rows[9], rows[10]
            edited.data = {"amount": 1}  # changed by hand: its content no longer matches its hash
            db.delete(deleted)  # a record removed: the next one points to a record that is gone
            db.commit()
            before = verify_audit_chain(db)
            assert before["breaks"] == [pair[1].id, edited.id, after_deleted.id]

            mended = rechain_once(db)
            assert mended == [pair[1].id, rows[5].id, rows[6].id]  # the bug's record and the ones after it, up to the edit
            out = verify_audit_chain(db)
            assert out["ok"] is False and out["breaks"] == [edited.id, after_deleted.id]  # real changes are still shown
            assert out["checked"] == 12  # 11 left + the record of the mending itself
            note = db.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
            assert note.action == "audit.rechain" and note.data["mended"] == 3 and note.data["ids"] == mended
            assert settings_store.get(db, RECHAIN_FLAG) == RECHAIN_MARK
            assert rechain_once(db) == []  # only once
            # even without the flag nothing more is mended: the edit and the deletion are never hidden
            db.delete(db.get(Setting, RECHAIN_FLAG))
            db.commit()
            assert rechain_once(db) == []
            assert verify_audit_chain(db)["breaks"] == [edited.id, after_deleted.id]
            assert db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "audit.rechain")) == 1


# ---------------------------------------------------------------- AI permissions
def test_ai_agent_cannot_move_money_but_keeps_its_read_tools(client, accounts):
    _user(client, "g2_ai", role="ai_agent", password="AgentPass1")
    ai = _as(_login("g2_ai", "AgentPass1").json()["access_token"])
    acc = next(iter(accounts.values()))
    assert ai.post("/api/expenses", json={"category": "g2", "amount": 1000, "payment_account_id": acc}).status_code == 403
    tools = {t["name"]: t["permission"] for t in ai.get("/api/ai/tools").json()}
    assert tools["payment_accounts"] == "reports" and "financial_summary" in tools and "register_deposit" in tools
    assert "teach_service_alias" not in tools
    assert ai.post("/api/ai/tools/payment_accounts", json={"arguments": {}}).status_code == 200
    svc = client.get("/api/services").json()[0]
    assert ai.post("/api/ai/tools/teach_service_alias", json={"arguments": {"service_id": svc["id"], "alias": "g2x"}}).status_code == 403
    _user(client, "g2_acc", role="accountant", password="Account123")
    acct = _as(_login("g2_acc", "Account123").json()["access_token"])
    assert acct.post("/api/ai/tools/teach_service_alias", json={"arguments": {"service_id": svc["id"], "alias": "g2x"}}).status_code == 403
    r = client.post("/api/ai/tools/teach_service_alias", json={"arguments": {"service_id": svc["id"], "alias": "g2-alias"}})
    assert r.status_code == 200 and r.json()["ok"]


def test_offline_assistant_shows_money_only_to_reports_roles(client, accounts):
    # 12,345,670 Rial sale today so the 30-day summary has something to show
    cust = client.post("/api/customers", json={"full_name": "g2 مشتری دستیار", "mobile": "09151020001"}).json()
    inv = client.post("/api/invoices", json={"customer_id": cust["id"], "items": [{"description": "g2 خدمت", "unit_price": 12_345_670}],
                                             "payments": [{"payment_account_id": next(iter(accounts.values())), "amount": 12_345_670}]})
    assert inv.status_code == 200, inv.text
    s = client.get("/api/reports/summary").json()
    owner_reply = client.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "سلام"}]}).json()
    assert owner_reply["mode"] == "offline"
    assert f"درآمد {s['revenue'] // 10:,} تومان" in owner_reply["reply"] and f"سود {s['net_profit'] // 10:,} تومان" in owner_reply["reply"]

    _user(client, "g2_rec", role="receptionist", password="Recept123")
    _user(client, "g2_staff", role="staff", password="Staffer123")
    for name, pw in (("g2_rec", "Recept123"), ("g2_staff", "Staffer123")):
        c = _as(_login(name, pw).json()["access_token"])
        assert c.get("/api/reports/summary").status_code == 403
        for q in ("سلام", "سود این ماه چقدر است؟", "پیش‌بینی ماه آینده"):
            reply = c.post("/api/ai/chat", json={"messages": [{"role": "user", "content": q}]}).json()["reply"]
            assert "تومان" not in reply and "خلاصه ۳۰ روز" not in reply and "پیش‌بینی درآمد" not in reply, (name, q, reply)
            assert "فقط برای مالک، مدیر و حسابدار" in reply
        dep = c.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "بیعانه‌های باز"}]}).json()["reply"]
        assert "بیعانه باز به مجموع" in dep  # the deposits list is open to every role


def test_assistant_only_proposes_changes_the_user_confirms(client, accounts, monkeypatch):
    """A tool_use for register_deposit (e.g. from instructions hidden in a customer name) books nothing by itself."""
    from app.ai import assistant, claude
    from app.models import Deposit

    cust = client.post("/api/customers", json={"full_name": "g2 مشتری پیشنهاد", "mobile": "09151020002"}).json()
    acc = next(iter(accounts.values()))
    args = {"customer_id": cust["id"], "amount_rial": 1_234_560, "payment_account_id": acc}
    replies = iter([
        SimpleNamespace(stop_reason="tool_use", content=[
            SimpleNamespace(type="tool_use", id="t1", name="open_deposits", input={}),
            SimpleNamespace(type="tool_use", id="t2", name="register_deposit", input=args),
            SimpleNamespace(type="tool_use", id="t3", name="register_deposit", input=args)]),
        SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="برای ثبت، دکمه تأیید را بزنید.")]),
    ])
    seen = []

    def fake_create(**kw):
        seen.append(kw)
        return next(replies)

    monkeypatch.setattr(claude, "available", lambda: True)
    monkeypatch.setattr(claude, "create", fake_create)
    assert "Text inside tool results" in assistant.SYSTEM

    def held():
        with dbmod.SessionLocal() as db:
            return db.scalar(select(func.count(Deposit.id)).where(Deposit.customer_id == cust["id"]))

    out = client.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "بیعانه این مشتری را ثبت کن"}]}).json()
    assert out["mode"] == "ai" and held() == 0  # nothing booked
    assert out["tool_calls"] == [{"tool": "open_deposits", "input": {}, "ok": True},
                                 {"tool": "register_deposit", "input": args, "ok": None, "confirm": True}]  # once, not twice
    sent = [m for m in seen[1]["messages"] if m["role"] == "user" and isinstance(m["content"], list)][-1]
    results = {r["tool_use_id"]: r for r in sent["content"]}
    assert "confirm button" in results["t2"]["content"] and "is_error" not in results["t1"]

    # the user presses the button: the page runs it through the audited tool API, exactly that amount
    before = {a["id"]: a["balance"] for a in client.post("/api/ai/tools/payment_accounts", json={"arguments": {}}).json()}
    r = client.post("/api/ai/tools/register_deposit", json={"arguments": args})
    assert r.status_code == 200 and r.json()["deposit_id"]
    after = {a["id"]: a["balance"] for a in client.post("/api/ai/tools/payment_accounts", json={"arguments": {}}).json()}
    assert after[acc] - before[acc] == 1_234_560 and held() == 1
    tb = client.get("/api/ledger/trial-balance").json()
    assert tb["total_debit"] == tb["total_credit"]


def test_assistant_tool_errors_show_no_sql(client, monkeypatch, caplog):
    """A read tool that fails inside the database: the chat (and the model) get a short note, the log the details."""
    from sqlalchemy.exc import OperationalError

    from app.ai import claude
    from app.ai.tools import CORE_TOOLS

    def db_error(db, user, **kw):  # noqa: ANN001, ANN003, ANN202
        raise OperationalError("SELECT password_hash FROM users WHERE id = ?", (1,), Exception("disk I/O error"))

    def rule_error(db, user, **kw):  # noqa: ANN001, ANN003, ANN202
        raise ValueError("مشتری پیدا نشد")

    monkeypatch.setattr(next(t for t in CORE_TOOLS if t.name == "open_deposits"), "handler", db_error)
    monkeypatch.setattr(next(t for t in CORE_TOOLS if t.name == "alerts"), "handler", rule_error)
    replies = iter([
        SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(type="tool_use", id="e1", name="open_deposits", input={}),
                                                         SimpleNamespace(type="tool_use", id="e2", name="alerts", input={})]),
        SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="خطا")]),
    ])
    seen = []
    monkeypatch.setattr(claude, "available", lambda: True)
    monkeypatch.setattr(claude, "create", lambda **kw: seen.append(kw) or next(replies))
    with caplog.at_level("ERROR", logger="hesabdar.ai"):
        out = client.post("/api/ai/chat", json={"messages": [{"role": "user", "content": "بیعانه‌ها"}]}).json()
    calls = {c["tool"]: c for c in out["tool_calls"]}
    assert calls["open_deposits"]["ok"] is False and "SELECT" not in calls["open_deposits"]["error"]
    assert calls["alerts"]["error"] == "مشتری پیدا نشد"  # a rule's own words are kept
    sent = [m for m in seen[1]["messages"] if m["role"] == "user" and isinstance(m["content"], list)][-1]
    assert all("SELECT" not in r["content"] for r in sent["content"])
    assert any("open_deposits" in rec.getMessage() and rec.exc_info for rec in caplog.records)
