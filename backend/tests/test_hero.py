"""Hero entry-interaction contract: prompt box, chips, city picker, banner.

The hero is a client-rendered Vite SPA, so these tests assert on what the
server actually ships: GET / serves the built page, and the referenced JS
bundle carries the prompt/chips/city/banner wiring plus the taste-card
persistence against GET /api/catalog.
"""

import re

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def _served_bundle_js() -> str:
    """Return the concatenated JS served for GET / (index.html + assets)."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    html = response.text
    assert '<div id="root">' in html
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
    assert scripts, "built SPA script missing from served index.html"
    bundle = []
    for src in scripts:
        path = src if src.startswith("/") else f"/{src}"
        if path.startswith("http"):
            continue
        asset = client.get(path)
        assert asset.status_code == 200, path
        bundle.append(asset.text)
    assert bundle, "no servable JS bundle found for GET /"
    return "\n".join(bundle)


def test_hero_prompt_box_wired() -> None:
    bundle = _served_bundle_js()
    assert "prompt-input" in bundle
    assert "What does home feel like?" in bundle


def test_hero_chip_tray_and_catalog() -> None:
    bundle = _served_bundle_js()
    # Multi-select chip buttons keyed by entity id, grouped by category.
    assert "data-entity-id" in bundle
    assert "aria-pressed" in bundle
    assert "selected-tokens" in bundle
    assert "data-token-remove" in bundle
    for category in ("Music", "Food", "Screens", "Vibes"):
        assert category in bundle
    # Inlined fallback snapshot keeps >=12 chips without the API.
    assert bundle.count("urn:entity:") >= 12
    # Live catalog behind the same fetch the hero performs.
    response = client.get("/api/catalog")
    assert response.status_code == 200
    chips = response.json()["chips"]
    assert len(chips) >= 12
    assert {"Music", "Food", "Screens", "Vibes"} <= {
        chip["category"] for chip in chips
    }


def test_hero_city_combobox_wired() -> None:
    bundle = _served_bundle_js()
    assert "city-input" in bundle
    assert "city-list" in bundle
    assert "combobox" in bundle
    assert "listbox" in bundle
    assert "option" in bundle
    for city in ("London", "Toronto", "NYC", "Lagos", "Nairobi"):
        assert city in bundle


def test_hero_persistence_and_banner() -> None:
    bundle = _served_bundle_js()
    assert "fetch" in bundle
    assert "/api/catalog" in bundle
    assert "localStorage" in bundle
    assert "elsewhere.taste" in bundle
    assert "taste-banner" in bundle
    assert "transplanting" in bundle
