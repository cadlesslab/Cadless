"""Chat turns the app owns, so that a closed page cannot end one.

A turn used to belong to its response. The stream's generator awaited the turn's task
on its way out, so a client that went away cancelled the generator and, through that
await, the turn. The turn's thread ran on — a model call or a build in a thread is
not something a cancel reaches — and kept what it built, but the write recording how
the turn ended was cancelled with the task, and the reply stayed ``pending`` for good.

Here the app launches a turn into a registry it holds, and the response only reads
what the turn sends. The registry keeps a reference to every turn's task until the
task ends: a task nothing refers to may be collected while it is still running.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

# What a reply says when the server stopped before its turn could finish: whether a
# drain cancelled the turn on the way out, or a start found it left pending.
INTERRUPTED_NOTE = "This turn was interrupted before it finished, because the server stopped."


@dataclass(eq=False)
class RunningTurn:
    """One chat turn from the moment its reply row exists until its task ends."""

    project_id: int
    session_id: int
    assistant_id: int
    # Asked at each of the turn's boundaries, from its thread. Per turn rather than
    # queued per session like a steer message, so a stop that arrives as a turn
    # ends cannot carry over and stop the next one.
    stop: threading.Event = field(default_factory=threading.Event)
    # Set once the turn's reply row holds the status it ended with.
    settled: bool = False
    # Set by the turn's thread when it returns, and by the turn's task when its own
    # clean-up has run. Whichever comes second removes the staging directory, since
    # a thread still running may yet copy files out of it.
    drive_done: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task | None = None


class TurnRegistry:
    """The turns running in this process, launched as tasks the app owns."""

    def __init__(self) -> None:
        self._turns: set[RunningTurn] = set()

    def launch(self, turn: RunningTurn, work: Coroutine[Any, Any, None]) -> asyncio.Task:
        task = asyncio.create_task(work)
        turn.task = task
        self._turns.add(turn)
        task.add_done_callback(lambda done: self._forget(turn, done))
        return task

    def for_session(self, session_id: int) -> list[RunningTurn]:
        return [turn for turn in self._turns if turn.session_id == session_id]

    def request_stop(self, session_id: int) -> bool:
        """Ask every unsettled turn in the session to stop; whether any was asked.

        A turn whose reply has settled answers no, though its task may still be
        running the work that follows a settle: it has already ended as whatever
        it ended as, and a stop cannot change that.
        """
        asked = False
        for turn in self.for_session(session_id):
            if not turn.settled:
                turn.stop.set()
                asked = True
        return asked

    async def drain(self, grace_s: float) -> None:
        """Let the running turns finish for up to ``grace_s``, then cancel the rest.

        For a shutdown. A cancelled turn settles ``interrupted``, and its thread is
        asked to stop as well, so it makes no further model call while the process
        winds down. The turns are not asked to stop up front: a stop settles a turn
        as stopped by its user, which is not what happened.
        """
        tasks = {turn.task for turn in self._turns if turn.task is not None}
        if not tasks:
            return
        _, late = await asyncio.wait(tasks, timeout=grace_s)
        for turn in [turn for turn in self._turns if turn.task in late]:
            turn.stop.set()
        for task in late:
            task.cancel()
        if late:
            await asyncio.wait(late, timeout=grace_s)

    def _forget(self, turn: RunningTurn, task: asyncio.Task) -> None:
        self._turns.discard(turn)
        # Read how it ended, so a turn that failed with nobody awaiting it is not
        # reported as an exception never retrieved. The turn logs its own failures.
        if not task.cancelled():
            task.exception()
