"""Disk-backed config catalog: chips, cities, seed pairs."""

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _load(name: str):
    with (DATA_DIR / name).open(encoding="utf-8") as f:
        return json.load(f)


_CHIPS = _load("chips.json")
_CITIES = _load("cities.json")
_SEED_PAIRS = _load("seed_pairs.json")


def get_catalog() -> dict:
    """Return {"chips": [...], "cities": [...]} from disk (loaded once)."""
    return {"chips": _CHIPS, "cities": _CITIES}


def get_seed_pairs() -> list:
    """Return the 4 frozen seed pairs from disk (loaded once)."""
    return _SEED_PAIRS


def label_for_entity(entity_id: str) -> str | None:
    """Chip label for an entity ID (exact match), or None.

    Bare UUIDs — the live /search shape the chip catalog now carries —
    have no name to derive; this maps them back to their human label
    ("Asake", "2am shawarma", …) for summaries and taste paths.
    """
    for chip in _CHIPS:
        if isinstance(chip, dict) and chip.get("entity_id") == entity_id:
            label = chip.get("label")
            if isinstance(label, str) and label.strip():
                return label.strip()
    return None
