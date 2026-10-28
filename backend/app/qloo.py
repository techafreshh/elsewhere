"""Qloo client with mock mode and live proxy shape.

Mock-first: when ``QLOO_API_KEY`` is absent from the environment, the
functions below return hand-written synthetic fixtures from
``backend/mocks/`` (ours, never real Qloo responses) in the exact live
shape, so the build and all downstream work proceed without the key.

With a key present they issue live ``GET`` requests to
``https://hackathon.api.qloo.com`` with the ``X-Api-Key`` header:

- search: ``GET {BASE}/search?query=...``
- insights: ``GET {BASE}/v2/insights?filter.type=urn:entity:place``
  ``&signal.interests.entities=<ids>&filter.location.query=London``
  ``&feature.explainability=true&take=8``

Invalid params are validated client-side (``ValueError``) before any
network call. The key is read from the environment only, never logged,
and never hardcoded.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://hackathon.api.qloo.com"
API_HEADER = "X-Api-Key"
PLACE_FILTER = "urn:entity:place"
BRAND_FILTER = "urn:entity:brand"

MOCKS_DIR = Path(__file__).resolve().parents[1] / "mocks"
SEARCH_MOCK = MOCKS_DIR / "search_asake.json"
INSIGHTS_MOCK = MOCKS_DIR / "insights_london_place.json"

DEFAULT_TAKE = 8
MAX_TAKE = 50

# Synthetic brand-kicker fixture (ours, not Qloo data). Kept inline: the
# brand call is a thin shared helper and needs no committed file.
BRAND_MOCK = [
    {
        "name": "Eko Bite LDN",
        "entity_id": "urn:entity:brand:mock-london-brand-001",
        "explainability": 0.91,
    },
    {
        "name": "Owambe Suya Co",
        "entity_id": "urn:entity:brand:mock-london-brand-002",
        "explainability": 0.84,
    },
    {
        "name": "Danfo Kitchen Express",
        "entity_id": "urn:entity:brand:mock-london-brand-003",
        "explainability": 0.77,
    },
    {
        "name": "Palm & Pine Grocers",
        "entity_id": "urn:entity:brand:mock-london-brand-004",
        "explainability": 0.69,
    },
    {
        "name": "Nollywood Nights Pop-up",
        "entity_id": "urn:entity:brand:mock-london-brand-005",
        "explainability": 0.63,
    },
]


def _has_key() -> bool:
    """True when a Qloo key is configured (read at call time, never cached)."""
    return bool(os.environ.get("QLOO_API_KEY"))


def _api_key() -> str:
    """Return the configured key (header value only; never log or print)."""
    return os.environ.get("QLOO_API_KEY") or ""


def _require_non_empty_str(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _require_take(take: object) -> int:
    if isinstance(take, bool) or not isinstance(take, int) or not 1 <= take <= MAX_TAKE:
        raise ValueError(f"take must be an int within 1..{MAX_TAKE}")
    return take


def _require_entity_ids(entity_ids: object) -> list[str] | None:
    if entity_ids is None:
        return None
    if not isinstance(entity_ids, (list, tuple)) or not entity_ids:
        raise ValueError("entity_ids must be a non-empty list of urn:entity:* IDs or None")
    cleaned: list[str] = []
    for entity_id in entity_ids:
        if not isinstance(entity_id, str) or not entity_id.startswith("urn:entity:"):
            raise ValueError(f"invalid entity_id (must start with 'urn:entity:'): {entity_id!r}")
        cleaned.append(entity_id)
    return cleaned


def _load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _live_get(path: str, query_string: str):
    """Issue a live GET to BASE_URL + path with the X-Api-Key header."""
    url = f"{BASE_URL}{path}?{query_string}"
    request = urllib.request.Request(url, headers={API_HEADER: _api_key()})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _insights_query_string(
    location_query: str,
    entity_ids: list[str] | None,
    take: int,
    filter_type: str = PLACE_FILTER,
    extra_filters: dict | None = None,
    refinements: list[str] | None = None,
) -> str:
    """Build the live /v2/insights query string (GET, params only, no key)."""
    if extra_filters is None and refinements is not None:
        try:
            try:
                from backend.app.agent import refinement_filters as _refinement_filters
            except ModuleNotFoundError:
                from app.agent import refinement_filters as _refinement_filters
            extra_filters = _refinement_filters(refinements)
        except Exception:
            extra_filters = None
    if filter_type == PLACE_FILTER:
        parts = ["filter.type=urn:entity:place"]
    else:
        parts = [f"filter.type={urllib.parse.quote(filter_type, safe=':')}"]
    if entity_ids:
        parts.append("signal.interests.entities=" + urllib.parse.quote(",".join(entity_ids), safe=","))
    parts.append("filter.location.query=" + urllib.parse.quote(location_query, safe=""))
    parts.append("feature.explainability=true")
    parts.append(f"take={take}")
    if extra_filters:
        for key, value in extra_filters.items():
            parts.append(
                f"{urllib.parse.quote(str(key), safe='')}"
                f"={urllib.parse.quote(str(value), safe=',')}"
            )
    return "&".join(parts)


def _normalize_search_entities(payload: object) -> list[dict]:
    """Normalize a live /search payload to a list of name/entity_id dicts."""
    if isinstance(payload, dict):
        items = payload.get("results", payload.get("entities", []))
    else:
        items = payload
    if not isinstance(items, list):
        return []
    normalized: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("title") or ""
        entity_id = item.get("entity_id") or item.get("id") or ""
        if name and entity_id:
            normalized.append({**item, "name": name, "entity_id": entity_id})
    return normalized


def qloo_search(query: str) -> list[dict]:
    """Resolve a name to Qloo entities.

    Mock (no key): returns the synthetic ``search_asake.json`` fixture.
    Live (key set): ``GET {BASE_URL}/search`` with the ``X-Api-Key`` header.
    """
    query = _require_non_empty_str(query, "query")
    if not _has_key():
        data = _load_json(SEARCH_MOCK)
        return list(data) if isinstance(data, list) else []
    query_string = "query=" + urllib.parse.quote(query, safe="")
    return _normalize_search_entities(_live_get("/search", query_string))


def _insights_live(
    location_query: str,
    entity_ids: list[str] | None,
    take: int,
    filter_type: str,
    refinements: list[str] | None = None,
    extra_filters: dict | None = None,
) -> dict:
    query_string = _insights_query_string(
        location_query, entity_ids, take, filter_type,
        extra_filters=extra_filters, refinements=refinements,
    )
    payload = _live_get("/v2/insights", query_string)
    if isinstance(payload, dict) and isinstance(payload.get("entities"), list):
        return payload
    entities = payload if isinstance(payload, list) else []
    return {"entities": entities}


def qloo_insights_place(
    location_query: str = "London",
    entity_ids: list[str] | None = None,
    take: int = DEFAULT_TAKE,
    refinements: list[str] | None = None,
    extra_filters: dict | None = None,
) -> dict:
    """Recommend places for a city, grounded in taste-signal entity IDs.

    Mock (no key): returns the synthetic ``insights_london_place.json``
    fixture, sliced to ``take`` (minimum 5 entities regardless).
    Live (key set): ``GET {BASE_URL}/v2/insights`` with
    ``filter.type=urn:entity:place``, ``signal.interests.entities``,
    ``filter.location.query``, ``feature.explainability=true`` and ``take``.
    """
    location_query = _require_non_empty_str(location_query, "location_query")
    take = _require_take(take)
    entity_ids = _require_entity_ids(entity_ids)
    if not _has_key():
        data = _load_json(INSIGHTS_MOCK)
        entities = data.get("entities", []) if isinstance(data, dict) else []
        return {**data, "entities": list(entities)[: max(take, 5)]}
    return _insights_live(
        location_query, entity_ids, take, PLACE_FILTER,
        refinements=refinements, extra_filters=extra_filters,
    )


def qloo_insights_brand(
    location_query: str = "London",
    entity_ids: list[str] | None = None,
    take: int = 5,
    refinements: list[str] | None = None,
    extra_filters: dict | None = None,
) -> dict:
    """Brand kicker row; thin shared helper over the same live shape."""
    location_query = _require_non_empty_str(location_query, "location_query")
    take = _require_take(take)
    entity_ids = _require_entity_ids(entity_ids)
    if not _has_key():
        return {"entities": list(BRAND_MOCK)[:take]}
    return _insights_live(
        location_query, entity_ids, take, BRAND_FILTER,
        refinements=refinements, extra_filters=extra_filters,
    )
