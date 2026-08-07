"""FastAPI application factory.

Mounts the API routers, configures CORS, initialises the persistence Store on
startup (lifespan), auto-loads the bundled catalog, and exposes /health. Routers
are added by the feature issues (projects, generation, versions, artifacts, SSE)
and registered here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import entry_points

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cadless.config import settings
from cadless.identity import has_principal_resolver
from cadless.store import Store, get_store

logger = logging.getLogger(__name__)


async def _autoload_catalog(store: Store) -> None:
    """Load every registered domain's catalog into the store.

    The tool ships sample parts so it works before any API key is entered, but a
    catalog item is surfaced only once it has been loaded into the store. Running
    this at startup makes the bundled catalog present on first boot. It is
    idempotent (``load_house`` skips items the db already records) and non-fatal (a
    single broken item must not stop the app from booting). It is a no-op when
    the roots hold no domain directories, so environments without a catalog
    (most tests) are unaffected.

    Two roots, not one. Bundled items ship with the image and the deployment
    mounts them read-only, so what a user receives is written under the data
    directory instead — and this is the only thing that reads either off disk.
    A root left out here is a root whose items vanish at the next restart.
    """
    from backend.catalog_state import ledger_for
    from cadless.catalog.domains import all_domains
    from cadless.catalog.importer import imported_domain_dir
    from cadless.catalog.ledger import LedgerUnreadable
    from cadless.catalog.loader import backfill_catalog_item_ids, load_all

    ledger = ledger_for(store)
    # Databases loaded before ``projects.catalog_item_id`` existed keep the mapping
    # only in the ledger, so copy it onto the rows before anything serves a request:
    # until that happens their catalog items read as ordinary editable projects.
    try:
        await backfill_catalog_item_ids(store, ledger)
    except LedgerUnreadable:
        logger.exception("catalog ledger unreadable")
        # Keep the unusable file as evidence, out of the way of the writes that
        # follow. What happens next turns on whether the db already knows which
        # projects are catalog items.
        ledger.quarantine()
        if await store.catalog_item_ids():
            # It does, so their read-only marks are intact and all that was lost is
            # the detail the catalog panel shows. Carry on: loading skips what the
            # db already has, and `catalog reload --all` rewrites details. Named
            # as the CLI spells it — `reload` is its own command, so the
            # `load --reload` this used to advise is one argparse refuses.
            logger.warning("carrying on: the db records which projects are catalog items")
        else:
            # It does not — this is a database from before the column, whose only
            # record of the mapping was the file we just failed to read. Loading now
            # would add a second copy of every item beside the ones already there,
            # and calling them all ordinary projects would unlock them. Do neither.
            logger.error(
                "cannot tell which projects are catalog items and will not guess; "
                "skipping autoload. Run `catalog reload --all` to rebuild."
            )
            return
    for domain in all_domains():
        for catalog_dir in (
            settings.domain_catalog_dir(domain.key),
            imported_domain_dir(domain.key),
        ):
            if not catalog_dir.is_dir():
                continue
            try:
                # `load_all` already carries on past an item it cannot read, so
                # what this catch is left with is a root that cannot be walked at
                # all. Between them, a broken item takes neither its neighbours
                # nor the other root's items with it.
                loaded = await load_all(store, ledger, catalog_dir)
            except Exception:  # a bad catalog item must never block startup
                logger.exception("catalog autoload failed for %s", catalog_dir)
                continue
            fresh = [hid for hid, pid in loaded.items() if pid is not None]
            if fresh:
                logger.info("autoloaded %d catalog item(s) from %s", len(fresh), catalog_dir)


def create_app(store: Store | None = None) -> FastAPI:
    store = store or get_store()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.init()
        await _autoload_catalog(store)
        yield

    app = FastAPI(
        title="Cadless",
        version="1.0.0",
        lifespan=lifespan,
        root_path=settings.root_path,
    )
    app.state.store = store
    # One catalog import at a time — `packages.import_catalog` says why. It hangs
    # off the app rather than living in that module because an `asyncio.Lock`
    # binds itself to the first event loop that contends on it, and a process
    # can build more than one app: the tests build one per case.
    app.state.import_gate = asyncio.Lock()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Whatever arrives here was not something a route knew how to describe,
        # so its text was written for us rather than for whoever asked — and
        # `OSError` writes the filename it failed on into it. Some of what this
        # app serves asks nobody to sign in, so that text is public. What it
        # actually was goes to the log, which is where it is any use.
        #
        # A parse error reached here the same way, from a file beside the database
        # rather than from anything the caller sent: every project route consulted
        # the catalog ledger, so one truncated copy of it answered
        # `Unterminated string starting at: line 1 column 29` to requests that had
        # nothing to do with the catalog. That route is closed, and this is what
        # kept its message from being the response even while it was open.
        logger.exception("unhandled error serving %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": "this machine failed on that request"},
        )

    _register_routers(app)
    _require_identity_if_asked()
    return app


class IdentityUnavailable(RuntimeError):
    """A build declared identity mandatory and nothing can supply it."""


def _require_identity_if_asked() -> None:
    """Refuse to boot a hosted build that cannot say who is asking.

    This exists because of how the line above behaves. `_register_routers`
    contains an add-on's failure on purpose — one broken extra must not take the
    app down with it — and for a router that is right: the build loses those
    routes and goes on serving the rest. For identity it is the wrong shape
    entirely. An add-on that failed to register its resolver leaves the engine
    with its own default, which answers "the one local user" to everybody, and
    the result is not a missing feature but every principal reading every other
    principal's work, logged once and then served normally for as long as the
    process runs.

    So a deployment says, at launch, that identity is not optional here. Then a
    resolver that is absent is a refusal rather than a downgrade. Off by default,
    because the tool on one person's machine has no resolver and does not want
    one, and that build must keep starting with nothing configured.
    """
    if not settings.require_identity:
        return
    if has_principal_resolver():
        return
    raise IdentityUnavailable(
        "CADLESS_REQUIRE_IDENTITY is set but no principal resolver is registered. "
        "A build that hosts more than one person must install one; refusing to "
        "start rather than serving every principal's work to everybody."
    )


ROUTER_MODULES = (
    "projects",
    "generation",
    "versions",
    "artifacts",
    "messages",
    "chat",
    "revert",
    "catalog",
    "settings",
    "packages",
)

# Where a distribution installed beside this one says it has a router to add.
# The tuple above can only name a module inside this tree, so it is no way in
# for a router that ships separately — and a build that adds one should not have
# to edit this file to be allowed to.
ROUTER_ENTRY_POINT_GROUP = "cadless.routers"


def _register_routers(app: FastAPI) -> None:
    """Include feature routers. Each is optional so partial builds still boot."""
    for module in ROUTER_MODULES:
        try:
            mod = __import__(f"backend.routers.{module}", fromlist=["router"])
            app.include_router(mod.router)
        except ImportError:
            pass
    for entry in entry_points(group=ROUTER_ENTRY_POINT_GROUP):
        try:
            app.include_router(_contained(entry.load(), entry.value))
        except Exception:
            # Not the same silence as the loop above. A module missing from this
            # tree is the ordinary shape of a partial build; a distribution that
            # advertised a router and then could not produce one is a broken
            # install. Booting anyway is still right — one bad add-on must not
            # stop the app — but saying nothing would leave it looking like a
            # build that simply never had those routes.
            logger.exception("could not load the router advertised as %s", entry.value)


def _contained(router: APIRouter, named: str) -> APIRouter:
    """The same router, with its startup work unable to stop the app.

    The `try` above covers `load()` and nothing after it. `include_router`
    *merges* an included router's lifespan into the app's, so an add-on that
    raises while starting up raises out of the app's own lifespan instead —
    measured: `/health` never answers, and a liveness probe fails on a build
    whose only fault was one broken add-on. That is the opposite of what a seam
    for optional routers is for, so the containment is here rather than in the
    contract each add-on is trusted to keep.

    Shutdown is contained on the same rule. A router that will not close cleanly
    is not a reason to fail the process on its way out.
    """
    inner = router.lifespan_context

    @asynccontextmanager
    async def contained(app: FastAPI) -> AsyncIterator[None]:
        started = inner(app)
        try:
            await started.__aenter__()
        except Exception:
            logger.exception("the router advertised as %s failed to start", named)
            started = None
        try:
            yield
        finally:
            if started is not None:
                try:
                    await started.__aexit__(None, None, None)
                except Exception:
                    logger.exception("the router advertised as %s failed to shut down", named)

    router.lifespan_context = contained
    return router
