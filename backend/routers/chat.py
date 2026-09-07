"""Chat SSE turn endpoint: agent loop -> neutral -> UI events.

``POST /projects/{id}/chat`` (SSE) accepts a user message, resolves the project's
single chat session, runs the provider-agnostic agent loop
(:mod:`cadless.agent`), and streams UI events mapped from the agent's neutral
:class:`~cadless.agent.StreamEventOut`s:

  ``turn_start`` · ``text_delta {text}`` · ``tool_start {tool,label}`` ·
  ``tool_progress {stage: …}`` · ``tool_result {version_id,ok,metrics,thumbnail}`` ·
  ``turn_end {stop_reason}`` · ``error``

The existing pipeline ``stage`` events NEST inside ``tool_progress`` so the
frontend ``StagedProgress`` is reused verbatim.

The turn is persisted like ``persist_generation``: a user message plus
a ``pending`` assistant message written up-front, settled to ``ok``/``error`` with
``blocks_json`` at the end. Each successful tool call persists a real
``ScriptVersion`` (+ artifacts) so ``tool_result`` can link ``version_id`` and a
served thumbnail URL. A clean abort / provider failure settles the dangling
assistant turn to ``error`` — never leaving it ``pending``.

This reuses the SSE plumbing pattern from ``backend/routers/generation.py``; it
does not reinvent it.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from backend.catalog_state import reject_if_catalog
from backend.deps import get_store
from backend.sse import SSE_HEADERS
from cadless import user_settings
from cadless.agent import Agent, SessionSteerRegistry, ToolContext
from cadless.compaction import compact_history
from cadless.config import settings
from cadless.distill import auto_distill
from cadless.exporters import EXPORTERS
from cadless.forge import persist_losers
from cadless.llm.registry import build_provider  # monkeypatched in tests
from cadless.llm.types import ContentBlock
from cadless.params import extract_params
from cadless.pipeline import Pipeline
from cadless.rag import retrieve_grounding
from cadless.scoped_store import ScopedStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# Process-wide steer registry: the in-flight `/chat` loop drains its
# session's queue at each iteration boundary; `POST /chat/steer` enqueues into it.
_steer_registry = SessionSteerRegistry()


class ImageAttachment(BaseModel):
    """One reference image the user attached to a turn."""

    media_type: str
    data: str  # base64; decoded once at the boundary to measure and to validate


class ChatRequest(BaseModel):
    message: str = ""
    # Reference images for this turn. They reach the model that writes the script
    # and are not carried into later turns — see ``_replay_history``.
    images: list[ImageAttachment] = Field(default_factory=list)
    # Per-turn forge opt-in (C4): a fresh generation races best-of-N when
    # this is True AND the global ``forge_enabled`` kill-switch is on (both-true
    # gate). Default False => today's single-generation behavior. Forge is per-turn,
    # not a persistent project setting: each turn opts in explicitly.
    forge: bool = False

    @model_validator(mode="after")
    def _needs_something_to_act_on(self) -> ChatRequest:
        """A turn must say something, in words or in pictures.

        ``message`` used to be non-empty by construction. It cannot be any more:
        handing over a photograph with no caption is a real way to ask for a part,
        and rejecting it would make the attachment useless on its own.
        """
        if not self.message.strip() and not self.images:
            raise ValueError("a turn needs a message or at least one image")
        return self


class SteerRequest(BaseModel):
    """The steer body — deliberately narrower than :class:`ChatRequest`.

    Steering injects a bare string into a running loop, so there is nowhere for an
    image to go. Sharing ``ChatRequest`` would make this route accept one and drop
    it without a word, which is worse than refusing it.
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    # Accepted and ignored, exactly as it was while this route shared ``ChatRequest``.
    # ``extra="forbid"`` is aimed at ``images``; leaving ``forge`` off it would turn
    # a body that has always been accepted into a 422 for a reason that has nothing
    # to do with why the model was narrowed.
    forge: bool = False


def build_pipeline() -> Pipeline:
    """Build the CAD pipeline the agent's tools run against (monkeypatched in tests)."""
    return Pipeline()


