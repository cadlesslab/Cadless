"""Project CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.catalog_state import reject_if_catalog
from backend.deps import get_store
from backend.schemas import ProjectCreate, ProjectOut, ProjectRename
from cadless.scoped_store import ScopedStore
from cadless.store import Project

router = APIRouter(prefix="/projects", tags=["projects"])


async def _project_out(store: ScopedStore, project: Project) -> ProjectOut:
    """Serialize a project, resolving its clone provenance (#22).

    ``derived_from_project_id`` is stored on the row; the source's display name
    and catalog id are resolved here (not persisted) so they stay correct after
    renames/reloads. A deleted source degrades gracefully to id-only provenance.

    Whether this is a catalog item, and which item a clone came from, both come
    off project rows. This used to read the sidecar ledger, which put
    a JSON file parse on every project response — including the list, once per
    response — and made an unreadable copy of that file a 500 for routes that have
    nothing to do with the catalog. The source project is fetched here regardless,
    for its name, so its mark comes along at no extra cost.
    """
    name = catalog_id = None
    if project.derived_from_project_id is not None:
        source = await store.get_project(project.derived_from_project_id)
        if source is not None:
            name = source.name
            catalog_id = source.catalog_item_id
    return ProjectOut.of(
        project,
        is_catalog=project.catalog_item_id is not None,
        derived_from_name=name,
        derived_from_catalog_id=catalog_id,
    )


class BranchRequest(BaseModel):
    """Selects the source version to fork into a new line; optional new line name."""

    version_id: int
    name: str | None = None


class CloneRequest(BaseModel):
    """Optional name for the deep-cloned project copy."""

    name: str | None = None


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, store: ScopedStore = Depends(get_store)):
    return ProjectOut.of(await store.create_project(body.name))


@router.post("/{project_id}/clone", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def clone_project(
    project_id: int, body: CloneRequest, store: ScopedStore = Depends(get_store)
):
    """Deep-copy a project (full chat history + every version's code/artifacts) into
    a new, editable project. This is the Customize-from-catalog action (#22): the
    clone is absent from the catalog ledger (so its params are editable), records
    ``derived_from`` provenance, and — because the copied chat history carries the
    catalog build transcript — its first chat turn already sees the baseline's
    design context (see ``ScopedStore.clone_project``). No LLM call happens here; the
    source project is untouched."""
    clone = await store.clone_project(project_id, name=body.name)
    if clone is None:
        raise HTTPException(status_code=404, detail="project not found")
    return await _project_out(store, clone)


@router.get("", response_model=list[ProjectOut])
async def list_projects(store: ScopedStore = Depends(get_store)):
    return [await _project_out(store, p) for p in await store.list_projects()]


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: int, store: ScopedStore = Depends(get_store)):
    project = await store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return await _project_out(store, project)


@router.patch("/{project_id}", response_model=ProjectOut)
async def rename_project(
    project_id: int, body: ProjectRename, store: ScopedStore = Depends(get_store)
):
    # 404 ahead of 403, as the other gated routes do. The rename is what reported
    # a missing project before, which put it after the mutation it guards.
    if not await store.get_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    # A renamed catalog item reads differently depending on where you look: the
    # catalog listing prefers the ledger's name, the project list shows this one.
    await reject_if_catalog(store, project_id, "rename it")
    project = await store.rename_project(project_id, body.name)
    if not project:  # deleted between the check and here
        raise HTTPException(status_code=404, detail="project not found")
    return await _project_out(store, project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: int, store: ScopedStore = Depends(get_store)):
    # 404 ahead of 403, and both ahead of the delete. `store.delete_project` is
    # what reported a missing project before — by the time it could, it had
    # already cascaded the versions away and rmtree'd their artifacts.
    if not await store.get_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    # Not recoverable: the ledger entry outlives the project it names, and a load
    # that finds an unchanged entry skips the item, so a restart does not bring a
    # deleted catalog item back. Clearing it from the catalog takes the entry too.
    await reject_if_catalog(
        store, project_id, "delete it", remedy="Clear it from the catalog to remove it."
    )
    if not await store.delete_project(project_id):  # deleted between the check and here
        raise HTTPException(status_code=404, detail="project not found")


@router.post("/{project_id}/branch", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def branch_project(
    project_id: int, body: BranchRequest, store: ScopedStore = Depends(get_store)
):
    """Fork a prior version into a brand-new project (an alternative line).

    The new line is seeded from the selected version's code/params (its starting and
    current model) and records the branch origin; the original project is unchanged.
    """
    if not await store.get_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    source = await store.get_version(body.version_id)
    if source is None or source.project_id != project_id:
        raise HTTPException(status_code=404, detail="version not found")
    branched = await store.branch_project(body.version_id, name=body.name)
    return ProjectOut.of(branched)
