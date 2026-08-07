"""Toolchain smoke test — verifies the package imports and the test runner works."""

import cadless


def test_package_imports():
    assert cadless.__version__ == "1.0.0"
