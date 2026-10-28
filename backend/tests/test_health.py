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
    assert body["model"] is True


def test_health_wrong_method_returns_405() -> None:
    response = client.post("/api/health")
    assert response.status_code == 405


def test_unknown_transplant_returns_404_not_405() -> None:
    response = client.post(
        "/api/transplant", content="{bad json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 404


def test_root_serves_wordmark() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "ELSEWHERE" in response.text
