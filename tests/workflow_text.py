"""Reading a workflow file the way its tests need it, without a YAML parser.

PyYAML is not a dependency of this package, and the tests ask narrow questions
of a workflow: what its wiring says once the comments are gone, and what one
step's shell script, id and environment are, so the step can be run.
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


def step_names(path: Path) -> list[str]:
    """The names of the workflow's steps, in the order they are written."""
    return [
        line.strip()[len("- name: ") :]
        for line in wiring(path).splitlines()
        if line.strip().startswith("- name: ")
    ]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _step(path: Path, name: str) -> list[str]:
    """The lines of the step named `name`, from its `- name:` line to the next at its indent."""
    lines = wiring(path).splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == f"- name: {name}"]
    if len(starts) != 1:
        raise LookupError(f"{len(starts)} steps named {name!r} in {path.name}")
    end = starts[0] + 1
    while end < len(lines) and not (
        lines[end].strip() and _indent(lines[end]) <= _indent(lines[starts[0]])
    ):
        end += 1
    return lines[starts[0] : end]


def _keys(step: list[str]) -> dict[str, int]:
    """Where each of the step's own keys starts, by line, keyed by name."""
    indent = _indent(step[0]) + 2
    return {
        line.strip().split(":", 1)[0]: i
        for i, line in enumerate(step[1:], start=1)
        if _indent(line) == indent and ":" in line
    }


def step_keys(path: Path, name: str) -> set[str]:
    """The keys the step named `name` sets besides its name."""
    return set(_keys(_step(path, name)))


def step_script(path: Path, name: str) -> str:
    """The `run:` script of the step named `name`, dedented and ready for bash."""
    step = _step(path, name)
    run = _keys(step).get("run")
    if run is None:
        raise LookupError(f"step {name!r} in {path.name} has no run script")
    value = step[run].strip()[len("run: ") :]
    if value[:1] in ("|", ">") and value != "|":
        raise LookupError(f"step {name!r} in {path.name} has a {value!r} script, not read here")
    if value != "|":
        return value + "\n"
    body = []
    for line in step[run + 1 :]:
        if line.strip() and _indent(line) <= _indent(step[run]):
            break
        body.append(line)
    return textwrap.dedent("\n".join(body)) + "\n"


def step_id(path: Path, name: str) -> str | None:
    """The `id:` of the step named `name`, which its outputs are read by."""
    step = _step(path, name)
    line = _keys(step).get("id")
    return None if line is None else step[line].strip()[len("id: ") :]


def step_env(path: Path, name: str) -> dict[str, str]:
    """The `env:` of the step named `name`, each value as written."""
    step = _step(path, name)
    start = _keys(step).get("env")
    if start is None:
        return {}
    env = {}
    for line in step[start + 1 :]:
        if not line.strip():
            continue
        if _indent(line) <= _indent(step[start]):
            break
        key, _, value = line.strip().partition(": ")
        if not value or value[:1] in ("|", ">"):
            raise LookupError(f"step {name!r} in {path.name} sets {key} other than on one line")
        env[key] = value
    return env
