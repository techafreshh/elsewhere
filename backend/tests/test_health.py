"""Walking-skeleton contract tests: mock health JSON + ELSEWHERE wordmark page."""

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health_returns_mock_mode() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mode"] == "mock"
    assert body["qloo"] is True


def test_root_serves_wordmark() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "ELSEWHERE" in response.text
