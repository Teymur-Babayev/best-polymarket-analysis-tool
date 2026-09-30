from pmanalysis.feeds.chainlink import ChainlinkFeed
from pmanalysis.feeds.opening_price import (
    oracle_from_ticks_at_end,
    oracle_from_ticks_at_start,
    seed_opening_from_previous,
)


def test_get_price_at_boundary():
    feed = ChainlinkFeed()
    start = 1_000_000
    feed._history["BTC"] = [
        (start * 1000 - 2000, 99.0),
        (start * 1000 + 500, 101.0),
        (start * 1000 + 1500, 102.0),
    ]
    assert feed.get_price_at("BTC", start) == 101.0


def test_boundary_capture_first_chainlink_tick():
    feed = ChainlinkFeed()
    start = 1_000_200  # aligned to 5m boundary (multiple of 300)
    feed._apply_tick({
        "asset": "BTC",
        "price": 100.25,
        "ts_ms": start * 1000 + 1200,
        "source": "chainlink",
    })
    assert feed.get_boundary_capture("BTC", start) == 100.25
    assert feed.get_opening_price_at("BTC", start) == (100.25, "chainlink_boundary")


def test_oracle_from_ticks_accepts_late_first_tick():
    from pmanalysis.db.models import Tick, Window, Market
    from pmanalysis.db.session import get_session, init_db

    init_db()
    with get_session() as session:
        market = session.query(Market).filter_by(base_slug="btc-updown-5m").first()
        window = Window(
            market_id=market.id,
            slug="test-window-opening-late-tick",
            asset="BTC",
            interval="5m",
            start_ts=1_000_000,
            end_ts=1_000_300,
        )
        session.add(window)
        session.flush()
        session.add(
            Tick(
                window_id=window.id,
                ts_ms=1_000_003_500,
                oracle_price=100.5,
                yes_mid=0.5,
                no_mid=0.5,
            )
        )
        session.flush()
        price = oracle_from_ticks_at_start(session, window.id, window.start_ts)
        assert price == 100.5


def test_oracle_from_ticks_at_start_uses_boundary_tick():
    from pmanalysis.db.models import Tick, Window, Market
    from pmanalysis.db.session import get_session, init_db

    init_db()
    with get_session() as session:
        market = session.query(Market).filter_by(base_slug="btc-updown-5m").first()
        window = Window(
            market_id=market.id,
            slug="test-window-opening-ticks",
            asset="BTC",
            interval="5m",
            start_ts=1_000_000,
            end_ts=1_000_300,
        )
        session.add(window)
        session.flush()
        session.add(
            Tick(
                window_id=window.id,
                ts_ms=999_998_000,
                oracle_price=98.0,
                yes_mid=0.5,
                no_mid=0.5,
            )
        )
        session.add(
            Tick(
                window_id=window.id,
                ts_ms=1_000_001_000,
                oracle_price=100.5,
                yes_mid=0.5,
                no_mid=0.5,
            )
        )
        session.flush()
        price = oracle_from_ticks_at_start(session, window.id, window.start_ts)
        assert price == 100.5


def test_oracle_from_ticks_at_end_uses_last_tick_before_boundary():
    from pmanalysis.db.models import Tick, Window, Market
    from pmanalysis.db.session import get_session, init_db

    init_db()
    with get_session() as session:
        market = session.query(Market).filter_by(base_slug="btc-updown-5m").first()
        window = Window(
            market_id=market.id,
            slug="test-window-closing-ticks",
            asset="BTC",
            interval="5m",
            start_ts=1_000_000,
            end_ts=1_000_300,
        )
        session.add(window)
        session.flush()
        session.add(
            Tick(
                window_id=window.id,
                ts_ms=1_000_299_500,
                oracle_price=101.25,
                yes_mid=0.5,
                no_mid=0.5,
            )
        )
        session.add(
            Tick(
                window_id=window.id,
                ts_ms=1_000_300_000,
                oracle_price=101.50,
                yes_mid=0.5,
                no_mid=0.5,
            )
        )
        session.flush()
        price = oracle_from_ticks_at_end(session, window.id, window.end_ts)
        assert price == 101.50


def test_seed_opening_from_previous_window():
    from pmanalysis.db.models import Tick, Window, Market
    from pmanalysis.db.session import get_session, init_db

    init_db()
    feed = ChainlinkFeed()
    with get_session() as session:
        market = session.query(Market).filter_by(base_slug="btc-updown-5m").first()
        prev = Window(
            market_id=market.id,
            slug="test-prev-window",
            asset="BTC",
            interval="5m",
            start_ts=1_000_000,
            end_ts=1_000_300,
        )
        session.add(prev)
        session.flush()
        session.add(
            Tick(
                window_id=prev.id,
                ts_ms=1_000_299_800,
                oracle_price=61777.99,
                yes_mid=0.5,
                no_mid=0.5,
            )
        )
        nxt = Window(
            market_id=market.id,
            slug="test-next-window",
            asset="BTC",
            interval="5m",
            start_ts=1_000_300,
            end_ts=1_000_600,
        )
        session.add(nxt)
        session.flush()

        assert seed_opening_from_previous(session, nxt, feed) is True
        assert nxt.opening_oracle_price == 61777.99
        assert nxt.strike_price == 61777.99
        assert nxt.strike_source == "prev_window_close"
        assert prev.final_oracle_price == 61777.99
