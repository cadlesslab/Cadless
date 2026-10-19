"""Artifact serving: STEP/STL/OBJ download, GLB, thumbnail and assembly fetch."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam
from fastapi.responses import FileResponse

from backend.deps import get_store
from cadless.exporters import ASSEMBLY_FILES
from cadless.scoped_store import ScopedStore

router = APIRouter(prefix="/versions/{version_id}/artifacts", tags=["artifacts"])

_MEDIA = {
    "step": "application/step",
    "glb": "model/gltf-binary",
    "stl": "model/stl",
    "obj": "model/obj",
    "thumbnail": "image/png",
    # A drawing of how the parts go together. Served like a thumbnail and, like
    # one, absent from EXPORTERS: it is not a format the model was exported to.
    "guide": "image/png",
    # The order and headings an assembly is played from. Data for the viewport
    # rather than a file for the user, and for the same reason as the guide not
    # an exported format.
    "assembly": "application/json",
    # The whole build as one file of each. One per build rather than one per
    # part, so not in EXPORTERS either, and not served by part number below.
    "assembly_glb": "model/gltf-binary",
    "assembly_step": "application/step",
}


#: Kinds a browser consumes in place rather than saving: the viewport loads a
#: mesh and reads an assembly's motion, and an ``<img>`` tag loads a thumbnail.
#: Named here rather than left to each route, so every route serves a given
#: kind the same way.
_INLINE = {"glb", "thumbnail", "guide", "assembly", "assembly_glb"}

#: The largest value SQLite stores as an integer. Anything above it raises out of
#: the driver at bind time rather than answering, so it is refused at the edge.
_SQLITE_MAX_INT = 2**63


def _serve(path: str, kind: str, filename: str) -> FileResponse:
    if kind in _INLINE:
        return FileResponse(path, media_type=_MEDIA[kind], content_disposition_type="inline")
    return FileResponse(path, media_type=_MEDIA[kind], filename=filename)


async def _resolve(store: ScopedStore, version_id: int, kind: str) -> str:
    artifact = await store.get_artifact(version_id, kind)
    if not artifact or not os.path.exists(artifact.path):
        raise HTTPException(status_code=404, detail=f"{kind} artifact not found")
    return artifact.path


async def _download(version_id: int, kind: str, store: ScopedStore) -> FileResponse:
    path = await _resolve(store, version_id, kind)
    return _serve(path, kind, f"model_{version_id}.{kind}")


@router.get("/step")
async def get_step(version_id: int, store: ScopedStore = Depends(get_store)):
    return await _download(version_id, "step", store)


@router.get("/stl")
async def get_stl(version_id: int, store: ScopedStore = Depends(get_store)):
    return await _download(version_id, "stl", store)


@router.get("/obj")
async def get_obj(version_id: int, store: ScopedStore = Depends(get_store)):
    return await _download(version_id, "obj", store)


@router.get("/glb")
async def get_glb(version_id: int, store: ScopedStore = Depends(get_store)):
    return await _download(version_id, "glb", store)


@router.get("/thumbnail")
async def get_thumbnail(version_id: int, store: ScopedStore = Depends(get_store)):
    """The baked catalog thumbnail PNG (#21)."""
    return await _download(version_id, "thumbnail", store)


@router.get("/assembly")
async def get_assembly(version_id: int, store: ScopedStore = Depends(get_store)):
    """How a checked assembly's parts go together, as the viewport plays them.

    A fixed address rather than a part of ``/{kind}/{part}``: a version has one
    of these or none, so a part number would only be one more thing to get wrong.
    """
    return await _download(version_id, "assembly", store)


async def _whole(version_id: int, kind: str, suffix: str, store: ScopedStore) -> FileResponse:
    path = await _resolve(store, version_id, kind)
    return _serve(path, kind, f"model_{version_id}_assembly.{suffix}")


@router.get("/assembly_glb")
async def get_assembly_glb(version_id: int, store: ScopedStore = Depends(get_store)):
    """The whole build as one GLB."""
    return await _whole(version_id, "assembly_glb", "glb", store)


@router.get("/assembly_step")
async def get_assembly_step(version_id: int, store: ScopedStore = Depends(get_store)):
    """The whole build as one STEP."""
    return await _whole(version_id, "assembly_step", "step", store)


@router.get("/{kind}/{part}")
async def get_part(
    version_id: int,
    kind: str,
    # The floor is the ordinal's own: parts are counted from 0, so a negative
    # one is a malformed address rather than a miss.
    part: int = PathParam(ge=0, lt=_SQLITE_MAX_INT),
    store: ScopedStore = Depends(get_store),
):
    """One numbered file of a kind, for a model that comes in several pieces.

    Two segments, so it shadows none of the fixed routes above — but unlike
    them, ``kind`` arrives from the URL. Reading its media type straight out of
    the table would raise on a typo instead of answering, which turns a mistyped
    address into a server error, so an unknown kind is refused as not found.

    The whole-build files are refused here too: a version has one of each or
    none, and served by number they would download under a part file's name.
    """
    if kind not in _MEDIA:
        raise HTTPException(status_code=404, detail=f"unknown artifact kind {kind!r}")
    if kind in ASSEMBLY_FILES:
        raise HTTPException(status_code=404, detail=f"{kind} has no numbered parts")
    artifact = await store.get_artifact_part(version_id, kind, part)
    if not artifact or not os.path.exists(artifact.path):
        raise HTTPException(status_code=404, detail=f"{kind} part {part} not found")
    return _serve(artifact.path, kind, f"model_{version_id}_p{part}.{kind}")
