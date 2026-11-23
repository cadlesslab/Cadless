"""What a generated script runs with: its builtins, its module views, and the gate in front.

Most tests here run code in the namespace a script gets *without* the static
gate in front of it, so that each pins what the namespace alone hands out.
Marked build123d: the build123d view is built from the real package.
"""

import math

import pytest

from cadless.script_runtime import ScriptRefused, run_script, script_globals

pytestmark = pytest.mark.build123d


def _run_ungated(code: str) -> dict:
    """``code`` run in a script's namespace with the static gate skipped."""
    namespace = script_globals()
    exec(compile(code, "<test>", "exec"), namespace)  # noqa: S102 - the test's own fixed code
    return namespace


@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("from build123d import os\n", ImportError),
        ("import build123d\nresult = build123d.os\n", AttributeError),
    ],
    ids=["by name", "as an attribute"],
)
def test_the_build123d_view_holds_no_module(code, error):
    with pytest.raises(error):
        _run_ungated(code)


def test_a_name_the_view_lacks_never_falls_back_to_the_real_package():
    # Named as the package, the view would let the import machinery find the
    # real submodule of that name.
    with pytest.raises(ImportError):
        _run_ungated("from build123d import build_common\n")


@pytest.mark.parametrize(
    "code",
    ["import build123d.topology\n", "from .build123d import Box\n", "import os\n"],
    ids=["a submodule", "relative", "another module"],
)
def test_the_import_guard_hands_out_the_three_views_alone(code):
    with pytest.raises(ImportError):
        _run_ungated(code)


def test_a_view_never_holds_a_module():
    # A later build123d could list a module among its public names; the view
    # drops it rather than hand it on.
    from cadless.script_runtime import _view

    view = _view("example", {"pi": math.pi, "math": math})

    assert (view.__all__, hasattr(view, "math")) == (["pi"], False)


@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("from build123d import *\nresult = export_stl\n", NameError),
        ("from build123d import export_stl\n", ImportError),
    ],
    ids=["after a star import", "imported by name"],
)
def test_withheld_functions_are_not_in_the_view(code, error):
    with pytest.raises(error):
        _run_ungated(code)


def test_a_name_a_later_build123d_adds_is_not_handed_out_until_reviewed(monkeypatch):
    import build123d

    monkeypatch.setattr(build123d, "export_new_format", lambda *args: None, raising=False)
    monkeypatch.setattr(build123d, "__all__", [*build123d.__all__, "export_new_format"])

    with pytest.raises(NameError):
        _run_ungated("from build123d import *\nresult = export_new_format\n")


def test_the_copy_view_holds_copy_s_functions_alone():
    with pytest.raises(AttributeError):
        _run_ungated("import copy\nresult = copy.dispatch_table\n")


def test_deepcopy_takes_no_memo():
    # The memo would hand back the originals deepcopy walked through.
    with pytest.raises(TypeError):
        _run_ungated("import copy\nresult = copy.deepcopy([1], {})\n")


@pytest.mark.parametrize(
    "code",
    [
        "result = type(1)\n",
        "raise SystemExit(0)\n",
        "result = __loader__\n",
        "result = open('missing')\n",
        "class A:\n    pass\n",
    ],
    ids=["type", "SystemExit", "the loader", "open", "a class"],
)
def test_a_script_gets_only_the_allow_listed_builtins(code):
    with pytest.raises(NameError):
        _run_ungated(code)


def test_print_goes_to_stderr(capsys):
    _run_ungated("print('built')\n")

    out, err = capsys.readouterr()
    assert (out, err) == ("", "built\n")


def test_views_are_fresh_for_every_script():
    _run_ungated("import math\nmath.pi = 3\n")

    assert _run_ungated("import math\nresult = math.pi\n")["result"] == pytest.approx(3.14159, 1e-5)
    assert math.pi == pytest.approx(3.14159, 1e-5)


