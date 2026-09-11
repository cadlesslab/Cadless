"""Artifact serving: STEP/STL/OBJ download, GLB and thumbnail fetch.

A version can hold several files of one kind — the pieces of a model too big to
print whole — so each kind has a fixed route that serves the first of them and
there is one numbered route for reaching any particular piece.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import FileResponse

from backend.deps import get_store
from cadless.scoped_store import ScopedStore

router = APIRouter(prefix="/versions/{version_id}/artifacts", tags=["artifacts"])

_MEDIA = {
    "step": "application/step",
    "glb": "model/gltf-binary",
    "stl": "model/stl",
    "obj": "model/obj",
    "thumbnail": "image/png",
}


#: Kinds a browser consumes in place rather than saving: the viewport loads a
#: mesh and an ``<img>`` tag loads a thumbnail. Named here rather than left to
#: each route, so the part route below serves the same kind the same way.
_INLINE = {"glb", "thumbnail"}


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
    # inline so the three.js viewport can fetch it directly
    return await _download(version_id, "glb", store)


@router.get("/thumbnail")
async def get_thumbnail(version_id: int, store: ScopedStore = Depends(get_store)):
    """The baked catalog thumbnail PNG (#21), inline for <img> tags."""
    return await _download(version_id, "thumbnail", store)


@router.get("/{kind}/{part}")
async def get_part(
    version_id: int,
    kind: str,
    # Bounded, because the value is bound as a SQLite integer and one too large
    # to fit raises out of the driver rather than answering — a server error for
    # what is really a malformed address. The floor is the ordinal's own: parts
    # are counted from 0, so a negative one is a bad request, not a miss.
    part: int = Path(ge=0, lt=2**63),
    store: ScopedStore = Depends(get_store),
):
    """One numbered file of a kind, for a model that comes in several pieces.

    Two segments, so it shadows none of the fixed routes above — but unlike
    them, ``kind`` arrives from the URL. Reading its media type straight out of
    the table would raise on a typo instead of answering, which turns a mistyped
    address into a server error, so an unknown kind is refused as not found.
    """
    if kind not in _MEDIA:
        raise HTTPException(status_code=404, detail=f"unknown artifact kind {kind!r}")
    artifact = await store.get_artifact_part(version_id, kind, part)
    if not artifact or not os.path.exists(artifact.path):
        raise HTTPException(status_code=404, detail=f"{kind} part {part} not found")
    return _serve(artifact.path, kind, f"model_{version_id}_p{part}.{kind}")
