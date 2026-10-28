"""Refinement mapping: late-night/cheaper to Qloo filters plus echo."""

import urllib.request

import pytest
from fastapi.testclient import TestClient

try:
    from backend.app import cache as cache_module
    from backend.app.agent import (
        build_insights_params,
        refinement_filters,
    )
    from backend.app.cache import cache_key
    from backend.app.main import app
    from backend.app.qloo import _insights_query_string
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import cache as cache_module
    from app.agent import (
        build_insights_params,
        refinement_filters,
    )
    from app.cache import cache_key
    from app.main import app
    from app.qloo import _insights_query_string

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_cache_and_mock_mode(monkeypatch, tmp_path):
    """Isolated cache dir, mock mode, no live HTTP."""
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.setattr(cache_module, "CACHE_DIR", tmp_path)

    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in mock mode")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)


def test_late_night_maps_to_hours() -> None:
    assert refinement_filters(["late-night"]) == {"filter.hours": "late_night"}


def test_cheaper_maps_to_price_level() -> None:
    result = refinement_filters(["cheaper"])
    assert result["filter.price_level"] == "1,2"
    assert "filter.price_level" in result


def test_both_refinements_merge() -> None:
    merged = refinement_filters(["late-night", "cheaper"])
    assert merged["filter.hours"] == "late_night"
    assert merged["filter.price_level"] == "1,2"


def test_unknown_empty_none_yield_empty() -> None:
    assert refinement_filters(["unknown-xyz"]) == {}
    assert refinement_filters([]) == {}
    assert refinement_filters(None) == {}


def test_folding_is_case_and_whitespace_insensitive() -> None:
    assert refinement_filters([" Late-NIGHT "]) == {"filter.hours": "late_night"}
    assert refinement_filters(["CHEAPER"])["filter.price_level"] == "1,2"
    assert refinement_filters(["Late-Night", " late-night "]) == {"filter.hours": "late_night"}


def test_build_insights_params_contains_price_level() -> None:
    params = build_insights_params("London", None, 8, ["cheaper"])
    assert "filter.price_level" in params
    assert params["filter.price_level"] == "1,2"


def test_build_insights_params_contains_hours() -> None:
    params = build_insights_params("London", None, 8, ["late-night"])
    assert "filter.hours" in params


def test_query_string_contains_price_level_for_cheaper() -> None:
    query = _insights_query_string("London", None, 8, refinements=["cheaper"])
    assert "filter.price_level" in query


def test_query_string_contains_hours_for_late_night() -> None:
    query = _insights_query_string("London", None, 8, refinements=["late-night"])
    assert "filter.hours" in query


def test_transplant_late_night_echo_and_cache_keys_differ() -> None:
    seeds = ["urn:entity:artist:seed-one", "urn:entity:artist:seed-two"]
    plain = client.post("/api/transplant", json={"seeds": seeds, "dest_city": "London"})
    assert plain.status_code == 200
    refined = client.post(
        "/api/transplant",
        json={"seeds": seeds, "dest_city": "London", "refinements": ["late-night"]},
    )
    assert refined.status_code == 200
    assert cache_key(seeds, "London", []) != cache_key(seeds, "London", ["late-night"])
    body = refined.json()
    haystack = f"{body.get('meta', {})} {body.get('notices', [])}".lower()
    assert ("hours" in haystack) or ("broadened" in haystack)


def test_transplant_both_refinements_double_post_cache_hit() -> None:
    payload = {
        "seeds": ["urn:entity:artist:seed-one", "urn:entity:artist:seed-two"],
        "dest_city": "London",
        "refinements": ["late-night", "cheaper"],
    }
    first = client.post("/api/transplant", json=payload)
    assert first.status_code == 200
    second = client.post("/api/transplant", json=payload)
    assert second.status_code == 200
    assert second.json()["meta"]["cache_hit"] is True
