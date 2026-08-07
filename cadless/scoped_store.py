"""The store as one principal sees it.

:class:`~cadless.store.Store` is the engine's own API and is unscoped: startup,
the catalogue CLI and housekeeping all need to reach every row, and they run
where there is no request and no principal to ask about. That is correct for
them and wrong for a request, so a request never gets one. It gets this.

Every method here is the same method with the caller's owner already applied.
What matters is not the delegation but what is *absent* from it: there is no way
to reach `all_artifact_paths` or `sweep_orphans` through this object, so a route
cannot call them by accident, and a method added to the store later is not
reachable from a route until somebody puts it here on purpose.

That last part is the whole design. Passing an owner argument to each call would
work exactly as well on the day it was written and would decay from the first
new method that forgot one; the failure would be silent and would look like a
feature. Here a route that reaches for something unscoped fails immediately with
an ``AttributeError``, and `tests/test_store_surface.py` refuses to let the two
surfaces drift apart without somebody writing down why.

The delegation is spelled out one method at a time rather than generated. It is
more lines, and they are lines a reviewer can read in one pass and check against
the store — which is worth more here than brevity, because the question this
file answers is "is anything missing".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from cadless.identity import SYSTEM_KEY, Principal
from cadless.llm.types import ContentBlock
from cadless.store import Artifact, ChatMessage, ChatSession, KBEntry, Project, ScriptVersion, Store


class ScopedStore:
    """A :class:`~cadless.store.Store` bound to one principal.

    Read as: everything below is the store's method of the same name with
    ``owner=`` already supplied. Nothing here decides visibility on its own —
    the rule lives in :func:`cadless.identity.visible_owners` and is applied in
    SQL by the store, so this class cannot get it subtly wrong in one place.
    """

    def __init__(self, store: Store, principal: Principal) -> None:
        self._store = store
        self.principal = principal

    @property
    def _owner(self) -> str:
        return self.principal.key

    @property
    def db_path(self):
        """Where the database file is.

        Not a row and not scoped: the catalogue ledger is a sidecar file beside
        the database, and `backend.catalog_state` derives its path from this.
        Passed through rather than reached around, so a route still has one
        object to hold.
        """
        return self._store.db_path

    @property
    def artifacts_dir(self):
        """Where artifact blobs live. Passed through for the same reason."""
        return self._store.artifacts_dir

    # ---- artifact blob paths --------------------------------------------
    def version_artifact_dir(self, version_id: int) -> str:
        """Where a version's artifact files live.

        Exposed without a scope because it reads nothing: it derives a path from
        an integer and makes sure the directory is there. Serving the bytes goes
        through :meth:`get_artifact`, which is scoped, and every caller here has
        already resolved the version through a scoped lookup.
        """
        return self._store.version_artifact_dir(version_id)

    # ---- projects -------------------------------------------------------
    async def create_project(self, name: str) -> Project:
        """Create a project belonging to this principal.

        No ``catalog_item_id``. Passing one would make a row that refuses every
        mutation, cannot be deleted through the project route, and cannot be
        reached by the catalogue route either, which looks for the build's copy
        — the same capability `set_catalog_item_id` is kept off this view to
        prevent. The catalogue loader creates those, through the build's view.
        """
        return await self._store.create_project(name, owner=self._owner)

    async def list_projects(self) -> list[Project]:
        return await self._store.list_projects(owner=self._owner)

    async def get_project(self, project_id: int) -> Project | None:
        return await self._store.get_project(project_id, owner=self._owner)

    async def rename_project(self, project_id: int, name: str) -> Project | None:
        return await self._store.rename_project(project_id, name, owner=self._owner)

    async def delete_project(self, project_id: int) -> bool:
        return await self._store.delete_project(project_id, owner=self._owner)

    async def clone_project(self, project_id: int, *, name: str | None = None) -> Project | None:
        return await self._store.clone_project(project_id, name=name, owner=self._owner)

    async def branch_project(self, version_id: int, *, name: str | None = None) -> Project | None:
        return await self._store.branch_project(version_id, name=name, owner=self._owner)

    async def project_id_for_catalog_item(self, item_id: str) -> int | None:
        return await self._store.project_id_for_catalog_item(item_id, owner=self._owner)

    async def catalog_item_ids(self) -> dict[int, str]:
        return await self._store.catalog_item_ids(owner=self._owner)

    # ---- per-build project records --------------------------------------
    async def record_plugin_data(
        self, project_id: int, plugin: str, data: Mapping[str, Any]
    ) -> bool:
        return await self._store.record_plugin_data(project_id, plugin, data, owner=self._owner)

    async def plugin_data(self, project_id: int, plugin: str) -> dict | None:
        return await self._store.plugin_data(project_id, plugin, owner=self._owner)

    async def plugin_data_for(self, project_ids: Sequence[int], plugin: str) -> dict[int, dict]:
        return await self._store.plugin_data_for(project_ids, plugin, owner=self._owner)

    # ---- versions -------------------------------------------------------
    async def add_version(
        self,
        project_id: int,
        prompt: str,
        code: str | None,
        ok: bool,
        error: str | None = None,
        volume: float | None = None,
        bbox: tuple[float, float, float] | None = None,
        parameters: dict | None = None,
        parent_version_id: int | None = None,
        candidate_of_version_id: int | None = None,
        plan_step: int | None = None,
    ) -> ScriptVersion:
        return await self._store.add_version(
            project_id,
            prompt,
            code,
            ok,
            error,
            volume,
            bbox,
            parameters,
            parent_version_id,
            candidate_of_version_id,
            plan_step,
            owner=self._owner,
        )

    async def list_versions(self, project_id: int) -> list[ScriptVersion]:
        return await self._store.list_versions(project_id, owner=self._owner)

    async def get_version(self, version_id: int) -> ScriptVersion | None:
        return await self._store.get_version(version_id, owner=self._owner)

    async def list_candidate_versions(self, winner_version_id: int) -> list[ScriptVersion]:
        return await self._store.list_candidate_versions(winner_version_id, owner=self._owner)

    async def last_ok_version(self, project_id: int) -> ScriptVersion | None:
        return await self._store.last_ok_version(project_id, owner=self._owner)

    async def set_current_version(self, project_id: int, version_id: int) -> bool:
        return await self._store.set_current_version(project_id, version_id, owner=self._owner)

    # ---- artifacts ------------------------------------------------------
    async def add_artifact(self, version_id: int, kind: str, path: str) -> Artifact:
        return await self._store.add_artifact(version_id, kind, path, owner=self._owner)

    async def list_artifacts(self, version_id: int) -> list[Artifact]:
        return await self._store.list_artifacts(version_id, owner=self._owner)

    async def get_artifact(self, version_id: int, kind: str) -> Artifact | None:
        return await self._store.get_artifact(version_id, kind, owner=self._owner)

    async def thumbnail_version_ids(self, project_ids: Sequence[int]) -> dict[int, int]:
        return await self._store.thumbnail_version_ids(project_ids, owner=self._owner)

    # ---- chat -----------------------------------------------------------
    async def get_or_create_session(self, project_id: int) -> ChatSession:
        return await self._store.get_or_create_session(project_id, owner=self._owner)

    async def add_message(
        self,
        session_id: int,
        role: str,
        content: str | None,
        status: str = "ok",
        error: str | None = None,
        version_id: int | None = None,
        blocks: list[ContentBlock] | None = None,
    ) -> ChatMessage:
        return await self._store.add_message(
            session_id,
            role,
            content,
            status,
            error,
            version_id,
            blocks,
            owner=self._owner,
        )

    async def update_message(
        self,
        message_id: int,
        *,
        status: str | None = None,
        error: str | None = None,
        version_id: int | None = None,
        content: str | None = None,
        blocks: list[ContentBlock] | None = None,
    ) -> ChatMessage | None:
        return await self._store.update_message(
            message_id,
            status=status,
            error=error,
            version_id=version_id,
            content=content,
            blocks=blocks,
            owner=self._owner,
        )

    async def list_messages(self, session_id: int) -> list[ChatMessage]:
        return await self._store.list_messages(session_id, owner=self._owner)

    # ---- knowledge base -------------------------------------------------
    async def add_kb_entry(
        self,
        nl_intent: str,
        code: str,
        embedding: list[float],
        *,
        params: dict | None = None,
        geometry_signature: dict | None = None,
        provenance: dict | None = None,
    ) -> KBEntry:
        return await self._store.add_kb_entry(
            nl_intent,
            code,
            embedding,
            params=params,
            geometry_signature=geometry_signature,
            provenance=provenance,
            owner=self._owner,
        )

    async def get_kb_entry(self, entry_id: int) -> KBEntry | None:
        return await self._store.get_kb_entry(entry_id, owner=self._owner)

    async def list_kb_entries(self) -> list[KBEntry]:
        return await self._store.list_kb_entries(owner=self._owner)

    async def query_kb_by_vector(
        self, embedding: list[float], top_k: int = 5
    ) -> list[tuple[KBEntry, float]]:
        return await self._store.query_kb_by_vector(embedding, top_k, owner=self._owner)


# What most of the engine takes: either the store itself, or a view of it. The
# distinction is the caller's to make and nothing downstream has to care, which
# is why so little of the tree needed changing — a function handed a scoped view
# is scoped all the way down without knowing it.
AnyStore = Store | ScopedStore

# The build acting for itself. Not a person, and a resolver cannot return it:
# `check_principal` refuses the reserved prefix, so this is only constructible
# from inside the engine.
SYSTEM = Principal(SYSTEM_KEY, "This build")


class BuildStore(ScopedStore):
    """The store as the build itself, with the two powers a request must not have.

    Both of these make a row that belongs to the installation and is read by
    every principal. Neither belongs on :class:`ScopedStore`: a route that could
    reach them could hand its own project to everybody, or mint a project that
    refuses every mutation, cannot be deleted through the project route, and
    cannot be reached through the catalogue route either.

    Kept as a subclass rather than a flag so the difference is visible in the
    type, and so `tests/test_store_surface.py` keeps measuring the request-facing
    surface rather than this one.
    """

    async def create_project(self, name: str, *, catalog_item_id: str | None = None) -> Project:
        return await self._store.create_project(
            name, catalog_item_id=catalog_item_id, owner=self._owner
        )

    async def set_catalog_item_id(self, project_id: int, item_id: str | None) -> None:
        return await self._store.set_catalog_item_id(project_id, item_id)


def system_view(store: AnyStore) -> BuildStore:
    """The store as the build itself, for the rows that belong to nobody.

    A catalogue item is the case, and it is the only one. Both roots the loader
    walks are shared directories — what shipped in the image, and what has been
    received into the data directory — so an item in either is there for
    everybody. Filing it under whoever happened to trigger the load would make
    ownership depend on which caller ran — startup, the CLI, `POST
    /packages/import`, `DELETE /catalog/{house_id}`, and whatever a build
    outside this tree adds — and the same item would be private or shared
    according to how it arrived.

    Accepts a view as well as a store so the loader can normalise whatever it
    was handed. Reaching through a view to the store underneath is a widening of
    scope, which is why this is a named function rather than a property: it is
    greppable, and `tests/test_store_surface.py` refuses to let a router import
    it.

    Per-principal catalogue storage is a different question and is not this
    seam's — it needs the directories split as well as the rows, which is the
    same shape as the artifact blob layout and belongs with it.
    """
    raw = store._store if isinstance(store, ScopedStore) else store
    return BuildStore(raw, SYSTEM)
