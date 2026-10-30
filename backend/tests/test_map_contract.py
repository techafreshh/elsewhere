"""Leaflet map plus brand kicker contract: map_center, 5 pins, brands.

Follows backend/tests/test_transplant.py (TestClient(app) plus the
``_isolated`` urlopen-blocking helper). The hero London transplant must
carry the spatial proof the Leaflet pane renders: a 2-float map_center,
exactly 5 map_pins equal to the card latlngs with card_index 0-4, and a
non-empty brands kicker row. The bundled SPA must contain the anchors the
black-box DOM probe drives (leaflet classes, kicker row, highlight).
"""

import json
import re
import urllib.request
from pathlib import Path

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


def _hero_london_body(monkeypatch, tmp_path) -> dict:
    _isolated(monkeypatch, tmp_path)
    seeds = [
        "urn:entity:artist:test-asake",
        "urn:entity:place:test-filmhouse",
        "urn:entity:place:test-owambe",
        "urn:entity:place:test-shawarma",
    ]
    response = client.post(
        "/api/transplant",
        json={"seeds": seeds, "dest_city": "London"},
    )
    assert response.status_code == 200
    return response.json()


def test_hero_map_center_is_london_pair(monkeypatch, tmp_path) -> None:
    body = _hero_london_body(monkeypatch, tmp_path)
    center = body["transplant"]["map_center"]
    assert isinstance(center, list) and len(center) == 2
    assert all(isinstance(coord, (int, float)) for coord in center)
    assert center == [51.5074, -0.1278]


def test_hero_five_pins_match_card_latlngs(monkeypatch, tmp_path) -> None:
    body = _hero_london_body(monkeypatch, tmp_path)
    cards = body["transplant"]["cards"]
    pins = body["transplant"]["map_pins"]
    assert len(cards) == 5
    assert len(pins) == 5
    for index, (pin, card) in enumerate(zip(pins, cards)):
        assert pin["card_index"] == index
        assert pin["latlng"] == card["latlng"]


def test_hero_brands_kicker_non_empty(monkeypatch, tmp_path) -> None:
    body = _hero_london_body(monkeypatch, tmp_path)
    brands = body["transplant"]["brands"]
    assert isinstance(brands, list) and len(brands) >= 1
    assert all(isinstance(name, str) and name for name in brands)


def test_empty_upstream_still_five_pins(monkeypatch, tmp_path) -> None:
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
    assert len(body["transplant"]["map_pins"]) == 5
    assert len(body["transplant"]["cards"]) == 5
    assert isinstance(body["transplant"]["map_center"], list)


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


def test_map_strings_in_served_bundle() -> None:
    """Leaflet container/marker classes, kicker row, highlight, mock brand."""
    bundle = _served_bundle_text()
    for anchor in (
        "leaflet-container",
        "leaflet-marker-icon",
        "brand-kicker-row",
        "brand-kicker",
        "card--highlight",
        "transplant-card",
        "data-index",
        "Eko Bite LDN",
        "/api/transplant",
    ):
        assert anchor in bundle, anchor
