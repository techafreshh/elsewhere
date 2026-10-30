"""Transplant agent loop: seeds plus city into grounded cards.

Pure helpers (seed resolution, hero plus brand calls, three-rung fallback
ladder, affinity math, card builders) shared by the JSON endpoint and the
future AG-UI SSE endpoint. Never raises for Qloo or summarizer failures:
empty upstream degrades through the ladder to a mock-slice fill with
``broadened=true`` rather than 500.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from statistics import median

try:
    from backend.app import qloo as _qloo_module
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import qloo as _qloo_module

try:
    from backend.app import summarizer as _summarizer_module
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import summarizer as _summarizer_module

# Re-exported at module level so tests can monkeypatch either here or on
# the qloo/summarizer modules; _*_fn() below resolves both locations.
qloo_insights_place = _qloo_module.qloo_insights_place
qloo_insights_brand = _qloo_module.qloo_insights_brand
qloo_search = _qloo_module.qloo_search
summarize_panes = _summarizer_module.summarize_panes
is_entity_id = _qloo_module.is_entity_id

_ORIG_PLACE = _qloo_module.qloo_insights_place
_ORIG_BRAND = _qloo_module.qloo_insights_brand
_ORIG_SEARCH = _qloo_module.qloo_search
_ORIG_SUMMARIZE = _summarizer_module.summarize_panes

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_DEFAULT_TIMEOUT = 15

# Refinement keywords steering a re-run without restarting the session.
# Single source of truth for refinement -> Qloo insight filter mapping.
REFINEMENT_FILTERS: dict[str, dict[str, str]] = {
    "late-night": {"filter.hours": "late_night"},
    "cheaper": {"filter.price_level": "1,2"},
}


def _fold_refinement(value: object) -> str | None:
    """Lowercase/strip fold; None for non-strings and empties."""
    if not isinstance(value, str):
        return None
    folded = value.strip().lower()
    return folded or None


def normalized_refinements(refinements: object = None) -> list[str]:
    """Sorted, deduped, folded refinement list (unknowns kept)."""
    if refinements is None:
        return []
    if isinstance(refinements, str):
        refinements = [refinements]
    if not isinstance(refinements, (list, tuple)):
        return []
    seen: set[str] = set()
    for item in refinements:
        folded = _fold_refinement(item)
        if folded and folded not in seen:
            seen.add(folded)
    return sorted(seen)


def refinement_filters(refinements: object = None) -> dict[str, str]:
    """Map refinement keywords to Qloo insight filter params.

    Folding mirrors ``cache._normalize_refinements`` (strip/lowercase);
    unknown strings are ignored for filters but stay in the cache key.
    Never raises: non-list input yields {}.
    """
    if refinements is None:
        return {}
    if isinstance(refinements, str):
        refinements = [refinements]
    if not isinstance(refinements, (list, tuple)):
        return {}
    merged: dict[str, str] = {}
    seen: set[str] = set()
    for item in refinements:
        folded = _fold_refinement(item)
        if folded is None or folded in seen:
            continue
        seen.add(folded)
        mapping = REFINEMENT_FILTERS.get(folded)
        if mapping:
            merged.update(mapping)
    return merged


def build_insights_params(
    location_query: str,
    entity_ids: list[str] | None = None,
    take: int = 8,
    refinements: object = None,
    filter_type: str = "urn:entity:place",
) -> dict:
    """Full insights param dict: base keys plus refinement filters."""
    params: dict = {
        "filter.type": filter_type,
        "filter.location.query": location_query,
        "feature.explainability": "true",
        "take": take,
    }
    if entity_ids:
        params["signal.interests.entities"] = ",".join(entity_ids)
    tags = catalog_tags_for(entity_ids)
    if tags:
        params["signal.interests.tags"] = ",".join(tags)
    params.update(refinement_filters(refinements))
    return params


def refinement_notices(refinements: object = None, broadened: bool = False) -> list[str]:
    """Human-readable echo of applied refinement filters."""
    notices: list[str] = []
    for ref in normalized_refinements(refinements):
        mapping = REFINEMENT_FILTERS.get(ref)
        if mapping:
            for key in mapping:
                notices.append(f"applied {key} for {ref}")
    if broadened:
        notices.append("broadened search to fill results")
    return notices


# Map fallback when the mock fixture carries no coordinates.
_FALLBACK_CENTERS = {
    "london": [51.5074, -0.1278],
    "toronto": [43.6532, -79.3832],
    "nyc": [40.7128, -74.0060],
    "lagos": [6.5244, 3.3792],
    "nairobi": [-1.2921, 36.8219],
}

# Legacy spellings folding into the world-city dictionary, so "NYC" and
# "New Delhi" resolve without growing the committed list.
_CITY_ALIASES = {
    "nyc": "new york",
    "new york city": "new york",
    "ny": "new york",
    "washington dc": "washington",
    "dc": "washington",
    "cdmx": "mexico city",
    "new delhi": "delhi",
    "bangalore": "bengaluru",
    "saigon": "ho chi minh city",
    "joburg": "johannesburg",
    "sao paulo": "sao paulo",
}

_CITY_DICT_CACHE: list[dict] | None = None


def _city_dictionary() -> list[dict]:
    """World-city rows from disk (name, country, lat, lng, aliases). [] on failure."""
    global _CITY_DICT_CACHE
    if _CITY_DICT_CACHE is None:
        try:
            path = Path(__file__).resolve().parents[1] / "data" / "cities.json"
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            _CITY_DICT_CACHE = [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []
        except Exception:
            _CITY_DICT_CACHE = []
    return _CITY_DICT_CACHE


def city_lookup(city: str | None) -> dict | None:
    """Dictionary row for a typed city name (folded aliases included), else None.

    Powers both the geocode endpoint (instant, offline-capable) and the
    map-center fallback. Never raises.
    """
    if not isinstance(city, str) or not city.strip():
        return None
    key = " ".join(city.strip().lower().split())
    folded = _CITY_ALIASES.get(key, key)
    for entry in _city_dictionary():
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        candidates = [name.lower()] + [
            str(alias).strip().lower()
            for alias in (entry.get("aliases") or [])
            if isinstance(alias, str) and alias.strip()
        ]
        # Heavy accents fold away via casefold, not lower.
        if folded.casefold() in [c.casefold() for c in candidates]:
            return entry
    return None


def city_center(city: str | None) -> list | None:
    """Best-effort ``[lat, lng]`` for a typed city: the world-city
    dictionary first (aliases folded), then the legacy fallback centers,
    else ``None``. Never raises."""
    entry = city_lookup(city)
    if entry is not None:
        try:
            lat = float(entry.get("lat"))
            lng = float(entry.get("lng"))
            return [lat, lng]
        except (TypeError, ValueError):
            pass
    if not isinstance(city, str) or not city.strip():
        return None
    key = " ".join(city.strip().lower().split())
    fallback = _FALLBACK_CENTERS.get(key)
    return list(fallback) if fallback else None


def map_center_for(city: str | None, latlngs: list) -> list:
    """Display center for the reveal map: the mean of the (real or
    offset-derived) card coordinates when sane, otherwise the city-center
    chain, otherwise a world view. Qloo geocodes ``filter.location.query``
    server-side, so a typed city outside the dictionary still yields entity
    pins — the mean recenters the viewport onto them. Never raises."""
    try:
        points = []
        for latlng in latlngs if isinstance(latlngs, list) else []:
            if not (isinstance(latlng, (list, tuple)) and len(latlng) >= 2):
                continue
            lat, lng = latlng[0], latlng[1]
            if (
                isinstance(lat, (int, float))
                and isinstance(lng, (int, float))
                and -90 <= lat <= 90
                and -180 <= lng <= 180
            ):
                points.append((float(lat), float(lng)))
        if points:
            lat = sum(p[0] for p in points) / len(points)
            lng = sum(p[1] for p in points) / len(points)
            return [round(lat, 4), round(lng, 4)]
    except Exception:
        pass
    return city_center(city) or [20.0, 0.0]


def _model_slug() -> str:
    """Model slug, read at call time so env changes apply without restart."""
    return os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)


def _extractor_timeout_seconds() -> float:
    """Per-call OpenRouter timeout, read at call time via OPENROUTER_TIMEOUT."""
    try:
        return float(os.environ.get("OPENROUTER_TIMEOUT", OPENROUTER_DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return float(OPENROUTER_DEFAULT_TIMEOUT)


def _extractor_messages(text: str) -> list[dict]:
    """Prompt asking for a bare JSON array of short taste names."""
    phrase = text.strip() if isinstance(text, str) else ""
    return [
        {
            "role": "system",
            "content": (
                "Extract candidate taste names from the user's free-text phrase. "
                "Reply with ONLY a bare JSON array of short taste names, "
                'e.g. ["Nollywood", "quiet rooftops"]. No prose, no numbering.'
            ),
        },
        {
            "role": "user",
            "content": f"Extract taste names from: {phrase}",
        },
    ]


def _parse_candidate_list(content: object) -> list[str] | None:
    """Parse a ``[...]`` JSON list of non-empty strings; None when unusable."""
    if not isinstance(content, str) or not content.strip():
        return None
    text = content.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    names: list[str] = []
    for item in parsed:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        if len(names) >= 3:
            break
    return names or None


def extract_taste_candidates(text: str) -> list[str] | None:
    """Extract up to 3 candidate taste names via the OpenRouter chat model.

    Same model/timeout rules as the pane summarizer. Returns ``None`` when
    ``OPENROUTER_API_KEY`` is absent, the text is blank, the call times
    out, the status is non-200, or the reply has no parseable ``[...]``
    of non-empty strings. Never raises.
    """
    try:
        if not isinstance(text, str) or not text.strip():
            return None
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            return None
        body = json.dumps(
            {
                "model": _model_slug(),
                "messages": _extractor_messages(text.strip()),
                "max_tokens": 100,
                "temperature": 0,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            OPENROUTER_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=_extractor_timeout_seconds()) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not choices:
            return None
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content", "")
        if isinstance(content, list):  # some providers return content blocks
            content = "".join(
                block.get("text", "") for block in content if isinstance(block, dict)
            )
        return _parse_candidate_list(content)
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        KeyError,
        IndexError,
        AttributeError,
        TypeError,
        OSError,
    ):
        return None
    except Exception:
        return None


def _qloo_mode() -> str:
    """Read at call time so key removal degrades without a restart."""
    return "live" if os.environ.get("QLOO_API_KEY") else "mock"


def _short_label(seed_id: str) -> str:
    # Bare UUIDs (the live /search shape) carry no name — never render them.
    stripped = seed_id.strip()
    if _qloo_module._BARE_UUID_RE.match(stripped):
        return ""
    return seed_id.split(":")[-1].replace("-", " ").strip() or seed_id


def _label_for_seed(seed_id: str) -> str:
    """Human label for a chip seed: URN slug, else catalog label, else ''.

    Bare UUIDs resolve through the chip catalog (Asake, 2am shawarma, …);
    unknown IDs yield '' so callers skip the label rather than render one.
    """
    label = _short_label(seed_id)
    if label:
        return label
    try:
        try:
            from backend.app.catalog import label_for_entity
        except ModuleNotFoundError:  # served with backend/ on sys.path
            from app.catalog import label_for_entity
        return label_for_entity(seed_id.strip()) or ""
    except Exception:
        return ""


def _tag_label(tag: object) -> str:
    """Human label from a Qloo tag entry (dict on live, str on mock)."""
    if isinstance(tag, dict):
        for field in ("name", "label", "value"):
            value = tag.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip().replace("-", " ")
        return ""
    if isinstance(tag, str) and tag.strip():
        return tag.strip().replace("-", " ")
    return ""


def _is_urn(value: object) -> bool:
    """Seed-classification gate: urn:entity:* URN or bare Qloo UUID."""
    return is_entity_id(value)


def _place_fn():
    """Hero-callable, honoring monkeypatches on agent or qloo module."""
    candidate = globals().get("qloo_insights_place")
    if candidate is not None and candidate is not _ORIG_PLACE:
        return candidate
    return _qloo_module.qloo_insights_place


def _brand_fn():
    candidate = globals().get("qloo_insights_brand")
    if candidate is not None and candidate is not _ORIG_BRAND:
        return candidate
    return _qloo_module.qloo_insights_brand


def _search_fn():
    candidate = globals().get("qloo_search")
    if candidate is not None and candidate is not _ORIG_SEARCH:
        return candidate
    return _qloo_module.qloo_search


def _summarize_fn():
    candidate = globals().get("summarize_panes")
    if candidate is not None and candidate is not _ORIG_SUMMARIZE:
        return candidate
    return _summarizer_module.summarize_panes


def _confidence_from_hit(item: dict) -> float:
    """Clamped 0..1 confidence from a search hit. Defaults to 0.5."""
    if not isinstance(item, dict):
        return 0.5
    for key in ("match_confidence", "confidence", "popularity", "affinity", "explainability"):
        try:
            value = item.get(key)
        except Exception:
            continue
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed != parsed:  # NaN never satisfies the 0..1 contract
            continue
        return max(0.0, min(1.0, parsed))
    return 0.5


def _query_tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens of length >= 3."""
    if not isinstance(text, str):
        return []
    return [tok for tok in re.findall(r"[a-z0-9]+", text.lower()) if len(tok) >= 3]


