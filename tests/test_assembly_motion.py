"""What makes a motion one the viewport plays, judged the way the viewport judges it.

The viewport refuses a motion that does not describe the parts on screen, and
what that looks like is a model with no player and nothing failing. These hold
the Python statement of its rules to that: a playable motion has nothing wrong
with it, and each rule broken on its own is named on its own -- one problem,
and the right one, so a rule that went missing would leave its case with none.
"""

from __future__ import annotations

import json
import re

import pytest

from cadless.assembly_motion import MAX_MOTION_BYTES, motion_problems, read_motion

# A field given this is taken out of the motion rather than set.
_DROP = object()

_SHELF_PARTS = [0, 1, 2]
_BRACKET_PARTS = [0, 1, 2, 3]


def _shelf() -> dict:
    """Three parts and the five fields alone: a base that stays, a lid lifted off
    upward and a panel slid out sideways."""
    return {
        "order": [0, 1, 2],
        "releases": [[], [0, 0, 1], [1, 0, 0]],
        "stretch": [1, 1, 1],
        "explode_fraction": 0.35,
    }


def _bracket() -> dict:
    """Four parts, grouped: a base that stays; a bracket and the pin through it,
    put together off the model and lowered onto it as one piece, the pin drawn
    out of the bracket sideways once the piece is off; and a cover slid on last."""
    return {
        "order": [0, 1, 2, 3],
        "releases": [[], [0, 0, 1], [0, 0, 1], [1, 0, 0]],
        "stretch": [1, 1, 2, 1],
        "explode_fraction": 0.35,
        "travel": [0, 0.5, 0.75, 0.4],
        "steps": [[0], [1, 2], [3]],
        "subassemblies": [{"parts": [1, 2], "splits": [[], [0, 1, 0]], "split_travel": [0, 0.25]}],
    }


def _with(motion: dict, **fields) -> dict:
    """``motion`` with ``fields`` set, any given as ``_DROP`` taken out."""
    changed = {**motion, **fields}
    return {key: value for key, value in changed.items() if value is not _DROP}


def _with_entry(**fields) -> dict:
    """The bracket with ``fields`` of its one sub-assembly set."""
    motion = _bracket()
    motion["subassemblies"] = [{**motion["subassemblies"][0], **fields}]
    return motion


def _the_one_problem(motion: object, parts: list[int]) -> str:
    problems = motion_problems(motion, parts)
    assert len(problems) == 1, problems
    return problems[0]


# --- playable -----------------------------------------------------------------


def test_a_motion_of_the_five_fields_has_nothing_wrong_with_it():
    assert motion_problems(_shelf(), _SHELF_PARTS) == []


def test_a_motion_of_runs_and_a_sub_assembly_has_nothing_wrong_with_it():
    assert motion_problems(_bracket(), _BRACKET_PARTS) == []


def test_a_sub_assembly_that_stays_and_only_comes_apart_has_nothing_wrong_with_it():
    # A base made of two pieces: neither leaves the model, so neither has a
    # heading in releases, and off it they part all the same.
    motion = _with(
        _bracket(),
        releases=[[], [], [0, 0, 1], [1, 0, 0]],
        travel=[0, 0, 0.75, 0.4],
        steps=[[0, 1], [2], [3]],
        subassemblies=[{"parts": [0, 1], "splits": [[], [0, 0, 1]], "split_travel": [0, 0.3]}],
    )
    assert motion_problems(motion, _BRACKET_PARTS) == []


def test_keys_it_does_not_know_are_ignored_at_the_top_and_in_a_sub_assembly():
    motion = _bracket()
    motion["joints"] = {"anything": [1, "two", None]}
    motion["subassemblies"][0]["colour"] = "red"
    assert motion_problems(motion, _BRACKET_PARTS) == []


def test_a_whole_number_written_as_a_float_is_a_whole_number():
    # As the viewport reads JSON, 2.0 and 2 are the same number.
    motion = _with(
        _bracket(),
        order=[0.0, 1.0, 2.0, 3.0],
        stretch=[1.0, 1.0, 2.0, 1.0],
        steps=[[0.0], [1.0, 2.0], [3.0]],
    )
    motion["subassemblies"][0]["parts"] = [1.0, 2.0]
    assert motion_problems(motion, _BRACKET_PARTS) == []


