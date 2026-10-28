"""ELSEWHERE walking skeleton: FastAPI app serving the SPA placeholder + mock health."""

import os
from pathlib import Path
from statistics import median

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

try:
    from backend.app.cache import cache_get, cache_key, cache_put
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app.cache import cache_get, cache_key, cache_put

try:
    from backend.app.catalog import get_catalog, get_seed_pairs
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app.catalog import get_catalog, get_seed_pairs

try:
    from backend.app.qloo import qloo_insights_brand, qloo_insights_place
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app.qloo import qloo_insights_brand, qloo_insights_place

MODEL_SLUG = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash")

# Map fallback when the mock fixture carries no coordinates.
_FALLBACK_CENTERS = {
    "london": [51.5074, -0.1278],
    "toronto": [43.6532, -79.3832],
    "nyc": [40.7128, -74.0060],
    "lagos": [6.5244, 3.3792],
    "nairobi": [-1.2921, 36.8219],
}

app = FastAPI(title="ELSEWHERE")


@app.get("/api/health")
def health() -> dict:
    """Mock-mode liveness probe. No env reads, no outbound calls."""
    return {"status": "ok", "mode": "mock", "qloo": True, "model": True}


@app.get("/api/catalog")
def catalog() -> dict:
    """Chip catalog grouped by category + city list, from JSON on disk."""
    return get_catalog()


@app.get("/api/seed_pairs")
def seed_pairs() -> list:
    """The 4 frozen demo seed pairs, from JSON on disk."""
    return get_seed_pairs()


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


def _build_transplant_payload(seed_ids: list[str], city: str, refinements: list[str]) -> dict:
    """Deterministic mock payload in the PRD section 10 envelope. Never raises."""
    city_display = city.strip() or "London"
    try:
        entities = qloo_insights_place(city_display).get("entities", [])
    except (ValueError, OSError):
        entities = []
    if not isinstance(entities, list) or not entities:
        entities = [{"name": city_display, "entity_id": "urn:entity:place:mock-fallback",
                     "explainability": 1.0, "address": city_display, "tags": []}]
    picked = [e for e in entities if isinstance(e, dict) and e.get("name")][:5] or entities[:5]
    scores = [float(e.get("explainability", 1.0) or 1.0) for e in picked]
    mid = float(median(scores)) or 1.0
    center = list(_FALLBACK_CENTERS.get(city_display.strip().lower(), [51.5074, -0.1278]))
    seed_hint = _short_label(seed_ids[0]) if seed_ids else city_display
    cards: list[dict] = []
    for index, entity in enumerate(picked):
        name = str(entity.get("name", ""))
        tags = entity.get("tags") if isinstance(entity.get("tags"), list) else []
        tag_hint = str(tags[0]).replace("-", " ") if tags else (refinements[0] if refinements else "your crowd")
        latlng = [round(center[0] + 0.01 * index, 4), round(center[1] + 0.012 * index, 4)]
        score = float(entity.get("explainability", 1.0) or 1.0)
        cards.append({
            "name": name,
            "address": str(entity.get("address", "")),
            "latlng": latlng,
            "why_you": f"Because {seed_hint} nights and {tag_hint} both live here",
            "affinity_multiplier": round(score / mid, 2),
            "taste_path": [seed_hint, tag_hint, name],
            "entity_id": str(entity.get("entity_id", "")),
        })
    try:
        brand_entities = qloo_insights_brand(city_display).get("entities", [])
        brands = [str(e.get("name", "")) for e in brand_entities
                  if isinstance(e, dict) and e.get("name")][:5]
    except (ValueError, OSError):
        brands = []
    return {
        "tourist": {
            "summary": f"Tourist take on {city_display}: the usual top-10 list, no reasons attached.",
            "items": [
                f"Top 10 restaurants in {city_display}",
                f"Must-see sights in {city_display}",
                f"Best nightlife in {city_display}",
            ],
        },
        "transplant": {
            "summary": f"Grounded transplant of {len(seed_ids)} tastes to {city_display}.",
            "cards": cards,
            "brands": brands,
            "map_center": center,
            "map_pins": [{"latlng": card["latlng"], "card_index": i} for i, card in enumerate(cards)],
        },
        "confirmations": [],
        "meta": {"model": MODEL_SLUG, "cache_hit": False,
                 "broadened": False, "qloo_mode": _qloo_mode()},
    }


@app.post("/api/transplant")
async def transplant(request: Request) -> JSONResponse:
    """Cache-first transplant: hit returns the stored payload, miss builds and stores."""
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
        key = cache_key(seed_ids, raw_city, refinements)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    stored = cache_get(key)
    if isinstance(stored, dict):
        stored = {**stored, "meta": {**stored.get("meta", {}),
                                    "cache_hit": True, "qloo_mode": "cache"}}
        return JSONResponse(status_code=200, content=stored)
    payload = _build_transplant_payload(seed_ids, raw_city, refinements)
    try:
        cache_put(key, payload)
    except (ValueError, OSError):
        pass  # cache write failure must never fail the demo
    return JSONResponse(status_code=200, content=payload)


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
