from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import auth, catalog, customers, finance, legacy, system
from .core import db as dbmod
from .core.config import get_settings
from .core.db import Base, SessionLocal
from .plugins.manager import manager
from .seed import seed_base
from .services import backup, codes, settings_store  # codes: gives new lines/services their code on save

log = logging.getLogger("hesabdar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _file_logging() -> None:
    """Keep a log file in data/logs so problems on a user's machine can be diagnosed."""
    from logging.handlers import RotatingFileHandler

    from .core.config import DATA_DIR
    try:
        (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(DATA_DIR / "logs" / "hesabdar.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(h)
    except OSError:
        pass


_file_logging()


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(dbmod.engine)
    _add_missing_columns()
    _ensure_indexes()
    with SessionLocal() as db:
        seed_base(db)
        from .services.accounting import assign_missing_codes, backfill_deposit_closures

        assign_missing_codes(db)  # every customer has a customer code
        codes.assign_missing(db)  # every service line and service has a code
        backfill_deposit_closures(db)  # refund date/account of deposits refunded before it was recorded
        db.commit()


# indexes for the queries that grow with the data (lists, reports, dashboards); created if missing on start-up
INDEXES = {
    "ix_deposits_closed_at": "deposits(closed_at)",
    "ix_appt_status_start": "appointments(status, start_at)",
    "ix_appt_customer_status": "appointments(customer_id, status)",
    "ix_appt_invoice": "appointments(invoice_id)",
    "ix_appt_service": "appointments(service_id)",
    "ix_appt_staff": "appointments(staff_id)",
    "ix_dep_status_received": "deposits(status, received_at)",
    "ix_dep_appointment": "deposits(appointment_id)",
    "ix_dep_applied_invoice": "deposits(applied_invoice_id)",
    "ix_inv_issued": "invoices(issued_at)",
    "ix_inv_status": "invoices(status)",
    "ix_inv_customer": "invoices(customer_id)",
    "ix_item_line": "invoice_items(line_id)",
    "ix_item_staff": "invoice_items(staff_id)",
    "ix_item_service": "invoice_items(service_id)",
    "ix_pay_invoice": "payments(invoice_id)",
    "ix_pay_paid": "payments(paid_at)",
    "ix_je_ref": "journal_entries(ref_type, ref_id)",
    "ix_cust_name": "customers(full_name)",
    "ix_cust_created": "customers(created_at)",
    "ix_exp_spent": "expenses(spent_at)",
}


def _tune_db() -> None:
    """Daily: refresh the query planner's statistics so reports stay fast as data grows (cheap, no locking)."""
    from sqlalchemy import text

    if str(dbmod.engine.url).startswith("sqlite"):
        with dbmod.engine.connect() as con:
            con.execute(text("PRAGMA optimize"))


def _ensure_indexes() -> None:
    from sqlalchemy import inspect, text

    insp = inspect(dbmod.engine)
    tables = set(insp.get_table_names())
    with dbmod.engine.begin() as con:
        for name, target in INDEXES.items():
            if target.split("(")[0] in tables:
                con.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {target}"))


def _add_missing_columns() -> None:
    """Lightweight auto-migration: add new nullable columns to existing tables after an update."""
    from sqlalchemy import inspect, text

    insp = inspect(dbmod.engine)
    with dbmod.engine.begin() as con:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing and col.nullable:
                    ddl = col.type.compile(dialect=dbmod.engine.dialect)
                    con.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))
                    log.info("added column %s.%s", table.name, col.name)


async def _periodic(name: str, interval: int, fn) -> None:  # noqa: ANN001
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(fn)
        except Exception:
            log.exception("job %s failed", name)


def _auto_backup() -> None:
    try:
        with SessionLocal() as db:
            mirror = settings_store.get(db, "backup.mirror_dir")
        backup.create_backup("auto", mirror or None)
    except backup.BackupError as exc:
        log.info("auto backup skipped: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    init_db()
    manager.sync_state()
    tasks = []
    if get_settings().environment != "test":
        s = get_settings()
        tasks.append(asyncio.create_task(_periodic("backup", s.backup_interval_hours * 3600, _auto_backup)))
        tasks.append(asyncio.create_task(_periodic("db_tune", 24 * 3600, _tune_db)))
        for name, interval, fn in manager.jobs():
            tasks.append(asyncio.create_task(_periodic(name, interval, fn)))
    yield
    for t in tasks:
        t.cancel()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Hesabdar - Beauty Salon Accounting", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # noqa: ANN001, ANN202
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=()")
        return response

    for r in (auth.router, catalog.router, customers.router, finance.router, legacy.router, system.router):
        app.include_router(r)

    manager.discover()
    manager.mount(app)

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": app.version}

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):  # noqa: ANN202
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"خطای داخلی ({type(exc).__name__}): {exc}"})

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError):  # noqa: ANN202
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    dist = s.frontend_dist
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            f = (dist / path).resolve()
            if path and f.is_file() and dist.resolve() in f.parents and f.name != "index.html":
                return FileResponse(f)
            # never cache the page itself, so a new version shows right after an update (assets are content-hashed)
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
