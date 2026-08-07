"""Child process that executes one generated build123d script.

Invoked as a subprocess by :mod:`cadless.worker`. Reads a code file, executes
it, computes a geometry summary, optionally exports artifacts, and prints a single
``__VTRESULT__ {json}`` line to stdout. Kept dependency-light and side-effect free
apart from the optional export.

Run: python -m cadless._worker_child <code_file> [<export_dir>] [<export_scale>]
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
        if export_dir:
            from cadless import exporters  # lazy: only when export requested

            shape = result if export_scale == 1.0 else result.scale(export_scale)
            for kind, export in exporters.EXPORTERS.items():
                summary[f"{kind}_path"] = export(shape, export_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"{SENTINEL} " + json.dumps(_error_payload(exc, code, prefix="post-process: ")))
        return 1

    print(f"{SENTINEL} " + json.dumps({"ok": True, **summary}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