_CATALOG_CHIPS_CACHE: list[dict] | None = None


def _catalog_chips() -> list[dict]:
    """Chip rows from disk for relevance anchoring. [] on any failure."""
    global _CATALOG_CHIPS_CACHE
    if _CATALOG_CHIPS_CACHE is not None:
        return _CATALOG_CHIPS_CACHE
    try:
        path = Path(__file__).resolve().parents[1] / "data" / "chips.json"
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, list):
            _CATALOG_CHIPS_CACHE = [c for c in data if isinstance(c, dict)]
        else:
            _CATALOG_CHIPS_CACHE = []
    except Exception:
        _CATALOG_CHIPS_CACHE = []
    return _CATALOG_CHIPS_CACHE


def _tokens_overlap(qtokens: list[str], ltokens: list[str]) -> bool:
    """Token equality, or substring when the shorter side has length >= 4."""
    for qtok in qtokens:
        for ltok in ltokens:
            if qtok == ltok:
                return True
            short, long = (qtok, ltok) if len(qtok) <= len(ltok) else (ltok, qtok)
            if len(short) >= 4 and short in long:
                return True
    return False


def _query_overlaps_catalog(query: str) -> bool:
    """True when the prose overlaps any catalog chip label."""
    qtokens = _query_tokens(query)
    if not qtokens:
        return False
    try:
        chips = _catalog_chips()
    except Exception:
        return False
    for chip in chips:
        try:
            label = str(chip.get("label", ""))
        except Exception:
            continue
        if not label.strip():
            continue
        ltokens = _query_tokens(label)
        if not ltokens:
            continue
        if _tokens_overlap(qtokens, ltokens):
            return True
    return False


