"""Reference images attached to a chat turn, and the render critique's captures."""

from __future__ import annotations

import asyncio
import base64

import pytest
from fastapi.testclient import TestClient

import backend.routers.chat as chat
from backend.app import create_app
from cadless.llm.types import ContentBlock
from cadless.store import Store
from tests.chat_api_helpers import (
    ScriptedProvider,
    StubPipeline,
    _events,
    _install,
    _kb_entries,
    _messages,
    _stream_chat,
    _text_turn,
    _tool_turn,
)


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


# --- attached reference images ----------------------------------------------


def _image(*, size: int = 32, media_type: str = "image/png") -> dict:
    """One attachment payload of ``size`` decoded bytes."""
    return {"media_type": media_type, "data": base64.b64encode(b"\x89PNG" * size).decode()}


def _chat_with_images(client, pid, images, text="build this"):
    """POST a turn carrying attachments and return its decoded SSE events."""
    with client.stream(
        "POST", f"/projects/{pid}/chat", json={"message": text, "images": images}
    ) as r:
        assert r.status_code == 200
        return _events("".join(r.iter_text()))


def _errors(events: list[dict]) -> list[str]:
    return [e.get("detail", "") for e in events if e.get("event") == "error"]


def _current_version_id(store, pid):
    async def _go():
        return (await store.get_project(pid)).current_version_id

    return asyncio.run(_go())


class _BlindProvider(ScriptedProvider):
    """A provider whose model cannot read an image."""

    def capabilities(self, model: str):
        caps = super().capabilities(model)
        return caps.model_copy(update={"supports_images": False})


def test_chat_refuses_an_image_over_the_per_image_limit(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("should not be reached")]))
    monkeypatch.setattr(chat.settings, "chat_image_max_bytes", 64)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    errors = _errors(_chat_with_images(client, pid, [_image(size=64)]))

    assert len(errors) == 1
    # The refusal has to say which limit was hit, not merely that one was.
    assert "size" in errors[0].lower() or "large" in errors[0].lower()
    assert "64" in errors[0]


def test_an_image_at_the_limit_is_not_refused_by_the_encoded_pre_check(client, store, monkeypatch):
    # The pre-check refuses on the base64 length so an oversize payload is not
    # decoded before being told no. Its bound therefore has to be loose enough that
    # a picture exactly at the ceiling still gets through — a pre-check that
    # over-refuses would reject valid images and no size test would notice.
    _install(monkeypatch, ScriptedProvider([_text_turn("ok")]))
    monkeypatch.setattr(chat.settings, "chat_image_max_bytes", 1024)
    monkeypatch.setattr(chat.settings, "chat_image_max_turn_bytes", 4096)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    exactly_at_limit = {
        "media_type": "image/png",
        "data": base64.b64encode(b"x" * 1024).decode(),
    }
    assert _errors(_chat_with_images(client, pid, [exactly_at_limit])) == []


def test_the_encoded_ceiling_never_refuses_a_payload_within_the_decoded_limit():
    # Checked across the three base64 padding cases, since the encoded length of a
    # given byte count is not a single formula but rounds up to a multiple of four.
    for size in range(0, 200):
        encoded = len(base64.b64encode(b"x" * size))
        assert encoded <= chat._encoded_ceiling(size)


def test_chat_refuses_more_images_than_the_count_limit(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("should not be reached")]))
    monkeypatch.setattr(chat.settings, "chat_image_max_count", 2)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    errors = _errors(_chat_with_images(client, pid, [_image(), _image(), _image()]))

    assert len(errors) == 1
    assert "2" in errors[0]
    assert "3" in errors[0]  # says how many arrived, so the user can act on it


def test_chat_refuses_images_over_the_whole_turn_limit(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("should not be reached")]))
    monkeypatch.setattr(chat.settings, "chat_image_max_bytes", 1_000_000)
    monkeypatch.setattr(chat.settings, "chat_image_max_turn_bytes", 300)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    # Each image is under the per-image ceiling; together they are over the turn's.
    errors = _errors(_chat_with_images(client, pid, [_image(size=50), _image(size=50)]))

    assert len(errors) == 1
    assert "300" in errors[0]


