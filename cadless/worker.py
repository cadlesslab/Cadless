"""Resource/time-limited execution worker.

Runs generated build123d code in a **fresh subprocess** with CPU, memory, and
wall-clock limits, capturing exceptions and timeouts. This is the PoC isolation
boundary at the process level; the container adds network/FS isolation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from cadless._worker_child import SENTINEL
from cadless.config import Settings, settings


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
    repair_context: RepairContext | None = None


def _repair_context_from_payload(payload: dict) -> RepairContext | None:
    """Build a RepairContext from a child/worker failure payload, if structured."""
    if "error_type" not in payload:
        return None
    return RepairContext(
        error_type=payload["error_type"],
        message=payload.get("message", ""),
        offending_line=payload.get("offending_line"),
        last_traceback=payload.get("traceback"),
    )


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
    config: Settings | None = None,
) -> ExecResult:
    """Execute `code` in an isolated subprocess and return an ExecResult.

    ``export_scale`` scales the shape at export time only (authoring units ->
    millimetres, from the catalog domain registry); the returned geometry
    summary (volume/bbox) always stays in authoring units.
    """
    cfg = config or settings
    if cfg.worker_url:
        return _run_remote(code, export_dir, export_scale, cfg)
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

        if not payload.get("ok"):
            return ExecResult(
                ok=False,
                error=payload.get("error", "unknown error"),
                repair_context=_repair_context_from_payload(payload),
            )

        bbox = payload.get("bbox")
        return ExecResult(
            ok=True,
            volume=payload.get("volume"),
            bbox=tuple(bbox) if bbox else None,
            part_count=payload.get("part_count"),
            manifold=payload.get("manifold"),
            min_wall_thickness=payload.get("min_wall_thickness"),
            step_path=payload.get("step_path"),
            glb_path=payload.get("glb_path"),
            stl_path=payload.get("stl_path"),
            obj_path=payload.get("obj_path"),
        )


def _run_remote(
    code: str, export_dir: str | None, export_scale: float, cfg: Settings
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
    bbox = data.get("bbox")
    rc = data.get("repair_context")
    return ExecResult(
        ok=data.get("ok", False),
        error=data.get("error"),
        timed_out=data.get("timed_out", False),
        volume=data.get("volume"),
        bbox=tuple(bbox) if bbox else None,
        part_count=data.get("part_count"),
        manifold=data.get("manifold"),
        min_wall_thickness=data.get("min_wall_thickness"),
        step_path=data.get("step_path"),
        glb_path=data.get("glb_path"),
        stl_path=data.get("stl_path"),
        obj_path=data.get("obj_path"),
        repair_context=RepairContext(**rc) if isinstance(rc, dict) else None,
    )


def _parse_payload(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith(SENTINEL):
            try:
                return json.loads(line[len(SENTINEL) :].strip())
            except json.JSONDecodeError:
                return None
    return None