def _hit_text(hit: dict) -> str:
    """Searchable hit surface: name, type, disambiguation, entity id."""
    if not isinstance(hit, dict):
        return ""
    parts: list[str] = []
    for key in ("name", "type", "disambiguation", "entity_id", "id"):
        try:
            value = hit.get(key)
        except Exception:
            continue
        if isinstance(value, str) and value:
            parts.append(value.lower())
    return " ".join(parts)


def _is_hit_relevant(query: str, hit: dict) -> bool:
    """True when any query token (len >= 3) hits the name/type/disambiguation."""
    qtokens = _query_tokens(query)
    if not qtokens:
        return False
    haystack = _hit_text(hit)
    if not haystack:
        return False
    return any(tok in haystack for tok in qtokens)


def _hit_matches_catalog_chip(hit: dict, query: str) -> bool:
    """Plan-literal relevance: hit id equals a catalog chip overlapping the query."""
    if not isinstance(hit, dict) or not isinstance(query, str):
        return False
    try:
        entity_id = hit.get("entity_id") or hit.get("id")
    except Exception:
        return False
    if not isinstance(entity_id, str) or not entity_id:
        return False
    qtokens = _query_tokens(query)
    if not qtokens:
        return False
    try:
        chips = _catalog_chips()
    except Exception:
        return False
    for chip in chips:
        try:
            if chip.get("entity_id") != entity_id:
                continue
            label = str(chip.get("label", ""))
        except Exception:
            continue
        if not label.strip():
            continue
        if _tokens_overlap(qtokens, _query_tokens(label)):
            return True
    return False


