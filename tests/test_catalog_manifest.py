"""Manifest parser tests (catalog Phase 1). Pure; tmp_path fixtures."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cadless.catalog.manifest import discover_houses, load_manifest, thumbnail_path


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


def _write_item(item: Path, manifest: dict) -> None:
    (item / "steps").mkdir(parents=True, exist_ok=True)
    (item / "steps" / "01.py").write_text("result = 1\n")
    (item / "manifest.json").write_text(json.dumps(manifest))


def _step(code: str = "steps/01.py", **extra) -> dict:
    return {"index": 1, "instruction": "s", "code": code, **extra}


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


def test_code_outside_the_item_directory_is_refused(tmp_path):
    """A step whose code sits beside the item rather than inside it."""
    item = tmp_path / "item"
    item.mkdir()
    (tmp_path / "outside.py").write_text("result = 'outside'\n")
    _write_item(item, {"id": "h", "name": "House", "steps": [_step("../outside.py")]})
    with pytest.raises(ValueError, match="inside the item directory"):
        load_manifest(item)


def test_absolute_code_path_is_refused(tmp_path):
    item = tmp_path / "item"
    item.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("result = 'outside'\n")
    _write_item(item, {"id": "h", "name": "House", "steps": [_step(str(outside))]})
    with pytest.raises(ValueError, match="inside the item directory"):
        load_manifest(item)


def test_artifact_path_escaping_the_item_is_refused(tmp_path):
    """Artifacts are copied into the store and served, so they are checked too."""
    item = tmp_path / "item"
    item.mkdir()
    step = _step(artifacts={"step": "../../secrets.step"})
    _write_item(item, {"id": "h", "name": "House", "steps": [step]})
    with pytest.raises(ValueError, match="inside the item directory"):
        load_manifest(item)


def test_thumbnail_escaping_the_item_is_refused(tmp_path):
    item = tmp_path / "item"
    item.mkdir()
    manifest = {"id": "h", "name": "House", "thumbnail": "../thumb.png", "steps": [_step()]}
    _write_item(item, manifest)
    with pytest.raises(ValueError, match="inside the item directory"):
        load_manifest(item)


def test_symlinked_step_file_leaving_the_item_is_refused(tmp_path):
    """A relative path that leaves the item through a link is the same escape."""
    item = tmp_path / "item"
    (item / "steps").mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("result = 'outside'\n")
    (item / "steps" / "01.py").symlink_to(outside)
    (item / "manifest.json").write_text(
        json.dumps({"id": "h", "name": "House", "steps": [_step()]})
    )
    with pytest.raises(ValueError, match="inside the item directory"):
        load_manifest(item)


def test_symlinked_item_directory_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    _write_item(real, {"id": "h", "name": "House", "steps": [_step()]})
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        load_manifest(link)
    assert load_manifest(real).id == "h"


def test_paths_inside_the_item_still_load_and_artifacts_may_be_missing(tmp_path):
    """Containment is the only new check: baked and half-baked items load as before."""
    item = tmp_path / "item"
    item.mkdir()
    (item / "artifacts" / "01").mkdir(parents=True)
    (item / "artifacts" / "01" / "model.step").write_text("ISO-STEP")
    step = _step(artifacts={"step": "artifacts/01/model.step", "glb": "artifacts/01/model.glb"})
    manifest = {"id": "h", "name": "House", "thumbnail": "artifacts/thumbnail.png", "steps": [step]}
    _write_item(item, manifest)
    loaded = load_manifest(item)
    assert loaded.steps[0].artifacts["glb"] == "artifacts/01/model.glb"
    assert loaded.thumbnail == "artifacts/thumbnail.png"


@pytest.mark.parametrize("value", ["", ".", "steps\\01.py", "steps/01.py\n"])
def test_spellings_a_package_entry_could_not_carry_are_refused(tmp_path, value):
    """The directory rule shares the package format's spelling rule."""
    item = tmp_path / "item"
    item.mkdir()
    _write_item(item, {"id": "h", "name": "House", "steps": [_step(value)]})
    with pytest.raises(ValueError, match="inside the item directory"):
        load_manifest(item)


def test_missing_artifact_under_a_link_out_is_still_refused(tmp_path):
    """Resolving a path that does not exist still follows the link it goes through."""
    item = tmp_path / "item"
    item.mkdir()
    (item / "esc").symlink_to(tmp_path, target_is_directory=True)
    step = _step(artifacts={"step": "esc/nope.step"})
    _write_item(item, {"id": "h", "name": "House", "steps": [step]})
    with pytest.raises(ValueError, match="inside the item directory"):
        load_manifest(item)


def test_thumbnail_path_needs_a_thumbnail(tmp_path):
    item = tmp_path / "item"
    item.mkdir()
    _write_item(item, {"id": "h", "name": "House", "steps": [_step()]})
    with pytest.raises(ValueError, match="no thumbnail"):
        thumbnail_path(item, load_manifest(item))