def test_a_build_written_with_every_import_form_runs():
    code = (
        "from build123d import *\n"
        "from build123d import Cylinder as Cyl\n"
        "import build123d as bd\n"
        "import math\n"
        "import copy\n"
        "parts = [copy.deepcopy(bd.Box(2, 2, 2)), copy.copy(Pos(5, 0, 0) * Cyl(1, 2))]\n"
        "result = Compound(children=parts)\n"
        "assert math.isclose(sum(p.volume for p in parts), 8 + 2 * math.pi, rel_tol=1e-6)\n"
    )

    assert _run_ungated(code)["result"].volume > 0


def test_a_script_that_changes_a_build123d_class_is_refused():
    # The gate lets a script set a part's placement; aimed at a class, the same
    # line would change how every part of that kind is read afterwards.
    from build123d import Box

    try:
        with pytest.raises(ScriptRefused) as refused:
            run_script(
                "from build123d import *\nBox.location = Location()\nresult = Box(1, 1, 1)\n",
                "<generated>",
            )
        restored = "location" not in vars(Box)
    finally:
        if "location" in vars(Box):
            delattr(Box, "location")

    assert "changed build123d's own classes: Box.location" in str(refused.value)
    assert restored  # put back for whatever runs next


@pytest.mark.parametrize(
    "ending",
    ["result = Box(1, 1, 1)\n", "result = Box(1, 1, 1)\nraise ValueError('after the change')\n"],
    ids=["finished", "raised"],
)
def test_a_script_that_changes_a_table_a_build123d_class_holds_is_refused_and_undone(ending):
    # Volume is read through this table, so a script that rewrote it would have
    # its build report whatever it planted there -- and, run in this process, so
    # would every script after it, however the first one ended.
    from build123d import Solid

    table = Solid.shape_properties_LUT
    kept = dict(table)
    code = "from build123d import *\nSolid.shape_properties_LUT.clear()\n" + ending

    try:
        with pytest.raises(ScriptRefused) as refused:
            run_script(code, "<generated>")
        restored = table == kept
    finally:
        table.update(kept)

    assert "shape_properties_LUT changed" in str(refused.value)
    assert restored


def test_none_set_on_a_class_that_did_not_hold_the_name_is_seen():
    from build123d import Box

    try:
        with pytest.raises(ScriptRefused):
            run_script(
                "from build123d import *\nBox.parent = None\nresult = Box(1, 1, 1)\n",
                "<generated>",
            )
    finally:
        if "parent" in vars(Box):
            delattr(Box, "parent")


def test_a_placement_a_class_defines_itself_is_put_back():
    # Plane defines its own location; replaced on the class, every plane read
    # afterwards in this process would carry the script's placement.
    from build123d import Plane

    kept = vars(Plane)["location"]
    try:
        with pytest.raises(ScriptRefused) as refused:
            run_script(
                "from build123d import *\nPlane.location = Location()\nresult = Box(1, 1, 1)\n",
                "<generated>",
            )
        restored = vars(Plane)["location"] is kept
    finally:
        Plane.location = kept

    assert "Plane.location set on the class" in str(refused.value)
    assert restored


def test_a_dict_a_script_is_handed_is_its_own():
    import build123d

    _run_ungated("from build123d import *\nUNITS_PER_METER.clear()\n")

    assert build123d.UNITS_PER_METER


def test_run_script_leaves_annotations_unevaluated():
    # As scripts have always been compiled: an annotation naming nothing runs.
    code = "def f(x: Undefined) -> Missing:\n    return x\nresult = f(2)\n"

    assert run_script(code, "<generated>")["result"] == 2


def test_run_script_refuses_what_the_gate_refuses():
    with pytest.raises(ScriptRefused) as refused:
        run_script("g = (x for x in [1])\nresult = g.gi_frame\n", "<generated>")

    assert any(r.startswith("interpreter-internals") for r in refused.value.reasons)
