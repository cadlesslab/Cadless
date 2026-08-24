"""Turning an exported mesh into the G-code a printer will accept.

A printer does not read STL. Between the mesh this project exports and the job
the device runs sits a slicer, and this module is the whole of that step: find
the binary, run it over a file, and report what came back.

The slicer is a separate process rather than a library, which is what keeps its
licence its own. It is expected to be on ``PATH`` -- the API image installs it,
so the ordinary Docker run needs nothing from the user. A checkout run outside
that image may not have it, and :attr:`SliceOutcome.missing` is that case
reported on its own rather than folded into a generic failure, because the
answer to it is an install rather than a retry.

Every profile value is passed explicitly. Leaning on a slicer's built-in
defaults would make the output depend on which build is installed, and a print
that silently changes with a package upgrade is worse than one that fails.

**On the trust boundary.** The mesh handed to the slicer was produced by code
this project treats as untrusted -- a catalogue item can arrive from elsewhere
and is executable -- and the slicer is a large C++ mesh parser. It runs in the
API process's container rather than behind the execution sandbox, so the CPU
rlimit below and the caller's concurrency gate are what bound it. That is a
weaker boundary than the one generated code runs behind, and it is recorded in
``docs/architecture.md`` rather than left implicit.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from cadless.worker import _limit_resources

#: Names the binary goes by. Debian ships ``prusa-slicer``; the upstream builds
#: and the macOS bundle use the capitalised form.
SLICER_NAMES = ("prusa-slicer", "PrusaSlicer", "prusa-slicer-console")

#: What to tell someone who has not got it. Named here so the API, the UI and
#: the tests all quote the same sentence.
INSTALL_HINT = (
    "No slicer was found. The Docker image ships one, so `make up` needs nothing "
    "extra; running the backend straight from a checkout needs PrusaSlicer on your "
    "PATH (Debian/Ubuntu: `apt install prusa-slicer`, macOS: "
    "`brew install --cask prusaslicer`)."
)

DEFAULT_TIMEOUT = 300.0

#: How much of the finished file to read back for the slicer's own summary.
#: Generous because the summary is not the last thing written -- see
#: :func:`read_stats`.
STATS_TAIL_BYTES = 512 * 1024

#: The block PrusaSlicer appends after its summary. Everything worth reading is
#: before it.
CONFIG_BLOCK = "prusaslicer_config = begin"

#: Written while slicing, renamed on success. A half-written file under the
#: final name would be indistinguishable from a finished one.
PART_SUFFIX = ".part"

#: The print profile, as explicit flags. These are the values a first print on a
#: 210 x 200 x 195 mm FDM machine with a 0.4 mm nozzle and 1.75 mm PLA wants;
#: they are deliberately conservative rather than fast.
DEFAULT_PROFILE: dict[str, str] = {
    "layer-height": "0.2",
    "first-layer-height": "0.3",
    "nozzle-diameter": "0.4",
    "filament-diameter": "1.75",
    "temperature": "205",
    "first-layer-temperature": "210",
    "bed-temperature": "60",
    "first-layer-bed-temperature": "60",
    "fill-density": "20%",
    "perimeters": "2",
    "top-solid-layers": "4",
    "bottom-solid-layers": "3",
    "skirts": "1",
    "gcode-flavor": "marlin",
    "bed-shape": "0x0,210x0,210x200,0x200",
    "max-print-height": "195",
}


@dataclass(frozen=True)
class SliceOutcome:
    """What came of running the slicer.

    ``missing`` is separate from ``ok`` because the two failures want different
    answers from whoever is reading: a missing binary is installed, a rejected
    model is fixed.
    """

    ok: bool
    detail: str = ""
    gcode_path: str = ""
    missing: bool = False
    stats: dict[str, Any] = field(default_factory=dict)


def find_slicer() -> str | None:
    """Return the slicer's path, or ``None`` when there is not one."""
    for name in SLICER_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def build_command(binary: str, mesh_path: str, out_path: str, profile: dict[str, str]) -> list[str]:
    """The argument vector, as a list so nothing goes through a shell.

    Kept separate from :func:`slice_mesh` so a test can assert on what would be
    run without running it, and so the profile is visible as data rather than
    buried in a call.
    """
    argv = [binary, "--export-gcode", "--output", out_path]
    for key, value in profile.items():
        argv += [f"--{key}", value]
    argv.append(mesh_path)
    return argv


