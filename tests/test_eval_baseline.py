"""The eval baseline summariser. Reads reports off disk; nothing is generated."""

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "eval_baseline", REPO_ROOT / "tools" / "eval_baseline.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def catalog(tmp_path):
    item = tmp_path / "mech-catalog" / "widget"
    item.mkdir(parents=True)
    (item / "manifest.json").write_text(
        json.dumps(
            {
                "id": "widget",
                "steps": [
                    {"index": 1, "geometry": {"volume": 100.0}},
                    {"index": 2, "geometry": {"volume": 250.0}},
                ],
            }
        )
    )
    return tmp_path


def test_an_easy_id_resolves_to_step_one_and_a_hard_id_to_the_last(catalog):
    # The suffix is the whole difference: an easy prompt asks for step 1 alone,
    # a hard prompt asks for the finished part. Comparing a hard result against
    # step 1 would score every complex item as wildly oversized.
    baseline = _load()

    assert baseline.truth_volume("mech-catalog/widget#1", catalog) == 100.0
    assert baseline.truth_volume("mech-catalog/widget", catalog) == 250.0


def test_an_unknown_item_yields_no_ground_truth_rather_than_raising(catalog):
    # Reports outlive the catalog they were measured against, so a renamed or
    # removed item must drop out of the comparison, not crash the summary.
    assert _load().truth_volume("mech-catalog/ghost", catalog) is None


def _report(path, records, **metrics):
    body = {
        "total": len(records),
        "success_rate": 1.0,
        "first_try_rate": 1.0,
        "repair_lift": 0.0,
        "avg_attempts": 1.0,
        "degenerate_count": 0,
        "records": records,
    }
    body.update(metrics)
    path.write_text(json.dumps(body))


def test_it_separates_wrong_size_from_did_not_build(tmp_path, catalog, capsys):
    # The distinction the built-in metrics cannot make: a solid that executes
    # but is the wrong size counts as a success everywhere else.
    baseline = _load()
    runs = tmp_path / "runs"
    runs.mkdir()
    records = [
        {"id": "mech-catalog/widget", "ok": True, "volume": 250.0},  # exact
        {"id": "mech-catalog/widget#1", "ok": True, "volume": 180.0},  # x1.8, wrong
        {"id": "mech-catalog/widget", "ok": False, "volume": None},  # never built
    ]
    _report(runs / "hard-pass1.json", records, success_rate=0.667)

    baseline.summarise("hard", runs, catalog)

    out = capsys.readouterr().out
    assert "within tolerance : [1]" in out
    assert "wrong size       : [1]" in out
    assert "did not build    : [1]" in out


def test_an_item_wrong_in_every_pass_is_called_out(tmp_path, catalog, capsys):
    # A consistently wrong item is a stable target; a sometimes-wrong one is
    # noise. Only the former is worth pointing a quality feature at.
    baseline = _load()
    runs = tmp_path / "runs"
    runs.mkdir()
    for i in (1, 2):
        _report(
            runs / f"hard-pass{i}.json",
            [{"id": "mech-catalog/widget", "ok": True, "volume": 500.0}],
        )

    baseline.summarise("hard", runs, catalog)

    out = capsys.readouterr().out
    assert "wrong size in all 2 passes" in out
    assert "mech-catalog/widget" in out


def test_no_reports_says_so_instead_of_dividing_by_zero(tmp_path, catalog, capsys):
    _load().summarise("hard", tmp_path, catalog)
    assert "no reports" in capsys.readouterr().out