def test_a_heading_just_longer_than_the_threshold_is_a_heading():
    assert motion_problems(_with(_shelf(), releases=[[], [0, 0, 2e-9], []]), _SHELF_PARTS) == []


def test_the_widest_spacing_and_the_furthest_travel_accepted_are_accepted():
    motion = _with(_shelf(), explode_fraction=1, travel=[0, 8.196, 0.4])
    assert motion_problems(motion, _SHELF_PARTS) == []


def test_members_whose_headings_differ_by_float_noise_come_off_as_one():
    # Chosen so the dot product of the two unit headings comes out just short
    # of 1: only the tolerance makes them one direction.
    motion = _with(_bracket(), releases=[[], [1, 1, 0], [1, 1, 1e-9], [1, 0, 0]])
    assert motion_problems(motion, _BRACKET_PARTS) == []


def test_members_whose_headings_differ_by_an_angle_one_can_see_are_refused():
    motion = _with(_bracket(), releases=[[], [0, 0, 1], [0.01, 0, 1], [1, 0, 0]])
    problem = _the_one_problem(motion, _BRACKET_PARTS)
    assert problem.startswith("subassembly 0's parts do not all come off along one heading")


# --- each rule, broken on its own -----------------------------------------------


@pytest.mark.parametrize(
    "payload", [[], "a motion", None, 3, True], ids=["list", "text", "null", "number", "bool"]
)
def test_a_motion_that_is_not_an_object_is_refused(payload):
    assert _the_one_problem(payload, _SHELF_PARTS).startswith("the motion is not a JSON object")


@pytest.mark.parametrize(
    ("field", "value", "pattern"),
    [
        pytest.param("order", _DROP, r"order is missing or is not a list$", id="no order"),
        pytest.param("order", [0, 1], r"order lists 2 parts, but 3 are drawn$", id="short order"),
        pytest.param("order", [0, 1.5, 2], r"order entry 1 is not a whole number$", id="fraction"),
        pytest.param(
            "order", [0, 1, 1], r"order does not name each drawn part exactly once$", id="twice"
        ),
        pytest.param("releases", _DROP, r"releases is missing or is not a list$", id="no releases"),
        pytest.param(
            "releases",
            [[], [0, 0, 1]],
            r"releases has 2 slots, but 3 parts are drawn$",
            id="short releases",
        ),
        pytest.param(
            "releases",
            [[], [0, 0, 1], "up"],
            r"releases slot 2 is neither \[\] nor three finite numbers$",
            id="slot not a list",
        ),
        pytest.param(
            "releases", [[], [0, 1], [1, 0, 0]], r"releases slot 1 is neither", id="two numbers"
        ),
        pytest.param(
            "releases",
            [[], [0, 0, float("nan")], [1, 0, 0]],
            r"releases slot 1 is neither",
            id="nan in a slot",
        ),
        pytest.param("releases", [[], [], []], r"no part has a heading", id="nothing moves"),
        pytest.param(
            "releases", [[], [0, 0, 1e-10], []], r"no part has a heading", id="too short to move"
        ),
        pytest.param("stretch", _DROP, r"stretch is missing or is not a list$", id="no stretch"),
        pytest.param(
            "stretch", [1, 1], r"stretch has 2 entries, but 3 parts are drawn$", id="short stretch"
        ),
        pytest.param(
            "stretch",
            [0, 1, 1],
            r"stretch entry 0 is not a whole number from 1 to 3$",
            id="stretch below 1",
        ),
        pytest.param(
            "stretch", [1, 4, 1], r"stretch entry 1 is not a whole number", id="stretch past count"
        ),
        pytest.param(
            "stretch", [1, 1.5, 1], r"stretch entry 1 is not a whole number", id="stretch fraction"
        ),
        pytest.param(
            "explode_fraction",
            _DROP,
            r"explode_fraction is missing or is not a finite number$",
            id="no fraction",
        ),
        pytest.param(
            "explode_fraction",
            float("inf"),
            r"explode_fraction is missing or is not a finite number$",
            id="infinite fraction",
        ),
        pytest.param(
            "explode_fraction",
            0,
            r"explode_fraction is not above 0 and at most 1$",
            id="no spacing",
        ),
        pytest.param(
            "explode_fraction", 1.01, r"explode_fraction is not above 0", id="spacing past model"
        ),
        pytest.param("travel", None, r"travel is given but is not a list$", id="null travel"),
        pytest.param(
            "travel", [0, 0.5], r"travel has 2 entries, but 3 parts are drawn$", id="short travel"
        ),
        pytest.param(
            "travel",
            [0, -0.1, 0.4],
            r"travel entry 1 is not a finite number from 0 to ",
            id="travel below 0",
        ),
        pytest.param(
            "travel",
            [0, 8.2, 0.4],
            r"travel entry 1 is not a finite number from 0 to ",
            id="travel past the cap",
        ),
        pytest.param(
            "travel",
            [0, 0, 0.4],
            r"travel entry 1 is 0, but part 1 has a heading to come off along$",
            id="heading with no travel",
        ),
        pytest.param("steps", None, r"steps is given but is not a list$", id="null steps"),
        pytest.param(
            "steps",
            [[0], [], [1, 2]],
            r"steps run 1 is not a non-empty list of whole numbers$",
            id="empty run",
        ),
        pytest.param(
            "steps", [[0], 1, [2]], r"steps run 1 is not a non-empty list", id="run not a list"
        ),
        pytest.param(
            "steps", [[0], [1.5, 2]], r"steps run 1 is not a non-empty list", id="run of fractions"
        ),
        pytest.param(
            "steps",
            [[0], [2, 1]],
            r"steps do not add up to the order, run after run$",
            id="runs out of order",
        ),
        pytest.param(
            "steps", [[0], [1]], r"steps do not add up to the order", id="runs leave a part out"
        ),
        pytest.param(
            "subassemblies",
            [],
            r"subassemblies are given without steps$",
            id="sub-assemblies without steps",
        ),
    ],
)
def test_each_rule_of_the_five_fields_and_the_runs_is_named_on_its_own(field, value, pattern):
    problem = _the_one_problem(_with(_shelf(), **{field: value}), _SHELF_PARTS)
    assert re.match(pattern, problem), problem


