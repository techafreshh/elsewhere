"""ELSEWHERE walking skeleton: FastAPI app serving the SPA placeholder + mock health."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="ELSEWHERE")


@app.get("/api/health")
def health() -> dict:
    """Mock-mode liveness probe. No env reads, no outbound calls."""
    return {"status": "ok", "mode": "mock", "qloo": True}


# Serve the committed SPA placeholder at /. Mounted after API routes so
# /api/* still resolves to JSON while / returns index.html.
DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="spa")
