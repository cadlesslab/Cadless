"""Shared FastAPI dependencies.

The two here are one dependency in two halves: who is asking, and the store as
they are allowed to see it. Routes ask for the second and mostly never name the
first, which is deliberate — a route that has to reason about a principal to do
its job has started making decisions about identity, and those belong in one
place rather than in thirty.
"""

from __future__ import annotations

import inspect
import logging

from fastapi import HTTPException, Request

from cadless.config import settings
from cadless.identity import LOCAL, Principal, check_principal, principal_resolver
from cadless.scoped_store import ScopedStore

logger = logging.getLogger(__name__)


async def get_principal(request: Request) -> Principal:
    """Who is asking.

    With no resolver registered this is the single local user — which is the
    entire behaviour of a build nobody is hosting. No configuration, no
    sign-in, nothing to switch on.

    With one registered, its answer is checked before it is trusted, and a
    failure is a refusal rather than a fallback. Falling back would mean an
    add-on that broke its own sign-in had quietly downgraded the installation to
    one shared identity: everyone would see everyone else's work, and the app
    would go on answering normally while it happened. A 503 is the honest
    version of "this build cannot currently tell who you are".

    The absence of a resolver is checked here and not only at startup, because
    the registry is a module global with a public way to empty it. Booting with
    one is not a promise of still having one.
    """
    resolve = principal_resolver()
    if resolve is None:
        if settings.require_identity:
            logger.error("identity is required here but no principal resolver is registered")
            raise HTTPException(status_code=503, detail="identity unavailable")
        return LOCAL
    try:
        answer = resolve(request)
        if inspect.isawaitable(answer):
            answer = await answer
        return check_principal(answer)
    except HTTPException:
        # A resolver refusing on purpose — 401 for "you did not say who you are",
        # 403 for "you did, and no". Those are its answers to give, and turning
        # them into 503 would report the build as broken when it is working.
        raise
    except Exception:
        # Note for whoever writes a resolver: this line puts the exception text
        # in the log. Do not interpolate the credential you were introspecting
        # into the message.
        logger.exception("the registered principal resolver could not say who is asking")
        raise HTTPException(status_code=503, detail="identity unavailable") from None


async def get_store(request: Request) -> ScopedStore:
    """The store as the caller is allowed to see it.

    It keeps the name it had when it returned the raw store, so the dependency a
    build overrides is the one it already knew about, and so a route reaching
    for something unscoped fails loudly here rather than quietly reading rows it
    should not have.
    """
    return ScopedStore(request.app.state.store, await get_principal(request))
