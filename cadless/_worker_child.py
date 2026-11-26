"""Child process that executes one generated build123d script.

Invoked as a subprocess by :mod:`cadless.worker`. Reads a code file, runs it
through the gate and the restricted namespace of :mod:`cadless.script_runtime`,
computes a geometry summary, optionally exports artifacts, and prints a single
``__VTRESULT__ {json}`` line to stdout -- the only thing it writes there: run as
a program, it points everything else written to stdout at stderr. Kept
dependency-light, and side-effect free apart from the optional export — which
writes one file per solid for each kind asked, every kind unless the caller
names fewer, and first removes what an earlier build left of any kind in the
same directory; plus, for a build of several parts, the whole as one GLB and
one STEP where those kinds are written, clearing an earlier build's pair by
exact name.

Run: python -m cadless._worker_child <code_file> [<export_dir>] [<export_scale>]
     [<check_assembly>] [<wall_secs>] [<check_file>] [<kinds>]
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback
from typing import TextIO

SENTINEL = "__VTRESULT__"

# Where the result line goes: stdout, unless the program's entry point below has
# kept stdout for it alone.
_result_stream: TextIO | None = None

# When this process started: the parent's wall clock runs from about here, so
# the time left to it is measured from here too.
_STARTED = time.monotonic()

# Filename used when compiling the generated script; lets us pick its frames out
# of a traceback so we can anchor the error on a line of the user's code.
_GENERATED_FILE = "<generated>"


def _emit(payload: dict) -> None:
    """Write the child's result line where the parent reads it."""
    stream = _result_stream if _result_stream is not None else sys.stdout
    stream.write(f"{SENTINEL} {json.dumps(payload)}\n")
    stream.flush()


def _keep_stdout_for_the_result() -> TextIO:
    """Point fd 1 at stderr, and keep the real stdout for the result line alone.

    The kernel writes its messages to fd 1 itself, and a message can carry text
    a script chose. Left on stdout, such a line could stand where the parent
    reads the result -- or after it, from a finalizer run at exit.
    """
    sys.stdout.flush()
    result = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    return result


def _refusal_payload(reasons: list[str]) -> dict:
    """A refused script, reported as the pipeline reports its own refusals."""
    text = "; ".join(reasons)
    return {
        "ok": False,
        "error": f"validation: {text}",
        "error_type": "ValidationError",
        "message": text,
    }


def _not_a_shape_payload(result) -> dict:
    """A ``result`` the child will not measure, reported so a repair can fix it.

    A builder assigned in place of what it built is the usual way to get here,
    so it is named with the attribute that holds the shape.
    """
    kind = type(result).__name__
    built = getattr(type(result), "_obj_name", None)
    if isinstance(built, str) and kind.startswith("Build"):
        message = f"`result` is the {kind} builder, not a shape: assign its .{built} to `result`"
    else:
        message = f"`result` is a {kind}, not a shape: assign the model to it"
    return {"ok": False, "error": message, "error_type": "TypeError", "message": message}


def measurement_budget(wall_secs: float, elapsed: float, asked: float | None = None) -> float:
    """How many seconds the assembly measurement may spend.

    A share of the time left rather than of the whole clock: the clock covers the
    build, the measurement and the export together, and a build that already ran
    long would otherwise be pushed past it by the measurement and return nothing.
    With no clock to divide, the fixed budget.

    ``asked`` is a figure the caller set for itself. It is kept where it is the
    smaller and never lengthens the share: the export comes after the
    measurement on the same clock, so a measurement let run past its share can
    leave the files unwritten and the whole build unreported, where one stopped
    at its share says what it did not establish and the files are written. A
    caller that wants longer lengthens the clock. With no clock there is
    nothing to stay inside, and the figure asked for stands.
    """
    from cadless import assembly_measure  # noqa: PLC0415 - lazy, as below

    if wall_secs <= 0:
        return assembly_measure.MEASUREMENT_TIME_BUDGET_SECONDS if asked is None else asked
    share = max(0.0, wall_secs - elapsed) * assembly_measure.MEASUREMENT_TIME_SHARE
    return share if asked is None else min(asked, share)


