"""Printing a version on a 3D printer that is on the local network.

Slicing and sending are separate calls on purpose. Slicing is the step that can
say how long the print will take and how much filament it will eat, and those
are the numbers someone wants before committing a couple of hours of machine
time. So ``/slice`` answers with them and puts nothing on the wire, and
``/send`` is the deliberate second step.

The sliced file is written beside the mesh it came from, in the version's own
artifact directory, and is not registered as an artifact: it is an intermediate
for one device, not a format the project exports. Ownership still holds, because
the directory is reached through the mesh's row on the scoped view -- a version
that is not the caller's never yields a path here.

Both the slicer and the socket are blocking, so each runs off the event loop.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from backend.deps import get_store
from cadless import printing, slicing, user_settings
from cadless.scoped_store import ScopedStore

router = APIRouter(prefix="/printing", tags=["printing"])

#: The sliced file's name inside the version's artifact directory.
GCODE_NAME = "print.gcode"


class AddressBody(BaseModel):
    """An address to test. Optional: omitted means the saved one."""

    model_config = ConfigDict(extra="forbid")

    address: str | None = None


def _saved_address() -> str:
    """The configured printer address, or ``""``."""
    return str(user_settings.load().get("printer_address") or "")


async def _mesh_path(store: ScopedStore, version_id: int) -> str:
    """The version's STL path, or a 404. Also the ownership gate for this router."""
    artifact = await store.get_artifact(version_id, "stl")
    if not artifact or not os.path.exists(artifact.path):
        raise HTTPException(
            status_code=404,
            detail="This version has no STL to print. Re-run it to export one.",
        )
    return artifact.path


@router.get("/capability")
async def capability() -> dict:
    """What this installation can currently do, so the UI can say so up front.

    Answered before anything is attempted: a Print button that explains it needs
    an address beats one that fails after slicing for two minutes.
    """
    binary = slicing.find_slicer()
    return {
        "slicer_available": binary is not None,
        "slicer_path": binary or "",
        "slicer_hint": "" if binary else slicing.INSTALL_HINT,
        "printer_configured": bool(_saved_address()),
    }


@router.post("/test")
async def test_connection(body: AddressBody | None = None) -> dict:
    """Open and close the printer's job port. Prints nothing.

    Takes an address so the Settings panel can check a value before saving it;
    falls back to the saved one so the same route serves a plain "is it there?".
    """
    address = (body.address if body else None) or _saved_address()
    if not address:
        raise HTTPException(status_code=400, detail="No printer address is configured.")
    outcome = await asyncio.to_thread(printing.probe, address)
    status = await asyncio.to_thread(printing.fetch_status, address)
    return {
        "ok": outcome.ok,
        "detail": outcome.detail,
        "reason": outcome.reason,
        "status": status.fields if status.ok else {},
        "status_detail": status.detail,
    }


@router.get("/status")
async def printer_status() -> dict:
    """What the printer says it is doing."""
    address = _saved_address()
    if not address:
        raise HTTPException(status_code=400, detail="No printer address is configured.")
    outcome = await asyncio.to_thread(printing.fetch_status, address)
    return {"ok": outcome.ok, "detail": outcome.detail, "fields": outcome.fields}


@router.post("/versions/{version_id}/slice")
async def slice_version(version_id: int, store: ScopedStore = Depends(get_store)) -> dict:
    """Slice the version's mesh and report what the print would cost.

    A missing slicer is reported as its own outcome rather than an error status:
    the UI turns it into installation guidance, and a 500 would read as a bug in
    the tool instead of something the reader can fix.
    """
    mesh = await _mesh_path(store, version_id)
    out_path = os.path.join(os.path.dirname(mesh), GCODE_NAME)
    outcome = await asyncio.to_thread(slicing.slice_mesh, mesh, out_path)
    return {
        "ok": outcome.ok,
        "detail": outcome.detail,
        "slicer_missing": outcome.missing,
        "stats": outcome.stats,
    }


@router.post("/versions/{version_id}/send")
async def send_version(version_id: int, store: ScopedStore = Depends(get_store)) -> dict:
    """Put the already-sliced job on the printer.

    Refuses to slice implicitly. Reaching here without a sliced file means the
    confirmation step was skipped, and quietly slicing would send a print nobody
    had seen the numbers for.
    """
    address = _saved_address()
    if not address:
        raise HTTPException(status_code=400, detail="No printer address is configured.")

    mesh = await _mesh_path(store, version_id)
    gcode_path = os.path.join(os.path.dirname(mesh), GCODE_NAME)
    if not os.path.exists(gcode_path):
        raise HTTPException(
            status_code=409,
            detail="This version has not been sliced yet.",
        )

    try:
        gcode = await asyncio.to_thread(_read_bytes, gcode_path)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"The sliced file could not be read: {exc}"
        ) from exc

    outcome = await asyncio.to_thread(
        printing.send_gcode, address, gcode, name=f"cadless-{version_id}"
    )
    return {
        "ok": outcome.ok,
        "detail": outcome.detail,
        "reason": outcome.reason,
        "bytes_sent": outcome.bytes_sent,
    }


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()