def _item_with_keys(tmp_path: Path, keys: list[str]) -> Path:
    item = tmp_path / "item"
    item.mkdir()
    step = _step(artifacts={key: f"artifacts/{n}.bin" for n, key in enumerate(keys)})
    _write_item(item, {"id": "h", "name": "House", "steps": [step]})
    return item


@pytest.mark.parametrize(
    "keys",
    [
        ["glb", "glb:"],
        ["glb", "glb:01"],
        ["glb", "glb:-1"],
        ["glb", "glb:x"],
        ["glb", "glb:1:2"],
        ["glb", "glb:0"],
        [":1"],
        ["glb", "a/b:1"],
    ],
)
def test_a_numbered_key_that_is_not_a_kind_and_a_part_from_one_is_refused(tmp_path, keys):
    """A numbered key says which part a file is, so it has one spelling per part.

    The first part is the bare kind, which is why `glb:0` is refused rather
    than read as a second name for it.
    """
    with pytest.raises(ValueError, match="is not <kind> or <kind>:<part number from 1>"):
        load_manifest(_item_with_keys(tmp_path, keys))


@pytest.mark.parametrize("key", ["a/b", "..", "x" * 33, "a b", ""])
def test_a_key_whose_kind_is_not_a_file_extension_is_refused(tmp_path, key):
    """The kind becomes the extension of the copy the loader writes.

    So it is held to that shape for a manifest under any catalog root, not
    only for one that arrived in a package.
    """
    with pytest.raises(ValueError, match="not a file extension"):
        load_manifest(_item_with_keys(tmp_path, [key]))


@pytest.mark.parametrize("keys", [["glb", "glb:2"], ["glb:1"]])
def test_a_kind_whose_part_numbers_skip_one_is_refused(tmp_path, keys):
    """The store files parts in the order they arrive, so a gap cannot be kept."""
    with pytest.raises(ValueError, match="with one missing below the last"):
        load_manifest(_item_with_keys(tmp_path, keys))


def test_numbered_keys_group_a_kind_s_parts_in_part_order(tmp_path):
    item = tmp_path / "item"
    item.mkdir()
    step = _step(artifacts={"glb:1": "a/1.glb", "stl": "a/0.stl", "glb": "a/0.glb"})
    _write_item(item, {"id": "h", "name": "House", "steps": [step]})
    assert load_manifest(item).steps[0].artifact_parts() == {
        "glb": ["glb", "glb:1"],
        "stl": ["stl"],
    }


# --- part names and whole-assembly files ------------------------------------

_TWO_PARTS = {
    "stl": "artifacts/01/model_p0.stl",
    "stl:1": "artifacts/01/model_p1.stl",
}


def test_a_step_may_name_its_parts(tmp_path):
    item = tmp_path / "item"
    _write_item(
        item,
        {"id": "h", "name": "H", "steps": [_step(artifacts=_TWO_PARTS, parts=["base", "lid"])]},
    )
    assert load_manifest(item).steps[0].parts == ["base", "lid"]


def test_a_step_that_names_no_parts_loads_as_before(tmp_path):
    item = tmp_path / "item"
    _write_item(item, {"id": "h", "name": "H", "steps": [_step(artifacts=_TWO_PARTS)]})
    assert load_manifest(item).steps[0].parts is None


@pytest.mark.parametrize(
    "parts",
    [
        ["base"],
        ["base", "lid", "spare"],
        ["base", ""],
        ["base", "   "],
        ["base", "x" * 129],
        ["base", "\ud800"],
        ["base", "a\u202eb"],
        ["base", "a\nb"],
        ["base", "a\U000e0041b"],
        ["base", "\u200b\u200d"],
    ],
    ids=[
        "too-few",
        "too-many",
        "empty",
        "blank",
        "too-long",
        "lone-surrogate",
        "bidi-control",
        "line-break",
        "tag-character",
        "nothing-visible",
    ],
)
def test_part_names_that_do_not_name_the_step_s_parts_are_refused(tmp_path, parts):
    item = tmp_path / "item"
    _write_item(item, {"id": "h", "name": "H", "steps": [_step(artifacts=_TWO_PARTS, parts=parts)]})
    with pytest.raises(ValueError):
        load_manifest(item)


def test_a_whole_assembly_file_is_keyed_once(tmp_path):
    item = tmp_path / "item"
    artifacts = {
        **_TWO_PARTS,
        "assembly_glb": "artifacts/01/assembly.glb",
        "assembly_step": "artifacts/01/assembly.step",
    }
    _write_item(item, {"id": "h", "name": "H", "steps": [_step(artifacts=artifacts)]})
    assert load_manifest(item).steps[0].artifacts["assembly_glb"] == "artifacts/01/assembly.glb"


