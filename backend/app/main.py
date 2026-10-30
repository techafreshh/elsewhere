"""ELSEWHERE walking skeleton: FastAPI app serving the SPA placeholder + mock health."""

import os
from pathlib import Path
from statistics import median

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

try:
    from backend.app.cache import cache_get, cache_key, cache_put
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app.cache import cache_get, cache_key, cache_put

try:
    from backend.app.catalog import get_catalog
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app.catalog import get_catalog

try:
    from backend.app.qloo import (
        is_entity_id,
        normalize_entity_id,
        qloo_insights_brand,
        qloo_insights_place,
        qloo_related,
        qloo_search,
    )
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app.qloo import (
        is_entity_id,
        normalize_entity_id,
        qloo_insights_brand,
        qloo_insights_place,
        qloo_related,
        qloo_search,
    )

try:
    from backend.app.agent import (
        build_note,
        build_payload,
        city_lookup,
        normalized_refinements,
        parse_seeds,
        refinement_filters,
        refinement_notices,
        resolve_seeds,
        run_transplant,
    )
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app.agent import (
        build_note,
        build_payload,
        city_lookup,
        normalized_refinements,
        parse_seeds,
        refinement_filters,
        refinement_notices,
        resolve_seeds,
        run_transplant,
    )

try:
    from backend.app.qloo import qloo_insights_brand, qloo_insights_place
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app.qloo import qloo_insights_brand, qloo_insights_place

try:
    from backend.app.agui import iter_transplant_events
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app.agui import iter_transplant_events

MODEL_SLUG = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash")

app = FastAPI(title="ELSEWHERE")


@app.get("/api/health")
def health() -> dict:
    """Liveness probe. `mode` mirrors agent._qloo_mode() — live with a key,
    mock without — read at call time so key removal degrades without a
    restart. No outbound calls."""
    try:
        from app.agent import _qloo_mode  # served with backend/ on sys.path
    except ModuleNotFoundError:  # repo-root invocation
        from backend.app.agent import _qloo_mode
    return {"status": "ok", "mode": _qloo_mode(), "qloo": True, "model": True}


@app.get("/api/catalog")
def catalog() -> dict:
    """Chip catalog grouped by category + city dictionary, from JSON on disk."""
    return get_catalog()


def _entity_type_label(entity: object) -> str:
    """Human type tag for a search/insights hit ("artist", "brand", …)."""
    if not isinstance(entity, dict):
        return ""
    types = entity.get("types")
    entries = types if isinstance(types, list) else [entity.get("type")]
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            continue
        label = entry.strip()
        if label.startswith("urn:entity:"):
            label = label[len("urn:entity:"):]
        if label and label != "entity":
            return label
    return ""


def _taste_hit(hit: object) -> dict | None:
    """Normalize one qloo_search hit into a taste-suggestion shape."""
    if not isinstance(hit, dict):
        return None
    name = hit.get("name")
    entity_id = hit.get("entity_id")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(entity_id, str) or not entity_id.strip():
        return None
    return {
        "label": name.strip(),
        "entity_id": entity_id.strip(),
        "type": _entity_type_label(hit),
    }


