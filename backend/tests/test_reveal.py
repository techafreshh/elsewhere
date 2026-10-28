"""Two-pane reveal contract: hero shape plus served-bundle DOM anchors.

Follows backend/tests/test_transplant.py (TestClient(app) style). The hero
shape test posts the frozen London seed pair to POST /api/transplant and
asserts the renderer inputs: 3+ tourist rows, exactly 5 cards each with
non-empty why_you, and both summaries >20 chars and unequal. The bundle
tests fetch GET / from the committed frontend/dist artifact, follow its
local asset references, and assert every acceptance id/class/fetch-path /
fallback string the black-box DOM probe drives.
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


def test_hero_london_reveal_shape(monkeypatch, tmp_path) -> None:
    """Hero seeds to London: 3+ tourist rows, 5 why_you cards, long unequal summaries."""
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/api/transplant",
        json={"seeds": _hero_london_seeds(), "dest_city": "London"},
    )
    assert response.status_code == 200
    body = response.json()
    items = body["tourist"]["items"]
    assert isinstance(items, list) and len(items) >= 3
    assert all(isinstance(entry, str) and entry for entry in items)
    cards = body["transplant"]["cards"]
    assert len(cards) == 5
    for card in cards:
        assert isinstance(card.get("why_you"), str) and len(card["why_you"]) > 0
    tourist_summary = body["tourist"]["summary"]
    transplant_summary = body["transplant"]["summary"]
    assert isinstance(tourist_summary, str) and len(tourist_summary) > 20
    assert isinstance(transplant_summary, str) and len(transplant_summary) > 20
    assert tourist_summary != transplant_summary


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


def test_reveal_ids_in_served_bundle() -> None:
    """Every DOM anchor the probe drives survives minification into dist."""
    bundle = _served_bundle_text()
    for anchor in (
        "tourist-pane",
        "transplant-pane",
        "tourist-summary",
        "transplant-summary",
        "transplant-submit",
        "transplant-status",
        "city-input",
        "reveal",
        "why-you",
        "tourist-row",
        "transplant-card",
        "data-entity-id",
    ):
        assert anchor in bundle, anchor


def test_reveal_fetch_paths_in_served_bundle() -> None:
    """Optimistic /agui SSE attempt with /api/transplant JSON fallback, plus taste persistence."""
    bundle = _served_bundle_text()
    assert "/agui" in bundle
    assert "/api/transplant" in bundle
    assert "localStorage" in bundle
    assert "elsewhere.taste" in bundle
    assert bundle.count("urn:entity:") >= 12


def test_reveal_template_floor_in_served_bundle() -> None:
    """Offline template copy: Tourist/transplant summaries, generic rows, Because cards."""
    bundle = _served_bundle_text()
    assert "Tourist take on" in bundle
    assert "transplant" in bundle
    assert "Top 10" in bundle
    assert "Because" in bundle
