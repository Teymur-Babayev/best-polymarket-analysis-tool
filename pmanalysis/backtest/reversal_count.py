"""Count alternating YES/NO ask extremes (e.g. >= 90c) across tick history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from pmanalysis.db.models import Tick, Window
from pmanalysis.export.parquet import load_window_frame

Side = Literal["YES", "NO"]


@dataclass(frozen=True)
class ReversalEvent:
    count: int
    side: Side
    ts_ms: int
    yes_ask: float
    no_ask: float
    secs_remaining: int


@dataclass(frozen=True)
class WindowReversalResult:
    window_id: int
    slug: str
    asset: str
    interval: str
    outcome: str | None
    tick_count: int
    reversal_count: int
    events: list[ReversalEvent]


def _fmt_utc(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def format_event_sequence(events: list[ReversalEvent]) -> str:
    """Side with secs remaining and UTC time per entry, e.g. YES[165s@04:42:15]."""
    if not events:
        return "-"
    parts: list[str] = []
    for ev in events:
        clock = datetime.fromtimestamp(ev.ts_ms / 1000, tz=timezone.utc).strftime("%H:%M:%S")
        ask = ev.yes_ask if ev.side == "YES" else ev.no_ask
        parts.append(f"{ev.side}[{ev.secs_remaining}s@{clock},{ask:.0f}c]")
    return ">".join(parts)


def format_entry_secs(events: list[ReversalEvent]) -> str:
    if not events:
        return "-"
    return ",".join(str(ev.secs_remaining) for ev in events)


def format_entry_utc(events: list[ReversalEvent]) -> str:
    if not events:
        return "-"
    return ",".join(_fmt_utc(ev.ts_ms) for ev in events)


def count_reversals(
    ticks: list[tuple[int, float, float, int]],
    *,
    threshold: float = 90.0,
) -> tuple[int, list[ReversalEvent]]:
    """Count alternating YES/NO ask >= threshold moments.

    Ask prices are stored in cents (0-100). First hit counts as 1; each flip
    to the other side increments (YES -> NO -> YES -> ...).
    """
    return count_reversals_tiered(
        ticks,
        first=threshold,
        second=threshold,
        third=threshold,
    )


def count_reversals_tiered(
    ticks: list[tuple[int, float, float, int]],
    *,
    first: float = 90.0,
    second: float = 90.0,
    third: float = 90.0,
) -> tuple[int, list[ReversalEvent]]:
    """Count flips with per-entry thresholds (1st / 2nd / 3rd+)."""
    last_side: Side | None = None
    events: list[ReversalEvent] = []

    for ts_ms, yes_ask, no_ask, secs_remaining in ticks:
        next_flip = len(events) + 1
        if next_flip == 1:
            threshold = first
        elif next_flip == 2:
            threshold = second
        else:
            threshold = third

        yes_hit = yes_ask >= threshold
        no_hit = no_ask >= threshold
        if yes_hit and no_hit:
            continue
        if not yes_hit and not no_hit:
            continue

        side: Side = "YES" if yes_hit else "NO"
        if last_side == side:
            continue

        events.append(
            ReversalEvent(
                count=next_flip,
                side=side,
                ts_ms=ts_ms,
                yes_ask=yes_ask,
                no_ask=no_ask,
                secs_remaining=secs_remaining,
            )
        )
        last_side = side

    return len(events), events


def _ticks_from_frame(frame) -> list[tuple[int, float, float, int]]:
    cols = ["ts_ms", "yes_ask", "no_ask", "secs_remaining"]
    if frame is None or frame.empty or not set(cols).issubset(frame.columns):
        return []
    sub = frame[cols].dropna(subset=["ts_ms"]).sort_values("ts_ms")
    return list(sub.itertuples(index=False, name=None))


def _load_ticks_for_window(
    session: Session, window: Window
) -> list[tuple[int, float, float, int]]:
    """SQLite first (live/recent windows); parquet archive fallback for windows
    whose ticks were pruned from SQLite by the retention job."""
    rows = (
        session.query(Tick.ts_ms, Tick.yes_ask, Tick.no_ask, Tick.secs_remaining)
        .filter_by(window_id=window.id)
        .order_by(Tick.ts_ms)
        .all()
    )
    if rows:
        return rows
    if window.archived:
        return _ticks_from_frame(load_window_frame(window))
    return []


@dataclass(frozen=True)
class LoadedWindow:
    window_id: int
    slug: str
    asset: str
    interval: str
    outcome: str | None
    end_ts: int
    ticks: list[tuple[int, float, float, int]]


def load_btc_window_ticks(session: Session) -> list[LoadedWindow]:
    """Load all BTC windows and ticks once for grid sweeps."""
    windows = (
        session.query(Window)
        .filter(Window.asset == "BTC")
        .order_by(Window.end_ts.desc())
        .all()
    )
    if not windows:
        return []

    window_map = {w.id: w for w in windows}
    window_ids = list(window_map.keys())
    ticks_by_window: dict[int, list[tuple[int, float, float, int]]] = {}
    batch_size = 200

    for offset in range(0, len(window_ids), batch_size):
        batch_ids = window_ids[offset : offset + batch_size]
        tick_rows = (
            session.query(
                Tick.window_id,
                Tick.ts_ms,
                Tick.yes_ask,
                Tick.no_ask,
                Tick.secs_remaining,
            )
            .filter(Tick.window_id.in_(batch_ids))
            .order_by(Tick.window_id, Tick.ts_ms)
            .all()
        )
        for window_id, ts_ms, yes_ask, no_ask, secs_remaining in tick_rows:
            ticks_by_window.setdefault(window_id, []).append(
                (ts_ms, yes_ask, no_ask, secs_remaining)
            )

    loaded: list[LoadedWindow] = []
    for window_id in window_ids:
        ticks = ticks_by_window.get(window_id)
        window = window_map[window_id]
        if not ticks and window.archived:
            ticks = _ticks_from_frame(load_window_frame(window))
        if not ticks:
            continue
        loaded.append(
            LoadedWindow(
                window_id=window.id,
                slug=window.slug,
                asset=window.asset,
                interval=window.interval,
                outcome=window.outcome,
                end_ts=window.end_ts,
                ticks=ticks,
            )
        )
    return loaded


def analyze_tiered_grid(
    loaded: list[LoadedWindow],
    *,
    first: float,
    second: float,
    third: float,
) -> list[WindowReversalResult]:
    results: list[WindowReversalResult] = []
    for window in loaded:
        reversal_count, events = count_reversals_tiered(
            window.ticks, first=first, second=second, third=third
        )
        results.append(
            WindowReversalResult(
                window_id=window.window_id,
                slug=window.slug,
                asset=window.asset,
                interval=window.interval,
                outcome=window.outcome,
                tick_count=len(window.ticks),
                reversal_count=reversal_count,
                events=events,
            )
        )
    end_ts_by_id = {window.window_id: window.end_ts for window in loaded}
    results.sort(key=lambda r: end_ts_by_id[r.window_id], reverse=True)
    return results


def analyze_window(
    session: Session,
    window: Window,
    *,
    threshold: float = 90.0,
) -> WindowReversalResult | None:
    ticks = _load_ticks_for_window(session, window)
    if not ticks:
        return None

    reversal_count, events = count_reversals(ticks, threshold=threshold)
    return WindowReversalResult(
        window_id=window.id,
        slug=window.slug,
        asset=window.asset,
        interval=window.interval,
        outcome=window.outcome,
        tick_count=len(ticks),
        reversal_count=reversal_count,
        events=events,
    )


def analyze_windows(
    session: Session,
    *,
    threshold: float = 90.0,
    asset: str | None = None,
    interval: str | None = None,
    slug: str | None = None,
    limit: int | None = None,
    resolved_only: bool = False,
) -> list[WindowReversalResult]:
    query = session.query(Window).order_by(Window.end_ts.desc())
    if slug:
        query = query.filter(Window.slug == slug)
    if asset:
        query = query.filter(Window.asset == asset.upper())
    if interval:
        query = query.filter(Window.interval == interval)
    if resolved_only:
        query = query.filter(Window.outcome.isnot(None))
    if limit:
        query = query.limit(limit)

    windows = query.all()
    if not windows:
        return []

    window_map = {w.id: w for w in windows}
    return _analyze_window_map(session, window_map, threshold=threshold)


def _analyze_window_map(
    session: Session,
    window_map: dict[int, Window],
    *,
    threshold: float,
) -> list[WindowReversalResult]:
    """Stream ticks for many windows in batches (fast full-DB scan)."""
    results: list[WindowReversalResult] = []
    window_ids = list(window_map.keys())
    batch_size = 200

    for offset in range(0, len(window_ids), batch_size):
        batch_ids = window_ids[offset : offset + batch_size]
        tick_rows = (
            session.query(
                Tick.window_id,
                Tick.ts_ms,
                Tick.yes_ask,
                Tick.no_ask,
                Tick.secs_remaining,
            )
            .filter(Tick.window_id.in_(batch_ids))
            .order_by(Tick.window_id, Tick.ts_ms)
            .all()
        )

        ticks_by_window: dict[int, list[tuple[int, float, float, int]]] = {}
        for window_id, ts_ms, yes_ask, no_ask, secs_remaining in tick_rows:
            ticks_by_window.setdefault(window_id, []).append(
                (ts_ms, yes_ask, no_ask, secs_remaining)
            )

        for window_id in batch_ids:
            ticks = ticks_by_window.get(window_id)
            window = window_map[window_id]
            if not ticks and window.archived:
                ticks = _ticks_from_frame(load_window_frame(window))
            if not ticks:
                continue
            reversal_count, events = count_reversals(ticks, threshold=threshold)
            results.append(
                WindowReversalResult(
                    window_id=window.id,
                    slug=window.slug,
                    asset=window.asset,
                    interval=window.interval,
                    outcome=window.outcome,
                    tick_count=len(ticks),
                    reversal_count=reversal_count,
                    events=events,
                )
            )

    results.sort(key=lambda r: window_map[r.window_id].end_ts, reverse=True)
    return results
