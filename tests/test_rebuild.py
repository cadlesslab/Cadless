"""A rebuild of edited code judged as the pipeline judges a build's last attempt.

The geometry is scripted rather than built: what is under test is what a rebuild
asks of the worker and what it does with the verdict.
"""

import json

from cadless.assembly_check import AssemblyMeasurements
from cadless.assembly_guide import MOTION_FILE
from cadless.pipeline import rebuild
from cadless.printer_profile import AssemblySpec, BuildVolume
from cadless.worker import ExecResult

SPEC = AssemblySpec(volume=BuildVolume(210.0, 200.0, 195.0), clearance_mm=0.2)


def _sound() -> AssemblyMeasurements:
    return AssemblyMeasurements(
        part_bboxes=[[100.0, 100.0, 50.0], [100.0, 100.0, 50.0]],
        overlaps=[],
        gaps=[[0, 1, 0.2]],
        order=[0, 1],
        releases=[[0.0, 0.0, 1.0], []],
    )


def _overlapping() -> AssemblyMeasurements:
    return AssemblyMeasurements(
        part_bboxes=[[100.0, 100.0, 50.0], [100.0, 100.0, 50.0]],
        overlaps=[[0, 1, 40.0]],
        gaps=[[0, 1, 0.0]],
        order=[0, 1],
    )


def _stub_run_code(monkeypatch, result: ExecResult, captured: dict | None = None):
    def fake(
        code,
        *,
        export_dir=None,
        export_scale=1.0,
        check_assembly=False,
        assembly_turn=False,
        config=None,
    ):
        if captured is not None:
            captured.update(
                code=code,
                export_dir=export_dir,
                export_scale=export_scale,
                check_assembly=check_assembly,
                assembly_turn=assembly_turn,
            )
        return result

    monkeypatch.setattr("cadless.pipeline.run_code", fake)


def _built(measurements=None, parts=None) -> ExecResult:
    return ExecResult(
        ok=True,
        volume=2000.0,
        bbox=(20.0, 10.0, 10.0),
        part_count=2 if measurements is not None else 1,
        assembly=measurements,
        parts=parts,
    )


def test_a_rebuild_asks_the_worker_to_measure_and_to_export_where_it_is_told(monkeypatch, tmp_path):
    captured: dict = {}
    _stub_run_code(monkeypatch, _built(), captured)

    rebuild("code", export_dir=str(tmp_path))

    assert captured == {
        "code": "code",
        "export_dir": str(tmp_path),
        "export_scale": 1.0,
        "check_assembly": True,
        "assembly_turn": False,
    }


def test_a_rebuild_of_an_assembly_runs_on_the_assembly_clock(monkeypatch):
    captured: dict = {}
    _stub_run_code(monkeypatch, _built(_sound()), captured)

    rebuild("code", assembly=SPEC)

    assert captured["assembly_turn"] is True


def test_a_rebuild_asked_for_the_assembly_clock_runs_on_it_held_to_no_printer(monkeypatch):
    # What a catalog version's motion needs: the order search is the point of the
    # build, and its parts were never sized for anybody's bed.
    captured: dict = {}
    _stub_run_code(monkeypatch, _built(_sound()), captured)

    rb = rebuild("code", assembly_clock=True)

    assert captured["assembly_turn"] is True
    assert rb.assembly["checks"] == ["overlap"]


def test_a_rebuild_exports_at_the_scale_it_is_given(monkeypatch):
    captured: dict = {}
    _stub_run_code(monkeypatch, _built(), captured)

    rebuild("code", export_scale=1000.0)

    assert captured["export_scale"] == 1000.0


def test_a_rebuild_judges_its_size_in_millimetres(monkeypatch):
    # Authored in metres: 0.3 m is past a 210 mm bed, which the unscaled box
    # would read as a third of a millimetre.
    _stub_run_code(
        monkeypatch,
        ExecResult(ok=True, volume=0.001, bbox=(0.3, 0.25, 0.018), part_count=1, assembly=None),
    )

    rb = rebuild("code", assembly=SPEC, export_scale=1000.0)

    assert rb.ok is False
    assert "one part" in rb.assembly["failures"][0]


