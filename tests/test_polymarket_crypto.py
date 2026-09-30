import asyncio

from pmanalysis.feeds.polymarket_crypto import fetch_price_to_beat


def test_fetch_price_to_beat_btc_5m_window():
    """Live check against polymarket.com past-results (Chainlink open at T0)."""
    price, source = asyncio.run(
        fetch_price_to_beat("BTC", "5m", 1781106900, 1781107200)
    )
    assert source == "polymarket_api"
    assert price is not None
    assert 62000 < price < 64000
