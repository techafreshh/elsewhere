"""Server-side JSON cache keyed by seeds + city + refinements.

Disk-backed, stdlib-only: one JSON file per transplant result under
``backend/data/cache/<sha256>.json``. The key is
``sha256("|".join(sorted_seed_ids) + "|" + normalized_city + "|"
+ "|".join(sorted_normalized_refinements))`` so ``[a, b]`` vs ``[b, a]``
(and ``London`` vs ``london``) hit the same file.

Missing or corrupt cache files are treated as misses (``None``), never
errors, per the Never-500 pattern. Writes are atomic (tmp file + rename)
so concurrent demo traffic
cannot leave half-written JSON behind. No TTL or eviction: files live
until deleted and warming overwrites by key.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache"


def _normalize_city(city: object) -> str:
    if not isinstance(city, str) or not city.strip():
        raise ValueError("city must be a non-empty string")
    return city.strip().lower()


def _normalize_refinements(refinements: object) -> list[str]:
    if refinements is None:
        return []
    if isinstance(refinements, str):
        refinements = [refinements]
    if not isinstance(refinements, (list, tuple)):
        raise ValueError("refinements must be a list of strings")
    cleaned: list[str] = []
    for item in refinements:
        if not isinstance(item, str):
            raise ValueError("refinements must be a list of strings")
        folded = item.strip().lower()
        if folded:
            cleaned.append(folded)
    return sorted(cleaned)


def cache_key(seed_ids: object, city: object, refinements: object = None) -> str:
    """Return the 64-char hex cache key for a transplant request.

    Seed IDs are stripped and sorted; the city is case/whitespace folded;
    refinements are lowercased, stripped and sorted. Unknown refinement
    strings are kept verbatim in the key, not rejected.
    """
    if not isinstance(seed_ids, (list, tuple)) or not seed_ids:
        raise ValueError("seed_ids must be a non-empty list of IDs")
    normalized_ids: list[str] = []
    for seed_id in seed_ids:
        if not isinstance(seed_id, str) or not seed_id.strip():
            raise ValueError("seed_ids must be a non-empty list of IDs")
        normalized_ids.append(seed_id.strip())
    normalized_city = _normalize_city(city)
    normalized_refs = _normalize_refinements(refinements)
    joined = "|".join(sorted(normalized_ids)) + "|" + normalized_city + "|" + "|".join(normalized_refs)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _path_for(key: str) -> Path:
    if not isinstance(key, str) or not key:
        raise ValueError("key must be a non-empty string")
    return CACHE_DIR / f"{key}.json"


def cache_get(key: str):
    """Return the cached payload dict for ``key``, or ``None`` on miss.

    A miss covers a missing file, a corrupt/truncated file, or a payload
    that is not a JSON object. Never raises for those cases.
    """
    try:
        path = _path_for(key)
    except ValueError:
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def cache_put(key: str, payload: dict) -> Path:
    """Persist ``payload`` under ``key`` atomically; return the file path."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    path = _path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(tmp_path, path)
    return path