@app.get("/api/search_tastes")
def search_tastes(q: str = "") -> JSONResponse:
    """Qloo search-as-you-type: any free text into real taste entities.

    Mock (no key) keeps only fixture hits whose name matches the query —
    the fixture is static, so echoing it for unrelated prefixes would read
    as fabricated results.
    """
    query = (q or "").strip()
    if len(query) < 2:
        return JSONResponse(
            status_code=422, content={"detail": "q must be at least 2 characters"}
        )
    try:
        hits = qloo_search(query)
    except Exception:
        hits = []
    primaries: list[dict] = []  # artists, brands, places, media — real tastes
    secondaries: list[dict] = []  # municipalities crowd short prefixes ("Ås")
    for hit in hits if isinstance(hits, list) else []:
        normalized = _taste_hit(hit)
        if normalized is None:
            continue
        if _qloo_mode() == "mock":
            name = normalized["label"].casefold()
            tokens = [tok for tok in query.casefold().split() if len(tok) >= 3]
            if tokens and not any(tok in name for tok in tokens):
                continue
        types = hit.get("types") if isinstance(hit.get("types"), list) else []
        first = types[0] if types and isinstance(types[0], str) else ""
        is_geo_noise = first in (
            "urn:entity:locality",
            "urn:entity:region",
            "urn:entity:area",
            "urn:entity:postal_code",
        )
        (secondaries if is_geo_noise else primaries).append(normalized)
        if len(primaries) >= 6 and len(secondaries) >= 3:
            break
    results = (primaries + secondaries)[:6]
    return JSONResponse(
        status_code=200, content={"results": results, "qloo_mode": _qloo_mode()}
    )


