"""Printing a version on a 3D printer, or preparing one to be carried there.

Slicing and sending are separate calls on purpose. Slicing is the step that can
say how long the print will take and how much filament it will eat, and those
are the numbers someone wants before committing a couple of hours of machine
time. So ``/slice`` answers with them and puts nothing on the wire, and the
second step is deliberate.

There are two second steps, because there are two kinds of deployment. One that
shares a network with the printer sends the job. One that does not -- anything
in a datacentre, where the device is on the *user's* network -- hands the job
back as a file. Which is on offer is `cadless.printing.actions`, and every route
below that could act on it is gated by the same rule rather than by the UI
having drawn the right button.

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
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from backend.deps import get_store
from cadless import printing, slicing, user_settings
from cadless.config import settings
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


#: Why a mode turned a request away, in the words the reader needs.
_REFUSALS = {
    printing.MODE_OFF: "Printing is switched off on this deployment.",
    printing.MODE_DOWNLOAD: (
        "This deployment prepares files to download and does not send to a printer."
    ),
    printing.MODE_AUTO: "This deployment does not allow that.",
}


def require_mode(*allowed: str) -> Callable[[], Coroutine[Any, Any, None]]:
    """Refuse a request the deployment's printing mode does not permit.

    A dependency rather than a line inside each handler, so which routes are
    gated is readable from the decorators and adding one forces the decision.
    That is not hypothetical tidiness: the connection test was written without a
    gate precisely because nothing said the set existed, and it is the route
    that dials the most freely.
    """

    async def check() -> None:
        current = printing.mode()
        if current not in allowed:
            raise HTTPException(status_code=409, detail=_REFUSALS[current])

    return check


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


async def _mesh_path(store: ScopedStore, version_id: int) -> str:
    """The version's STL path, or a 404. Also the ownership gate for this router."""
    artifact = await store.get_artifact(version_id, "stl")
    if not artifact or not os.path.exists(artifact.path):
        raise HTTPException(
            status_code=404,
            detail="This version has no STL to print. Re-run it to export one.",
        )
    return artifact.path


def _gcode_for(mesh_path: str, saved: dict | None = None) -> str:
    """The sliced job for this mesh, or a 409 saying what is wrong with it.

    Newer than the mesh, not merely present. A failed re-slice discards its
    part-file and leaves the last good job under this name, and a rerun rewrites
    the mesh in place without touching it -- so "a file is there" is not "a file
    for the shape that is there now", and the gap between them is a print of the
    wrong object.

    **And sliced for the printer that is configured now.** The mesh's own
    timestamp used to answer both questions, because the slicing profile was a
    constant. It is the user's since the build volume became configurable, and
    the refusal this router produces for an oversized model ends with "set your
    printer's real size in Settings" -- so correcting the bed and then sending is
    a sequence the product actively invites. Without this check that sends the
    job cut for the old bed, whose moves run off the new one.
    """
    path = os.path.join(os.path.dirname(mesh_path), GCODE_NAME)
    if not os.path.exists(path):
        raise HTTPException(status_code=409, detail="This version has not been sliced yet.")
    was = slicing.sliced_under(path)
    # An empty fingerprint is a job from a build that did not write one. Refusing
    # those would make an upgrade re-slice everything for a mismatch there is no
    # evidence of; a recorded one that disagrees is evidence.
    if was and was != slicing.profile_fingerprint(slicing.profile_from_settings(saved)):
        raise HTTPException(
            status_code=409,
            detail="This job was sliced for a different printer. Slice it again.",
        )
    if os.path.getmtime(path) < os.path.getmtime(mesh_path):
        raise HTTPException(
            status_code=409,
            detail="This model has changed since it was sliced. Slice it again.",
        )
    return path


