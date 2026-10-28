"""Catalog contract tests: chips shape, cities, seed pairs."""

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_catalog_chips_shape() -> None:
    response = client.get("/api/catalog")
    assert response.status_code == 200
    chips = response.json()["chips"]
    assert len(chips) >= 12
    for chip in chips:
        assert chip["label"]
        assert chip["category"]
        assert chip["entity_id"].startswith("urn:entity:")


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
