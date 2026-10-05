"""Toolchain smoke test — verifies the package imports and the test runner works."""

import re

import cadless


def test_package_imports():
    # A shape rather than a number: every release pull request moves the number.
    assert re.fullmatch(r"\d+\.\d+\.\d+", cadless.__version__)
