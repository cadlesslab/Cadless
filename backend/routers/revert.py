"""Explicit revert API: the Pillar 4 safety net Blueprint mode relies on.

``POST /projects/{id}/revert`` makes today's implicit "current = last good"
behavior explicit and testable. Reverts the project's ``current_version_id`` to a
target version; when the target is omitted, it reverts to the project's LAST OK
version. This pairs with the chat turn-settlement auto-revert policy
(:func:`backend.routers.chat._revert_to_last_ok`), which guarantees the same
invariant after a failed/aborted turn.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.catalog_state import reject_if_catalog
from backend.deps import get_store
from backend.schemas import VersionOut
from cadless.scoped_store import ScopedStore

router = APIRouter(tags=["revert"])


class RevertRequest(BaseModel):
    # Optional target version. When omitted, revert to the project's last OK version.
    version_id: int | None = None


@router.post("/projects/{project_id}/revert", response_model=VersionOut)
async def revert(project_id: int, body: RevertRequest, store: ScopedStore = Depends(get_store)):
    """Revert the project's current version to a target (or its last OK version).

    - 404 if the project does not exist.
    - 403 if the project is a catalog item (read-only, like rerun/reparametrize).
    - 400 if an explicit target version does not exist, belongs to another
      project, or is not OK (not current-eligible).
    - 400 if no target is given and the project has no OK version to fall back to.
    """
    if not await store.get_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    await reject_if_catalog(store, project_id, "change which version is current")

    if body.version_id is not None:
        target = await store.get_version(body.version_id)
        if target is None or target.project_id != project_id:
            raise HTTPException(status_code=400, detail="target version not found for this project")
        if not target.ok:
            raise HTTPException(status_code=400, detail="target version is not OK / not revertible")
    else:
        target = await store.last_ok_version(project_id)
        if target is None:
            raise HTTPException(status_code=400, detail="no OK version to revert to")

    await store.set_current_version(project_id, target.id)
    return VersionOut.of(target, await store.list_artifacts(target.id))
