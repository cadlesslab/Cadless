"""Isolated execution-worker HTTP service.

Runs in the worker container (no network egress, cgroup-limited). Receives
build123d code from the api, executes it locally, writes STEP/GLB into the shared
artifacts volume, and returns a geometry summary. It never calls Bedrock.

Run: uvicorn worker.service:app --host 0.0.0.0 --port 9000
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI
from pydantic import BaseModel

from cadless.config import Settings
from cadless.worker import run_code

app = FastAPI(title="Cadless Worker", version="1.0.0")


class RunRequest(BaseModel):
    code: str
    export_dir: str | None = None
    export_scale: float = 1.0  # authoring units -> mm, applied to exports only
    timeout: float | None = None


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
    result = run_code(
        req.code, export_dir=req.export_dir, export_scale=req.export_scale, config=cfg
    )
    return asdict(result)
