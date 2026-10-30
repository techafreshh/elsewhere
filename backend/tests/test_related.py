"""POST /api/related: cross-domain correlated-taste suggestions."""

import urllib.request

from fastapi.testclient import TestClient

try:
    from backend.app import cache as cache_module
    from backend.app import main as main_module
    from backend.app.main import app
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import cache as cache_module
    from app import main as main_module
    from app.main import app

client = TestClient(app)

ID_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ID_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ID_RELATED_1 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
ID_RELATED_2 = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(cache_module, "CACHE_DIR", tmp_path)

    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in mock mode")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


def test_related_422s_on_bad_bodies(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    assert client.post("/api/related", content="{bad", headers={"Content-Type": "application/json"}).status_code == 422
    assert client.post("/api/related", json=[]).status_code == 422
    assert client.post("/api/related", json={"entity_ids": 42}).status_code == 422
    assert client.post("/api/related", json={"entity_ids": [42]}).status_code == 422
    assert (
        client.post("/api/related", json={"entity_ids": ["not-an-id at all"]}).status_code
        == 422
    )


def test_related_cold_start_falls_back_to_catalog(monkeypatch, tmp_path) -> None:
    """Empty selection is valid: the row fills from the catalog so the
    suggestion pills never render blank before the first pick."""
    _isolated(monkeypatch, tmp_path)
    response = client.post("/api/related", json={"entity_ids": []})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "catalog"
    results = body["results"]
    assert 0 < len(results) <= 10
    assert all(entry["label"] and entry["entity_id"] for entry in results)


def test_related_normalizes_and_excludes_selected(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)

    def _fake_related(entity_ids: list[str], take: int = 8) -> dict:
        assert entity_ids == [ID_A, ID_B]
        return {
            "entities": [
                {
                    "name": "Doja Cat",
                    "entity_id": ID_RELATED_1,
                    "types": ["urn:entity:artist"],
                },
                {
                    # Already selected — must come back out.
                    "name": "Selected Echo",
                    "entity_id": ID_A,
                    "types": ["urn:entity:artist"],
                },
                {"name": "Adidas", "entity_id": ID_RELATED_2, "types": ["urn:entity:brand"]},
                "garbage",
                {"name": ""},
                {"entity_id": ID_RELATED_1},
            ]
            + [
                {"name": f"Filler {i}", "entity_id": f"urn:entity:artist:filler-{i}"}
                for i in range(8)
            ]
        }

    monkeypatch.setattr(main_module, "qloo_related", _fake_related)
    response = client.post("/api/related", json={"entity_ids": [ID_A, ID_B]})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "qloo"
    results = body["results"]
    # Cap is 10 — the wizard filters suggestions per category section, so
    # the payload needs headroom above the visible row.
    assert len(results) == 10
    assert all(entry["entity_id"].lower() not in {ID_A, ID_B} for entry in results)
    assert results[0] == {"label": "Doja Cat", "entity_id": ID_RELATED_1, "type": "artist"}
    assert results[1]["label"] == "Adidas"
    assert results[1]["type"] == "brand"


def test_related_falls_back_to_catalog_chips(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)

    def _boom(entity_ids: list[str], take: int = 8) -> dict:
        raise RuntimeError("qloo down")

    chips = client.get("/api/catalog").json()["chips"]
    assert len(chips) >= 12
    excluded_id = next(
        chip["entity_id"] for chip in chips if chip.get("entity_id")
    )

    monkeypatch.setattr(main_module, "qloo_related", _boom)
    response = client.post("/api/related", json={"entity_ids": [excluded_id]})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "catalog"
    results = body["results"]
    assert 0 < len(results) <= 6
    assert all(entry["entity_id"] != excluded_id for entry in results)
    assert all(entry["label"] and entry["entity_id"] for entry in results)
