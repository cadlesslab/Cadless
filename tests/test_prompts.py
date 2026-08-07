"""Prompt assembly + code extraction tests. No Bedrock."""

from cadless.prompts import (
    CodeGenerator,
    build_refinement_message,
    build_repair_message,
    build_user_message,
    extract_code,
    extract_code_and_params,
    refine_diff_ratio,
)


def test_extract_fenced_python():
    text = "Here you go:\n```python\nfrom build123d import *\nresult = Box(1,1,1)\n```\nDone"
    code = extract_code(text)
    assert code.startswith("from build123d import *")
    assert "Box(1,1,1)" in code
    assert "Here you go" not in code


def test_extract_plain_unfenced():
    text = "Response: from build123d import *\nresult = Box(2,2,2)"
    code = extract_code(text)
    assert code.startswith("from build123d import *")
    assert "Response:" not in code


def test_extract_first_block_only():
    text = "```python\nresult = 1\n```\nand\n```python\nresult = 2\n```"
    assert "result = 1" in extract_code(text)
    assert "result = 2" not in extract_code(text)


def test_extract_code_and_params_pulls_both():
    text = (
        "```python\nfrom build123d import *\n\n"
        'params = {"length": 40, "width": 20}\n'
        'result = Box(params["length"], params["width"], 5)\n```'
    )
    code, params = extract_code_and_params(text)
    assert "Box(params" in code
    assert params == {"length": 40, "width": 20}


def test_extract_code_and_params_empty_when_no_block():
    code, params = extract_code_and_params("```python\nresult = Box(1, 1, 1)\n```")
    assert "Box(1, 1, 1)" in code
    assert params == {}


def test_build_user_message_has_fewshot_and_intent():
    msg = build_user_message("A 5 mm cube")
    assert "Request: A 5 mm cube" in msg
    assert "build123d" in msg  # few-shot present


def test_build_repair_message_includes_error_and_prev_code():
    msg = build_repair_message("a cube", "result = Box(", "SyntaxError: unexpected EOF")
    assert "SyntaxError" in msg
    assert "result = Box(" in msg
    assert "result" in msg


def test_build_repair_message_with_structured_context_is_line_anchored():
    from cadless.worker import RepairContext

    ctx = RepairContext(
        error_type="StdFail_NotDone",
        message="BRep_API: command not done",
        offending_line="result = Cylinder(5, 10) - Box(20, 20, 20)",
        last_traceback=(
            "Traceback (most recent call last):\n"
            '  File "<generated>", line 3, in <module>\n'
            "    result = Cylinder(5, 10) - Box(20, 20, 20)\n"
            "StdFail_NotDone: BRep_API: command not done"
        ),
    )
    msg = build_repair_message("a part", "from build123d import *\n", "ignored", context=ctx)
    # The structured fields make the prompt line-anchored.
    assert "StdFail_NotDone" in msg
    assert "BRep_API: command not done" in msg
    assert "result = Cylinder(5, 10) - Box(20, 20, 20)" in msg
    assert "Traceback (most recent call last)" in msg


class _FakeProvider:
    """Records the last ``complete()`` call and replays a canned reply.

    Mirrors the single-shot half of the :class:`ChatProvider` protocol so
    ``CodeGenerator`` can be unit-tested without Bedrock.
    """

    def __init__(self, reply):
        self.reply = reply
        self.last = None
        self.calls = 0

    def complete(self, *, model, system, user, temperature=None):
        self.calls += 1
        self.last = (system, user, model)
        self.last_temperature = temperature
        return self.reply


def test_generator_generate_extracts_code():
    fake = _FakeProvider("```python\nfrom build123d import *\nresult = Box(3,3,3)\n```")
    gen = CodeGenerator(provider=fake)
    code = gen.generate("a 3mm cube")
    assert "Box(3,3,3)" in code
    assert "Request: a 3mm cube" in fake.last[1]


def test_generator_routes_through_provider_complete():
    """CodeGenerator must call the injected provider's complete() (the seam)."""
    fake = _FakeProvider("```python\nresult = Box(1,1,1)\n```")
    gen = CodeGenerator(provider=fake, model="sonnet-4-6")
    gen.generate("a cube")
    assert fake.calls == 1
    system, _user, model = fake.last
    assert system  # SYSTEM_PROMPT forwarded
    assert model == "sonnet-4-6"


def test_generator_streams_tokens_via_on_token():
    """With on_token, generate() streams each text delta and assembles the code."""
    from cadless.llm.providers import StreamChunk
    from cadless.llm.providers.fake import FakeChatProvider
    from cadless.llm.types import StreamEvent

    parts = ["```python\n", "from build123d import *\n", "result = Box(5, 5, 5)\n", "```"]
    provider = FakeChatProvider(
        script=[StreamChunk(StreamEvent.TEXT_DELTA, {"text": p}) for p in parts]
    )
    gen = CodeGenerator(provider=provider)
    seen: list[str] = []
    code = gen.generate("a 5mm cube", on_token=seen.append)
    assert seen == parts  # every delta surfaced live, in order (>=2 tokens)
    assert "Box(5, 5, 5)" in code  # assembled from the stream + extracted


