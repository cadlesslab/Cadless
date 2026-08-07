"""Generation endpoint: NL prompt -> pipeline -> persisted version.

POST /projects/{id}/generate runs the full generate->validate->execute->repair
pipeline, stores a new ScriptVersion plus its STEP/GLB artifacts, and (on success)
marks it the project's current version.
"""

from __future__ import annotations

import asyncio
import functools
import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from backend.catalog_state import reject_if_catalog
from backend.deps import get_store
from backend.schemas import VersionOut
from backend.sse import SSE_HEADERS
from cadless.exporters import EXPORTERS
from cadless.pipeline import generate_cad  # monkeypatched in tests
from cadless.scoped_store import ScopedStore
from cadless.store import ScriptVersion

router = APIRouter(tags=["generation"])


class GenerateRequest(BaseModel):
    """Fresh generation (``prompt``) or refinement of a prior version.

    Refinement mode is selected by supplying ``prior_version_id`` together with
    ``delta_prompt`` (the change request, e.g. "make the hole 8 mm").
    """

    prompt: str | None = Field(default=None, min_length=1)
    prior_version_id: int | None = None
    delta_prompt: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_a_mode(self) -> GenerateRequest:
        if self.prior_version_id is not None:
            if not self.delta_prompt:
                raise ValueError("delta_prompt is required when prior_version_id is set")
        elif not self.prompt:
            raise ValueError("prompt is required")
        return self


class GenerateResponse(BaseModel):
    ok: bool
    attempt_count: int
    version: VersionOut


async def persist_generation(
    store: ScopedStore,
    project_id: int,
    intent: str,
    on_progress=None,
    *,
    prior_code: str | None = None,
    parent_version_id: int | None = None,
) -> tuple[ScriptVersion, int]:
    """Run the pipeline (in a worker thread) and persist version + artifacts.

    ``intent`` is the request that produced this version — the user prompt for a
    fresh generation, or the delta instruction for a refinement (``prior_code``
    set). Exports go to a staging dir under the shared artifacts volume (not a
    local tempdir) so the isolated worker container and the api see the same files.
    """
    # Persist the in-flight conversation turn up-front so a reload mid-generation
    # shows the pending turn (the assistant message settles to ok/error below).
    session = await store.get_or_create_session(project_id)
    await store.add_message(session.id, "user", intent)
    assistant = await store.add_message(session.id, "assistant", None, status="pending")

    staging = Path(store.artifacts_dir) / "_staging" / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    try:
        result = await run_in_threadpool(
            functools.partial(
                generate_cad, intent, str(staging), on_progress=on_progress, prior_code=prior_code
            )
        )
        version = await store.add_version(
            project_id,
            intent,
            result.code,
            result.ok,
            result.error,
            result.volume,
            result.bbox,
            parameters=result.parameters,
            parent_version_id=parent_version_id,
        )
        if result.ok:
            dest = store.version_artifact_dir(version.id)
            for kind in EXPORTERS:
                src = getattr(result, f"{kind}_path", None)
                if src and Path(src).exists():
                    target = Path(dest) / f"model.{kind}"
                    shutil.copy(src, target)
                    await store.add_artifact(version.id, kind, str(target))
            await store.set_current_version(project_id, version.id)
            await store.update_message(assistant.id, status="ok", version_id=version.id)
        else:
            await store.update_message(
                assistant.id, status="error", error=result.error, version_id=version.id
            )
    except Exception as exc:  # noqa: BLE001
        # Unexpected failure before a version was persisted: settle the dangling
        # pending turn to error so a reload shows the failure (SSE `error` path).
        await store.update_message(assistant.id, status="error", error=str(exc))
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return version, result.attempt_count


async def _resolve_refinement(store: ScopedStore, project_id: int, prior_version_id: int) -> str:
    """Validate the refinement source and return its code (raises HTTPException)."""
    prior = await store.get_version(prior_version_id)
    if not prior or prior.project_id != project_id:
        raise HTTPException(status_code=404, detail="prior version not found")
    if not prior.code:
        raise HTTPException(status_code=400, detail="prior version has no code to refine")
    return prior.code


@router.post("/projects/{project_id}/generate", response_model=GenerateResponse)
async def generate(project_id: int, body: GenerateRequest, store: ScopedStore = Depends(get_store)):
    if not await store.get_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    await reject_if_catalog(store, project_id, "generate new versions")
    intent, prior_code, parent_id = body.prompt, None, None
    if body.prior_version_id is not None:
        prior_code = await _resolve_refinement(store, project_id, body.prior_version_id)
        intent, parent_id = body.delta_prompt, body.prior_version_id
    version, attempts = await persist_generation(
        store, project_id, intent, prior_code=prior_code, parent_version_id=parent_id
    )
    artifacts = await store.list_artifacts(version.id)
    return GenerateResponse(
        ok=version.ok, attempt_count=attempts, version=VersionOut.of(version, artifacts)
    )


@router.get("/projects/{project_id}/generate/stream")
async def generate_stream(
    project_id: int,
    prompt: str | None = None,
    prior_version_id: int | None = None,
    delta_prompt: str | None = None,
    store: ScopedStore = Depends(get_store),
):
    """SSE: stream per-attempt progress, ending with a `done` event (version id).

    Mirrors POST /generate: pass ``prompt`` for a fresh build, or
    ``prior_version_id`` + ``delta_prompt`` to refine an existing version.
    """
    if not await store.get_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    await reject_if_catalog(store, project_id, "generate new versions")
    intent, prior_code, parent_id = prompt, None, None
    if prior_version_id is not None:
        if not delta_prompt:
            raise HTTPException(
                status_code=422, detail="delta_prompt is required when prior_version_id is set"
            )
        prior_code = await _resolve_refinement(store, project_id, prior_version_id)
        intent, parent_id = delta_prompt, prior_version_id
    elif not prompt:
        raise HTTPException(status_code=422, detail="prompt is required")

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_progress(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    async def run() -> None:
        try:
            version, attempts = await persist_generation(
                store,
                project_id,
                intent,
                on_progress,
                prior_code=prior_code,
                parent_version_id=parent_id,
            )
            on_progress(
                {
                    "event": "done",
                    "version_id": version.id,
                    "ok": version.ok,
                    "attempt_count": attempts,
                }
            )
        except Exception as exc:  # noqa: BLE001
            on_progress({"event": "error", "detail": str(exc)})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

    task = asyncio.create_task(run())

    async def events():
        while True:
            event = await queue.get()
            if event is None:
                break
            yield {"data": json.dumps(event)}
        await task

    return EventSourceResponse(events(), headers=SSE_HEADERS)