@router.get("/filament", dependencies=[Depends(require_mode(printing.MODE_AUTO))])
async def filament() -> dict:
    """How much filament the machine says it has left.

    Its own figure rather than a count kept here. A tally of what this tool has
    printed would drift the moment somebody printed from the panel or changed the
    cartridge, and the printer already knows.

    **Never an error.** This is one extra fact on the way into a confirmation
    dialog, and a printer that is off, unreachable or has no cartridge must not
    be able to stop a print being offered — every one of those answers `ok:
    false` with a reason and nothing else changes.

    Gated on `auto` because it dials the printer: a download-only deployment has
    no address to ask and nothing to ask it about.
    """
    address = await asyncio.to_thread(_saved_address)
    if not address:
        return {"ok": False, "detail": "No printer address is configured.", "percent": None}

    outcome = await asyncio.to_thread(printing.fetch_status, address)
    if not outcome.ok:
        return {"ok": False, "detail": outcome.detail, "percent": None}

    fields = outcome.fields
    saved = await asyncio.to_thread(user_settings.load)
    capacity = slicing.cartridge_grams(saved)
    percent = fields.get("filament_percent")
    return {
        "ok": True,
        "detail": "",
        # None when the machine reports no cartridge: the figure it keeps
        # showing there is the last one, and stale.
        "percent": percent,
        "loaded": fields.get("filament_loaded", False),
        "colour": fields.get("filament_colour"),
        # Only when somebody has said how much a full one holds. Without that,
        # a percentage is all there honestly is.
        "grams_left": None if percent is None or capacity is None else capacity * percent / 100,
        "cartridge_grams": capacity,
    }


@router.get("/capability")
async def capability() -> dict:
    """What this installation can currently do, so the UI can say so up front.

    Answered before anything is attempted: a Print button that explains what is
    missing beats one that fails after slicing for two minutes. Not gated by the
    mode -- reporting that printing is off is the one thing an off deployment
    still has to be able to say.
    """
    binary = await asyncio.to_thread(slicing.find_slicer)
    address = await asyncio.to_thread(_saved_address)
    allowed = printing.actions(
        slicer_available=binary is not None, address_configured=bool(address)
    )
    return {
        "slicer_available": binary is not None,
        "slicer_path": binary or "",
        "slicer_hint": "" if binary else slicing.INSTALL_HINT,
        "printer_configured": bool(address),
        # Whether a printer address can be recorded here at all. Without it,
        # "no address yet" on somebody's laptop and "no address is possible"
        # on a hosted build are the same two booleans on the wire, and the UI
        # has to tell one reader to go and fix something and the other that
        # there is nothing to fix.
        "can_configure": not settings.require_identity,
        "mode": printing.mode(),
        # What the UI should offer. Two booleans rather than one mode string,
        # because the caller's question is "which buttons" and answering it here
        # keeps the rule in one place instead of restating it in TypeScript.
        "can_send": allowed.send,
        "can_download": allowed.download,
    }


@router.post("/test", dependencies=[Depends(require_mode(printing.MODE_AUTO))])
async def test_connection(body: AddressBody | None = None) -> dict:
    """Open and close the printer's job port, then ask what it is doing.

    Takes an address so the Settings panel can check a value before saving it;
    falls back to the saved one so the same route serves a plain "is it there?".

    The status read is what makes the answer worth having: a port that accepts a
    connection says the path is open, and the device naming its own state says
    the thing at the other end is the printer.

    The most tightly gated route here, because it is the only one that dials an
    address the *caller* supplies rather than one an operator saved.
    """
    # A build that refuses settings writes can never save what is being tested,
    # so testing leads nowhere and only offers the dialling. Testing before
    # saving is this route's whole purpose; where saving is impossible the
    # purpose is gone and the capability should be too.
    if settings.require_identity:
        raise HTTPException(
            status_code=409,
            detail="This build does not store a printer address, so there is nothing to test.",
        )

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


@router.post(
    "/versions/{version_id}/slice",
    dependencies=[Depends(require_mode(printing.MODE_AUTO, printing.MODE_DOWNLOAD))],
)
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
    mesh = await _mesh_path(store, version_id)
    saved = await asyncio.to_thread(user_settings.load)

    # Answered before the slicer runs, because the answer is already known. The
    # version carries its bounding box, and a model that fits in no orientation
    # costs a slicer run to be told "All objects are outside of the print
    # volume" -- which names neither the model's size nor the printer's.
    version = await store.get_version(version_id)
    if version is not None:
        why = slicing.too_big_for(version.bbox, slicing.build_volume(saved))
        if why:
            return {"ok": False, "detail": why, "slicer_missing": False, "stats": {}}

    out_path = os.path.join(os.path.dirname(mesh), GCODE_NAME)
    profile = slicing.profile_from_settings(saved)
    async with request.app.state.slice_gate:
        outcome = await asyncio.to_thread(slicing.slice_mesh, mesh, out_path, profile=profile)
    return {
        "ok": outcome.ok,
        "detail": outcome.detail,
        "slicer_missing": outcome.missing,
        "stats": outcome.stats,
    }


