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
    for city in ("London", "Toronto", "NYC", "Lagos", "Nairobi"):
        assert city in cities


def test_seed_pairs() -> None:
    response = client.get("/api/seed_pairs")
    assert response.status_code == 200
    pairs = response.json()
    assert len(pairs) == 4
    for pair in pairs:
        assert pair["from_home"]
        assert pair["dest_city"]
        assert isinstance(pair["seed_ids"], list) and len(pair["seed_ids"]) > 0
