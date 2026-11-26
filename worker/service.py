"""Isolated execution-worker HTTP service.

Runs in the worker container (no network egress, cgroup-limited). Receives
build123d code from the api, executes it locally, writes the run's files into the
shared artifacts volume, and returns a geometry summary. It never calls Bedrock.

Run: uvicorn worker.service:app --host 0.0.0.0 --port 9000
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

import cadless
from cadless.config import Settings
from cadless.worker import run_code

app = FastAPI(title="Cadless Worker", version=cadless.__version__)


class RunRequest(BaseModel):
    code: str
    export_dir: str | None = None
    export_scale: float = 1.0  # authoring units -> mm, applied to exports only
    check_assembly: bool = False  # measure how a multi-part result's parts relate
    timeout: float | None = None
    # What the caller asks of that measurement, and the export kinds it asks for.
    # Typed as anything, so that run_code reads both and answers for what it
    # cannot use. Typed as what each should be, a wrong shape was answered here
    # before run_code saw it, with all of what was sent repeated back.
    check: Any = None
    kinds: Any = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/run")
def run(req: RunRequest) -> dict:
    # Force local execution here (worker_url empty) regardless of ambient env,
    # so the worker never tries to delegate to itself.
    cfg = Settings(worker_url="")
    if req.timeout:
        cfg = cfg.model_copy(update={"exec_timeout_secs": req.timeout})
    # Each handed on only where it was sent, so a run that asks nothing is
    # started as it always was. Sent and empty is still sent.
    asked = {
        name: getattr(req, name) for name in ("check", "kinds") if getattr(req, name) is not None
    }
    result = run_code(
        req.code,
        export_dir=req.export_dir,
        export_scale=req.export_scale,
        check_assembly=req.check_assembly,
        config=cfg,
        **asked,
    )
    return asdict(result)
