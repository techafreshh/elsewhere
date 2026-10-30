"""Resolve-chips operator script: refusal, mocked resolution, probe fallback.

The script is loaded by path so the tests exercise the real file; all
writes are redirected at tmp copies of chips.json and the frontend
snapshot, never the repo originals. Search and the insights probe are
monkeypatched — no network.
"""

import importlib.util
import json
import urllib.request
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "resolve_chips.py"


def _load_resolve_module():
    """Import the resolution script by path so tests exercise the real file."""
    spec = importlib.util.spec_from_file_location("resolve_chips", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolve_chips = _load_resolve_module()


@pytest.fixture()
def _isolated_files(monkeypatch, tmp_path):
    """Redirect the script's read/write targets at tmp copies."""
    repo = Path(__file__).resolve().parents[2]
    targets = {
        "CHIPS_PATH": repo / "backend" / "data" / "chips.json",
        "TASTE_TS_PATH": repo / "frontend" / "src" / "taste.ts",
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


def _fake_hit(label: str, qtype: str | None = None, probe_ok: bool = True) -> dict:
    """Fake search hit with a genre tag; ``qtype`` fills ``types`` so the
    resolver's category preference can match."""
    slug = label.lower().replace(" ", "-")
    hit = {
        "name": label,
        "entity_id": f"urn:entity:test:real-{slug}",
        "tags": [
            {"name": "Test genre", "tag_id": f"urn:tag:genre:{slug}", "type": "urn:tag:genre"}
        ],
    }
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
    probed: list[str] = []
    original_chips = _read_json(_isolated_files["CHIPS_PATH"])
    old_ids = {chip["entity_id"] for chip in original_chips}

    def _fake_search(query: str) -> list[dict]:
        calls.append(query)
        return [_fake_hit(query, qtype="place")]

    def _fake_probe(entity_id: str) -> bool:
        probed.append(entity_id)
        # Stored catalog ids are stale in this scenario; fresh search hits
        # probe clean. This forces the search+probe repair path for every
        # chip (the keep-existing path is covered by the tag-only test's
        # inverse and the live operator run).
        return entity_id not in old_ids

    monkeypatch.setattr(resolve_chips, "qloo_search", _fake_search)
    monkeypatch.setattr(resolve_chips, "signal_probe_ok", _fake_probe)

    assert resolve_chips.main() == 0

    # One search per distinct label, keyed by the operator override query
    # where one exists (vibes labels search curated queries, not verbatim).
    expected_queries = {
        resolve_chips.OVERRIDES.get(chip["label"], chip["label"])
        for chip in original_chips
    }
    assert sorted(calls) == sorted(expected_queries)
    # Stored id probed then fresh pick for every chip, except the
    # FORCE_RESEARCH labels that skip the stored-id fast path entirely.
    forced = sum(1 for chip in original_chips if chip["label"] in resolve_chips.FORCE_RESEARCH)
    assert len(probed) == 2 * (len(original_chips) - forced) + forced

    chips = _read_json(_isolated_files["CHIPS_PATH"])
    assert len(chips) == len(original_chips)
    for chip, original in zip(chips, original_chips):
        query = resolve_chips.OVERRIDES.get(original["label"], original["label"])
        expected = _fake_hit(query)["entity_id"]
        assert chip["entity_id"] == expected
        assert chip["entity_id"] not in old_ids
        assert chip["entity_ok"] is True
        assert chip["entity_name"] == query  # fake hits are named for the query
        assert chip["tag_ids"] == [f"urn:tag:genre:{query.lower().replace(' ', '-')}"]
        assert chip["label"] == original["label"]
        assert chip["category"] == original["category"]

    taste_ts = Path(_isolated_files["TASTE_TS_PATH"]).read_text(encoding="utf-8")
    assert "FALLBACK_CHIPS" in taste_ts
    for old_id in old_ids:
        assert old_id not in taste_ts
    for chip in chips:
        assert chip["entity_id"] in taste_ts


def test_probe_failure_keeps_best_hit_as_tag_only(_isolated_files, monkeypatch, capsys) -> None:
    """When no candidate probes clean the best relevant hit stays — its tags
    steer the request even though insights will drop the entity itself."""
    monkeypatch.setenv("QLOO_API_KEY", "test-key")
    original_chips = _read_json(_isolated_files["CHIPS_PATH"])

    monkeypatch.setattr(
        resolve_chips, "qloo_search", lambda query: [_fake_hit(query, qtype="place")]
    )
    monkeypatch.setattr(resolve_chips, "signal_probe_ok", lambda entity_id: False)

    assert resolve_chips.main() == 0
    out, _ = capsys.readouterr()
    assert "TAG-ONLY" in out

    chips = _read_json(_isolated_files["CHIPS_PATH"])
    by_label = {chip["label"]: chip for chip in chips}
    asake = by_label["Asake"]
    assert asake["entity_id"] == _fake_hit("Asake")["entity_id"]
    assert asake["entity_ok"] is False
    assert asake["tag_ids"] == ["urn:tag:genre:asake"]


def test_unresolved_label_left_unchanged(_isolated_files, monkeypatch, capsys) -> None:
    monkeypatch.setenv("QLOO_API_KEY", "test-key")
    original_chips = _read_json(_isolated_files["CHIPS_PATH"])
    label_category = {c["label"]: c["category"] for c in original_chips}

    def _fake_search(query: str) -> list[dict]:
        if query == "Miles Davis":
            # An irrelevant hit name: no query token matches, so the
            # candidate list is empty and the label reports unresolved.
            return [{"name": "Thames River Walk", "entity_id": "urn:entity:place:thames-1"}]
        return [_fake_hit(query, qtype="place")]

    monkeypatch.setattr(resolve_chips, "qloo_search", _fake_search)
    old_ids = {chip["entity_id"] for chip in original_chips}
    # Stored ids probe stale, so every chip takes the search+probe path;
    # Tems's search returns only an irrelevant hit → unresolved.
    monkeypatch.setattr(
        resolve_chips, "signal_probe_ok", lambda entity_id: entity_id not in old_ids
    )

    assert resolve_chips.main() == 1
    out, _ = capsys.readouterr()
    assert "UNRESOLVED Miles Davis" in out

    chips = _read_json(_isolated_files["CHIPS_PATH"])
    by_label = {chip["label"]: chip for chip in chips}
    assert set(by_label) == {chip["label"] for chip in original_chips}
    original_ids = {chip["label"]: chip["entity_id"] for chip in original_chips}
    # Unresolved label: the entity_id is untouched — whatever it was on disk
    # (placeholder URN or previously resolved real UUID) survives verbatim.
    assert by_label["Miles Davis"]["entity_id"] == original_ids["Miles Davis"]
    assert by_label["Miles Davis"]["category"] == "Music"
    assert by_label["Asake"]["entity_id"] == _fake_hit("Asake")["entity_id"]


def test_script_source_guards() -> None:
    source = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "QLOO_API_KEY" in source
    assert "qloo_search" in source
    assert "signal_probe_ok" in source
    assert "_is_hit_relevant" in source
    assert "FALLBACK_CHIPS" in source
    assert "sk-" not in source
