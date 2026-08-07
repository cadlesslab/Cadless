"""Work a cancelled request must not be able to stop partway.

Two routes take the catalog's import gate and then hand real work to worker
threads through ``asyncio.to_thread``. A thread is not something cancellation
reaches: it runs to its end whatever happens to the request that started it. So
a gate held by an ``async with`` in the request's own task is given up at the
moment that task is interrupted — while the work it was guarding is still
going. The next caller in then finds the gate free, decides a name is free on
the strength of checks the running work is about to invalidate, and the two
arrive at the same directory: an import renaming onto one that has since been
filled, or a removal carrying off what an import has just put in place.

Running the guarded work as a task of its own moves what the gate belongs to.
It is released by the work ending rather than by the request going away, which
is the property both routes were relying on and neither had.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


async def to_completion[T](work: Coroutine[Any, Any, T]) -> T:
    """Run ``work`` in a task of its own and hand back what it returns.

    Cancelling the caller does not cancel ``work``; it stops waiting for it. The
    wait on the way out is so the caller returns after the work is done, which
    matters to anything the caller would otherwise clean up underneath it.

    That wait is best-effort by nature. A cancel scope that re-delivers on every
    await — how a request timeout is usually built on top of anyio — cuts it
    short, and the task then finishes unattended. Nothing the work needs may
    depend on the wait for that reason: what the work owns, it has to own for
    real. What the wait is for is ordering, not safety.
    """
    running = asyncio.create_task(work)
    # Read whatever it ends with, even when nobody is left to await it, so a
    # refusal answered to a request that has gone away is not reported as an
    # exception nobody retrieved.
    running.add_done_callback(_read_outcome)
    try:
        return await asyncio.shield(running)
    except asyncio.CancelledError:
        await asyncio.wait({running})
        raise


def _read_outcome(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()
