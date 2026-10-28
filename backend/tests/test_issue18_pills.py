"""Free-text approval pills plus refinement re-runs (issue #18).

Backend contract pins for the frontend approval/refinement lap: free text
resolves into `confirmations` candidates carrying name, `urn:entity:` id,
and numeric confidence; a refinement re-post echoes
`meta.applied_refinements`, carries non-empty `notices`, and still renders
5 cards; an identical second post hits the disk cache (the <10s round-trip
path — same `cache_key`, no recompute).
"""

import time
import urllib.request

from fastapi.testclient import TestClient

try:
    from backend.app import cache as cache_module
    from backend.app.main import app
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import cache as cache_module
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


def test_free_text_confirmations_shape(monkeypatch, tmp_path) -> None:
    """`slow-burn Nollywood` yields text plus a named, URN, confident candidate."""
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/api/transplant",
        json={"seeds": [{"text": "slow-burn Nollywood"}], "dest_city": "London"},
    )
    assert response.status_code == 200
    body = response.json()
    confirmations = body["confirmations"]
    assert len(confirmations) == 1
    assert confirmations[0]["text"] == "slow-burn Nollywood"
    candidates = confirmations[0]["candidates"]
    assert candidates, "resolvable text must surface candidates"
    first = candidates[0]
    assert isinstance(first["name"], str) and first["name"]
    assert isinstance(first["entity_id"], str)
    assert first["entity_id"].startswith("urn:entity:")
    assert isinstance(first["confidence"], (int, float))
    assert 0 <= first["confidence"] <= 1
    assert len(body["transplant"]["cards"]) == 5


def test_frontend_onsubmit_mixed_seeds_shape(monkeypatch, tmp_path) -> None:
    """Mixed chip-plus-`{text}` body (exactly what `onSubmit` sends) resolves."""
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/api/transplant",
        json={
            "seeds": ["urn:entity:artist:asake", {"text": "slow-burn Nollywood"}],
            "dest_city": "London",
        },
    )
    assert response.status_code == 200
    body = response.json()
    confirmations = body["confirmations"]
    assert len(confirmations) == 1
    assert confirmations[0]["text"] == "slow-burn Nollywood"
    assert confirmations[0]["candidates"], "dict-form text must surface candidates"
    assert len(body["transplant"]["cards"]) == 5


def test_refinement_echo_notices_and_five_cards(monkeypatch, tmp_path) -> None:
    """`refinements: [\"late-night\"]` echoes, notices, and keeps 5 cards."""
    _isolated(monkeypatch, tmp_path)
    base = {"seeds": [{"text": "slow-burn Nollywood"}], "dest_city": "London"}
    first = client.post("/api/transplant", json=base)
    assert first.status_code == 200
    refined = client.post(
        "/api/transplant", json={**base, "refinements": ["late-night"]}
    )
    assert refined.status_code == 200
    body = refined.json()
    assert body["meta"]["applied_refinements"] == ["late-night"]
    assert isinstance(body["notices"], list) and len(body["notices"]) > 0
    assert all(isinstance(entry, str) and entry for entry in body["notices"])
    assert len(body["transplant"]["cards"]) == 5


def test_refinement_round_trip_cached(monkeypatch, tmp_path) -> None:
    """Identical refinement re-post hits the cache in well under 10s."""
    _isolated(monkeypatch, tmp_path)
    payload = {
        "seeds": [{"text": "slow-burn Nollywood"}],
        "dest_city": "London",
        "refinements": ["late-night"],
    }
    first = client.post("/api/transplant", json=payload)
    assert first.status_code == 200
    assert first.json()["meta"]["cache_hit"] is False
    started = time.perf_counter()
    second = client.post("/api/transplant", json=payload)
    elapsed = time.perf_counter() - started
    assert second.status_code == 200
    assert second.json()["meta"]["cache_hit"] is True
    assert len(second.json()["transplant"]["cards"]) == 5
    assert elapsed < 10
