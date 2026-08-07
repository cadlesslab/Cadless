"""Version history API: list / get / re-run / set-current.

The parametric script is the source of truth, so a version can be deterministically
re-executed (re-run) to refresh its artifacts.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.catalog_state import reject_if_catalog
from backend.deps import get_store
from backend.schemas import VersionOut
from cadless.exporters import EXPORTERS
from cadless.params import apply_param_overrides
from cadless.scoped_store import ScopedStore
from cadless.worker import run_code  # monkeypatchable in tests

router = APIRouter(tags=["versions"])


class SetCurrentRequest(BaseModel):
    version_id: int


class RerunResponse(BaseModel):
    ok: bool
    error: str | None = None
    version: VersionOut


class ReparametrizeRequest(BaseModel):
    params: dict[str, Any]


class ReparametrizeResponse(BaseModel):
    ok: bool
    error: str | None = None
    version: VersionOut


async def _version_out(store: ScopedStore, vid: int) -> VersionOut:
    version = await store.get_version(vid)
    return VersionOut.of(version, await store.list_artifacts(vid))


@router.get("/projects/{project_id}/versions", response_model=list[VersionOut])
async def list_versions(project_id: int, store: ScopedStore = Depends(get_store)):
    if not await store.get_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    out = []
    for v in await store.list_versions(project_id):
        out.append(VersionOut.of(v, await store.list_artifacts(v.id)))
    return out


@router.get("/versions/{version_id}", response_model=VersionOut)
async def get_version(version_id: int, store: ScopedStore = Depends(get_store)):
    version = await store.get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="version not found")
    return VersionOut.of(version, await store.list_artifacts(version_id))


@router.get("/versions/{version_id}/candidates", response_model=list[VersionOut])
async def list_candidates(version_id: int, store: ScopedStore = Depends(get_store)):
    """The forge best-of-N losers recorded against a winning version.

    Returns the non-current candidate rows flagged with this version as their
    ``candidate_of_version_id`` (empty for a normal, non-raced version), so a UI
    can show the race behind a winning model.
    """
    if not await store.get_version(version_id):
        raise HTTPException(status_code=404, detail="version not found")
    return [
        VersionOut.of(v, await store.list_artifacts(v.id))
        for v in await store.list_candidate_versions(version_id)
    ]


@router.post("/versions/{version_id}/rerun", response_model=RerunResponse)
async def rerun_version(version_id: int, store: ScopedStore = Depends(get_store)):
    version = await store.get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="version not found")
    # Re-running would re-export in place at scale 1.0, clobbering baked
    # artifacts produced at the domain export_scale (#31).
    await reject_if_catalog(store, version.project_id, "re-run its code")
    if not version.code:
        raise HTTPException(status_code=400, detail="version has no code to re-run")

    dest = store.version_artifact_dir(version_id)
    res = await run_in_threadpool(run_code, version.code, export_dir=dest)
    if res.ok:
        existing = {a.kind for a in await store.list_artifacts(version_id)}
        for kind in EXPORTERS:
            target = Path(dest) / f"model.{kind}"
            if kind not in existing and target.exists():
                await store.add_artifact(version_id, kind, str(target))
    return RerunResponse(ok=res.ok, error=res.error, version=await _version_out(store, version_id))


@router.post("/versions/{version_id}/reparametrize", response_model=ReparametrizeResponse)
async def reparametrize_version(
    version_id: int, body: ReparametrizeRequest, store: ScopedStore = Depends(get_store)
):
    """Re-run a version with overridden dimensions — deterministic, no LLM call.

    The override values are spliced into the version's ``params`` block and the
    edited script is executed via the same sandbox path; the result is persisted
    as a new version under the same project.
    """
    version = await store.get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="version not found")
    await reject_if_catalog(store, version.project_id, "edit its parameters")
    if not version.code:
        raise HTTPException(status_code=400, detail="version has no code to re-run")
    if not version.parameters:
        raise HTTPException(status_code=400, detail="version has no parameters to override")
    try:
        new_code = apply_param_overrides(version.code, body.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    merged = {**version.parameters, **body.params}
    staging = Path(store.artifacts_dir) / "_staging" / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    try:
        res = await run_in_threadpool(run_code, new_code, export_dir=str(staging))
        new_version = await store.add_version(
            version.project_id,
            version.prompt,
            new_code,
            res.ok,
            res.error,
            res.volume,
            res.bbox,
            parameters=merged,
            parent_version_id=version.id,
        )
        if res.ok:
            dest = store.version_artifact_dir(new_version.id)
            for kind in EXPORTERS:
                src = getattr(res, f"{kind}_path", None)
                if src and Path(src).exists():
                    target = Path(dest) / f"model.{kind}"
                    shutil.copy(src, target)
                    await store.add_artifact(new_version.id, kind, str(target))
            await store.set_current_version(version.project_id, new_version.id)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return ReparametrizeResponse(
        ok=res.ok, error=res.error, version=await _version_out(store, new_version.id)
    )


@router.post("/projects/{project_id}/current", response_model=dict)
async def set_current(
    project_id: int, body: SetCurrentRequest, store: ScopedStore = Depends(get_store)
):
    # 404 ahead of 403, as the other gated routes do. A deleted project can
    # outlive its ledger entry (see the stale-entry note in routers/catalog.py),
    # and a project that is gone should read as gone rather than as protected.
    if not await store.get_project(project_id):
        raise HTTPException(status_code=404, detail="project or version not found")
    # Moving current is the one mutation that strands a catalog item's baked
    # thumbnail on a version nothing points at.
    await reject_if_catalog(store, project_id, "change which version is current")
    if not await store.set_current_version(project_id, body.version_id):
        raise HTTPException(status_code=404, detail="project or version not found")
    return {"current_version_id": body.version_id}