def parse_seeds(raw_seeds: object) -> tuple[list[str], list[str]]:
    """Split raw seeds into chip ids and free-text queries.

    Strings and dict ``id``/``entity_id``/``urn`` fields are chips
    (zero network). Dict ``text`` fields with no id are free text kept
    in request order for the resolve step.
    """
    chips: list[str] = []
    texts: list[str] = []
    if not isinstance(raw_seeds, (list, tuple)):
        return chips, texts
    for seed in raw_seeds:
        if isinstance(seed, str):
            if seed.strip():
                chips.append(seed.strip())
        elif isinstance(seed, dict):
            chip: str | None = None
            for field in ("id", "entity_id", "urn"):
                try:
                    value = seed.get(field)
                except Exception:
                    continue
                if isinstance(value, str) and value.strip():
                    chip = value.strip()
                    break
            if chip is not None:
                chips.append(chip)
            else:
                try:
                    text = seed.get("text")
                except Exception:
                    text = None
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
    return chips, texts


def _search_one_candidate(query: str) -> list[dict]:
    """Run one ``qloo_search`` query through staging plus relevance filter.

    Exactly one ``_search_fn()`` call; any failure or irrelevance
    yields ``[]``, never raises.
    """
    if not isinstance(query, str) or not query.strip():
        return []
    query = query.strip()
    try:
        results = _search_fn()(query)
    except Exception:
        return []
    if isinstance(results, list):
        items = results
    elif isinstance(results, dict) and isinstance(results.get("results"), list):
        items = results["results"]
    else:
        items = []
    staged: list[tuple[dict, dict]] = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        entity_id = item.get("entity_id") or item.get("id")
        if not name or not entity_id:
            continue
        if not is_entity_id(entity_id):
            continue
        staged.append(
            (
                {
                    "name": str(name),
                    "entity_id": str(entity_id),
                    "confidence": _confidence_from_hit(item),
                },
                item,
            )
        )
    if not staged:
        return []
    # Mock proxy keeps its fixture when the prose overlaps the catalog;
    # otherwise only query-relevant hits survive (gibberish drops out).
    try:
        if _query_overlaps_catalog(query):
            return [candidate for candidate, _hit in staged]
    except Exception:
        pass
    kept: list[dict] = []
    for candidate, hit in staged:
        try:
            relevant = _is_hit_relevant(query, hit) or _hit_matches_catalog_chip(
                hit, query
            )
        except Exception:
            relevant = False
        if relevant:
            kept.append(candidate)
    return kept


def resolve_text_seed(text: str) -> list[dict]:
    """Resolve one free-text query to confirmable candidates.

    When ``OPENROUTER_API_KEY`` is configured, the phrase is first split
    into up to 3 candidate taste names and each costs one ``qloo_search``
    call (merged in order, deduped by ``entity_id``). Without a key, or
    when extraction fails or yields nothing, falls back to exactly one
    raw-phrase search. Any failure yields ``[]`` (unresolved, never raised).
    """
    if not isinstance(text, str) or not text.strip():
        return []
    query = text.strip()
    candidates: list[str] | None = None
    try:
        candidates = extract_taste_candidates(query)
    except Exception:
        candidates = None
    if not candidates:
        return _search_one_candidate(query)
    merged: list[dict] = []
    seen: set[str] = set()
    for candidate_query in candidates[:3]:
        if not isinstance(candidate_query, str) or not candidate_query.strip():
            continue
        try:
            hits = _search_one_candidate(candidate_query.strip())
        except Exception:
            continue
        for hit in hits:
            try:
                entity_id = str(hit.get("entity_id", ""))
            except Exception:
                continue
            if not entity_id or entity_id in seen:
                continue
            seen.add(entity_id)
            merged.append(hit)
    return merged


def build_note(unresolved: list[str] | None) -> str | None:
    """Visible note for dropped texts; None when everything resolved."""
    if not unresolved:
        return None
    cleaned = [t.strip() for t in unresolved if isinstance(t, str) and t.strip()]
    if not cleaned:
        return None
    return "; ".join(f'could not resolve "{text}"' for text in cleaned)


def _valid_signals_for_insights(signals: list[str] | None) -> list[str] | None:
    """Qloo-valid URN subset for insights; None when empty."""
    if not signals:
        return None
    valid = [s for s in signals if _is_urn(s)]
    return valid or None


