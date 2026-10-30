"""Resolve chip labels to Qloo entities that the /v2/insights graph knows.

Operator-run only, with the real key in the environment (``.env`` on the
VPS, never committed). Live-wiring lesson baked in: ``/search`` happily
returns entities the insights graph does not know (the Asake artist ID
warns ``not_found`` and the signal is silently dropped, degrading every
recommendation to popularity-sorted generics). So resolution validates:

for each chip in ``backend/data/chips.json``, an existing ``entity_id``
that probes clean is kept verbatim (curated picks outrank a fresh
search) — only its tag harvest runs. Otherwise the script searches,
orders candidate hits by category fit + label relevance, and probes each
against ``/v2/insights`` (one minimal GET per candidate) until one comes
back without a ``not_found`` warning. The winning row is rewritten with

- ``entity_id`` — probed-usable entity (best relevant hit when none probe
  clean; its tags still steer the request),
- ``entity_name`` — the entity's own name (diagnostics),
- ``entity_ok`` — whether the entity probed clean,
- ``tag_ids`` — up to 4 genre/category-flavoured tag URNs from the hit,
  sent as ``signal.interests.tags`` so taste survives entity loss.

Only ``chips.json`` and the ``FALLBACK_CHIPS`` snapshot in
``frontend/src/taste.ts`` are rewritten, in place; labels and categories
are never touched. Without ``QLOO_API_KEY`` the script refuses (exit 2).
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
    from backend.app.qloo import is_entity_id, qloo_search, signal_probe_ok
except ModuleNotFoundError:  # served with backend/ on sys.path (app.main)
    from app import qloo as _qloo_module
    from app.agent import _is_hit_relevant
    from app.qloo import is_entity_id, qloo_search, signal_probe_ok

_ORIG_SEARCH = _qloo_module.qloo_search
_ORIG_PROBE = _qloo_module.signal_probe_ok

CHIPS_PATH = _REPO_ROOT / "backend" / "data" / "chips.json"
TASTE_TS_PATH = _REPO_ROOT / "frontend" / "src" / "taste.ts"
TASTE_TS_MARKER = "FALLBACK_CHIPS"

# Chip category -> Qloo type substrings a resolution should prefer, so an
# "Owambe parties" chip resolves to the owambe restaurant/place, not the
# first textually relevant climbing gym or movie.
CATEGORY_TYPES: dict[str, tuple[str, ...]] = {
    "Music": ("artist", "music"),
    "Food": ("place", "brand", "cuisine", "food", "restaurant"),
    "Screens": ("movie", "tv", "media", "place", "brand"),
    "Vibes": ("place", "locality", "brand", "tag"),
}

# Tag types worth sending as signal.interests.tags when the entity itself
# is missing from the insights graph (or to reinforce it).
_TAG_TYPE_HINTS = ("genre", "category", "cuisine", "ambience", "audience", "music", "food", "media")

# Operator-curated search queries for vibes labels. Chip labels are taste
# phrases, not entity names — searching "Owambe parties" verbatim surfaces a
# trampoline park that mentions "Kitty Parties", and "Quiet rooftops" a book
# titled "Quiet". The override is the query the OPERATOR verified returns
# the intended kind of entity; relevance is then checked against the
# override's tokens.
OVERRIDES: dict[str, str] = {
    # Vibes labels search for the venue/activity, never the phrase verbatim.
    "Rooftop sunsets": "rooftop bar",
    "Quiet bookshops": "bookshop",
    "Jazz clubs": "jazz club",
    "Karaoke nights": "karaoke",
    "Techno nights": "techno club",
    "Street markets": "street market",
    # Food
    "2am shawarma": "shawarma",
    "Jollof rice": "jollof",
    "Late-night ramen": "ramen",
    "Street tacos": "tacos",
    "Dim sum": "dim sum",
    "Slow brunch": "brunch",
    # Screens
    "Late-night cinema": "cinema",
    "Nollywood drama": "Nollywood",
    "Bollywood musicals": "Bollywood",
    "Anime marathons": "anime",
    "K-drama nights": "K-drama",
    "Music documentaries": "music documentary",
}

# Labels whose stored entity probes clean but is known-wrong (a previous
# run's one-token relevance accident that happens to validate). These
# always take the search+probe path.
FORCE_RESEARCH: set[str] = {
    "Jollof rice",
    "Nollywood drama",
    "K-drama nights",
    "Music documentaries",
    "Quiet bookshops",
    "Slow brunch",
}


def _search_fn():
    """Search callable, honoring monkeypatches on this module or qloo."""
    candidate = globals().get("qloo_search")
    if candidate is not None and candidate is not _ORIG_SEARCH:
        return candidate
    return _qloo_module.qloo_search


def _probe_fn():
    """Probe callable, honoring monkeypatches (tests stub this)."""
    candidate = globals().get("signal_probe_ok")
    if candidate is not None and candidate is not _ORIG_PROBE:
        return candidate
    return _qloo_module.signal_probe_ok


def _hit_types(hit: dict) -> str:
    try:
        return " ".join(str(t) for t in (hit.get("types") or [])).lower()
    except Exception:
        return ""


def _ordered_candidates(label: str, hits: object, prefer_types: tuple[str, ...]) -> list[dict]:
    """Relevant hits (at most 5), on-category first, relevance-ordered."""
    if not isinstance(hits, list):
        return []
    on_category: list[dict] = []
    off_category: list[dict] = []
    for hit in hits[:8]:
        if not isinstance(hit, dict):
            continue
        entity_id = hit.get("entity_id") or hit.get("id")
        if not is_entity_id(entity_id):
            continue
        try:
            relevant = _is_hit_relevant(label, hit)
        except Exception:
            relevant = False
        if not relevant:
            continue
        if prefer_types and any(p in _hit_types(hit) for p in prefer_types):
            on_category.append(hit)
        else:
            off_category.append(hit)
    return (on_category + off_category)[:5]


def _pick_tags(hit: dict, cap: int = 4) -> list[str]:
    """Genre/category-flavoured tag URNs from a search hit, capped. Keyword
    tags (``urn:tag:keyword:…`` — per-title plot nouns like
    ``reference_to_a_suicide_attempt``) are noise and never harvested."""
    tags = hit.get("tags") if isinstance(hit.get("tags"), list) else []
    picked: list[str] = []
    fallback: list[str] = []
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        tag_id = tag.get("tag_id") or tag.get("value") or tag.get("id")
        if not isinstance(tag_id, str) or not tag_id.startswith("urn:tag:"):
            continue
        tag_type = str(tag.get("type") or "")
        if "keyword" in tag_type:
            continue
        if any(hint in tag_type for hint in _TAG_TYPE_HINTS):
            if tag_id not in picked:
                picked.append(tag_id)
        elif tag_id not in fallback:
            fallback.append(tag_id)
        if len(picked) >= cap:
            return picked[:cap]
    return (picked + fallback)[:cap]


def resolve_label(label: str, prefer_types: tuple[str, ...], existing_id: str | None) -> dict | None:
    """Search + probe one label. Returns the resolved fields or None.

    An existing ``entity_id`` that probes clean wins immediately (the
    catalog's curated picks outrank a fresh search); search + probe only
    repair chips whose stored entity is absent from the insights graph.
    """
    if existing_id and is_entity_id(existing_id) and label not in FORCE_RESEARCH:
        try:
            if _probe_fn()(existing_id):
                hit = _hit_for_id(label, existing_id)
                return {
                    "entity_id": existing_id,
                    "entity_name": str(hit.get("name", "")) if hit else "",
                    "entity_ok": True,
                    "tag_ids": _pick_tags(hit) if hit else [],
                }
        except Exception:
            pass
    try:
        hits = _search_fn()(OVERRIDES.get(label, label))
    except Exception:
        hits = None
    candidates = _ordered_candidates(OVERRIDES.get(label, label), hits, prefer_types)
    if not candidates:
        return None
    for hit in candidates:
        entity_id = str(hit.get("entity_id") or hit.get("id"))
        try:
            ok = _probe_fn()(entity_id)
        except Exception:
            ok = False
        if ok:
            return {
                "entity_id": entity_id,
                "entity_name": str(hit.get("name", "")),
                "entity_ok": True,
                "tag_ids": _pick_tags(hit),
            }
    # Nothing probed clean: keep the best relevant hit (its tags still
    # steer the request — the insights drop is a warning, not an error).
    hit = candidates[0]
    return {
        "entity_id": str(hit.get("entity_id") or hit.get("id")),
        "entity_name": str(hit.get("name", "")),
        "entity_ok": False,
        "tag_ids": _pick_tags(hit),
    }


def _hit_for_id(label: str, entity_id: str) -> dict | None:
    """The search hit carrying ``entity_id`` (tag harvest for kept picks)."""
    for query in dict.fromkeys([label, OVERRIDES.get(label, label)]):
        try:
            hits = _search_fn()(query)
        except Exception:
            continue
        if not isinstance(hits, list):
            continue
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            hit_id = hit.get("entity_id") or hit.get("id")
            if isinstance(hit_id, str) and hit_id.strip().lower() == entity_id.strip().lower():
                return hit
    return None


def _rebuild_chips_block(text: str, marker: str, chips: list) -> str | None:
    """Regenerate the ``FALLBACK_CHIPS: Chip[] = [...]`` block body verbatim
    from the resolved chips list. Rebuilding beats old-id string replacement:
    a replace misses whenever chips.json and the snapshot have drifted."""
    start = text.find(marker)
    if start < 0:
        return None
    eq = text.find("= [", start)
    if eq < 0:
        return None
    end = text.find("];", eq)
    if end < 0:
        return None
    lines: list[str] = []
    for chip in chips:
        if not isinstance(chip, dict):
            continue
        category = str(chip.get("category", "")).replace('"', "'")
        entity_id = str(chip.get("entity_id", "")).replace('"', "'")
        label = str(chip.get("label", "")).replace('"', "'")
        lines.append(f'  {{ category: "{category}", entity_id: "{entity_id}", label: "{label}" }},')
    return text[: eq + 3] + "\n" + "\n".join(lines) + "\n" + text[end:]


def main() -> int:
    """Resolve every chip label; rewrite chips.json + FALLBACK_CHIPS."""
    if not os.environ.get("QLOO_API_KEY"):
        print(
            "resolve_chips: QLOO_API_KEY is not set; refusing to resolve "
            "(the key stays in .env only, never in the repo).",
            file=sys.stderr,
        )
        return 2
    chips_path = Path(CHIPS_PATH)
    taste_ts_path = Path(TASTE_TS_PATH)
    try:
        chips = json.loads(chips_path.read_text(encoding="utf-8"))
        taste_ts = taste_ts_path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"resolve_chips: cannot read inputs: {exc}", file=sys.stderr)
        return 1
    if not isinstance(chips, list):
        print("resolve_chips: chips.json must be a list", file=sys.stderr)
        return 1
    if TASTE_TS_MARKER not in taste_ts or "];" not in taste_ts[taste_ts.find(TASTE_TS_MARKER):]:
        print(
            f"resolve_chips: block marker {TASTE_TS_MARKER!r} missing in {taste_ts_path}; "
            "refusing to rewrite.",
            file=sys.stderr,
        )
        return 1

    resolved: dict[str, dict] = {}
    unresolved: list[str] = []
    for chip in chips:
        if not isinstance(chip, dict):
            continue
        label = chip.get("label")
        if not isinstance(label, str) or not label.strip() or label in resolved:
            continue
        prefer = CATEGORY_TYPES.get(str(chip.get("category", "")), ())
        existing = chip.get("entity_id") if isinstance(chip.get("entity_id"), str) else None
        fields = resolve_label(label, prefer, existing)
        if fields is None:
            unresolved.append(label)
            print(f"UNRESOLVED {label}")
            continue
        resolved[label] = fields
        flag = "OK" if fields["entity_ok"] else "TAG-ONLY"
        print(
            f"RESOLVED {label} -> {fields['entity_id']} [{flag}] "
            f"name={fields['entity_name']!r} tags={len(fields['tag_ids'])}"
        )

    changed = False
    new_chips: list[dict] = []
    for chip in chips:
        if not isinstance(chip, dict):
            new_chips.append(chip)
            continue
        entry = dict(chip)
        label = entry.get("label")
        if isinstance(label, str) and label in resolved:
            fields = resolved[label]
            for key, value in fields.items():
                if entry.get(key) != value:
                    entry[key] = value
                    changed = True
        new_chips.append(entry)

    # The frontend snapshot is rebuilt unconditionally: it must mirror
    # chips.json even on an idempotent re-run where no chip changed.
    new_taste_ts = _rebuild_chips_block(taste_ts, TASTE_TS_MARKER, new_chips)
    if new_taste_ts is None:  # rechecked; never silent
        print("resolve_chips: frontend block vanished; refusing to rewrite.",
              file=sys.stderr)
        return 1
    if changed:
        chips_path.write_text(
            json.dumps(new_chips, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if new_taste_ts != taste_ts:
        taste_ts_path.write_text(new_taste_ts, encoding="utf-8")
        print("resolve_chips: resynced FALLBACK_CHIPS snapshot")
    if changed:
        print(f"resolve_chips: rewrote {len(resolved)} label(s)")
    if unresolved:
        print(f"resolve_chips: {len(unresolved)} label(s) unresolved")
        return 1
    print(f"resolve_chips: resolved all {len(resolved)} label(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