def test_a_second_whole_assembly_file_of_a_kind_is_refused(tmp_path):
    item = tmp_path / "item"
    artifacts = {
        **_TWO_PARTS,
        "assembly_glb": "artifacts/01/assembly.glb",
        "assembly_glb:1": "artifacts/01/assembly2.glb",
    }
    _write_item(item, {"id": "h", "name": "H", "steps": [_step(artifacts=artifacts)]})
    with pytest.raises(ValueError):
        load_manifest(item)


def test_a_name_of_the_longest_length_is_kept(tmp_path):
    item = tmp_path / "item"
    names = ["base", "x" * 128]
    _write_item(item, {"id": "h", "name": "H", "steps": [_step(artifacts=_TWO_PARTS, parts=names)]})
    assert load_manifest(item).steps[0].parts == names


def test_parts_are_counted_by_the_part_files_of_the_most_files(tmp_path):
    """Two stl parts and one step file are two parts, as the API numbers them."""
    item = tmp_path / "item"
    artifacts = {**_TWO_PARTS, "step": "artifacts/01/model_p0.step"}
    _write_item(
        item, {"id": "h", "name": "H", "steps": [_step(artifacts=artifacts, parts=["a", "b"])]}
    )
    assert load_manifest(item).steps[0].parts == ["a", "b"]


def test_files_that_are_not_part_formats_do_not_count_as_parts(tmp_path):
    item = tmp_path / "item"
    artifacts = {
        **_TWO_PARTS,
        "guide": "artifacts/01/guide_f0.png",
        "guide:1": "artifacts/01/guide_f1.png",
        "guide:2": "artifacts/01/guide_f2.png",
    }
    _write_item(
        item, {"id": "h", "name": "H", "steps": [_step(artifacts=artifacts, parts=["a", "b"])]}
    )
    assert load_manifest(item).steps[0].parts == ["a", "b"]


@pytest.mark.parametrize(
    "artifacts",
    [{}, {"assembly_glb": "artifacts/01/assembly.glb"}],
    ids=["no-files", "whole-assembly-only"],
)
def test_names_for_a_step_with_no_part_files_are_refused(tmp_path, artifacts):
    item = tmp_path / "item"
    _write_item(item, {"id": "h", "name": "H", "steps": [_step(artifacts=artifacts, parts=["a"])]})
    with pytest.raises(ValueError):
        load_manifest(item)


def test_names_written_with_joiners_and_marks_are_kept(tmp_path):
    """Persian spelled with a ZWNJ, an emoji joined by ZWJs, Hebrew with an RLM."""
    item = tmp_path / "item"
    names = [
        "\u06a9\u062a\u0627\u0628\u200c\u062e\u0627\u0646\u0647",
        "\U0001f468\u200d\U0001f469\u200d\U0001f467",
    ]
    _write_item(item, {"id": "h", "name": "H", "steps": [_step(artifacts=_TWO_PARTS, parts=names)]})
    assert load_manifest(item).steps[0].parts == names

    names = ["\u05d2\u05dc\u05d2\u05dc\u200f", "lid"]
    _write_item(item, {"id": "h", "name": "H", "steps": [_step(artifacts=_TWO_PARTS, parts=names)]})
    assert load_manifest(item).steps[0].parts == names


def test_names_for_more_parts_than_a_version_keeps_are_refused(tmp_path):
    item = tmp_path / "item"
    count = 2001
    artifacts = {
        ("stl" if i == 0 else f"stl:{i}"): f"artifacts/01/model_p{i}.stl" for i in range(count)
    }
    names = [f"p{i}" for i in range(count)]
    _write_item(item, {"id": "h", "name": "H", "steps": [_step(artifacts=artifacts, parts=names)]})
    with pytest.raises(ValueError, match="2000"):
        load_manifest(item)


def test_reading_a_manifest_loads_no_engine_configuration():
    """A manifest is read where the engine is not configured to run."""
    root = Path(__file__).resolve().parents[1]
    probe = (
        f"import sys; sys.path.insert(0, {str(root)!r}); "
        "import cadless.catalog.manifest; "
        "assert cadless.catalog.manifest.__file__.startswith(sys.path[0]); "
        "print('cadless.config' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_a_version_s_metrics_written_back_into_an_item_are_what_the_item_recorded():
    """An item's metrics are filed on a version in millimetres; whatever writes a
    version's back into an item writes them in the item's own units again."""
    from cadless.catalog.manifest import StepGeometry

    # The demo house's final step, rounded to the decimals it was authored with.
    recorded = StepGeometry(volume=158.058, bbox=(12.4, 8.4, 6.9))

    filed = recorded.in_millimetres(1000.0)
    written_back = filed.in_authoring_units(1000.0)

    assert filed.bbox == pytest.approx((12400.0, 8400.0, 6900.0))
    assert written_back.volume == 158.058
    assert written_back.bbox == (12.4, 8.4, 6.9)  # the decimals it was authored with
