"""Artifact serving: STEP/STL/OBJ download + GLB fetch."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
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


async def _resolve(store: ScopedStore, version_id: int, kind: str) -> str:
    artifact = await store.get_artifact(version_id, kind)
    if not artifact or not os.path.exists(artifact.path):
        raise HTTPException(status_code=404, detail=f"{kind} artifact not found")
    return artifact.path


async def _download(version_id: int, kind: str, store: ScopedStore) -> FileResponse:
    path = await _resolve(store, version_id, kind)
    return FileResponse(path, media_type=_MEDIA[kind], filename=f"model_{version_id}.{kind}")


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
    path = await _resolve(store, version_id, "glb")
    # inline so the three.js viewport can fetch it directly
    return FileResponse(path, media_type=_MEDIA["glb"], content_disposition_type="inline")


@router.get("/thumbnail")
async def get_thumbnail(version_id: int, store: ScopedStore = Depends(get_store)):
    """The baked catalog thumbnail PNG (#21), inline for <img> tags."""
    path = await _resolve(store, version_id, "thumbnail")
    return FileResponse(path, media_type=_MEDIA["thumbnail"], content_disposition_type="inline")
