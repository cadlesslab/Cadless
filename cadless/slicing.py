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
import math
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
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

#: The build volume assumed when the user has not said what they own, in
#: millimetres. Named rather than written into the profile string, because the
#: fit check below and the ``bed-shape`` flag have to be the same numbers -- two
#: copies would drift, and the one that drifts is the one telling somebody their
#: model does not fit.
DEFAULT_BED_WIDTH = 210.0
DEFAULT_BED_DEPTH = 200.0
DEFAULT_MAX_HEIGHT = 195.0

#: How much hotter the first layer runs than the rest. The default profile
#: already does this (205 then 210), and saving a different filament has to move
#: the first layer with it rather than leaving it on the old constant.
FIRST_LAYER_BONUS_C = 5.0


def _fmt(value: float) -> str:
    """A number as a flag value: no trailing ``.0`` on a whole one."""
    whole = int(value)
    return str(whole) if value == whole else str(value)


def _bed_shape(width: float, depth: float) -> str:
    """The four corners PrusaSlicer wants, anticlockwise from the origin."""
    return f"0x0,{_fmt(width)}x0,{_fmt(width)}x{_fmt(depth)},0x{_fmt(depth)}"


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
    "bed-shape": _bed_shape(DEFAULT_BED_WIDTH, DEFAULT_BED_DEPTH),
    "max-print-height": _fmt(DEFAULT_MAX_HEIGHT),
}


@dataclass(frozen=True)
class BuildVolume:
    """What the printer can physically hold, in millimetres."""

    width: float
    depth: float
    height: float


def _number(
    saved: Mapping[str, Any] | None, field_name: str, *, allow_zero: bool = False
) -> float | None:
    """A saved value as a usable number, or ``None`` when it is not one.

    Fail closed at the point of use rather than trusting what was validated on
    the way in. ``settings.json`` can be hand-edited, half-written, or left by an
    older build, and the command that reaches a printer has to stay one the
    slicer can act on -- a bed of ``nan`` is not a bed.

    ``allow_zero`` because a temperature of zero is a real answer (no heated
    bed) while a dimension of zero is not.
    """
    if not saved:
        return None
    raw = saved.get(field_name)
    # `bool` is an `int`, and `True` would otherwise become a 1 mm nozzle.
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    if value < 0 or (value == 0 and not allow_zero):
        return None
    return value


def build_volume(saved: Mapping[str, Any] | None = None) -> BuildVolume:
    """The printer's build volume: what the user saved, or the default."""
    return BuildVolume(
        width=_number(saved, "printer_bed_width") or DEFAULT_BED_WIDTH,
        depth=_number(saved, "printer_bed_depth") or DEFAULT_BED_DEPTH,
        height=_number(saved, "printer_max_height") or DEFAULT_MAX_HEIGHT,
    )


def profile_from_settings(saved: Mapping[str, Any] | None = None) -> dict[str, str]:
    """:data:`DEFAULT_PROFILE`, with whatever the user has saved about their printer.

    Every unset value keeps today's default, which is the whole upgrade path: an
    installation that has never opened Settings goes on producing exactly the
    G-code it produced before any of this existed.
    """
    profile = dict(DEFAULT_PROFILE)
    volume = build_volume(saved)
    profile["bed-shape"] = _bed_shape(volume.width, volume.depth)
    profile["max-print-height"] = _fmt(volume.height)

    nozzle = _number(saved, "printer_nozzle_diameter")
    if nozzle is not None:
        profile["nozzle-diameter"] = _fmt(nozzle)
    filament = _number(saved, "printer_filament_diameter")
    if filament is not None:
        profile["filament-diameter"] = _fmt(filament)
    hot = _number(saved, "printer_nozzle_temperature", allow_zero=True)
    if hot is not None:
        profile["temperature"] = _fmt(hot)
        profile["first-layer-temperature"] = _fmt(hot + FIRST_LAYER_BONUS_C)
    bed = _number(saved, "printer_bed_temperature", allow_zero=True)
    if bed is not None:
        profile["bed-temperature"] = _fmt(bed)
        profile["first-layer-bed-temperature"] = _fmt(bed)
    return profile


def too_big_for(bbox: Sequence[Any] | None, volume: BuildVolume) -> str:
    """Why this model cannot fit, or ``""`` when it might.

    Answered from the bounding box the version already carries, so the reader
    hears it immediately instead of waiting for a slicer to run and then being
    told less. PrusaSlicer's own answer to this is ``All objects are outside of
    the print volume``, which names neither size.

    **Deliberately conservative.** This exists to say something more useful than
    the slicer would, so refusing a model that could be printed is the worse
    error. The footprint is therefore compared in both orientations -- turning a
    part a quarter turn costs the operator nothing -- and a bounding box that is
    not three usable numbers is not refused at all.
    """
    try:
        width, depth, height = (float(value) for value in bbox)  # type: ignore[misc]
    except (TypeError, ValueError):
        return ""
    if not all(math.isfinite(value) for value in (width, depth, height)):
        return ""

    footprint = sorted((width, depth))
    bed = sorted((volume.width, volume.depth))
    fits_flat = footprint[0] <= bed[0] and footprint[1] <= bed[1]
    if fits_flat and height <= volume.height:
        return ""
    return (
        f"This model is {_fmt(width)} x {_fmt(depth)} x {_fmt(height)} mm, and the printer's "
        f"build volume is {_fmt(volume.width)} x {_fmt(volume.depth)} x {_fmt(volume.height)} mm. "
        "Ask for a smaller model, or set your printer's real size in Settings."
    )


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
        # It exited zero and wrote nothing, which happens when every object is
        # outside the print volume -- and it says so on stderr before doing it.
        # Reading that only on the non-zero branch turned a precise explanation
        # into "wrote no G-code", which names nothing the reader can act on.
        said = (done.stderr or done.stdout or "").strip().splitlines()
        tail = " ".join(line.strip() for line in said[-3:])
        if tail:
            return SliceOutcome(False, f"The slicer produced no G-code: {tail}")
        return SliceOutcome(False, "The slicer reported success but wrote no G-code.")

    try:
        os.replace(part_path, out_path)
    except OSError as exc:
        _discard(part_path)
        return SliceOutcome(False, f"The sliced file could not be put in place: {exc}")

    return SliceOutcome(True, "", gcode_path=out_path, stats=read_stats(out_path))
