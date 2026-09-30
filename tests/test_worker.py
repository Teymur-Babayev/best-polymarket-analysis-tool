import asyncio

from pmanalysis.collector.worker import CoalescingWorker, DbWriteWorker


def test_db_write_worker_serializes_jobs():
    async def run() -> None:
        worker = DbWriteWorker("test-db")
        worker.start()
        order: list[int] = []
        lock = asyncio.Lock()

        async def job(n: int) -> int:
            async with lock:
                order.append(n)
            await asyncio.sleep(0.02)
            return n

        results = await asyncio.gather(
            worker.run("a", lambda: job(1)),
            worker.run("b", lambda: job(2)),
            worker.run("c", lambda: job(3)),
        )
        await worker.stop()
        assert results == [1, 2, 3]
        assert order == [1, 2, 3]

    asyncio.run(run())


def test_coalescing_worker_merges_duplicate_keys():
    async def run() -> None:
        worker = DbWriteWorker("test-db")
        worker.start()
        calls: list[str] = []

        async def handler(asset: str) -> None:
            calls.append(asset)

        opening = CoalescingWorker(
            "opening",
            handler,
            debounce_sec=0.05,
            db_worker=worker,
        )
        for _ in range(50):
            opening.schedule("BTC")
        await opening.drain()
        await worker.stop()
        assert calls == ["BTC"]

    asyncio.run(run())


def test_coalescing_worker_batches_multiple_assets():
    async def run() -> None:
        worker = DbWriteWorker("test-db")
        worker.start()
        calls: list[str] = []

        async def handler(asset: str) -> None:
            calls.append(asset)

        opening = CoalescingWorker(
            "opening",
            handler,
            debounce_sec=0.05,
            db_worker=worker,
        )
        opening.schedule("BTC")
        opening.schedule("ETH")
        opening.schedule("BTC")
        opening.schedule("SOL")
        await opening.drain()
        await worker.stop()
        assert sorted(calls) == ["BTC", "ETH", "SOL"]

    asyncio.run(run())
