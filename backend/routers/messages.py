"""Messages read API: persisted transcript + legacy version fallback.

GET /projects/{id}/messages returns the chat transcript. When a project predates
the chat feature (no ``chat_messages`` rows), the transcript is derived on read
from its versions -- one user message (the version prompt) plus one assistant
message (status/error/version_id from the version) per version, ordered by version
id. This mirrors the frontend's version-to-message mapping, so no bulk backfill is
needed; real rows take over once any new turn is written.
"""

from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Response

from backend.deps import get_store
from backend.schemas import MessageOut
from cadless.config import settings
from cadless.llm.types import ContentBlock
from cadless.scoped_store import ScopedStore
from cadless.store import ScriptVersion

router = APIRouter(tags=["messages"])


def _legacy_transcript(versions: list[ScriptVersion]) -> list[MessageOut]:
    """Synthesize a transcript from versions for pre-feature projects.

    Versions arrive ordered by id (see ``store.list_versions``). Each yields a
    user message then an assistant message; ``seq`` is a synthetic 1-based counter
    over the synthesized messages.
    """
    out: list[MessageOut] = []
    seq = 0
    for v in versions:
        seq += 1
        out.append(
            MessageOut(
                id=seq,
                seq=seq,
                role="user",
                content=v.prompt,
                status="ok",
                error=None,
                version_id=None,
                created_at=v.created_at,
                blocks=_text_blocks(v.prompt),
            )
        )
        seq += 1
        out.append(
            MessageOut(
                id=seq,
                seq=seq,
                role="assistant",
                content=None,
                status="ok" if v.ok else "error",
                error=v.error,
                version_id=v.id,
                created_at=v.created_at,
                blocks=_text_blocks(None),
            )
        )
    return out


def _text_blocks(content: str | None) -> list[ContentBlock]:
    """A single synthesized ``text`` block from a plain-text projection (empty if none)."""
    return [ContentBlock.of_text(content)] if content else []


@router.get("/projects/{project_id}/messages", response_model=list[MessageOut])
async def list_messages(project_id: int, store: ScopedStore = Depends(get_store)):
    if not await store.get_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    session = await store.get_or_create_session(project_id)
    messages = await store.list_messages(session.id)
    if messages:
        return [MessageOut.of(m) for m in messages]
    return _legacy_transcript(await store.list_versions(project_id))


def _attachment_cache_headers() -> dict[str, str]:
    """How long a browser may keep an attachment, and what that depends on.

    The bytes at one of these addresses never change, so caching them saves the
    transcript re-asking for every picture on every reload. But a browser cache is
    keyed by URL and knows nothing about who is asking — and these ids are small
    integers. Where the build requires an identity, two people sharing a browser
    profile would mean the second reads the first's upload out of the cache without
    the request ever reaching the owner-scoped lookup that would refuse it. So the
    saving is only taken where there is one principal by construction.
    """
    if settings.require_identity:
        return {"Cache-Control": "no-store"}
    return {"Cache-Control": "private, max-age=3600", "Vary": "Cookie, Authorization"}


@router.get("/projects/{project_id}/messages/{message_id}/attachments/{index}")
async def get_attachment(
    project_id: int,
    message_id: int,
    index: int,
    store: ScopedStore = Depends(get_store),
):
    """The bytes of the ``index``-th image attached to one message.

    The transcript payload carries the block without its data (see
    ``MessageOut.of``), so this is where an ``<img>`` gets the picture. The path
    is project-scoped and the lookup is checked against that project's session:
    naming someone else's project alongside a message id must not read it.
    """
    if not await store.get_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    session = await store.get_or_create_session(project_id)
    # One row, selected by both ids. Scanning the session instead would parse every
    # block of every message — including the payload of every other picture in it —
    # to find one, on every image the transcript renders.
    message = await store.get_message(session.id, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="message not found")

    images = [b for b in message.blocks if b.kind == "image" and b.data]
    if index < 0 or index >= len(images):
        raise HTTPException(status_code=404, detail="attachment not found")
    block = images[index]
    try:
        raw = base64.b64decode(block.data, validate=True)
    except (binascii.Error, ValueError) as exc:
        # Stored data that will not decode is a corrupt row, not a missing one.
        raise HTTPException(status_code=500, detail="attachment is unreadable") from exc

    # The stored media type is checked again here rather than trusted. It was
    # allow-listed when the attachment arrived, but that check lives in one
    # caller: a row written by any other path — a future import, or a
    # distribution mounting this router with its own writer — would reach this
    # line unchecked, and a Content-Type this origin serves is the difference
    # between a picture and script. Every other route in this tree answers with a
    # server-side constant; this is the only one echoing stored input, so it
    # narrows to a type that cannot execute, and tells the browser not to guess.
    ctype = (
        block.media_type
        if block.media_type in settings.chat_image_media_types
        else "application/octet-stream"
    )
    return Response(
        content=raw,
        media_type=ctype,
        headers={
            "X-Content-Type-Options": "nosniff",
            **_attachment_cache_headers(),
        },
    )
