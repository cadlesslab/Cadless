"""The generate -> validate -> execute -> repair loop.

Ties the pieces together: a CodeGenerator (/3276) produces build123d code,
the static validator gates it, the execution worker runs it
and exports artifacts (/3280). On any failure the error is fed back to the
model for a bounded number of repair attempts (``repair_max_attempts``, total
tries including the first generation).

Progress events
--------------------------
``run`` calls ``on_progress(event)`` as it works. Three event shapes are emitted
here; the API layer adds ``done``/``error`` when the version is persisted:

  {"event": "start",  "intent": str, "max_tries": int, "mode": "generate"|"refine"}
      once, before any work.
  {"event": "stage",  "phase": str, "status": "begin"|"ok"|"error",
                      "attempt": int, "error"?: str}
      the granular lifecycle. ``phase`` is one of ``STAGE_PHASES``:
      interpret -> generate|refine -> (validate -> build -> mesh [-> critique])*
      with ``repair`` between failed attempts. ``attempt`` is the 1-based try
      (0 for the pre-loop interpret/generate phases). Meshing happens inside the
      worker alongside ``build``; ``mesh`` is reported ``ok`` once artifacts exist.
  {"event": "attempt", "n": int, "stage": str, "ok": bool, "error": str|None}
      one per attempt — retained for the existing UI consumer (do not remove).

Consumers must ignore unknown event types and fields for forward compatibility.
"""

from __future__ import annotations

import base64
import logging
import os
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from cadless.assertions import (
    GeometryAssertions,
    GeometrySignature,
    evaluate_assertions,
)
from cadless.config import Settings, settings
from cadless.llm.types import ContentBlock
from cadless.params import extract_params
from cadless.prompts import CodeGenerator
from cadless.validation import validate_code
from cadless.worker import run_code

logger = logging.getLogger(__name__)

# Lifecycle phases emitted as {"event": "stage", "phase": ..., "status": ...}.
STAGE_PHASES = (
    "interpret",
    "generate",
    "refine",
    "validate",
    "build",
    "mesh",
    "critique",
    "assert",
    "repair",
)


@dataclass
class Attempt:
    n: int
    code: str
    stage: str  # "validate" | "execute"
    error: str | None  # None == this attempt succeeded


@dataclass
class GenerationResult:
    ok: bool
    intent: str
    code: str | None = None
    error: str | None = None
    volume: float | None = None
    bbox: tuple[float, float, float] | None = None
    step_path: str | None = None
    glb_path: str | None = None
    stl_path: str | None = None
    obj_path: str | None = None
    parameters: dict = field(default_factory=dict)
    attempts: list[Attempt] = field(default_factory=list)
    #: The render review of the build this result carries, as
    #: ``{"matches": bool, "attempt": int}``, or ``None`` where that build was
    #: not reviewed — a verdict never outlives the attempt it was taken for.
    #: Deliberately carries no text. The orchestrator needs to know the reviewer
    #: disagreed — otherwise it announces a finished part beside a verdict saying
    #: it is wrong — but the reviewer's own words are a vision model's free prose
    #: written from a prompt holding the user's, and handing that to the
    #: orchestrator as fact is the thing the transcript already refuses to do.
    critique: dict | None = None

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def last_stage(self) -> str | None:
        """The repair stage of the final attempt (e.g. ``validate``/``execute``).

        This is where the loop last acted — the failing stage on a failed run, or
        ``execute`` on a successful one. Surfaced so the orchestrator can detect a
        repeated 'Nth failure at the same stage' cycle. ``None`` when no
        attempt ran.
        """
        return self.attempts[-1].stage if self.attempts else None


