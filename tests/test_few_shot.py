"""Every curated few-shot example must compile AND produce a real solid.

This is the AC for (">=5 few-shot examples, each compiles under
build123d"). Marked `build123d` because it executes the OCCT geometry kernel.
"""

import pytest

from cadless import api_subset
from cadless.few_shot import FEW_SHOT
from cadless.system_prompt import SYSTEM_PROMPT

pytestmark = pytest.mark.build123d


def test_at_least_five_examples():
    assert len(FEW_SHOT) >= 5


def test_system_prompt_mentions_result_and_allowed_surface():
    assert api_subset.RESULT_VARIABLE in SYSTEM_PROMPT
    assert "build123d" in SYSTEM_PROMPT
    assert "network" in SYSTEM_PROMPT.lower()


@pytest.mark.parametrize("example", FEW_SHOT, ids=lambda e: e.prompt[:40])
def test_example_compiles_and_builds_a_solid(example):
    ns: dict = {}
    exec(compile(example.code, "<few_shot>", "exec"), ns)  # noqa: S102 - trusted curated code
    assert api_subset.RESULT_VARIABLE in ns, "example must assign `result`"
    result = ns[api_subset.RESULT_VARIABLE]
    volume = result.volume  # build123d Shape API
    assert volume > 0, f"expected a non-empty solid, got volume={volume}"


def test_examples_only_use_allowed_imports():
    import ast

    for ex in FEW_SHOT:
        tree = ast.parse(ex.code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert api_subset.is_module_allowed(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert api_subset.is_module_allowed(alias.name)
