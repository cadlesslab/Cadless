"""Reading a workflow file the way its tests need it, without a YAML parser.

PyYAML is not a dependency of this package, and the tests ask two narrow
questions of a workflow: what its wiring says once the comments are gone, and
what one step's shell script is, so it can be run.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path


def wiring(path: Path) -> str:
    """The workflow with its comment lines removed, so prose cannot satisfy an assertion."""
    text = path.read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def job(path: Path, name: str) -> str:
    """One job's block, from its key to the next key at the same indent."""
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  \S|\Z)", wiring(path), re.M | re.S)
    if not match:
        raise LookupError(f"no job {name!r} in {path.name}")
    return match.group(1)


def step_script(path: Path, name: str) -> str:
    """The `run:` script of the step named `name`, dedented and ready for bash."""
    lines = wiring(path).splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == f"- name: {name}"]
    if len(starts) != 1:
        raise LookupError(f"{len(starts)} steps named {name!r} in {path.name}")
    step_indent = len(lines[starts[0]]) - len(lines[starts[0]].lstrip())
    for i in range(starts[0] + 1, len(lines)):
        line = lines[i]
        indent = len(line) - len(line.lstrip())
        if line.strip() and indent <= step_indent:
            break
        if line.strip() == "run: |":
            body = []
            for following in lines[i + 1 :]:
                if following.strip() and len(following) - len(following.lstrip()) <= indent:
                    break
                body.append(following)
            return textwrap.dedent("\n".join(body)) + "\n"
        if line.strip().startswith("run: "):
            return line.strip()[len("run: ") :] + "\n"
    raise LookupError(f"step {name!r} in {path.name} has no run script")
