"""GET /api/geocode: typed city -> location, plus the agent-side center helpers."""

import urllib.request

from fastapi.testclient import TestClient

try:
    from backend.app import agent as agent_module
    from backend.app import cache as cache_module
    from backend.app import main as main_module
    from backend.app.main import app
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import agent as agent_module
    from app import cache as cache_module
    from app import main as main_module
    from app.main import app

client = TestClient(app)


def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(cache_module, "CACHE_DIR", tmp_path)
    main_module._GEO_CACHE.clear()

    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in mock mode")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


def test_geocode_422_on_short_name(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    assert client.get("/api/geocode", params={"name": "B"}).status_code == 422


def test_geocode_dictionary_hit(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    response = client.get("/api/geocode", params={"name": "Berlin"})
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert body["source"] == "dictionary"
    assert body["name"] == "Berlin"
    assert body["country"] == "Germany"
    assert abs(body["lat"] - 52.52) < 0.05
    assert abs(body["lng"] - 13.405) < 0.05


def test_geocode_alias_hit(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    body = client.get("/api/geocode", params={"name": "NYC"}).json()
    assert body["found"] is True
    assert body["name"] == "New York"
    assert abs(body["lat"] - 40.7128) < 0.05


def test_geocode_unknown_in_mock_mode(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    for name in ("Xyzzyville", "Nowhere-at-all"):
        body = client.get("/api/geocode", params={"name": name}).json()
        assert body == {"found": False}


def test_geocode_qloo_locality_fallback(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    calls: list[str] = []

    def _fake_search(query: str) -> list[dict]:
        calls.append(query)
        return [
            {
                "name": "Ushuaia",
                "entity_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                "types": ["urn:entity:locality"],
                "location": {"lat": -54.8019, "lon": -68.3030},
            },
            {
                # A music hit first is fine — only geography types qualify.
                "name": "Ithaca (song)",
                "entity_id": "ffff00ff-0000-4000-8000-000000000001",
                "types": ["urn:entity:track"],
            },
        ]

    monkeypatch.setattr(main_module, "qloo_search", _fake_search)
    body = client.get("/api/geocode", params={"name": "Ushuaia"}).json()
    assert body["found"] is True
    assert body["source"] == "qloo"
    assert body["name"] == "Ushuaia"
    assert abs(body["lat"] + 54.8019) < 0.001
    assert abs(body["lng"] + 68.3030) < 0.001
    # Cached: a repeat lookup must not re-hit Qloo.
    before = len(calls)
    body2 = client.get("/api/geocode", params={"name": "Ushuaia"}).json()
    assert body2 == body
    assert len(calls) == before


def test_geocode_music_only_hits_stay_unfound(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)

    def _fake_search(query: str) -> list[dict]:
        return [
            {
                "name": "Berlin (song)",
                "entity_id": "ffff00ff-0000-4000-8000-000000000002",
                "types": ["urn:entity:track"],
                "location": {"lat": 1.0, "lon": 2.0},
            }
        ]

    monkeypatch.setattr(main_module, "qloo_search", _fake_search)
    body = client.get("/api/geocode", params={"name": "Kandertal"}).json()
    assert body == {"found": False}


def test_city_center_helpers(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    assert agent_module.city_center("NYC") == [40.7128, -74.006]
    assert agent_module.city_center("new delhi") is not None
    assert agent_module.city_center("Lagos") == [6.5244, 3.3792]
    assert agent_module.city_center("Xyzzyville") is None
    assert agent_module.city_center("") is None
    assert agent_module.city_center(None) is None

    mean = agent_module.map_center_for("Nowhere", [[10.0, 20.0], [12.0, 22.0]])
    assert mean == [11.0, 21.0]
    fallback = agent_module.map_center_for("Lagos", [])
    assert fallback == [6.5244, 3.3792]
    world = agent_module.map_center_for("Xyzzyville", ["bad"])
    assert world == [20.0, 0.0]
