#!/usr/bin/env python3
"""Derive the versioned benchmark tiers from the catalog manifests.

The tiers under ``cadless/evalkit/tiers/`` are generated, not hand-written, so a
score that moves can be attributed: either the engine changed or the ruler did,
and the diff says which.

Two tiers, split by how much the prompt gives away rather than by how large the
model is:

* **easy** — each item's step-1 instruction. These state every dimension
  ("32 mm outer diameter, 13 mm inner diameter, 4 mm thickness"), so building
  them is close to transcription. Step 1 only: the step scripts are cumulative,
  so a later step's instruction describes one feature while its code re-emits
  everything before it, which would not be a fair standalone prompt.
* **hard** — the item-level description of the complex items. One line for a
  whole part, dimensions left to be inferred. This is where failure headroom
  lives, and failure headroom is the point: a benchmark everything passes
  cannot show that a quality feature helped.

Run ``python tools/build_eval_tiers.py`` to regenerate, or with ``--check`` to
assert the committed files still match the catalog.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = REPO_ROOT / "catalog"
TIERS_DIR = REPO_ROOT / "cadless" / "evalkit" / "tiers"

# Placed inside the gap the data actually shows. Sorted by total step lines the
# items run 188, 183, 180, 178, then fall to 143 -- a 35-line break, by far the
# widest in that region. An earlier eyeball threshold of 180 cut between 180 and
# 178 instead, splitting one cluster and dropping panel-mount-plate over a
# two-line difference. The boundary belongs in the break, not inside the group.
COMPLEX_LINE_THRESHOLD = 150


def _step_script(item_dir: Path, step: dict) -> Path | None:
    """Where a step's build123d source lives.

    The manifest's ``code`` field holds a *path* ("steps/01.py"), not the source,
    so it is what to follow — counting the field itself scores every step as one
    line. The zero-padded reconstruction is only a fallback for a manifest that
    omits ``code``.
    """
    code = step.get("code")
    if isinstance(code, str) and code.strip():
        return item_dir / code
    index = step.get("index")
    return item_dir / "steps" / f"{index:02d}.py" if index is not None else None


def _item_line_count(item_dir: Path, steps: list[dict]) -> tuple[int, list[str]]:
    """Total build123d lines for an item, plus any step scripts that are missing.

    Missing scripts are returned rather than silently counted as zero: a
    disappearing script would drag the total under the complexity threshold and
    drop the item out of the hard tier without a word, and ``--check`` cannot
    catch that because it compares against the committed file using this same
    function. A ruler must not shrink quietly.
    """
    total = 0
    missing: list[str] = []
    for step in steps:
        script = _step_script(item_dir, step)
        if script is None or not script.is_file():
            missing.append(str(step.get("code") or step.get("index")))
            continue
        total += len(script.read_text(encoding="utf-8").splitlines())
    return total, missing


def _max_bodies(steps: list[dict]) -> int:
    # expected_bodies is absent or null on single-solid items rather than 1.
    return max((s.get("expected_bodies") or 1) for s in steps) if steps else 1


def is_hard(description: str | None, step_lines: int, max_bodies: int) -> bool:
    """Whether an item can supply a hard prompt.

    Multi-body items qualify regardless of size: composing separate solids with
    stated clearances is a different capability from adding features to one, and
    bedside-table is only 73 lines yet is a two-body compound.
    """
    if not description:
        return False
    return step_lines >= COMPLEX_LINE_THRESHOLD or max_bodies > 1


def collect() -> tuple[list[dict], list[dict], list[str]]:
    """Return (easy rows, hard rows, notes about what was left out)."""
    easy: list[dict] = []
    hard: list[dict] = []
    skipped: list[str] = []

    for catalog_dir in sorted(p for p in CATALOG_ROOT.iterdir() if p.is_dir()):
        for item_dir in sorted(p for p in catalog_dir.iterdir() if p.is_dir()):
            manifest_path = item_dir / "manifest.json"
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            item_id = manifest.get("id") or item_dir.name
            steps = manifest.get("steps") or []
            qualified = f"{catalog_dir.name}/{item_id}"

            first = next((s for s in steps if s.get("index") == 1), None)
            instruction = (first or {}).get("instruction")
            if instruction:
                easy.append({"id": f"{qualified}#1", "prompt": instruction})
            else:
                skipped.append(f"{qualified}: no step-1 instruction")

            description = manifest.get("description")
            total_lines, missing = _item_line_count(item_dir, steps)
            if missing:
                skipped.append(
                    f"{qualified}: step script(s) missing ({', '.join(missing)}) -- "
                    f"line count is short by that much and the tier may be wrong"
                )
            bodies = _max_bodies(steps)
            if is_hard(description, total_lines, bodies):
                hard.append({"id": qualified, "prompt": description})
            elif not description:
                skipped.append(f"{qualified}: no description, cannot supply a hard prompt")

    easy.sort(key=lambda r: r["id"])
    hard.sort(key=lambda r: r["id"])
    return easy, hard, skipped


def render(rows: list[dict]) -> str:
    # ensure_ascii=True (the default) keeps the committed tiers pure ASCII. The
    # reader specifies utf-8 explicitly, so this is belt-and-braces rather than
    # load-bearing -- it also keeps the diff of a prompt change readable as
    # bytes. json.loads restores the real characters either way.
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed tiers differ from what the catalog produces",
    )
    args = parser.parse_args(argv)

    easy, hard, skipped = collect()
    tiers = {"easy": easy, "hard": hard}

    # Printed on both paths: a note about a missing step script is exactly the
    # thing --check cannot detect on its own, since it compares the committed
    # file against the same shortened count.
    for note in skipped:
        print(f"  left out -- {note}", file=sys.stderr)

    if args.check:
        for name, rows in tiers.items():
            path = TIERS_DIR / f"{name}.jsonl"
            current = path.read_text(encoding="utf-8") if path.is_file() else ""
            if current != render(rows):
                print(f"{path} is stale -- rerun tools/build_eval_tiers.py", file=sys.stderr)
                return 1
        print(f"tiers up to date (easy={len(easy)}, hard={len(hard)})")
        return 0

    TIERS_DIR.mkdir(parents=True, exist_ok=True)
    for name, rows in tiers.items():
        (TIERS_DIR / f"{name}.jsonl").write_text(render(rows), encoding="utf-8")
        print(f"wrote {name}.jsonl ({len(rows)} prompts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
