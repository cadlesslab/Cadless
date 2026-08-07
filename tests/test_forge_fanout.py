"""Best-of-N parallel candidate fan-out tests (C1).

The fan-out primitive runs N *fresh* generations in parallel over the provider
seam at a higher (forge) temperature, giving each candidate its own export dir,
and returns the list of N GenerationResults. Failures are isolated per candidate.

These tests inject a scripted generator (no LLM, no OCCT) by stubbing the
Pipeline's single-run seam so concurrency, distinct export dirs, temperature
threading, candidate count, and failure isolation are asserted deterministically.
"""

from __future__ import annotations

import threading
import time

from cadless.config import Settings
from cadless.pipeline import GenerationResult, Pipeline


class _RecordingGen:
    """Generator stub recording the temperature it was generated with."""

    def __init__(self):
        self.temperatures: list[float | None] = []
        self._lock = threading.Lock()

    def generate(self, intent, grounding=None, temperature=None):
        with self._lock:
            self.temperatures.append(temperature)
        # Return banned code so the run fails fast at validation (no OCCT needed);
        # the candidate is still a well-formed (failed) GenerationResult.
        return "import os\nfrom build123d import *\nresult = Box(1,1,1)\n"

    def repair(self, intent, code, error, context=None):
        return code


def test_run_candidates_returns_n_results():
    gen = _RecordingGen()
    pipe = Pipeline(generator=gen, config=Settings(repair_max_attempts=1))
    results = pipe.run_candidates("a bracket", n=4)
    assert len(results) == 4
    assert all(isinstance(r, GenerationResult) for r in results)


def test_run_candidates_uses_forge_temperature():
    gen = _RecordingGen()
    cfg = Settings(repair_max_attempts=1, forge_temperature=0.9)
    pipe = Pipeline(generator=gen, config=cfg)
    pipe.run_candidates("a bracket", n=3)
    assert gen.temperatures == [0.9, 0.9, 0.9]


def test_run_candidates_distinct_export_dirs(tmp_path):
    seen: list[str | None] = []
    lock = threading.Lock()

    class _DirGen(_RecordingGen):
        pass

    pipe = Pipeline(generator=_DirGen(), config=Settings(repair_max_attempts=1))

    orig_run = pipe.run

    def _spy_run(intent, export_dir=None, **kwargs):
        with lock:
            seen.append(export_dir)
        return orig_run(intent, export_dir=export_dir, **kwargs)

    pipe.run = _spy_run  # type: ignore[method-assign]
    pipe.run_candidates("a bracket", n=3, export_dir=str(tmp_path))
    assert len(seen) == 3
    assert len(set(seen)) == 3  # all distinct
    assert all(d is not None and str(tmp_path) in d for d in seen)


def test_run_candidates_runs_concurrently():
    """All N generations overlap: a barrier in generate() only releases once all
    N threads have arrived, which can only happen if they run in parallel."""
    n = 4
    barrier = threading.Barrier(n, timeout=5)

    class _BarrierGen(_RecordingGen):
        def generate(self, intent, grounding=None, temperature=None):
            barrier.wait()  # blocks until all N candidates are in-flight
            return super().generate(intent, grounding, temperature)

    pipe = Pipeline(generator=_BarrierGen(), config=Settings(repair_max_attempts=1))
    start = time.monotonic()
    results = pipe.run_candidates("a bracket", n=n)
    elapsed = time.monotonic() - start
    assert len(results) == n
    assert elapsed < 5  # barrier did not time out => true concurrency


def test_run_candidates_failure_isolation():
    """One candidate raising does not sink the rest; it returns a failed result."""

    class _FlakyGen(_RecordingGen):
        def __init__(self):
            super().__init__()
            self._calls = 0

        def generate(self, intent, grounding=None, temperature=None):
            with self._lock:
                self._calls += 1
                mine = self._calls
            if mine == 2:
                raise RuntimeError("boom in candidate 2")
            return super().generate(intent, grounding, temperature)

    pipe = Pipeline(generator=_FlakyGen(), config=Settings(repair_max_attempts=1))
    results = pipe.run_candidates("a bracket", n=3)
    assert len(results) == 3  # all 3 candidates represented
    failed = [r for r in results if not r.ok and r.error and "boom" in r.error]
    assert len(failed) == 1  # the raising candidate became a failed result


def test_run_candidates_n_le_1_is_single_normal_run():
    """n<=1 short-circuits to a single normal run with default (None) temperature
    and no extra export-dir mangling — behavior identical to Pipeline.run."""
    gen = _RecordingGen()
    pipe = Pipeline(generator=gen, config=Settings(repair_max_attempts=1))
    results = pipe.run_candidates("a bracket", n=1)
    assert len(results) == 1
    # Single-run path must not apply the forge temperature.
    assert gen.temperatures == [None]


def test_run_default_path_temperature_unchanged():
    """Pipeline.run (no temperature arg) still calls generate with temperature
    defaulting to None — the legacy single-run path is unchanged."""
    gen = _RecordingGen()
    pipe = Pipeline(generator=gen, config=Settings(repair_max_attempts=1))
    pipe.run("a bracket")
    assert gen.temperatures == [None]
