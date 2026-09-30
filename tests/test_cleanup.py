from pmanalysis.db.cleanup import remove_impossible_quote_ticks
from pmanalysis.db.models import Market, Tick, Window
from pmanalysis.db.session import get_session, init_db


def test_remove_impossible_quote_ticks(tmp_path, monkeypatch):
    monkeypatch.setattr("pmanalysis.db.session.DB_PATH", tmp_path / "test.db")

    init_db()
    with get_session() as session:
        market_row = session.query(Market).filter_by(base_slug="sol-updown-5m").first()
        window = Window(
            market_id=market_row.id,
            slug="sol-updown-5m-test",
            asset="SOL",
            interval="5m",
            start_ts=1,
            end_ts=2,
        )
        session.add(window)
        session.flush()
        window_id = window.id
        session.add_all([
            Tick(window_id=window_id, ts_ms=1000, yes_ask=51.0, no_ask=50.0),
            Tick(window_id=window_id, ts_ms=2000, yes_ask=91.0, no_ask=91.0),
            Tick(window_id=window_id, ts_ms=3000, yes_ask=10.0, no_ask=92.0),
        ])
        session.commit()

    result = remove_impossible_quote_ticks()
    assert result["ticks_removed"] == 1
    assert result["windows_affected"] == 1

    with get_session() as session:
        ticks = session.query(Tick).filter_by(window_id=window_id).order_by(Tick.ts_ms).all()
        assert len(ticks) == 2
        assert all(not (t.yes_ask >= 90 and t.no_ask >= 90) for t in ticks)
