"""VLM critique tests. No Bedrock; fake VLM client + fake renderer."""

import pytest

from cadless.config import Settings
from cadless.pipeline import Pipeline
from cadless.vlm_critique import Critique, VlmCritic, parse_verdict

GOOD = "from build123d import *\nresult = Box(10, 10, 10)\n"


def test_parse_verdict_match():
    assert parse_verdict("MATCH").matches
    assert parse_verdict("match, looks good").matches


def test_parse_verdict_mismatch_extracts_feedback():
    c = parse_verdict("MISMATCH: the hole is missing")
    assert not c.matches
    assert c.feedback == "the hole is missing"


class _FakeVlmClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def converse(self, **kwargs):
        self.calls += 1
        # ensure an image block was sent
        content = kwargs["messages"][0]["content"]
        assert any("image" in part for part in content)
        return {"output": {"message": {"content": [{"text": self.reply}]}}}


def test_vlmcritic_renders_and_parses():
    critic = VlmCritic(renderer=lambda _p: b"PNGDATA", client=_FakeVlmClient("MATCH"))
    verdict = critic.critique("a cube", "/tmp/x.glb")
    assert verdict.matches


class AlwaysGood:
    def generate(self, intent, grounding=None, temperature=None, on_token=None):
        return GOOD

    def repair(self, intent, code, error, context=None):
        self.last_error = error
        return GOOD


class _ScriptedCritic:
    """Returns MISMATCH once, then MATCH — simulates a fixed-on-second-look part."""

    def __init__(self):
        self.verdicts = iter([Critique(False, "wrong size"), Critique(True, "")])

    def critique(self, intent, glb_path):
        return next(self.verdicts)


@pytest.mark.build123d
def test_pipeline_off_by_default_skips_critique(tmp_path):
    gen = AlwaysGood()
    # critic present but flag default-off -> not consulted
    pipe = Pipeline(generator=gen, critic=_ScriptedCritic())
    result = pipe.run("a cube", export_dir=str(tmp_path))
    assert result.ok
    assert result.attempt_count == 1
    assert all(a.stage != "critique" for a in result.attempts)


@pytest.mark.build123d
def test_pipeline_critique_triggers_repair_when_enabled(tmp_path):
    gen = AlwaysGood()
    cfg = Settings(vlm_critique_enabled=True, repair_max_attempts=3)
    pipe = Pipeline(generator=gen, config=cfg, critic=_ScriptedCritic())
    result = pipe.run("a cube", export_dir=str(tmp_path))
    assert result.ok
    # first attempt executes-but-mismatches -> critique attempt, then success
    assert any(a.stage == "critique" for a in result.attempts)
    assert "wrong size" in gen.last_error
