"""Pydantic request/response models shared across routers."""

from __future__ import annotations

from pydantic import BaseModel, Field

from cadless.llm.types import ContentBlock
from cadless.store import Artifact, ChatMessage, Project, ScriptVersion


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectOut(BaseModel):
    # Every field is listed and mapped by hand in `of` below, which is what
    # keeps the project's owner off the wire: a client is only ever shown its
    # own projects, so the key they are filed under tells it nothing it did not
    # already assume and names other principals' key space if it ever differs.
    # Do not swap this for `from_attributes` — that would publish the column the
    # day it is added. `test_store_surface` holds this.
    id: int
    name: str
    created_at: str
    updated_at: str
    current_version_id: int | None = None
    branched_from_version_id: int | None = None
    # Catalog items are read-only: parameters can be viewed but not edited; the
    # user clones the item first to customize it.
    is_catalog: bool = False
    # Customize-from-catalog provenance (#22): a clone records the project it was
    # copied from; the resolved name + catalog id let the UI render a
    # "based on <name>" chip linking back to the catalog item. All None for
    # projects that are not clones (or whose source no longer resolves).
    derived_from_project_id: int | None = None
    derived_from_name: str | None = None
    derived_from_catalog_id: str | None = None

    @classmethod
    def of(
        cls,
        p: Project,
        *,
        is_catalog: bool = False,
        derived_from_name: str | None = None,
        derived_from_catalog_id: str | None = None,
    ) -> ProjectOut:
        return cls(
            id=p.id,
            name=p.name,
            created_at=p.created_at,
            updated_at=p.updated_at,
            current_version_id=p.current_version_id,
            branched_from_version_id=p.branched_from_version_id,
            is_catalog=is_catalog,
            derived_from_project_id=p.derived_from_project_id,
            derived_from_name=derived_from_name,
            derived_from_catalog_id=derived_from_catalog_id,
        )


class ArtifactOut(BaseModel):
    kind: str
    bytes: int

    @classmethod
    def of(cls, a: Artifact) -> ArtifactOut:
        return cls(kind=a.kind, bytes=a.bytes)


class MessageOut(BaseModel):
    id: int
    seq: int
    role: str
    content: str | None
    status: str
    error: str | None
    version_id: int | None
    created_at: str
    blocks: list[ContentBlock] = []

    @classmethod
    def of(cls, m: ChatMessage) -> MessageOut:
        # Block-based transcript: the frontend renders from ``blocks`` only. A turn
        # persisted with plain ``content`` but no neutral blocks (e.g. a user message
        # from POST /chat) would otherwise render as nothing, so synthesize a single
        # text block from its content — matching the legacy version-derived path.
        blocks = list(m.blocks)
        if not blocks and m.content:
            blocks = [ContentBlock.of_text(m.content)]
        return cls(
            id=m.id,
            seq=m.seq,
            role=m.role,
            content=m.content,
            status=m.status,
            error=m.error,
            version_id=m.version_id,
            created_at=m.created_at,
            blocks=blocks,
        )


class VersionOut(BaseModel):
    id: int
    project_id: int
    prompt: str
    code: str | None
    ok: bool
    error: str | None
    volume: float | None
    bbox: tuple[float, float, float] | None
    created_at: str
    parameters: dict = {}
    parent_version_id: int | None = None
    # UI narration: the active plan step this checkpoint was written
    # under (1-based), or null when no plan was active. Lets the UI narrate
    # "rolled back to step N". Purely additive — null for legacy/no-plan rows.
    plan_step: int | None = None
    artifacts: list[ArtifactOut] = []

    @classmethod
    def of(cls, v: ScriptVersion, artifacts: list[Artifact] | None = None) -> VersionOut:
        return cls(
            id=v.id,
            project_id=v.project_id,
            prompt=v.prompt,
            code=v.code,
            ok=v.ok,
            error=v.error,
            volume=v.volume,
            bbox=v.bbox,
            created_at=v.created_at,
            parameters=v.parameters,
            parent_version_id=v.parent_version_id,
            plan_step=v.plan_step,
            artifacts=[ArtifactOut.of(a) for a in (artifacts or [])],
        )
