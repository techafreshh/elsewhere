"""Free-text resolve plus approval confirmations (issue #8)."""

import urllib.request

from fastapi.testclient import TestClient

try:
    from backend.app import cache as cache_module
    from backend.app.cache import cache_key
    from backend.app.main import app
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import cache as cache_module
    from app.cache import cache_key
    from app.main import app

client = TestClient(app)


def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.setattr(cache_module, "CACHE_DIR", tmp_path)

    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in mock mode")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


def test_text_seed_resolves_to_confirmable_candidate(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/api/transplant",
        json={"seeds": [{"text": "slow-burn Nollywood"}], "dest_city": "Toronto"},
    )
    assert response.status_code == 200
    body = response.json()
    confirmations = body["confirmations"]
    assert len(confirmations) == 1
    assert confirmations[0]["text"] == "slow-burn Nollywood"
    candidates = confirmations[0]["candidates"]
    assert candidates, "resolved text must surface candidates"
    first = candidates[0]
    assert first["name"]
    assert first["entity_id"]
    assert first["entity_id"].startswith("urn:entity:")
    assert isinstance(first["confidence"], (int, float))
    assert 0 <= first["confidence"] <= 1
    assert len(body["transplant"]["cards"]) == 5


def test_gibberish_text_unresolved_but_still_five_cards(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/api/transplant",
        json={"seeds": [{"text": "zzzzqqq-nonexistent-xyz"}], "dest_city": "London"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["transplant"]["cards"]) == 5
    confirmations = body.get("confirmations", [])
    assert confirmations and confirmations[0]["candidates"] == []
    assert isinstance(body.get("note"), str) and body["note"]


def test_chip_only_passes_through_with_empty_confirmations(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/api/transplant",
        json={"seeds": [{"id": "urn:entity:artist:asake"}], "dest_city": "London"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["confirmations"] == []
    assert "note" not in body
    assert len(body["transplant"]["cards"]) == 5


def test_mixed_chip_plus_text_keys_on_chip_entity(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    chip = "urn:entity:artist:asake"
    payload = {
        "seeds": [{"id": chip}, {"text": "slow-burn Nollywood"}],
        "dest_city": "London",
    }
    first = client.post("/api/transplant", json=payload)
    assert first.status_code == 200
    assert first.json()["meta"]["cache_hit"] is False
    assert len(first.json()["transplant"]["cards"]) == 5
    # Effective key feeds on the chip plus the resolved top candidate.
    top_entity = first.json()["confirmations"][0]["candidates"][0]["entity_id"]
    expected = cache_key([chip, top_entity], "London", [])
    assert (cache_module.CACHE_DIR / f"{expected}.json").exists()
    second = client.post("/api/transplant", json=payload)
    assert second.status_code == 200
    assert second.json()["meta"]["cache_hit"] is True
    assert len(second.json()["transplant"]["cards"]) == 5
