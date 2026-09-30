"""Build live tick payloads from current feed state (no DB round-trip)."""

from __future__ import annotations

from datetime import datetime, timezone

from pmanalysis.collector.window_manager import WindowManager
from pmanalysis.core.live_store import TickPoint
from pmanalysis.collector.window_cache import CachedWindow
from pmanalysis.feeds.binance import BinanceFeed
from pmanalysis.feeds.chainlink import ChainlinkFeed
from pmanalysis.feeds.clob import ClobFeed
from pmanalysis.indicators.compute import IndicatorState, compute_indicators, compute_tick_fields
from pmanalysis.schemas import MarketCard


def _dist_from_opening(current: float | None, opening: float | None) -> float | None:
    if current is None or opening is None:
        return None
    return round(current - opening, 2)


def build_live_tick(
    window: CachedWindow,
    chainlink: ChainlinkFeed,
    clob: ClobFeed,
    window_manager: WindowManager,
    indicator_state: IndicatorState,
    ts_ms: int,
    binance: BinanceFeed | None = None,
) -> tuple[MarketCard, TickPoint] | None:
    yes_ask, yes_bid = clob.get_quote(window.yes_token_id)
    no_ask, no_bid = clob.get_quote(window.no_token_id)
    if yes_ask <= 0 and yes_bid <= 0 and no_ask <= 0 and no_bid <= 0:
        return None

    now_ts = int(datetime.now(timezone.utc).timestamp())
    oracle_price = chainlink.get_price(window.asset)
    secs_remaining = max(0, window.end_ts - now_ts)
    tick_fields = compute_tick_fields(
        yes_ask=yes_ask,
        yes_bid=yes_bid,
        no_ask=no_ask,
        no_bid=no_bid,
        oracle_price=oracle_price,
        strike_price=window.strike_price,
        secs_remaining=secs_remaining,
    )
    from pmanalysis.config import twap_window_for_interval

    opening = window.opening_oracle_price or window.strike_price
    twap_win = twap_window_for_interval(window.interval)
    twap_oracle = chainlink.get_twap(window.asset, twap_win)
    # Initial Price = TWAP at window open only (never current TWAP / Chainlink spot).
    twap_initial = chainlink.get_twap_opening(window.asset, window.start_ts, twap_win)
    ind_fields = compute_indicators(
        indicator_state,
        ts_ms=ts_ms,
        oracle_price=oracle_price,
        yes_mid=tick_fields["yes_mid"],
        no_mid=tick_fields["no_mid"],
        combined_ask=tick_fields["combined_ask"],
        window_duration=window_manager.window_duration(window),
        secs_remaining=secs_remaining,
    )
    card = MarketCard(
        asset=window.asset,
        interval=window.interval,
        slug=window.slug,
        question=window.question,
        yes_mid=tick_fields["yes_mid"],
        no_mid=tick_fields["no_mid"],
        yes_ask=yes_ask,
        no_ask=no_ask,
        combined_ask=tick_fields["combined_ask"],
        oracle_price=oracle_price,
        strike_price=window.strike_price,
        opening_oracle_price=opening,
        dist_from_strike=tick_fields["dist_from_strike"],
        # Difference = current TWAP − initial TWAP.
        dist_from_opening=_dist_from_opening(twap_oracle, twap_initial),
        start_ts=window.start_ts,
        end_ts=window.end_ts,
        secs_remaining=secs_remaining,
        outcome=window.outcome,
        arb_edge=ind_fields["arb_edge"],
        lean=ind_fields["lean"],
        twap_oracle=twap_oracle,
        twap_initial=twap_initial,
    )
    point = TickPoint(
        ts_ms=ts_ms,
        oracle_price=oracle_price,
        binance_price=binance.get_price(window.asset) if binance else None,
        yes_mid=tick_fields["yes_mid"],
        no_mid=tick_fields["no_mid"],
        yes_ask=yes_ask,
        yes_bid=yes_bid,
        no_ask=no_ask,
        no_bid=no_bid,
        combined_ask=tick_fields["combined_ask"],
        dist_from_strike=tick_fields["dist_from_strike"],
        arb_edge=ind_fields["arb_edge"],
        lean=ind_fields["lean"],
        twap_oracle=twap_oracle,
    )
    return card, point
