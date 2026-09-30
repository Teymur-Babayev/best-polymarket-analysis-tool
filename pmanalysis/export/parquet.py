"""Export resolved windows to Parquet and CSV."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from pmanalysis.config import ARCHIVE_DIR, EXPORT_DIR
from pmanalysis.db.models import Indicator, Tick, Window


def _window_frame(session: Session, window: Window) -> pd.DataFrame:
    ticks = (
        session.query(Tick)
        .filter_by(window_id=window.id)
        .order_by(Tick.ts_ms)
        .all()
    )
    indicators = {
        row.ts_ms: row
        for row in session.query(Indicator).filter_by(window_id=window.id).all()
    }
    rows = []
    for tick in ticks:
        ind = indicators.get(tick.ts_ms)
        rows.append({
            "ts_ms": tick.ts_ms,
            "slug": window.slug,
            "asset": window.asset,
            "interval": window.interval,
            "oracle_price": tick.oracle_price,
            "binance_price": tick.binance_price,
            "yes_ask": tick.yes_ask,
            "yes_bid": tick.yes_bid,
            "no_ask": tick.no_ask,
            "no_bid": tick.no_bid,
            "yes_mid": tick.yes_mid,
            "no_mid": tick.no_mid,
            "combined_ask": tick.combined_ask,
            "yes_spread": tick.yes_spread,
            "secs_remaining": tick.secs_remaining,
            "dist_from_strike": tick.dist_from_strike,
            "strike_price": window.strike_price,
            "implied_yes_prob": ind.implied_yes_prob if ind else None,
            "arb_edge": ind.arb_edge if ind else None,
            "lean": ind.lean if ind else None,
            "momentum_1m": ind.momentum_1m if ind else None,
            "volatility_5m": ind.volatility_5m if ind else None,
            "twap_oracle": getattr(ind, "twap_oracle", None) if ind else None,
            "outcome": window.outcome,
        })
    return pd.DataFrame(rows)


def export_window_parquet(session: Session, window: Window) -> Path:
    frame = _window_frame(session, window)
    out_dir = ARCHIVE_DIR / window.asset.lower() / window.interval
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{window.slug}.parquet"
    frame.to_parquet(out_path, index=False)
    window.archived = True
    session.add(window)
    return out_path


def export_window_csv(session: Session, window: Window) -> Path:
    frame = _window_frame(session, window)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EXPORT_DIR / f"{window.slug}.csv"
    frame.to_csv(out_path, index=False)
    return out_path


def archive_path_for(asset: str, interval: str, slug: str) -> Path:
    return ARCHIVE_DIR / asset.lower() / interval / f"{slug}.parquet"


def load_window_frame(window: Window) -> pd.DataFrame | None:
    """Read a finalized window's full tick history back from its parquet archive.

    Used as the backtest data source once live SQLite ticks/indicators have been
    pruned for storage/perf reasons (see db/cleanup.py retention job) — the archive
    is written once per window at finalize time and never touched again.
    """
    path = archive_path_for(window.asset, window.interval, window.slug)
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None