class Pipeline:
    def __init__(
        self,
        generator: CodeGenerator | None = None,
        config: Settings | None = None,
        critic=None,
    ):
        self._gen = generator or CodeGenerator()
        # Snapshot, not the live object. The settings layer applies a change by
        # mutating the shared singleton in place, so holding it would let an
        # edit land part-way through a turn and leave that turn attributable to
        # no single configuration — which is precisely what an A/B of a quality
        # knob needs it to be. `build_pipeline()` runs per request, so a request
        # still sees the current values; only a turn already under way is
        # insulated. `CodeGenerator` already pins its model the same way.
        self._cfg = (config or settings).model_copy()
        self._critic = critic  # optional VlmCritic

    @property
    def config(self) -> Settings:
        """The snapshot this pipeline runs under.

        Exposed so everything else a turn touches can be handed the same
        configuration. Grounding retrieval in particular happens outside the
        pipeline (``backend/routers/chat.py``), and reading the live singleton
        there would put a turn's retrieval and its generation under two
        different settings — the split this snapshot exists to prevent.
        """
        return self._cfg

    def run(
        self,
        intent: str,
        export_dir: str | None = None,
        on_progress=None,
        prior_code: str | None = None,
        assertions: GeometryAssertions | None = None,
        grounding: str | None = None,
        temperature: float | None = None,
        export_scale: float = 1.0,
        images: Sequence[ContentBlock] = (),
        on_reading: Callable[[str], None] | None = None,
        critique: bool = True,
    ) -> GenerationResult:
        """Generate (or, when ``prior_code`` is given, refine) then validate/execute.

        Refinement edits ``prior_code`` to satisfy the change request ``intent``;
        a fresh generation builds from ``intent`` alone. The validate -> execute ->
        repair loop is identical in both modes.

        ``grounding`` is the optional dynamic-RAG block of retrieved
        known-good examples. It is forwarded ONLY to the fresh-generation branch
        (``generate``); refinement is out of B4 scope and never sees it. Purely
        additive: ``None`` (the default) reproduces the legacy no-retrieval prompt.

        ``assertions`` are optional, deterministic post-conditions checked
        after a successful build. A failed assertion adds a repair signal through the
        same mechanism the VLM critique uses; it never hard-stops and a missing
        assertion never blocks. This is the "run the unit tests" gate for geometry.

        ``temperature`` overrides the provider default for the *fresh*
        generation call only; the best-of-N fan-out raises it for diversity.
        ``None`` (the default) keeps the legacy single-run temperature, so the
        normal path is unchanged. It is ignored on the refine path.

        ``images`` are the turn's reference pictures. Unlike ``grounding``, they
        reach BOTH branches and the repair rounds: a picture is what the request
        actually is, so an edit or a repair that lost it would be working from the
        half of the request least able to describe the shape. ``()`` (the default)
        leaves every call on the path it took before.

        ``export_scale`` (issue #30) is the authoring-units -> millimetre factor
        applied to exported artifacts only (never the volume/bbox geometry
        summary), matching how catalog goldens bake at the domain registry's
        scale. The default ``1.0`` keeps every legacy caller byte-identical.

        ``critique`` lets a caller opt this run out of the render review even
        where one is configured. It exists for the best-of-N fan-out, where the
        judge has a vision rung of its own and each candidate would otherwise
        pay separately for a signal that rung derives once.
        """
        attempts: list[Attempt] = []
        max_tries = max(1, self._cfg.repair_max_attempts)
        mode = "refine" if prior_code else "generate"
        _emit(
            on_progress, {"event": "start", "intent": intent, "max_tries": max_tries, "mode": mode}
        )
        _emit_stage(on_progress, "interpret", "ok", 0)

        _emit_stage(on_progress, mode, "begin", 1)
        if prior_code:
            # Refine is out of streaming scope: keep the one-shot call.
            code = self._gen.refine(intent, prior_code, images=images, on_reading=on_reading)
        else:
            # Fresh generation streams its tokens as a ``codegen`` progress event so
            # the chat layer can show the code being written live. The
            # token sink is wired ONLY when a progress listener is present, so the
            # forge fan-out (candidates run without on_progress) and offline callers
            # keep the exact one-shot call shape (no ``on_token`` kwarg at all).
            on_token = _codegen_on_token(on_progress)
            extra = {"on_token": on_token} if on_token is not None else {}
            code = self._gen.generate(
                intent,
                grounding,
                temperature=temperature,
                images=images,
                on_reading=on_reading,
                **extra,
            )
        _emit_stage(on_progress, mode, "ok", 1)
        last_error = "no attempts ran"
        last_critique: dict | None = None

        for n in range(1, max_tries + 1):
            # Cleared here rather than beside the critique, so that every way an
            # attempt can end reaches it — a build that fails to execute never
            # gets as far as a review, and carrying the previous attempt's
            # verdict past it attaches a pass to a build that is not the one
            # being returned.
            last_critique = None
            _emit_stage(on_progress, "validate", "begin", n)
            verdict = validate_code(code)
            if not verdict.ok:
                last_error = "validation: " + "; ".join(verdict.reasons)
                _emit_stage(on_progress, "validate", "error", n, last_error)
                self._record(attempts, on_progress, Attempt(n, code, "validate", last_error))
                code = self._repair(
                    on_progress, intent, code, last_error, n, max_tries, images=images
                )
                if code is None:
                    break
                continue
            _emit_stage(on_progress, "validate", "ok", n)

            _emit_stage(on_progress, "build", "begin", n)
            res = run_code(code, export_dir=export_dir, export_scale=export_scale, config=self._cfg)
            if res.ok:
                # VLM critique: a valid solid may still be the wrong shape.
                #
                # Every attempt is reviewed, the last one included. Skipping the
                # last — which is what a `n < max_tries` gate does — leaves the
                # build actually delivered as the one build nobody looked at,
                # and makes "ran out of budget" indistinguishable from "was
                # never checked". On the last attempt there is no budget to
                # repair with, so the finding is reported and the part is
                # handed over with it attached.
                crit = (
                    self._try_critique(on_progress, intent, res, n)
                    if critique and self._should_critique(res)
                    else None
                )
                if crit is not None:
                    last_critique = {"matches": crit.matches, "attempt": n}
                    if not crit.matches:
                        last_error = "critique: " + crit.feedback
                        _emit_stage(on_progress, "critique", "error", n, last_error)
                        if n < max_tries:
                            self._record(
                                attempts, on_progress, Attempt(n, code, "critique", last_error)
                            )
                            code = self._repair(
                                on_progress,
                                intent,
                                code,
                                last_error,
                                n,
                                max_tries,
                                forced=True,
                                images=images,
                            )
                            continue
                    else:
                        _emit_stage(on_progress, "critique", "ok", n)
                # Deterministic geometry post-conditions: a failed
                # assertion is a semantic repair signal via the same channel as the
                # VLM critique. Optional and additive — only when budget remains and
                # at least one assertion was requested; never a hard stop.
                if assertions is not None and n < max_tries:
                    _emit_stage(on_progress, "assert", "begin", n)
                    report = evaluate_assertions(_signature(res), assertions)
                    signal = report.repair_signal()
                    if signal is not None:
                        last_error = "assertion: " + signal
                        _emit_stage(on_progress, "assert", "error", n, last_error)
                        self._record(attempts, on_progress, Attempt(n, code, "assert", last_error))
                        code = self._repair(
                            on_progress,
                            intent,
                            code,
                            last_error,
                            n,
                            max_tries,
                            forced=True,
                            images=images,
                        )
                        continue
                    _emit_stage(on_progress, "assert", "ok", n)
                _emit_stage(on_progress, "build", "ok", n)
                # Meshing/export ran inside the worker; artifacts now exist.
                _emit_stage(on_progress, "mesh", "ok", n)
                self._record(attempts, on_progress, Attempt(n, code, "execute", None))
                return GenerationResult(
                    ok=True,
                    intent=intent,
                    code=code,
                    volume=res.volume,
                    bbox=res.bbox,
                    step_path=res.step_path,
                    glb_path=res.glb_path,
                    stl_path=res.stl_path,
                    obj_path=res.obj_path,
                    parameters=extract_params(code),
                    attempts=attempts,
                    critique=last_critique,
                )
            last_error = "execution: " + (res.error or "unknown")
            _emit_stage(on_progress, "build", "error", n, last_error)
            self._record(attempts, on_progress, Attempt(n, code, "execute", last_error))
            code = self._repair(
                on_progress,
                intent,
                code,
                last_error,
                n,
                max_tries,
                context=res.repair_context,
                images=images,
            )
            if code is None:
                break

        return GenerationResult(
            ok=False,
            intent=intent,
            code=attempts[-1].code if attempts else None,
            error=last_error,
            attempts=attempts,
            critique=last_critique,
        )

    def run_candidates(
        self,
        intent: str,
        n: int | None = None,
        export_dir: str | None = None,
        assertions: GeometryAssertions | None = None,
        grounding: str | None = None,
        temperature: float | None = None,
        images: Sequence[ContentBlock] = (),
        on_reading: Callable[[str], None] | None = None,
    ) -> list[GenerationResult]:
        """Best-of-N fan-out (C1): run N *fresh* generations in parallel.

        Produces ``n`` candidate :class:`GenerationResult`s for a single fresh
        request by running ``n`` independent ``run`` calls concurrently on a thread
        pool. The LLM call is IO-bound and OCCT executes in subprocesses, so threads
        give real wall-clock parallelism: N candidates cost ~one run of time (you
        pay N× tokens + compute, not N× latency).

        Scope is fresh generation only — there is no ``prior_code`` parameter, since
        edit_model (low variance) and set_parameters (deterministic) are out of
        scope per the epic. Each candidate gets its OWN export dir (derived from
        ``export_dir`` with a per-candidate suffix) so their artifacts never collide.

        ``n`` defaults to ``forge_candidate_count``. When ``n <= 1`` this is a single
        normal ``run`` at the default temperature (no fan-out overhead, no forge
        temperature) — identical to calling :meth:`run` directly. Otherwise the
        ``temperature`` (defaulting to ``forge_temperature``) is applied to every
        candidate for diversity.

        Robustness: a candidate that raises does NOT sink the others — it is captured
        and surfaced as a failed ``GenerationResult`` (same shape ``run`` returns on
        failure). The returned list always has ``max(1, n)`` entries.

        C1 only provides this primitive; it is NOT wired into the live agent/chat
        path (that is gated behind C4) and does not pick a winner (C2).
        """
        count = self._cfg.forge_candidate_count if n is None else n
        if count <= 1:
            # No fan-out: a plain single run at the default temperature so behavior
            # matches the legacy path exactly (no forge temperature applied).
            return [
                self.run(
                    intent,
                    export_dir=export_dir,
                    assertions=assertions,
                    grounding=grounding,
                    images=images,
                    on_reading=on_reading,
                    critique=False,
                )
            ]

        temp = self._cfg.forge_temperature if temperature is None else temperature

        # A candidate does not critique, and both branches of this method agree
        # on that. The fan-out runs with no progress sink, so a candidate's
        # captures and verdict are thrown away the moment they are produced,
        # while the cost multiplies by the candidate count.
        #
        # **So a forge turn currently gets no render critique at all.** The
        # judge has a rung for exactly this — comparing candidates by vision —
        # but the live call site supplies it no critic, so that rung does not
        # fire either. Wiring it is where this signal belongs; until then this
        # is a deliberate absence rather than an oversight, and it is worth
        # knowing that turning forge on turns the reviewer off with it.

        def _one(idx: int) -> GenerationResult:
            cand_dir = _candidate_dir(export_dir, idx)
            try:
                return self.run(
                    intent,
                    export_dir=cand_dir,
                    assertions=assertions,
                    grounding=grounding,
                    temperature=temp,
                    images=images,
                    on_reading=on_reading,
                    critique=False,
                )
            except Exception as exc:  # isolate: one bad candidate must not sink others
                return GenerationResult(
                    ok=False,
                    intent=intent,
                    error=f"candidate {idx} raised: {exc!r}",
                )

        with ThreadPoolExecutor(max_workers=count) as pool:
            # map preserves input order, so results[i] is candidate i.
            return list(pool.map(_one, range(count)))

    def _record(self, attempts: list[Attempt], on_progress, attempt: Attempt) -> None:
        attempts.append(attempt)
        _emit(
            on_progress,
            {
                "event": "attempt",
                "n": attempt.n,
                "stage": attempt.stage,
                "ok": attempt.error is None,
                "error": attempt.error,
            },
        )

    def _should_critique(self, res) -> bool:
        # ``stl_path`` rather than ``glb_path``: it is the artifact the critic's
        # renderer can load, so gating on any other one lets a call through to a
        # file it cannot read.
        return bool(self._critic and self._cfg.vlm_critique_enabled and res.stl_path)

    def _try_critique(self, on_progress, intent: str, res, n: int):
        """The reviewer's verdict, or ``None`` where it could not be taken.

        The critique is an extra signal on a build that has already succeeded,
        so a model that cannot see — or a provider that cannot be reached — has
        to leave that build alone. Letting the exception out instead turns every
        successful turn into a failed one wherever the configured model is not
        vision-capable, which is the whole deployment now that this runs by
        default.

        Reported rather than swallowed. A reviewer that never ran looks exactly
        like one that always agreed, and that is the version of this failure
        nobody would notice.
        """
        _emit_stage(on_progress, "critique", "begin", n)
        try:
            crit = self._critic.critique(intent, res.stl_path)
            # Publishing sits inside the guard as well. It reads the verdict's
            # captures, so a critic composed outside this tree that returns
            # something shaped differently would otherwise raise here — past the
            # catch, and straight through the successful build this exists to
            # protect. Published before the caller branches, so the round that
            # settles the part is shown as well as the rounds that did not.
            _emit_critique(on_progress, n, crit)
            return crit
        except Exception as exc:  # noqa: BLE001 — additive signal, never fatal
            logger.warning("render critique unavailable, skipping: %s", exc, exc_info=True)
            _emit_stage(on_progress, "critique", "error", n, f"critique unavailable: {exc}")
            return None

    def _repair(
        self,
        on_progress,
        intent,
        code,
        error,
        n,
        max_tries,
        *,
        forced: bool = False,
        context=None,
        images: Sequence[ContentBlock] = (),
    ) -> str | None:
        """Ask the model to fix ``code``; return None if the budget is exhausted.

        ``forced`` is used by the critique path, which has already guaranteed
        ``n < max_tries`` before calling. ``context`` is the structured
        :class:`~cadless.worker.RepairContext` from an execution failure; validation/critique failures pass ``None``.
        """
        if not forced and n >= max_tries:
            return None
        _emit_stage(on_progress, "repair", "begin", n, error)
        repaired = self._gen.repair(intent, code, error, context, images=images)
        _emit_stage(on_progress, "repair", "ok", n)
        return repaired


