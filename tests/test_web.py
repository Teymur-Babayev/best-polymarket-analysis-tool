from fastapi.testclient import TestClient

from pmanalysis.web.app import create_app


def test_dashboard_renders():
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert "Initial Price" in response.text
    assert "Price to Beat" in response.text
    assert "lightweight-charts" in response.text


def test_api_markets():
    client = TestClient(create_app())
    response = client.get("/api/markets")
    assert response.status_code == 200
    assert len(response.json()) == 6


def test_market_redirect():
    client = TestClient(create_app())
    response = client.get("/market/BTC/5m")
    assert response.status_code == 200
    assert "BTC" in response.text


def test_history_page_has_clear_button():
    client = TestClient(create_app())
    response = client.get("/history")
    assert response.status_code == 200
    assert "Clear Database" in response.text


def test_clear_db_endpoint(monkeypatch):
    def fake_clear(*, include_archives=True):
        return {
            "cleared": {"windows": 2, "ticks": 50, "indicators": 50, "candles": 0, "collector_gaps": 0},
            "archive_entries_removed": 0,
            "export_entries_removed": 0,
            "db_path": "test.db",
            "db_size_mb": 0.1,
        }

    monkeypatch.setattr("pmanalysis.web.app.clear_database", fake_clear)
    client = TestClient(create_app())
    response = client.post("/api/admin/clear-db")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["cleared"]["windows"] == 2
