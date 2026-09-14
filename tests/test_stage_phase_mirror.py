"""Pin the pipeline's stage vocabulary to the progress display that renders it.

``STAGE_PHASES`` in ``cadless/pipeline.py`` names every phase the loop can emit;
``STEP_DEFS`` in ``frontend/src/panels/progress.ts`` decides which of them a
person actually sees. Nothing made them agree, and the lookup on the TypeScript
side drops an unknown phase without a word -- so a phase added to the engine and
not mirrored renders as nothing, with no error anywhere and every test green.

That already happened once: ``assert`` was added to the tuple and never reached
the display, and the comment in ``frontend/src/api.ts`` listing the vocabulary
had gone stale the same way. ``tests/test_sse_progress.py`` cannot catch it --
it asserts the emitted phases are a *subset* of the tuple, which stays true no
matter what the browser does with them.

The Python side is read from the live tuple rather than the source text, so it
cannot drift from what the module declares. Only the TypeScript is parsed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cadless.pipeline import STAGE_PHASES

_PROGRESS_TS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "panels" / "progress.ts"

#: Phases deliberately absent from the display, each with the reason it is absent.
#:
#: This is an allowlist rather than a silence: a new phase fails the test until
#: someone either maps it to a step or writes it down here, which is exactly the
#: decision that was skipped when ``assert`` went in.
_NOT_DISPLAYED = {
    # Repair happens *between* attempts rather than being a stage of one, and the
    # display already shows the attempt number climbing.
    "repair": "shown as a rising attempt count, not as a step of its own",
}


def _ts_source() -> str:
    if not _PROGRESS_TS.exists():  # pragma: no cover - only in a frontend-less checkout
        pytest.skip(f"{_PROGRESS_TS} is not present in this checkout")
    return _PROGRESS_TS.read_text(encoding="utf-8")


def _ts_step_phases(src: str) -> set[str]:
    """Every phase named in a ``phases: [...]`` entry of ``STEP_DEFS``."""
    match = re.search(r"const\s+STEP_DEFS\s*:[^=]*=\s*\[(.*?)\n\];", src, re.DOTALL)
    assert match, "frontend/src/panels/progress.ts declares no `STEP_DEFS` array"
    body = match.group(1)
    # Strip comments first: this file documents phase names in prose, and counting
    # those would let the mirror pass on a phase that is only ever talked about.
    body = re.sub(r"//[^\n]*", "", body)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    phases: set[str] = set()
    for entry in re.findall(r"phases\s*:\s*\[([^\]]*)\]", body):
        phases.update(re.findall(r"[\"']([^\"']+)[\"']", entry))
    return phases


def test_every_emittable_phase_either_renders_or_is_written_down():
    shown = _ts_step_phases(_ts_source())
    unaccounted = set(STAGE_PHASES) - shown - set(_NOT_DISPLAYED)

    assert not unaccounted, (
        "these pipeline phases reach no step in frontend/src/panels/progress.ts, so "
        "they render as nothing at all: "
        f"{sorted(unaccounted)}. Map each to a STEP_DEFS entry, or add it to "
        "_NOT_DISPLAYED here with the reason it is not shown."
    )


def test_the_display_names_no_phase_the_pipeline_cannot_emit():
    shown = _ts_step_phases(_ts_source())
    invented = shown - set(STAGE_PHASES)

    assert not invented, (
        "frontend/src/panels/progress.ts waits on phases the pipeline never emits, "
        f"so their steps hang pending forever: {sorted(invented)}"
    )


def test_a_phase_written_down_as_hidden_is_still_a_real_phase():
    stale = set(_NOT_DISPLAYED) - set(STAGE_PHASES)
    assert not stale, f"_NOT_DISPLAYED names phases that no longer exist: {sorted(stale)}"


def test_the_parser_reads_entries_rather_than_prose():
    # The guard is only as good as this parse: if it silently found nothing, every
    # assertion above would pass vacuously. Two known entries pin that it does not.
    shown = _ts_step_phases(_ts_source())
    assert {"interpret", "build"} <= shown