def _error_payload(exc: BaseException, code: str, *, prefix: str = "") -> dict:
    """Build the structured failure payload for one execution exception.

    Captures the full formatted traceback and maps the last frame that belongs
    to the generated script back to its source line, so the repair prompt can be
    line-anchored.
    """
    tb_text = traceback.format_exc()
    offending_line = _offending_line(exc, code)
    message = f"{prefix}{type(exc).__name__}: {exc}"
    return {
        "ok": False,
        "error": message,
        "error_type": type(exc).__name__,
        "message": str(exc),
        "offending_line": offending_line,
        "traceback": tb_text,
    }


def _offending_line(exc: BaseException, code: str) -> str | None:
    """Return the generated-script source line of the last user frame, or None."""
    lines = code.splitlines()
    lineno = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.filename == _GENERATED_FILE:
            lineno = frame.lineno
    # SyntaxError carries its location on the exception, not in the traceback.
    if lineno is None and isinstance(exc, SyntaxError) and exc.lineno:
        lineno = exc.lineno
    if lineno is None or not (1 <= lineno <= len(lines)):
        return None
    return lines[lineno - 1].strip()


def _degenerate(summary: dict) -> str | None:
    """Why the summarised result is no solid to hand on, or ``None`` where it is one.

    A volume that is no finite number measured nothing. A part placed where a
    coordinate is NaN or infinite has no volume of its own, so the whole keeps
    its neighbours' and passes on volume alone, with a box no reader can use --
    the assembly measurement among them, which the kernel can hold inside one
    call past its budget.
    """
    volume = summary["volume"]
    if not math.isfinite(volume):
        return "volume is not a finite number"
    if volume <= 0:
        return "volume <= 0"
    if not all(math.isfinite(side) for side in summary["bbox"]):
        return (
            "its bounding box is not finite numbers: a part sits where a coordinate is "
            "NaN or infinite"
        )
    return None


def _summarise(result) -> dict:
    bbox = result.bounding_box()
    size = bbox.size
    return {
        "volume": float(result.volume),
        "bbox": [float(size.X), float(size.Y), float(size.Z)],
        # Cheap, deterministic metrics for post-condition assertions.
        # min_wall_thickness is deliberately omitted: a robust OCCT thickness probe
        # is too heavy/unreliable for the PoC, so it stays absent and assertions
        # against it are skipped (never spuriously failed) downstream.
        "part_count": _part_count(result),
        "manifold": _is_manifold(result),
    }


def _part_count(result) -> int | None:
    """Number of disjoint solids in the result, or None if it can't be counted."""
    try:
        return len(result.solids())
    except Exception:  # noqa: BLE001 - metric is best-effort; None -> skipped
        return None


def _read_tree(result, scale: float):
    """The build read as a tree of named parts, or ``None`` if its solids cannot be read.

    A shape whose solids cannot be read is exported whole for the same reason
    ``_part_count`` returns ``None`` there: the fallback is the old behaviour,
    not a failure. Nothing after that is caught here: a part that cannot be
    baked or scaled fails the build as a post-process error, as a result that
    cannot be scaled does.
    """
    from cadless.assembly_tree import build_tree  # noqa: PLC0415 - lazy, as the exporters are

    try:
        result.solids()
    except Exception:  # noqa: BLE001 - unsplittable shapes export whole, as before
        return None
    return build_tree(result, scale)


def _parts_to_export(shape, tree) -> list:
    """The pieces to write as separate files: the tree's parts, or the shape itself.

    A one-part build exports the shape it was handed rather than the part read
    back out of it. They are the same geometry, and passing the original through
    keeps a single-part export identical to what it was before parts existed --
    which is what the upgrade path rests on. A build of several parts exports
    each part with its placement already in its geometry, so no reader of a
    part's file has a Location to compose.
    """
    if tree is None or len(tree.parts) < 2:
        return [shape]
    return [part.solid for part in tree.parts]


def _clear_previous(export_dir: str, kind: str) -> None:
    """Delete this kind's files from an earlier build in the same directory.

    An export directory may already hold an earlier build's files: callers reuse
    one. Left in place, a two-part build followed by a one-solid one leaves
    ``model.stl`` and ``model_p*.stl`` side by side, and nothing in either name
    says which build is current -- whoever reads the directory has to guess.
    Cleared, it holds one shape at a time and the question never arises.

    Failures are not swallowed: a file that cannot be removed would leave exactly
    the mixed directory this exists to prevent, and the export is better abandoned
    loudly than completed into one.
    """
    from pathlib import Path

    directory = Path(export_dir)
    if not directory.is_dir():
        return
    for stale in [directory / f"model.{kind}", *directory.glob(f"model_p*.{kind}")]:
        # ``exists`` follows a link, so a dangling one would be left for the
        # export to write through.
        if stale.is_symlink() or stale.exists():
            stale.unlink()


