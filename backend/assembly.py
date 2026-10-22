"""The assembly check at the API's edge: which spec a build is held to, and what
its verdict shows a reader.

Shared by every route that builds a version -- the chat and the parameter panel
-- so a model is held to the same rule and its findings read the same wherever
it was changed.
"""

from __future__ import annotations

import asyncio

from cadless import printer_profile, user_settings
from cadless.config import settings
from cadless.printer_profile import AssemblySpec


async def assembly_spec_for(wanted: bool) -> AssemblySpec | None:
    """The spec a build is held to as an assembly, or ``None`` for an ordinary build.

    ``wanted`` is whether this build should be one: asked for on this turn, or
    an edit of a model already built as one. The kill-switch stays outermost: a
    version's record must not keep the feature alive after it has been switched
    off. What the spec says comes from the printer saved now, so changing printer
    changes what an edit has to satisfy -- a version remembers that it is an
    assembly, never which machine it was once built for.
    """
    if not (settings.assembly_enabled and wanted):
        return None
    return printer_profile.assembly_spec(await asyncio.to_thread(user_settings.load))


def assembly_findings(verdict: object) -> dict | None:
    """What the assembly check found wrong with a build, for a reader.

    The check's own sentences, failures kept apart from what it could not
    establish, so a reader can ask for the fix without a model having to relay
    them. ``None`` where there is nothing to show: no verdict, a build that was
    no assembly, or one the check accepted.

    Read defensively, keeping only lists of non-blank strings: a request must not
    fail on the verdict's shape, nor show a reader anything but sentences.
    """
    if not isinstance(verdict, dict):
        return None

    def sentences(raw: object) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [line for line in raw if isinstance(line, str) and line.strip()]

    failures = sentences(verdict.get("failures"))
    unchecked = sentences(verdict.get("unchecked"))
    if not failures and not unchecked:
        return None
    return {"failures": failures, "unchecked": unchecked}
