"""Summarizer contract tests: template fallback, model slug, timeout speed."""

import time
import urllib.request
from pathlib import Path

import pytest

from backend.app import summarizer
from backend.app.summarizer import summarize_panes

SUMMARIZER_SOURCE = Path(__file__).resolve().parents[1] / "app" / "summarizer.py"


@pytest.fixture(autouse=True)
def _no_key_and_no_network(monkeypatch):
    """Template mode: no key configured, and any live HTTP call fails the test."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in template mode")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


def test_no_key_returns_template_with_required_substrings() -> None:
    result = summarize_panes(["Asake", "owambe nights"], None)
    assert "Tourist" in result["tourist"]["summary"]
    assert "transplant" in result["transplant"]["summary"]
    assert result["tourist"]["summary"].strip()
    assert result["transplant"]["summary"].strip()
    cards = result["transplant"]["cards"]
    assert isinstance(cards, list) and len(cards) >= 1
    for card in cards:
        assert card["why_you"].strip()


def test_envelope_shape_with_evidence_cards() -> None:
    evidence = {"cards": [{"name": "Eko Bite"}, {"name": "Suya Co"}]}
    result = summarize_panes("Asake, owambe", evidence)
    cards = result["transplant"]["cards"]
    assert len(cards) == 2
    assert all(card["why_you"].strip() for card in cards)
    assert [card["name"] for card in cards] == ["Eko Bite", "Suya Co"]


def test_source_declares_model_and_env_override() -> None:
    source = SUMMARIZER_SOURCE.read_text(encoding="utf-8")
    assert "deepseek/deepseek-v4.1-flash" in source
    assert "OPENROUTER_MODEL" in source
    assert "OPENROUTER_API_KEY" in source
    assert "openrouter.ai/api/v1/chat/completions" in source


def test_llm_timeout_falls_back_fast(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def _timeout(*args, **kwargs):
        raise TimeoutError("simulated LLM timeout")

    monkeypatch.setattr(urllib.request, "urlopen", _timeout)
    started = time.monotonic()
    result = summarize_panes(["Asake"], {"cards": [{"name": "Eko Bite"}]})
    elapsed = time.monotonic() - started
    assert elapsed < 2
    assert "Tourist" in result["tourist"]["summary"]
    assert "transplant" in result["transplant"]["summary"]
    assert result["tourist"]["summary"].strip()
    assert result["transplant"]["summary"].strip()
    assert result["transplant"]["cards"][0]["why_you"].strip()
