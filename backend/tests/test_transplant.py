"""Hero transplant contract: 5 cards, 1-decimal affinity, model slug, fallback."""

import urllib.request
from statistics import median

from fastapi.testclient import TestClient

try:
    from backend.app import agent as agent_module
    from backend.app import cache as cache_module
    from backend.app.main import app
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import agent as agent_module
    from app import cache as cache_module
    from app.main import app

client = TestClient(app)


def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.setattr(cache_module, "CACHE_DIR", tmp_path)

    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in mock mode")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


def test_hero_returns_five_grounded_cards(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/api/transplant",
        json={"seeds": [{"id": "urn:entity:artist:test1"}], "dest_city": "London"},
    )
    assert response.status_code == 200
    body = response.json()
    cards = body["transplant"]["cards"]
    assert len(cards) == 5
    for card in cards:
        assert card["name"]
        assert card["why_you"]
        assert isinstance(card["affinity_multiplier"], (int, float))
        assert isinstance(card["taste_path"], list) and len(card["taste_path"]) == 3
        assert card["entity_id"]


def test_affinity_one_decimal_and_model_slug(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/api/transplant",
        json={"seeds": [{"id": "urn:entity:artist:test1"}], "dest_city": "London"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["model"] == "deepseek/deepseek-v4.1-flash"
    # Recompute expected affinity from the mock hero entities in order.
    hero = agent_module._hero_call("London", ["urn:entity:artist:test1"], take=8)
    usable = agent_module._usable_entities(hero)[:5]
    scores = [float(e.get("explainability", 1.0) or 1.0) for e in usable]
    mid = float(median(scores)) or 1.0
    cards = body["transplant"]["cards"]
    for card, score in zip(cards, scores):
        assert card["affinity_multiplier"] == round(score / mid, 1)


def test_empty_upstream_still_200_with_broadened(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(agent_module, "qloo_insights_place", lambda *a, **k: {"entities": []})
    monkeypatch.setattr(
        agent_module._qloo_module, "qloo_insights_place", lambda *a, **k: {"entities": []}
    )
    response = client.post(
        "/api/transplant",
        json={"seeds": [{"id": "urn:entity:artist:test1"}], "dest_city": "London"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["broadened"] is True
    assert len(body["transplant"]["cards"]) == 5


def test_first_hit_not_broadened(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/api/transplant",
        json={"seeds": [{"id": "urn:entity:artist:test1"}], "dest_city": "London"},
    )
    assert response.status_code == 200
    assert response.json()["meta"]["broadened"] is False


def test_cache_hit_flag(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    payload = {"seeds": [{"id": "urn:entity:artist:test1"}], "dest_city": "London"}
    first = client.post("/api/transplant", json=payload)
    assert first.json()["meta"]["cache_hit"] is False
    second = client.post("/api/transplant", json=payload)
    assert second.status_code == 200
    assert second.json()["meta"]["cache_hit"] is True
    assert second.json()["meta"]["qloo_mode"] == "cache"


def test_422_envelopes(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    assert client.post("/api/transplant", json={}).status_code == 422
    assert client.post("/api/transplant", json={"seeds": []}).status_code == 422
    assert (
        client.post(
            "/api/transplant", json={"seeds": [{"id": "urn:entity:artist:test1"}]}
        ).status_code
        == 422
    )
    bad = client.post("/api/transplant", content="{bad json",
                      headers={"Content-Type": "application/json"})
    assert bad.status_code == 422
