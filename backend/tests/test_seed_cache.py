"""Seed-cache script, key-removed drill, and behavior regression.

Warming runs in-process through ``run_transplant`` + ``cache_put`` (the
same code path as ``POST /api/transplant``); the drill then proves the
frozen pairs still serve full reveals with the Qloo key removed.
"""

import importlib.util
import json
import urllib.request
from pathlib import Path
from statistics import median

import pytest
from fastapi.testclient import TestClient

try:
    from backend.app import agent as agent_module
    from backend.app import cache as cache_module
    from backend.app.agent import _fallback_entities, build_insights_params
    from backend.app.cache import cache_key
    from backend.app.catalog import get_seed_pairs
    from backend.app.main import app
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import agent as agent_module
    from app import cache as cache_module
    from app.agent import _fallback_entities, build_insights_params
    from app.cache import cache_key
    from app.catalog import get_seed_pairs
    from app.main import app

client = TestClient(app)
_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "seed_cache.py"


def _load_seed_cache_module():
    """Import the warming script by path so tests exercise the real file."""
    spec = importlib.util.spec_from_file_location("seed_cache", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_cache = _load_seed_cache_module()


@pytest.fixture(autouse=True)
def _isolated_cache_and_mock_mode(monkeypatch, tmp_path):
    """Point the cache at a tmp dir and force mock mode (no key, no network)."""
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.setattr(cache_module, "CACHE_DIR", tmp_path)

    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in mock mode")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


def test_main_warms_four_five_card_files() -> None:
    assert seed_cache.main() == 0
    files = sorted(cache_module.CACHE_DIR.glob("*.json"))
    assert len(files) >= 4
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert len(payload["transplant"]["cards"]) == 5


def test_main_rerun_is_idempotent() -> None:
    assert seed_cache.main() == 0
    before = sorted(path.name for path in cache_module.CACHE_DIR.glob("*.json"))
    assert seed_cache.main() == 0
    after = sorted(path.name for path in cache_module.CACHE_DIR.glob("*.json"))
    assert before == after
    assert len(after) >= 4


def test_key_removed_drill_serves_all_pairs() -> None:
    assert seed_cache.main() == 0
    pairs = get_seed_pairs()
    assert len(pairs) == 4
    for pair in pairs:
        response = client.post(
            "/api/transplant",
            json={"seeds": pair["seed_ids"], "dest_city": pair["dest_city"]},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["transplant"]["cards"]) == 5
        assert body["meta"]["qloo_mode"] in ("mock", "cache")


def test_warmed_pair_repost_is_cache_hit() -> None:
    assert seed_cache.main() == 0
    pair = get_seed_pairs()[0]
    response = client.post(
        "/api/transplant",
        json={"seeds": pair["seed_ids"], "dest_city": pair["dest_city"]},
    )
    assert response.status_code == 200
    assert response.json()["meta"]["cache_hit"] is True
    assert response.json()["meta"]["qloo_mode"] == "cache"


def test_seed_pairs_are_four_shaped() -> None:
    pairs = get_seed_pairs()
    assert len(pairs) == 4
    for pair in pairs:
        assert isinstance(pair["seed_ids"], list) and pair["seed_ids"]
        assert isinstance(pair["dest_city"], str) and pair["dest_city"].strip()


def test_build_insights_params_late_night_filter() -> None:
    params = build_insights_params("London", None, 8, ["late-night"])
    assert params["filter.hours"] == "late_night"


def test_build_insights_params_cheaper_filter() -> None:
    params = build_insights_params("London", None, 8, ["cheaper"])
    assert params["filter.price_level"] == "1,2"


def test_build_insights_params_merges_both_refinements() -> None:
    params = build_insights_params("London", None, 8, ["late-night", "cheaper"])
    assert params["filter.hours"] == "late_night"
    assert params["filter.price_level"] == "1,2"


def test_fallback_ladder_broadened_fill(monkeypatch) -> None:
    monkeypatch.setattr(agent_module, "qloo_insights_place", lambda *a, **k: {"entities": []})
    monkeypatch.setattr(
        agent_module._qloo_module, "qloo_insights_place", lambda *a, **k: {"entities": []}
    )
    entities, broadened = _fallback_entities("London", ["urn:entity:artist:test1"])
    assert broadened is True
    assert len(entities) >= 5
    payload = agent_module.run_transplant(["urn:entity:artist:test1"], "London")
    assert len(payload["transplant"]["cards"]) == 5
    assert payload["meta"]["broadened"] is True


def test_affinity_median_math() -> None:
    payload = agent_module.run_transplant(["urn:entity:artist:test1"], "London")
    hero = agent_module._hero_call("London", ["urn:entity:artist:test1"], take=8)
    usable = agent_module._usable_entities(hero)[:5]
    scores = [float(entity.get("explainability", 1.0) or 1.0) for entity in usable]
    mid = float(median(scores)) or 1.0
    for card, score in zip(payload["transplant"]["cards"], scores):
        assert card["affinity_multiplier"] == round(score / mid, 1)


def test_cache_key_order_invariant() -> None:
    assert cache_key(["b", "a"], "London") == cache_key(["a", "b"], "London")
    assert len(cache_key(["a", "b"], "London")) == 64


def test_mock_e2e_hero_pair_returns_five_why_you_cards() -> None:
    pair = get_seed_pairs()[0]
    response = client.post(
        "/api/transplant",
        json={"seeds": pair["seed_ids"], "dest_city": pair["dest_city"]},
    )
    assert response.status_code == 200
    cards = response.json()["transplant"]["cards"]
    assert len(cards) == 5
    for card in cards:
        assert card["name"]
        assert card["why_you"]
        assert isinstance(card["taste_path"], list) and len(card["taste_path"]) == 3