#: PrusaSlicer writes its own summary into the file as comments. Reading them
#: back beats re-deriving the numbers here.
_STAT_PATTERNS = {
    "estimated_time": re.compile(r"estimated printing time[^=]*=\s*(.+)"),
    "filament_grams": re.compile(r"filament used \[g\][^=]*=\s*([\d.]+)"),
    "filament_mm": re.compile(r"filament used \[mm\][^=]*=\s*([\d.]+)"),
}


def read_stats(gcode_path: str) -> dict[str, Any]:
    """Pull the slicer's own summary out of the finished file.

    Only the tail is read: a sliced model runs to tens of megabytes and nothing
    here needs it in memory. The window is large, and anything after the
    configuration block is dropped before searching, because the summary is
    **not** the last thing in the file -- PrusaSlicer appends a multi-kilobyte
    dump of every setting after it, and a window sized for "the last few lines"
    lands inside that dump and finds nothing.
    """
    stats: dict[str, Any] = {}
    try:
        size = os.path.getsize(gcode_path)
        with open(gcode_path, "rb") as handle:
            handle.seek(max(0, size - STATS_TAIL_BYTES))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return stats
    head, sep, _ = tail.partition(CONFIG_BLOCK)
    searchable = head if sep else tail
    for name, pattern in _STAT_PATTERNS.items():
        found = pattern.search(searchable)
        if found:
            stats[name] = found.group(1).strip()
    return stats


def _discard(path: str) -> None:
    """Remove a part-file, ignoring the case where it was never created."""
    with contextlib.suppress(OSError):
        os.unlink(path)


def slice_mesh(
    mesh_path: str,
    out_path: str,
    *,
    profile: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> SliceOutcome:
    """Slice ``mesh_path`` into ``out_path``.

    Failure is returned rather than raised, matching the printer client: a
    handler should be able to report a model the slicer would not take without
    that becoming a 500.

    The slicer writes to a part-file that is renamed only once it has exited
    cleanly and left something behind. Without that, a timeout -- which kills
    the slicer part-way through writing -- leaves a truncated file under the
    final name, and the only thing standing between that and a printer is a
    caller checking whether the path exists.
    """
    binary = find_slicer()
    if binary is None:
        return SliceOutcome(False, INSTALL_HINT, missing=True)
    if not os.path.exists(mesh_path):
        return SliceOutcome(False, "The model file to print is missing from disk.")

    part_path = out_path + PART_SUFFIX
    _discard(part_path)
    argv = build_command(binary, mesh_path, part_path, profile or DEFAULT_PROFILE)
    try:
        done = subprocess.run(  # noqa: S603 - argv built here; never a shell string
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            # The mesh is untrusted input to a large C++ parser. A wall-clock
            # timeout alone does not stop it burning a core; this is the same
            # cap the code-execution subprocess runs under.
            preexec_fn=_limit_resources(int(timeout)),  # noqa: PLW1509
        )
    except subprocess.TimeoutExpired:
        _discard(part_path)
        return SliceOutcome(False, f"The slicer did not finish within {timeout:.0f}s.")
    except OSError as exc:  # the binary vanished between the check and the run
        _discard(part_path)
        return SliceOutcome(False, f"The slicer could not be started: {exc}", missing=True)

    if done.returncode != 0:
        _discard(part_path)
        # The slicer's own words are the useful part -- it is the thing that
        # knows the model is too tall or the mesh is not manifold.
        said = (done.stderr or done.stdout or "").strip().splitlines()
        tail = " ".join(said[-3:]) if said else f"exit code {done.returncode}"
        return SliceOutcome(False, f"The slicer refused this model: {tail}")

    if not os.path.exists(part_path) or os.path.getsize(part_path) == 0:
        _discard(part_path)
        return SliceOutcome(False, "The slicer reported success but wrote no G-code.")

    try:
        os.replace(part_path, out_path)
    except OSError as exc:
        _discard(part_path)
        return SliceOutcome(False, f"The sliced file could not be put in place: {exc}")

    return SliceOutcome(True, "", gcode_path=out_path, stats=read_stats(out_path))
