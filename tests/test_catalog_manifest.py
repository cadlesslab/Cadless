"""Manifest parser tests (catalog Phase 1). Pure; tmp_path fixtures."""

import json
from pathlib import Path

import pytest

from cadless.catalog.manifest import discover_houses, load_manifest


def _write_house(house_dir: Path, indices: list[int]) -> None:
    (house_dir / "steps").mkdir(parents=True, exist_ok=True)
    steps = []
    for i in indices:
        (house_dir / "steps" / f"{i:02d}.py").write_text(f"result = {i}\n")
        steps.append(
            {
                "index": i,
                "instruction": f"step {i}",
                "code": f"steps/{i:02d}.py",
            }
        )
    manifest = {"id": "h", "name": "House", "steps": steps}
    (house_dir / "manifest.json").write_text(json.dumps(manifest))


def test_load_valid_manifest_sorts_steps(tmp_path):
    _write_house(tmp_path, [2, 1])  # out of order on disk
    manifest = load_manifest(tmp_path)
    assert [s.index for s in manifest.steps] == [1, 2]
    assert manifest.name == "House"


def test_non_contiguous_indices_raise(tmp_path):
    _write_house(tmp_path, [1, 3])
    with pytest.raises(ValueError):
        load_manifest(tmp_path)


def test_missing_code_file_raises(tmp_path):
    _write_house(tmp_path, [1])
    (tmp_path / "steps" / "01.py").unlink()
    with pytest.raises(ValueError):
        load_manifest(tmp_path)


def test_missing_manifest_raises(tmp_path):
    with pytest.raises(ValueError):
        load_manifest(tmp_path)


def test_discovery_metadata_fields_round_trip(tmp_path):
    """category, tags, description, and thumbnail parse when present (#21)."""
    (tmp_path / "steps").mkdir(parents=True)
    (tmp_path / "steps" / "01.py").write_text("result = 1\n")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "id": "h",
                "name": "House",
                "category": "bungalow",
                "tags": ["two-storey", "garage"],
                "description": "A cosy two-storey with attached garage.",
                "thumbnail": "artifacts/thumbnail.png",
                "steps": [{"index": 1, "instruction": "s", "code": "steps/01.py"}],
            }
        )
    )
    m = load_manifest(tmp_path)
    assert m.category == "bungalow"
    assert m.tags == ["two-storey", "garage"]
    assert m.description == "A cosy two-storey with attached garage."
    assert m.thumbnail == "artifacts/thumbnail.png"


def test_discovery_metadata_defaults_for_legacy_manifests(tmp_path):
    """Existing manifests (no metadata keys) stay valid — fields default."""
    _write_house(tmp_path, [1])
    m = load_manifest(tmp_path)
    assert m.category is None
    assert m.tags == []
    assert m.description is None
    assert m.thumbnail is None


def test_new_schema_fields_round_trip(tmp_path):
    """slug, verified, and per-step transcript survive load."""
    (tmp_path / "steps").mkdir(parents=True)
    (tmp_path / "steps" / "01.py").write_text("result = 1\n")
    manifest = {
        "id": "h",
        "name": "Corner Bracket",
        "slug": "corner-bracket",
        "verified": True,
        "steps": [
            {
                "index": 1,
                "instruction": "step 1",
                "code": "steps/01.py",
                "transcript": {
                    "user_prompt": "Make an L-shaped corner bracket.",
                    "assistant_message": "Here is the bracket sketched and extruded.",
                },
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    loaded = load_manifest(tmp_path)
    assert loaded.slug == "corner-bracket"
    assert loaded.verified is True
    assert loaded.steps[0].transcript.user_prompt == "Make an L-shaped corner bracket."
    assert loaded.steps[0].transcript.assistant_message.startswith("Here is the bracket")


def test_legacy_manifest_defaults_new_fields(tmp_path):
    """A manifest with no slug/verified/transcript loads with those absent."""
    _write_house(tmp_path, [1])
    loaded = load_manifest(tmp_path)
    assert loaded.slug is None
    assert loaded.verified is None
    assert loaded.steps[0].transcript is None
    assert loaded.content_version is None


def test_discover_houses(tmp_path):
    _write_house(tmp_path / "house-a", [1])
    _write_house(tmp_path / "house-b", [1])
    (tmp_path / "not-a-house").mkdir()  # no manifest.json
    assert discover_houses(tmp_path) == ["house-a", "house-b"]
    assert discover_houses(tmp_path / "missing") == []