def _refusal(detail: str) -> EventSourceResponse:
    """Answer a turn that cannot run with one error event and nothing else.

    Refusals on this route are delivered in the stream rather than as a status
    code, because the reason is for the person typing — "that image is too large"
    belongs in the conversation, where the frontend already renders an ``error``
    event. It also has to happen *before* the turn starts: once running, an
    exception settles the turn and reverts the project to its last good version
    (see ``run``), which is a destructive way to report a rejected attachment.
    """

    async def _one():
        yield {"data": json.dumps({"event": "error", "detail": detail})}

    return EventSourceResponse(_one(), headers=SSE_HEADERS)


def _encoded_ceiling(decoded_limit: int) -> int:
    """The largest base64 string that could still decode within ``decoded_limit``."""
    return (decoded_limit + 2) // 3 * 4 + 4


def _check_images(images: list[ImageAttachment]) -> None:
    """Gate the turn's attachments, or raise ``ValueError`` saying which limit broke.

    Returns nothing: the decoded bytes are not the product here, only the verdict.
    The blocks the turn goes on to carry are built from the base64 the request
    already holds, so keeping every decoded image alive to hand back would double
    the peak for no reader.

    Every limit names itself and the value that broke it, so the message is
    something the user can act on rather than a bare refusal.
    """
    if len(images) > settings.chat_image_max_count:
        raise ValueError(
            f"at most {settings.chat_image_max_count} images per turn; "
            f"this turn carried {len(images)}"
        )

    allowed = settings.chat_image_media_types
    total = 0
    for n, image in enumerate(images, start=1):
        if image.media_type not in allowed:
            raise ValueError(
                f"attachment {n}: {image.media_type} is not a format this can send; "
                f"supported formats are {', '.join(allowed)}"
            )
        # Refuse on the encoded length first. Decoding is where the cost is, and a
        # ceiling checked afterwards is a ceiling the caller has already spent.
        # base64 is four characters per three bytes, so the encoded form is never
        # shorter than this bound — a string past it cannot decode to something
        # within the limit, whatever its padding.
        if len(image.data) > _encoded_ceiling(settings.chat_image_max_bytes):
            raise ValueError(
                f"attachment {n} is too large, over the "
                f"{settings.chat_image_max_bytes}-byte limit for one image"
            )
        try:
            raw = base64.b64decode(image.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            # Unreadable rather than oversized: refuse instead of forwarding bytes
            # no adapter could encode.
            raise ValueError(f"attachment {n} is not valid base64 data") from exc
        if len(raw) > settings.chat_image_max_bytes:
            raise ValueError(
                f"attachment {n} is too large: {len(raw)} bytes, over the "
                f"{settings.chat_image_max_bytes}-byte limit for one image"
            )
        total += len(raw)
        if total > settings.chat_image_max_turn_bytes:
            # Checked inside the loop so a turn already over its ceiling stops
            # decoding rather than finishing the batch to say so — which is also
            # why the message says "already more than" rather than quoting a
            # running total the user would read as everything they sent.
            raise ValueError(
                "the attachments already come to more than the "
                f"{settings.chat_image_max_turn_bytes}-byte limit for one turn"
            )


def _blind_role(provider) -> str | None:
    """The name of a model in this turn's path that cannot read an image.

    Two models see the picture: the orchestrator reads it to decide what to build,
    and the codegen model gets it again when it writes the script. An image that
    only reaches the first would leave the part built from a description of the
    picture rather than the picture. ``bedrock_model_slug`` is the codegen slug
    despite its name — it is what :class:`~cadless.prompts.CodeGenerator` resolves.
    """
    for slug in dict.fromkeys((settings.orchestrator_model, settings.bedrock_model_slug)):
        if not provider.capabilities(slug).supports_images:
            return slug
    return None


async def _current_model(store: ScopedStore, project_id: int) -> tuple[str | None, dict]:
    """Resolve the project's current code + params for the agent's edit context."""
    project = await store.get_project(project_id)
    if not project or project.current_version_id is None:
        return None, {}
    version = await store.get_version(project.current_version_id)
    if not version or not version.code:
        return None, {}
    return version.code, version.parameters or extract_params(version.code)


def _replayed_block(block: ContentBlock) -> str:
    """What one stored block contributes to the replayed conversation, as text.

    An image replays as words, never as pixels. This function only ever sees past
    turns — the current turn's attachments are handed to the agent directly — so
    an image reaching here is by definition one the model has already looked at,
    and the cached reading is what it wrote down at the time. Sending the picture
    again would charge for every turn that follows it.

    Everything else that is not conversational text contributes nothing, as before:
    replaying past tool and thinking plumbing builds an invalid transcript.
    """
    if block.kind == "text":
        return (block.text or "").strip()
    if block.kind == "image":
        return f"[reference image: {block.reading}]" if block.reading else "[a reference image]"
    return ""


async def _replay_history(store: ScopedStore, session_id: int) -> list:
    """Replay the persisted transcript as neutral agent ``Message``s — text only.

    A stored turn flattens its whole agent loop into one message's blocks
    (e.g. ``[thinking, tool_use, tool_result, text]``). Replaying those verbatim
    builds an INVALID Bedrock transcript — a ``toolResult`` or signed ``thinking``
    block plus a trailing ``text`` inside a single ``assistant`` message, which the
    model rejects ("This model doesn't support the text field for assistant
    messages"). Past-turn reasoning / tool plumbing isn't needed for context (the
    current code is the source of truth, handed to the agent separately), so we
    replay only the conversational text. Consecutive same-role messages are merged
    so a dropped tool-only/clarification turn can't leave an invalid role sequence.
    """
    from cadless.llm.types import Message

    messages: list[Message] = []
    for m in await store.list_messages(session_id):
        parts = [_replayed_block(b) for b in m.blocks]
        text = "\n\n".join(p for p in parts if p) or (m.content or "").strip()
        if not text:
            continue
        if messages and messages[-1].role == m.role:  # keep roles alternating
            messages[-1].content.append(ContentBlock.of_text(text))
        else:
            messages.append(Message(role=m.role, content=[ContentBlock.of_text(text)]))
    return messages


async def _persist_tool_version(
    store: ScopedStore,
    project_id: int,
    intent: str,
    payload: dict,
    plan_step: int | None = None,
    parent_version_id: int | None = None,
) -> int | None:
    """Persist a ScriptVersion (+ artifacts) for a successful tool result.

    Returns the new version id, or ``None`` when the tool failed (nothing to
    persist). Mirrors ``persist_generation``'s artifact copy + set-current logic.

    ``plan_step`` is the OPTIONAL active plan-step pointer at the moment
    this checkpoint is written, so the UI can later narrate "rolled back to step N".
    It is ``None`` for unplanned turns; passing it through is purely additive.

    ``parent_version_id`` chains this checkpoint onto the model it was built from
    (the version current when the turn started, then the previous tool's version),
    so the version lineage stays connected across chat turns instead of every turn
    becoming a disconnected root.
    """
    if not payload.get("ok"):
        return None
    metrics = payload.get("metrics") or {}
    bbox = metrics.get("bbox")
    version = await store.add_version(
        project_id,
        intent,
        payload.get("code"),
        True,
        None,
        metrics.get("volume"),
        tuple(bbox) if bbox else None,
        parameters=metrics.get("parameters") or {},
        parent_version_id=parent_version_id,
        plan_step=plan_step,
    )
    thumb = payload.get("thumbnail")
    src_dir = Path(thumb).parent if thumb else None
    if src_dir and src_dir.exists():
        dest = store.version_artifact_dir(version.id)
        for kind in EXPORTERS:
            src = src_dir / f"model.{kind}"
            if src.exists():
                target = Path(dest) / f"model.{kind}"
                shutil.copy(src, target)
                await store.add_artifact(version.id, kind, str(target))
    await store.set_current_version(project_id, version.id)
    return version.id


@router.post("/projects/{project_id}/chat")
async def chat(project_id: int, body: ChatRequest, store: ScopedStore = Depends(get_store)):
    if not await store.get_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    # A turn persists a new version and moves current, so catalog items refuse
    # it. Ahead of the credentials guard: a read-only item is not a key problem.
    await reject_if_catalog(store, project_id, "edit it with chat")

    # Keyless-first-run guard: if the selected provider has no usable credentials,
    # guide the user to Settings instead of running a doomed LLM call that fails
    # with a cryptic vendor error (or silently no-ops on ambient creds).
    if not user_settings.has_credentials():
        return _refusal(user_settings.credentials_hint())

    provider = build_provider()
    image_blocks: list[ContentBlock] = []
    if body.images:
        try:
            _check_images(body.images)
        except ValueError as exc:
            return _refusal(str(exc))
        blind = _blind_role(provider)
        if blind is not None:
            return _refusal(
                f"the configured model {blind!r} cannot read images. Remove the "
                "attachment, or pick a vision-capable model in Settings."
            )
        image_blocks = [
            ContentBlock.of_image(data=i.data, media_type=i.media_type) for i in body.images
        ]

    session = await store.get_or_create_session(project_id)
    history = await _replay_history(store, session.id)
    code, params = await _current_model(store, project_id)

    # A non-empty ``blocks`` stops ``MessageOut.of`` synthesizing a text block from
    # ``content``, so once there is a picture the words have to be carried beside it
    # explicitly or they disappear from the transcript.
    user_blocks = list(image_blocks)
    if user_blocks and body.message.strip():
        user_blocks.append(ContentBlock.of_text(body.message))
    user_message = await store.add_message(
        session.id, "user", body.message, blocks=user_blocks or None
    )
    # The first reading the turn produces wins. Repair rounds see the same picture
    # and would each write another, and a later one is a reading of a build that
    # went wrong rather than of the reference.
    readings: list[str] = []

    def _keep_first_reading(reading: str) -> None:
        if not readings:
            readings.append(reading)

    assistant = await store.add_message(session.id, "assistant", None, status="pending")

    staging = Path(store.artifacts_dir) / "_staging" / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)

    # Session hygiene: fold older turns of a long transcript into a
    # rolling synopsis so the agent's context stays bounded. The durable code
    # source of truth stays the persisted script_versions chain — this only
    # rewrites the conversational history replayed to the model. Purely additive:
    # a short session (at/below threshold) yields today's full history unchanged.
    history = await compact_history(history, provider)
    pipeline = build_pipeline()
    # Forge both-true gate (C4): race best-of-N only when the turn opted in
    # AND the global kill-switch is on. N is budget-scaled (config), not hard-coded.
    forge_active = bool(body.forge and settings.forge_enabled)
    forge_n = settings.forge_scaled_n() if forge_active else 1
    # The current version (if any) is the parent the race branches off, so winner +
    # loser candidate rows hang off the model the turn started from.
    project = await store.get_project(project_id)
    parent_version_id = project.current_version_id if project else None

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    # Stream fresh-generation codegen tokens to the client live as the model writes
    # the build123d code: the agent pushes each delta here, and we map it
    # to a ``codegen_delta`` SSE event onto the same queue the UI events use.
    context = ToolContext(
        pipeline=pipeline,
        current_code=code,
        current_params=params,
        export_dir=str(staging),
        images=image_blocks,
        on_reading=_keep_first_reading if image_blocks else None,
        forge=forge_active,
        forge_n=forge_n,
        on_codegen=lambda text: emit({"event": "codegen_delta", "text": text}),
        # The reviewer's captures and verdict, forwarded verbatim: the payload is
        # already the shape the client reads, and re-wrapping it here would put
        # the event's field names in two places.
        on_critique=emit,
    )
    agent = Agent(provider=provider)

    async def run() -> None:
        produced_blocks: list = []
        any_failure = False
        settled = False
        # The last successful tool result of the turn — its code + metrics seed the
        # auto-distill flywheel once the turn settles ok.
        last_good: dict | None = None
        try:
            # The agent loop is synchronous; drive it in a worker thread and bridge
            # each yielded neutral event onto the asyncio queue.
            # Plan-step narration: track the active plan submitted this
            # turn so each subsequent action checkpoint is stamped with the 1-based
            # step it advanced. ``plan_total`` is 0 until a plan event arrives, in
            # which case ``plan_step`` stays None (unplanned behavior unchanged).
            plan_total = 0
            plan_cursor = 0
            # Keep the version lineage connected: each persisted checkpoint chains
            # onto the previous one, starting from the model the turn began on, so a
            # chat turn refines its predecessor instead of forking a new root.
            chain_parent = parent_version_id

            def drive() -> None:
                nonlocal produced_blocks, any_failure, last_good
                nonlocal plan_total, plan_cursor, chain_parent
                for ev in agent.stream_turn(
                    user_text=body.message,
                    context=context,
                    history=history,
                    steer=_steer_registry.source_for(session.id),
                ):
                    if ev.kind == "plan":
                        plan_total = len(ev.data.get("steps") or [])
                        plan_cursor = 0
                    if ev.kind == "tool_result":
                        # Persist a version for a successful tool, link it + a served
                        # thumbnail URL into the UI event (scheduled on the loop).
                        ok = bool(ev.data.get("ok"))
                        any_failure = any_failure or not ok
                        plan_step = None
                        if plan_total:
                            plan_cursor = min(plan_cursor + 1, plan_total)
                            plan_step = plan_cursor
                        version_id = asyncio.run_coroutine_threadsafe(
                            _persist_tool_version(
                                store,
                                project_id,
                                body.message,
                                ev.data,
                                plan_step,
                                parent_version_id=chain_parent,
                            ),
                            loop,
                        ).result()
                        if version_id is not None:
                            chain_parent = version_id  # next tool chains onto this one
                        # Blueprint rollback policy (D3): on a failed step
                        # WITHIN a planned (Blueprint) turn, auto-revert current to
                        # the last OK version straight away (reusing D1's last_ok
                        # logic) so a bad step never leaves the project on broken or
                        # partial geometry mid-turn. The failed tool_result is still
                        # fed back to the orchestrator below (the existing error
                        # channel) so it can retry/replan rather than collapsing the
                        # turn — bounding stays with Phase A's escalation + the
                        # tool-iteration cap. This is a thin policy reaction at the
                        # existing tool-call boundary, NOT a 1:1 plan-step executor.
                        # Gated on a plan being active (``plan_total``): ordinary
                        # unplanned turns keep today's behavior (settlement-only
                        # revert) untouched.
                        if not ok and plan_total:
                            asyncio.run_coroutine_threadsafe(
                                _revert_to_last_ok(store, project_id),
                                loop,
                            ).result()
                        # Forge race: the winner was just persisted as the
                        # current version above; record the losing candidates as
                        # non-current rows tied to it (same async-store bridge).
                        forge_race = ev.data.get("forge")
                        if version_id is not None and forge_race:
                            asyncio.run_coroutine_threadsafe(
                                persist_losers(
                                    store,
                                    project_id,
                                    body.message,
                                    forge_race.get("losers", []),
                                    winner_version_id=version_id,
                                    parent_version_id=parent_version_id,
                                ),
                                loop,
                            ).result()
                        if ok and ev.data.get("code") and version_id is not None:
                            last_good = {
                                "version_id": version_id,
                                "code": ev.data.get("code"),
                                "metrics": ev.data.get("metrics") or {},
                            }
                        data = {
                            "version_id": version_id,
                            "ok": ok,
                            "metrics": ev.data.get("metrics"),
                            "thumbnail": (
                                f"/versions/{version_id}/artifacts/glb"
                                if version_id is not None
                                else None
                            ),
                            "tool": ev.data.get("tool"),
                            "error": ev.data.get("error"),
                        }
                        emit({"event": "tool_result", **data})
                    elif ev.kind == "turn_end":
                        result = ev.data.get("result")
                        if result is not None:
                            produced_blocks = result.blocks
                        emit({"event": "turn_end", "stop_reason": ev.data.get("stop_reason")})
                    else:
                        emit({"event": ev.kind, **ev.data})

            # Dynamic RAG: retrieve known-good KB grounding for this
            # turn's request and hand it to the agent via the context. Retrieval is
            # async (the KB store), but the agent loop runs sync-in-threadpool, so we
            # retrieve HERE at the async layer (same place auto_distill runs) and
            # thread the grounding string down through ToolContext. Best-effort: a
            # retrieval failure must never fail the turn, mirroring the distill hook.
            try:
                # The pipeline's snapshot, not the live singleton: retrieval and
                # generation belong to the same turn, so a save landing between
                # them must not put them under different settings.
                context.grounding = await retrieve_grounding(
                    store, provider, intent=body.message, config=pipeline.config
                )
            except Exception:  # noqa: BLE001  (best-effort grounding — never fail the turn)
                logger.warning(
                    "RAG grounding retrieval failed for project %s; continuing without grounding",
                    project_id,
                    exc_info=True,
                )
                context.grounding = ""

            await run_in_threadpool(drive)
            status = "error" if any_failure else "ok"
            version_id = await _latest_version_id(any_failure, store, project_id)
            await store.update_message(
                assistant.id,
                status=status,
                blocks=produced_blocks,
                version_id=version_id,
                error="a tool call failed" if any_failure else None,
            )
            settled = True
            # Flywheel: a turn that settled ok+asserted auto-distills its
            # known-good result into a KB entry. Best-effort and AFTER the turn has
            # settled, so a distill failure can never affect the user's turn.
            if not any_failure and last_good is not None:
                await auto_distill(
                    store,
                    provider,
                    project_id=project_id,
                    version_id=last_good["version_id"],
                    intent=body.message,
                    code=last_good["code"],
                    ok=True,
                    metrics=last_good["metrics"],
                )
        except Exception as exc:  # noqa: BLE001  (abort / provider failure)
            # Auto-revert policy: an aborted/failed turn must never leave
            # current pointing at a failed/partial version — guarantee last OK.
            fallback_id = await _revert_to_last_ok(store, project_id)
            await store.update_message(
                assistant.id,
                status="error",
                error=str(exc),
                blocks=produced_blocks,
                version_id=fallback_id,
            )
            settled = True
            emit({"event": "error", "detail": str(exc)})
        finally:
            if not settled:
                # Defensive: never leave a dangling pending assistant turn.
                await store.update_message(assistant.id, status="error", error="turn aborted")
            # Keep the reading beside the picture it describes. In ``finally``
            # because a turn that aborted after codegen still read the reference,
            # and best-effort because a cache that could fail a turn would be a
            # worse trade than no cache at all.
            if readings:
                try:
                    await store.update_message(
                        user_message.id,
                        blocks=[
                            b.model_copy(update={"reading": readings[0]})
                            if b.kind == "image"
                            else b
                            for b in user_blocks
                        ],
                    )
                except Exception:  # noqa: BLE001  (the cache is never worth a turn)
                    logger.warning(
                        "could not store the reference reading for project %s",
                        project_id,
                        exc_info=True,
                    )
            shutil.rmtree(staging, ignore_errors=True)
            loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

    task = asyncio.create_task(run())

    async def events():
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield {"data": json.dumps(event)}
        finally:
            await task

    return EventSourceResponse(events(), headers=SSE_HEADERS)


