"""Writing the assembly guide. No network: a scripted provider.

What is under test is the division of labour. The order and the joints are the
engine's, measured while the split was checked; the model supplies names for the
parts and nothing else. So a model that answers badly costs a guide its
vocabulary and can never cost it its sequence.
"""

import json

import pytest

from cadless.guide_writer import AssemblyGuide, GuideWriter, plain_guide
from cadless.llm.providers import StreamChunk
from cadless.llm.providers.fake import FakeChatProvider
from cadless.llm.types import StreamEvent

CODE = "from build123d import *\nbase = Box(40, 40, 10)\nresult = base\n"

# A three-part chain: 0 joins 1, 1 joins 2. Assembled 1, then 0, then 2.
ORDER = [1, 0, 2]
JOINTS = [[1], [0, 2], [1]]


def _provider(reply: str) -> FakeChatProvider:
    return FakeChatProvider(script=[StreamChunk(StreamEvent.TEXT_DELTA, {"text": reply})])


class _AngryProvider(FakeChatProvider):
    def stream_turn(self, **kw):
        raise RuntimeError("the provider is down")


def _named(*names: str) -> str:
    return json.dumps({"parts": list(names)})


# --- what the engine can say on its own -----------------------------------


def test_the_plain_guide_names_parts_the_way_the_engine_labels_them():
    guide = plain_guide(ORDER, JOINTS)
    assert guide.parts == ["part 1", "part 2", "part 3"]


def test_the_plain_guide_has_one_step_per_part_and_starts_where_the_order_does():
    guide = plain_guide(ORDER, JOINTS)
    assert len(guide.steps) == 3
    assert guide.steps[0] == "Start with part 2."


def test_each_later_step_names_only_parts_already_placed():
    # Part 2 joins both its neighbours, but at the step that adds part 1 only
    # part 2 is on the bench. Naming part 3 there would tell a reader to fit
    # something that is not in front of them yet.
    guide = plain_guide(ORDER, JOINTS)
    assert guide.steps[1] == "Fit part 1 to part 2."
    assert guide.steps[2] == "Fit part 3 to part 2."


def test_a_part_joined_to_nothing_yet_placed_is_still_given_a_step():
    guide = plain_guide([0, 1], [[], []])
    assert guide.steps == ["Start with part 1.", "Add part 2."]


# --- what the model adds --------------------------------------------------


def test_the_model_supplies_the_names_and_the_engine_builds_the_sentences():
    writer = GuideWriter(provider=_provider(_named("foot", "column", "cap")))
    guide = writer.write("a lamp stand", CODE, ORDER, JOINTS)
    assert guide.parts == ["foot", "column", "cap"]
    assert guide.steps == [
        "Start with column.",
        "Fit foot to column.",
        "Fit cap to column.",
    ]


def test_the_model_cannot_change_the_order_because_it_is_never_asked_for_one():
    # The only thing taken from the reply is the names. A reply that also states
    # an order, or steps, changes nothing: there is nowhere for them to land.
    reply = json.dumps(
        {
            "parts": ["foot", "column", "cap"],
            "order": [2, 1, 0],
            "steps": ["Do it backwards.", "Then this.", "Then that."],
        }
    )
    guide = GuideWriter(provider=_provider(reply)).write("a lamp stand", CODE, ORDER, JOINTS)
    assert guide.steps[0] == "Start with column."
    assert "backwards" not in " ".join(guide.steps)


@pytest.mark.parametrize(
    "reply",
    [
        "I am afraid I cannot do that.",
        json.dumps({"parts": []}),
        json.dumps({"parts": ["only one"]}),
        json.dumps({"parts": ["a", "b", ""]}),
        json.dumps({"parts": ["a", "b", 3]}),
        json.dumps({"nothing": "useful"}),
        "",
    ],
)
def test_an_unusable_reply_leaves_the_engine_labels_standing(reply):
    guide = GuideWriter(provider=_provider(reply)).write("a lamp stand", CODE, ORDER, JOINTS)
    assert guide == plain_guide(ORDER, JOINTS)


def test_a_provider_that_fails_costs_the_names_and_nothing_else():
    # The parts are already built and checked by the time a guide is written.
    # Losing the model must not lose the guide, let alone the build.
    guide = GuideWriter(provider=_AngryProvider(script=[])).write("a lamp", CODE, ORDER, JOINTS)
    assert guide == plain_guide(ORDER, JOINTS)


def test_a_reply_wrapped_in_a_code_fence_is_still_read():
    reply = "Here you go:\n```json\n" + _named("foot", "column", "cap") + "\n```\n"
    guide = GuideWriter(provider=_provider(reply)).write("a lamp stand", CODE, ORDER, JOINTS)
    assert guide.parts == ["foot", "column", "cap"]


# --- what goes into the transcript ----------------------------------------


def test_the_payload_carries_the_names_and_the_steps():
    guide = AssemblyGuide(parts=["a", "b"], steps=["Start with a.", "Fit b to a."])
    assert guide.as_payload() == {
        "parts": ["a", "b"],
        "steps": ["Start with a.", "Fit b to a."],
    }


def test_the_payload_survives_the_trip_to_the_browser_unchanged():
    guide = plain_guide(ORDER, JOINTS)
    assert json.loads(json.dumps(guide.as_payload())) == guide.as_payload()
