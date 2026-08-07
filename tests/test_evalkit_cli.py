"""The evalkit command line. Every test injects a pipeline, so nothing calls a
provider — the real one bills per prompt."""

import json
from pathlib import Path

from cadless.config import settings
from cadless.evalkit import load_tier
from cadless.evalkit.__main__ import main
from cadless.pipeline import Attempt, GenerationResult


class StubPipeline:
    """Stands in for Pipeline: same call shape, no model, no OCCT."""

    def __init__(self, *, ok: bool = True, attempts: int = 1, volume: float | None = 1000.0):
        self.ok = ok
        self.attempts = attempts
        self.volume = volume
        self.seen: list[str] = []

    def run(self, intent, export_dir=None, on_progress=None, prior_code=None, grounding=None):
        self.seen.append(intent)
        # attempt_count is a read-only property over `attempts`, so the list has
        # to be populated for the report's avg_attempts to mean anything.
        return GenerationResult(
            ok=self.ok,
            intent=intent,
            volume=self.volume if self.ok else None,
            attempts=[
                Attempt(n=i + 1, code="result = None", stage="execute", error=None)
                for i in range(self.attempts)
            ],
        )


def _hard_size() -> int:
    # Derived, not hard-coded: adding a catalog item should fail the one test
    # that owns the counts (test_evalkit_tiers) with a message that says to
    # regenerate, not five unrelated CLI assertions saying `assert 18 == 19`.
    return len(load_tier("hard"))


def test_running_a_tier_reports_every_prompt(capsys):
    stub = StubPipeline()

    assert main(["--tier", "hard"], pipeline=stub) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["total"] == _hard_size()
    assert report["success_rate"] == 1.0
    assert len(stub.seen) == _hard_size()


def test_a_failing_pipeline_is_reported_rather_than_raised(capsys):
    assert main(["--tier", "hard"], pipeline=StubPipeline(ok=False)) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["success_rate"] == 0.0


def test_csv_format_is_available(capsys):
    assert main(["--tier", "hard", "--format", "csv"], pipeline=StubPipeline()) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "id,ok,attempts,repaired,volume,error"
    assert len(lines) == _hard_size() + 1  # header + one row per prompt


def test_output_ends_in_exactly_one_newline(tmp_path):
    # to_csv already ends in a newline and to_json does not; normalising means
    # neither format grows a blank last line or loses its final one.
    for fmt in ("json", "csv"):
        target = tmp_path / f"r.{fmt}"
        assert (
            main(["--tier", "hard", "--format", fmt, "--out", str(target)], pipeline=StubPipeline())
            == 0
        )
        body = target.read_text()
        assert body.endswith("\n")
        assert not body.endswith("\n\n")


def test_out_writes_the_report(tmp_path, capsys):
    target = tmp_path / "baseline.json"

    assert main(["--tier", "hard", "--out", str(target)], pipeline=StubPipeline()) == 0

    assert json.loads(target.read_text())["total"] == _hard_size()
    assert "baseline.json" in capsys.readouterr().out


def test_writing_into_the_catalog_is_refused_before_anything_is_generated(
    tmp_path, monkeypatch, capsys
):
    # docker-compose mounts the catalog read-only, so a run that works on the
    # host would fail in the container. The ordering is the point: refusing
    # after the run would already have spent the money.
    monkeypatch.setattr(settings, "catalog_root", tmp_path)
    stub = StubPipeline()

    rc = main(["--tier", "hard", "--out", str(tmp_path / "sub" / "r.json")], pipeline=stub)

    assert rc == 2
    assert stub.seen == []  # nothing was generated, so nothing was billed
    assert "read-only" in capsys.readouterr().err
    assert not (tmp_path / "sub").exists()


def test_an_export_dir_in_the_catalog_is_refused_too(tmp_path, monkeypatch, capsys):
    # This one matters more than --out, not less: the exporters mkdir and write
    # per prompt *during* the run, so a bad value fails partway through after
    # money has been spent.
    monkeypatch.setattr(settings, "catalog_root", tmp_path)
    stub = StubPipeline()

    rc = main(["--tier", "hard", "--export-dir", str(tmp_path / "exports")], pipeline=stub)

    assert rc == 2
    assert stub.seen == []
    assert "--export-dir" in capsys.readouterr().err


def test_the_guard_holds_when_catalog_root_is_relative(tmp_path, monkeypatch, capsys):
    # settings.catalog_root defaults to a relative "./catalog", so resolving it
    # against the cwd alone makes the guard evaporate the moment you run from
    # somewhere else. Reproduces that: cwd is elsewhere, the target is the real
    # repo catalog, and it must still be refused.
    monkeypatch.setattr(settings, "catalog_root", Path("catalog"))
    monkeypatch.chdir(tmp_path)
    repo_catalog = Path(__file__).resolve().parents[1] / "catalog" / "report.json"
    stub = StubPipeline()

    rc = main(["--tier", "hard", "--out", str(repo_catalog)], pipeline=stub)

    assert rc == 2
    assert stub.seen == []
    assert "read-only" in capsys.readouterr().err


def test_an_unknown_tier_lists_the_real_ones_without_a_traceback(capsys):
    rc = main(["--tier", "medium"], pipeline=StubPipeline())

    assert rc == 2
    err = capsys.readouterr().err
    assert "medium" in err
    assert "easy" in err and "hard" in err


def test_no_pipeline_is_constructed_when_one_is_injected(monkeypatch):
    # Guards the money path: constructing the real Pipeline is what eventually
    # reaches a provider, so the injected one must short-circuit it entirely.
    def explode(*args, **kwargs):
        raise AssertionError("the real Pipeline must not be constructed")

    monkeypatch.setattr("cadless.evalkit.pipeline_eval.Pipeline", explode)

    assert main(["--tier", "easy"], pipeline=StubPipeline()) == 0


def test_a_falsy_pipeline_double_is_still_used(monkeypatch, capsys):
    # `pipeline or Pipeline()` would treat a double defining __len__ as absent
    # and construct the billing path instead. `is None` is what keeps it honest.
    class EmptyStub(StubPipeline):
        def __len__(self):
            return 0

    def explode(*args, **kwargs):
        raise AssertionError("the real Pipeline must not be constructed")

    monkeypatch.setattr("cadless.evalkit.pipeline_eval.Pipeline", explode)
    stub = EmptyStub()
    assert not stub  # falsy, and still the pipeline that must be used

    assert main(["--tier", "hard"], pipeline=stub) == 0
    assert len(stub.seen) == _hard_size()
    capsys.readouterr()


# Note: no test omits `pipeline`. That path constructs the real Pipeline and
# bills per prompt, so it is left to a deliberate manual run.
