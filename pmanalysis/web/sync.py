"""Sync live store from DB when collector runs in a separate process."""

from __future__ import annotations

import asyncio
import logging

from pmanalysis.core.live_store import TickPoint, get_live_store, market_key
from pmanalysis.db.models import Indicator, Tick, Window
from pmanalysis.db.repository import active_window, build_market_cards, latest_oracle_prices
from pmanalysis.db.session import get_session, run_db

log = logging.getLogger(__name__)

_db_lock = asyncio.Lock()
_history_loaded_slugs: set[str] = set()


def reset_history_load_cache() -> None:
    _history_loaded_slugs.clear()


def invalidate_history_cache(asset: str, interval: str) -> None:
    prefix = f"{market_key(asset, interval)}:"
    # Materialize the matches first: difference_update(genexpr) mutates the
    # same set the generator lazily iterates, which raised "Set changed size
    # during iteration" on every window roll (silently swallowed by the
    # caller's try/except, but it meant this cache never actually got
    # invalidated on the affected roll).
    stale = [k for k in _history_loaded_slugs if k.startswith(prefix)]
    _history_loaded_slugs.difference_update(stale)


def load_history_points(session, window: Window) -> list[TickPoint]:
    """All sampled ticks for a window (YES/NO + oracle) for chart backfill."""
    ticks = (
        session.query(Tick)
        .filter_by(window_id=window.id)
        .order_by(Tick.ts_ms)
        .all()
    )
    if not ticks:
        return []
    indicators = {
        row.ts_ms: row
        for row in session.query(Indicator)
        .filter_by(window_id=window.id)
        .all()
    }
    points: list[TickPoint] = []
    for t in ticks:
        ind = indicators.get(t.ts_ms)
        points.append(
            TickPoint(
                ts_ms=t.ts_ms,
                oracle_price=t.oracle_price,
                binance_price=t.binance_price,
                yes_mid=t.yes_mid,
                no_mid=t.no_mid,
                yes_ask=t.yes_ask,
                yes_bid=t.yes_bid,
                no_ask=t.no_ask,
                no_bid=t.no_bid,
                combined_ask=t.combined_ask,
                dist_from_strike=t.dist_from_strike,
                arb_edge=ind.arb_edge if ind else None,
                lean=ind.lean if ind else None,
                twap_oracle=getattr(ind, "twap_oracle", None) if ind else None,
            )
        )
    return points


def _load_history_sync(asset: str, interval: str) -> list[TickPoint]:
    with get_session() as session:
        window = active_window(session, asset, interval)
        if not window:
            return []
        return load_history_points(session, window)


def _hydrate_snapshot_sync() -> tuple[list, dict[str, float]]:
    with get_session() as session:
        return build_market_cards(session), latest_oracle_prices(session)


async def ensure_history_loaded(asset: str, interval: str, *, force: bool = False) -> None:
    """Populate live-store chart history from DB for one market."""
    store = get_live_store()
    key = market_key(asset, interval)
    card = await store.get_market_card(asset, interval)
    slug = card.slug if card else ""
    cache_key = f"{key}:{slug}"
    if not force and cache_key in _history_loaded_slugs:
        buf = store.history.get(key)
        if buf and len(buf) > 3:
            return

    try:
        async with _db_lock:
            if cache_key in _history_loaded_slugs and not force:
                return
            points = await run_db(lambda: _load_history_sync(asset, interval))
            if points:
                await store.load_history(asset, interval, points)
            _history_loaded_slugs.add(cache_key)
    except Exception as exc:
        log.warning("load history for %s %s failed: %s", asset, interval, exc)


async def hydrate_live_store_from_db() -> None:
    """Populate in-memory live store from DB (used at startup and as WS fallback)."""
    store = get_live_store()
    try:
        cards, oracle = await run_db(_hydrate_snapshot_sync)
        await store.set_snapshot(cards, oracle)
        # Tick history is loaded on demand via ensure_history_loaded (keeps startup fast).
    except Exception as exc:
        log.warning("hydrate live store failed: %s", exc)


async def db_sync_loop(interval: float = 1.0) -> None:
    store = get_live_store()
    while True:
        try:
            await hydrate_live_store_from_db()
        except Exception as exc:
            log.warning("db sync failed: %s", exc)
        await asyncio.sleep(interval)
