"""Resolve-chips operator script: refusal, mocked resolution, unresolved.

The script is loaded by path (like ``test_seed_cache.py``) so the tests
exercise the real file; all writes are redirected at tmp copies of the
data and frontend snapshots, never the repo originals.
"""

import importlib.util
import json
import urllib.request
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "resolve_chips.py"
_QLOO_SOURCE = Path(__file__).resolve().parents[1] / "app" / "qloo.py"


def _load_resolve_module():
    """Import the resolution script by path so tests exercise the real file."""
    spec = importlib.util.spec_from_file_location("resolve_chips", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolve_chips = _load_resolve_module()


@pytest.fixture()
def _isolated_files(monkeypatch, tmp_path):
    """Redirect the script's four read/write targets at tmp copies."""
    repo = Path(__file__).resolve().parents[2]
    targets = {
        "CHIPS_PATH": repo / "backend" / "data" / "chips.json",
        "SEED_PAIRS_PATH": repo / "backend" / "data" / "seed_pairs.json",
        "TASTE_TS_PATH": repo / "frontend" / "src" / "taste.ts",
        "APP_TSX_PATH": repo / "frontend" / "src" / "App.tsx",
    }
    copies = {}
    for attr, src in targets.items():
        dst = tmp_path / src.name
        dst.write_bytes(src.read_bytes())
        monkeypatch.setattr(resolve_chips, attr, dst)
        copies[attr] = dst
    monkeypatch.delenv("QLOO_API_KEY", raising=False)

    def _forbidden(*args, **kwargs):
        raise AssertionError("no network in mock mode")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)
    return copies


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


_CATEGORY_TYPE = {
    "Music": "artist",
    "Food": "place",
    "Screens": "movie",
    "Vibes": "tag",
}


def _fake_hit(label: str, category: str | None = None) -> dict:
    """Fake search hit; with a category its ``types`` match the chip's, so
    the resolver's first pick is final (one query per label, no retyped
    retry) — exactly the contract exercised in the live run."""
    slug = label.lower().replace(" ", "-")
    hit = {"name": label, "entity_id": f"urn:entity:test:real-{slug}"}
    qtype = _CATEGORY_TYPE.get(category) if category else None
    if qtype:
        hit["types"] = [f"urn:entity:{qtype}"]
    return hit


def test_no_key_refuses_and_touches_nothing(_isolated_files, capsys) -> None:
    before = {attr: Path(path).read_bytes() for attr, path in _isolated_files.items()}
    assert resolve_chips.main() == 2
    _, err = capsys.readouterr()
    assert "QLOO_API_KEY" in err
    for attr, path in _isolated_files.items():
        assert Path(path).read_bytes() == before[attr]


def test_mocked_full_resolution_rewrites_ids(_isolated_files, monkeypatch) -> None:
    monkeypatch.setenv("QLOO_API_KEY", "test-key")
    calls: list[str] = []
    original_chips = _read_json(_isolated_files["CHIPS_PATH"])
    label_category = {c["label"]: c["category"] for c in original_chips}

    def _fake_search(query: str) -> list[dict]:
        calls.append(query)
        return [_fake_hit(query, label_category.get(query))]

    monkeypatch.setattr(resolve_chips, "qloo_search", _fake_search)
    original_pairs = _read_json(_isolated_files["SEED_PAIRS_PATH"])
    old_urns = {chip["entity_id"] for chip in original_chips}

    assert resolve_chips.main() == 0

    # One search per distinct label, results cached across files. All fake
    # hits carry the chip category's type, so the first pick is final and
    # no retyped retry query is spent (the retry path is covered separately).
    assert sorted(calls) == sorted({chip["label"] for chip in original_chips})

    chips = _read_json(_isolated_files["CHIPS_PATH"])
    assert len(chips) == len(original_chips)
    for chip, original in zip(chips, original_chips):
        assert chip["entity_id"] == _fake_hit(original["label"])["entity_id"]
        assert chip["entity_id"].startswith("urn:entity:")
        assert chip["entity_id"] not in old_urns
        assert chip["label"] == original["label"]
        assert chip["category"] == original["category"]

    pairs = _read_json(_isolated_files["SEED_PAIRS_PATH"])
    assert len(pairs) == len(original_pairs)
    label_to_new = {chip["label"]: chip["entity_id"] for chip in chips}
    old_to_label = {chip["entity_id"]: chip["label"] for chip in original_chips}
    for pair, original in zip(pairs, original_pairs):
        assert pair["from_home"] == original["from_home"]
        assert pair["dest_city"] == original["dest_city"]
        assert pair["seed_ids"] == [
            label_to_new[old_to_label[seed]] for seed in original["seed_ids"]
        ]

    taste_ts = Path(_isolated_files["TASTE_TS_PATH"]).read_text(encoding="utf-8")
    app_tsx = Path(_isolated_files["APP_TSX_PATH"]).read_text(encoding="utf-8")
    assert "FALLBACK_CHIPS" in taste_ts
    assert "HERO_LONDON_SEEDS" in app_tsx
    for old_urn in old_urns:
        assert old_urn not in taste_ts
        assert old_urn not in app_tsx
    for chip in chips:
        assert chip["entity_id"] in taste_ts