@router.post(
    "/versions/{version_id}/send",
    dependencies=[Depends(require_mode(printing.MODE_AUTO))],
)
async def send_version(version_id: int, store: ScopedStore = Depends(get_store)) -> dict:
    """Put the already-sliced job on the printer.

    Refuses to slice implicitly. Reaching here without a sliced file means the
    confirmation step was skipped, and quietly slicing would send a print nobody
    had seen the numbers for.
    """
    # The same rule the capability answer is built from, asked again here. A
    # saved address does not by itself mean this host can reach a printer: a
    # build that refuses settings writes can still be holding one an earlier,
    # local launch of the same data directory saved, and that device is on
    # somebody else's network.
    address = _saved_address()
    if not address:
        raise HTTPException(status_code=400, detail="No printer address is configured.")
    if not printing.actions(slicer_available=True, address_configured=True).send:
        raise HTTPException(
            status_code=409,
            detail="This build does not send to printers. Download the job instead.",
        )

    mesh = await _mesh_path(store, version_id)
    gcode_path = _gcode_for(mesh, await asyncio.to_thread(user_settings.load))

    outcome = await asyncio.to_thread(
        printing.send_gcode, address, gcode_path, name=f"cadless-{version_id}"
    )
    return {
        "ok": outcome.ok,
        "detail": outcome.detail,
        "reason": outcome.reason,
        "bytes_sent": outcome.bytes_sent,
    }


@router.get(
    "/versions/{version_id}/gcode",
    dependencies=[Depends(require_mode(printing.MODE_AUTO, printing.MODE_DOWNLOAD))],
)
async def download_gcode(version_id: int, store: ScopedStore = Depends(get_store)) -> FileResponse:
    """Hand back the sliced job as a file.

    The half of printing that works from anywhere. A deployment in a datacentre
    has no route to a printer on somebody's own network and no honest way to get
    one, but it can still do the part the user cannot: turn the model into a job
    their machine will accept, so what they carry over is ready to print rather
    than a mesh they have to slice themselves.

    Served rather than regenerated: this is the same file `/slice` produced and
    `/send` would have sent.
    """
    mesh = await _mesh_path(store, version_id)
    saved = await asyncio.to_thread(user_settings.load)
    return FileResponse(
        _gcode_for(mesh, saved),
        media_type="text/x.gcode",
        filename=f"model_{version_id}.gcode",
    )


@router.delete("/profile", dependencies=[Depends(require_mode(*printing.MODES))])
async def forget_profile() -> dict:
    """Forget every saved printer measurement, returning to the defaults.

    The same gap `forget_address` exists for, one field over: `save()` only ever
    sets, so a blank box means "leave this alone" -- right for a value somebody
    did not retype, and no way back for one they got wrong. Without this the only
    route to the defaults is editing `settings.json` by hand.

    All seven together rather than one at a time: a printer profile describes one
    machine, and half of one is not a smaller description of it. `user_settings.clear`
    accepts these because they are saved state rather than configuration.
    """
    await asyncio.to_thread(user_settings.clear, *slicing.PRINTER_PROFILE_LIMITS)
    return {"ok": True}


@router.delete("/address", dependencies=[Depends(require_mode(*printing.MODES))])
async def forget_address() -> dict:
    """Forget the saved printer address.

    Its own route because the settings endpoint only ever sets: a blank field
    there means "leave this alone", which is right for a key someone did not
    retype but leaves a mistyped address unfixable short of editing the file by
    hand.

    Reachable in every mode on purpose, which is why the gate names them all
    rather than being absent. Removing an address is the recovery from having
    the wrong one, and a deployment that has switched printing off is exactly
    where somebody wants the stale one gone.
    """
    try:
        user_settings.clear("printer_address")
    except ValueError as exc:
        # A build that refuses settings writes refuses this too. It answers the
        # way the settings endpoint does rather than as an unhandled error --
        # which is what it was, on the one build where a stale address is worth
        # removing most.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}
