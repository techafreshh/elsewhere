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
import time
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://hackathon.api.qloo.com"
API_HEADER = "X-Api-Key"
PLACE_FILTER = "urn:entity:place"
BRAND_FILTER = "urn:entity:brand"
ARTIST_FILTER = "urn:entity:artist"

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


# Live Qloo IDs arrive as bare UUIDs (search hits and insights results both)
# while mock fixtures and older docs use urn:entity:* URNs. Both are accepted;
# the hackathon API's signal.interests.entities only accepts the bare form
# (URN forms 400 with "contains invalid entity ids").
_BARE_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def is_entity_id(value: object) -> bool:
    """True for a usable Qloo entity ID: urn:entity:* URN or bare UUID."""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return stripped.startswith("urn:entity:") or bool(_BARE_UUID_RE.match(stripped))


def normalize_entity_id(value: object) -> str:
    """Stripped, lowercased entity ID. Live /v2/insights matches UUIDs
    case-insensitively but lowercase is the canonical wire form; URN
    prefixes lowercase safely too."""
    return str(value).strip().lower() if isinstance(value, str) else ""


def _require_entity_ids(entity_ids: object) -> list[str] | None:
    if entity_ids is None:
        return None
    if not isinstance(entity_ids, (list, tuple)) or not entity_ids:
        raise ValueError("entity_ids must be a non-empty list of entity IDs or None")
    cleaned: list[str] = []
    for entity_id in entity_ids:
        if not is_entity_id(entity_id):
            raise ValueError(f"invalid entity_id (urn:entity:* or bare UUID): {entity_id!r}")
        cleaned.append(normalize_entity_id(entity_id))
    return cleaned


def _load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _live_get(path: str, query_string: str):
    """Issue a live GET to BASE_URL + path with the X-Api-Key header.

    One retry on 429/5xx or transport errors with a short backoff — the
    hackathon API occasionally blinks under burst load (the onboarding's
    autocomplete/related calls right before a run make this common), and a
    swallowed first-call failure degrades the whole transplant (empty
    brands, broadened ladder) even though an immediate retry succeeds.
    """
    url = f"{BASE_URL}{path}?{query_string}"
    last_error: Exception | None = None
    for attempt in range(2):
        if attempt:
            time.sleep(1.5)
        try:
            request = urllib.request.Request(url, headers={API_HEADER: _api_key()})
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            last_error = exc
            try:
                exc.close()
            except Exception:
                pass
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
    raise last_error  # type: ignore[misc]


def signal_probe_ok(entity_id: str, location_query: str = "London") -> bool:
    """True when ``entity_id`` is a live signal the /v2/insights graph knows.

    Issues one minimal insights GET with the ID as the only signal and
    inspects the ``warnings`` array: a ``not_found`` entry for the ID means
    the entity is absent from the graph (search hits can 404 here — e.g.
    the Asake artist ID) and must not be sent as a signal. Operator-side
    helper for ``resolve_chips.py``; never raises. Live mode only — mock
    mode has no warnings concept, so probes pass.
    """
    if not is_entity_id(entity_id):
        return False
    if not _has_key():
        return True
    try:
        query_string = (
            "filter.type=urn:entity:place"
            + "&signal.interests.entities="
            + urllib.parse.quote(normalize_entity_id(entity_id), safe="")
            + "&filter.location.query="
            + urllib.parse.quote(location_query, safe="")
            + "&take=1"
        )
        payload = _live_get("/v2/insights", query_string)
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    warnings = payload.get("warnings")
    if not isinstance(warnings, list):
        return True
    for warning in warnings:
        if not isinstance(warning, dict) or warning.get("type") != "not_found":
            continue
        missing = warning.get("signal.interests.entities")
        if isinstance(missing, list) and normalize_entity_id(entity_id) in [
            normalize_entity_id(m) for m in missing if isinstance(m, str)
        ]:
            return False
    return True


def _insights_query_string(
    location_query: str,
    entity_ids: list[str] | None,
    take: int,
    filter_type: str = PLACE_FILTER,
    extra_filters: dict | None = None,
    refinements: list[str] | None = None,
    tag_ids: list[str] | None = None,
    with_location: bool = True,
) -> str:
    """Build the live /v2/insights query string (GET, params only, no key).

    ``with_location=False`` drops ``filter.location.query`` entirely — the
    taste-correlation fan-out (``qloo_related``) is city-agnostic, and live
    probing confirmed artist/brand insights return without a location while
    omitting the param, unlike the ``filter.type=urn:entity`` untyped form
    (400 "value not supported").
    """
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
    if tag_ids:
        parts.append("signal.interests.tags=" + urllib.parse.quote(",".join(tag_ids), safe=","))
    if with_location:
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
    tag_ids: list[str] | None = None,
    with_location: bool = True,
) -> dict:
    query_string = _insights_query_string(
        location_query, entity_ids, take, filter_type,
        extra_filters=extra_filters, refinements=refinements, tag_ids=tag_ids,
        with_location=with_location,
    )
    payload = _live_get("/v2/insights", query_string)
    if isinstance(payload, dict):
        # Live envelope nests the array under results.entities; some older
        # shapes carry it top-level. Accept both.
        results = payload.get("results")
        if isinstance(results, dict) and isinstance(results.get("entities"), list):
            return {**payload, "entities": results["entities"]}
        if isinstance(payload.get("entities"), list):
            return payload
    entities = payload if isinstance(payload, list) else []
    return {"entities": entities}


