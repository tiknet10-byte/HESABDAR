from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import auth, catalog, customers, finance, system
from .core import db as dbmod
from .core.config import get_settings
from .core.db import Base, SessionLocal
from .plugins.manager import manager
from .seed import seed_base
from .services import backup, settings_store

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
    with SessionLocal() as db:
        seed_base(db)
        db.commit()


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

    for r in (auth.router, catalog.router, customers.router, finance.router, system.router):
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
            if path and f.is_file() and dist.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