def test_an_ordinal_with_no_release_slot_is_named():
    # Parts numbered from 1: the order names each of them, but the slots are
    # indexed by ordinal from 0, so the last part has none.
    problem = _the_one_problem(_with(_shelf(), order=[1, 2, 3]), [1, 2, 3])
    assert problem.startswith("order names a part releases has no slot for"), problem


_SHARING = [
    {"parts": [1, 2], "splits": [[], [0, 1, 0]], "split_travel": [0, 0.25]},
    {"parts": [2, 1], "splits": [[], []], "split_travel": [0, 0]},
]


@pytest.mark.parametrize(
    ("motion", "pattern"),
    [
        pytest.param(
            _with(_bracket(), subassemblies=None),
            r"subassemblies is given but is not a list$",
            id="null sub-assemblies",
        ),
        pytest.param(
            _with(_bracket(), subassemblies=[[1, 2]]),
            r"subassembly 0 is not an object$",
            id="entry not an object",
        ),
        pytest.param(
            _with_entry(parts=[1]),
            r"subassembly 0's parts are not two or more different whole numbers$",
            id="one member",
        ),
        pytest.param(
            _with_entry(parts=[1, 1]),
            r"subassembly 0's parts are not two or more different whole numbers$",
            id="a member twice",
        ),
        pytest.param(
            _with_entry(parts=[1, 2.5]),
            r"subassembly 0's parts are not two or more",
            id="member a fraction",
        ),
        pytest.param(
            _with(_bracket(), steps=[[0], [1], [2], [3]]),
            r"subassembly 0's parts are not all in one run of steps$",
            id="members in two runs",
        ),
        pytest.param(
            _with(_bracket(), subassemblies=_SHARING),
            r"subassembly 1 shares a part with an earlier one$",
            id="a part in two sub-assemblies",
        ),
        pytest.param(
            _with_entry(splits=None),
            r"subassembly 0's splits are not a list with a slot for each of its parts$",
            id="no splits",
        ),
        pytest.param(
            _with_entry(splits=[[]]),
            r"subassembly 0's splits are not a list with a slot for each",
            id="short splits",
        ),
        pytest.param(
            _with_entry(splits=[[], [0, 1]]),
            r"subassembly 0's split slot 1 is neither \[\] nor three finite numbers$",
            id="split of two numbers",
        ),
        pytest.param(
            _with_entry(split_travel=None),
            r"subassembly 0's split_travel is not a list with an entry for each of its parts$",
            id="no split travel",
        ),
        pytest.param(
            _with_entry(split_travel=[0]),
            r"subassembly 0's split_travel is not a list with an entry for each",
            id="short split travel",
        ),
        pytest.param(
            _with_entry(split_travel=[0, -0.25]),
            r"subassembly 0's split_travel entry 1 is not a finite number from 0 to ",
            id="split travel below 0",
        ),
        pytest.param(
            _with_entry(split_travel=[0, 0]),
            r"subassembly 0's split_travel entry 1 is 0, but that member splits off along a "
            r"heading$",
            id="split with no travel",
        ),
        pytest.param(
            _with_entry(split_travel=[0.1, 0.25]),
            r"subassembly 0's split_travel entry 0 is above 0, but that member has no split "
            r"heading$",
            id="travel with no split",
        ),
        pytest.param(
            _with(_bracket(), releases=[[], [0, 0, 1], [1, 0, 0], [1, 0, 0]]),
            r"subassembly 0's parts do not all come off along one heading$",
            id="members leave two ways",
        ),
        pytest.param(
            _with(_bracket(), releases=[[], [0, 0, 1], [], [1, 0, 0]]),
            r"subassembly 0's parts do not all come off along one heading$",
            id="one member stays",
        ),
    ],
)
def test_each_rule_of_a_sub_assembly_is_named_on_its_own(motion, pattern):
    problem = _the_one_problem(motion, _BRACKET_PARTS)
    assert re.match(pattern, problem), problem


