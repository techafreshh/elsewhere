"""AG-UI SSE contract: headers, event order, secrets, free-text sequencing, 422s."""

import json
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


def _collect_events(raw: str) -> list[dict]:
    events: list[dict] = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload:
                events.append(json.loads(payload))
    return events


def test_streams_run_started_to_finished(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/agui",
        json={"seeds": [{"id": "urn:entity:artist:test1"}], "dest_city": "London"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    raw = response.text
    assert "RUN_STARTED" in raw
    assert "RUN_FINISHED" in raw
    assert raw.index("RUN_STARTED") < raw.index("RUN_FINISHED")


def test_contains_place_tool_and_state_delta_without_secrets(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("QLOO_API_KEY", "sk-test-agui-secret-xyz")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter-secret-abc")
    live_qloo = "sk-test-agui-secret-xyz"
    response = client.post(
        "/agui",
        json={"seeds": [{"id": "urn:entity:artist:test1"}], "dest_city": "London"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    raw = response.text
    events = _collect_events(raw)
    starts = [e for e in events if e.get("type") == "TOOL_CALL_START"]
    assert any(e.get("toolCallName") == "qloo_insights_place" for e in starts)
    deltas = [e for e in events if e.get("type") == "STATE_DELTA"]
    assert deltas
    assert any(
        isinstance(e.get("delta"), dict)
        and ("cards" in e["delta"] or "pins" in e["delta"])
        for e in deltas
    )
    assert "sk-" not in raw
    assert live_qloo not in raw


def test_free_text_orders_search_before_place(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/agui",
        json={"seeds": [{"text": "slow-burn Nollywood"}], "dest_city": "London"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    raw = response.text
    assert "qloo_search" in raw
    assert "qloo_insights_place" in raw
    assert raw.index("qloo_search") < raw.index("qloo_insights_place")


def test_tool_names_ordered_and_cards_delta_before_finish(monkeypatch, tmp_path) -> None:
    """The four loop tools stream as TOOL_CALL_START in loop order and a
    cards STATE_DELTA precedes RUN_FINISHED (the progressive-reveal feed)."""
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/agui",
        json={
            "seeds": [{"text": "slow-burn Nollywood"}, {"id": "urn:entity:artist:test1"}],
            "dest_city": "London",
        },
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    events = _collect_events(response.text)
    starts = [e for e in events if e.get("type") == "TOOL_CALL_START"]
    names = [e.get("toolCallName") for e in starts]
    expected = ["qloo_search", "qloo_insights_place", "qloo_insights_brand", "summarize_panes"]
    for name in expected:
        assert name in names, name
    order = [names.index(name) for name in expected]
    assert order == sorted(order)
    kinds = [e.get("type") for e in events]
    assert "RUN_FINISHED" in kinds
    finish_at = kinds.index("RUN_FINISHED")
    card_deltas = [
        i
        for i, e in enumerate(events)
        if e.get("type") == "STATE_DELTA"
        and isinstance(e.get("delta"), dict)
        and e["delta"].get("cards")
    ]
    assert card_deltas, "no STATE_DELTA carries cards"
    assert card_deltas[0] < finish_at


def test_agui_422_envelopes(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    assert client.post("/agui", json={}).status_code == 422
    assert client.post("/agui", json={"seeds": []}).status_code == 422
    assert (
        client.post(
            "/agui", json={"seeds": [{"id": "urn:entity:artist:test1"}]}
        ).status_code
        == 422
    )
    bad = client.post(
        "/agui", content="{bad json", headers={"Content-Type": "application/json"}
    )
    assert bad.status_code == 422
