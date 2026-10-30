"""Affinity badges with count-up plus citations taste-path chains.

Hero-shape test posts synthetic chip seeds to POST /api/transplant and
asserts the renderer inputs: exactly 5 cards each with a finite numeric
affinity_multiplier and a 3-element taste_path of non-empty strings. The
score test pins the live-shape affinity math (explainability on mock
fixtures, popularity live, rank-decay floor) end to end. The bundle test
fetches GET / from the committed frontend/dist artifact, follows its local
asset references, and asserts the badge/path anchors survive minification
into the served asset.
"""

import json
import re
import urllib.request
from pathlib import Path
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

REPO_ROOT = Path(__file__).resolve().parents[2]


def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.setattr(cache_module, "CACHE_DIR", tmp_path)

    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in mock mode")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


def _hero_london_seeds() -> list[str]:
    """Synthetic chip seeds — mock mode ignores IDs, so any valid shape works."""
    return [
        "urn:entity:artist:test-asake",
        "urn:entity:place:test-filmhouse",
        "urn:entity:place:test-owambe",
        "urn:entity:place:test-shawarma",
    ]


def test_hero_cards_carry_affinity_and_taste_path(monkeypatch, tmp_path) -> None:
    """Hero seeds to London: 5 cards, finite multiplier, 3-string path each."""
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/api/transplant",
        json={"seeds": _hero_london_seeds(), "dest_city": "London"},
    )
    assert response.status_code == 200
    cards = response.json()["transplant"]["cards"]
    assert len(cards) == 5
    for card in cards:
        multiplier = card.get("affinity_multiplier")
        assert isinstance(multiplier, (int, float))
        assert multiplier == multiplier and abs(multiplier) != float("inf")
        path = card.get("taste_path")
        assert isinstance(path, list) and len(path) == 3
        assert all(isinstance(entry, str) and len(entry) > 0 for entry in path)


def test_affinity_math_parity_with_card_builder() -> None:
    """Card multipliers equal score/median clamped — mock explainability path.

    Live /v2/insights returns no explainability field (only popularity, with
    results in affinity order); mock fixtures carry explainability. Either
    way the card math must be score/median clamped to 0.6..3.0.
    """
    hero = agent_module._hero_call("London", ["urn:entity:artist:test1"], take=8)
    usable = agent_module._usable_entities(hero)[:5]
    assert usable, "mock fixture must supply hero entities"
    scores = [
        agent_module._entity_score(entity, rank, len(usable))
        for rank, entity in enumerate(usable)
    ]
    mid = float(median(scores))
    for card, score in zip(
        agent_module.run_transplant(["urn:entity:artist:test1"], "London")["transplant"]["cards"],
        scores,
    ):
        expected = round(max(0.6, min(3.0, score / mid)), 1)
        assert card["affinity_multiplier"] == expected


def test_entity_score_prefers_explainability_then_popularity() -> None:
    """Field precedence: explainability, popularity, then rank decay."""
    assert agent_module._entity_score({"explainability": 0.7}, 0, 5) == 0.7
    assert agent_module._entity_score({"popularity": 0.42}, 0, 5) == 0.42
    assert agent_module._entity_score({}, 2, 5) == 0.6  # (5 - 2) / 5
    assert agent_module._entity_score({"popularity": 0.0}, 1, 5) == 0.8  # zero-pop floor
    assert agent_module._entity_score({"explainability": "nan-ish"}, 0, 5) == 1.0


def _served_bundle_text() -> str:
    """GET / plus every local script/link asset it references, concatenated."""
    index = client.get("/")
    assert index.status_code == 200
    html = index.text
    assert 'id="root"' in html
    refs = re.findall(r'(?:src|href)="(/[^"]+)"', html)
    assert refs, "served index.html references no local assets"
    parts = [html]
    for ref in dict.fromkeys(refs):
        asset = client.get(ref)
        assert asset.status_code == 200, ref
        parts.append(asset.text)
    return "\n".join(parts)


def test_affinity_badge_anchors_in_served_bundle() -> None:
    """Badge and citation markup survive minification into dist."""
    bundle = _served_bundle_text()
    for anchor in (
        "affinity-badge",
        "taste-path",
        "× your graph",
    ):
        assert anchor in bundle, anchor
