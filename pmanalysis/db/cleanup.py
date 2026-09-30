"""Remove corrupt tick rows from the database; prune old ticks once archived."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text, tuple_

from pmanalysis.db.models import Indicator, Tick, Window
from pmanalysis.db.session import get_session

log = logging.getLogger(__name__)


def remove_impossible_quote_ticks(*, ask_threshold: float = 90.0) -> dict:
    """Delete ticks where both YES and NO ask are >= threshold (impossible quotes)."""
    with get_session() as session:
        bad_rows = (
            session.query(Tick.id, Tick.window_id, Tick.ts_ms)
            .filter(Tick.yes_ask >= ask_threshold, Tick.no_ask >= ask_threshold)
            .all()
        )
        if not bad_rows:
            return {
                "ticks_removed": 0,
                "indicators_removed": 0,
                "windows_affected": 0,
            }

        tick_ids = [row[0] for row in bad_rows]
        pairs = list({(row[1], row[2]) for row in bad_rows})
        windows_affected = len({row[1] for row in bad_rows})

        indicators_removed = (
            session.query(Indicator)
            .filter(tuple_(Indicator.window_id, Indicator.ts_ms).in_(pairs))
            .delete(synchronize_session=False)
        )
        ticks_removed = (
            session.query(Tick)
            .filter(Tick.id.in_(tick_ids))
            .delete(synchronize_session=False)
        )
        session.commit()

    return {
        "ticks_removed": ticks_removed,
        "indicators_removed": indicators_removed,
        "windows_affected": windows_affected,
    }


def purge_archived_ticks(*, retention_hours: int = 72, batch_size: int = 25) -> dict:
    """Delete Tick/Indicator rows for windows already archived to parquet.

    Only windows that are (a) marked archived=True (parquet file written by
    export_window_parquet) and (b) resolved more than `retention_hours` ago are
    touched, so the "recent previews" UI (last handful of windows per market)
    and any in-flight backtest against fresh data are never affected. Deletes
    run in small window batches to avoid long write locks against the live
    collector.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
    with get_session() as session:
        window_ids = [
            row[0]
            for row in session.query(Window.id)
            .filter(Window.archived.is_(True), Window.resolved_at < cutoff)
            .all()
        ]

    ticks_removed = 0
    indicators_removed = 0
    windows_affected = 0
    for offset in range(0, len(window_ids), batch_size):
        batch = window_ids[offset : offset + batch_size]
        with get_session() as session:
            n_ind = (
                session.query(Indicator)
                .filter(Indicator.window_id.in_(batch))
                .delete(synchronize_session=False)
            )
            n_tick = (
                session.query(Tick)
                .filter(Tick.window_id.in_(batch))
                .delete(synchronize_session=False)
            )
            session.commit()
        indicators_removed += n_ind
        ticks_removed += n_tick
        if n_tick or n_ind:
            windows_affected += len(batch)

    if ticks_removed or indicators_removed:
        log.info(
            "Retention purge: %d ticks, %d indicators removed across %d windows (archived, >%dh old)",
            ticks_removed,
            indicators_removed,
            windows_affected,
            retention_hours,
        )

    return {
        "ticks_removed": ticks_removed,
        "indicators_removed": indicators_removed,
        "windows_affected": windows_affected,
    }


def checkpoint_wal() -> None:
    """Flush the WAL back into the main DB file so it doesn't grow unbounded."""
    from pmanalysis.db.session import get_engine

    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
