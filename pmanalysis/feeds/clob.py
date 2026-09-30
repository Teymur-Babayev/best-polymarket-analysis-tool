"""CLOB REST and WebSocket helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

import httpx
import websockets

from pmanalysis.config import CLOB_API, CLOB_WS_URL

log = logging.getLogger(__name__)


def valid_ask_cents(value: float) -> bool:
    return 0 < value < 98


def book_to_cents(book: dict) -> tuple[float, float]:
    asks = book.get("asks", []) if isinstance(book, dict) else []
    bids = book.get("bids", []) if isinstance(book, dict) else []
    best_ask = round(min(float(a["price"]) for a in asks) * 100, 1) if asks else 0.0
    best_bid = round(max(float(b["price"]) for b in bids) * 100, 1) if bids else 0.0
    return best_ask, best_bid


def _price_to_cents(raw: str | float | None) -> float:
    if raw in (None, "", "0"):
        return 0.0
    try:
        return round(float(raw) * 100, 1)
    except (TypeError, ValueError):
        return 0.0


def _quote_update(
    token_id: str,
    best_ask: float,
    best_bid: float,
    *,
    event_type: str | None = None,
    ts_ms: int | None = None,
) -> dict:
    row = {
        "token_id": str(token_id),
        "best_ask": best_ask,
        "best_bid": best_bid,
        "event_type": event_type,
    }
    if ts_ms is not None:
        row["ts_ms"] = ts_ms
    return row


def parse_clob_ws(msg) -> list[dict]:
    """Parse market-channel messages into normalized quote updates."""
    out: list[dict] = []

    if isinstance(msg, list):
        for item in msg:
            if not isinstance(item, dict):
                continue
            token_id = item.get("asset_id", "")
            if not token_id:
                continue
            best_ask, best_bid = book_to_cents(item)
            out.append(_quote_update(token_id, best_ask, best_bid, event_type="book"))
        return out

    if not isinstance(msg, dict):
        return out

    event_type = msg.get("event_type")
    ts_ms = None
    for key in ("timestamp", "ts", "t"):
        raw_ts = msg.get(key)
        if raw_ts is None:
            continue
        try:
            ts_ms = int(raw_ts)
            if ts_ms < 1_000_000_000_000:
                ts_ms *= 1000
            break
        except (TypeError, ValueError):
            pass

    if event_type == "best_bid_ask":
        token_id = str(msg.get("asset_id", ""))
        if token_id:
            out.append(
                _quote_update(
                    token_id,
                    _price_to_cents(msg.get("best_ask")),
                    _price_to_cents(msg.get("best_bid")),
                    event_type=event_type,
                    ts_ms=ts_ms,
                )
            )
        return out

    if event_type == "book":
        token_id = str(msg.get("asset_id", ""))
        if token_id:
            best_ask, best_bid = book_to_cents(msg)
            out.append(
                _quote_update(
                    token_id, best_ask, best_bid, event_type=event_type, ts_ms=ts_ms
                )
            )
        return out

    if event_type == "price_change":
        for change in msg.get("price_changes", []):
            token_id = str(change.get("asset_id", ""))
            if not token_id:
                continue
            out.append(
                _quote_update(
                    token_id,
                    _price_to_cents(change.get("best_ask")),
                    _price_to_cents(change.get("best_bid")),
                    event_type=event_type,
                    ts_ms=ts_ms,
                )
            )
        return out

    # Legacy / snapshot shapes without event_type
    if msg.get("asset_id") and (msg.get("asks") is not None or msg.get("bids") is not None):
        token_id = str(msg.get("asset_id", ""))
        if token_id:
            best_ask, best_bid = book_to_cents(msg)
            out.append(
                _quote_update(
                    token_id, best_ask, best_bid, event_type="book", ts_ms=ts_ms
                )
            )

    for change in msg.get("price_changes", []):
        token_id = str(change.get("asset_id", ""))
        if not token_id:
            continue
        out.append(
            _quote_update(
                token_id,
                _price_to_cents(change.get("best_ask")),
                _price_to_cents(change.get("best_bid")),
                event_type="price_change",
                ts_ms=ts_ms,
            )
        )

    return out


class ClobFeed:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(8.0))
        self._prices: dict[str, dict[str, float]] = {}
        self._last_update: dict[str, float] = {}
        self._running = False

    @property
    def prices(self) -> dict[str, dict[str, float]]:
        return self._prices

    async def close(self) -> None:
        self._running = False
        await self._client.aclose()

    async def fetch_book(self, token_id: str) -> dict:
        response = await self._client.get(f"{CLOB_API}/book", params={"token_id": token_id})
        data = response.json()
        best_ask, best_bid = book_to_cents(data)
        return {"best_ask": best_ask, "best_bid": best_bid, "raw": data}

    def apply_quote(self, token_id: str, best_ask: float, best_bid: float) -> bool:
        """Merge partial quote updates; return True if visible prices changed."""
        self._last_update[token_id] = time.monotonic()
        prev = self._prices.get(token_id, {})
        ask = best_ask if best_ask > 0 else prev.get("best_ask", 0.0)
        bid = best_bid if best_bid > 0 else prev.get("best_bid", 0.0)
        if ask == prev.get("best_ask", 0.0) and bid == prev.get("best_bid", 0.0):
            return False
        self._prices[token_id] = {"best_ask": ask, "best_bid": bid}
        return True

    def stale_tokens(self, token_ids: list[str], max_age_sec: float) -> list[str]:
        """Token ids with no WS quote (or none at all) within max_age_sec.

        Lets the REST /book poll act as a fallback for a dead/slow WS feed
        instead of a fixed-rate poll racing the WebSocket reader.
        """
        now = time.monotonic()
        out = []
        for token_id in token_ids:
            last = self._last_update.get(token_id)
            if last is None or (now - last) >= max_age_sec:
                out.append(token_id)
        return out

    async def seed_tokens(self, token_ids: list[str]) -> list[str]:
        """Refresh books from REST; return token ids whose quotes changed."""

        async def _one(token_id: str) -> str | None:
            try:
                book = await self.fetch_book(token_id)
                changed = self.apply_quote(token_id, book["best_ask"], book["best_bid"])
                self._last_update[token_id] = time.monotonic()
                if changed:
                    return token_id
            except Exception as exc:
                log.debug("seed book failed for %s: %s", token_id, exc)
            return None

        results = await asyncio.gather(*[_one(t) for t in token_ids])
        return [t for t in results if t]

    def subscribe_msg(self, token_ids: list[str]) -> str:
        return json.dumps({
            "assets_ids": token_ids,
            "type": "market",
            "custom_feature_enabled": True,
        })

    def dynamic_subscribe_msg(self, token_ids: list[str]) -> str:
        return json.dumps({
            "assets_ids": token_ids,
            "operation": "subscribe",
            "custom_feature_enabled": True,
        })

    async def _ping_loop(self, ws) -> None:
        while self._running:
            try:
                await ws.send("PING")
            except Exception:
                break
            await asyncio.sleep(10)

    async def run(
        self,
        get_token_ids: Callable[[], list[str]],
        on_update: Callable[[list[dict]], None] | None = None,
    ) -> None:
        self._running = True
        backoff = 5
        while self._running:
            token_ids = list(dict.fromkeys(get_token_ids()))
            if not token_ids:
                await asyncio.sleep(2)
                continue
            subscribed: set[str] = set()
            try:
                async with websockets.connect(CLOB_WS_URL, ping_interval=None) as ws:
                    await ws.send(self.subscribe_msg(token_ids))
                    subscribed = set(token_ids)
                    backoff = 5
                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    try:
                        async for raw in ws:
                            if not self._running:
                                break
                            if raw == "PONG":
                                continue
                            try:
                                payload = json.loads(raw)
                            except json.JSONDecodeError:
                                continue

                            updates = parse_clob_ws(payload)
                            changed: list[dict] = []
                            for upd in updates:
                                token_id = upd["token_id"]
                                if self.apply_quote(
                                    token_id, upd["best_ask"], upd["best_bid"]
                                ):
                                    quote = self._prices.get(token_id, {})
                                    changed.append({
                                        "token_id": token_id,
                                        "best_ask": quote.get("best_ask", 0.0),
                                        "best_bid": quote.get("best_bid", 0.0),
                                        "event_type": upd.get("event_type"),
                                        "ts_ms": upd.get("ts_ms"),
                                    })

                            new_ids = set(get_token_ids())
                            missing = new_ids - subscribed
                            if missing:
                                await ws.send(self.dynamic_subscribe_msg(list(missing)))
                                subscribed.update(missing)

                            if changed and on_update:
                                on_update(changed)
                    finally:
                        ping_task.cancel()
                        await asyncio.gather(ping_task, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("CLOB WS disconnected: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def get_quote(self, token_id: str) -> tuple[float, float]:
        quote = self._prices.get(token_id, {})
        return quote.get("best_ask", 0.0), quote.get("best_bid", 0.0)
