"""Turning an exported mesh into the G-code a printer will accept.

A printer does not read STL. Between the mesh this project exports and the job
the device runs sits a slicer, and this module is the whole of that step: find
the binary, run it over a file, and report what came back. What machine it is
being run for lives in :mod:`cadless.printer_profile`, and whether the model
fits that machine in :mod:`cadless.print_fit` -- both are answered before
anything here starts, and neither needs a subprocess.

The slicer is a separate process rather than a library, which is what keeps its
licence its own. It is expected to be on ``PATH`` -- the API image installs it,
so the ordinary Docker run needs nothing from the user. A checkout run outside
that image may not have it, and :attr:`SliceOutcome.missing` is that case
reported on its own rather than folded into a generic failure, because the
answer to it is an install rather than a retry.

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
import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from cadless.printer_profile import DEFAULT_PROFILE, BuildVolume, fmt
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

#: Written beside a finished job, naming the profile it was sliced under.
#:
#: Beside rather than inside: these bytes are streamed to a printer, and the one
#: thing this file must not do is change what the printer receives.
PROFILE_SUFFIX = ".profile"


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
    #: What the slicer said about the print while still producing one.
    #:
    #: It writes these on a *successful* run and exits zero, so nothing here used
    #: to read them. A table scaled to a tenth is thin legs under a floating top
    #: — the print most likely to come off the bed — and the slicer says so.
    #: That matters most when the tool is the one that proposed the scale.
    warning: str = ""


def find_slicer() -> str | None:
    """Return the slicer's path, or ``None`` when there is not one."""
    for name in SLICER_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def build_command(
    binary: str,
    mesh_path: str,
    out_path: str,
    profile: dict[str, str],
    *,
    fit_to: BuildVolume | None = None,
    rotate_degrees: float = 0.0,
) -> list[str]:
    """The argument vector, as a list so nothing goes through a shell.

    Kept separate from :func:`slice_mesh` so a test can assert on what would be
    run without running it, and so the profile is visible as data rather than
    buried in a call.

    ``fit_to`` shrinks the model until it fits the volume given. The slicer does
    that arithmetic against the **mesh**, which is always millimetres, rather
    than against the bounding box this project records in the project's own
    authoring units — so the shrinking itself is unit-correct once it is asked
    for. It does **not** close :func:`too_big_for`'s units gap: what asks for it
    is that same bounding-box check, so a model whose box reads a thousand times
    too small is never refused and never offered this either.

    ``rotate_degrees`` turns it about Z, only ever :data:`QUARTER_TURN`, for a
    model that fits turned and not as it stands or one whose offered size assumes
    that orientation.

    **The order of the two is load-bearing and measured.** ``--rotate`` is
    emitted first, and PrusaSlicer applies the transforms in that order: the same
    model and volume give 97.4% rotated-then-fitted and 92.7% the other way
    round. :func:`scale_offer` promises the first of those, so reordering these
    two blocks would break the promise with every test still green. Measured
    against the real binary in the API image, 1600 x 900 x 750 mm on the default
    bed: ``--rotate 90 --scale-to-fit 200,190,195`` puts extruding moves across
    106.42 x 189.55 mm, which is that box turned and scaled by 11.875%, not by
    the 12.5% an unrotated fit would give.
    """
    argv = [binary, "--export-gcode", "--output", out_path]
    for key, value in profile.items():
        argv += [f"--{key}", value]
    if rotate_degrees:
        argv += ["--rotate", fmt(rotate_degrees)]
    if fit_to is not None:
        argv += [
            "--scale-to-fit",
            f"{fmt(fit_to.width)},{fmt(fit_to.depth)},{fmt(fit_to.height)}",
        ]
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


