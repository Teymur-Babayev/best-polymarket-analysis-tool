from pmanalysis.db.clear import clear_database
from pmanalysis.db.models import Market, Tick, Window
from pmanalysis.db.session import get_session, init_db


def test_clear_database_keeps_markets(tmp_path, monkeypatch):
    monkeypatch.setattr("pmanalysis.db.clear.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("pmanalysis.db.session.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("pmanalysis.db.clear.ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr("pmanalysis.db.clear.EXPORT_DIR", tmp_path / "exports")

    init_db()
    with get_session() as session:
        market = session.query(Market).filter_by(base_slug="btc-updown-5m").first()
        window = Window(
            market_id=market.id,
            slug="test-clear-window",
            asset="BTC",
            interval="5m",
            start_ts=1_000_000,
            end_ts=1_000_300,
        )
        session.add(window)
        session.flush()
        session.add(Tick(window_id=window.id, ts_ms=1_000_001_000, yes_mid=0.5, no_mid=0.5))

    result = clear_database(include_archives=False)
    assert result["cleared"]["ticks"] == 1
    assert result["cleared"]["windows"] == 1

    with get_session() as session:
        assert session.query(Window).count() == 0
        assert session.query(Tick).count() == 0
        assert session.query(Market).count() >= 1