def test_chat_refuses_an_unsupported_media_type(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("should not be reached")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    errors = _errors(_chat_with_images(client, pid, [_image(media_type="image/tiff")]))

    assert len(errors) == 1
    assert "image/tiff" in errors[0]  # names the format that was refused


def test_chat_refuses_an_image_when_the_model_cannot_see(client, store, monkeypatch):
    _install(monkeypatch, _BlindProvider([_text_turn("should not be reached")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    errors = _errors(_chat_with_images(client, pid, [_image()]))

    assert len(errors) == 1
    assert "image" in errors[0].lower()


def test_a_refused_attachment_leaves_the_project_version_alone(client, store, monkeypatch):
    # An error raised *inside* a turn reverts the project to its last good version.
    # A refused attachment must not: nothing was built, so nothing is rolled back.
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    _stream_chat(client, pid)
    before = _current_version_id(store, pid)
    assert before is not None  # the turn really did produce a version

    monkeypatch.setattr(chat.settings, "chat_image_max_count", 1)
    _chat_with_images(client, pid, [_image(), _image()])

    assert _current_version_id(store, pid) == before


def test_chat_accepts_a_turn_that_is_only_an_image(client, store, monkeypatch):
    # "here, build this" with the picture doing the talking. The message field is
    # empty, which the request model used to reject outright.
    _install(monkeypatch, ScriptedProvider([_text_turn("A bracket, then.")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _chat_with_images(client, pid, [_image()], text="")

    assert _errors(events) == []


def test_chat_still_refuses_a_turn_with_neither_text_nor_image(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("should not be reached")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    r = client.post(f"/projects/{pid}/chat", json={"message": "", "images": []})

    assert r.status_code == 422


def test_steer_refuses_an_attached_image(client, store, monkeypatch):
    # Steer forwards a bare string into the running loop, so an image on this
    # route would be accepted and then silently dropped. Refuse it instead.
    _install(monkeypatch, ScriptedProvider([_text_turn("ok")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    r = client.post(
        f"/projects/{pid}/chat/steer",
        json={"message": "actually make it taller", "images": [_image()]},
    )

    assert r.status_code == 422


def _replay(store, pid, blocks, content="", role="user"):
    async def go():
        sess = await store.get_or_create_session(pid)
        await store.add_message(sess.id, role, content, blocks=blocks)
        return await chat._replay_history(store, sess.id)

    return asyncio.run(go())


def test_replay_carries_the_reading_rather_than_the_bytes(client, store):
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    block = ContentBlock.of_image(
        data="aGVsbG8=", media_type="image/png", reading="an L-bracket with two holes"
    )

    msgs = _replay(store, pid, [block, ContentBlock.of_text("build this")], content="build this")

    assert {b.kind for m in msgs for b in m.content} == {"text"}  # no pixels replayed
    replayed = msgs[0].content[0].text
    assert "an L-bracket with two holes" in replayed
    assert "build this" in replayed
    assert "aGVsbG8=" not in replayed


def test_replay_falls_back_to_a_placeholder_with_no_reading(client, store):
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    block = ContentBlock.of_image(data="aGVsbG8=", media_type="image/png")

    msgs = _replay(store, pid, [block, ContentBlock.of_text("build this")], content="build this")

    assert "image" in msgs[0].content[0].text.lower()


def test_an_image_only_turn_is_not_dropped_from_the_replayed_history(client, store):
    # The whole message used to vanish, not just the image: an image-only turn
    # flattened to an empty string and the empty check skipped it entirely, so the
    # next turn's model never learned the conversation had a picture in it.
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    block = ContentBlock.of_image(data="aGVsbG8=", media_type="image/png", reading="a cube")

    msgs = _replay(store, pid, [block], content="")

    assert len(msgs) == 1
    assert "a cube" in msgs[0].content[0].text


def test_a_second_turn_sends_the_reading_and_never_the_picture(client, store, monkeypatch):
    provider = ScriptedProvider([_text_turn("A bracket."), _text_turn("Taller, then.")])
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _chat_with_images(client, pid, [_image()], text="build this")
    _stream_chat(client, pid, "now make it twice as tall")

    replayed = provider.calls[-1]["messages"]
    assert "image" not in [b.kind for m in replayed for b in m.content]


def test_an_attached_image_reaches_the_orchestrator(client, store, monkeypatch):
    provider = ScriptedProvider([_text_turn("A bracket.")])
    _install(monkeypatch, provider)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _chat_with_images(client, pid, [_image()], text="build this")

    sent = provider.calls[-1]["messages"][-1].content
    assert "image" in [b.kind for b in sent]


def test_an_attached_image_reaches_the_model_that_writes_the_script(client, store, monkeypatch):
    # The orchestrator seeing it is not enough — the acceptance criterion is that
    # the picture reaches the codegen call, which only happens if the tool layer
    # forwards it into the pipeline.
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a bracket"}),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _chat_with_images(client, pid, [_image()], text="build this")

    assert pipeline.images  # the tool actually ran
    assert [b.kind for b in pipeline.images[-1]] == ["image"]


def test_an_edit_turn_carries_the_image_into_the_pipeline_too(client, store, monkeypatch):
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="edit_model", tool_input={"change": "taller"}),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _chat_with_images(client, pid, [_image()], text="like this, but taller")

    assert [b.kind for b in pipeline.images[-1]] == ["image"]


def test_the_words_are_kept_beside_the_picture_in_the_transcript(client, store, monkeypatch):
    # Persisting blocks stops MessageOut synthesizing one from ``content``, so the
    # message text has to be carried explicitly or it vanishes from the UI.
    _install(monkeypatch, ScriptedProvider([_text_turn("ok")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _chat_with_images(client, pid, [_image()], text="build this")

    user = _messages(store, pid)[0]
    assert [b.kind for b in user.blocks] == ["image", "text"]
    assert user.blocks[1].text == "build this"


def test_the_reading_is_stored_beside_the_picture_it_describes(client, store, monkeypatch):
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a bracket"}),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()
    pipeline.reading = "an L-bracket with two bolt holes"
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _chat_with_images(client, pid, [_image()], text="build this")

    image_block = _messages(store, pid)[0].blocks[0]
    assert image_block.kind == "image"
    assert image_block.reading == "an L-bracket with two bolt holes"


def test_a_turn_whose_model_wrote_no_reading_still_settles(client, store, monkeypatch):
    # The cache is a convenience. A model that ignored the instruction must leave a
    # working turn behind, with the image simply carrying no reading.
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a bracket"}),
            _text_turn("Done."),
        ]
    )
    pipeline = StubPipeline()  # reading stays None
    _install(monkeypatch, provider, pipeline=pipeline)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _chat_with_images(client, pid, [_image()], text="build this")

    assert _errors(events) == []
    assert _messages(store, pid)[0].blocks[0].reading is None


def test_a_text_only_turn_persists_exactly_as_before(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("ok")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid, "make a cube")

    user = _messages(store, pid)[0]
    assert user.blocks == []  # no synthesized blocks; content still carries it
    assert user.content == "make a cube"


def test_steer_still_accepts_a_plain_message(client, store, monkeypatch):
    _install(monkeypatch, ScriptedProvider([_text_turn("ok")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    r = client.post(f"/projects/{pid}/chat/steer", json={"message": "make it taller"})

    assert r.status_code == 202


def test_steer_still_accepts_the_body_it_always_did(client, store, monkeypatch):
    # This route shared ChatRequest, so a client sending `forge` was accepted and
    # the value ignored. Narrowing the model to refuse images must not turn that
    # into a 422 as a side effect — it is a public HTTP contract.
    _install(monkeypatch, ScriptedProvider([_text_turn("ok")]))
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    r = client.post(
        f"/projects/{pid}/chat/steer", json={"message": "make it taller", "forge": False}
    )

    assert r.status_code == 202


# --- the render critique's captures -----------------------------------------

_CRITIQUE_VIEWS = ("front", "right", "top", "iso")


class _CritiquingPipeline(StubPipeline):
    """Reports two critique rounds after building, as the real loop does."""

    def run(
        self,
        intent,
        export_dir=None,
        on_progress=None,
        prior_code=None,
        grounding=None,
        images=(),
        on_reading=None,
        assembly=None,
    ):
        result = super().run(
            intent,
            export_dir=export_dir,
            on_progress=on_progress,
            prior_code=prior_code,
            grounding=grounding,
            images=images,
            on_reading=on_reading,
        )
        for attempt, (matches, feedback) in enumerate(((False, "too tall"), (True, "")), start=1):
            on_progress(
                {
                    "event": "critique",
                    "attempt": attempt,
                    "matches": matches,
                    "feedback": feedback,
                    "views": [
                        {
                            "name": name,
                            "png_b64": base64.standard_b64encode(
                                f"PNG:{name}:{attempt}".encode()
                            ).decode("ascii"),
                        }
                        for name in _CRITIQUE_VIEWS
                    ],
                }
            )
        return result


def _generate_turn(provider_text="Done."):
    return [
        _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "a cube"}),
        _text_turn(provider_text),
    ]


def test_every_critique_round_streams_live(client, store, monkeypatch):
    """Both rounds reach the client, and not inside the post-tool burst.

    Collected progress events arrive as one ``tool_progress`` once the tool has
    settled — after the loop these describe is over — so a capture delivered
    that way is no longer news. Asserting the absence from that burst is what
    keeps them on the live channel.
    """
    _install(monkeypatch, ScriptedProvider(_generate_turn()), pipeline=_CritiquingPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid)

    rounds = [e for e in events if e["event"] == "critique"]
    assert [r["attempt"] for r in rounds] == [1, 2]
    assert [r["matches"] for r in rounds] == [False, True]
    assert rounds[0]["feedback"] == "too tall"
    assert [v["name"] for v in rounds[0]["views"]] == list(_CRITIQUE_VIEWS)
    assert base64.standard_b64decode(rounds[0]["views"][0]["png_b64"]) == b"PNG:front:1"

    nested = [e["stage"] for e in events if e["event"] == "tool_progress" and "stage" in e]
    assert not [s for s in nested if s.get("event") == "critique"], "delivered twice"


def test_the_last_round_of_captures_survives_a_reload(client, store, monkeypatch):
    """The transcript keeps the round the turn ended on, not every round.

    The stream is what shows the work as it happens; a reload wants the state
    the turn finished in. Keeping every round would multiply a turn's stored
    bytes by however many it took to settle.
    """
    _install(monkeypatch, ScriptedProvider(_generate_turn()), pipeline=_CritiquingPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid)

    assistant = [m for m in _messages(store, pid) if m.role == "assistant"][-1]
    captures = [b for b in assistant.blocks if b.kind == "image"]
    assert len(captures) == len(_CRITIQUE_VIEWS)
    assert base64.standard_b64decode(captures[0].data) == b"PNG:front:2"  # the last round
    assert [c.reading for c in captures] == [
        f"a render of the part this turn built, seen from the {name}" for name in _CRITIQUE_VIEWS
    ]

    # The transcript the browser reads carries the shape without the bytes, and
    # the attachment route — untouched, and role-agnostic — serves each one.
    out = client.get(f"/projects/{pid}/messages").json()
    served = next(m for m in out if m["role"] == "assistant" and m["blocks"])
    assert [b["data"] for b in served["blocks"] if b["kind"] == "image"] == [None] * 4
    r = client.get(f"/projects/{pid}/messages/{assistant.id}/attachments/0")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content == b"PNG:front:2"


def test_the_verdict_line_names_something_even_with_no_captures():
    """A critic composed outside this tree can return a verdict and no pictures.

    "Reviewed the build from : it matches the request." is what naming nothing
    produces, and it reaches the transcript where a person reads it.
    """
    line = chat._critique_line({"matches": True, "feedback": "", "views": []})
    assert line == "Reviewed the build from its render: it matches the request."
    assert line.startswith(chat._CRITIQUE_PREFIX)


def test_an_edit_turn_routes_its_critique_the_same_way(client, store, monkeypatch):
    """An edit can build the wrong shape just as readily as a fresh generation.

    Left on the raw callback the captures ride inside the collected burst —
    arriving after the loop they describe, and never reaching the transcript —
    so a mismatch that forced a repair on an edit would leave no trace at all
    while still being paid for.
    """
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="edit_model", tool_input={"change": "taller"}),
            _text_turn("Done."),
        ]
    )
    _install(monkeypatch, provider, pipeline=_CritiquingPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    events = _stream_chat(client, pid, "make it taller")

    assert [e["attempt"] for e in events if e["event"] == "critique"] == [1, 2]
    nested = [e["stage"] for e in events if e["event"] == "tool_progress" and "stage" in e]
    assert not [s for s in nested if s.get("event") == "critique"], "rode the collected burst"

    assistant = [m for m in _messages(store, pid) if m.role == "assistant"][-1]
    assert len([b for b in assistant.blocks if b.kind == "image"]) == len(_CRITIQUE_VIEWS)


def test_the_verdict_is_stored_beside_the_captures(client, store, monkeypatch):
    """Four renders with nothing said about them ask the reader to guess.

    An image on an assistant turn replays as nothing and carries no verdict of
    its own, so the sentence has to be its own block for a reload to be worth
    anything — and, unlike the pictures, it is worth replaying.
    """
    _install(monkeypatch, ScriptedProvider(_generate_turn()), pipeline=_CritiquingPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid)

    assistant = [m for m in _messages(store, pid) if m.role == "assistant"][-1]
    said = [b.text for b in assistant.blocks if b.kind == "text"]
    assert any(
        t == "Reviewed the build from front, right, top, iso: it matches the request." for t in said
    ), said


def test_a_mismatch_verdict_says_what_was_wrong(client, store, monkeypatch):
    class _OneBadRound(_CritiquingPipeline):
        def run(self, intent, export_dir=None, on_progress=None, **kw):
            result = StubPipeline.run(self, intent, export_dir=export_dir, on_progress=on_progress)
            on_progress(
                {
                    "event": "critique",
                    "attempt": 1,
                    "matches": False,
                    "feedback": "the bore is too small",
                    "views": [{"name": "front", "png_b64": "eA=="}],
                }
            )
            return result

    _install(monkeypatch, ScriptedProvider(_generate_turn()), pipeline=_OneBadRound())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid)

    assistant = [m for m in _messages(store, pid) if m.role == "assistant"][-1]
    said = [b.text for b in assistant.blocks if b.kind == "text"]
    assert "Reviewed the build from front: the bore is too small." in said, said


def test_nothing_the_review_stored_is_replayed_into_a_later_turn(client, store, monkeypatch):
    """Neither the renders nor the sentence was something the model said.

    The images replayed through the reference-image path would misdescribe
    where they came from and add four lines to every turn that follows, for the
    rest of the session. The sentence is worse: it is free prose a vision model
    wrote from a prompt carrying the user's own words, and replaying it hands
    the orchestrator that text as a fact the assistant had itself established.
    Both are kept for a reader reloading the page and for nobody else.
    """
    provider = ScriptedProvider(_generate_turn() + [_text_turn("And again.")])
    _install(monkeypatch, provider, pipeline=_CritiquingPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid)
    _stream_chat(client, pid, "and again")

    replayed = "\n".join(
        block.text or ""
        for call in provider.calls
        for message in call["messages"]
        for block in message.content
    )
    assert "reference image" not in replayed
    assert "Reviewed the build from" not in replayed
    # The model's own words still replay — this must narrow what reaches the
    # orchestrator, not silence the assistant turn it is attached to.
    assert "Done." in replayed


def test_a_tool_result_s_files_are_taken_only_from_the_turn_s_staging(store, tmp_path):
    """The thumbnail path is the worker's report; a directory taken from it on trust
    is whatever directory it names."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "model.glb").write_bytes(b"glTF-not-this-turn-s")
    staging = tmp_path / "staging"
    staging.mkdir()

    async def go():
        await store.init()
        p = await store.create_project("P")
        payload = {"ok": True, "code": "result = 1", "thumbnail": str(elsewhere / "model.glb")}
        vid = await chat._persist_tool_version(store, p.id, "x", payload, staging=staging)
        return await store.list_artifacts(vid)

    assert asyncio.run(go()) == []


class _WantingPartsPipeline(StubPipeline):
    def run(self, *args, **kwargs):
        result = super().run(*args, **kwargs)
        result.assembly = {"ok": False, "failures": ["two parts share volume"], "measured": True}
        return result


def test_a_build_kept_with_parts_found_wanting_distills_nothing(client, store, monkeypatch):
    """It is the model asked for, but no example to learn from."""
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "x"}),
            _text_turn("Built it, though two parts overlap."),
        ]
    )
    _install(monkeypatch, provider, pipeline=_WantingPartsPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid)
    assert _kb_entries(store) == []


class _SoundThenWantingPipeline(StubPipeline):
    def __init__(self):
        super().__init__()
        self.builds = 0

    def run(self, *args, **kwargs):
        result = super().run(*args, **kwargs)
        self.builds += 1
        if self.builds > 1:
            result.assembly = {"ok": False, "failures": ["two parts share volume"]}
        return result


def test_a_turn_ending_on_parts_found_wanting_distills_no_earlier_build(client, store, monkeypatch):
    """The turn's message now describes the build it ended on, not the one before."""
    provider = ScriptedProvider(
        [
            _tool_turn(tool_use_id="tu-1", name="generate_model", tool_input={"spec": "x"}),
            _tool_turn(tool_use_id="tu-2", name="edit_model", tool_input={"change": "y"}),
            _text_turn("Done, though two parts overlap."),
        ]
    )
    _install(monkeypatch, provider, pipeline=_SoundThenWantingPipeline())
    pid = client.post("/projects", json={"name": "P"}).json()["id"]

    _stream_chat(client, pid)
    assert _kb_entries(store) == []