@app.post("/api/related")
async def related(request: Request) -> JSONResponse:
    """Cross-domain correlated tastes for the current selection ("people
    into your picks also like…"). Qloo artist+brand fan-out first; the chip
    catalog fills in when Qloo has nothing — including when nothing is
    picked yet (empty ``entity_ids`` is a valid "cold start" call, so the
    suggestion pills render on every onboarding step, never blank). Never
    500s. Only malformed ids are 422."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=422, content={"detail": "Invalid JSON body"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=422, content={"detail": "Body must be a JSON object"})
    raw_ids = body.get("entity_ids", [])
    if raw_ids is None:
        raw_ids = []
    if not isinstance(raw_ids, list):
        return JSONResponse(
            status_code=422, content={"detail": "entity_ids must be a list"}
        )
    selected: list[str] = []
    for value in raw_ids:
        if not isinstance(value, str) or not is_entity_id(value):
            return JSONResponse(
                status_code=422,
                content={"detail": f"invalid entity id: {value!r}"},
            )
        selected.append(normalize_entity_id(value))
        if len(selected) >= 8:
            break
    source = "qloo"
    data: dict = {}
    if selected:
        try:
            data = qloo_related(selected, take=8)
        except Exception:
            data = {}
    raw_entities = (
        data.get("entities") if isinstance(data, dict) and isinstance(data.get("entities"), list) else []
    )
    results = []
    selected_set = set(selected)
    for entity in raw_entities:
        hit = _taste_hit(entity)
        if hit is None or hit["entity_id"].lower() in selected_set:
            continue
        results.append(hit)
        # Ten, not six: the wizard filters suggestions per category section
        # (artist→Music, movie→Screens…), so the payload needs headroom.
        if len(results) >= 10:
            break
    if not results:
        source = "catalog"
        try:
            chips = [c for c in get_catalog().get("chips", []) if isinstance(c, dict)]
        except Exception:
            chips = []
        for chip in chips:
            chip_id = chip.get("entity_id")
            label = chip.get("label")
            if not isinstance(chip_id, str) or not chip_id.strip():
                continue
            if chip_id.lower() in selected_set:
                continue
            if not isinstance(label, str) or not label.strip():
                continue
            category = chip.get("category")
            results.append({
                "label": label.strip(),
                "entity_id": chip_id.strip(),
                "type": category.strip().lower() if isinstance(category, str) else "",
            })
            if len(results) >= 6:
                break
    return JSONResponse(
        status_code=200, content={"results": results, "source": source}
    )


_GEO_TYPES = (
    "urn:entity:locality",
    "urn:entity:destination",
    "urn:entity:place",
    "urn:entity:neighbourhood",
    "urn:entity:region",
    "urn:entity:area",
)

_GEO_CACHE: dict[str, dict] = {}


def _hit_location(hit: object) -> list | None:
    """Extract [lat, lon] from a search hit (top-level or properties)."""
    if not isinstance(hit, dict):
        return None
    for source in (hit.get("location"), (hit.get("properties") or {}).get("location")):
        if isinstance(source, dict):
            try:
                lat = float(source.get("lat"))
                lng = float(source.get("lon", source.get("lng")))
            except (TypeError, ValueError):
                continue
            return [lat, lng]
    return None


@app.get("/api/geocode")
def geocode(name: str = "") -> JSONResponse:
    """Typed city -> location. World-city dictionary first (instant,
    offline-capable, aliases included), then Qloo /search for a geography
    hit carrying coordinates. found=false keeps the UI honest; the
    transplant pipeline still passes the raw string to Qloo's
    filter.location.query, which geocodes server-side. Never 500s."""
    query = (name or "").strip()
    if len(query) < 2:
        return JSONResponse(
            status_code=422, content={"detail": "name must be at least 2 characters"}
        )
    key = query.casefold()
    try:
        entry = city_lookup(query)
    except Exception:
        entry = None
    if entry is not None:
        try:
            lat = float(entry.get("lat"))
            lng = float(entry.get("lng"))
        except (TypeError, ValueError):
            entry = None
        else:
            return JSONResponse(
                status_code=200,
                content={
                    "found": True,
                    "name": str(entry.get("name", query)),
                    "country": str(entry.get("country", "")),
                    "lat": lat,
                    "lng": lng,
                    "source": "dictionary",
                },
            )
    if key in _GEO_CACHE:
        return JSONResponse(status_code=200, content=_GEO_CACHE[key])
    found: dict = {"found": False}
    try:
        hits = qloo_search(query)
    except Exception:
        hits = []
    for hit in hits if isinstance(hits, list) else []:
        if not isinstance(hit, dict):
            continue
        types = hit.get("types") if isinstance(hit.get("types"), list) else []
        if not any(t in _GEO_TYPES for t in types if isinstance(t, str)):
            continue
        latlng = _hit_location(hit)
        if latlng is None:
            continue
        found = {
            "found": True,
            "name": str(hit.get("name", query)).strip() or query,
            "country": "",
            "lat": latlng[0],
            "lng": latlng[1],
            "source": "qloo",
        }
        break
    if len(_GEO_CACHE) > 64:
        _GEO_CACHE.clear()
    _GEO_CACHE[key] = found
    return JSONResponse(status_code=200, content=found)


def _extract_seed_ids(seeds: object) -> list[str]:
    """Accept seed ID strings or {"id"|"entity_id"|"text"} objects."""
    if not isinstance(seeds, (list, tuple)) or not seeds:
        return []
    ids: list[str] = []
    for seed in seeds:
        if isinstance(seed, str):
            if seed.strip():
                ids.append(seed.strip())
        elif isinstance(seed, dict):
            for field in ("id", "entity_id", "urn"):
                value = seed.get(field)
                if isinstance(value, str) and value.strip():
                    ids.append(value.strip())
                    break
            else:
                text = seed.get("text")
                if isinstance(text, str) and text.strip():
                    ids.append(text.strip())
    return ids


def _short_label(seed_id: str) -> str:
    return seed_id.split(":")[-1].replace("-", " ").strip() or seed_id


def _qloo_mode() -> str:
    """Read at call time so key removal degrades without a restart."""
    return "live" if os.environ.get("QLOO_API_KEY") else "mock"


def _apply_refinement_echo(payload: dict, refinements: list[str]) -> dict:
    """Layer refinement echo onto a transplant payload (idempotent)."""
    if not isinstance(payload, dict):
        return payload
    try:
        applied = refinement_filters(refinements)
    except Exception:
        applied = {}
    try:
        applied_refs = normalized_refinements(refinements)
    except Exception:
        applied_refs = []
    broadened = False
    try:
        meta = payload.get("meta")
        if isinstance(meta, dict):
            broadened = bool(meta.get("broadened"))
    except Exception:
        broadened = False
    try:
        notices = refinement_notices(refinements, broadened=broadened)
    except Exception:
        notices = []
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    payload["meta"] = {
        **meta,
        "applied_refinements": applied_refs,
        "applied_filters": applied if isinstance(applied, dict) else {},
    }
    payload["notices"] = notices
    return payload


def _build_transplant_payload(
    raw_seeds: object,
    city: str,
    refinements: list[str],
    signals: list[str] | None = None,
    confirmations: list[dict] | None = None,
    labels: list[str] | None = None,
    note: str | None = None,
) -> dict:
    """Delegate to the agent loop (PRD section 10 envelope). Never raises.

    When pre-resolved signals/confirmations/labels are supplied (single
    ``qloo_search`` per text already done by the caller), assemble without
    re-searching; otherwise resolve inside ``run_transplant``.
    """
    try:
        if signals is not None and confirmations is not None and labels is not None:
            payload = build_payload(signals, confirmations, labels, city, refinements, note)
        else:
            payload = run_transplant(raw_seeds, city, refinements)
    except Exception:
        try:
            payload = run_transplant(raw_seeds, city, refinements)
        except Exception:
            return {"tourist": {"summary": "Tourist take.", "items": []}, "transplant": {"summary": "Transplant.", "cards": [], "brands": [], "map_center": [51.5074, -0.1278], "map_pins": []}, "confirmations": [], "notices": [], "meta": {"model": MODEL_SLUG, "cache_hit": False, "broadened": True, "qloo_mode": _qloo_mode(), "applied_refinements": [], "applied_filters": {}}}
    if not isinstance(payload, dict):
        return payload
    return _apply_refinement_echo(payload, refinements)


def _resolve_for_transplant(raw_seeds: object) -> tuple[list[str], list[dict], list[str], str | None]:
    """One search per text seed; chips pass through. Never raises."""
    try:
        signals, confirmations, labels = resolve_seeds(raw_seeds)
    except Exception:
        return [], [], [], None
    try:
        unresolved = [
            c.get("text", "")
            for c in confirmations
            if isinstance(c, dict) and not c.get("candidates")
        ]
        note = build_note(unresolved)
    except Exception:
        unresolved = []
        note = None
    return signals, confirmations, labels, note


@app.post("/api/transplant")
async def transplant(request: Request) -> JSONResponse:
    """Cache-first transplant: hit returns the stored payload, miss builds and stores.

    Free-text ``{"text": ...}`` seeds resolve via one ``qloo_search`` each
    into ``confirmations``; chips pass through with zero latency. The cache
    key feeds on effective signal ids (chips plus top candidates) plus raw
    unresolved texts so distinct gibberish never collides.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=422, content={"detail": "Invalid JSON body"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=422, content={"detail": "Body must be a JSON object"})
    raw_seeds = body.get("seeds")
    seed_ids = _extract_seed_ids(raw_seeds)
    if not seed_ids:
        return JSONResponse(status_code=422, content={"detail": "seeds must be a non-empty list"})
    raw_city = body.get("dest_city", body.get("city", body.get("destCity", "")))
    if not isinstance(raw_city, str) or not raw_city.strip():
        return JSONResponse(status_code=422, content={"detail": "dest_city must be a non-empty string"})
    raw_refs = body.get("refinements", [])
    if raw_refs is None:
        raw_refs = []
    if isinstance(raw_refs, str):
        raw_refs = [raw_refs]
    if not isinstance(raw_refs, (list, tuple)):
        return JSONResponse(status_code=422, content={"detail": "refinements must be a list of strings"})
    refinements = [r.strip() for r in raw_refs if isinstance(r, str) and r.strip()]
    signals, confirmations, labels, note = _resolve_for_transplant(raw_seeds)
    try:
        unresolved_texts = [
            c.get("text", "")
            for c in confirmations
            if isinstance(c, dict) and not c.get("candidates")
        ]
    except Exception:
        unresolved_texts = []
    effective = list(signals) + [t for t in unresolved_texts if isinstance(t, str) and t]
    if not effective:
        effective = list(seed_ids)
    try:
        key = cache_key(effective, raw_city, refinements)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    stored = cache_get(key)
    if isinstance(stored, dict):
        stored_meta = stored.get("meta")
        if not isinstance(stored_meta, dict):
            stored_meta = {}
        stored = {**stored, "meta": {**stored_meta,
                                    "cache_hit": True, "qloo_mode": "cache"}}
        try:
            stored["confirmations"] = confirmations
            if isinstance(note, str) and note.strip():
                stored["note"] = note.strip()
            else:
                stored.pop("note", None)
        except Exception:
            pass
        stored = _apply_refinement_echo(stored, refinements)
        stored_meta = stored.get("meta")
        if isinstance(stored_meta, dict):
            stored_meta["cache_hit"] = True
            stored_meta["qloo_mode"] = "cache"
        return JSONResponse(status_code=200, content=stored)
    payload = _build_transplant_payload(
        raw_seeds, raw_city, refinements,
        signals=signals, confirmations=confirmations, labels=labels, note=note,
    )
    try:
        cache_put(key, payload)
    except (ValueError, OSError):
        pass  # cache write failure must never fail the demo
    return JSONResponse(status_code=200, content=payload)


