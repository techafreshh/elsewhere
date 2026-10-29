"""Per-card why-you lines from the transplant LLM call (issue #44).

Mocked-LLM lines keyed by card name must flow through the existing
``transplant.cards`` override in ``agent.build_payload``; unknown names are
ignored; missing keys or LLM failures degrade to template copy, never a 500.
"""

import json
import re
import urllib.request

from fastapi.testclient import TestClient

try:
    from backend.app import agent as agent_module
    from backend.app import agui as agui_module
    from backend.app import cache as cache_module
    from backend.app import summarizer
    from backend.app.main import app
    from backend.app.summarizer import _parse_why_you, summarize_panes
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import agent as agent_module
    from app import agui as agui_module
    from app import cache as cache_module
    from app import summarizer
    from app.main import app
    from app.summarizer import _parse_why_you, summarize_panes

client = TestClient(app)


def _forbid_network(monkeypatch):
    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in why-you tests")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.setattr(cache_module, "CACHE_DIR", tmp_path)
    _forbid_network(monkeypatch)


def _why_you_echo_fake():
    """Fake ``_chat_once``: pane summaries plus why-you lines for asked names."""

    def _fake(messages, model=None, **kwargs):
        try:
            text = json.dumps(messages)
        except (TypeError, ValueError):
            return "A fine pane summary."
        if "why-you line" not in text:
            return "A fine pane summary."
        tail = messages[-1]["content"].split("exactly these cards:")[-1]
        names = re.findall(r'"([^"]+)"', tail)
        payload = {name: f"Model line for {name}" for name in names}
        payload["Invented Place"] = "should be ignored"
        return json.dumps(payload)

    return _fake


def test_model_why_you_flows_through_payload_unknown_ignored(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(summarizer, "_chat_once", _why_you_echo_fake())
    payload = agent_module.build_payload(
        ["urn:entity:artist:test1"], [], ["afrobeats"], "London"
    )
    cards = payload["transplant"]["cards"]
    assert len(cards) == 5
    for card in cards:
        assert card["why_you"] == f"Model line for {card['name']}"
    assert all(card["name"] != "Invented Place" for card in cards)


def test_parse_why_you_strict(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    names = ["Eko Bite", "Suya Co"]
    parsed = _parse_why_you(
        json.dumps(
            {
                "Eko Bite": "  Model line one  ",
                "Suya Co": "x" * 500,
                "Invented Place": "should be ignored",
                "Empty Line": "",
            }
        ),
        names,
    )
    assert parsed == {"Eko Bite": "Model line one", "Suya Co": "x" * 200}
    fenced = "```json\n" + json.dumps({"Eko Bite": "Fenced line"}) + "\n```"
    assert _parse_why_you(fenced, names) == {"Eko Bite": "Fenced line"}
    assert _parse_why_you("not json at all", names) == {}
    assert _parse_why_you(None, names) == {}


def test_summarize_panes_mocked_third_call(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _forbid_network(monkeypatch)
    calls = []

    def _fake(messages, model=None, **kwargs):
        calls.append(messages)
        if len(calls) < 3:
            return f"pane summary {len(calls)}"
        return json.dumps({"Eko Bite": "Model line one", "Nowhere": "ignored"})

    monkeypatch.setattr(summarizer, "_chat_once", _fake)
    result = summarize_panes("Asake", {"cards": [{"name": "Eko Bite"}]})
    assert len(calls) == 3
    cards = result["transplant"]["cards"]
    assert cards == [{"name": "Eko Bite", "why_you": "Model line one"}]
    assert result["tourist"]["summary"] == "pane summary 1"
    assert result["transplant"]["summary"] == "pane summary 2"


def test_no_key_keeps_template_why_you(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    payload = agent_module.build_payload(
        ["urn:entity:artist:test1"], [], ["afrobeats"], "London"
    )
    cards = payload["transplant"]["cards"]
    assert len(cards) == 5
    for card in cards:
        assert card["why_you"].startswith("Because ")


def test_third_call_exception_still_200_with_five_cards(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = {"n": 0}

    def _fake(messages, model=None, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            return f"pane summary {calls['n']}"
        raise RuntimeError("why-you boom")

    monkeypatch.setattr(summarizer, "_chat_once", _fake)
    response = client.post(
        "/api/transplant",
        json={"seeds": [{"id": "urn:entity:artist:test1"}], "dest_city": "London"},
    )
    assert response.status_code == 200
    cards = response.json()["transplant"]["cards"]
    assert len(cards) == 5
    for card in cards:
        assert card["why_you"].strip()


def test_agui_stream_shows_model_lines(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(summarizer, "_chat_once", _why_you_echo_fake())
    frames = list(
        agui_module.iter_transplant_events(["urn:entity:artist:test1"], "London")
    )
    assert frames and all(frame.startswith("data: ") for frame in frames)
    card_frames = [
        json.loads(frame[len("data: "):])
        for frame in frames
        if frame.startswith("data: ")
    ]
    deltas = [
        body
        for body in card_frames
        if body.get("type") == "STATE_DELTA" and isinstance(body.get("delta"), dict)
    ]
    streamed = [body for body in deltas if isinstance(body["delta"].get("cards"), list)]
    assert streamed
    final = streamed[-1]["delta"]["cards"]
    assert len(final) == 5
    for card in final:
        assert card["why_you"] == f"Model line for {card['name']}"
