"""Batch-persist every Binance / Chainlink / CLOB WebSocket update."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from pmanalysis.config import FEED_RECORD_BATCH_SIZE, FEED_RECORD_FLUSH_SEC
from pmanalysis.db.models import BinanceTrade, ClobQuote, OracleTick
from pmanalysis.db.session import get_session

if TYPE_CHECKING:
    from pmanalysis.collector.worker import DbWriteWorker

log = logging.getLogger(__name__)


class FeedRecorder:
    """Buffer high-frequency feed events and flush them in bulk to SQLite."""

    def __init__(self, db_worker: DbWriteWorker) -> None:
        self._db_worker = db_worker
        self._binance: list[dict] = []
        self._oracle: list[dict] = []
        self._clob: list[dict] = []
        self._flush_task: asyncio.Task | None = None
        self._flushing = False
        self._running = False
        self.counts = {"binance": 0, "oracle": 0, "clob": 0}

    def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self._flush_now()

    def record_binance(self, tick: dict) -> None:
        if not self._running:
            return
        asset = tick.get("asset")
        price = tick.get("price")
        if not asset or price is None:
            return
        self._binance.append({
            "asset": asset,
            "ts_ms": int(tick.get("ts_ms") or time.time() * 1000),
            "price": float(price),
            "qty": float(tick["qty"]) if tick.get("qty") is not None else None,
            "trade_id": str(tick["trade_id"]) if tick.get("trade_id") is not None else None,
        })
        self._maybe_schedule()

    def record_oracle(self, tick: dict) -> None:
        if not self._running:
            return
        asset = tick.get("asset")
        price = tick.get("price")
        if not asset or price is None:
            return
        self._oracle.append({
            "asset": asset,
            "ts_ms": int(tick.get("ts_ms") or time.time() * 1000),
            "price": float(price),
            "source": str(tick.get("source") or "chainlink"),
        })
        self._maybe_schedule()

    def record_clob(self, updates: list[dict]) -> None:
        if not self._running or not updates:
            return
        now_ms = int(time.time() * 1000)
        for upd in updates:
            token_id = upd.get("token_id")
            if not token_id:
                continue
            self._clob.append({
                "ts_ms": int(upd.get("ts_ms") or now_ms),
                "token_id": str(token_id),
                "window_id": upd.get("window_id"),
                "asset": upd.get("asset"),
                "interval": upd.get("interval"),
                "side": upd.get("side"),
                "best_ask": float(upd.get("best_ask") or 0.0),
                "best_bid": float(upd.get("best_bid") or 0.0),
                "event_type": upd.get("event_type"),
            })
        self._maybe_schedule()

    def _pending_count(self) -> int:
        return len(self._binance) + len(self._oracle) + len(self._clob)

    def _maybe_schedule(self) -> None:
        if self._pending_count() >= FEED_RECORD_BATCH_SIZE:
            self._schedule_flush(immediate=True)
        elif self._flush_task is None or self._flush_task.done():
            self._schedule_flush(immediate=False)

    def _schedule_flush(self, *, immediate: bool) -> None:
        if self._flush_task and not self._flush_task.done():
            if immediate and not self._flushing:
                self._flush_task.cancel()
            else:
                return
        self._flush_task = asyncio.create_task(
            self._flush_loop(0.0 if immediate else FEED_RECORD_FLUSH_SEC),
            name="feed-record-flush",
        )

    async def _flush_loop(self, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self._flush_now()
        except asyncio.CancelledError:
            raise

    async def _flush_now(self) -> None:
        if self._flushing or self._pending_count() == 0:
            return
        self._flushing = True
        binance = self._binance
        oracle = self._oracle
        clob = self._clob
        self._binance = []
        self._oracle = []
        self._clob = []
        try:
            if not binance and not oracle and not clob:
                return
            counts = await self._db_worker.run(
                "feed-record",
                lambda: self._write_batch_sync(binance, oracle, clob),
            )
            self.counts["binance"] += counts["binance"]
            self.counts["oracle"] += counts["oracle"]
            self.counts["clob"] += counts["clob"]
            log.debug(
                "feed record flush: binance=%d oracle=%d clob=%d",
                counts["binance"],
                counts["oracle"],
                counts["clob"],
            )
        finally:
            self._flushing = False

    def _write_batch_sync(
        self,
        binance: list[dict],
        oracle: list[dict],
        clob: list[dict],
    ) -> dict[str, int]:
        with get_session() as session:
            if binance:
                session.bulk_insert_mappings(BinanceTrade, binance)
            if oracle:
                session.bulk_insert_mappings(OracleTick, oracle)
            if clob:
                session.bulk_insert_mappings(ClobQuote, clob)
        return {
            "binance": len(binance),
            "oracle": len(oracle),
            "clob": len(clob),
        }
