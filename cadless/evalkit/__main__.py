"""Run a benchmark tier through the pipeline and print the report.

    python -m cadless.evalkit --tier hard
    python -m cadless.evalkit --tier easy --out runs/easy.json

**This spends money.** Every prompt drives a real generation, and the repair loop
can take up to ``repair_max_attempts`` model calls per prompt, so a tier costs
roughly (prompts x attempts) requests against whichever provider
``CADLESS_LLM_PROVIDER`` selects. Nothing here is part of ``make test``.

``--forge-n N`` **multiplies that already-paid cost by N**, because a race generates
and executes N candidates per prompt and keeps one. It also adds one cheap-model call
per surviving candidate for the judge's tie-break. Size the spend before the run, not
after it::

    python -m cadless.evalkit --tier hard --forge-n 3 --out runs/hard-forge.json

Generation is not deterministic, so a single run is one sample rather than a
measurement. Repeat a tier and look at the spread before quoting a number.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cadless.config import settings
from cadless.evalkit.harness import available_tiers, load_tier
from cadless.evalkit.pipeline_eval import run_pipeline_eval


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cadless.evalkit",
        description="Run a benchmark tier through the full pipeline.",
    )
    parser.add_argument("--tier", required=True, help="tier name, e.g. easy or hard")
    parser.add_argument("--out", help="write the report here instead of stdout")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument(
        "--export-dir",
        help="where the pipeline writes STEP/STL/GLB artifacts (off by default)",
    )
    parser.add_argument(
        "--forge-n",
        type=int,
        default=1,
        metavar="N",
        help=(
            "race N candidates per prompt and judge them (default 1 = off, the "
            "single-run path). N multiplies the tier's cost by N."
        ),
    )
    return parser


def _catalog_roots() -> list[Path]:
    """Every place ``catalog_root`` plausibly points at.

    ``settings.catalog_root`` defaults to a relative ``./catalog``, so resolving
    it alone would answer differently depending on where python was started —
    the same working-directory dependence ``TIERS_DIR`` is anchored to avoid. A
    guard that silently stops guarding when you cd elsewhere is worse than none,
    so a relative value is checked against the repo root as well.
    """
    configured = Path(settings.catalog_root)
    roots = [configured.resolve()]
    if not configured.is_absolute():
        repo_root = Path(__file__).resolve().parents[2]
        roots.append((repo_root / configured).resolve())
    return roots


def _reject_if_under_catalog_root(target: Path, what: str) -> str | None:
    """The catalog is mounted read-only in the container (docker-compose.yml:76).

    Something written there succeeds on a developer's machine and fails in the
    container, which is the worst place to find out.
    """
    resolved = target.resolve()
    for root in _catalog_roots():
        if resolved.is_relative_to(root):
            return (
                f"refusing to use {resolved} for {what}: it is under the catalog root "
                f"({root}), which is mounted read-only in the container. "
                f"Choose a path outside it."
            )
    return None


def main(argv: list[str] | None = None, pipeline=None, provider=None) -> int:
    """Run one tier and emit the report; return a process exit code.

    ``pipeline`` and ``provider`` exist so tests can drive the whole path without a
    live call — every run is billed, so nothing here may reach a real provider by
    default in a test.
    """
    args = _build_parser().parse_args(argv)

    # Validate before running: a tier costs real requests, so a typo in --out
    # should not be discovered after paying for the whole run.
    try:
        prompts = load_tier(args.tier)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # 0 or a negative N would silently mean "single run" rather than what was asked
    # for, and the report would look like a race that decided nothing.
    if args.forge_n < 1:
        print(f"--forge-n must be at least 1, got {args.forge_n}", file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else None
    # --export-dir matters more than --out here, not less: the exporters mkdir and
    # write per prompt *during* the paid run, so a bad value there fails partway
    # through after money has been spent, while --out fails on one cheap write.
    for target, what in (
        (out, "--out"),
        (Path(args.export_dir) if args.export_dir else None, "--export-dir"),
    ):
        if target is None:
            continue
        refusal = _reject_if_under_catalog_root(target, what)
        if refusal:
            print(refusal, file=sys.stderr)
            return 2

    # The judge's cheap-LLM rung needs a provider, and only a race has a judge.
    # Built here rather than inside the loop so a misconfigured provider fails
    # before the first paid prompt, and never built at all on the single-run path.
    if args.forge_n > 1 and provider is None:
        from cadless.llm.registry import build_provider

        provider = build_provider()

    report = run_pipeline_eval(
        prompts=prompts,
        pipeline=pipeline,
        export_dir=args.export_dir,
        forge_n=args.forge_n,
        provider=provider,
    )
    text = report.to_json() if args.format == "json" else report.to_csv()
    # to_csv already ends in a newline and to_json does not, so normalise to
    # exactly one rather than emitting a blank last line for one of the two.
    text = text if text.endswith("\n") else text + "\n"

    if out is None:
        sys.stdout.write(text)
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({report.total} prompts, tiers available: {available_tiers()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
