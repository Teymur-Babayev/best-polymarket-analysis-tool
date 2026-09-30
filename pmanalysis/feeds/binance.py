"""Binance spot trade WebSocket feed for chart overlay."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

import websockets

from pmanalysis.config import ASSETS

log = logging.getLogger(__name__)

_SYMBOLS = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "SOL": "solusdt",
}
_ASSET_FOR_SYMBOL = {v.upper(): k for k, v in _SYMBOLS.items()}


def _stream_url() -> str:
    streams = "/".join(f"{sym}@trade" for sym in _SYMBOLS.values())
    return f"wss://stream.binance.com:9443/stream?streams={streams}"


def parse_trade_msg(raw: str) -> dict | None:
    """Parse a Binance combined-stream trade message."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict) or data.get("e") != "trade":
        return None

    symbol = str(data.get("s", "")).upper()
    asset = _ASSET_FOR_SYMBOL.get(symbol)
    if not asset:
        return None

    try:
        price = float(data.get("p", 0))
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None

    ts_ms = int(data.get("T", 0) or data.get("E", 0) or time.time() * 1000)
    qty = None
    try:
        if data.get("q") not in (None, ""):
            qty = float(data["q"])
    except (TypeError, ValueError):
        qty = None
    trade_id = data.get("t")
    return {
        "asset": asset,
        "price": price,
        "ts_ms": ts_ms,
        "qty": qty,
        "trade_id": str(trade_id) if trade_id is not None else None,
        "source": "binance",
    }


class BinanceFeed:
    def __init__(self) -> None:
        self._prices: dict[str, float] = {}
        self._history: dict[str, list[tuple[int, float]]] = {a: [] for a in ASSETS}
        self._running = False
        self._connected = False
        self._on_tick: Callable[[dict], None] | None = None

    @property
    def prices(self) -> dict[str, float]:
        return dict(self._prices)

    @property
    def connected(self) -> bool:
        return self._connected

    def get_price(self, asset: str) -> float | None:
        return self._prices.get(asset)

    def history(self, asset: str, max_points: int = 1200) -> list[tuple[int, float]]:
        return self._history.get(asset, [])[-max_points:]

    async def close(self) -> None:
        self._running = False

    def _apply_tick(self, tick: dict) -> None:
        asset = tick["asset"]
        price = tick["price"]
        ts_ms = tick.get("ts_ms") or int(time.time() * 1000)
        self._prices[asset] = price
        hist = self._history.setdefault(asset, [])
        hist.append((ts_ms, price))
        if len(hist) > 1200:
            self._history[asset] = hist[-1200:]

    def _price_at_boundary(
        self,
        hist: list[tuple[int, float]],
        ts_sec: int,
        fallback: float | None = None,
    ) -> float | None:
        """First trade at/after T0, else last trade within 2s before T0."""
        target_ms = ts_sec * 1000
        if not hist:
            return fallback

        for ts_ms, price in hist:
            if ts_ms >= target_ms and ts_ms <= target_ms + 15_000:
                return price

        before = [(ts_ms, price) for ts_ms, price in hist if ts_ms <= target_ms]
        if before:
            closest_ms, closest_price = max(before, key=lambda row: row[0])
            if target_ms - closest_ms <= 2000:
                return closest_price

        return fallback

    def points_for_window(self, asset: str, start_ts: int, end_ts: int) -> list[dict]:
        """Binance trade points inside a window for chart backfill."""
        hist = self._history.get(asset, [])
        if not hist:
            price = self.get_price(asset)
            if price is None:
                return []
            now_ms = int(time.time() * 1000)
            return [{"ts_ms": now_ms, "binance_price": price}]

        start_ms = start_ts * 1000
        end_ms = end_ts * 1000
        points: list[dict] = []
        for ts_ms, price in hist:
            if start_ms <= ts_ms <= end_ms:
                points.append({"ts_ms": ts_ms, "binance_price": price})

        opening = self._price_at_boundary(hist, start_ts)
        if opening is not None and not any(p["ts_ms"] == start_ms for p in points):
            points.insert(0, {"ts_ms": start_ms, "binance_price": opening})

        if not points and hist:
            last_ts, last_price = hist[-1]
            if last_ts >= start_ms - 60_000:
                points.append({
                    "ts_ms": max(last_ts, start_ms),
                    "binance_price": last_price,
                })
        return points

    async def run(self, on_tick: Callable[[dict], None] | None = None) -> None:
        self._running = True
        self._on_tick = on_tick
        backoff = 5
        url = _stream_url()

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    self._connected = True
                    backoff = 5
                    log.info("Binance trade feed connected")
                    async for raw in ws:
                        if not self._running:
                            break
                        tick = parse_trade_msg(raw)
                        if not tick:
                            continue
                        self._apply_tick(tick)
                        if self._on_tick:
                            self._on_tick(tick)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected = False
                log.warning("Binance feed disconnected: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
