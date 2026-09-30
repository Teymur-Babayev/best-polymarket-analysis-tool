"""Resolve the opening oracle price (Polymarket Price to Beat at T0)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from pmanalysis.db.models import Tick, Window
from pmanalysis.feeds.chainlink import ChainlinkFeed
from pmanalysis.feeds.gamma import GammaClient
from pmanalysis.feeds.polymarket_crypto import fetch_price_to_beat

SOURCE_PRIORITY = (
    "chainlink_boundary",
    "prev_window_close",
    "polymarket_api",
    "gamma",
    "chainlink_start",
    "oracle_start",
    "tick_end",
    "tick_start",
    "question",
)


def _source_rank(source: str | None) -> int:
    try:
        return SOURCE_PRIORITY.index(source or "")
    except ValueError:
        return len(SOURCE_PRIORITY)


def previous_window(session: Session, window: Window) -> Window | None:
    """Window that ended exactly when this one started (same asset/interval)."""
    return (
        session.query(Window)
        .filter(
            Window.asset == window.asset,
            Window.interval == window.interval,
            Window.end_ts == window.start_ts,
        )
        .order_by(Window.id.desc())
        .first()
    )


def oracle_from_ticks_at_end(session: Session, window_id: int, end_ts: int) -> float | None:
    """Last oracle tick at or before window end (closing price at boundary)."""
    end_ms = end_ts * 1000
    row = (
        session.query(Tick.oracle_price, Tick.ts_ms)
        .filter(
            Tick.window_id == window_id,
            Tick.oracle_price.isnot(None),
            Tick.ts_ms <= end_ms,
            Tick.ts_ms >= end_ms - 15_000,
        )
        .order_by(Tick.ts_ms.desc())
        .first()
    )
    if row and row[0] is not None:
        return float(row[0])
    return None


def oracle_from_ticks_at_start(session: Session, window_id: int, start_ts: int) -> float | None:
    """First oracle tick at or after the window boundary (last-resort fallback)."""
    start_ms = start_ts * 1000
    row = (
        session.query(Tick.oracle_price)
        .filter(
            Tick.window_id == window_id,
            Tick.oracle_price.isnot(None),
            Tick.ts_ms >= start_ms,
            Tick.ts_ms < start_ms + 15_000,
        )
        .order_by(Tick.ts_ms.asc())
        .first()
    )
    if row and row[0] is not None:
        return float(row[0])

    grace_ms = 5000
    row = (
        session.query(Tick.oracle_price, Tick.ts_ms)
        .filter(
            Tick.window_id == window_id,
            Tick.oracle_price.isnot(None),
            Tick.ts_ms >= start_ms - grace_ms,
            Tick.ts_ms < start_ms,
        )
        .order_by(Tick.ts_ms.desc())
        .first()
    )
    if row and row[0] is not None:
        return float(row[0])
    return None


def opening_price_from_feed(
    chainlink: ChainlinkFeed,
    asset: str,
    start_ts: int,
) -> tuple[float | None, str | None]:
    """Resolve opening price from in-memory oracle feed history."""
    return chainlink.get_opening_price_at(asset, start_ts)


def resolve_final_oracle_price(
    session: Session,
    window: Window,
    chainlink: ChainlinkFeed,
) -> tuple[float | None, str | None]:
    """Closing oracle at window end — same boundary as the next window's open."""
    if window.final_oracle_price is not None:
        return window.final_oracle_price, "tick_end"

    tick_price = oracle_from_ticks_at_end(session, window.id, window.end_ts)
    if tick_price is not None:
        return tick_price, "tick_end"

    feed_price, feed_src = opening_price_from_feed(chainlink, window.asset, window.end_ts)
    if feed_price is not None:
        return feed_price, feed_src

    current = chainlink.get_price(window.asset)
    if current is not None:
        return current, "oracle_start"

    return None, None


def capture_final_oracle_price(
    session: Session,
    window: Window,
    chainlink: ChainlinkFeed,
) -> bool:
    """Persist closing oracle on a completed window. Returns True if changed."""
    price, _ = resolve_final_oracle_price(session, window, chainlink)
    if price is None or window.final_oracle_price == price:
        return False
    window.final_oracle_price = price
    session.add(window)
    return True


