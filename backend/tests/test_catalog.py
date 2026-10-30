"""Catalog contract tests: chips shape, cities, seed pairs."""

import re

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)

# Qloo IDs arrive as urn:entity:* URNs (mock fixtures) or bare UUIDs
# (live /search results, as committed to chips.json) — both are valid.
_UUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


def _is_entity_id(value: str) -> bool:
    return value.startswith("urn:entity:") or bool(re.fullmatch(_UUID_RE, value))


def test_catalog_chips_shape() -> None:
    response = client.get("/api/catalog")
    assert response.status_code == 200
    chips = response.json()["chips"]
    assert len(chips) >= 12
    for chip in chips:
        assert chip["label"]
        assert chip["category"]
        assert _is_entity_id(chip["entity_id"])


def test_catalog_cities() -> None:
    response = client.get("/api/catalog")
    assert response.status_code == 200
    cities = response.json()["cities"]
    # The 5-city shortlist grew into a world dictionary of geotyped rows.
    assert len(cities) >= 100
    names = {city.get("name") for city in cities if isinstance(city, dict)}
    for city in ("London", "Toronto", "New York", "Lagos", "Nairobi", "Berlin"):
        assert city in names
    aliases = {
        alias
        for city in cities
        if isinstance(city, dict)
        for alias in (city.get("aliases") or [])
    }
    assert "NYC" in aliases
    for city in cities:
        assert isinstance(city["lat"], (int, float))
        assert isinstance(city["lng"], (int, float))
        assert -90 <= city["lat"] <= 90
        assert -180 <= city["lng"] <= 180

