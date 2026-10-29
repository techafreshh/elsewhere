"""LLM candidate extraction before /search (issue #43)."""

import json
import urllib.request

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


def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_TIMEOUT", raising=False)
    monkeypatch.setattr(cache_module, "CACHE_DIR", tmp_path)

    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in mock mode")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


def _hit(name, entity_id):
    return {
        "name": name,
        "entity_id": entity_id,
        "type": "urn:entity:movie",
        "disambiguation": f"{name} hit",
    }


class _FakeResponse:
    """Minimal urlopen context manager stub for OpenRouter replies."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def _openrouter_stub(content: str, status: int = 200):
    body = json.dumps(
        {"choices": [{"message": {"content": content}}]}
    ).encode("utf-8")

    def _fake(*args, **kwargs):
        return _FakeResponse(body, status=status)

    return _fake


def test_no_key_single_raw_phrase_search(monkeypatch, tmp_path) -> None:
    """No OPENROUTER_API_KEY: exactly one qloo_search with the raw phrase."""
    _isolated(monkeypatch, tmp_path)
    calls: list[str] = []

    def _fake_search(query):
        calls.append(query)
        return [_hit("Nollywood slow-burns", "urn:entity:movie:nollywood-slow-burn")]

    monkeypatch.setattr(agent_module, "qloo_search", _fake_search)
    phrase = "I love slow-burn Nollywood and quiet rooftops"
    candidates = agent_module.resolve_text_seed(phrase)
    assert calls == [phrase]
    assert candidates
    assert all(c["entity_id"].startswith("urn:entity:") for c in candidates)


def test_mocked_extractor_fans_out_with_search_budget(monkeypatch, tmp_path) -> None:
    """Mocked ["Nollywood", "quiet rooftops"]: both resolve, <=1 search each."""
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        agent_module,
        "extract_taste_candidates",
        lambda text: ["Nollywood", "quiet rooftops"],
    )
    calls: dict[str, int] = {}
    fixtures = {
        "Nollywood": [_hit("Nollywood slow-burns", "urn:entity:movie:nollywood-slow-burn")],
        "quiet rooftops": [_hit("Quiet rooftops", "urn:entity:tag:quiet-rooftops")],
    }

    def _fake_search(query):
        calls[query] = calls.get(query, 0) + 1
        return list(fixtures.get(query, []))

    monkeypatch.setattr(agent_module, "qloo_search", _fake_search)
    phrase = "I love slow-burn Nollywood and quiet rooftops"
    candidates = agent_module.resolve_text_seed(phrase)
    assert calls == {"Nollywood": 1, "quiet rooftops": 1}
    assert all(count <= 1 for count in calls.values())
    entity_ids = {c["entity_id"] for c in candidates}
    assert "urn:entity:movie:nollywood-slow-burn" in entity_ids
    assert "urn:entity:tag:quiet-rooftops" in entity_ids

    signals, confirmations, _labels = agent_module.resolve_seeds([{"text": phrase}])
    assert len(confirmations) == 1
    assert confirmations[0]["text"] == phrase
    merged_ids = {c["entity_id"] for c in confirmations[0]["candidates"]}
    assert "urn:entity:movie:nollywood-slow-burn" in merged_ids
    assert "urn:entity:tag:quiet-rooftops" in merged_ids
    assert signals and signals[-1] == confirmations[0]["candidates"][0]["entity_id"]


def test_extractor_none_falls_back_to_raw_phrase(monkeypatch, tmp_path) -> None:
    """Extractor returning None: raw-phrase search path, still 200 downstream."""
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(agent_module, "extract_taste_candidates", lambda text: None)
    calls: list[str] = []

    def _fake_search(query):
        calls.append(query)
        return [_hit("Nollywood slow-burns", "urn:entity:movie:nollywood-slow-burn")]

    monkeypatch.setattr(agent_module, "qloo_search", _fake_search)
    phrase = "slow-burn Nollywood"
    candidates = agent_module.resolve_text_seed(phrase)
    assert calls == [phrase]
    assert candidates


def test_garbage_extractor_output_end_to_end_still_200(monkeypatch, tmp_path) -> None:
    """Non-JSON extractor content: falls back to raw-phrase search, HTTP 200."""
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        urllib.request, "urlopen", _openrouter_stub("definitely not json {{{")
    )
    assert agent_module.extract_taste_candidates("slow-burn Nollywood") is None
    response = client.post(
        "/api/transplant",
        json={"seeds": [{"text": "slow-burn Nollywood"}], "dest_city": "Toronto"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["confirmations"]) == 1
    assert body["confirmations"][0]["candidates"]
    assert len(body["transplant"]["cards"]) == 5


def test_extractor_timeout_end_to_end_still_200(monkeypatch, tmp_path) -> None:
    """Extractor timeout/exception: raw-phrase fallback, HTTP 200."""
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def _timeout(*args, **kwargs):
        raise TimeoutError("simulated extractor timeout")

    monkeypatch.setattr(urllib.request, "urlopen", _timeout)
    assert agent_module.extract_taste_candidates("slow-burn Nollywood") is None
    response = client.post(
        "/api/transplant",
        json={"seeds": [{"text": "slow-burn Nollywood"}], "dest_city": "London"},
    )
    assert response.status_code == 200
    assert response.json()["confirmations"][0]["candidates"]
