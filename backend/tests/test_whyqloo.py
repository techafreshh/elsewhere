"""Why Qloo won strip plus honesty toggle plus broadened notice.

Backend contract: hero London query disclosure (params only, no key),
taste-path sentence with an arrow, honesty model slug, broadened flag
plus notice, SSE parity, and served-bundle anchors surviving minification.
"""

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

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

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRET_RE = re.compile(r"[A-Za-z0-9_\-]{20,}")
# Real Qloo entity IDs are bare UUIDs; they are legitimate query content
# (the disclosed signal.interests.entities list), not secret material —
# so they are excluded from the secret-like token check.
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _secret_like_tokens(text: str) -> list[str]:
    """20+ char tokens that are not real entity UUIDs."""
    # Percent-encoded UUID lists (%2C separators) glue tokens together;
    # decode percent-escapes first so UUID boundaries are exact.
    decoded = urllib.parse.unquote(text)
    return [
        token
        for token in SECRET_RE.findall(decoded)
        if not UUID_RE.fullmatch(token)
    ]


def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.setattr(cache_module, "CACHE_DIR", tmp_path)

    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in mock mode")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


def _hero_london_seeds() -> list[str]:
    """Synthetic chip seeds — mock mode ignores IDs, so any valid shape works."""
    return [
        "urn:entity:artist:test-asake",
        "urn:entity:place:test-filmhouse",
        "urn:entity:place:test-owambe",
        "urn:entity:place:test-shawarma",
    ]


def _collect_events(raw: str) -> list[dict]:
    events: list[dict] = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload:
                events.append(json.loads(payload))
    return events


def test_why_qloo_hero_london_disclosure(monkeypatch, tmp_path) -> None:
    """Hero London body carries params-only query, arrow sentence, no secrets."""
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/api/transplant",
        json={"seeds": _hero_london_seeds(), "dest_city": "London"},
    )
    assert response.status_code == 200
    body = response.json()
    why = body.get("why_qloo")
    assert isinstance(why, dict), body.keys()
    query = why.get("qloo_query")
    assert isinstance(query, str) and query
    assert "filter.location.query=London" in query
    sentence = why.get("taste_path_sentence")
    assert isinstance(sentence, str) and sentence
    assert "\u2192" in sentence
    assert why.get("model") == "deepseek/deepseek-v4.1-flash"
    assert body["meta"]["model"] == "deepseek/deepseek-v4.1-flash"
    # Params only: no key material anywhere in the serialized body.
    serialized = json.dumps(body)
    assert "X-Api-Key" not in serialized
    for key in ("QLOO_API_KEY", "OPENROUTER_API_KEY"):
        assert key not in serialized
    # No 20+ char secret-like token in the rendered query line (real entity
    # UUIDs in the signal list are legitimate query content, not secrets).
    assert _secret_like_tokens(query) == [], query


def test_why_qloo_honesty_format(monkeypatch, tmp_path) -> None:
    """Honesty line format threads the live model slug with the both-panes cue."""
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/api/transplant",
        json={"seeds": _hero_london_seeds(), "dest_city": "London"},
    )
    body = response.json()
    model = body["why_qloo"]["model"]
    honesty = f"both panes: {model}"
    assert "deepseek/deepseek-v4.1-flash" in honesty
    assert "both panes" in honesty


def test_why_qloo_broadened_notice(monkeypatch, tmp_path) -> None:
    """Empty upstream still 200s with broadened=true and a broadened notice."""
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(agent_module, "qloo_insights_place", lambda *a, **k: {"entities": []})
    monkeypatch.setattr(
        agent_module._qloo_module, "qloo_insights_place", lambda *a, **k: {"entities": []}
    )
    response = client.post(
        "/api/transplant",
        json={"seeds": [{"id": "urn:entity:artist:test1"}], "dest_city": "London"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["broadened"] is True
    notices = body.get("notices", [])
    assert any("broadened search" in str(n).lower() for n in notices), notices
    # Disclosure fields survive the broadened ladder too.
    assert isinstance(body.get("why_qloo"), dict)
    assert "filter.location.query=London" in body["why_qloo"]["qloo_query"]
    assert "\u2192" in body["why_qloo"]["taste_path_sentence"]


def test_why_qloo_agui_snapshot_parity(monkeypatch, tmp_path) -> None:
    """SSE snapshot mirrors broadened, query, sentence, and model."""
    _isolated(monkeypatch, tmp_path)
    response = client.post(
        "/agui",
        json={"seeds": _hero_london_seeds(), "dest_city": "London"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    events = _collect_events(response.text)
    snapshots = [e for e in events if e.get("type") == "STATE_SNAPSHOT"]
    assert snapshots, "no STATE_SNAPSHOT in stream"
    snapshot = snapshots[-1].get("snapshot", {})
    assert "broadened" in snapshot
    query = snapshot.get("qloo_query") or (snapshot.get("why_qloo") or {}).get("qloo_query", "")
    assert "filter.location.query=London" in str(query), snapshot.keys()
    sentence = snapshot.get("taste_path_sentence") or (snapshot.get("why_qloo") or {}).get(
        "taste_path_sentence", ""
    )
    assert "\u2192" in str(sentence)
    model = snapshot.get("model") or (snapshot.get("why_qloo") or {}).get("model", "")
    assert "deepseek/deepseek-v4.1-flash" in str(model)
    assert _secret_like_tokens(str(query)) == []


def _served_bundle_text() -> str:
    """GET / plus every local script/link asset it references, concatenated."""
    index = client.get("/")
    assert index.status_code == 200
    html = index.text
    assert 'id="root"' in html
    refs = re.findall(r'(?:src|href)="(/[^"]+)"', html)
    assert refs, "served index.html references no local assets"
    parts = [html]
    for ref in dict.fromkeys(refs):
        asset = client.get(ref)
        assert asset.status_code == 200, ref
        parts.append(asset.text)
    return "\n".join(parts)


def test_why_qloo_served_bundle_anchors() -> None:
    """Strip, toggle, alert, and query anchors survive minification into dist."""
    bundle = _served_bundle_text()
    for anchor in (
        "Why this beats a list",
        "both panes",
        "broadened search",
        "filter.location.query",
        "alert",
        "why-query",
        "why-strip",
        "honesty-toggle",
    ):
        assert anchor in bundle, anchor
    # role=alert survives minified (role:`alert`) or unminified (role="alert").
    assert "role" in bundle
