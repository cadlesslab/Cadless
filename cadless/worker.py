"""Resource/time-limited execution worker.

Runs generated build123d code in a **fresh subprocess** with CPU, memory, and
wall-clock limits, capturing exceptions and timeouts. This is the PoC isolation
boundary at the process level; the container adds network/FS isolation.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from cadless._worker_child import SENTINEL
from cadless.assembly_check import AssemblyMeasurements
from cadless.assembly_tree import parts_record
from cadless.config import Settings, settings
from cadless.print_fit import _dimensions, _number


@dataclass
class RepairContext:
    """Structured, line-anchored failure context for the repair loop.

    Richer than the flat ``error`` string: it carries the exception type, its
    message, the generated-script source line the failure maps back to, and the
    full formatted traceback so the repair prompt can point the model at the
    exact offending line of deep OCCT failures.
    """

    error_type: str
    message: str
    offending_line: str | None = None
    last_traceback: str | None = None


@dataclass
class ExecResult:
    ok: bool
    error: str | None = None
    timed_out: bool = False
    volume: float | None = None
    bbox: tuple[float, float, float] | None = None
    # Cheap geometry metrics for deterministic post-condition assertions.
    # ``part_count`` is the number of disjoint solids; ``manifold`` is whether the
    # result is a single closed (watertight) shell; both default conservatively.
    # ``min_wall_thickness`` is None at PoC scale (not cheaply computable via OCCT)
    # so thickness assertions are skipped rather than spuriously failed.
    part_count: int | None = None
    manifold: bool | None = None
    min_wall_thickness: float | None = None
    step_path: str | None = None
    glb_path: str | None = None
    stl_path: str | None = None
    obj_path: str | None = None
    # How the parts of a multi-part result relate to each other, measured only
    # when the caller asked for it. ``None`` means nothing was measured, which is
    # not the same as "measured and found nothing wrong".
    assembly: AssemblyMeasurements | None = None
    # One entry per part, in part order: ``{"index", "name", "path", "color"}``,
    # as the build's tree names them. ``None`` where the build could not be read
    # as a tree, the worker that ran it does not report parts, or the list it
    # reported is not one the store would keep -- which is not the same as one
    # part.
    parts: list[dict] | None = None
    # The whole build as one file of each; ``None`` where the child wrote none.
    assembly_glb_path: str | None = None
    assembly_step_path: str | None = None
    repair_context: RepairContext | None = None


def _repair_context_from_payload(
    payload: dict, traceback_key: str = "traceback"
) -> RepairContext | None:
    """Build a RepairContext from a child/worker failure payload, if structured.

    Key by key, as the rest of a result is read: unpacked, a key this build does
    not know raised out of the call, and a field of the wrong type was handed
    on to a repair prompt that reads every one as text.
    """
    error_type = payload.get("error_type")
    if not isinstance(error_type, str):
        return None
    return RepairContext(
        error_type=error_type,
        message=_text(payload.get("message")) or "",
        offending_line=_text(payload.get("offending_line")),
        last_traceback=_text(payload.get(traceback_key)),
    )


def _read_result(payload: dict, repair_context: RepairContext | None) -> ExecResult:
    """The result a child printed or the worker service sent, every field checked.

    The generated script runs in the child and owns its stdout, so the line read
    back is whatever the script chose to print; and a worker on another engine
    build sends shapes this one never wrote. Handed on as it came, a field of the
    wrong shape raised here, or passed downstream as a reading -- a box read a
    letter to a side, a count or a flag in text -- so a value that is not what
    its field holds is read as ``None``, not measured, instead.

    Except the volume and the box, which every success the child reports
    carries: one without them is no success. Read as not measured, a lone part
    on a turn held to the bed was handed over without being held to it.
    """
    if payload.get("ok") is not True:
        return ExecResult(
            ok=False,
            error=_text(payload.get("error")) or "unknown error",
            timed_out=payload.get("timed_out") is True,
            repair_context=repair_context,
        )
    volume, bbox = _amount(payload.get("volume")), _dimensions(payload.get("bbox"))
    if volume is None or bbox is None:
        return ExecResult(
            ok=False, error="the build reported no volume and bounding box that can be read"
        )
    return ExecResult(
        ok=True,
        volume=volume,
        bbox=bbox,
        part_count=_count(payload.get("part_count")),
        manifold=_flag(payload.get("manifold")),
        min_wall_thickness=_amount(payload.get("min_wall_thickness")),
        step_path=_text(payload.get("step_path")),
        glb_path=_text(payload.get("glb_path")),
        stl_path=_text(payload.get("stl_path")),
        obj_path=_text(payload.get("obj_path")),
        assembly=AssemblyMeasurements.from_payload(payload.get("assembly")),
        parts=parts_record(payload.get("parts")),
        assembly_glb_path=_text(payload.get("assembly_glb_path")),
        assembly_step_path=_text(payload.get("assembly_step_path")),
    )


def _amount(value: object) -> float | None:
    """``value`` where it is a finite number no smaller than zero, else ``None``."""
    number = _number(value)
    return number if number is not None and math.isfinite(number) and number >= 0.0 else None


def _count(value: object) -> int | None:
    """``value`` where it is a whole number no smaller than zero, else ``None``."""
    return value if type(value) is int and value >= 0 else None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _limit_resources(cpu_secs: int):
    """preexec_fn: cap CPU seconds in the child (POSIX).

    Note: we deliberately do NOT set RLIMIT_AS. OCCT reserves a very large
    virtual address space, so an AS cap makes it thrash rather than fail cleanly.
    Real *memory* limiting is enforced at the container level (cgroup mem_limit)
    in the deployment epic; at the process level we rely on the CPU
    rlimit plus the wall-clock timeout below.
    """

    def _apply():
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_secs, cpu_secs))

    return _apply


def run_code(
    code: str,
    *,
    export_dir: str | None = None,
    export_scale: float = 1.0,
    check_assembly: bool = False,
    config: Settings | None = None,
) -> ExecResult:
    """Execute `code` in an isolated subprocess and return an ExecResult.

    ``export_scale`` scales the shape at export time only (authoring units ->
    millimetres, from the catalog domain registry); the returned geometry
    summary (volume/bbox) always stays in authoring units.

    ``check_assembly`` asks the child to measure how the parts of a multi-part
    result relate to each other. Off by default, so a caller that only wants the
    files does not spend the build's wall clock on a measurement nobody reads.
    """
    cfg = config or settings
    if cfg.worker_url:
        return _run_remote(code, export_dir, export_scale, check_assembly, cfg)
    wall = cfg.exec_timeout_secs
    cpu = max(1, int(wall) + 1)  # CPU limit slightly above wall-clock

    with tempfile.TemporaryDirectory() as tmp:
        code_file = os.path.join(tmp, "model.py")
        with open(code_file, "w") as fh:
            fh.write(code)

        cmd = [
            sys.executable,
            "-m",
            "cadless._worker_child",
            code_file,
            export_dir or "",
            str(export_scale),
            "1" if check_assembly else "",
            str(wall),
        ]
        preexec = _limit_resources(cpu) if os.name == "posix" else None
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=wall,
                preexec_fn=preexec,  # noqa: PLW1509 - intentional resource limiting
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                ok=False, timed_out=True, error=f"execution exceeded {wall:g}s wall-clock limit"
            )

        payload = _parse_payload(proc.stdout)
        if payload is None:
            stderr = (proc.stderr or "").strip()[-500:]
            killed = proc.returncode and proc.returncode < 0
            msg = "killed (resource limit)" if killed else (stderr or "no result emitted")
            return ExecResult(ok=False, error=msg, timed_out=killed)
        return _read_result(payload, _repair_context_from_payload(payload))


def _run_remote(
    code: str,
    export_dir: str | None,
    export_scale: float,
    check_assembly: bool,
    cfg: Settings,
) -> ExecResult:
    """Delegate execution to the isolated worker service over HTTP.

    Both api and worker share the artifacts volume at the same path, so the worker
    writes STEP/GLB into `export_dir` and the api reads them back.
    """
    import urllib.request

    payload = json.dumps(
        {
            "code": code,
            "export_dir": export_dir,
            "export_scale": export_scale,
            "check_assembly": check_assembly,
            "timeout": cfg.exec_timeout_secs,
        }
    ).encode()
    req = urllib.request.Request(
        cfg.worker_url.rstrip("/") + "/run",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.exec_timeout_secs + 15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 - network/worker failure
        return ExecResult(ok=False, error=f"worker unreachable: {exc}")
    if not isinstance(data, dict):
        return ExecResult(ok=False, error="the worker's reply is not a result")
    rc = data.get("repair_context")
    return _read_result(
        data,
        _repair_context_from_payload(rc, "last_traceback") if isinstance(rc, dict) else None,
    )


def _parse_payload(stdout: str) -> dict | None:
    """The child's result line as an object, or ``None`` where there is none.

    Anything the parser raises on is none either: the line is the generated
    script's to write, and one nested past the parser's depth, or holding an
    integer past its length, raised out of the call rather than failing it.
    """
    for line in reversed(stdout.splitlines()):
        if line.startswith(SENTINEL):
            try:
                payload = json.loads(line[len(SENTINEL) :].strip())
            except (ValueError, RecursionError):
                return None
            return payload if isinstance(payload, dict) else None
    return None
