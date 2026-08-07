"""Structured parameter block tests: extraction + override splicing.

Pure string/AST manipulation — no build123d, no Bedrock.
"""

import pytest

from cadless.params import apply_param_overrides, extract_params

WITH_PARAMS = """\
from build123d import *

params = {"length": 40, "width": 20, "hole_dia": 6}
plate = Box(params["length"], params["width"], 8)
result = plate - Cylinder(radius=params["hole_dia"] / 2, height=8)
"""

NO_PARAMS = """\
from build123d import *

result = Box(10, 10, 10)
"""

MULTILINE_PARAMS = """\
from build123d import *

params = {
    "size": 20,
    "fillet": 2,
}
result = fillet(Box(params["size"], params["size"], params["size"]).edges(),
                radius=params["fillet"])
"""


# ---- extract_params -------------------------------------------------------


def test_extract_params_reads_literal_dict():
    assert extract_params(WITH_PARAMS) == {"length": 40, "width": 20, "hole_dia": 6}


def test_extract_params_absent_returns_empty():
    assert extract_params(NO_PARAMS) == {}


def test_extract_params_handles_multiline_literal():
    assert extract_params(MULTILINE_PARAMS) == {"size": 20, "fillet": 2}


def test_extract_params_ignores_non_literal_dict():
    # A params built from an expression is not a safe literal -> {}.
    code = "from build123d import *\nparams = dict(a=1)\nresult = Box(1, 1, 1)\n"
    assert extract_params(code) == {}


def test_extract_params_syntax_error_returns_empty():
    assert extract_params("def (:\n") == {}


# ---- apply_param_overrides ------------------------------------------------


def test_apply_overrides_updates_value_and_preserves_rest():
    out = apply_param_overrides(WITH_PARAMS, {"hole_dia": 10})
    assert extract_params(out) == {"length": 40, "width": 20, "hole_dia": 10}
    # body is preserved verbatim
    assert 'plate = Box(params["length"], params["width"], 8)' in out
    # still parses
    compile(out, "<test>", "exec")


def test_apply_overrides_preserves_key_order():
    out = apply_param_overrides(WITH_PARAMS, {"length": 99})
    assert list(extract_params(out)) == ["length", "width", "hole_dia"]


def test_apply_overrides_multiline_block():
    out = apply_param_overrides(MULTILINE_PARAMS, {"size": 30})
    assert extract_params(out) == {"size": 30, "fillet": 2}
    assert 'radius=params["fillet"]' in out  # trailing body intact


def test_apply_overrides_unknown_param_raises():
    with pytest.raises(ValueError, match="unknown parameter"):
        apply_param_overrides(WITH_PARAMS, {"depth": 5})


def test_apply_overrides_non_literal_value_raises():
    with pytest.raises(ValueError, match="numbers, strings, or booleans"):
        apply_param_overrides(WITH_PARAMS, {"length": [1, 2, 3]})


def test_apply_overrides_no_params_block_raises():
    with pytest.raises(ValueError, match="no `params` block"):
        apply_param_overrides(NO_PARAMS, {"length": 5})
