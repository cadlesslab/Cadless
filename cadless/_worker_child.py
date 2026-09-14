"""Child process that executes one generated build123d script.

Invoked as a subprocess by :mod:`cadless.worker`. Reads a code file, executes
it, computes a geometry summary, optionally exports artifacts, and prints a single
``__VTRESULT__ {json}`` line to stdout. Kept dependency-light, and side-effect
free apart from the optional export — which writes one file per solid and first
removes what an earlier build left of that kind in the same directory.

Run: python -m cadless._worker_child <code_file> [<export_dir>] [<export_scale>]
     [<check_assembly>] [<wall_secs>]
"""

from __future__ import annotations

import json
import sys
import traceback

SENTINEL = "__VTRESULT__"

# Filename used when compiling the generated script; lets us pick its frames out
# of a traceback so we can anchor the error on a line of the user's code.
_GENERATED_FILE = "<generated>"


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


def _parts_to_export(shape) -> list:
    """The pieces to write as separate files: the solids, or the shape itself.

    A one-solid build exports the shape it was handed rather than the solid pulled
    back out of it. They are the same geometry, and passing the original through
    keeps a single-part export identical to what it was before parts existed --
    which is what the upgrade path rests on. A shape that cannot be split is
    exported whole for the same reason ``_part_count`` returns ``None`` there:
    the fallback is the old behaviour, not a failure.
    """
    try:
        solids = shape.solids()
    except Exception:  # noqa: BLE001 - unsplittable shapes export whole, as before
        return [shape]
    return list(solids) if len(solids) > 1 else [shape]


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
        if stale.exists():
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
    # Off unless the turn asked for an assembly. A multi-solid result is not by
    # itself an assembly, and measuring the relations between parts nobody will
    # read costs the same wall clock the build is running on.
    check_assembly = bool(len(argv) > 4 and argv[4])
    # The parent's wall clock, so the assembly measurement can take a share of
    # it rather than a fixed number of seconds. A clock that runs out up here
    # returns no summary at all, so the measurement has to stop before it, and
    # a constant only does that at one particular timeout.
    wall_secs = float(argv[5]) if len(argv) > 5 and argv[5] else 0.0
    code = open(code_file).read()  # noqa: S108,SIM115 - trusted path from parent

    ns: dict = {}
    try:
        exec(compile(code, _GENERATED_FILE, "exec"), ns)  # noqa: S102
    except Exception as exc:  # noqa: BLE001
        print(f"{SENTINEL} " + json.dumps(_error_payload(exc, code)))
        return 1

    if "result" not in ns:
        print(f"{SENTINEL} " + json.dumps({"ok": False, "error": "missing `result`"}))
        return 1

    result = ns["result"]
    try:
        summary = _summarise(result)
        if summary["volume"] <= 0:
            print(
                f"{SENTINEL} "
                + json.dumps({"ok": False, "error": "degenerate solid (volume <= 0)"})
            )
            return 1
        # Scaled once, above both readers. The export writes millimetres and the
        # assembly checks are against a build volume in millimetres, so measuring
        # the unscaled result would compare authoring units to millimetres and
        # never refuse a model authored in metres.
        shape = result if export_scale == 1.0 else result.scale(export_scale)
        parts = _parts_to_export(shape)
        if check_assembly and len(parts) > 1:
            from dataclasses import asdict  # noqa: PLC0415 - lazy, as the exporters are

            from cadless import assembly_check  # noqa: PLC0415
            from cadless.assembly_check import measure_assembly  # noqa: PLC0415

            budget = (
                wall_secs * assembly_check.MEASUREMENT_TIME_SHARE
                if wall_secs > 0
                else assembly_check.MEASUREMENT_TIME_BUDGET_SECONDS
            )
            summary["assembly"] = asdict(measure_assembly(parts, time_budget=budget))
        if export_dir:
            from cadless import exporters  # lazy: only when export requested

            for kind, export in exporters.EXPORTERS.items():
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
    except Exception as exc:  # noqa: BLE001
        print(f"{SENTINEL} " + json.dumps(_error_payload(exc, code, prefix="post-process: ")))
        return 1

    print(f"{SENTINEL} " + json.dumps({"ok": True, **summary}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