def _candidate_dir(export_dir: str | None, idx: int) -> str | None:
    """Per-candidate export dir so fan-out artifacts never collide.

    ``None`` in (no export requested) -> ``None`` out. Otherwise nest a
    ``candidate-{idx}`` subdirectory under the requested export dir and create it.
    """
    if export_dir is None:
        return None
    cand = os.path.join(export_dir, f"candidate-{idx}")
    os.makedirs(cand, exist_ok=True)
    return cand


def _signature(res) -> GeometrySignature:
    """Build the assertion input from a successful ExecResult's geometry metrics."""
    return GeometrySignature(
        volume=res.volume,
        bbox=res.bbox,
        part_count=res.part_count,
        manifold=res.manifold,
        min_wall_thickness=res.min_wall_thickness,
    )


def _emit(on_progress, event: dict) -> None:
    """Invoke the optional progress callback, ignoring its return value."""
    if on_progress is not None:
        on_progress(event)


def _codegen_on_token(on_progress):
    """A token sink that re-emits each codegen delta as a ``codegen`` progress event.

    Returns ``None`` when there is no progress listener, so the generator keeps its
    one-shot ``complete()`` path (forge candidates, offline callers) —.
    """
    if on_progress is None:
        return None
    return lambda text: _emit(on_progress, {"event": "codegen", "text": text})


