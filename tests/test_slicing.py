"""The slicing step: finding the binary, calling it, and reading what it said.

The slicer itself is not installed in CI, so the subprocess is replaced. What is
worth pinning is the argument vector (a profile value silently dropped changes
what gets printed), the failure classification (a missing binary and a refused
model want different answers from the reader), and that nothing is left under
the final name unless the slicer finished.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from cadless import slicing


@pytest.fixture
def mesh(tmp_path):
    path = tmp_path / "model.stl"
    path.write_bytes(b"solid x\nendsolid x\n")
    return str(path)


@pytest.fixture
def out(tmp_path):
    return str(tmp_path / "print.gcode")


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr(slicing, "find_slicer", lambda: "/usr/bin/prusa-slicer")


def _output_path(argv: list[str]) -> str:
    """Where the slicer was told to write. Not the final name -- see PART_SUFFIX."""
    return argv[argv.index("--output") + 1]


def _writes(text: str, code: int = 0):
    """A fake slicer that writes `text` where it was told to."""

    def run(argv, **_kwargs):
        with open(_output_path(argv), "w") as handle:
            handle.write(text)
        return subprocess.CompletedProcess(argv, code, "", "")

    return run


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
        assert _output_path(argv) == "out.gcode"

    def test_the_profile_covers_what_a_print_needs(self):
        """A missing one of these makes the slicer fall back to its own default,
        which would make output depend on the installed build."""
        for key in ("layer-height", "nozzle-diameter", "filament-diameter", "bed-shape"):
            assert key in slicing.DEFAULT_PROFILE


class TestSlicing:
    def test_a_missing_binary_is_its_own_outcome(self, monkeypatch, mesh, out):
        monkeypatch.setattr(slicing, "find_slicer", lambda: None)
        outcome = slicing.slice_mesh(mesh, out)
        assert not outcome.ok
        assert outcome.missing is True
        assert outcome.detail == slicing.INSTALL_HINT

    def test_a_missing_mesh_is_not_reported_as_a_missing_slicer(self, installed, tmp_path, out):
        outcome = slicing.slice_mesh(str(tmp_path / "gone.stl"), out)
        assert not outcome.ok
        assert outcome.missing is False

    def test_the_slicers_own_words_reach_the_reader(self, installed, monkeypatch, mesh, out):
        monkeypatch.setattr(
            slicing.subprocess,
            "run",
            lambda argv, **k: subprocess.CompletedProcess(
                argv, 1, "", "Object too tall for the print volume"
            ),
        )
        outcome = slicing.slice_mesh(mesh, out)
        assert not outcome.ok
        assert "too tall" in outcome.detail

    def test_success_without_a_file_is_still_a_failure(self, installed, monkeypatch, mesh, out):
        """A zero exit with nothing written must not read as a print-ready job."""
        monkeypatch.setattr(
            slicing.subprocess,
            "run",
            lambda argv, **k: subprocess.CompletedProcess(argv, 0, "", ""),
        )
        outcome = slicing.slice_mesh(mesh, out)
        assert not outcome.ok
        assert "wrote no G-code" in outcome.detail

    def test_a_timeout_is_reported_rather_than_raised(self, installed, monkeypatch, mesh, out):
        def boom(*_a, **_k):
            raise subprocess.TimeoutExpired("prusa-slicer", 1)

        monkeypatch.setattr(slicing.subprocess, "run", boom)
        outcome = slicing.slice_mesh(mesh, out, timeout=1)
        assert not outcome.ok
        assert "did not finish" in outcome.detail

    def test_a_sliced_job_reports_its_stats(self, installed, monkeypatch, mesh, out):
        monkeypatch.setattr(
            slicing.subprocess,
            "run",
            _writes(
                "G28\n"
                "; estimated printing time (normal mode) = 1h 2m 3s\n"
                "; filament used [g] = 12.34\n"
            ),
        )
        outcome = slicing.slice_mesh(mesh, out)
        assert outcome.ok, outcome.detail
        assert outcome.stats["estimated_time"] == "1h 2m 3s"
        assert outcome.stats["filament_grams"] == "12.34"

    def test_the_command_never_goes_through_a_shell(self, installed, monkeypatch, mesh, out):
        """A mesh path is a filename from disk; running it through a shell would
        make its contents executable."""
        seen = {}

        def capture(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return subprocess.CompletedProcess(argv, 1, "", "no")

        monkeypatch.setattr(slicing.subprocess, "run", capture)
        slicing.slice_mesh(mesh, out)
        assert isinstance(seen["argv"], list)
        assert seen["kwargs"].get("shell") in (None, False)

    def test_the_child_is_cpu_limited(self, installed, monkeypatch, mesh, out):
        """The mesh is untrusted input to a large C++ parser, and a wall clock
        does not stop it burning a core."""
        seen = {}

        def capture(argv, **kwargs):
            seen["preexec"] = kwargs.get("preexec_fn")
            return subprocess.CompletedProcess(argv, 1, "", "no")

        monkeypatch.setattr(slicing.subprocess, "run", capture)
        slicing.slice_mesh(mesh, out)
        assert callable(seen["preexec"])


class TestNothingHalfWrittenSurvives:
    """The caller treats the file's presence as evidence of a finished slice."""

    def test_a_timeout_leaves_nothing_under_the_final_name(self, installed, monkeypatch, mesh, out):
        def die(argv, **_k):
            # What a killed slicer leaves: a part-file with a truncated job.
            with open(_output_path(argv), "w") as handle:
                handle.write("G28\nG1 X1 ; cut off here")
            raise subprocess.TimeoutExpired("prusa-slicer", 1)

        monkeypatch.setattr(slicing.subprocess, "run", die)
        outcome = slicing.slice_mesh(mesh, out, timeout=1)
        assert not outcome.ok
        assert not os.path.exists(out)
        assert not os.path.exists(out + slicing.PART_SUFFIX)

    def test_a_refused_model_leaves_nothing_under_the_final_name(
        self, installed, monkeypatch, mesh, out
    ):
        monkeypatch.setattr(slicing.subprocess, "run", _writes("G28\npartial", code=1))
        outcome = slicing.slice_mesh(mesh, out)
        assert not outcome.ok
        assert not os.path.exists(out)
        assert not os.path.exists(out + slicing.PART_SUFFIX)

    def test_a_failed_slice_does_not_disturb_the_last_good_one(
        self, installed, monkeypatch, mesh, out
    ):
        """A retry that fails must not take away what was already printable."""
        with open(out, "w") as handle:
            handle.write("G28\n; the good one\n")
        monkeypatch.setattr(slicing.subprocess, "run", _writes("G28\npartial", code=1))
        assert not slicing.slice_mesh(mesh, out).ok
        with open(out) as handle:
            assert "the good one" in handle.read()

    def test_success_moves_the_part_file_into_place(self, installed, monkeypatch, mesh, out):
        monkeypatch.setattr(slicing.subprocess, "run", _writes("G28\n; done\n"))
        outcome = slicing.slice_mesh(mesh, out)
        assert outcome.ok, outcome.detail
        assert os.path.exists(out)
        assert not os.path.exists(out + slicing.PART_SUFFIX)


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

    def test_the_summary_is_found_before_the_config_block(self, tmp_path):
        """PrusaSlicer writes the summary and *then* dumps every setting.

        A window sized for "the last few lines" lands inside that dump and finds
        nothing, and the confirmation dialog loses the numbers it exists for.
        """
        path = tmp_path / "real-shaped.gcode"
        path.write_text(
            "G1 X1 Y1\n" * 10_000
            + "; estimated printing time (normal mode) = 2h 30m 0s\n"
            + "; filament used [g] = 41.2\n"
            + f"; {slicing.CONFIG_BLOCK}\n"
            + "".join(f"; setting_{i} = value\n" for i in range(4000))
            + "; prusaslicer_config = end\n"
        )
        stats = slicing.read_stats(str(path))
        assert stats["estimated_time"] == "2h 30m 0s"
        assert stats["filament_grams"] == "41.2"

    def test_a_setting_inside_the_config_block_is_not_read_as_a_summary(self, tmp_path):
        """The dump contains lines shaped like the summary; they are not it."""
        path = tmp_path / "decoy.gcode"
        path.write_text(
            f"; {slicing.CONFIG_BLOCK}\n; filament used [g] = 999\n; prusaslicer_config = end\n"
        )
        assert "filament_grams" not in slicing.read_stats(str(path))
