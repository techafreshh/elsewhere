"""ELSEWHERE walking skeleton: FastAPI app serving the SPA placeholder + mock health."""

import os
from pathlib import Path
from statistics import median

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
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
    from backend.app.agent import (
        normalized_refinements,
        refinement_filters,
        refinement_notices,
        run_transplant,
    )
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app.agent import (
        normalized_refinements,
        refinement_filters,
        refinement_notices,
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


def _build_transplant_payload(seed_ids: list[str], city: str, refinements: list[str]) -> dict:
    """Delegate to the agent loop (PRD section 10 envelope). Never raises."""
    payload = run_transplant(seed_ids, city, refinements)
    if not isinstance(payload, dict):
        return payload
    return _apply_refinement_echo(payload, refinements)


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
        stored_meta = stored.get("meta")
        if not isinstance(stored_meta, dict):
            stored_meta = {}
        stored = {**stored, "meta": {**stored_meta,
                                    "cache_hit": True, "qloo_mode": "cache"}}
        stored = _apply_refinement_echo(stored, refinements)
        stored_meta = stored.get("meta")
        if isinstance(stored_meta, dict):
            stored_meta["cache_hit"] = True
            stored_meta["qloo_mode"] = "cache"
        return JSONResponse(status_code=200, content=stored)
    payload = _build_transplant_payload(seed_ids, raw_city, refinements)
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
