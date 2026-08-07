"""Benchmark tier loading. No provider calls — the tiers are static repo data."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

from cadless.evalkit import available_tiers, load_tier
from cadless.evalkit.harness import _BENCHMARK_PATH, TIERS_DIR


def _write_tier(directory: Path, name: str, rows: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r) + "\n" for r in rows)
    (directory / f"{name}.jsonl").write_text(body)


def test_tiers_dir_is_anchored_on_the_package_not_the_cwd(tmp_path, monkeypatch):
    # _BENCHMARK_PATH resolves against settings.catalog_root, which defaults to a
    # relative "./catalog" — so it moves when the process starts elsewhere. The
    # tier directory must not inherit that, or a tier would resolve differently
    # depending on where pytest was invoked from.
    before = TIERS_DIR
    monkeypatch.chdir(tmp_path)
    assert TIERS_DIR == before
    assert TIERS_DIR.is_absolute()
    assert TIERS_DIR.parent == Path(__file__).resolve().parents[1] / "cadless" / "evalkit"


def test_load_tier_parses_a_tier_file(tmp_path, monkeypatch):
    monkeypatch.setattr("cadless.evalkit.harness.TIERS_DIR", tmp_path)
    _write_tier(tmp_path, "sample", [{"id": "a", "prompt": "a 10 mm cube"}])

    prompts = load_tier("sample")

    assert [(p.id, p.prompt) for p in prompts] == [("a", "a 10 mm cube")]


def test_available_tiers_lists_stems_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr("cadless.evalkit.harness.TIERS_DIR", tmp_path)
    _write_tier(tmp_path, "hard", [{"id": "h", "prompt": "p"}])
    _write_tier(tmp_path, "easy", [{"id": "e", "prompt": "p"}])

    assert available_tiers() == ["easy", "hard"]


def test_unknown_tier_names_the_available_ones(tmp_path, monkeypatch):
    monkeypatch.setattr("cadless.evalkit.harness.TIERS_DIR", tmp_path)
    _write_tier(tmp_path, "easy", [{"id": "e", "prompt": "p"}])

    with pytest.raises(ValueError) as exc:
        load_tier("nope")

    # A bare FileNotFoundError would leave the caller guessing at the spelling.
    assert "nope" in str(exc.value)
    assert "easy" in str(exc.value)


def test_the_server_side_benchmark_is_still_not_bundled():
    # Precondition, stated honestly: the four skip-guarded tests in
    # test_evalkit.py wake up in CI the moment _BENCHMARK_PATH resolves, and
    # nothing in this repo should put a file there. Only meaningful when the
    # catalog root is the default one -- an operator pointing it at real content
    # is a different situation, not a failure.
    if os.getenv("CADLESS_CATALOG_ROOT"):
        pytest.skip("catalog root overridden; the bundled-content claim does not apply")
    assert not _BENCHMARK_PATH.exists()


def test_load_tier_never_reads_the_server_side_path(monkeypatch):
    # The behavioural half: point _BENCHMARK_PATH at something that explodes on
    # access and confirm a tier still loads. Guards against a future refactor
    # routing load_tier through the catalog root.
    class Exploding:
        def __getattr__(self, name):
            raise AssertionError("load_tier must not touch _BENCHMARK_PATH")

    monkeypatch.setattr("cadless.evalkit.harness._BENCHMARK_PATH", Exploding())

    assert load_tier("easy")


def test_available_tiers_ignores_a_directory_that_looks_like_a_tier(tmp_path, monkeypatch):
    # Without an is_file() filter this listed the directory and then load_tier
    # rejected it, producing "unknown tier 'x'; available: ['x']".
    monkeypatch.setattr("cadless.evalkit.harness.TIERS_DIR", tmp_path)
    _write_tier(tmp_path, "real", [{"id": "r", "prompt": "p"}])
    (tmp_path / "decoy.jsonl").mkdir()

    assert available_tiers() == ["real"]


def test_a_malformed_tier_line_says_which_file_and_line(tmp_path, monkeypatch):
    monkeypatch.setattr("cadless.evalkit.harness.TIERS_DIR", tmp_path)
    (tmp_path / "broken.jsonl").write_text('{"id": "a", "prompt": "ok"}\n{"id": "b"\n')

    with pytest.raises(ValueError) as exc:
        load_tier("broken")

    assert "broken.jsonl:2" in str(exc.value)


def test_a_tier_line_missing_prompt_is_a_value_error_not_a_key_error(tmp_path, monkeypatch):
    # A KeyError escaping load_tier reaches the CLI as a raw traceback, because
    # only ValueError is caught there.
    monkeypatch.setattr("cadless.evalkit.harness.TIERS_DIR", tmp_path)
    (tmp_path / "nokey.jsonl").write_text('{"id": "a"}\n')

    with pytest.raises(ValueError):
        load_tier("nokey")


# --- the shipped tiers -------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = REPO_ROOT / "catalog"


def _manifest(qualified_id: str) -> dict:
    catalog, item = qualified_id.split("/", 1)
    return json.loads((CATALOG_ROOT / catalog / item / "manifest.json").read_text())


def test_both_tiers_ship_and_parse():
    assert available_tiers() == ["easy", "hard"]
    assert len(load_tier("easy")) == 39
    assert len(load_tier("hard")) == 18


def test_every_easy_prompt_is_an_items_step_one_instruction():
    for prompt in load_tier("easy"):
        assert prompt.id.endswith("#1")
        manifest = _manifest(prompt.id[: -len("#1")])
        first = next(s for s in manifest["steps"] if s["index"] == 1)
        assert prompt.prompt == first["instruction"]


def test_every_hard_prompt_is_an_items_own_description():
    for prompt in load_tier("hard"):
        assert prompt.prompt == _manifest(prompt.id)["description"]


def test_the_two_tiers_differ_in_what_the_prompt_gives_away():
    # The point of the split: an easy prompt states the dimensions of one
    # feature, a hard prompt names a whole part and leaves them to be inferred.
    # So every hard id must also appear in easy -- same object, different ask.
    easy_items = {p.id.removesuffix("#1") for p in load_tier("easy")}
    hard_items = {p.id for p in load_tier("hard")}
    assert hard_items < easy_items


def test_tier_ids_are_unique_and_traceable_to_a_catalog_item():
    for tier in ("easy", "hard"):
        prompts = load_tier(tier)
        ids = [p.id for p in prompts]
        assert len(set(ids)) == len(ids)
        for p in prompts:
            assert p.prompt.strip()
            _manifest(p.id.removesuffix("#1"))  # raises if the item is gone


def test_tiers_are_pure_ascii():
    # load_benchmark reads with read_text() and no explicit encoding, so a
    # non-ASCII byte would make the tier depend on the reader's locale.
    for tier in ("easy", "hard"):
        assert (TIERS_DIR / f"{tier}.jsonl").read_text().isascii()


def test_committed_tiers_match_what_the_catalog_regenerates():
    # A tier nobody can regenerate cannot be audited when a score moves.
    build_eval_tiers = _load_builder()
    assert build_eval_tiers.main(["--check"]) == 0


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_eval_tiers", REPO_ROOT / "tools" / "build_eval_tiers.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_builder_follows_the_manifests_code_path(tmp_path, monkeypatch):
    # The manifest's `code` field is a path, not source. Following it rather than
    # reconstructing "steps/NN.py" is what keeps the line count right for an item
    # that names its scripts differently.
    builder = _load_builder()
    item = tmp_path / "cat" / "widget"
    (item / "src").mkdir(parents=True)
    (item / "src" / "one.py").write_text("a = 1\n" * 200)
    (item / "manifest.json").write_text(
        json.dumps(
            {
                "id": "widget",
                "description": "A widget.",
                "steps": [{"index": 1, "instruction": "make it", "code": "src/one.py"}],
            }
        )
    )
    monkeypatch.setattr(builder, "CATALOG_ROOT", tmp_path)

    easy, hard, skipped = builder.collect()

    assert [p["id"] for p in hard] == ["cat/widget"]  # 200 lines, over the threshold
    assert [p["id"] for p in easy] == ["cat/widget#1"]
    assert skipped == []


def test_a_missing_step_script_is_reported_not_silently_counted_as_zero(tmp_path, monkeypatch):
    # The failure this guards: a vanished script drags the line count under the
    # threshold and the item drops out of the hard tier without a word. --check
    # cannot catch it, because it compares using the same shortened count.
    builder = _load_builder()
    item = tmp_path / "cat" / "widget"
    item.mkdir(parents=True)
    (item / "manifest.json").write_text(
        json.dumps(
            {
                "id": "widget",
                "description": "A widget.",
                "steps": [{"index": 1, "instruction": "make it", "code": "steps/01.py"}],
            }
        )
    )
    monkeypatch.setattr(builder, "CATALOG_ROOT", tmp_path)

    _easy, hard, skipped = builder.collect()

    assert hard == []  # it did fall out of the tier ...
    assert any("missing" in note for note in skipped)  # ... but it said so
