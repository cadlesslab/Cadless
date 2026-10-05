"""The title check reads the title that exists now, and every scan of it can fail.

These assert on the workflow file rather than on behaviour, because a
workflow's wiring is reachable from no runtime here. Plain string checks rather
than a YAML parse, because PyYAML is not a dependency of this package.
"""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/pr-title.yml"


def _wiring() -> str:
    """The file with its comment lines removed, so prose cannot satisfy an assertion."""
    text = WORKFLOW.read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _step(name: str) -> str:
    """The wiring of one step, from its name up to the next step."""
    parts = _wiring().split(f"- name: {name}\n", 1)
    assert len(parts) == 2, f"the step {name!r} is missing"
    return parts[1].split("\n      - ", 1)[0]


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


def test_the_title_is_not_taken_from_the_event_payload():
    """A re-run replays the payload, and an expression in a run line hands the shell a contributor's text."""
    assert "github.event.pull_request.title" not in _wiring()


def test_an_edited_title_is_checked_again():
    """A title changed after a green run would otherwise merge unchecked."""
    assert "types: [opened, edited, reopened, synchronize]" in _wiring()


def test_no_step_is_allowed_to_fail_quietly():
    assert "continue-on-error" not in _wiring()


def test_the_title_is_checked_for_its_form():
    step = _step("The title says what kind of change it is")
    # The whole line, so a trailing `|| true` cannot pass for it.
    assert "run: python tools/check_pr_title.py pr_title.txt" in _lines(step)


def test_the_title_is_scanned_for_attribution():
    step = _step("No assistant attribution in the title")
    assert "run: python tools/attribution_guard.py pr_title.txt" in _lines(step)


def test_an_internal_reference_in_the_title_fails_the_job():
    step = _step("No internal references in the title")
    assert "python tools/leak_guard.py --text pr_title.txt || {" in _lines(step)
    assert "exit 1" in _lines(step), "a finding must fail the job, not just print"
