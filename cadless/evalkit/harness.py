"""Eval harness: run a benchmark prompt set through a generator + executor and
report compile / success rates.

Metrics:
  * **compile_rate** — fraction of prompts whose generated code parses + compiles.
  * **success_rate** — fraction whose code also executed and produced a `result`.

The default executor (`default_run`) is a minimal in-process runner used for the
skeleton and for unit tests. The production sandboxed worker
(``cadless.worker``) and the full pipeline plug in via
the same ``run_fn`` interface.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from cadless.api_subset import RESULT_VARIABLE
from cadless.config import settings

# The original benchmark set lives with the rest of the catalog content under
# catalog_root (outside the repo). It stays that way: it is large, it changes
# with the content, and it is not ours to version.
_BENCHMARK_PATH = settings.catalog_root / "prompts.jsonl"

# Tiers are the opposite trade: small, curated prompt sets that ARE versioned
# here, because engine-quality work quotes numbers against them. A benchmark
# that can change without a diff cannot anchor a regression claim.
#
# Anchored on this file rather than on settings.catalog_root, which defaults to
# a relative "./catalog" and so moves with the working directory. And named
# "tiers" rather than "benchmarks" on purpose — .gitignore ignores
# `benchmarks/` at any depth, which would silently untrack these.
TIERS_DIR = Path(__file__).resolve().parent / "tiers"


@dataclass(frozen=True)
class BenchmarkPrompt:
    id: str
    prompt: str


@dataclass
class RunResult:
    """Outcome of executing one generated script."""

    ok: bool
    error: str | None = None


@dataclass
class EvalRecord:
    id: str
    prompt: str
    code: str
    compiled: bool
    executed: bool
    error: str | None = None


@dataclass
class EvalReport:
    records: list[EvalRecord]

    @property
    def total(self) -> int:
        return len(self.records)

    @property
    def compile_rate(self) -> float:
        return _rate(r.compiled for r in self.records) if self.records else 0.0

    @property
    def success_rate(self) -> float:
        return _rate(r.executed for r in self.records) if self.records else 0.0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "compile_rate": round(self.compile_rate, 4),
            "success_rate": round(self.success_rate, 4),
            "records": [asdict(r) for r in self.records],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "compiled", "executed", "error"])
        for r in self.records:
            writer.writerow([r.id, r.compiled, r.executed, (r.error or "").replace("\n", " ")])
        return buf.getvalue()


def _rate(flags) -> float:
    flags = list(flags)
    return sum(1 for f in flags if f) / len(flags)


def load_benchmark(path: Path | None = None) -> list[BenchmarkPrompt]:
    """Load benchmark prompts from a JSONL file (defaults to ``catalog_root/prompts.jsonl``)."""
    path = path or _BENCHMARK_PATH
    prompts = []
    # Explicit encoding: without it the decode follows the process locale, so the
    # same file would load on one machine and raise UnicodeDecodeError under a C
    # or cp1252 locale. Prompt text is prose and routinely non-ASCII.
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        # These files are hand-edited and generated alike, so a bad line should
        # say which line of which file rather than surfacing a bare parse error.
        try:
            obj = json.loads(line)
            prompts.append(BenchmarkPrompt(id=obj["id"], prompt=obj["prompt"]))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(f"{path}:{lineno}: malformed benchmark line: {exc}") from exc
    return prompts


def available_tiers() -> list[str]:
    """Names of the benchmark tiers shipped with the package."""
    if not TIERS_DIR.is_dir():
        return []
    # is_file() so this agrees with load_tier: without it a directory named
    # "x.jsonl" would be listed as available and then rejected as unknown.
    return sorted(p.stem for p in TIERS_DIR.glob("*.jsonl") if p.is_file())


def load_tier(name: str) -> list[BenchmarkPrompt]:
    """Load a versioned benchmark tier by name (e.g. ``"easy"``, ``"hard"``).

    Unlike ``load_benchmark``, this never consults ``catalog_root`` — a tier is
    repo content, so it resolves the same way no matter where the process was
    started from.
    """
    path = TIERS_DIR / f"{name}.jsonl"
    if not path.is_file():
        raise ValueError(f"unknown tier {name!r}; available: {available_tiers()}")
    return load_benchmark(path)


def default_run(code: str) -> RunResult:
    """Minimal in-process executor: compile, exec, and check `result` exists.

    Used for the harness skeleton + tests. NOT a sandbox — the production worker
     replaces this with a resource-limited isolated runner.
    """
    try:
        compiled = compile(code, "<eval>", "exec")
    except SyntaxError as exc:  # pragma: no cover - exercised via tests
        return RunResult(ok=False, error=f"compile: {exc}")
    ns: dict = {}
    try:
        exec(compiled, ns)  # noqa: S102 - trusted in tests; sandboxed in prod
    except Exception as exc:
        return RunResult(ok=False, error=f"runtime: {type(exc).__name__}: {exc}")
    if RESULT_VARIABLE not in ns:
        return RunResult(ok=False, error=f"missing `{RESULT_VARIABLE}` variable")
    return RunResult(ok=True)


def _compiles(code: str) -> bool:
    try:
        compile(code, "<eval>", "exec")
        return True
    except SyntaxError:
        return False


def run_eval(
    prompts: list[BenchmarkPrompt],
    generate_fn: Callable[[str], str],
    run_fn: Callable[[str], RunResult] = default_run,
) -> EvalReport:
    """Generate code for each prompt, execute it, and collect a report."""
    records: list[EvalRecord] = []
    for bp in prompts:
        try:
            code = generate_fn(bp.prompt)
        except Exception as exc:
            records.append(
                EvalRecord(
                    bp.id,
                    bp.prompt,
                    "",
                    compiled=False,
                    executed=False,
                    error=f"generate: {type(exc).__name__}: {exc}",
                )
            )
            continue
        compiled = _compiles(code)
        if not compiled:
            records.append(
                EvalRecord(
                    bp.id, bp.prompt, code, compiled=False, executed=False, error="did not compile"
                )
            )
            continue
        result = run_fn(code)
        records.append(
            EvalRecord(
                bp.id, bp.prompt, code, compiled=True, executed=result.ok, error=result.error
            )
        )
    return EvalReport(records=records)
