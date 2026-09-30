import json

from pmanalysis.feeds.binance import BinanceFeed, parse_trade_msg


def test_parse_binance_trade_message():
    raw = json.dumps({
        "stream": "btcusdt@trade",
        "data": {
            "e": "trade",
            "E": 1_700_000_000_000,
            "s": "BTCUSDT",
            "p": "61777.99",
            "T": 1_700_000_000_123,
        },
    })
    tick = parse_trade_msg(raw)
    assert tick is not None
    assert tick["asset"] == "BTC"
    assert tick["price"] == 61777.99
    assert tick["ts_ms"] == 1_700_000_000_123


def test_binance_points_for_window():
    feed = BinanceFeed()
    start = 1_000_000
    end = start + 300
    feed._history["BTC"] = [
        (start * 1000 + 1000, 100.0),
        (start * 1000 + 5000, 101.0),
        (end * 1000, 102.0),
        (end * 1000 + 1000, 103.0),
    ]
    points = feed.points_for_window("BTC", start, end)
    assert len(points) == 4
    assert points[0]["ts_ms"] == start * 1000
    assert points[0]["binance_price"] == 100.0
    assert points[-1]["binance_price"] == 102.0
