import asyncio

from pmanalysis.collector.feed_recorder import FeedRecorder
from pmanalysis.collector.worker import DbWriteWorker
from pmanalysis.db.models import BinanceTrade, ClobQuote, OracleTick
from pmanalysis.db.session import get_session, init_db
from pmanalysis.feeds.binance import parse_trade_msg
from pmanalysis.feeds.clob import parse_clob_ws


def test_parse_trade_msg_includes_qty_and_trade_id():
    raw = (
        '{"stream":"btcusdt@trade","data":{"e":"trade","s":"BTCUSDT","p":"100.5",'
        '"q":"0.01","t":12345,"T":1700000000123}}'
    )
    tick = parse_trade_msg(raw)
    assert tick is not None
    assert tick["asset"] == "BTC"
    assert tick["price"] == 100.5
    assert tick["qty"] == 0.01
    assert tick["trade_id"] == "12345"
    assert tick["ts_ms"] == 1700000000123


def test_parse_clob_ws_includes_event_type():
    updates = parse_clob_ws({
        "event_type": "best_bid_ask",
        "asset_id": "tok1",
        "best_ask": "0.55",
        "best_bid": "0.54",
        "timestamp": "1700000000123",
    })
    assert len(updates) == 1
    assert updates[0]["event_type"] == "best_bid_ask"
    assert updates[0]["best_ask"] == 55.0
    assert updates[0]["best_bid"] == 54.0
    assert updates[0]["ts_ms"] == 1700000000123


def test_feed_recorder_flushes_all_streams(tmp_path, monkeypatch):
    import pmanalysis.db.session as db_session

    monkeypatch.setattr(db_session, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("pmanalysis.collector.feed_recorder.FEED_RECORD_BATCH_SIZE", 1000)
    monkeypatch.setattr("pmanalysis.collector.feed_recorder.FEED_RECORD_FLUSH_SEC", 0.01)
    db_session._engine = None
    db_session._SessionLocal = None

    init_db()

    async def _run() -> None:
        worker = DbWriteWorker("test-feed")
        worker.start()
        recorder = FeedRecorder(worker)
        recorder.start()
        recorder.record_binance({
            "asset": "BTC",
            "price": 100.0,
            "ts_ms": 1_000,
            "qty": 0.5,
            "trade_id": "9",
        })
        recorder.record_oracle({
            "asset": "BTC",
            "price": 100.1,
            "ts_ms": 1_001,
            "source": "chainlink",
        })
        recorder.record_clob([{
            "token_id": "tok-yes",
            "window_id": 1,
            "asset": "BTC",
            "interval": "5m",
            "side": "YES",
            "best_ask": 55.0,
            "best_bid": 54.0,
            "event_type": "best_bid_ask",
            "ts_ms": 1_002,
        }])
        await asyncio.sleep(0.05)
        await recorder.stop()
        await worker.stop()

    asyncio.run(_run())

    with get_session() as session:
        assert session.query(BinanceTrade).count() == 1
        assert session.query(OracleTick).count() == 1
        assert session.query(ClobQuote).count() == 1
        trade = session.query(BinanceTrade).one()
        assert trade.asset == "BTC"
        assert trade.qty == 0.5
        quote = session.query(ClobQuote).one()
        assert quote.side == "YES"
        assert quote.best_ask == 55.0
