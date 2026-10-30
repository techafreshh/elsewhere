"""GET /api/search_tastes: Qloo search-as-you-type contract."""

import urllib.request

from fastapi.testclient import TestClient

try:
    from backend.app import cache as cache_module
    from backend.app import main as main_module
    from backend.app.main import app
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import cache as cache_module
    from app import main as main_module
    from app.main import app

client = TestClient(app)


def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(cache_module, "CACHE_DIR", tmp_path)

    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in mock mode")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


def test_search_tastes_422_on_short_query(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    assert client.get("/api/search_tastes", params={"q": "a"}).status_code == 422
    assert client.get("/api/search_tastes", params={"q": ""}).status_code == 422


def test_search_tastes_normalizes_hits(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    # Live mode: the mock-name filter only applies to the static fixture
    # (no key); here qloo_search itself is faked so nothing hits network.
    monkeypatch.setenv("QLOO_API_KEY", "test-key")

    def _fake_search(query: str) -> list[dict]:
        assert query == "asake"
        return [
            {
                "name": "Asake",
                "entity_id": "A80A473D-103A-4B71-8BFB-461D537B35EA",
                "types": ["urn:entity:artist"],
                "popularity": 0.9,
            },
            {"name": "No id here", "entity_id": None},
            "not-a-dict",
            {
                "name": "Lonely At The Top",
                "entity_id": "urn:entity:track:mock-1",
                "types": ["urn:entity:track"],
            },
            {
                "name": "Extra",
                "entity_id": "urn:entity:brand:mock-2",
                "types": ["urn:entity:brand"],
            },
            {"name": "Filler 3", "entity_id": "urn:entity:brand:mock-3"},
            {"name": "Filler 4", "entity_id": "urn:entity:brand:mock-4"},
            {"name": "Filler 5", "entity_id": "urn:entity:brand:mock-5"},
            {"name": "Overflow 6", "entity_id": "urn:entity:brand:mock-6"},
        ]

    monkeypatch.setattr(main_module, "qloo_search", _fake_search)
    response = client.get("/api/search_tastes", params={"q": "asake"})
    assert response.status_code == 200
    body = response.json()
    assert body["qloo_mode"] == "live"
    results = body["results"]
    assert len(results) <= 6
    assert results[0] == {
        "label": "Asake",
        "entity_id": "A80A473D-103A-4B71-8BFB-461D537B35EA",
        "type": "artist",
    }
    # Malformed hits never leak, but well-formed ones after them do.
    assert all(entry["label"] and entry["entity_id"] for entry in results)
    assert any(entry["label"] == "Lonely At The Top" for entry in results)


def test_search_tastes_mock_filters_nonmatching_fixture(monkeypatch, tmp_path) -> None:
    """Without a key the search fixture is static — unrelated prefixes must
    not echo unrelated names back as fabricated results."""
    _isolated(monkeypatch, tmp_path)  # no QLOO_API_KEY -> mock mode
    response = client.get("/api/search_tastes", params={"q": "zzzzzz"})
    assert response.status_code == 200
    assert response.json()["results"] == []
