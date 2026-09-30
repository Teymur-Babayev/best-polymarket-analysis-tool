import json

from pmanalysis.feeds.chainlink import parse_msgs


def test_parse_chainlink_update():
    raw = json.dumps({
        "topic": "crypto_prices_chainlink",
        "type": "update",
        "timestamp": 1753314088421,
        "payload": {
            "symbol": "btc/usd",
            "timestamp": 1753314088395,
            "value": 67234.50,
        },
    })
    ticks = parse_msgs(raw)
    assert len(ticks) == 1
    assert ticks[0]["asset"] == "BTC"
    assert ticks[0]["price"] == 67234.50
    assert ticks[0]["source"] == "chainlink"


def test_parse_binance_history_dump():
    raw = json.dumps({
        "topic": "crypto_prices",
        "type": "subscribe",
        "timestamp": 1753314000000,
        "payload": {
            "symbol": "btcusdt",
            "data": [
                {"timestamp": 1753313999000, "value": 67000.0},
                {"timestamp": 1753314000000, "value": 67100.0},
            ],
        },
    })
    ticks = parse_msgs(raw)
    assert len(ticks) == 2
    assert ticks[0]["asset"] == "BTC"
    assert ticks[0]["source"] == "binance"


def test_parse_pong_returns_empty():
    assert parse_msgs("PONG") == []


def test_parse_chainlink_twap_thirty():
    raw = json.dumps({
        "topic": "crypto_prices_twap_thirty",
        "type": "update",
        "timestamp": 1785178800123,
        "payload": {
            "symbol": "btc/usd",
            "value": 65000.5,
            "full_accuracy_value": "65000500000000000000000",
            "timestamp": 1785178800000,
            "window_s": 30,
        },
    })
    ticks = parse_msgs(raw)
    assert len(ticks) == 1
    assert ticks[0]["asset"] == "BTC"
    assert ticks[0]["source"] == "twap_30"
    assert ticks[0]["window_s"] == 30
    assert ticks[0]["price"] == 65000.5


def test_parse_chainlink_twap_uses_full_accuracy():
    raw = json.dumps({
        "topic": "crypto_prices_twap_sixty",
        "type": "update",
        "timestamp": 1785178800123,
        "payload": {
            "symbol": "eth/usd",
            "value": 1900.0,
            "full_accuracy_value": "1902294094679001006080",
            "timestamp": 1785178800000,
            "window_s": 60,
        },
    })
    ticks = parse_msgs(raw)
    assert ticks[0]["asset"] == "ETH"
    assert ticks[0]["window_s"] == 60
    assert abs(ticks[0]["price"] - 1902.294094679001) < 1e-9
