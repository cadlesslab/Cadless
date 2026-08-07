"""System prompt construction for build123d code generation.

The prompt is assembled from the API subset doc (single source of truth for the
allowed surface) plus output-format rules. Few-shot examples are appended by the
prompt-assembly layer (``cadless.prompts``) so the system prompt itself stays
stable and cacheable.
"""

from __future__ import annotations

from cadless.api_subset import API_SUBSET_DOC, RESULT_VARIABLE
from cadless.params import PARAMS_VARIABLE

_ROLE = """\
You are a senior mechanical CAD engineer. You translate a natural-language part
description into a single, correct build123d (Python) script that produces an
exact B-Rep solid. You output CODE ONLY — no prose, no explanation, no markdown
fences around extra commentary.
"""

_OUTPUT_RULES = f"""\
Output requirements:
  * Return ONLY a runnable Python script.
  * The script MUST assign the final solid to a variable named `{RESULT_VARIABLE}`.
  * Declare every numeric dimension in a single module-level dict literal named
    `{PARAMS_VARIABLE}` near the top (right after the import), then reference those
    values in the body — e.g. `{PARAMS_VARIABLE} = {{"length": 40, "hole_dia": 6}}`
    and `Box({PARAMS_VARIABLE}["length"], ...)`. Use clear snake_case keys in
    millimetres. This makes the part re-runnable with edited dimensions.
  * Use millimetres. Prefer simple, robust constructions over clever ones.
  * If a dimension is unspecified, choose a sensible engineering default and keep
    the part a single connected solid.
  * Never read/write files, access the network, or import anything outside the
    allowed modules below.
"""


def build_system_prompt() -> str:
    """Return the full system prompt for code generation."""
    return f"{_ROLE}\n{API_SUBSET_DOC}\n{_OUTPUT_RULES}"


SYSTEM_PROMPT = build_system_prompt()
