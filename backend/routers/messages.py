"""Messages read API: persisted transcript + legacy version fallback.

GET /projects/{id}/messages returns the chat transcript. When a project predates
the chat feature (no ``chat_messages`` rows), the transcript is derived on read
from its versions -- one user message (the version prompt) plus one assistant
message (status/error/version_id from the version) per version, ordered by version
id. This mirrors the frontend's version-to-message mapping, so no bulk backfill is
needed; real rows take over once any new turn is written.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.deps import get_store
from backend.schemas import MessageOut
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