def resolve_seeds(seed_ids: object) -> tuple[list[str], list[dict], list[str]]:
    """Split chip signals from free text; resolve text via search.

    Accepts raw seeds (strings or ``{"id"|"entity_id"|"text"}`` dicts).
    Returns ``(signals, confirmations, labels)``. Unresolvable text is
    dropped with empty candidates, never raised. Chip seeds (strings or
    ``id``/``entity_id``/``urn`` dicts) pass through with zero network;
    each ``text`` seed costs at most one ``qloo_search`` call without a key,
    or up to three (one per extracted candidate) when the LLM extractor runs.
    """
    chips, texts = parse_seeds(seed_ids)
    signals: list[str] = list(chips)
    confirmations: list[dict] = []
    # Labels preserve request order for summaries and taste paths.
    labels: list[str] = []
    if isinstance(seed_ids, (list, tuple)):
        for seed in seed_ids:
            if isinstance(seed, str):
                if seed.strip():
                    label = _label_for_seed(seed.strip())
                    if label:
                        labels.append(label)
            elif isinstance(seed, dict):
                chip: str | None = None
                for field in ("id", "entity_id", "urn"):
                    try:
                        value = seed.get(field)
                    except Exception:
                        continue
                    if isinstance(value, str) and value.strip():
                        chip = value.strip()
                        break
                if chip is not None:
                    label = _label_for_seed(chip)
                    if label:
                        labels.append(label)
                else:
                    try:
                        text_value = seed.get("text")
                    except Exception:
                        text_value = None
                    if isinstance(text_value, str) and text_value.strip():
                        labels.append(text_value.strip())
    else:
        labels = [l for c in chips if (l := _label_for_seed(c))] + list(texts)
    for text in texts:
        candidates = resolve_text_seed(text)
        confirmations.append({"text": text, "candidates": candidates})
        if candidates:
            try:
                signals.append(str(candidates[0]["entity_id"]))
            except Exception:
                pass
    return signals, confirmations, labels


def _hero_call(
    city: str,
    signals: list[str] | None,
    take: int = 8,
    refinements: list[str] | None = None,
) -> list:
    """Place hero call; degrades to [] on validation or transport failure."""
    tags = catalog_tags_for(signals)
    try:
        fn = _place_fn()
        if refinements is None:
            try:
                payload = fn(city, entity_ids=signals or None, take=take, tag_ids=tags or None)
            except TypeError:
                payload = fn(city, entity_ids=signals or None, take=take)
        else:
            try:
                payload = fn(
                    city, entity_ids=signals or None, take=take, refinements=refinements,
                    tag_ids=tags or None,
                )
            except TypeError:
                try:
                    payload = fn(city, entity_ids=signals or None, take=take, refinements=refinements)
                except TypeError:
                    payload = fn(city, entity_ids=signals or None, take=take)
    except (ValueError, OSError):
        return []
    except Exception:
        return []
    if isinstance(payload, dict):
        entities = payload.get("entities", [])
    elif isinstance(payload, list):
        entities = payload
    else:
        entities = []
    return entities if isinstance(entities, list) else []


def _brand_names(
    city: str,
    signals: list[str] | None,
    take: int = 5,
    refinements: list[str] | None = None,
) -> list[str]:
    """Brand kicker names, sliced to take; [] on any failure."""
    tags = catalog_tags_for(signals)
    try:
        fn = _brand_fn()
        if refinements is None:
            try:
                payload = fn(city, entity_ids=signals or None, take=take, tag_ids=tags or None)
            except TypeError:
                payload = fn(city, entity_ids=signals or None, take=take)
        else:
            try:
                payload = fn(
                    city, entity_ids=signals or None, take=take, refinements=refinements,
                    tag_ids=tags or None,
                )
            except TypeError:
                try:
                    payload = fn(city, entity_ids=signals or None, take=take, refinements=refinements)
                except TypeError:
                    payload = fn(city, entity_ids=signals or None, take=take)
    except (ValueError, OSError):
        return []
    except Exception:
        return []
    entities = payload.get("entities", []) if isinstance(payload, dict) else []
    if not isinstance(entities, list):
        return []
    names = [str(e.get("name", "")) for e in entities if isinstance(e, dict) and e.get("name")]
    return _clean_brand_names(names)[:take]


def _usable_entities(entities: object) -> list[dict]:
    """Dicts carrying a name; everything else is upstream noise."""
    if not isinstance(entities, list):
        return []
    return [e for e in entities if isinstance(e, dict) and e.get("name")]


def _entity_score(entity: dict, rank: int, total: int) -> float:
    """Personal-fit score for one result entity, best-effort by shape.

    Live /v2/insights returns no explainability/affinity field (verified
    against the hackathon API — ``feature.explainability=true`` adds
    nothing) but results arrive in affinity order and each entity carries
    a 0..1 ``popularity``. Preference: explicit ``explainability`` (mock
    fixtures), then ``popularity``, then a rank-decay floor. Never raises.
    """
    for field in ("explainability", "popularity"):
        try:
            value = entity.get(field)
            if value is None:
                continue
            parsed = float(value)
        except (TypeError, ValueError, AttributeError):
            continue
        if parsed == parsed and 0.01 <= parsed <= 1.0:  # NaN / zero-pop guard
            return parsed
    if total > 0:
        return (total - rank) / total
    return 1.0