def test_unresolved_label_left_unchanged(_isolated_files, monkeypatch, capsys) -> None:
    monkeypatch.setenv("QLOO_API_KEY", "test-key")
    original_chips = _read_json(_isolated_files["CHIPS_PATH"])
    label_category = {c["label"]: c["category"] for c in original_chips}

    def _fake_search(query: str) -> list[dict]:
        if query == "Tems":
            return [{"name": "Thames River Walk", "entity_id": "urn:entity:place:thames-1"}]
        return [_fake_hit(query, label_category.get(query))]

    monkeypatch.setattr(resolve_chips, "qloo_search", _fake_search)
    original_chips = _read_json(_isolated_files["CHIPS_PATH"])

    assert resolve_chips.main() == 1
    out, _ = capsys.readouterr()
    assert "UNRESOLVED Tems" in out

    chips = _read_json(_isolated_files["CHIPS_PATH"])
    by_label = {chip["label"]: chip for chip in chips}
    assert set(by_label) == {chip["label"] for chip in original_chips}
    original_ids = {chip["label"]: chip["entity_id"] for chip in original_chips}
    tems = by_label["Tems"]
    # Unresolved label: the entity_id is untouched — whatever it was on disk
    # (placeholder URN or previously resolved real UUID) survives verbatim.
    assert tems["entity_id"] == original_ids["Tems"]
    assert tems["category"] == "Music"
    asake = by_label["Asake"]
    assert asake["entity_id"] == _fake_hit("Asake")["entity_id"]

    pairs = _read_json(_isolated_files["SEED_PAIRS_PATH"])
    assert original_ids["Tems"] in pairs[2]["seed_ids"]


def test_retyped_retries_lead_token_for_off_category(_isolated_files, monkeypatch, capsys) -> None:
    """A relevant top hit whose type misses the chip category triggers one
    lead-token retry; the retry hit must match the full label and carry a
    preferred type to win, and the retyped ID propagates into seed pairs."""
    monkeypatch.setenv("QLOO_API_KEY", "test-key")
    calls: list[str] = []
    original_chips = _read_json(_isolated_files["CHIPS_PATH"])
    label_category = {c["label"]: c["category"] for c in original_chips}
    old_to_label = {chip["entity_id"]: chip["label"] for chip in original_chips}

    def _fake_search(query: str) -> list[dict]:
        calls.append(query)
        if query == "Nollywood slow-burns":
            # Relevant by name, but a book — off-category for a Screens chip.
            return [{
                "name": "Nollywood slow-burns",
                "entity_id": "urn:entity:book:slow",
                "types": ["urn:entity:book"],
            }]
        if query == "Nollywood":
            # Lead-token retry hit: on-category, and its name still matches
            # the full label (relevance is checked against the label, not
            # the retry query).
            return [{
                "name": "Nollywood slow-burns",
                "entity_id": "urn:entity:movie:fake-movie",
                "types": ["urn:entity:movie"],
            }]
        return [_fake_hit(query, label_category.get(query))]

    monkeypatch.setattr(resolve_chips, "qloo_search", _fake_search)

    assert resolve_chips.main() == 0
    out, _ = capsys.readouterr()
    assert "RETYPED Nollywood slow-burns via 'Nollywood'" in out

    chips = _read_json(_isolated_files["CHIPS_PATH"])
    by_label = {chip["label"]: chip for chip in chips}
    assert set(by_label) == set(label_category)
    nollywood = by_label["Nollywood slow-burns"]
    assert nollywood["entity_id"] == "urn:entity:movie:fake-movie"
    asake = by_label["Asake"]
    assert asake["entity_id"] == _fake_hit("Asake")["entity_id"]

    old_nollywood_id = next(
        chip_id for chip_id, label in old_to_label.items() if label == "Nollywood slow-burns"
    )
    pairs = _read_json(_isolated_files["SEED_PAIRS_PATH"])
    assert "urn:entity:movie:fake-movie" in pairs[1]["seed_ids"]
    assert old_nollywood_id not in {s for pair in pairs for s in pair["seed_ids"]}


def test_script_source_guards() -> None:
    source = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "QLOO_API_KEY" in source
    assert "qloo_search" in source
    assert "_is_hit_relevant" in source
    assert "FALLBACK_CHIPS" in source
    assert "HERO_LONDON_SEEDS" in source
    assert "sk-" not in source