def _is_manifold(result) -> bool | None:
    """Whether the result is a single closed (watertight) manifold, or None.

    Uses build123d's ``is_manifold`` property; returns None when unavailable so a
    manifold assertion is skipped rather than failed on an unknown value.
    """
    try:
        return bool(result.is_manifold)
    except Exception:  # noqa: BLE001 - metric is best-effort; None -> skipped
        return None


def main(argv: list[str]) -> int:
    code_file = argv[1]
    export_dir = argv[2] if len(argv) > 2 and argv[2] else None
    # Authoring-units -> mm factor applied to *exports only* (issue #18/#20);
    # the geometry summary always stays in the script's authoring units.
    export_scale = float(argv[3]) if len(argv) > 3 and argv[3] else 1.0
    # Off unless the caller will read the measurement: measuring the relations
    # between parts costs the same wall clock the build is running on.
    check_assembly = bool(len(argv) > 4 and argv[4])
    # The parent's wall clock, so the assembly measurement can take a share of
    # it rather than a fixed number of seconds. A clock that runs out up here
    # returns no summary at all, so the measurement has to stop before it, and
    # a constant only does that at one particular timeout.
    wall_secs = float(argv[5]) if len(argv) > 5 and argv[5] else 0.0
    if not math.isfinite(export_scale) or export_scale <= 0:
        # Before the script runs: a negative scale exported a mirrored model as a
        # success, NaN held the kernel inside the export or passed where nothing
        # was exported, and zero or infinity failed in the kernel as if the model
        # were at fault.
        error = f"export scale {argv[3]!r} is not a positive finite number"
        _emit({"ok": False, "error": error})
        return 1
    # What the caller asks of the assembly measurement, read before the script
    # runs: a request nobody could act on is the caller's fault and is said as
    # that, rather than found after a build was paid for, and a script has run
    # nothing by the time what is asked of its build is settled.
    request = None
    if len(argv) > 6 and argv[6]:
        from cadless.assembly_check import read_check_request  # noqa: PLC0415 - lazy, as below

        try:
            with open(argv[6], encoding="utf-8") as fh:  # trusted path from parent
                request = read_check_request(json.load(fh))
        except (OSError, ValueError, RecursionError) as refused:
            _emit({"ok": False, "error": f"the assembly check's request cannot be used: {refused}"})
            return 1
    # The kinds the caller asks the export for, read before the script runs for
    # the reason the request above is. Text in the argument rather than a file:
    # it names a few of the registry's kinds however many parts a build has.
    kinds = None
    if len(argv) > 7 and argv[7]:
        from cadless.exporters import read_kinds  # noqa: PLC0415 - lazy, as below

        try:
            kinds = read_kinds(json.loads(argv[7]))
        except (ValueError, RecursionError) as refused:
            _emit({"ok": False, "error": f"the run's export kinds cannot be used: {refused}"})
            return 1
    code = open(code_file).read()  # noqa: S108,SIM115 - trusted path from parent

    # Lazy, as the exporters are: the parent imports this module for SENTINEL.
    from cadless.script_runtime import ScriptRefused, run_script  # noqa: PLC0415

    # BaseException, here and below: whatever ends a build early is reported,
    # rather than ending the process before it can say so.
    try:
        ns = run_script(code, _GENERATED_FILE)
    except ScriptRefused as refused:
        _emit(_refusal_payload(refused.reasons))
        return 1
    except BaseException as exc:  # noqa: BLE001
        _emit(_error_payload(exc, code))
        return 1

    if "result" not in ns:
        _emit({"ok": False, "error": "missing `result`"})
        return 1

    result = ns["result"]
    from build123d.topology import Shape  # noqa: PLC0415 - lazy, as above

    if not isinstance(result, Shape):
        # Everything below reads the result by duck typing, so an object the
        # script put together itself would report whatever it chose to.
        _emit(_not_a_shape_payload(result))
        return 1
    try:
        summary = _summarise(result)
        problem = _degenerate(summary)
        if problem is not None:
            _emit({"ok": False, "error": f"degenerate solid ({problem})"})
            return 1
        # Scaled once, above both readers. The export writes millimetres and the
        # assembly checks are against a build volume in millimetres, so measuring
        # the unscaled result would compare authoring units to millimetres and
        # never refuse a model authored in metres. About the world origin: left to
        # itself, ``scale`` anchors at the shape's own Location, which would leave
        # a placed result's position in authoring units while its parts -- baked
        # first, then scaled -- move to millimetres.
        shape = result if export_scale == 1.0 else result.scale(export_scale, about=(0, 0, 0))
        # Each part is scaled after its placement is baked in, the way the whole
        # shape above is: about the world origin.
        tree = _read_tree(result, export_scale)
        summary["parts"] = tree.as_payload()["parts"] if tree is not None else None
        parts = _parts_to_export(shape, tree)
        # A request asks for the measurement, whatever the flag says. A build of
        # one part has no relation to measure and is left alone, unless an order
        # was declared for it: whether that order is this build's is then for
        # the measurement to say, where saying nothing would leave the caller
        # to take silence for agreement.
        declares = request is not None and request.order is not None
        if (check_assembly or request is not None) and (len(parts) > 1 or declares):
            from dataclasses import asdict  # noqa: PLC0415 - lazy, as the exporters are

            from cadless.assembly_measure import measure_assembly  # noqa: PLC0415

            budget = measurement_budget(
                wall_secs,
                time.monotonic() - _STARTED,
                None if request is None else request.time_budget,
            )
            # Handed on only where it was asked, so a measurement nothing was
            # asked of is called as it always was.
            asked: dict = {}
            if declares:
                asked.update(order=request.order, releases=request.releases, binds=request.binds)
            if request is not None and request.probe_budget is not None:
                asked["probe_budget"] = request.probe_budget
            # The names the tree gave, so what the measurement could not
            # establish is worded as everything else a reader is shown.
            names = [part.name for part in tree.parts] if tree is not None else None
            summary["assembly"] = asdict(
                measure_assembly(
                    parts, time_budget=budget, names=names, scale=export_scale, **asked
                )
            )
        if export_dir:
            from pathlib import Path  # noqa: PLC0415 - lazy, as below

            from cadless import exporters  # lazy: only when export requested

            registry = exporters.EXPORTERS
            asked = registry if kinds is None else {kind: registry[kind] for kind in kinds}
            # A kind the caller did not ask for is cleared all the same: the
            # directory is reused, and whoever reads it next files what it finds
            # with this build. Nothing to do where every kind is written, each
            # being cleared below as it always was.
            for kind in registry:
                if kind not in asked:
                    _clear_previous(export_dir, kind)
            for kind, export in asked.items():
                _clear_previous(export_dir, kind)
                paths = [
                    export(part, export_dir, exporters.part_name(i, len(parts)))
                    for i, part in enumerate(parts)
                ]
                # The scalar stays the first part. For a one-solid build that is
                # the whole model, which is what every reader of it was written
                # against; for an assembly it is a *fragment*, so anything that
                # renders or judges the one file is looking at part 0 alone.
                # Said here because nothing in the name says it. Multiplicity is
                # read off the directory rather than carried beside this: a
                # second field would be a second place for the two to disagree.
                summary[f"{kind}_path"] = paths[0]
            # By exact name: ``assembly.json`` beside them is the pipeline's
            # motion file, not this build's to clear.
            for name in exporters.ASSEMBLY_FILES.values():
                stale = Path(export_dir) / name
                if stale.is_symlink() or stale.exists():
                    stale.unlink()
            # A whole file that cannot be written fails the build, as a part file
            # would, rather than reporting a build of several parts with its
            # whole files missing and nothing said.
            if tree is not None and len(tree.parts) > 1:
                whole = exporters.export_assembly(tree, export_dir, kinds)
                for kind, path in whole.items():
                    summary[f"{kind}_path"] = path
    except BaseException as exc:  # noqa: BLE001
        _emit(_error_payload(exc, code, prefix="post-process: "))
        return 1

    _emit({"ok": True, **summary})
    return 0


if __name__ == "__main__":
    _result_stream = _keep_stdout_for_the_result()
    sys.exit(main(sys.argv))
