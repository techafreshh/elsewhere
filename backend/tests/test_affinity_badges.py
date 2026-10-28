"""Affinity badges with count-up plus citations taste-path chains.

Hero-shape test posts the frozen London seed pair to POST /api/transplant
and asserts the renderer inputs: exactly 5 cards each with a finite numeric
affinity_multiplier and a 3-element taste_path of non-empty strings. The
bundle test fetches GET / from the committed frontend/dist artifact, follows
its local asset references, and asserts the badge/path anchors survive
minification into the served asset.
"""

import json
import re
import urllib.request
from pathlib import Path

from fastapi.testclient import TestClient

try:
    from backend.app import cache as cache_module
    from backend.app.main import app
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
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
    pairs = json.loads((REPO_ROOT / "backend" / "data" / "seed_pairs.json").read_text())
    hero = next(p for p in pairs if p.get("dest_city") == "London")
    return list(hero["seed_ids"])


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
