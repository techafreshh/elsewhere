"""Resolve chip/seed placeholder URNs to real Qloo entity IDs via /search.

Operator-run only, with the real key in the environment (``.env`` on the
VPS, never committed): for each chip label in ``backend/data/chips.json``
the script calls ``qloo_search`` once, keeps the first ``urn:entity:`` hit
that passes ``agent._is_hit_relevant``, and rewrites ``chips.json``,
``seed_pairs.json`` (remapped through the pre-resolution chip
``entity_id``-to-label table), and the inline frontend snapshots
(``FALLBACK_CHIPS`` in ``frontend/src/taste.ts``, ``HERO_LONDON_SEEDS`` in
``frontend/src/App.tsx``) in place. Labels, categories, and cities are
never touched; unresolvable labels are reported and left unchanged.

Without ``QLOO_API_KEY`` the script refuses (exit 2) and touches nothing.
Exit 0 when every label resolved, 1 when at least one did not.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Repo root on sys.path so `python backend/scripts/resolve_chips.py` works
# from the repo root (the script dir alone cannot resolve `backend.*`).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from backend.app import qloo as _qloo_module
    from backend.app.agent import _is_hit_relevant
    from backend.app.qloo import qloo_search
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import qloo as _qloo_module
    from app.agent import _is_hit_relevant
    from app.qloo import qloo_search

_ORIG_SEARCH = _qloo_module.qloo_search

CHIPS_PATH = _REPO_ROOT / "backend" / "data" / "chips.json"
SEED_PAIRS_PATH = _REPO_ROOT / "backend" / "data" / "seed_pairs.json"
TASTE_TS_PATH = _REPO_ROOT / "frontend" / "src" / "taste.ts"
APP_TSX_PATH = _REPO_ROOT / "frontend" / "src" / "App.tsx"

TASTE_TS_MARKER = "FALLBACK_CHIPS"
APP_TSX_MARKER = "HERO_LONDON_SEEDS"


def _search_fn():
    """Search callable, honoring monkeypatches on this module or qloo."""
    candidate = globals().get("qloo_search")
    if candidate is not None and candidate is not _ORIG_SEARCH:
        return candidate
    return _qloo_module.qloo_search


def pick_best_hit(label: str, hits: object) -> str | None:
    """First ``urn:entity:`` hit (of at most 3) relevant to the label."""
    if not isinstance(hits, list):
        return None
    for hit in hits[:3]:
        if not isinstance(hit, dict):
            continue
        try:
            entity_id = hit.get("entity_id") or hit.get("id")
        except Exception:
            continue
        if not isinstance(entity_id, str) or not entity_id.startswith("urn:entity:"):
            continue
        try:
            relevant = _is_hit_relevant(label, hit)
        except Exception:
            relevant = False
        if relevant:
            return entity_id
    return None


def _replace_in_block(text: str, marker: str, urn_map: dict[str, str]) -> str | None:
    """Swap old URNs for new ones inside the ``marker`` ... ``];`` block."""
    start = text.find(marker)
    if start < 0:
        return None
    end = text.find("];", start)
    if end < 0:
        return None
    end += 2
    block = text[start:end]
    for old_urn, new_urn in urn_map.items():
        block = block.replace(old_urn, new_urn)
    return text[:start] + block + text[end:]


def main() -> int:
    """Resolve every chip label; rewrite JSON + TS snapshots in place."""
    if not os.environ.get("QLOO_API_KEY"):
        print(
            "resolve_chips: QLOO_API_KEY is not set; refusing to resolve "
            "(the key stays in .env only, never in the repo).",
            file=sys.stderr,
        )
        return 2
    chips_path = Path(CHIPS_PATH)
    pairs_path = Path(SEED_PAIRS_PATH)
    taste_ts_path = Path(TASTE_TS_PATH)
    app_tsx_path = Path(APP_TSX_PATH)
    try:
        chips = json.loads(chips_path.read_text(encoding="utf-8"))
        pairs = json.loads(pairs_path.read_text(encoding="utf-8"))
        taste_ts = taste_ts_path.read_text(encoding="utf-8")
        app_tsx = app_tsx_path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"resolve_chips: cannot read inputs: {exc}", file=sys.stderr)
        return 1
    if not isinstance(chips, list) or not isinstance(pairs, list):
        print("resolve_chips: chips.json and seed_pairs.json must be lists", file=sys.stderr)
        return 1
    for path, text, marker in (
        (taste_ts_path, taste_ts, TASTE_TS_MARKER),
        (app_tsx_path, app_tsx, APP_TSX_MARKER),
    ):
        if marker not in text or "];" not in text[text.find(marker):]:
            print(
                f"resolve_chips: block marker {marker!r} missing in {path}; "
                "refusing to rewrite.",
                file=sys.stderr,
            )
            return 1
    old_to_label: dict[str, str] = {}
    for chip in chips:
        if not isinstance(chip, dict):
            continue
        entity_id = chip.get("entity_id")
        label = chip.get("label")
        if isinstance(entity_id, str) and isinstance(label, str) and label.strip():
            old_to_label.setdefault(entity_id, label)
    labels: list[str] = []
    for chip in chips:
        if not isinstance(chip, dict):
            continue
        label = chip.get("label")
        if isinstance(label, str) and label.strip() and label not in labels:
            labels.append(label)
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for label in labels:
        try:
            hits = _search_fn()(label)
        except Exception:
            hits = None
        urn = pick_best_hit(label, hits)
        if urn is None:
            unresolved.append(label)
            print(f"UNRESOLVED {label}")
        else:
            resolved[label] = urn
            print(f"RESOLVED {label} -> {urn}")
    for pair in pairs:
        seed_ids = pair.get("seed_ids") if isinstance(pair, dict) else None
        if not isinstance(seed_ids, list):
            continue
        for seed_id in seed_ids:
            if (
                isinstance(seed_id, str)
                and seed_id not in old_to_label
                and seed_id not in unresolved
            ):
                unresolved.append(seed_id)
                print(f"UNRESOLVED {seed_id}")
    changed = False
    new_chips: list[dict] = []
    for chip in chips:
        if not isinstance(chip, dict):
            new_chips.append(chip)
            continue
        entry = dict(chip)
        label = entry.get("label")
        if (
            isinstance(label, str)
            and label in resolved
            and entry.get("entity_id") != resolved[label]
        ):
            entry["entity_id"] = resolved[label]
            changed = True
        new_chips.append(entry)
    new_pairs: list[dict] = []
    for pair in pairs:
        if not isinstance(pair, dict):
            new_pairs.append(pair)
            continue
        entry = dict(pair)
        seed_ids = entry.get("seed_ids")
        if isinstance(seed_ids, list):
            remapped: list = []
            for seed_id in seed_ids:
                label = old_to_label.get(seed_id) if isinstance(seed_id, str) else None
                if (
                    isinstance(label, str)
                    and label in resolved
                    and resolved[label] != seed_id
                ):
                    remapped.append(resolved[label])
                    changed = True
                else:
                    remapped.append(seed_id)
            entry["seed_ids"] = remapped
        new_pairs.append(entry)
    if changed:
        urn_map = {
            old_urn: resolved[label]
            for old_urn, label in old_to_label.items()
            if label in resolved
        }
        new_taste_ts = _replace_in_block(taste_ts, TASTE_TS_MARKER, urn_map)
        new_app_tsx = _replace_in_block(app_tsx, APP_TSX_MARKER, urn_map)
        if new_taste_ts is None or new_app_tsx is None:  # rechecked; never silent
            print("resolve_chips: frontend block vanished; refusing to rewrite.",
                  file=sys.stderr)
            return 1
        chips_path.write_text(
            json.dumps(new_chips, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        pairs_path.write_text(
            json.dumps(new_pairs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if new_taste_ts != taste_ts:
            taste_ts_path.write_text(new_taste_ts, encoding="utf-8")
        if new_app_tsx != app_tsx:
            app_tsx_path.write_text(new_app_tsx, encoding="utf-8")
        print(f"resolve_chips: rewrote {len(resolved)} label(s)")
    if unresolved:
        print(f"resolve_chips: {len(unresolved)} label(s) unresolved")
        return 1
    print(f"resolve_chips: resolved all {len(resolved)} label(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
