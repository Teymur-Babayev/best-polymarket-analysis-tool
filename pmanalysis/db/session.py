"""Database engine and session helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TypeVar

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from pmanalysis.config import DB_PATH, MARKET_SLUGS, ensure_data_dirs
from pmanalysis.db.models import Base, Indicator, Market, Tick, Window

_engine = None
_SessionLocal = None
T = TypeVar("T")


def _sqlite_url() -> str:
    ensure_data_dirs()
    return f"sqlite:///{DB_PATH.as_posix()}"


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        # NullPool: open/close per session — avoids SQLite pool exhaustion under async load.
        _engine = create_engine(
            _sqlite_url(),
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def _migrate_columns(engine) -> None:
    """Add columns introduced after initial deploy (SQLite has no ALTER IF NOT EXISTS)."""
    with engine.begin() as conn:
        window_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(windows)")).fetchall()}
        if "opening_oracle_price" not in window_cols:
            conn.execute(text("ALTER TABLE windows ADD COLUMN opening_oracle_price FLOAT"))

        tick_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(ticks)")).fetchall()}
        if "binance_price" not in tick_cols:
            conn.execute(text("ALTER TABLE ticks ADD COLUMN binance_price FLOAT"))

        indicator_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(indicators)")).fetchall()
        }
        if "twap_oracle" not in indicator_cols:
            conn.execute(text("ALTER TABLE indicators ADD COLUMN twap_oracle FLOAT"))


def _purge_retired_intervals(session: Session) -> None:
    """Remove markets/intervals no longer tracked (e.g. retired 1h)."""
    active = {interval for _, interval, _ in MARKET_SLUGS}
    retired = (
        session.query(Market.interval)
        .distinct()
        .all()
    )
    for (interval,) in retired:
        if interval in active:
            continue
        window_ids = [
            row[0]
            for row in session.query(Window.id).filter(Window.interval == interval).all()
        ]
        if window_ids:
            session.query(Tick).filter(Tick.window_id.in_(window_ids)).delete(
                synchronize_session=False
            )
            session.query(Indicator).filter(Indicator.window_id.in_(window_ids)).delete(
                synchronize_session=False
            )
        session.query(Window).filter(Window.interval == interval).delete()
        session.query(Market).filter(Market.interval == interval).delete()


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_columns(engine)
    with get_session() as session:
        _purge_retired_intervals(session)
        for asset, interval, base_slug in MARKET_SLUGS:
            exists = session.query(Market).filter_by(base_slug=base_slug).first()
            if not exists:
                session.add(Market(asset=asset, interval=interval, base_slug=base_slug))
        session.commit()


@contextmanager
def get_session() -> Generator[Session, None, None]:
    get_engine()
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def run_db(fn: Callable[[], T]) -> T:
    """Run blocking DB work off the asyncio event loop."""
    return await asyncio.to_thread(fn)


def db_health() -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    size_mb = round(DB_PATH.stat().st_size / (1024 * 1024), 2) if DB_PATH.exists() else 0.0
    return {"path": str(DB_PATH), "size_mb": size_mb, "ok": True}