@router.post("/projects/{project_id}/chat/steer", status_code=202)
async def steer(project_id: int, body: SteerRequest, store: ScopedStore = Depends(get_store)):
    """Queue a steer message for the project's in-flight `/chat` turn.

    The message is enqueued into the session's steer queue; the running agent loop
    drains it at its next iteration boundary, injecting it so the next model call
    sees it (and persisting it in order on the assistant turn). Idempotent w.r.t.
    whether a turn is active: if none is running, the message simply waits for the
    next turn's first boundary. Hard caps are unaffected — the loop checks them
    independently of steering.
    """
    if not await store.get_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    session = await store.get_or_create_session(project_id)
    _steer_registry.enqueue(session.id, body.message)
    return {"queued": True}


async def _last_ok_version_id(store: ScopedStore, project_id: int) -> int | None:
    """The id of the project's last OK (current-eligible) version, or ``None``.

    Pillar 4 safety net: the explicit "last good model" the project
    should fall back to when a turn fails or aborts.
    """
    version = await store.last_ok_version(project_id)
    return version.id if version else None


async def _revert_to_last_ok(store: ScopedStore, project_id: int) -> int | None:
    """Auto-revert policy: guarantee the project's current is its last OK version.

    Formalizes the previously-implicit "current = last good" behavior.
    Both the failed-turn settlement and the abort/except path call this so a failed
    or partial version can never be left as the project's current. Returns the
    resulting current version id (the last OK one, or ``None`` if none exists).
    """
    target = await _last_ok_version_id(store, project_id)
    if target is not None:
        await store.set_current_version(project_id, target)
    return target


async def _latest_version_id(any_failure: bool, store: ScopedStore, project_id: int) -> int | None:
    """Resolve the version the assistant turn settles on.

    On success the turn keeps its new version (the project's current). On failure
    the auto-revert policy guarantees current points at the last OK version, and
    the assistant turn is linked to that fallback."""
    if any_failure:
        return await _revert_to_last_ok(store, project_id)
    project = await store.get_project(project_id)
    return project.current_version_id if project else None