# Each would pass for a whole number or a number if a boolean counted as one:
# in Python, True is 1 and False is 0.
@pytest.mark.parametrize(
    ("motion", "parts", "pattern"),
    [
        pytest.param(
            _with(_shelf(), order=[False, 1, 2]), _SHELF_PARTS, r"order entry 0 ", id="order"
        ),
        pytest.param(
            _with(_shelf(), stretch=[True, 1, 1]), _SHELF_PARTS, r"stretch entry 0 ", id="stretch"
        ),
        pytest.param(
            _with(_shelf(), explode_fraction=True),
            _SHELF_PARTS,
            r"explode_fraction is missing or is not a finite number",
            id="fraction",
        ),
        pytest.param(
            _with(_shelf(), travel=[0, True, 0.4]), _SHELF_PARTS, r"travel entry 1 ", id="travel"
        ),
        pytest.param(
            _with(_shelf(), releases=[[], [False, False, True], [1, 0, 0]]),
            _SHELF_PARTS,
            r"releases slot 1 ",
            id="heading",
        ),
        pytest.param(
            _with(_bracket(), steps=[[False], [1, 2], [3]]),
            _BRACKET_PARTS,
            r"steps run 0 ",
            id="run",
        ),
        pytest.param(
            _with_entry(parts=[True, 2]),
            _BRACKET_PARTS,
            r"subassembly 0's parts are not",
            id="member",
        ),
        pytest.param(
            _with_entry(splits=[[], [False, True, False]]),
            _BRACKET_PARTS,
            r"subassembly 0's split slot 1 ",
            id="split",
        ),
        pytest.param(
            _with_entry(split_travel=[False, 0.25]),
            _BRACKET_PARTS,
            r"subassembly 0's split_travel entry 0 ",
            id="split travel",
        ),
    ],
)
def test_a_boolean_is_never_a_number(motion, parts, pattern):
    problem = _the_one_problem(motion, parts)
    assert re.match(pattern, problem), problem


