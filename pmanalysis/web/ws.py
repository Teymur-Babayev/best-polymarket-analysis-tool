"""WebSocket live stream handler."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from pmanalysis.core.live_store import get_live_store
from pmanalysis.config import ASSETS, INTERVALS, WS_DRAIN_TIMEOUT_SEC, WS_SNAPSHOT_INTERVAL_SEC
from pmanalysis.collector.registry import get_collector
from pmanalysis.web.sync import ensure_history_loaded, hydrate_live_store_from_db, invalidate_history_cache

log = logging.getLogger(__name__)


def _json_default(value: Any) -> Any:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _dumps(payload: dict) -> str:
    return json.dumps(payload, allow_nan=False, default=_json_default)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._selection: dict[WebSocket, tuple[str, str]] = {}

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        self._selection[ws] = ("BTC", "5m")

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        self._selection.pop(ws, None)

    def _is_connected(self, ws: WebSocket) -> bool:
        return ws.client_state == WebSocketState.CONNECTED

    async def send_json(self, ws: WebSocket, payload: dict) -> bool:
        """Send JSON to client. Returns False if the socket is closed."""
        if not self._is_connected(ws):
            return False
        try:
            await ws.send_text(_dumps(payload))
            return True
        except WebSocketDisconnect:
            return False
        except RuntimeError:
            return False
        except Exception as exc:
            # Ignore send failures during shutdown or after client disconnect.
            if type(exc).__name__ in {"ConnectionClosed", "ConnectionClosedOK", "ConnectionClosedError"}:
                return False
            log.warning("ws send failed: %s", exc)
            return False

    async def _send_history(
        self, ws: WebSocket, store, asset: str, interval: str
    ) -> bool:
        try:
            await ensure_history_loaded(asset, interval)
            collector = get_collector()
            if collector:
                card = await store.get_market_card(asset, interval)
                if card and card.start_ts and card.end_ts:
                    oracle_pts = collector.chainlink.oracle_points_for_window(
                        asset, card.start_ts, card.end_ts
                    )
                    await store.enrich_oracle_history(
                        asset,
                        interval,
                        card.start_ts,
                        card.end_ts,
                        oracle_pts,
                        price_at=lambda ts, a=asset: collector.chainlink.get_price_at(a, ts),
                    )
                    binance_pts = collector.binance.points_for_window(
                        asset, card.start_ts, card.end_ts
                    )
                    await store.enrich_binance_history(
                        asset, interval, card.start_ts, card.end_ts, binance_pts
                    )
            return await self.send_json(ws, await store.get_history_payload(asset, interval))
        except Exception as exc:
            log.warning("ws history send failed for %s %s: %s", asset, interval, exc, exc_info=True)
            return self._is_connected(ws)

    async def handle_client(self, ws: WebSocket) -> None:
        store = get_live_store()
        await self.connect(ws)
        last_tick_seq = store.seq
        last_snapshot = time.monotonic()
        try:
            # Accept + first snapshot ASAP so the UI leaves "Connecting…".
            snapshot = await store.get_snapshot_payload()
            if not await self.send_json(ws, snapshot):
                return

            if not snapshot.get("markets"):
                await hydrate_live_store_from_db()
                snapshot = await store.get_snapshot_payload()
                if not await self.send_json(ws, snapshot):
                    return

            asset, interval = self._selection[ws]
            if not await self._send_history(ws, store, asset, interval):
                return
            last_tick_seq = store.seq

            drain_timeout = max(0.001, WS_DRAIN_TIMEOUT_SEC)
            while self._is_connected(ws):
                try:
                    # Short receive poll so we can drain feed updates quickly.
                    raw = await asyncio.wait_for(ws.receive_text(), timeout=drain_timeout)
                    msg = json.loads(raw)
                    if msg.get("type") == "select":
                        asset = str(msg.get("asset", "BTC")).upper()
                        interval = str(msg.get("interval", "5m"))
                        if asset in ASSETS and interval in INTERVALS:
                            prev = self._selection.get(ws)
                            # Client always re-selects on open; ignore no-op to avoid WS drops.
                            if prev != (asset, interval):
                                self._selection[ws] = (asset, interval)
                                invalidate_history_cache(asset, interval)
                                try:
                                    if not await self._send_history(ws, store, asset, interval):
                                        break
                                    card = await store.get_market_card(asset, interval)
                                    if card and card.slug:
                                        if not await self.send_json(
                                            ws,
                                            {
                                                "type": "tick",
                                                "asset": asset,
                                                "interval": interval,
                                                "market": card.model_dump(),
                                                "point": None,
                                            },
                                        ):
                                            break
                                    last_tick_seq = store.seq
                                except Exception as exc:
                                    log.warning(
                                        "ws select handler failed for %s %s: %s",
                                        asset,
                                        interval,
                                        exc,
                                        exc_info=True,
                                    )
                except asyncio.TimeoutError:
                    pass
                except json.JSONDecodeError:
                    pass
                except WebSocketDisconnect:
                    break

                if not self._is_connected(ws):
                    break

                # Drain immediately if data is ready; otherwise wait briefly for feeds.
                oracle_payload = await store.drain_oracle_update()
                asset, interval = self._selection[ws]
                tick_updates, last_tick_seq = await store.drain_tick_updates(
                    last_tick_seq, asset, interval
                )
                if not oracle_payload and not tick_updates:
                    try:
                        await store.wait_update(timeout=drain_timeout)
                    except asyncio.CancelledError:
                        break
                    oracle_payload = await store.drain_oracle_update()
                    tick_updates, last_tick_seq = await store.drain_tick_updates(
                        last_tick_seq, asset, interval
                    )

                if oracle_payload and not await self.send_json(ws, oracle_payload):
                    break

                send_failed = False
                for payload in tick_updates:
                    if not await self.send_json(ws, payload):
                        send_failed = True
                        break
                if send_failed:
                    break

                now = time.monotonic()
                if now - last_snapshot >= WS_SNAPSHOT_INTERVAL_SEC:
                    if not await self.send_json(
                        ws, await store.get_snapshot_payload()
                    ):
                        break
                    last_snapshot = now
        except asyncio.CancelledError:
            raise
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log.warning("ws client error: %s", exc, exc_info=True)
        finally:
            self.disconnect(ws)


manager = ConnectionManager()
