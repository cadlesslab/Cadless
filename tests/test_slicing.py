"""The slicing step: finding the binary, calling it, and reading what it said.

The slicer itself is not installed in CI, so the subprocess is replaced. What is
worth pinning is the argument vector (a profile value silently dropped changes
what gets printed) and the failure classification (a missing binary and a
refused model want different answers from the reader).
"""

from __future__ import annotations

import subprocess

import pytest

from cadless import slicing


@pytest.fixture
def mesh(tmp_path):
    path = tmp_path / "model.stl"
    path.write_bytes(b"solid x\nendsolid x\n")
    return str(path)


class TestFindingTheBinary:
    def test_none_when_the_path_has_no_slicer(self, monkeypatch):
        monkeypatch.setattr(slicing.shutil, "which", lambda _name: None)
        assert slicing.find_slicer() is None

    def test_the_first_name_that_resolves_wins(self, monkeypatch):
        monkeypatch.setattr(
            slicing.shutil,
            "which",
            lambda name: "/usr/bin/prusa-slicer" if name == "prusa-slicer" else None,
        )
        assert slicing.find_slicer() == "/usr/bin/prusa-slicer"

    def test_an_upstream_build_under_another_name_is_found(self, monkeypatch):
        monkeypatch.setattr(
            slicing.shutil,
            "which",
            lambda name: "/opt/PrusaSlicer" if name == "PrusaSlicer" else None,
        )
        assert slicing.find_slicer() == "/opt/PrusaSlicer"


class TestTheCommand:
    def test_every_profile_value_is_passed(self):
        argv = slicing.build_command("/s", "in.stl", "out.gcode", slicing.DEFAULT_PROFILE)
        for key, value in slicing.DEFAULT_PROFILE.items():
            assert f"--{key}" in argv
            assert argv[argv.index(f"--{key}") + 1] == value

    def test_the_mesh_is_the_final_positional(self):
        argv = slicing.build_command("/s", "in.stl", "out.gcode", {"layer-height": "0.2"})
        assert argv[-1] == "in.stl"
        assert argv[0] == "/s"

    def test_the_output_path_is_named(self):
        argv = slicing.build_command("/s", "in.stl", "out.gcode", {})
        assert argv[argv.index("--output") + 1] == "out.gcode"

    def test_the_profile_covers_what_a_print_needs(self):
        """A missing one of these makes the slicer fall back to its own default,
        which would make output depend on the installed build."""
        for key in ("layer-height", "nozzle-diameter", "filament-diameter", "bed-shape"):
            assert key in slicing.DEFAULT_PROFILE


class TestSlicing:
    def test_a_missing_binary_is_its_own_outcome(self, monkeypatch, mesh, tmp_path):
        monkeypatch.setattr(slicing, "find_slicer", lambda: None)
        outcome = slicing.slice_mesh(mesh, str(tmp_path / "out.gcode"))
        assert not outcome.ok
        assert outcome.missing is True
        assert outcome.detail == slicing.INSTALL_HINT

    def test_a_missing_mesh_is_not_reported_as_a_missing_slicer(self, monkeypatch, tmp_path):
        monkeypatch.setattr(slicing, "find_slicer", lambda: "/usr/bin/prusa-slicer")
        outcome = slicing.slice_mesh(str(tmp_path / "gone.stl"), str(tmp_path / "o.gcode"))
        assert not outcome.ok
        assert outcome.missing is False

    def test_the_slicers_own_words_reach_the_reader(self, monkeypatch, mesh, tmp_path):
        monkeypatch.setattr(slicing, "find_slicer", lambda: "/usr/bin/prusa-slicer")
        monkeypatch.setattr(
            slicing.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(
                a[0], 1, "", "Object too tall for the print volume"
            ),
        )
        outcome = slicing.slice_mesh(mesh, str(tmp_path / "out.gcode"))
        assert not outcome.ok
        assert "too tall" in outcome.detail

    def test_success_without_a_file_is_still_a_failure(self, monkeypatch, mesh, tmp_path):
        """A zero exit with nothing written must not read as a print-ready job."""
        monkeypatch.setattr(slicing, "find_slicer", lambda: "/usr/bin/prusa-slicer")
        monkeypatch.setattr(
            slicing.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""),
        )
        outcome = slicing.slice_mesh(mesh, str(tmp_path / "never-written.gcode"))
        assert not outcome.ok
        assert "wrote no G-code" in outcome.detail

    def test_a_timeout_is_reported_rather_than_raised(self, monkeypatch, mesh, tmp_path):
        monkeypatch.setattr(slicing, "find_slicer", lambda: "/usr/bin/prusa-slicer")

        def boom(*_a, **_k):
            raise subprocess.TimeoutExpired("prusa-slicer", 1)

        monkeypatch.setattr(slicing.subprocess, "run", boom)
        outcome = slicing.slice_mesh(mesh, str(tmp_path / "out.gcode"), timeout=1)
        assert not outcome.ok
        assert "did not finish" in outcome.detail

    def test_a_sliced_job_reports_its_stats(self, monkeypatch, mesh, tmp_path):
        out = tmp_path / "out.gcode"
        monkeypatch.setattr(slicing, "find_slicer", lambda: "/usr/bin/prusa-slicer")

        def write(*a, **_k):
            out.write_text(
                "G28\n"
                "; estimated printing time (normal mode) = 1h 2m 3s\n"
                "; filament used [g] = 12.34\n"
            )
            return subprocess.CompletedProcess(a[0], 0, "", "")

        monkeypatch.setattr(slicing.subprocess, "run", write)
        outcome = slicing.slice_mesh(mesh, str(out))
        assert outcome.ok, outcome.detail
        assert outcome.stats["estimated_time"] == "1h 2m 3s"
        assert outcome.stats["filament_grams"] == "12.34"

    def test_the_command_never_goes_through_a_shell(self, monkeypatch, mesh, tmp_path):
        """A mesh path is a filename from disk; running it through a shell would
        make its contents executable."""
        seen = {}
        monkeypatch.setattr(slicing, "find_slicer", lambda: "/usr/bin/prusa-slicer")

        def capture(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return subprocess.CompletedProcess(argv, 1, "", "no")

        monkeypatch.setattr(slicing.subprocess, "run", capture)
        slicing.slice_mesh(mesh, str(tmp_path / "out.gcode"))
        assert isinstance(seen["argv"], list)
        assert seen["kwargs"].get("shell") in (None, False)


class TestReadingStats:
    def test_an_unreadable_file_yields_nothing(self, tmp_path):
        assert slicing.read_stats(str(tmp_path / "absent.gcode")) == {}

    def test_a_file_without_a_summary_yields_nothing(self, tmp_path):
        path = tmp_path / "plain.gcode"
        path.write_text("G28\nG1 X1\n")
        assert slicing.read_stats(str(path)) == {}

    def test_the_summary_is_found_at_the_tail_of_a_large_file(self, tmp_path):
        path = tmp_path / "big.gcode"
        path.write_text("G1 X1 Y1\n" * 50_000 + "; filament used [g] = 7.5\n")
        assert slicing.read_stats(str(path))["filament_grams"] == "7.5"