def profile_fingerprint(profile: Mapping[str, str]) -> str:
    """A stable short digest of the profile a job was sliced under.

    Exists because the profile stopped being a constant. While it was one, "the
    mesh has not changed" meant "this job was built for this printer"; now the
    user can correct their bed between slicing and sending, and the refusal this
    module produces tells them to do exactly that. Without a fingerprint the
    stale job goes to the machine, and its moves leave the bed.

    Sorted before hashing so the digest is a property of the values rather than
    of dict ordering.

    **What it does not cover**: :data:`SCALE_MARGIN_MM` is not part of the
    profile, so a job scaled under one margin stays servable after that constant
    is edited. That takes a source change rather than anything a user can do, so
    it is written down rather than guarded.
    """
    payload = json.dumps(dict(sorted(profile.items())), separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


#: Where the slicer's advice starts, and the shape of the progress lines that
#: surround it. Its own output interleaves the two. Matched without regard to
#: case because nothing promises which the slicer uses, and the prefix is
#: dropped from what is returned -- the reader wants the sentence, not the label.
_WARNING_START = re.compile(r"print warning:", re.IGNORECASE)

_PROGRESS_LINE = re.compile(r"^\s*\d+ =>")


def _advice_in(text: str) -> str:
    """Every warning block in one stream, joined, with the prefixes dropped.

    A block runs from ``print warning:`` to the next progress line, and there can
    be more than one -- the slicer says its piece, carries on slicing, and says
    another. Taking only the first dropped advice about a print that was made.

    **One limit, deliberately left.** Advice whose own text is shaped like a
    progress line (``100 => ...``) ends the block early. Nothing in the output
    distinguishes the two, so the alternative is reading progress lines to the
    reader as though they were advice, which is the worse of the two errors.
    """
    blocks: list[str] = []
    said: list[str] = []
    for raw in text.splitlines():
        found = _WARNING_START.search(raw)
        if found:
            if said:
                blocks.append(" ".join(said))
            said = []
            rest = raw[found.end() :].strip()
            if rest:
                said.append(rest)
            continue
        if not said:
            continue
        if _PROGRESS_LINE.match(raw):
            blocks.append(" ".join(said))
            said = []
            continue
        line = raw.strip()
        if line:
            said.append(line)
    if said:
        blocks.append(" ".join(said))
    return " ".join(blocks)


def _warning_from(done: subprocess.CompletedProcess) -> str:
    """The slicer's advice about a print it nonetheless produced.

    Its output interleaves progress lines with the warning block, so the last few
    lines -- which is what :func:`_what_it_said` takes -- are usually progress
    rather than the thing worth reading. This reads the blocks instead.

    **One stream at a time, and this is load-bearing.** Searching the two joined
    let a block that ran to the end of stdout continue straight into stderr,
    where this container's ``libGL`` and ``Gtk-Message`` noise lives -- and what
    comes back here is rendered to the reader verbatim. Whichever stream holds
    the advice, it also holds its end.
    """
    for stream in (done.stdout or "", done.stderr or ""):
        found = _advice_in(stream)
        if found:
            return found
    return ""


def _what_it_said(done: subprocess.CompletedProcess) -> str:
    """The last few lines the slicer wrote, or ``""`` when it said nothing.

    One helper for both failure branches. They quote the same thing for the same
    reason -- the slicer is what knows why -- and two copies eight lines apart is
    one place for the next person to change and one to forget.
    """
    said = (done.stderr or done.stdout or "").strip().splitlines()
    return " ".join(line.strip() for line in said[-3:])


def _discard(path: str) -> None:
    """Remove a part-file, ignoring the case where it was never created."""
    with contextlib.suppress(OSError):
        os.unlink(path)


def sliced_under(gcode_path: str) -> str:
    """The fingerprint recorded beside a job, or ``""`` when there is none.

    Empty for a job sliced before this existed. The caller decides what that
    means -- refusing every job an older build left behind would turn an upgrade
    into a re-slice of everything, for a mismatch nobody has evidence of.
    """
    try:
        with open(gcode_path + PROFILE_SUFFIX) as handle:
            return handle.read().strip()
    except OSError:
        return ""


def slice_mesh(
    mesh_path: str,
    out_path: str,
    *,
    profile: dict[str, str] | None = None,
    fit_to: BuildVolume | None = None,
    rotate_degrees: float = 0.0,
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
    argv = build_command(
        binary,
        mesh_path,
        part_path,
        profile or DEFAULT_PROFILE,
        fit_to=fit_to,
        rotate_degrees=rotate_degrees,
    )
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
        tail = _what_it_said(done) or f"exit code {done.returncode}"
        return SliceOutcome(False, f"The slicer refused this model: {tail}")

    if not os.path.exists(part_path) or os.path.getsize(part_path) == 0:
        _discard(part_path)
        # It exited zero and wrote nothing, which happens when every object is
        # outside the print volume -- and it says so on stderr before doing it.
        # Reading that only on the non-zero branch turned a precise explanation
        # into "wrote no G-code", which names nothing the reader can act on.
        tail = _what_it_said(done)
        if tail:
            return SliceOutcome(False, f"The slicer produced no G-code: {tail}")
        return SliceOutcome(False, "The slicer reported success but wrote no G-code.")

    try:
        os.replace(part_path, out_path)
    except OSError as exc:
        _discard(part_path)
        return SliceOutcome(False, f"The sliced file could not be put in place: {exc}")

    # After the rename, never before: a fingerprint sitting beside a job that
    # does not exist would answer for the next one written under that name.
    _discard(out_path + PROFILE_SUFFIX)
    with contextlib.suppress(OSError):
        with open(out_path + PROFILE_SUFFIX, "w") as handle:
            handle.write(profile_fingerprint(profile or DEFAULT_PROFILE))

    return SliceOutcome(
        True,
        "",
        gcode_path=out_path,
        stats=read_stats(out_path),
        warning=_warning_from(done),
    )
