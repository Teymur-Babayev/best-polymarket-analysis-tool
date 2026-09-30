"""Reset collected data for a fresh professional dataset."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import text

from pmanalysis.config import ARCHIVE_DIR, DB_PATH, EXPORT_DIR
from pmanalysis.db.models import (
    BinanceTrade,
    Candle,
    ClobQuote,
    CollectorGap,
    Indicator,
    OracleTick,
    Tick,
    Window,
)
from pmanalysis.db.session import get_engine, get_session, init_db


def _remove_tree_contents(path: Path) -> int:
    """Delete files and subdirs under path; keep the root directory."""
    if not path.exists():
        return 0
    removed = 0
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
        removed += 1
    return removed


def clear_database(*, include_archives: bool = True) -> dict:
    """
    Wipe all collected market data and start fresh.

    Keeps the `markets` seed table (BTC/ETH/SOL definitions).
    Stops are not required if no other process holds the DB, but restart
    the collector after clearing for a clean in-memory state.
    """
    init_db()
    counts: dict[str, int] = {}

    with get_session() as session:
        for model, name in (
            (Tick, "ticks"),
            (Indicator, "indicators"),
            (BinanceTrade, "binance_trades"),
            (OracleTick, "oracle_ticks"),
            (ClobQuote, "clob_quotes"),
            (Window, "windows"),
            (Candle, "candles"),
            (CollectorGap, "collector_gaps"),
        ):
            counts[name] = session.query(model).delete()
        session.commit()

    engine = get_engine()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM"))

    archive_removed = 0
    export_removed = 0
    if include_archives:
        archive_removed = _remove_tree_contents(ARCHIVE_DIR)
        export_removed = _remove_tree_contents(EXPORT_DIR)

    try:
        from pmanalysis.core.live_store import get_live_store
        from pmanalysis.web.sync import reset_history_load_cache

        reset_history_load_cache()
        store = get_live_store()
        store.markets.clear()
        store.history.clear()
        store.oracle_prices.clear()
        store.binance_prices.clear()
        store._tick_queue.clear()
        store.seq = 0
    except Exception:
        pass

    size_mb = round(DB_PATH.stat().st_size / (1024 * 1024), 2) if DB_PATH.exists() else 0.0

    return {
        "cleared": counts,
        "archive_entries_removed": archive_removed,
        "export_entries_removed": export_removed,
        "db_path": str(DB_PATH),
        "db_size_mb": size_mb,
    }