def _affinity_multiplier(score: float, median_score: float) -> float:
    """Score vs result-set median, clamped to 0.6..3.0 so no card reads
    ``0.0×`` (an awkward zero) or an absurd spike. 1.0 when median is 0."""
    if not median_score or median_score != median_score:
        return 1.0
    return round(max(0.6, min(3.0, score / median_score)), 1)


def _entity_latlng(entity: dict, index: int, center: list) -> list:
    """Real coordinates from ``entity.location`` when present (live shape),
    else the deterministic center-offset pin (mock fixtures carry none)."""
    location = entity.get("location") if isinstance(entity, dict) else None
    if isinstance(location, dict):
        try:
            lat = float(location.get("lat"))
            lon = float(location.get("lon"))
        except (TypeError, ValueError):
            lat, lon = 0.0, 0.0
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and (lat != 0.0 or lon != 0.0):
            return [round(lat, 6), round(lon, 6)]
    return [round(center[0] + 0.01 * index, 4), round(center[1] + 0.012 * index, 4)]


def _entity_address(entity: dict) -> str:
    """Street address: live results nest it under ``properties.address``;
    mock fixtures and older shapes carry a top-level ``address``."""
    if not isinstance(entity, dict):
        return ""
    properties = entity.get("properties")
    nested = properties.get("address") if isinstance(properties, dict) else None
    for candidate in (entity.get("address"), nested):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


_MOJIBAKE_MARKS = ("Ÿ", "Ã", "Â", "Ð", "¥", "¤", "¢", "‚", "ƒ", "„", "…Â")


def _repair_mojibake(name: str) -> str:
    """Undo UTF-8-as-cp1252 double encoding (Qloo data carries some, e.g.
    Arabic brand names served as ``ŸÙÆ…``). Round-trips the string through
    cp1252/latin-1 bytes and keeps the result only when it decodes cleanly
    as UTF-8 — legitimate names fail the decode and pass through."""
    if not any(mark in name for mark in _MOJIBAKE_MARKS):
        return name
    for codec in ("cp1252", "latin-1"):
        try:
            candidate = name.encode(codec)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        try:
            fixed = candidate.decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if fixed != name:
            return fixed
    return name


