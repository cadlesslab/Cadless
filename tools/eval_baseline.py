#!/usr/bin/env python3
"""Summarise repeated eval runs: metric spread, and whether the shape was right.

Two things a single ``python -m cadless.evalkit`` report cannot tell you:

* **Spread.** Generation is non-deterministic, so one run is a sample rather
  than a behaviour. Quoting a number without the spread invites a later phase
  to read noise as a result.
* **Whether the solid is the one that was asked for.** ``success_rate`` means
  "built a valid solid", not "built the right solid" -- nothing in the report
  compares against the catalog's own recorded geometry. On the hard tier that
  distinction is most of the story: runs score ~0.93 on success while most of
  those successes are the wrong size.

Usage -- point it at a directory of reports named ``<tier>-pass<N>.json``:

    python tools/eval_baseline.py --runs ./runs --tier hard

Produce those reports with, per pass:

    CADLESS_LLM_PROVIDER=bedrock python -m cadless.evalkit \\
        --tier hard --out runs/hard-pass1.json
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = REPO_ROOT / "catalog"
METRICS = ("success_rate", "first_try_rate", "repair_lift", "avg_attempts", "degenerate_count")
TOLERANCE = 0.05


def truth_volume(prompt_id: str, catalog_root: Path) -> float | None:
    """The catalog's own volume for the step a prompt asks for.

    An easy-tier id ends in ``#1`` and asks for step 1 alone; a hard-tier id
    names the item and asks for the finished part, which is the last step.
    """
    catalog, item = prompt_id.split("#")[0].split("/", 1)
    manifest = catalog_root / catalog / item / "manifest.json"
    if not manifest.is_file():
        return None
    steps = json.loads(manifest.read_text(encoding="utf-8")).get("steps") or []
    if not steps:
        return None
    step = steps[0] if "#1" in prompt_id else steps[-1]
    return (step.get("geometry") or {}).get("volume")


def summarise(tier: str, runs_dir: Path, catalog_root: Path) -> None:
    reports = sorted(runs_dir.glob(f"{tier}-pass*.json"))
    if not reports:
        print(f"{tier}: no reports in {runs_dir}")
        return
    data = [json.loads(p.read_text(encoding="utf-8")) for p in reports]
    n = len(data)

    print(f"\n{tier} tier -- {data[0]['total']} prompts, n={n} passes")
    print(f"{'metric':<18}{'mean':>9}{'sd':>9}{'min':>9}{'max':>9}{'SEM':>9}")
    for metric in METRICS:
        values = [d[metric] for d in data]
        sd = statistics.stdev(values) if n > 1 else 0.0
        sem = sd / n**0.5 if n > 1 else 0.0
        print(
            f"{metric:<18}{statistics.mean(values):>9.4f}{sd:>9.4f}"
            f"{min(values):>9.3f}{max(values):>9.3f}{sem:>9.4f}"
        )

    within, wrong, unbuilt = [], [], []
    never_built: collections.Counter = collections.Counter()
    always_wrong: collections.Counter = collections.Counter()
    for report in data:
        w = x = u = 0
        for rec in report["records"]:
            if not rec["ok"]:
                u += 1
                never_built[rec["id"]] += 1
                continue
            truth, got = truth_volume(rec["id"], catalog_root), rec.get("volume")
            if not truth or not got:
                continue
            if abs(got / truth - 1) <= TOLERANCE:
                w += 1
            else:
                x += 1
                always_wrong[rec["id"]] += 1
        within.append(w)
        wrong.append(x)
        unbuilt.append(u)

    print(f"\n  volume vs the catalog's own geometry (+/-{TOLERANCE:.0%}), per pass:")
    print(f"    within tolerance : {within}  mean {statistics.mean(within):.1f}")
    print(f"    wrong size       : {wrong}  mean {statistics.mean(wrong):.1f}")
    print(f"    did not build    : {unbuilt}  mean {statistics.mean(unbuilt):.1f}")
    if never_built:
        print("    failed to build in some passes:")
        for pid, count in never_built.most_common():
            print(f"      {pid:<44} {count}/{n}")
    stable = [pid for pid, c in always_wrong.items() if c == n]
    if stable:
        print(f"    wrong size in all {n} passes -- a stable target, unlike the rates above:")
        for pid in sorted(stable):
            print(f"      {pid}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", required=True, help="directory of <tier>-pass<N>.json reports")
    parser.add_argument("--tier", action="append", help="tier to summarise (repeatable)")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    args = parser.parse_args(argv)

    for tier in args.tier or ["easy", "hard"]:
        summarise(tier, Path(args.runs), Path(args.catalog))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