def _emit_stage(
    on_progress, phase: str, status: str, attempt: int, error: str | None = None
) -> None:
    """Emit a granular {"event": "stage", ...} lifecycle event."""
    event = {"event": "stage", "phase": phase, "status": status, "attempt": attempt}
    if error is not None:
        event["error"] = error
    _emit(on_progress, event)


def _emit_critique(on_progress, attempt: int, crit) -> None:
    """Emit what the reviewer saw and what it concluded, on a channel of its own.

    Not a ``stage`` event. That shape is phase/status/attempt/error and every
    stage emits it, so widening it to carry pictures would reach every emitter
    and every consumer for the sake of one — and stage events are collected and
    replayed after the tool settles, which is after this loop has finished. A
    capture is only worth showing while the round it belongs to is running.

    The bytes go out base64-encoded because every consumer of this stream ends
    at ``json.dumps``.
    """
    _emit(
        on_progress,
        {
            "event": "critique",
            "attempt": attempt,
            "matches": bool(crit.matches),
            "feedback": crit.feedback,
            "views": [
                {"name": name, "png_b64": base64.standard_b64encode(png).decode("ascii")}
                for name, png in crit.captures
            ],
        },
    )


def generate_cad(
    intent: str,
    export_dir: str | None = None,
    on_progress=None,
    prior_code: str | None = None,
    assertions: GeometryAssertions | None = None,
    export_scale: float = 1.0,
) -> GenerationResult:
    """Convenience entry point using a default pipeline on the configured provider."""
    return Pipeline().run(
        intent,
        export_dir=export_dir,
        on_progress=on_progress,
        prior_code=prior_code,
        assertions=assertions,
        export_scale=export_scale,
    )