def opening_from_previous_window(
    session: Session,
    window: Window,
    chainlink: ChainlinkFeed,
) -> tuple[float | None, str | None]:
    """
    New window opens at the previous window's close (Polymarket boundary rule).
    Provides an immediate, zero-latency initial price on roll.
    """
    prev = previous_window(session, window)
    if not prev:
        return None, None

    capture_final_oracle_price(session, prev, chainlink)
    if prev.final_oracle_price is not None:
        return prev.final_oracle_price, "prev_window_close"

    return None, None


def seed_opening_from_previous(
    session: Session,
    window: Window,
    chainlink: ChainlinkFeed,
) -> bool:
    """Set opening/strike from the previous window's final oracle. Returns True if changed."""
    price, source = opening_from_previous_window(session, window, chainlink)
    if price is None:
        return False

    changed = False
    if window.opening_oracle_price != price:
        window.opening_oracle_price = price
        changed = True

    old_rank = _source_rank(window.strike_source)
    new_rank = _source_rank(source)
    can_update_strike = window.strike_price is None or new_rank <= old_rank
    if can_update_strike and window.strike_price != price:
        window.strike_price = price
        window.strike_source = source
        changed = True

    if changed:
        session.add(window)
    return changed


async def resolve_opening_oracle_price(
    session: Session,
    window: Window,
    chainlink: ChainlinkFeed,
    gamma: GammaClient,
) -> tuple[float | None, str | None]:
    """
    Polymarket crypto up/down Price to Beat at window open (Chainlink at T0).

    Live windows: past-results only lists completed windows, so feed capture comes first.
    Settled windows: prefer Polymarket past-results (same source as polymarket.com).
    """
    now_ts = int(datetime.now(timezone.utc).timestamp())
    is_live = now_ts < window.end_ts

    feed_price, feed_src = opening_price_from_feed(chainlink, window.asset, window.start_ts)
    tick_price = oracle_from_ticks_at_start(session, window.id, window.start_ts)

    if is_live:
        if feed_price is not None:
            return feed_price, feed_src
        if tick_price is not None:
            return tick_price, "tick_start"

        prev_price, prev_src = opening_from_previous_window(session, window, chainlink)
        if prev_price is not None:
            return prev_price, prev_src

        strike, source = await gamma.fetch_strike(window.slug)
        if strike is not None:
            return strike, source

        pm_price, pm_src = await fetch_price_to_beat(
            window.asset, window.interval, window.start_ts, window.end_ts
        )
        if pm_price is not None:
            return pm_price, pm_src
        return None, None

    pm_price, pm_src = await fetch_price_to_beat(
        window.asset, window.interval, window.start_ts, window.end_ts
    )
    if pm_price is not None:
        return pm_price, pm_src

    strike, source = await gamma.fetch_strike(window.slug)
    if strike is not None:
        return strike, source

    if feed_price is not None:
        return feed_price, feed_src

    if tick_price is not None:
        return tick_price, "tick_start"

    return None, None


async def apply_opening_price(
    session: Session,
    window: Window,
    chainlink: ChainlinkFeed,
    gamma: GammaClient,
) -> bool:
    """Update window opening/strike from Polymarket rules. Returns True if changed."""
    price, source = await resolve_opening_oracle_price(session, window, chainlink, gamma)
    if price is None:
        return False

    changed = False
    old_rank = _source_rank(window.strike_source)
    new_rank = _source_rank(source)
    can_update_opening = (
        window.opening_oracle_price is None or new_rank <= old_rank
    )
    can_update_strike = window.strike_price is None or new_rank <= old_rank

    if can_update_opening and window.opening_oracle_price != price:
        window.opening_oracle_price = price
        changed = True

    if can_update_strike and window.strike_price != price:
        window.strike_price = price
        window.strike_source = source
        changed = True

    if changed:
        session.add(window)
    return changed
