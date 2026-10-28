"""Qloo client contract tests: mock shapes, validation, live-call shape."""

import urllib.request
from pathlib import Path

import pytest

from backend.app.qloo import (
    qloo_insights_brand,
    qloo_insights_place,
    qloo_search,
)

QLOO_SOURCE = Path(__file__).resolve().parents[1] / "app" / "qloo.py"


@pytest.fixture(autouse=True)
def _no_key_and_no_network(monkeypatch):
    """Mock mode: no key configured, and any live HTTP call fails the test."""
    monkeypatch.delenv("QLOO_API_KEY", raising=False)

    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in mock mode: params must validate first")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


def test_search_mock_shape() -> None:
    results = qloo_search("Asake")
    assert isinstance(results, list) and len(results) >= 1
    for entity in results:
        assert entity["name"]
        assert entity["entity_id"]


def test_insights_place_mock_shape() -> None:
    payload = qloo_insights_place("London")
    assert isinstance(payload, dict)
    entities = payload["entities"]
    assert isinstance(entities, list) and len(entities) >= 5
    for entity in entities:
        assert entity["name"]
        assert entity["entity_id"]
        assert isinstance(entity["explainability"], (int, float))


def test_insights_place_take_floor() -> None:
    payload = qloo_insights_place("London", take=2)
    assert len(payload["entities"]) >= 5


def test_brand_helper_mock_shape() -> None:
    payload = qloo_insights_brand("London")
    assert isinstance(payload["entities"], list) and payload["entities"]
    assert payload["entities"][0]["entity_id"].startswith("urn:entity:")


def test_invalid_params_raise_before_network() -> None:
    with pytest.raises(ValueError):
        qloo_search("")
    with pytest.raises(ValueError):
        qloo_search("   ")
    with pytest.raises(ValueError):
        qloo_insights_place("")
    with pytest.raises(ValueError):
        qloo_insights_place("London", take=0)
    with pytest.raises(ValueError):
        qloo_insights_place("London", take=51)
    with pytest.raises(ValueError):
        qloo_insights_place("London", entity_ids=["not-a-urn"])
    with pytest.raises(ValueError):
        qloo_insights_brand("London", take=0)


def test_source_has_live_shape_and_no_hardcoded_key() -> None:
    source = QLOO_SOURCE.read_text(encoding="utf-8")
    assert "hackathon.api.qloo.com" in source
    assert "X-Api-Key" in source
    assert "filter.type=urn:entity:place" in source
    assert "signal.interests.entities" in source
    assert "filter.location.query" in source
    assert "feature.explainability=true" in source
    assert 'os.environ.get("QLOO_API_KEY")' in source
    assert "sk-" not in source
