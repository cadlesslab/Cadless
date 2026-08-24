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

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from backend.deps import get_store
from cadless import printing, slicing, user_settings
from cadless.scoped_store import ScopedStore

#: The header a caller must send to reach anything here.
#:
#: Not a secret and not authentication -- its whole job is to stop a request
#: being a CORS "simple request". The rest of this API is reached with a JSON
#: body, which forces a ``Content-Type`` the browser will not send cross-site
#: without asking first, so the origin allow-list is consulted and a foreign
#: page is turned away. The routes below take no body, and a body-less POST is
#: simple: no preflight, no allow-list, no refusal. Without this, any page the
#: operator happened to visit could ``fetch(..., {mode: "no-cors"})`` this port
#: and start a print on their machine -- blind, but the filament is real.
#:
#: A browser cannot attach a custom header cross-site without a preflight, and
#: ``<img>`` and form posts cannot attach one at all, so requiring it puts these
#: routes back behind the allow-list that already guards the rest.
ACTION_HEADER = "x-cadless-action"


async def require_action_header(x_cadless_action: str | None = Header(default=None)) -> None:
    """Refuse a request that did not have to ask the browser's permission first."""
    if not x_cadless_action:
        raise HTTPException(
            status_code=403,
            detail=f"This endpoint requires the {ACTION_HEADER} header.",
        )


router = APIRouter(
    prefix="/printing",
    tags=["printing"],
    dependencies=[Depends(require_action_header)],
)

#: The sliced file's name inside the version's artifact directory.
GCODE_NAME = "print.gcode"


class AddressBody(BaseModel):
    """An address to test. Optional: omitted means the saved one."""

    model_config = ConfigDict(extra="forbid")

    address: str | None = None


def _saved_address() -> str:
    """The configured printer address, or ``""``."""
    return str(user_settings.load().get("printer_address") or "")


def _allowed() -> printing.Actions:
    """What this deployment can complete right now.

    Read fresh on each request rather than at import: a slicer can be installed
    and an address saved while the app is running, and a capability answered
    from a snapshot would keep saying no.
    """
    return printing.actions(
        slicer_available=slicing.find_slicer() is not None,
        address_configured=bool(_saved_address()),
    )


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
    allowed = _allowed()
    return {
        "slicer_available": binary is not None,
        "slicer_path": binary or "",
        "slicer_hint": "" if binary else slicing.INSTALL_HINT,
        "printer_configured": bool(_saved_address()),
        "mode": printing.mode(),
        # What the UI should offer. Two booleans rather than one mode string,
        # because the caller's question is "which buttons" and answering it here
        # keeps the rule in one place instead of restating it in TypeScript.
        "can_send": allowed.send,
        "can_download": allowed.download,
    }


@router.post("/test")
async def test_connection(body: AddressBody | None = None) -> dict:
    """Open and close the printer's job port, then ask what it is doing.

    Takes an address so the Settings panel can check a value before saving it;
    falls back to the saved one so the same route serves a plain "is it there?".

    The status read is what makes the answer worth having: a port that accepts a
    connection says the path is open, and the device naming its own state says
    the thing at the other end is the printer.
    """
    address = (body.address if body else None) or _saved_address()
    if not address:
        raise HTTPException(status_code=400, detail="No printer address is configured.")
    outcome = await asyncio.to_thread(printing.probe, address)
    status = (
        await asyncio.to_thread(printing.fetch_status, address)
        if outcome.ok
        else printing.StatusOutcome(False, "not asked: the job port did not answer")
    )
    return {
        "ok": outcome.ok,
        "detail": outcome.detail,
        "reason": outcome.reason,
        "status": status.fields if status.ok else {},
        "status_detail": status.detail,
    }


@router.post("/versions/{version_id}/slice")
async def slice_version(
    version_id: int,
    request: Request,
    store: ScopedStore = Depends(get_store),
) -> dict:
    """Slice the version's mesh and report what the print would cost.

    A missing slicer is reported as its own outcome rather than an error status:
    the UI turns it into installation guidance, and a 500 would read as a bug in
    the tool instead of something the reader can fix.

    Serialised on the app's slice gate -- the slicer is a heavy parser reading
    untrusted geometry inside this container, so one at a time.
    """
    if printing.mode() == printing.MODE_OFF:
        raise HTTPException(status_code=409, detail="Printing is switched off on this deployment.")
    mesh = await _mesh_path(store, version_id)
    out_path = os.path.join(os.path.dirname(mesh), GCODE_NAME)
    async with request.app.state.slice_gate:
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
    had seen the numbers for. The file's presence is evidence of a *finished*
    slice rather than merely an attempted one, because a failed slice never
    leaves one under this name.
    """
    current = printing.mode()
    if current != printing.MODE_AUTO:
        # Enforced here rather than trusted to the UI. The capability endpoint
        # tells the frontend which button to draw; this is what makes the answer
        # true for anything else that can reach the port.
        raise HTTPException(
            status_code=409,
            detail=(
                "This deployment prepares files to download and does not send to a printer."
                if current == printing.MODE_DOWNLOAD
                else "Printing is switched off on this deployment."
            ),
        )

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

    outcome = await asyncio.to_thread(
        printing.send_gcode, address, gcode_path, name=f"cadless-{version_id}"
    )
    return {
        "ok": outcome.ok,
        "detail": outcome.detail,
        "reason": outcome.reason,
        "bytes_sent": outcome.bytes_sent,
    }


@router.get("/versions/{version_id}/gcode")
async def download_gcode(version_id: int, store: ScopedStore = Depends(get_store)) -> FileResponse:
    """Hand back the sliced job as a file.

    The half of printing that works from anywhere. A deployment in a datacentre
    has no route to a printer on somebody's own network and no honest way to
    get one, but it can still do the part the user cannot: turn the model into a
    job their machine will accept, so what they carry over is ready to print
    rather than a mesh they have to slice themselves.

    Served rather than regenerated: this is the same file `/slice` produced and
    `/send` would have sent, so what is downloaded and what would be printed
    cannot drift apart.
    """
    if printing.mode() == printing.MODE_OFF:
        raise HTTPException(status_code=409, detail="Printing is switched off on this deployment.")
    mesh = await _mesh_path(store, version_id)
    gcode_path = os.path.join(os.path.dirname(mesh), GCODE_NAME)
    if not os.path.exists(gcode_path):
        raise HTTPException(status_code=409, detail="This version has not been sliced yet.")
    return FileResponse(
        gcode_path,
        media_type="text/x.gcode",
        filename=f"model_{version_id}.gcode",
    )


@router.delete("/address")
async def forget_address() -> dict:
    """Forget the saved printer address.

    Its own route because the settings endpoint only ever sets: a blank field
    there means "leave this alone", which is right for a key someone did not
    retype but leaves a mistyped address unfixable short of editing the file by
    hand.
    """
    user_settings.clear("printer_address")
    return {"ok": True}
