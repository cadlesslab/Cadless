"""The published-text job reads the body that exists now, not the one that failed.

These assert on the workflow file rather than on behaviour, and deliberately: a
workflow's wiring is not reachable from any runtime here, and the defect being
guarded is one only the wiring shows. `github.event.pull_request.body` is the
event payload, and GitHub replays the payload on a re-run — so a job that failed
on a body re-read the text that had already failed and could never pass. The
only way back was to close and reopen the pull request, which nothing said.

Plain string checks rather than a YAML parse, because PyYAML is not a dependency
of this package and adding one to assert four lines would cost more than it says.
"""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/leak-guard.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _wiring() -> str:
    """The file with its comment lines removed.

    What is under test is what the job does, and the comments here explain the
    very payload expression the first assertion forbids. Scanning the prose
    alongside the wiring makes the explanation indistinguishable from the fault
    it explains.
    """
    return "\n".join(line for line in _text().splitlines() if not line.lstrip().startswith("#"))


def test_the_body_is_not_taken_from_the_event_payload():
    assert "github.event.pull_request.body" not in _wiring(), (
        "the event payload is replayed on a re-run, so a corrected body would never be seen"
    )


def test_the_body_is_fetched_from_the_api():
    text = _text()
    assert "gh api" in text and "/pulls/$PR_NUMBER" in text
    assert "pull-requests: read" in text, "fetching the body needs the token scoped to read it"


def test_the_two_inputs_are_scanned_separately():
    """Their remedies are opposites, so one step could only ever name both.

    A commit message is rewritten and force-pushed; a body is edited in place
    and must not be force-pushed at all.
    """
    text = _text()
    assert "--text messages.txt\n" in text or "--text messages.txt " in text
    assert "--text pr_body.txt" in text
    assert "--text messages.txt pr_body.txt" not in text, "one step cannot name two remedies"


def test_the_body_remedy_does_not_tell_anyone_to_force_push():
    """Force-pushing over a pull request body does nothing: the text was never
    in a commit. The old single message said to, and a reader followed it."""
    body_step = _text().split("No internal references in the pull request body", 1)
    assert len(body_step) == 2, "the body step is missing"
    assert "force-push" in body_step[1].split("::error::", 1)[1].split("\n", 1)[0], (
        "the body remedy should mention force-push only to rule it out"
    )
    assert "Do not force-push" in body_step[1]
