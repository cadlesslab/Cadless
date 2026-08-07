"""Static-validation tests."""

from cadless.api_subset import ALLOWED_EXAMPLE, REJECTED_EXAMPLE
from cadless.few_shot import FEW_SHOT
from cadless.validation import validate_code


def test_allowed_example_passes():
    res = validate_code(ALLOWED_EXAMPLE)
    assert res.ok, res.reasons


def test_all_few_shot_pass_validation():
    for ex in FEW_SHOT:
        assert validate_code(ex.code).ok, (ex.prompt, validate_code(ex.code).reasons)


def test_rejected_example_fails_for_import_and_more():
    res = validate_code(REJECTED_EXAMPLE)
    assert not res.ok
    assert any("disallowed import: os" in r for r in res.reasons)


def test_banned_open_call():
    res = validate_code("from build123d import *\nopen('/etc/passwd')\nresult = Box(1,1,1)")
    assert not res.ok
    assert any("open" in r for r in res.reasons)


def test_dunder_escape_blocked():
    code = "result = ().__class__.__bases__[0].__subclasses__()"
    res = validate_code(code)
    assert not res.ok
    assert any("dunder" in r for r in res.reasons)


def test_missing_result_fails():
    res = validate_code("from build123d import *\nx = Box(1,1,1)")
    assert not res.ok
    assert any("result" in r for r in res.reasons)


def test_tuple_unpacking_result_ok():
    res = validate_code("from build123d import *\nresult, leftover = Box(1,1,1), 0")
    assert res.ok, res.reasons


def test_syntax_error_reported():
    res = validate_code("from build123d import *\nresult = Box(1,1,")
    assert not res.ok
    assert any("syntax error" in r for r in res.reasons)


def test_eval_and_import_dunder_banned():
    res = validate_code("result = eval('1')\n__import__('os')")
    assert not res.ok
    assert any("eval" in r for r in res.reasons)
    assert any("__import__" in r for r in res.reasons)


def test_bool_protocol():
    assert bool(validate_code(ALLOWED_EXAMPLE)) is True
    assert bool(validate_code("x = 1")) is False
