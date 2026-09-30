"""Async workers — serialized DB writes off the uvicorn event loop."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class DbWriteWorker:
    """
    Single-consumer queue so SQLite writes never run concurrently.

    Sync callables run via asyncio.to_thread so they do not freeze WebSocket/HTTP
    on the main event loop. Async callables run on the main loop (avoid for heavy DB).
    """

    def __init__(self, name: str = "db", *, maxsize: int = 1000) -> None:
        self.name = name
        self._queue: asyncio.Queue[
            tuple[str, Callable[[], Any], asyncio.Future[Any] | None]
        ] = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name=f"worker-{self.name}")

    async def stop(self) -> None:
        self._running = False
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def submit(self, label: str, fn: Callable[[], Any]) -> None:
        """Enqueue a job; errors are logged, not propagated.

        If SQLite is falling behind (queue full), drop the job rather than
        letting an unbounded queue of pending writes eat RAM — these are
        fire-and-forget jobs (nothing awaits the result).
        """
        if not self._running:
            return
        try:
            self._queue.put_nowait((label, fn, None))
        except asyncio.QueueFull:
            log.warning("%s queue full (%d); dropping job %s", self.name, self._queue.maxsize, label)

    async def run(self, label: str, fn: Callable[[], Any]) -> T:
        """Enqueue and await the result. Backpressures (awaits queue space)
        instead of growing unbounded when SQLite can't keep up."""
        if not self._running:
            raise RuntimeError(f"{self.name} worker is not running")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        await self._queue.put((label, fn, future))
        return await future

    async def _execute(self, fn: Callable[[], Any]) -> Any:
        if inspect.iscoroutinefunction(fn):
            return await fn()
        # Sync callable (including lambda): run in a worker thread.
        result = await asyncio.to_thread(fn)
        # lambda: async_fn() returns a coroutine object — await on main loop.
        if inspect.isawaitable(result):
            return await result
        return result

    async def _loop(self) -> None:
        while self._running:
            try:
                label, fn, future = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            try:
                result = await self._execute(fn)
                if future is not None and not future.done():
                    future.set_result(result)
            except asyncio.CancelledError:
                if future is not None and not future.done():
                    future.cancel()
                raise
            except Exception as exc:
                if future is not None and not future.done():
                    future.set_exception(exc)
                else:
                    log.warning(
                        "%s job %s failed: %r",
                        self.name,
                        label,
                        exc,
                        exc_info=True,
                    )


class CoalescingWorker:
    """
    Debounced worker: duplicate keys merge while a flush is pending.
    Handler runs through the optional DbWriteWorker when provided.
    """

    def __init__(
        self,
        name: str,
        handler: Callable[[str], Awaitable[None]],
        *,
        debounce_sec: float = 0.5,
        db_worker: DbWriteWorker | None = None,
    ) -> None:
        self.name = name
        self._handler = handler
        self._debounce_sec = debounce_sec
        self._db_worker = db_worker
        self._pending: set[str] = set()
        self._flush_task: asyncio.Task | None = None

    def schedule(self, key: str) -> None:
        if not key:
            return
        self._pending.add(key)
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush(), name=f"coalesce-{self.name}")

    async def drain(self) -> None:
        if self._flush_task and not self._flush_task.done():
            await self._flush_task

    async def _flush(self) -> None:
        try:
            await asyncio.sleep(self._debounce_sec)
            keys = list(self._pending)
            self._pending.clear()
            for key in keys:
                await self._dispatch(key)
        except asyncio.CancelledError:
            raise

    async def _dispatch(self, key: str) -> None:
        label = f"{self.name}:{key}"

        async def job() -> None:
            await self._handler(key)

        try:
            if self._db_worker is not None:
                await self._db_worker.run(label, job)
            else:
                await job()
        except Exception as exc:
            log.warning(
                "%s failed for %s: %r",
                self.name,
                key,
                exc,
                exc_info=True,
            )