@app.post("/agui")
async def agui(request: Request):
    """AG-UI 1.0 SSE stream of the transplant loop. Never cached."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=422, content={"detail": "Invalid JSON body"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=422, content={"detail": "Body must be a JSON object"})
    seed_ids = _extract_seed_ids(body.get("seeds"))
    if not seed_ids:
        return JSONResponse(status_code=422, content={"detail": "seeds must be a non-empty list"})
    raw_city = body.get("dest_city", body.get("city", body.get("destCity", "")))
    if not isinstance(raw_city, str) or not raw_city.strip():
        return JSONResponse(status_code=422, content={"detail": "dest_city must be a non-empty string"})
    raw_refs = body.get("refinements", [])
    if raw_refs is None:
        raw_refs = []
    if isinstance(raw_refs, str):
        raw_refs = [raw_refs]
    if not isinstance(raw_refs, (list, tuple)):
        return JSONResponse(status_code=422, content={"detail": "refinements must be a list of strings"})
    refinements = [r.strip() for r in raw_refs if isinstance(r, str) and r.strip()]
    try:
        cache_key(seed_ids, raw_city, refinements)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    return StreamingResponse(
        iter_transplant_events(seed_ids, raw_city, refinements),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.api_route("/agui", methods=["GET", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
def agui_method_not_allowed() -> JSONResponse:
    """POST-only: wrong-method hits on the known /agui path stay 405."""
    return JSONResponse(status_code=405, content={"detail": "Method Not Allowed"})


@app.api_route("/api", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@app.api_route("/api/{_:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
def api_fallback(request: Request) -> JSONResponse:
    """Unknown /api/* returns JSON 404 (never StaticFiles 405) until real endpoints land.

    A path matching a declared API route but with the wrong method returns 405
    so GET-only routes (e.g. POST /api/health) keep their 405 semantics.
    """
    path = request.url.path
    for route in app.routes:
        route_path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if route_path == path and methods and request.method not in methods:
            return JSONResponse(status_code=405, content={"detail": "Method Not Allowed"})
    return JSONResponse(status_code=404, content={"detail": "Not Found"})


# Serve the committed SPA placeholder at /. Mounted after API routes so
# /api/* still resolves to JSON while / returns index.html.
DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="spa")


@app.get("/", include_in_schema=False)
def root_fallback() -> HTMLResponse:
    """200 SPA placeholder when DIST_DIR is absent (image without dist)."""
    index = DIST_DIR / "index.html"
    if index.is_file():
        return HTMLResponse(index.read_text(encoding="utf-8", errors="replace"))
    return HTMLResponse(
        "<!doctype html><html><head><title>ELSEWHERE</title></head>"
        "<body><h1>ELSEWHERE</h1></body></html>"
    )
