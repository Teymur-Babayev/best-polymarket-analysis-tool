"""Polymarket RTDS Chainlink oracle + Chainlink TWAP feeds (Binance REST fallback)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Callable

import httpx
import websockets

from pmanalysis.config import RTDS_URL, TWAP_WINDOW_SEC

log = logging.getLogger(__name__)

_TOPIC_CHAINLINK = "crypto_prices_chainlink"
_TOPIC_BINANCE = "crypto_prices"
_TOPIC_TWAP_30 = "crypto_prices_twap_thirty"
_TOPIC_TWAP_60 = "crypto_prices_twap_sixty"
_TWAP_TOPICS = {
    _TOPIC_TWAP_30: 30,
    _TOPIC_TWAP_60: 60,
}
_CHAINLINK_SYMBOL_ASSET = {
    "btc/usd": "BTC",
    "eth/usd": "ETH",
    "sol/usd": "SOL",
}
_BINANCE_SYMBOL_ASSET = {
    "btcusdt": "BTC",
    "ethusdt": "ETH",
    "solusdt": "SOL",
}
_REST_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}
_STALE_SEC = 45
_REST_POLL_SEC = 2.0
_E18 = Decimal(10) ** 18
_BOUNDARY_CAPTURE_MAX_SEC = 5


def subscribe_msg() -> str:
    """Subscribe to Chainlink spot + Chainlink TWAP (30s/60s) on RTDS."""
    return json.dumps({
        "action": "subscribe",
        "subscriptions": [
            {"topic": _TOPIC_CHAINLINK, "type": "*", "filters": ""},
            {"topic": _TOPIC_TWAP_30, "type": "update", "filters": ""},
            {"topic": _TOPIC_TWAP_60, "type": "update", "filters": ""},
        ],
    })


def _asset_for_symbol(symbol: str, topic: str) -> str | None:
    sym = (symbol or "").lower()
    if topic == _TOPIC_CHAINLINK or topic in _TWAP_TOPICS:
        return _CHAINLINK_SYMBOL_ASSET.get(sym)
    if topic == _TOPIC_BINANCE:
        return _BINANCE_SYMBOL_ASSET.get(sym)
    return _CHAINLINK_SYMBOL_ASSET.get(sym) or _BINANCE_SYMBOL_ASSET.get(sym)


def _parse_price_value(body: dict) -> float | None:
    """Prefer Chainlink E18 full_accuracy_value; fall back to numeric value."""
    fa = body.get("full_accuracy_value")
    if fa is not None and str(fa).strip() != "":
        try:
            return float(Decimal(str(fa)) / _E18)
        except (InvalidOperation, ValueError, TypeError):
            pass
    try:
        price = float(body.get("value", 0))
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def parse_msgs(raw: str) -> list[dict]:
    """Parse RTDS messages (spot updates, TWAP updates, history dumps)."""
    raw = (raw or "").strip()
    if not raw or raw in ("PONG", "pong"):
        return []

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []

    topic = payload.get("topic", "")
    if topic not in (_TOPIC_CHAINLINK, _TOPIC_BINANCE) and topic not in _TWAP_TOPICS:
        return []

    body = payload.get("payload") or {}
    out: list[dict] = []

    if topic in _TWAP_TOPICS:
        window_s = int(body.get("window_s") or _TWAP_TOPICS[topic])
        symbol = str(body.get("symbol", ""))
        asset = _asset_for_symbol(symbol, topic)
        if not asset:
            return out
        price = _parse_price_value(body)
        if price is None or price <= 0:
            return out
        ts_ms = int(body.get("timestamp", 0) or payload.get("timestamp", 0))
        out.append({
            "asset": asset,
            "price": price,
            "ts_ms": ts_ms,
            "source": f"twap_{window_s}",
            "window_s": window_s,
        })
        return out

    source = "chainlink" if topic == _TOPIC_CHAINLINK else "binance"
    data = body.get("data")
    if isinstance(data, list) and data:
        symbol = str(body.get("symbol", ""))
        asset = _asset_for_symbol(symbol, topic)
        if asset:
            for pt in data:
                if not isinstance(pt, dict):
                    continue
                try:
                    price = float(pt.get("value", 0))
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                ts_ms = int(pt.get("timestamp", 0) or payload.get("timestamp", 0))
                out.append({
                    "asset": asset,
                    "price": price,
                    "ts_ms": ts_ms,
                    "source": source,
                })
        return out

    symbol = str(body.get("symbol", ""))
    asset = _asset_for_symbol(symbol, topic)
    if not asset:
        return out

    try:
        price = float(body.get("value", 0))
    except (TypeError, ValueError):
        return out
    if price <= 0:
        return out

    ts_ms = int(body.get("timestamp", 0) or payload.get("timestamp", 0))
    out.append({
        "asset": asset,
        "price": price,
        "ts_ms": ts_ms,
        "source": source,
    })
    return out


class ChainlinkFeed:
    def __init__(self, twap_window_sec: int = TWAP_WINDOW_SEC) -> None:
        self.twap_window_sec = 60 if twap_window_sec not in (30, 60) else twap_window_sec
        self._chainlink_prices: dict[str, float] = {}
        self._fallback_prices: dict[str, float] = {}
        self._twap_prices: dict[str, dict[int, float]] = {
            a: {} for a in _CHAINLINK_SYMBOL_ASSET.values()
        }
        self._history: dict[str, list[tuple[int, float]]] = {
            a: [] for a in _CHAINLINK_SYMBOL_ASSET.values()
        }
        self._chainlink_history: dict[str, list[tuple[int, float]]] = {
            a: [] for a in _CHAINLINK_SYMBOL_ASSET.values()
        }
        self._twap_history: dict[str, dict[int, list[tuple[int, float]]]] = {
            a: {30: [], 60: []} for a in _CHAINLINK_SYMBOL_ASSET.values()
        }
        # First Chainlink tick at/after each window boundary: (asset, start_ts) -> price
        self._boundary_captures: dict[tuple[str, int], float] = {}
        # First TWAP tick at/after boundary: (asset, start_ts, window_s) -> price
        self._twap_boundary_captures: dict[tuple[str, int, int], float] = {}
        self._running = False
        self._connected = False
        self._last_data_ts = 0.0
        self._on_tick: Callable[[dict], None] | None = None

    @property
    def prices(self) -> dict[str, float]:
        merged = dict(self._fallback_prices)
        merged.update(self._chainlink_prices)
        return merged

    @property
    def twap_prices(self) -> dict[str, float]:
        """Latest Chainlink TWAP for the default lookback window."""
        return self.twap_prices_for_window(self.twap_window_sec)

    def twap_prices_for_window(self, window_s: int) -> dict[str, float]:
        win = 60 if window_s not in (30, 60) else window_s
        out: dict[str, float] = {}
        for asset, by_win in self._twap_prices.items():
            price = by_win.get(win)
            if price is not None:
                out[asset] = price
        return out

    def twap_by_window(self) -> dict[str, dict[str, float]]:
        """Both RTDS TWAP lookbacks: {'30': {asset: price}, '60': {...}}."""
        return {
            "30": self.twap_prices_for_window(30),
            "60": self.twap_prices_for_window(60),
        }

    @property
    def connected(self) -> bool:
        return self._connected

    def history(self, asset: str, max_points: int = 300) -> list[tuple[int, float]]:
        return self._history.get(asset, [])[-max_points:]

    async def close(self) -> None:
        self._running = False

    def _emit_tick(self, tick: dict) -> None:
        if self._on_tick:
            self._on_tick(tick)

    def _apply_tick(self, tick: dict) -> bool:
        asset = tick["asset"]
        price = tick["price"]
        ts_ms = tick.get("ts_ms") or 0
        source = tick.get("source", "chainlink")
        point_ts = ts_ms if ts_ms > 0 else int(time.time() * 1000)

        if source.startswith("twap_"):
            window_s = int(tick.get("window_s") or source.split("_")[-1] or self.twap_window_sec)
            self._twap_prices.setdefault(asset, {})[window_s] = price
            hist = self._twap_history.setdefault(asset, {}).setdefault(window_s, [])
            hist.append((point_ts, price))
            if len(hist) > 1200:
                self._twap_history[asset][window_s] = hist[-1200:]
            self._maybe_capture_twap_boundary(asset, point_ts, price, window_s)
            self._last_data_ts = time.time()
            return True

        if source == "chainlink":
            self._chainlink_prices[asset] = price
            cl_hist = self._chainlink_history.setdefault(asset, [])
            cl_hist.append((point_ts, price))
            if len(cl_hist) > 1200:
                self._chainlink_history[asset] = cl_hist[-1200:]
            self._maybe_capture_boundary(asset, point_ts, price)
        else:
            if asset not in self._chainlink_prices:
                self._fallback_prices[asset] = price

        hist = self._history.setdefault(asset, [])
        hist.append((point_ts, price))
        if len(hist) > 1200:
            self._history[asset] = hist[-1200:]
        self._last_data_ts = time.time()
        return True

    async def _ping_loop(self, ws) -> None:
        while self._running:
            try:
                await ws.send("PING")
            except Exception:
                break
            await asyncio.sleep(5)

    async def _stale_watch_loop(self, ws) -> None:
        while self._running:
            await asyncio.sleep(5)
            if not self._connected:
                continue
            if self._last_data_ts and time.time() - self._last_data_ts > _STALE_SEC:
                log.warning("RTDS oracle feed stale (> %ss), reconnecting", _STALE_SEC)
                try:
                    await ws.close()
                except Exception:
                    pass
                break

    async def _rest_poll_loop(self) -> None:
        """Binance REST fallback when RTDS is rate-limited or down."""
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    for asset, symbol in _REST_SYMBOLS.items():
                        if asset in self._chainlink_prices:
                            continue
                        resp = await client.get(
                            "https://api.binance.com/api/v3/ticker/price",
                            params={"symbol": symbol},
                        )
                        if resp.status_code != 200:
                            continue
                        data = resp.json()
                        price = float(data.get("price", 0))
                        if price <= 0:
                            continue
                        tick = {
                            "asset": asset,
                            "price": price,
                            "ts_ms": int(time.time() * 1000),
                            "source": "binance_rest",
                        }
                        self._apply_tick(tick)
                        self._emit_tick(tick)
            except Exception as exc:
                log.debug("oracle REST poll: %s", exc)
            await asyncio.sleep(_REST_POLL_SEC)

    async def run(self, on_tick: Callable[[dict], None] | None = None) -> None:
        self._running = True
        self._on_tick = on_tick
        rest_task = asyncio.create_task(self._rest_poll_loop(), name="oracle-rest")
        backoff = 5
        try:
            while self._running:
                try:
                    async with websockets.connect(RTDS_URL, ping_interval=None) as ws:
                        await ws.send(subscribe_msg())
                        self._connected = True
                        self._last_data_ts = time.time()
                        backoff = 5
                        log.info(
                            "RTDS oracle feed connected (Chainlink spot + TWAP %ss/%ss)",
                            30,
                            60,
                        )
                        ping_task = asyncio.create_task(self._ping_loop(ws))
                        stale_task = asyncio.create_task(self._stale_watch_loop(ws))
                        try:
                            async for raw in ws:
                                if not self._running:
                                    break
                                ticks = parse_msgs(raw)
                                if not ticks:
                                    continue
                                for tick in ticks:
                                    self._apply_tick(tick)
                                    self._emit_tick(tick)
                        finally:
                            ping_task.cancel()
                            stale_task.cancel()
                            await asyncio.gather(ping_task, stale_task, return_exceptions=True)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._connected = False
                    msg = str(exc)
                    if "429" in msg:
                        backoff = max(backoff, 120)
                        log.warning(
                            "RTDS oracle feed rate-limited (429); REST fallback active; retry in %ss",
                            backoff,
                        )
                    else:
                        log.warning("RTDS oracle feed disconnected: %s", exc)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 300)
        finally:
            rest_task.cancel()
            await asyncio.gather(rest_task, return_exceptions=True)

    def get_price(self, asset: str) -> float | None:
        if asset in self._chainlink_prices:
            return self._chainlink_prices[asset]
        return self._fallback_prices.get(asset)

    def get_twap(self, asset: str, window_s: int | None = None) -> float | None:
        """Latest Chainlink-computed TWAP from Polymarket RTDS."""
        win = window_s if window_s in (30, 60) else self.twap_window_sec
        return self._twap_prices.get(asset, {}).get(win)

    def _maybe_capture_boundary(self, asset: str, ts_ms: int, price: float) -> None:
        """Record the first Chainlink tick at/after each aligned window boundary."""
        ts_sec = ts_ms // 1000
        for step in (300, 900):
            boundary = (ts_sec // step) * step
            if boundary <= ts_sec <= boundary + _BOUNDARY_CAPTURE_MAX_SEC:
                key = (asset, boundary)
                if key not in self._boundary_captures:
                    self._boundary_captures[key] = price

    def _maybe_capture_twap_boundary(
        self, asset: str, ts_ms: int, price: float, window_s: int
    ) -> None:
        ts_sec = ts_ms // 1000
        for step in (300, 900):
            boundary = (ts_sec // step) * step
            if boundary <= ts_sec <= boundary + _BOUNDARY_CAPTURE_MAX_SEC:
                key = (asset, boundary, window_s)
                if key not in self._twap_boundary_captures:
                    self._twap_boundary_captures[key] = price

    def get_boundary_capture(self, asset: str, start_ts: int) -> float | None:
        """Price captured from the first Chainlink tick at/after window start."""
        for offset in range(_BOUNDARY_CAPTURE_MAX_SEC + 1):
            price = self._boundary_captures.get((asset, start_ts + offset))
            if price is not None:
                return price
        return None

    def get_twap_opening(
        self, asset: str, start_ts: int, window_s: int | None = None
    ) -> float | None:
        """Chainlink TWAP at window open (Initial Price)."""
        win = window_s if window_s in (30, 60) else self.twap_window_sec
        for offset in range(_BOUNDARY_CAPTURE_MAX_SEC + 1):
            price = self._twap_boundary_captures.get((asset, start_ts + offset, win))
            if price is not None:
                return price
        return self._price_at_boundary(
            self._twap_history.get(asset, {}).get(win, []),
            start_ts,
            fallback=None,
        )

    def get_chainlink_price_at(self, asset: str, ts_sec: int) -> float | None:
        captured = self.get_boundary_capture(asset, ts_sec)
        if captured is not None:
            return captured
        return self._price_at_boundary(
            self._chainlink_history.get(asset, []),
            ts_sec,
            fallback=self._chainlink_prices.get(asset),
        )

    def get_opening_price_at(self, asset: str, start_ts: int) -> tuple[float | None, str | None]:
        """
        Opening oracle at window T0 (Polymarket Price to Beat).

        Prefers live Chainlink boundary capture, then Chainlink history, then feed history.
        """
        captured = self.get_boundary_capture(asset, start_ts)
        if captured is not None:
            return captured, "chainlink_boundary"

        chainlink = self._price_at_boundary(
            self._chainlink_history.get(asset, []),
            start_ts,
            fallback=None,
        )
        if chainlink is not None:
            return chainlink, "chainlink_start"

        oracle = self._price_at_boundary(
            self._history.get(asset, []),
            start_ts,
            fallback=None,
        )
        if oracle is not None:
            return oracle, "oracle_start"

        return None, None

    def get_price_at(self, asset: str, ts_sec: int) -> float | None:
        price, _ = self.get_opening_price_at(asset, ts_sec)
        if price is not None:
            return price
        return self.get_price(asset)

    def _price_at_boundary(
        self,
        hist: list[tuple[int, float]],
        ts_sec: int,
        fallback: float | None = None,
    ) -> float | None:
        """First tick at/after T0, else last tick within 2s before T0."""
        target_ms = ts_sec * 1000
        if not hist:
            return fallback

        for ts_ms, price in hist:
            if ts_ms >= target_ms and ts_ms <= target_ms + _BOUNDARY_CAPTURE_MAX_SEC * 1000:
                return price

        before = [(ts_ms, price) for ts_ms, price in hist if ts_ms <= target_ms]
        if before:
            closest_ms, closest_price = max(before, key=lambda row: row[0])
            if target_ms - closest_ms <= 2000:
                return closest_price

        return fallback

    def _price_at_from_hist(
        self,
        hist: list[tuple[int, float]],
        ts_sec: int,
        fallback: float | None = None,
    ) -> float | None:
        return self._price_at_boundary(hist, ts_sec, fallback=fallback)

    def oracle_points_for_window(
        self, asset: str, start_ts: int, end_ts: int
    ) -> list[dict]:
        """Chainlink/oracle history points for chart backfill inside a window."""
        hist = self._history.get(asset, [])
        if not hist:
            price = self.get_price(asset)
            if price is None:
                return []
            now_ms = int(time.time() * 1000)
            return [{"ts_ms": now_ms, "oracle_price": price}]

        start_ms = start_ts * 1000
        end_ms = end_ts * 1000
        points: list[dict] = []
        for ts_ms, price in hist:
            if start_ms <= ts_ms <= end_ms:
                points.append({"ts_ms": ts_ms, "oracle_price": price})
        if not points and hist:
            last_ts, last_price = hist[-1]
            if last_ts >= start_ms - 60_000:
                points.append({"ts_ms": max(last_ts, start_ms), "oracle_price": last_price})
        return points

    def twap_points_for_window(
        self,
        asset: str,
        start_ts: int,
        end_ts: int,
        window_s: int | None = None,
    ) -> list[dict]:
        """Chainlink TWAP history points for chart backfill."""
        win = window_s if window_s in (30, 60) else self.twap_window_sec
        hist = self._twap_history.get(asset, {}).get(win, [])
        start_ms = start_ts * 1000
        end_ms = end_ts * 1000
        points: list[dict] = []
        for ts_ms, price in hist:
            if start_ms <= ts_ms <= end_ms:
                points.append({"ts_ms": ts_ms, "twap_oracle": price})
        if not points:
            price = self.get_twap(asset, win)
            if price is not None:
                now_ms = int(time.time() * 1000)
                if start_ms <= now_ms <= end_ms + 60_000:
                    points.append({"ts_ms": max(now_ms, start_ms), "twap_oracle": price})
        return points