def _clean_brand_names(names: object) -> list[str]:
    """Brand kicker names: repaired, de-garbage, deduped case-insensitively,
    order-preserving. Qloo live results carry duplicates and doubly-encoded
    names; both read as broken to a judge."""
    if not isinstance(names, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in names:
        if not isinstance(raw, str):
            continue
        name = _repair_mojibake(raw).strip()
        if not name:
            continue
        # Still mojibake after repair: unfixable garbage reads worse than
        # a missing kicker — drop it.
        if any(mark in name for mark in _MOJIBAKE_MARKS):
            continue
        key = " ".join(name.lower().split())
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(name)
    return cleaned


def catalog_tags_for(signals: list[str] | None) -> list[str]:
    """Tag URNs recorded for the given chip entity IDs (chips.json rows
    carry ``tag_ids`` resolved alongside each entity). Order-preserving,
    deduped, capped at 8 — tag signals steer results when an entity goes
    stale in the insights graph (the Asake lesson: /search finds it, the
    insights graph does not). Never raises."""
    if not signals:
        return []
    tags: list[str] = []
    seen: set[str] = set()
    try:
        chips = _catalog_chips()
    except Exception:
        return []
    for signal in signals:
        if not isinstance(signal, str):
            continue
        for chip in chips:
            if not isinstance(chip, dict):
                continue
            chip_id = chip.get("entity_id")
            if not isinstance(chip_id, str) or chip_id.strip().lower() != signal.strip().lower():
                continue
            chip_tags = chip.get("tag_ids")
            if not isinstance(chip_tags, list):
                break
            for tag in chip_tags:
                if isinstance(tag, str) and tag.strip() and tag not in seen:
                    seen.add(tag)
                    tags.append(tag.strip())
            break
        if len(tags) >= 8:
            break
    return tags[:8]


def _mock_slice() -> list[dict]:
    """Full mock fixture slice, bypassing the (possibly patched) hero fn."""
    try:
        path = _qloo_module.INSIGHTS_MOCK
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        entities = data.get("entities", []) if isinstance(data, dict) else []
        usable = _usable_entities(entities)
        if usable:
            return list(usable)
    except (OSError, ValueError):
        pass
    except Exception:
        pass
    return []


def _fallback_entities(
    city: str, signals: list[str], refinements: list[str] | None = None
) -> tuple[list[dict], bool]:
    """Three-rung ladder plus mock-slice fill. Returns (entities, broadened).

    Rung order per PRD section 6: drop audiences, drop tags, drop the
    weakest (last) seed. Any rung path sets ``broadened=true``; only the
    first-hit path (five usable from the hero call) stays false. Never
    returns fewer than five; never raises.
    """
    first = _usable_entities(_hero_call(city, signals or None, take=8, refinements=refinements))
    if len(first) >= 5:
        return first[:8], False
    merged: list[dict] = list(first)
    seen = {(e.get("entity_id"), e.get("name")) for e in merged}

    def _absorb(entities: list[dict]) -> None:
        for entity in entities:
            key = (entity.get("entity_id"), entity.get("name"))
            if key not in seen:
                seen.add(key)
                merged.append(entity)

    # Rung 1: drop the weakest (last) seed — the only arg change that can
    # plausibly return a different result. Identical-param retries were
    # removed: under rate limiting they just doubled the burst and starved
    # the ladder into the synthetic fill.
    if len(merged) < 5:
        trimmed = signals[:-1] if len(signals) > 1 else signals
        _absorb(
            _usable_entities(_hero_call(city, trimmed or None, take=8, refinements=refinements))
        )
    # Final fill from the full mock slice (bypasses patched hero fn).
    if len(merged) < 5:
        _absorb(_mock_slice())
    # Last-resort synthetic cards so the contract holds even with no fixture.
    index = 0
    while len(merged) < 5:
        index += 1
        name = f"{city} transplant spot {index}"
        if name not in {e.get("name") for e in merged}:
            merged.append(
                {
                    "name": name,
                    "entity_id": f"urn:entity:place:synthetic-{index}",
                    "explainability": 0.6,
                    "address": city,
                    "tags": [],
                }
            )
    return merged[:8], True


def build_payload(
    signals: list[str] | None,
    confirmations: list[dict] | None,
    labels: list[str] | None,
    city: str,
    refinements: list[str] | None = None,
    note: str | None = None,
) -> dict:
    """Assemble the PRD section 10 envelope from resolved signals. No search."""
    city_display = city.strip() if isinstance(city, str) and city.strip() else "London"
    refs = [r.strip() for r in (refinements or []) if isinstance(r, str) and r.strip()]
    signals = list(signals or [])
    confirmations = list(confirmations or [])
    labels = list(labels or [])
    valid = _valid_signals_for_insights(signals)
    entities, broadened = _fallback_entities(city_display, valid or [], refinements=refs)
    picked = list(entities[:5])
    # Pad to exactly 5 cards by cycling the picked list.
    if picked:
        index = 0
        while len(picked) < 5:
            picked.append(picked[index % len(picked)])
            index += 1
    picked = picked[:5]

    scores = [_entity_score(entity, rank, len(picked)) for rank, entity in enumerate(picked)]
    try:
        mid = float(median(scores)) if scores else 1.0
    except (ValueError, TypeError):
        mid = 1.0

    center = list(city_center(city_display) or [51.5074, -0.1278])
    # Seed hint for per-card copy: first human label (UUID seeds yield none),
    # falling back to the city so the copy never renders a raw entity ID.
    seed_hint = next(
        (label for label in (labels or []) if isinstance(label, str) and label.strip()),
        city_display,
    )
    cards: list[dict] = []
    real_latlngs: list = []
    for index, entity in enumerate(picked):
        name = str(entity.get("name", ""))
        tags = entity.get("tags") if isinstance(entity.get("tags"), list) else []
        tag_hint = _tag_label(tags[0]) if tags else (refs[0] if refs else "your crowd")
        tag_hint = tag_hint or (refs[0] if refs else "your crowd")
        latlng = _entity_latlng(entity, index, center)
        location = entity.get("location") if isinstance(entity, dict) else None
        if isinstance(location, dict):
            try:
                real_latlngs.append(
                    [float(location["lat"]), float(location.get("lon", location.get("lng")))]
                )
            except (KeyError, TypeError, ValueError):
                pass
        cards.append(
            {
                "name": name,
                "address": _entity_address(entity),
                "latlng": latlng,
                "why_you": f"Because {seed_hint} nights and {tag_hint} both live here",
                "affinity_multiplier": _affinity_multiplier(scores[index], mid),
                "taste_path": [seed_hint, tag_hint, name],
                "entity_id": str(entity.get("entity_id", "")),
            }
        )
    # Guarantee 5 cards even when the ladder yields nothing usable.
    while len(cards) < 5:
        index = len(cards)
        latlng = [round(center[0] + 0.01 * index, 4), round(center[1] + 0.012 * index, 4)]
        cards.append(
            {
                "name": f"{city_display} transplant spot {index + 1}",
                "address": city_display,
                "latlng": latlng,
                "why_you": f"Because {seed_hint} nights belong here",
                "affinity_multiplier": 1.0,
                "taste_path": [seed_hint, refs[0] if refs else "your crowd", f"spot {index + 1}"],
                "entity_id": f"urn:entity:place:synthetic-{index + 1}",
            }
        )
    cards = cards[:5]

    # Display center: mean over REAL entity locations only (Qloo geocodes
    # typed cities server-side, so even a city outside the dictionary frames
    # right); offset-derived or synthetic cards recenter exactly onto the
    # generation center instead.
    center = map_center_for(city_display, real_latlngs) or center

    tourist_summary = (
        f"Tourist take on {city_display}: the usual top-10 list, no reasons attached."
    )
    transplant_summary = f"Grounded transplant of {len(labels) or len(signals) or 1} tastes to {city_display}."
    try:
        panes = _summarize_fn()(labels or [city_display], {"cards": picked, "city": city_display})
        if isinstance(panes, dict):
            tourist = panes.get("tourist") if isinstance(panes.get("tourist"), dict) else {}
            transplant = (
                panes.get("transplant") if isinstance(panes.get("transplant"), dict) else {}
            )
            if isinstance(tourist.get("summary"), str) and tourist["summary"].strip():
                tourist_summary = tourist["summary"]
            if isinstance(transplant.get("summary"), str) and transplant["summary"].strip():
                transplant_summary = transplant["summary"]
            overrides = transplant.get("cards") if isinstance(transplant, dict) else []
            if isinstance(overrides, list):
                by_name: dict[str, str] = {}
                for c in overrides:
                    try:
                        if not isinstance(c, dict):
                            continue
                        raw_name = c.get("name")
                        raw_line = c.get("why_you")
                        if not isinstance(raw_name, str) or not isinstance(raw_line, str):
                            continue
                        name = raw_name.strip()
                        line = raw_line.strip()
                        if not name or not line:
                            continue
                        by_name[name] = line[:200]
                    except Exception:
                        continue
                for card in cards:
                    try:
                        if card.get("name") in by_name:
                            card["why_you"] = by_name[card["name"]]
                    except Exception:
                        continue
    except Exception:
        pass

    brands = _brand_names(city_display, valid, take=5, refinements=refs)
    applied = refinement_filters(refs)
    applied_refinements = normalized_refinements(refs)
    notices = refinement_notices(refs, broadened=broadened)
    try:
        qloo_params = build_insights_params(city_display, valid, take=8, refinements=refs)
        qloo_query = urllib.parse.urlencode(qloo_params)
    except Exception:
        qloo_query = (
            "filter.type=urn:entity:place&filter.location.query="
            + urllib.parse.quote(city_display, safe="")
            + "&feature.explainability=true&take=8"
        )
    taste_path_sentence = ""
    try:
        first_path = cards[0].get("taste_path") if cards else None
        if isinstance(first_path, list):
            # Drop raw entity IDs (bare UUIDs leak via seed_hint fallbacks).
            cleaned = [
                str(p) for p in first_path
                if isinstance(p, str) and p.strip() and not _qloo_module.is_entity_id(p.strip())
            ]
            if len(cleaned) >= 2:
                taste_path_sentence = " \u2192 ".join(cleaned)
    except Exception:
        taste_path_sentence = ""
    if not taste_path_sentence:
        try:
            head = [str(label) for label in (labels or []) if isinstance(label, str) and str(label).strip()][:2]
            tail = str(cards[0].get("name", city_display)) if cards else city_display
            taste_path_sentence = " \u2192 ".join(head + [tail]) if head else f"{city_display} \u2192 your crowd \u2192 {tail}"
        except Exception:
            taste_path_sentence = f"{city_display} \u2192 your crowd \u2192 {city_display}"
    model_slug = _model_slug()
    why_qloo = {
        "taste_path_sentence": taste_path_sentence,
        "qloo_query": qloo_query,
        "model": model_slug,
    }
    envelope: dict = {
        "tourist": {
            "summary": tourist_summary,
            "items": [
                f"Top 10 restaurants in {city_display}",
                f"Must-see sights in {city_display}",
                f"Best nightlife in {city_display}",
            ],
        },
        "transplant": {
            "summary": transplant_summary,
            "cards": cards,
            "brands": brands,
            "map_center": center,
            "map_pins": [{"latlng": card["latlng"], "card_index": i} for i, card in enumerate(cards)],
        },
        "confirmations": confirmations,
        "notices": notices,
        "why_qloo": why_qloo,
        "meta": {
            "model": _model_slug(),
            "cache_hit": False,
            "broadened": broadened,
            "qloo_mode": _qloo_mode(),
            "applied_refinements": applied_refinements,
            "applied_filters": applied,
        },
    }
    if isinstance(note, str) and note.strip():
        envelope["note"] = note.strip()
    return envelope


def run_transplant(seed_ids: object, city: str, refinements: list[str] | None = None) -> dict:
    """Build the PRD section 10 envelope. Never raises on Qloo/LLM failure."""
    try:
        signals, confirmations, labels = resolve_seeds(seed_ids or [])
    except Exception:
        signals, confirmations, labels = [], [], []
    try:
        unresolved = [c.get("text", "") for c in confirmations if not c.get("candidates")]
        note = build_note(unresolved)
    except Exception:
        note = None
    try:
        return build_payload(signals, confirmations, labels, city, refinements, note)
    except Exception:
        try:
            return build_payload([], [], [str(city)], city, refinements, None)
        except Exception:
            city_display = city.strip() if isinstance(city, str) and city.strip() else "London"
            try:
                fallback_query = urllib.parse.urlencode(build_insights_params(city_display, None, take=8))
            except Exception:
                fallback_query = (
                    "filter.type=urn:entity:place&filter.location.query="
                    + urllib.parse.quote(city_display, safe="")
                    + "&feature.explainability=true&take=8"
                )
            return {
                "tourist": {"summary": "Tourist take.", "items": []},
                "transplant": {"summary": "Transplant.", "cards": [], "brands": [], "map_center": [51.5074, -0.1278], "map_pins": []},
                "confirmations": [],
                "notices": ["broadened search to fill results"],
                "why_qloo": {
                    "taste_path_sentence": f"{city_display} \u2192 your crowd \u2192 {city_display}",
                    "qloo_query": fallback_query,
                    "model": _model_slug(),
                },
                "meta": {"model": _model_slug(), "cache_hit": False, "broadened": True, "qloo_mode": _qloo_mode(), "applied_refinements": [], "applied_filters": {}},
            }
