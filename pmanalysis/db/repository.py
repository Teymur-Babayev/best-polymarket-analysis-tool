"""Read-only DB queries for the web layer."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from pmanalysis.config import ASSETS, INTERVALS
from pmanalysis.db.models import Indicator, Tick, Window
from pmanalysis.indicators.compute import win_rate
from pmanalysis.schemas import MarketCard, WindowSummary


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def latest_oracle_prices(session: Session) -> dict[str, float]:
    """Prefer oracle_ticks (indexed); fall back to sampled ticks if empty."""
    from pmanalysis.db.models import OracleTick

    out: dict[str, float] = {}
    for asset in ASSETS:
        row = (
            session.query(OracleTick.price)
            .filter(OracleTick.asset == asset)
            .order_by(OracleTick.ts_ms.desc())
            .first()
        )
        if row and row[0] is not None:
            out[asset] = float(row[0])
            continue
        tick_row = (
            session.query(Tick.oracle_price)
            .join(Window, Tick.window_id == Window.id)
            .filter(Window.asset == asset, Tick.oracle_price.isnot(None))
            .order_by(Tick.ts_ms.desc())
            .first()
        )
        if tick_row and tick_row[0] is not None:
            out[asset] = float(tick_row[0])
    return out


def active_window(session: Session, asset: str, interval: str) -> Window | None:
    now_ts = _now_ts()
    return (
        session.query(Window)
        .filter(
            Window.asset == asset,
            Window.interval == interval,
            Window.outcome.is_(None),
            Window.end_ts > now_ts,
        )
        .order_by(Window.end_ts.asc())
        .first()
    )


def latest_tick(session: Session, window_id: int) -> Tick | None:
    return (
        session.query(Tick)
        .filter_by(window_id=window_id)
        .order_by(Tick.ts_ms.desc())
        .first()
    )


def opening_oracle_price(session: Session, window: Window) -> float | None:
    if window.opening_oracle_price is not None:
        return window.opening_oracle_price
    from pmanalysis.feeds.opening_price import oracle_from_ticks_at_start

    tick = oracle_from_ticks_at_start(session, window.id, window.start_ts)
    if tick is not None:
        return tick
    if window.strike_price is not None and window.strike_source in (
        "polymarket_api",
        "gamma",
        "chainlink_boundary",
        "chainlink_start",
        "oracle_start",
        "tick_start",
        "prev_window_close",
    ):
        return window.strike_price
    return None


def _dist_from_opening(current: float | None, opening: float | None) -> float | None:
    if current is None or opening is None:
        return None
    return round(current - opening, 2)


def latest_indicator(session: Session, window_id: int, ts_ms: int) -> Indicator | None:
    return (
        session.query(Indicator)
        .filter_by(window_id=window_id, ts_ms=ts_ms)
        .first()
    )


def build_market_cards(session: Session) -> list[MarketCard]:
    now_ts = _now_ts()
    cards: list[MarketCard] = []
    for asset in ASSETS:
        for interval in INTERVALS:
            window = active_window(session, asset, interval)
            if not window:
                cards.append(_empty_card(asset, interval))
                continue
            tick = latest_tick(session, window.id)
            indicator = latest_indicator(session, window.id, tick.ts_ms) if tick else None
            opening = opening_oracle_price(session, window)
            oracle = tick.oracle_price if tick else None
            twap = getattr(indicator, "twap_oracle", None) if indicator else None
            # Prefer earliest stored TWAP in this window as Initial Price.
            first_twap = (
                session.query(Indicator.twap_oracle)
                .filter(
                    Indicator.window_id == window.id,
                    Indicator.twap_oracle.isnot(None),
                )
                .order_by(Indicator.ts_ms.asc())
                .first()
            )
            twap_initial = (
                float(first_twap[0]) if first_twap and first_twap[0] is not None else None
            )
            cards.append(
                MarketCard(
                    asset=asset,
                    interval=interval,
                    slug=window.slug,
                    question=window.question,
                    yes_mid=tick.yes_mid if tick else 0,
                    no_mid=tick.no_mid if tick else 0,
                    yes_ask=tick.yes_ask if tick else 0,
                    no_ask=tick.no_ask if tick else 0,
                    combined_ask=tick.combined_ask if tick else 0,
                    oracle_price=oracle,
                    strike_price=window.strike_price,
                    opening_oracle_price=opening,
                    dist_from_strike=tick.dist_from_strike if tick else None,
                    # Difference = current TWAP − initial TWAP.
                    dist_from_opening=_dist_from_opening(twap, twap_initial),
                    start_ts=window.start_ts,
                    end_ts=window.end_ts,
                    secs_remaining=max(0, window.end_ts - now_ts),
                    outcome=window.outcome,
                    arb_edge=indicator.arb_edge if indicator else None,
                    lean=indicator.lean if indicator else None,
                    twap_oracle=twap,
                    twap_initial=twap_initial,
                )
            )
    return cards


def _empty_card(asset: str, interval: str) -> MarketCard:
    return MarketCard(
        asset=asset,
        interval=interval,
        slug="",
        question="No active window",
        yes_mid=0,
        no_mid=0,
        yes_ask=0,
        no_ask=0,
        combined_ask=0,
        oracle_price=None,
        strike_price=None,
        opening_oracle_price=None,
        dist_from_strike=None,
        dist_from_opening=None,
        start_ts=0,
        end_ts=0,
        secs_remaining=0,
        outcome=None,
        arb_edge=None,
        lean=None,
    )


def list_windows(
    session: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    asset: str | None = None,
    interval: str | None = None,
) -> list[WindowSummary]:
    query = session.query(Window).order_by(Window.end_ts.desc())
    if asset:
        query = query.filter(Window.asset == asset.upper())
    if interval:
        query = query.filter(Window.interval == interval)
    if offset:
        query = query.offset(offset)
    if limit and limit > 0:
        query = query.limit(limit)
    rows = query.all()
    if not rows:
        return []

    window_ids = [row.id for row in rows]
    tick_counts = dict(
        session.query(Tick.window_id, func.count(Tick.id))
        .filter(Tick.window_id.in_(window_ids))
        .group_by(Tick.window_id)
        .all()
    )
    return [
        WindowSummary(
            id=row.id,
            slug=row.slug,
            asset=row.asset,
            interval=row.interval,
            start_ts=row.start_ts,
            end_ts=row.end_ts,
            strike_price=row.strike_price,
            outcome=row.outcome,
            archived=row.archived,
            tick_count=tick_counts.get(row.id, 0),
        )
        for row in rows
    ]


def count_windows(
    session: Session,
    *,
    asset: str | None = None,
    interval: str | None = None,
) -> int:
    query = session.query(func.count(Window.id))
    if asset:
        query = query.filter(Window.asset == asset.upper())
    if interval:
        query = query.filter(Window.interval == interval)
    return int(query.scalar() or 0)


def db_stats(session: Session) -> dict:
    """Fast stats for status page — avoid full-table COUNT on multi-million-row tables."""
    from pmanalysis.db.models import BinanceTrade, ClobQuote, OracleTick

    def _approx_rows(model) -> int:
        # SQLite: MAX(id) is O(index) vs COUNT(*) full scan on huge tables.
        pk = getattr(model, "id", None)
        if pk is None:
            return int(session.query(func.count()).select_from(model).scalar() or 0)
        return int(session.query(func.max(pk)).scalar() or 0)

    # `MAX(Tick.ts_ms)` alone forces a full table scan (~34M rows): the only
    # index on ts_ms is the composite (window_id, ts_ms), so ts_ms isn't the
    # index's leftmost column and SQLite can't do the O(1) rightmost-leaf
    # trick it uses for MAX(id). This alone was responsible for /status and
    # /api/feeds/stats stalling 5-120s. Ticks are written in id order, so the
    # row with the max primary key (fast, id is the actual index) is an
    # accurate proxy for the most recent tick.
    latest_tick = (
        session.query(Tick.ts_ms).order_by(Tick.id.desc()).limit(1).scalar()
    )

    return {
        "window_count": _approx_rows(Window),
        "tick_count": _approx_rows(Tick),
        "binance_trade_count": _approx_rows(BinanceTrade),
        "oracle_tick_count": _approx_rows(OracleTick),
        "clob_quote_count": _approx_rows(ClobQuote),
        "latest_tick": latest_tick,
    }


def list_binance_trades(
    session: Session,
    *,
    limit: int = 200,
    offset: int = 0,
    asset: str | None = None,
) -> tuple[list[dict], int]:
    from pmanalysis.db.models import BinanceTrade

    query = session.query(BinanceTrade)
    if asset:
        query = query.filter(BinanceTrade.asset == asset.upper())
    total = query.count()
    rows = (
        query.order_by(BinanceTrade.ts_ms.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "asset": r.asset,
            "ts_ms": r.ts_ms,
            "price": r.price,
            "qty": r.qty,
            "trade_id": r.trade_id,
        }
        for r in rows
    ], total


def list_oracle_ticks(
    session: Session,
    *,
    limit: int = 200,
    offset: int = 0,
    asset: str | None = None,
    source: str | None = None,
) -> tuple[list[dict], int]:
    from pmanalysis.db.models import OracleTick

    query = session.query(OracleTick)
    if asset:
        query = query.filter(OracleTick.asset == asset.upper())
    if source:
        query = query.filter(OracleTick.source == source)
    total = query.count()
    rows = (
        query.order_by(OracleTick.ts_ms.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "asset": r.asset,
            "ts_ms": r.ts_ms,
            "price": r.price,
            "source": r.source,
        }
        for r in rows
    ], total


def list_clob_quotes(
    session: Session,
    *,
    limit: int = 200,
    offset: int = 0,
    asset: str | None = None,
    side: str | None = None,
) -> tuple[list[dict], int]:
    from pmanalysis.db.models import ClobQuote

    query = session.query(ClobQuote)
    if asset:
        query = query.filter(ClobQuote.asset == asset.upper())
    if side:
        query = query.filter(ClobQuote.side == side.upper())
    total = query.count()
    rows = (
        query.order_by(ClobQuote.ts_ms.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "ts_ms": r.ts_ms,
            "token_id": r.token_id,
            "window_id": r.window_id,
            "asset": r.asset,
            "interval": r.interval,
            "side": r.side,
            "best_ask": r.best_ask,
            "best_bid": r.best_bid,
            "event_type": r.event_type,
        }
        for r in rows
    ], total


def window_preview_payload(
    session: Session,
    slug: str,
    *,
    max_points: int = 64,
) -> dict | None:
    """Lightweight downsampled YES/NO series for sidebar previews."""
    window = session.query(Window).filter_by(slug=slug).first()
    if not window:
        return None
    rows = (
        session.query(Tick.ts_ms, Tick.yes_ask, Tick.no_ask, Tick.yes_mid, Tick.no_mid)
        .filter_by(window_id=window.id)
        .order_by(Tick.ts_ms)
        .all()
    )
    if not rows:
        points: list[dict] = []
    elif len(rows) <= max_points:
        points = [
            {
                "ts_ms": r.ts_ms,
                "yes_ask": r.yes_ask,
                "no_ask": r.no_ask,
                "yes_mid": r.yes_mid,
                "no_mid": r.no_mid,
            }
            for r in rows
        ]
    else:
        step = max(1, len(rows) // max_points)
        sampled = list(rows[::step])
        if sampled[-1] is not rows[-1]:
            sampled.append(rows[-1])
        points = [
            {
                "ts_ms": r.ts_ms,
                "yes_ask": r.yes_ask,
                "no_ask": r.no_ask,
                "yes_mid": r.yes_mid,
                "no_mid": r.no_mid,
            }
            for r in sampled
        ]
    return {
        "window": {
            "slug": window.slug,
            "asset": window.asset,
            "interval": window.interval,
            "start_ts": window.start_ts,
            "end_ts": window.end_ts,
            "strike_price": window.strike_price,
            "outcome": window.outcome,
        },
        "tick_count": len(rows),
        "ticks": points,
    }


def list_recent_previews(
    session: Session,
    *,
    asset: str,
    interval: str,
    limit: int = 7,
    page: int = 0,
    skip_current: bool = False,
    max_points: int = 64,
) -> dict:
    """Paginated recent windows with downsampled chart points.

    When skip_current=True, the newest (live/current) window is excluded so the
    sidebar starts at the previous market.
    """
    limit = max(1, min(int(limit), 10))
    page = max(0, int(page))
    base_skip = 1 if skip_current else 0
    offset = base_skip + page * limit

    q = (
        session.query(Window)
        .filter(Window.asset == asset.upper(), Window.interval == interval)
        .order_by(Window.end_ts.desc())
    )
    # Fetch one extra to know if older pages exist.
    windows = q.offset(offset).limit(limit + 1).all()
    has_older = len(windows) > limit
    windows = windows[:limit]

    items: list[dict] = []
    for window in windows:
        payload = window_preview_payload(session, window.slug, max_points=max_points)
        if payload:
            items.append(payload)

    return {
        "items": items,
        "page": page,
        "limit": limit,
        "skip_current": skip_current,
        "has_newer": page > 0,
        "has_older": has_older,
    }


def window_ticks_payload(session: Session, slug: str) -> dict | None:
    window = session.query(Window).filter_by(slug=slug).first()
    if not window:
        return None
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
    return {
        "window": {
            "slug": window.slug,
            "asset": window.asset,
            "interval": window.interval,
            "start_ts": window.start_ts,
            "end_ts": window.end_ts,
            "strike_price": window.strike_price,
            "opening_oracle_price": opening_oracle_price(session, window),
            "outcome": window.outcome,
        },
        "ticks": [
            {
                "ts_ms": t.ts_ms,
                "oracle_price": t.oracle_price,
                "binance_price": t.binance_price,
                "yes_mid": t.yes_mid,
                "no_mid": t.no_mid,
                "yes_ask": t.yes_ask,
                "no_ask": t.no_ask,
                "combined_ask": t.combined_ask,
                "dist_from_strike": t.dist_from_strike,
                "arb_edge": indicators[t.ts_ms].arb_edge if t.ts_ms in indicators else None,
                "twap_oracle": (
                    getattr(indicators[t.ts_ms], "twap_oracle", None)
                    if t.ts_ms in indicators
                    else None
                ),
            }
            for t in ticks
        ],
    }


def stats_win_rate(session: Session, asset: str, interval: str) -> float | None:
    rows = (
        session.query(Window.outcome)
        .filter(
            Window.asset == asset.upper(),
            Window.interval == interval,
            Window.outcome.isnot(None),
        )
        .order_by(Window.end_ts.desc())
        .limit(50)
        .all()
    )
    return win_rate([row[0] for row in rows if row[0]])
