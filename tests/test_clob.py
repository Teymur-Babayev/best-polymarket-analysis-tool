from pmanalysis.feeds.clob import ClobFeed, parse_clob_ws


def test_parse_best_bid_ask_event():
    msg = {
        "event_type": "best_bid_ask",
        "asset_id": "123",
        "best_bid": "0.48",
        "best_ask": "0.52",
    }
    updates = parse_clob_ws(msg)
    assert len(updates) == 1
    assert updates[0]["token_id"] == "123"
    assert updates[0]["best_bid"] == 48.0
    assert updates[0]["best_ask"] == 52.0


def test_apply_quote_merges_partial_updates():
    feed = ClobFeed()
    feed.apply_quote("tok", 52.0, 48.0)
    feed.apply_quote("tok", 53.0, 0.0)
    ask, bid = feed.get_quote("tok")
    assert ask == 53.0
    assert bid == 48.0