def test_an_unasked_rebuild_whose_parts_share_volume_is_kept_with_its_verdict(monkeypatch):
    _stub_run_code(monkeypatch, _built(_overlapping()))

    rb = rebuild("code")

    assert rb.ok is True
    assert rb.assembly["ok"] is False
    assert rb.assembly["checks"] == ["overlap"]
    assert any("share" in line for line in rb.assembly["failures"]), rb.assembly["failures"]


def test_a_rebuild_of_an_assembly_whose_parts_share_volume_is_refused(monkeypatch):
    _stub_run_code(monkeypatch, _built(_overlapping()))

    rb = rebuild("code", assembly=SPEC)

    assert rb.ok is False
    assert rb.error.startswith("assembly: ")
    assert rb.assembly["ok"] is False
    assert rb.assembly["checks"] == ["fit", "mating", "order", "overlap"]


def test_a_rebuild_of_one_part_carries_no_verdict(monkeypatch):
    _stub_run_code(monkeypatch, _built(None))

    rb = rebuild("code")

    assert (rb.ok, rb.assembly, rb.guide) == (True, None, None)


def test_a_rebuild_of_an_assembly_that_measured_nothing_says_so(monkeypatch):
    _stub_run_code(monkeypatch, _built(None))

    rb = rebuild("code", assembly=SPEC)

    assert rb.ok is True
    assert rb.assembly["ok"] is None
    assert rb.assembly["measured"] is False


def test_a_rebuild_of_an_assembly_into_one_part_too_big_for_the_bed_is_refused(monkeypatch):
    # A parameter edit that grows a model built as an assembly past the bed, in
    # one piece: the lone part is held to the bed as any part is.
    _stub_run_code(
        monkeypatch,
        ExecResult(ok=True, volume=2000.0, bbox=(300.0, 250.0, 18.0), part_count=1, assembly=None),
    )

    rb = rebuild("code", assembly=SPEC)

    assert rb.ok is False
    assert "one part" in rb.assembly["failures"][0]


def test_a_build_that_failed_is_reported_as_it_failed(monkeypatch):
    _stub_run_code(monkeypatch, ExecResult(ok=False, error="NameError: x"))

    rb = rebuild("code", assembly=SPEC)

    assert (rb.ok, rb.error, rb.assembly, rb.guide) == (False, "NameError: x", None, None)


def test_an_accepted_rebuild_writes_its_motion_and_its_guide(monkeypatch, tmp_path):
    parts = [
        {"index": 0, "name": "base", "path": [], "color": None},
        {"index": 1, "name": "lid", "path": [], "color": None},
    ]
    _stub_run_code(monkeypatch, _built(_sound(), parts))

    rb = rebuild("code", export_dir=str(tmp_path))

    assert rb.ok is True
    assert json.loads((tmp_path / MOTION_FILE).read_text())["order"] == [0, 1]
    assert rb.guide["parts"] == ["base", "lid"]
    assert rb.guide["steps"]


def test_a_rebuild_told_not_to_describe_writes_its_motion_alone(monkeypatch, tmp_path):
    _stub_run_code(monkeypatch, _built(_sound()))

    rb = rebuild("code", export_dir=str(tmp_path), describe=False)

    assert rb.ok is True
    assert (tmp_path / MOTION_FILE).exists()
    assert rb.guide is None
    assert not list(tmp_path.glob("guide_f*.png"))


def test_a_refused_rebuild_leaves_no_motion_from_an_earlier_build(monkeypatch, tmp_path):
    (tmp_path / MOTION_FILE).write_text('{"order": [1, 0]}', encoding="utf-8")
    _stub_run_code(monkeypatch, _built(_overlapping()))

    rb = rebuild("code", export_dir=str(tmp_path), assembly=SPEC)

    assert rb.ok is False
    assert not (tmp_path / MOTION_FILE).exists()
    assert rb.guide is None