# --- never raises --------------------------------------------------------------


def _nested(depth: int) -> list:
    """A list nested ``depth`` deep, built without recursion."""
    value: list = []
    for _ in range(depth):
        value = [value]
    return value


_ODD_VALUES = [
    None,
    True,
    0,
    -1,
    1.5,
    float("nan"),
    float("-inf"),
    10**400,  # too large for a float
    10**5000,  # too long to print
    "x",
    [],
    {},
    [None],
    [[]],
    [{}],
    [[None, "x", {}]],
    {"parts": {}},
    _nested(100_000),
]


def _placed(where: str, value: object) -> dict:
    """The bracket with ``value`` put at ``where``."""
    motion = _bracket()
    entry = motion["subassemblies"][0]
    if where in motion:
        motion[where] = value
    elif where == "release slot":
        motion["releases"][1] = value
    elif where == "run":
        motion["steps"][1] = value
    elif where == "ordinal in a run":
        motion["steps"][1][0] = value
    elif where == "entry":
        motion["subassemblies"][0] = value
    elif where == "split slot":
        entry["splits"][1] = value
    else:
        entry[where] = value
    return motion


@pytest.mark.parametrize(
    "where",
    [
        "order",
        "releases",
        "stretch",
        "explode_fraction",
        "travel",
        "steps",
        "subassemblies",
        "release slot",
        "run",
        "ordinal in a run",
        "entry",
        "parts",
        "splits",
        "split slot",
        "split_travel",
    ],
)
def test_any_value_anywhere_is_judged_without_raising(where):
    for value in _ODD_VALUES:
        problems = motion_problems(_placed(where, value), _BRACKET_PARTS)
        assert all(isinstance(problem, str) for problem in problems)


# --- reading a file -------------------------------------------------------------


def _encoded(motion: dict) -> bytes:
    return json.dumps(motion).encode()


def test_a_file_that_plays_is_read_back_as_written():
    assert read_motion(_encoded(_bracket()), _BRACKET_PARTS) == _bracket()


def test_a_file_at_the_size_limit_is_read_and_one_byte_over_it_is_refused():
    at_limit = _encoded(_shelf()).ljust(MAX_MOTION_BYTES)
    assert read_motion(at_limit, _SHELF_PARTS) == _shelf()
    with pytest.raises(ValueError, match="more than the"):
        read_motion(at_limit + b" ", _SHELF_PARTS)


def test_a_file_over_the_limit_is_refused_before_any_of_it_is_decoded():
    # Not UTF-8 either: refused for its size, so nothing got as far as decoding it.
    with pytest.raises(ValueError, match="more than the"):
        read_motion(b"\xff" * (MAX_MOTION_BYTES + 1), _SHELF_PARTS)


def test_a_file_that_is_not_utf8_is_refused():
    with pytest.raises(ValueError, match="UTF-8"):
        read_motion(b'{"order": "\xff"}', _SHELF_PARTS)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_a_number_json_has_no_spelling_for_is_refused_wherever_it_is(constant):
    # Under a key no rule reads, so what refuses it is the reading, not a rule.
    text = _encoded(_shelf())[:-1] + f', "note": {constant}}}'.encode()
    with pytest.raises(ValueError, match=constant):
        read_motion(text, _SHELF_PARTS)


def test_nesting_too_deep_to_read_is_refused_as_a_bad_file_not_a_crash():
    deep = b"[" * 100_000 + b"]" * 100_000
    text = _encoded(_shelf())[:-1] + b', "note": ' + deep + b"}"
    with pytest.raises(ValueError, match="nest"):
        read_motion(text, _SHELF_PARTS)


def test_a_file_whose_top_level_is_not_an_object_is_refused():
    with pytest.raises(ValueError, match="not a JSON object"):
        read_motion(b"[]", _SHELF_PARTS)


def test_a_file_that_would_not_play_is_refused_with_what_is_wrong():
    with pytest.raises(ValueError, match="order lists 2 parts, but 3 are drawn"):
        read_motion(_encoded(_with(_shelf(), order=[0, 1])), _SHELF_PARTS)
