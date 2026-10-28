"""Cache behavior: order-insensitive hits, city/refinement misses, gitignore guard."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

try:
    from backend.app import cache as cache_module
    from backend.app.cache import cache_get, cache_key, cache_put
    from backend.app.main import app
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import cache as cache_module
    from app.cache import cache_get, cache_key, cache_put
    from app.main import app

client = TestClient(app)
GITIGNORE = Path(__file__).resolve().parents[2] / ".gitignore"


@pytest.fixture(autouse=True)
def _isolated_cache_and_mock_mode(monkeypatch, tmp_path):
    """Point the cache at a tmp dir and force mock mode (no key, no network)."""
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.setattr(cache_module, "CACHE_DIR", tmp_path)


def test_cache_key_order_insensitive() -> None:
    assert cache_key(["b", "a"], "London") == cache_key(["a", "b"], "London")
    assert len(cache_key(["a", "b"], "London")) == 64


def test_cache_key_city_and_refinement_sensitive() -> None:
    assert cache_key(["a", "b"], "London") != cache_key(["a", "b"], "Toronto")
    assert cache_key(["a", "b"], "London") != cache_key(["a", "b"], "London", ["late-night"])
    assert cache_key(["a", "b"], "London") == cache_key(["a", "b"], "  london  ")


def test_put_then_get_round_trip() -> None:
    key = cache_key(["a", "b"], "London")
    assert cache_get(key) is None
    payload = {"transplant": {"cards": [1, 2, 3]}, "meta": {"cache_hit": False}}
    cache_put(key, payload)
    assert cache_get(key) == payload


def test_corrupt_file_degrades_to_miss() -> None:
    key = cache_key(["a", "b"], "London")
    cache_put(key, {"transplant": {"cards": [1]}})
    (cache_module.CACHE_DIR / f"{key}.json").write_text("{truncated", encoding="utf-8")
    assert cache_get(key) is None


def test_transplant_reordered_seeds_hit_with_identical_cards() -> None:
    seeds = [{"id": "a"}, {"id": "b"}]
    first = client.post("/api/transplant", json={"seeds": seeds, "dest_city": "London"})
    assert first.status_code == 200
    assert first.json()["meta"]["cache_hit"] is False
    second = client.post(
        "/api/transplant", json={"seeds": list(reversed(seeds)), "dest_city": "London"}
    )
    assert second.status_code == 200
    assert second.json()["meta"]["cache_hit"] is True
    assert len(second.json()["transplant"]["cards"]) == len(first.json()["transplant"]["cards"])
    assert second.json()["transplant"]["cards"] == first.json()["transplant"]["cards"]


def test_transplant_different_city_misses() -> None:
    seeds = ["a", "b"]
    client.post("/api/transplant", json={"seeds": seeds, "dest_city": "London"})
    response = client.post("/api/transplant", json={"seeds": seeds, "dest_city": "Toronto"})
    assert response.status_code == 200
    assert response.json()["meta"]["cache_hit"] is False


def test_transplant_different_refinements_miss() -> None:
    seeds = ["a", "b"]
    client.post("/api/transplant", json={"seeds": seeds, "dest_city": "London"})
    response = client.post(
        "/api/transplant",
        json={"seeds": seeds, "dest_city": "London", "refinements": ["late-night"]},
    )
    assert response.status_code == 200
    assert response.json()["meta"]["cache_hit"] is False


def test_transplant_corrupt_cache_file_misses_without_500() -> None:
    seeds = ["a", "b"]
    client.post("/api/transplant", json={"seeds": seeds, "dest_city": "London"})
    key = cache_key(seeds, "London", [])
    (cache_module.CACHE_DIR / f"{key}.json").write_text("{truncated", encoding="utf-8")
    response = client.post("/api/transplant", json={"seeds": seeds, "dest_city": "London"})
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["cache_hit"] is False
    assert len(body["transplant"]["cards"]) >= 1


def test_transplant_malformed_meta_never_500() -> None:
    seeds = ["a", "b"]
    key = cache_key(seeds, "London", [])
    cache_put(key, {"transplant": {"cards": []}, "meta": "bad"})
    response = client.post("/api/transplant", json={"seeds": seeds, "dest_city": "London"})
    assert response.status_code == 200
    assert response.json()["meta"]["cache_hit"] is True


def test_gitignore_covers_env_and_cache() -> None:
    text = GITIGNORE.read_text(encoding="utf-8")
    assert ".env" in text
    assert "data/cache/" in text


def test_cache_round_trip_survives_json_serialization() -> None:
    key = cache_key(["a", "b"], "London")
    payload = {"transplant": {"cards": [{"name": "x"}]}, "meta": {"cache_hit": False}}
    cache_put(key, payload)
    raw = json.loads((cache_module.CACHE_DIR / f"{key}.json").read_text(encoding="utf-8"))
    assert raw == payload
