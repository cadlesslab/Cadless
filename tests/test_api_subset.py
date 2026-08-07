"""Invariants for the API-subset policy data."""

import ast

from cadless import api_subset as a


def test_allowed_and_banned_modules_are_disjoint():
    assert a.ALLOWED_IMPORT_MODULES.isdisjoint(a.BANNED_IMPORT_MODULES)


def test_is_module_allowed_handles_submodules():
    assert a.is_module_allowed("build123d")
    assert a.is_module_allowed("build123d.objects")  # submodule of an allowed pkg
    assert not a.is_module_allowed("os")
    assert not a.is_module_allowed("os.path")


def test_examples_are_syntactically_valid_python():
    # Both examples must parse; only their *policy* validity differs.
    ast.parse(a.ALLOWED_EXAMPLE)
    ast.parse(a.REJECTED_EXAMPLE)


def test_allowed_example_only_imports_allowed_modules():
    tree = ast.parse(a.ALLOWED_EXAMPLE)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert a.is_module_allowed(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert a.is_module_allowed(alias.name)


def test_rejected_example_trips_the_policy():
    tree = ast.parse(a.REJECTED_EXAMPLE)
    banned_hits = [
        n.names[0].name
        for n in ast.walk(tree)
        if isinstance(n, ast.Import) and not a.is_module_allowed(n.names[0].name)
    ]
    assert "os" in banned_hits


def test_describe_policy_snapshot():
    policy = a.describe_policy()
    assert policy["result_variable"] == "result"
    assert "build123d" in policy["allowed_import_modules"]
    assert "subprocess" in policy["banned_import_modules"]
