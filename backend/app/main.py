from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import auth, catalog, customers, finance, system
from .core import db as dbmod
from .core.config import get_settings
from .core.db import Base, SessionLocal
from .plugins.manager import manager
from .seed import seed_base
from .services import backup, settings_store

log = logging.getLogger("hesabdar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(dbmod.engine)
    with SessionLocal() as db:
        seed_base(db)
        db.commit()


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
    app = FastAPI(title="Hesabdar - Beauty Salon Accounting", version="1.0.0", lifespan=lifespan,
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