def qloo_insights_place(
    location_query: str = "London",
    entity_ids: list[str] | None = None,
    take: int = DEFAULT_TAKE,
    refinements: list[str] | None = None,
    extra_filters: dict | None = None,
    tag_ids: list[str] | None = None,
) -> dict:
    """Recommend places for a city, grounded in taste-signal entity IDs.

    Mock (no key): returns the synthetic ``insights_london_place.json``
    fixture, sliced to ``take`` (minimum 5 entities regardless).
    Live (key set): ``GET {BASE_URL}/v2/insights`` with
    ``filter.type=urn:entity:place``, ``signal.interests.entities``,
    optional ``signal.interests.tags``, ``filter.location.query``,
    ``feature.explainability=true`` and ``take``.
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
        refinements=refinements, extra_filters=extra_filters, tag_ids=tag_ids,
    )


def qloo_insights_brand(
    location_query: str = "London",
    entity_ids: list[str] | None = None,
    take: int = 5,
    refinements: list[str] | None = None,
    extra_filters: dict | None = None,
    tag_ids: list[str] | None = None,
) -> dict:
    """Brand kicker row; thin shared helper over the same live shape."""
    location_query = _require_non_empty_str(location_query, "location_query")
    take = _require_take(take)
    entity_ids = _require_entity_ids(entity_ids)
    if not _has_key():
        return {"entities": list(BRAND_MOCK)[:take]}
    return _insights_live(
        location_query, entity_ids, take, BRAND_FILTER,
        refinements=refinements, extra_filters=extra_filters, tag_ids=tag_ids,
    )


def qloo_related(entity_ids: list[str], take: int = 6) -> dict:
    """Cross-domain correlated tastes for a signal set ("you might also like").

    Fans out per ``filter.type`` — artist, movie, tv_show, brand and place —
    without a location (live probe: all five answer location-less; the
    untyped ``filter.type=urn:entity`` form and ``video_game`` are 400s).
    Legs run in parallel threads and merge **round-robin** so every domain
    is represented near the top of the visible row — the artist-first
    concatenation used before drowned screens/food/vibes picks under music
    artists. Each leg is best-effort: one failing domain never fails the
    others. Mock (no key) returns no entities — callers fall back to the
    chip catalog.
    """
    ids = _require_entity_ids(entity_ids)
    take = _require_take(take)
    if not _has_key() or not ids:
        return {"entities": []}

    related_filters = (
        ARTIST_FILTER,
        "urn:entity:movie",
        "urn:entity:tv_show",
        BRAND_FILTER,
        PLACE_FILTER,
    )

    def _leg(filter_type: str) -> list[dict]:
        try:
            payload = _insights_live(
                "", ids, take, filter_type, with_location=False,
            )
            entities = payload.get("entities", []) if isinstance(payload, dict) else []
        except Exception:
            # Some /search-only chip ids (the Asake lesson) hard-400 the
            # non-artist insights types. Retry the leg on tag signals —
            # the graph accepts those for anything (probe-verified).
            tags = _tag_signals()
            if not tags:
                return []
            try:
                payload = _insights_live(
                    "", None, take, filter_type, with_location=False, tag_ids=tags,
                )
                entities = payload.get("entities", []) if isinstance(payload, dict) else []
            except Exception:
                return []
        out: list[dict] = []
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            if not str(entity.get("entity_id", "")):
                continue
            # Correlated entities arrive without a types field; the fan-out
            # domain IS their type, so tag it for downstream normalization.
            if not entity.get("types"):
                entity = {**entity, "types": [filter_type]}
            out.append(entity)
        return out

    tags_cache: list[str] | None = None

    def _tag_signals() -> list[str]:
        """Chip tag_ids for the selected entities — computed once lazily
        (agent imports qloo at module scope, so this import stays deferred)."""
        nonlocal tags_cache
        if tags_cache is None:
            try:
                try:
                    from backend.app.agent import catalog_tags_for
                except ModuleNotFoundError:
                    from app.agent import catalog_tags_for
                tags_cache = [t for t in catalog_tags_for(ids) if isinstance(t, str)][:8]
            except Exception:
                tags_cache = []
        return tags_cache

    leg_lists: list[list[dict]] = []
    try:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(related_filters)) as pool:
            leg_lists = list(pool.map(_leg, related_filters))
    except Exception:
        leg_lists = [_leg(filter_type) for filter_type in related_filters]

    # Round-robin merge: one entity per domain per cycle, deduped by id, so
    # the cap cuts tails — never whole categories.
    merged: list[dict] = []
    seen: set[str] = set()
    cursors = [0] * len(leg_lists)
    while len(merged) < take * 3:
        advanced = False
        for leg_index, leg in enumerate(leg_lists):
            while cursors[leg_index] < len(leg):
                entity = leg[cursors[leg_index]]
                cursors[leg_index] += 1
                entity_id = str(entity.get("entity_id", ""))
                if entity_id in seen:
                    continue
                seen.add(entity_id)
                merged.append(entity)
                advanced = True
                break
        if not advanced:
            break
    return {"entities": merged}
