"""ELSEWHERE walking skeleton: FastAPI app serving the SPA placeholder + mock health."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

try:
    from backend.app.catalog import get_catalog, get_seed_pairs
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app.catalog import get_catalog, get_seed_pairs

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