def test_generator_without_on_token_does_not_stream():
    """The default (no on_token) path stays one-shot complete() — no streaming."""
    fake = _FakeProvider("```python\nresult = Box(1,1,1)\n```")
    gen = CodeGenerator(provider=fake)
    gen.generate("a cube")  # _FakeProvider has no stream_turn; must use complete()
    assert fake.calls == 1


def test_generator_repair_uses_repair_message():
    fake = _FakeProvider("result = Box(4,4,4)\nfrom build123d import *")
    gen = CodeGenerator(provider=fake)
    gen.repair("a cube", "result = Box(", "boom")
    assert "boom" in fake.last[1]


def test_build_refinement_message_includes_prior_code_and_delta():
    msg = build_refinement_message("make the hole 8 mm", "params = {'hole': 6}\nresult = ...")
    assert "make the hole 8 mm" in msg
    assert "params = {'hole': 6}" in msg
    assert "result" in msg


def test_generator_refine_uses_refinement_message():
    fake = _FakeProvider("```python\nfrom build123d import *\nresult = Box(8,8,8)\n```")
    gen = CodeGenerator(provider=fake)
    code = gen.refine("make it 8mm", "from build123d import *\nresult = Box(5,5,5)")
    assert "Box(8,8,8)" in code
    # the refinement message carried both the delta and the prior code
    assert "make it 8mm" in fake.last[1]
    assert "Box(5,5,5)" in fake.last[1]


# ---: refine must be surgical / geometry-preserving ----------------


def test_refinement_message_instructs_surgical_geometry_preserving_edit():
    """The refine prompt must explicitly demand a minimal, preserving edit.

    Regression for: edit_model rewrote a 9244-char house down to 802
    chars on a small "align the floors" tweak. The prompt must tell the model to
    change only what is necessary and keep all other geometry/params identical,
    and must forbid a from-scratch rewrite / simplification.
    """
    msg = build_refinement_message("align the first and second floor", "result = ...")
    low = msg.lower()
    # Must instruct preserving everything else unchanged.
    assert "only" in low and ("necessary" in low or "required" in low)
    assert "identical" in low or "unchanged" in low or "preserve" in low
    # Must forbid wholesale rewrite / over-simplification.
    assert "rewrite" in low or "from scratch" in low or "simplif" in low
    # Must still return the full script (not a fragment).
    assert "full" in low


def test_refine_diff_ratio_is_zero_for_identical_code():
    code = "from build123d import *\nresult = Box(5, 5, 5)\n"
    assert refine_diff_ratio(code, code) == 0.0


def test_refine_diff_ratio_small_for_a_one_line_tweak():
    """A pure dimensional tweak changes one line -> a small ratio."""
    before = (
        "from build123d import *\n"
        'params = {"w": 5, "h": 5, "d": 5}\n'
        'result = Box(params["w"], params["h"], params["d"])\n'
    )
    after = (
        "from build123d import *\n"
        'params = {"w": 8, "h": 5, "d": 5}\n'  # only this line changed
        'result = Box(params["w"], params["h"], params["d"])\n'
    )
    assert refine_diff_ratio(before, after) < 0.5


def test_refine_diff_ratio_large_for_a_from_scratch_rewrite():
    """The 9244->802 over-simplification the bug describes must score high.

    This is the metric the edit-similarity guardrail/eval keys on: a small
    request that yields a wholesale rewrite produces a near-1.0 ratio, while a
    surgical edit stays small (the assertion in the eval test below).
    """
    big = (
        "from build123d import *\n"
        + "\n".join(f"part_{i} = Box({i + 1}, {i + 1}, {i + 1})" for i in range(80))
        + "\nresult = part_0\n"
    )
    tiny = "from build123d import *\nresult = Box(1, 1, 1)\n"
    ratio = refine_diff_ratio(big, tiny)
    assert ratio > 0.8


def test_surgical_refine_keeps_edit_similarity_bounded():
    """Edit-similarity eval: a small request must yield a bounded diff.

    Mirrors the eval the work item asks for — assert that a surgical refine
    (model preserves the structure, tweaks one line) keeps the code delta small.
    Drives the regression: before the fix the prompt did not demand preservation,
    so a rewrite (ratio ~1.0) would breach this bound.
    """
    prior = (
        "from build123d import *\n"
        'params = {"floor1_z": 0, "floor2_z": 30, "wall": 10}\n'
        "floor1 = Pos(0, 0, params['floor1_z']) * Box(100, 100, 5)\n"
        "floor2 = Pos(0, 0, params['floor2_z']) * Box(100, 100, 5)\n"
        "result = floor1 + floor2\n"
    )
    # A surgical edit: only the alignment line's offset changes.
    surgical = prior.replace('"floor2_z": 30', '"floor2_z": 5')
    fake = _FakeProvider(f"```python\n{surgical}```")
    gen = CodeGenerator(provider=fake)
    refined = gen.refine("align the first and second floor", prior)
    assert refine_diff_ratio(prior, refined) < 0.3
