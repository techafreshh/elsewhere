"""Warm the server-side transplant cache for the frozen demo seed pairs.

Runs the same in-process code path as ``POST /api/transplant``
(``run_transplant`` + ``cache_put``) so it works in CI, in mock mode,
and on a cold VPS with no server up. The live Qloo key (if present) is
picked up automatically because ``qloo.py`` reads ``QLOO_API_KEY`` at
call time; with the key removed the same call still yields full
five-card reveals from the mock fixtures.

Exit status is 0 only when every seed pair warms to exactly 5 cards;
anything else exits nonzero so CI and deploy hooks fail loudly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Repo root on sys.path so `python backend/scripts/seed_cache.py` works
# from the repo root (the script dir alone cannot resolve `backend.*`).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from backend.app.agent import run_transplant
    from backend.app.cache import cache_key, cache_put
    from backend.app.catalog import get_seed_pairs
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app.agent import run_transplant
    from app.cache import cache_key, cache_put
    from app.catalog import get_seed_pairs


def _pairs_from_disk() -> list:
    """Frozen pairs straight from JSON (catalog-unavailable fallback)."""
    path = Path(__file__).resolve().parents[1] / "data" / "seed_pairs.json"
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, list) else []


def main() -> int:
    """Warm every seed pair; return 0 only when all yield exactly 5 cards."""
    try:
        pairs = get_seed_pairs()
    except Exception:
        pairs = None
    if not isinstance(pairs, list) or not pairs:
        pairs = _pairs_from_disk()
    failures = 0
    for pair in pairs:
        try:
            seed_ids = pair["seed_ids"]
            city = pair["dest_city"]
        except (TypeError, KeyError):
            print(f"SKIP malformed pair: {pair!r}")
            failures += 1
            continue
        try:
            payload = run_transplant(seed_ids, city)
        except Exception as exc:  # never let one pair abort the warming run
            print(f"FAIL {city}: build raised {exc!r}")
            failures += 1
            continue
        cards = payload.get("transplant", {}).get("cards", []) if isinstance(payload, dict) else []
        if not isinstance(cards, list) or len(cards) != 5:
            got = len(cards) if isinstance(cards, list) else repr(cards)
            print(f"FAIL {city}: expected 5 cards, got {got}")
            failures += 1
            continue
        try:
            path = cache_put(cache_key(seed_ids, city), payload)
        except Exception as exc:  # cache write must never fail the demo
            print(f"FAIL {city}: cache write raised {exc!r}")
            failures += 1
            continue
        print(f"{city} -> {path}")
    if failures:
        print(f"seed_cache: {failures} pair(s) failed")
        return 1
    print(f"seed_cache: warmed {len(pairs)} pair(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
