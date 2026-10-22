"""Numbers the frontend's source declares, read for the engine's mirror tests.

The engine and the frontend share no code, so a mirror test reads the frontend's
constant from its source and holds it against the engine's own value.
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"


def ts_number(path: Path, name: str) -> float:
    """The number ``const <name>`` is declared as in ``path``, exported or not."""
    found = re.search(
        rf"^(?:export )?const {name} = ([0-9.eE+-]+);", path.read_text(), re.MULTILINE
    )
    assert found, f"{name} is no longer a numeric constant in {path.name}"
    return float(found.group(1))
