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


@dataclass(eq=False)
class RunningTurn:
    """One chat turn from the moment its reply row exists until its task ends."""

    project_id: int
    session_id: int
    assistant_id: int
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

    def _forget(self, turn: RunningTurn, task: asyncio.Task) -> None:
        self._turns.discard(turn)
        # Read how it ended, so a turn that failed with nobody awaiting it is not
        # reported as an exception never retrieved. The turn logs its own failures.
        if not task.cancelled():
            task.exception()
