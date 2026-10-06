from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, future=True, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")  # crash-safe, concurrent reads
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA synchronous=FULL")  # money data: never lose a committed transaction
            cur.execute("PRAGMA busy_timeout=8000")  # wait instead of failing when a backup/report is writing
            cur.execute("PRAGMA cache_size=-65536")  # 64 MB page cache - keeps large reports fast
            cur.execute("PRAGMA temp_store=MEMORY")
            cur.execute("PRAGMA mmap_size=268435456")
            cur.close()
    return engine


engine = _make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def reconfigure(url: str) -> None:
    """Point the app at another database (used by tests and restore)."""
    global engine
    engine.dispose()
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
