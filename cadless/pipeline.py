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
      interpret -> generate|refine ->
      (validate -> build -> mesh [-> critique] [-> assembly] [-> assert]
       [-> guide])*
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
from pathlib import Path

from cadless.assembly_check import evaluate_assembly
from cadless.assertions import (
    GeometryAssertions,
    GeometrySignature,
    evaluate_assertions,
)
from cadless.config import Settings, settings
from cadless.exporters import exported_parts
from cadless.llm.types import ContentBlock
from cadless.params import extract_params
from cadless.printer_profile import AssemblySpec
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
    "assembly",
    "assert",
    "guide",
    "repair",
)


@dataclass
class Attempt:
    n: int
    code: str
    stage: str  # "validate" | "execute" | "critique" | "assembly" | "assert"
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
    #: The assembly check of the build this result carries, or ``None`` where the
    #: turn did not ask for one. ``{"ok", "order", "attempt", "measured"}``, plus
    #: ``failures``, ``unchecked``, ``joints`` and ``releases`` once something was
    #: measured.
    #:
    #: ``joints`` is one sorted neighbour list per part, and ``releases`` one unit
    #: heading per part -- the way the order search took that part out -- with an
    #: empty entry for the part left standing, which nothing had to be freed from.
    #: Both are indexed like ``order``. Every value here is JSON-safe on purpose:
    #: this dict is serialised onward whole, and a shape ``json.dumps`` retypes
    #: silently would arrive on the far side as something else.
    #:
    #: ``measured`` is false when the turn asked but nothing came back — the model
    #: produced a single solid, or the worker could not split the shape. ``ok`` is
    #: then ``None`` rather than ``True``: not checked is not the same as passed,
    #: and without this the result is indistinguishable from a turn that never
    #: asked.
    #:
    #: ``order`` is the sequence the parts go together in, **indexed from zero to
    #: match the exported ``model_p{i}`` files** rather than the part numbers in
    #: ``failures``, which count from one because a person reads them. Whichever
    #: the assembly guide shows, it converts here rather than assuming.
    #:
    #: Unlike :attr:`critique` this may carry its findings' text: those are this
    #: engine's own sentences about geometry it measured, not a vision model's
    #: prose written from a prompt holding the user's.
    assembly: dict | None = None
    #: How the parts go together, as a reader is shown it: ``{"parts", "steps",
    #: "frames"}``, or ``None`` where there was no assembly to describe. Written
    #: only once the split has been accepted -- a guide to a build about to be
    #: refused describes an assembly nobody receives.
    #:
    #: ``frames`` counts the drawings stored beside the parts, which a reader
    #: addresses by index. Zero is ordinary rather than a failure: a build whose
    #: headings were never measured gets its written steps and no pictures.
    guide: dict | None = None

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
        guide_writer=None,
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
        # Optional GuideWriter. Without one the guide is still written, from the
        # order and the joints alone -- what a writer adds is names for the parts,
        # and the sentences are the engine's either way.
        self._guide_writer = guide_writer

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

    @property
    def critic(self):
        """The reviewer this pipeline would use, or ``None`` where it would not.

        Gated on the setting as well as on injection, because the forge judge's
        rung asks only whether it was given one. Handing it a reviewer this
        pipeline would not itself run would leave the setting off for one path
        and on for the other, and the one it was on for is the expensive one.
        """
        return self._critic if self._cfg.vlm_critique_enabled else None

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
        assembly: AssemblySpec | None = None,
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
        # Which repair rounds of this turn may redesign the split. A fresh run
        # has no split to preserve, so every one of its repairs is entitled; an
        # edit acts on a model already cut, so its repairs are not -- they are
        # fixing what the edit produced, not answering how the thing comes apart.
        # Computed once here rather than at each repair site so they cannot drift
        # apart, and overridden at exactly one: the assembly check, where the
        # split is what failed and a redesign is the answer.
        #
        # Truthiness rather than `is None`, to read `prior_code` the same way the
        # lines around it do. An empty string takes the fresh-generation branch
        # below, so under identity it would be handed a fresh design on the first
        # round and edit framing on every repair of it -- one turn arguing with
        # itself. No caller reaches it today; the point is that every reading of
        # `prior_code` in this loop agrees whoever does.
        may_resplit = not prior_code
        _emit(
            on_progress, {"event": "start", "intent": intent, "max_tries": max_tries, "mode": mode}
        )
        _emit_stage(on_progress, "interpret", "ok", 0)

        _emit_stage(on_progress, mode, "begin", 1)
        if prior_code:
            # Refine is out of streaming scope: keep the one-shot call.
            code = self._gen.refine(
                intent, prior_code, images=images, on_reading=on_reading, assembly=assembly
            )
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
                assembly=assembly,
                **extra,
            )
        _emit_stage(on_progress, mode, "ok", 1)
        last_error = "no attempts ran"
        last_critique: dict | None = None
        last_assembly: dict | None = None

        for n in range(1, max_tries + 1):
            # Cleared here rather than beside the critique, so that every way an
            # attempt can end reaches it — a build that fails to execute never
            # gets as far as a review, and carrying the previous attempt's
            # verdict past it attaches a pass to a build that is not the one
            # being returned. The assembly verdict is cleared here for exactly
            # the same reason and must stay beside it.
            last_critique = None
            last_assembly = None
            _emit_stage(on_progress, "validate", "begin", n)
            verdict = validate_code(code)
            if not verdict.ok:
                last_error = "validation: " + "; ".join(verdict.reasons)
                _emit_stage(on_progress, "validate", "error", n, last_error)
                self._record(attempts, on_progress, Attempt(n, code, "validate", last_error))
                code = self._repair(
                    on_progress,
                    intent,
                    code,
                    last_error,
                    n,
                    max_tries,
                    may_resplit=may_resplit,
                    images=images,
                    assembly=assembly,
                )
                if code is None:
                    break
                continue
            _emit_stage(on_progress, "validate", "ok", n)

            _emit_stage(on_progress, "build", "begin", n)
            res = run_code(
                code,
                export_dir=export_dir,
                export_scale=export_scale,
                check_assembly=assembly is not None,
                config=self._cfg,
            )
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
                                may_resplit=may_resplit,
                                forced=True,
                                images=images,
                                assembly=assembly,
                            )
                            continue
                    else:
                        _emit_stage(on_progress, "critique", "ok", n)
                # Does the split actually go together? Only on a turn that asked
                # for an assembly, and only once the model produced more than one
                # part -- a lone part against the build volume is print_fit's
                # question, not this one.
                #
                # Runs on every attempt including the last, for the reason the
                # critique does: a `n < max_tries` gate leaves the build actually
                # delivered as the one nobody checked. Unlike the critique it then
                # refuses that build rather than handing it over with the finding
                # attached -- a vision model disagreeing about a shape is an
                # opinion, whereas two parts occupying the same space is not, and
                # printing it wastes hours and material.
                if assembly is not None and res.assembly is None:
                    # The turn asked and nothing came back. Ordinarily that means
                    # the model produced one solid, which is not an assembly and
                    # is print_fit's question; it also covers a worker too old to
                    # know the flag, and a shape whose solids could not be split.
                    # Recorded rather than passed over silently, so a reader can
                    # tell this from a turn that never asked -- both of which
                    # otherwise leave the result carrying nothing at all.
                    last_assembly = {"ok": None, "order": None, "attempt": n, "measured": False}
                if assembly is not None and res.assembly is not None:
                    _emit_stage(on_progress, "assembly", "begin", n)
                    fit = evaluate_assembly(res.assembly, assembly)
                    last_assembly = {
                        "ok": fit.ok,
                        "order": fit.order,
                        "attempt": n,
                        "measured": True,
                        "failures": list(fit.failures),
                        "unchecked": list(fit.unchecked),
                        "joints": fit.joints,
                        # Off the measurements rather than the verdict: which way
                        # a part came out is something measured, not something
                        # ruled, and the report carries rulings.
                        "releases": list(res.assembly.releases),
                    }
                    signal = fit.repair_signal()
                    if signal is not None:
                        last_error = "assembly: " + signal
                        _emit_stage(on_progress, "assembly", "error", n, last_error)
                        self._record(
                            attempts, on_progress, Attempt(n, code, "assembly", last_error)
                        )
                        if n >= max_tries:
                            break
                        code = self._repair(
                            on_progress,
                            intent,
                            code,
                            last_error,
                            n,
                            max_tries,
                            # The one site that overrides the turn's answer: the
                            # split is what failed here, so this round is the one
                            # a redesign answers -- on an edit as much as on a
                            # fresh run. Withholding the brief would leave it
                            # failing the same check until the budget ran out.
                            may_resplit=True,
                            forced=True,
                            images=images,
                            assembly=assembly,
                        )
                        continue
                    _emit_stage(on_progress, "assembly", "ok", n)
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
                            # Inherits rather than overriding, and one assertion
                            # class argues for the other reading: an expected part
                            # count is answered only by cutting the model
                            # differently. Left inheriting because on the paths
                            # that carry a spec today the turn's answer is already
                            # the right one, so an override here would be
                            # behaviour nothing in this tree can exercise.
                            # Revisit when a caller wires an assembly spec and
                            # geometry assertions together.
                            may_resplit=may_resplit,
                            forced=True,
                            images=images,
                            assembly=assembly,
                        )
                        continue
                    _emit_stage(on_progress, "assert", "ok", n)
                _emit_stage(on_progress, "build", "ok", n)
                # Meshing/export ran inside the worker; artifacts now exist.
                _emit_stage(on_progress, "mesh", "ok", n)
                # Last, and only on a split that was accepted. A guide written
                # earlier would describe an assembly a later check still refuses.
                guide = self._guide(on_progress, intent, code, last_assembly, export_dir, n)
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
                    assembly=last_assembly,
                    guide=guide,
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
                may_resplit=may_resplit,
                context=res.repair_context,
                images=images,
                assembly=assembly,
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
            assembly=last_assembly,
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
        assembly: AssemblySpec | None = None,
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
                    assembly=assembly,
                )
            ]

        temp = self._cfg.forge_temperature if temperature is None else temperature

        # A candidate does not critique, and both branches of this method agree
        # on that. The fan-out runs with no progress sink, so a candidate's
        # captures and verdict are thrown away the moment they are produced,
        # while the cost multiplies by the candidate count.
        #
        # The signal reaches a forge turn through the judge instead, whose own
        # rung compares candidates by vision and is handed this pipeline's
        # reviewer, gated on the same setting. So it is not absent from the
        # turn, only from the attempt: a candidate is never repaired against a
        # verdict, and the review happens once, on the field, where there is
        # somebody to show it to. It runs when more than one candidate survives
        # the hard filter — a race settled before then needs no tie broken.

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
                    assembly=assembly,
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

        What it is shown is the whole build rather than the scalar path, which on
        a multi-part build names one piece. Parts the renderer cannot read raise
        and land in the guard below, which is a review that did not happen --
        never one that passed.
        """
        _emit_stage(on_progress, "critique", "begin", n)
        try:
            crit = self._critic.critique(intent, critique_subject(res.stl_path))
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

    def _guide(self, on_progress, intent: str, code: str, verdict, export_dir, n: int):
        """How the accepted parts go together, or ``None`` where there is no assembly.

        Never fatal, and for a sharper reason than the critique's: by this point
        the parts are built, measured and accepted, so a turn that failed here
        would throw away a finished capability over its description.

        The drawings are written beside the exports rather than returned, because
        this has no store of its own to put them in: the export directory is the
        hand-off, and what it holds when a build finishes is what is taken up.
        That is also why the sweep below runs before anything else and on every
        accepted build, rather than where the drawing happens: callers reuse one
        export directory across the builds of a turn, so a build that draws
        nothing would otherwise leave the previous one's frames to be taken up as
        its own -- describing one model with pictures of another.
        """
        _clear_guide_frames(export_dir)
        if not (verdict and verdict.get("ok") and verdict.get("order")):
            return None
        order = verdict["order"]
        if len(order) < 2:
            return None

        try:
            _emit_stage(on_progress, "guide", "begin", n)
            from cadless.guide_writer import plain_guide  # noqa: PLC0415

            joints = verdict.get("joints") or []
            written = (
                self._guide_writer.write(intent, code, order, joints)
                if self._guide_writer
                else plain_guide(order, joints)
            )
            frames = self._draw_guide(export_dir, order, verdict.get("releases") or [])
            _emit_stage(on_progress, "guide", "ok", n)
            return {**written.as_payload(), "frames": frames}
        except Exception as exc:  # noqa: BLE001 — a description, never the build
            logger.warning("assembly guide unavailable, skipping: %s", exc, exc_info=True)
            # Returning nothing has to mean nothing on disk, here as well as one
            # level down: frames can already be written by the time something
            # after them raises, and left there they are filed against a version
            # whose guide is absent.
            _clear_guide_frames(export_dir)
            _emit_stage(on_progress, "guide", "error", n, f"guide unavailable: {exc}")
            return None

    @staticmethod
    def _draw_guide(export_dir, order, releases) -> int:
        """Write the guide's frames beside the exports; how many were drawn.

        Zero is an ordinary answer, and never an error: a turn with no export
        directory, parts in a format the renderer cannot read, or a build whose
        headings were never measured. Failing to draw must not take the written
        steps with it -- they stand on the order alone, which is measured whether
        or not there is anything to draw from.
        """
        if not export_dir:
            return 0
        directory = Path(export_dir)
        try:
            from cadless.assembly_guide import guide_frames  # noqa: PLC0415
            from cadless.catalog.thumbnail import load_mesh  # noqa: PLC0415

            # Through the shared reader rather than by spelling the part names
            # here: a second spelling drifts from the writer's quietly, and a
            # guide drawn from the wrong files describes a model nobody built.
            # A count that disagrees with the order draws nothing, which
            # ``guide_frames`` already treats as an ordinary answer.
            meshes = [load_mesh(part) for part in exported_parts(directory, "stl")]
            drawn = guide_frames(meshes, order, releases)
            # Writing is inside the guard too. Reading and drawing are not the
            # only halves that can fail -- a full disk gives up part-way through
            # the set, and leaving that half-written would both lose the steps
            # and file frames no guide refers to.
            for position, (_, png) in enumerate(drawn):
                (directory / f"guide_f{position}.png").write_bytes(png)
        except Exception as exc:  # noqa: BLE001 — a picture, never the words
            logger.warning(
                "assembly guide: drawing failed, keeping the steps: %s", exc, exc_info=True
            )
            _clear_guide_frames(export_dir)
            return 0
        return len(drawn)

    def _repair(
        self,
        on_progress,
        intent,
        code,
        error,
        n,
        max_tries,
        *,
        may_resplit: bool,
        forced: bool = False,
        context=None,
        images: Sequence[ContentBlock] = (),
        assembly: AssemblySpec | None = None,
    ) -> str | None:
        """Ask the model to fix ``code``; return None if the budget is exhausted.

        ``forced`` is used by the critique path, which has already guaranteed
        ``n < max_tries`` before calling. ``context`` is the structured
        :class:`~cadless.worker.RepairContext` from an execution failure; validation/critique failures pass ``None``.

        ``may_resplit`` has no default on purpose. The generator's own parameter
        does -- it is a public surface with callers that predate the question --
        but here the answer depends on which stage failed, which only the call
        site knows, so a default would let a new repair site inherit an answer it
        never gave and be wrong in whichever direction the default happened to
        point.
        """
        if not forced and n >= max_tries:
            return None
        _emit_stage(on_progress, "repair", "begin", n, error)
        repaired = self._gen.repair(
            intent, code, error, context, images=images, assembly=assembly, may_resplit=may_resplit
        )
        _emit_stage(on_progress, "repair", "ok", n)
        return repaired


def critique_subject(stl_path: str) -> str | list[str]:
    """What the reviewer is shown for a build: the whole of it.

    Public because the forge judge reviews candidates through the same rung and
    must resolve them the same way. One definition rather than the same rule
    spelled twice, for the reason the reader it calls gives.

    ``stl_path`` is the *first* part of a multi-part build -- for one solid that
    is the model, and for an assembly it is a fragment. Reviewing that alone asks
    about a piece while the verdict is filed against the model, so a wrong split
    can pass and a sound one can be repaired against a mismatch nobody built.

    Multiplicity is read off the directory rather than carried in a second field
    beside the scalar, which would be a second place for the two to disagree.
    Fewer than two parts hands the scalar straight back, so a one-solid build
    takes the route it has always taken and a directory that answers nothing
    degrades to it rather than raising. A forge candidate needs no special case:
    its parts sit beside its own scalar.

    This leans on the export step clearing both namings before it writes: a
    directory holding one build's ``model`` beside another's ``model_p*`` would
    answer with the wrong set, and the answer would be a quiet fall back to the
    fragment rather than an error. Whoever changes that clearing owes this a
    look.
    """
    found = exported_parts(Path(stl_path).parent, "stl")
    if len(found) < 2:
        return stl_path
    return [str(part) for part in found]


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


def _clear_guide_frames(export_dir) -> None:
    """Drop any guide frames already in the export directory.

    Never raises: this runs to keep a later build from taking up an earlier one's
    pictures, and failing to tidy must not fail the build that is tidying. The
    sibling sweep in the worker child takes the opposite stance and lets its
    failures out, which is right there and wrong here -- that one runs before a
    build is accepted, so abandoning it loudly costs nothing already earned.

    One file that will not go does not stop the rest: a partial sweep leaves
    fewer frames to be taken up by mistake than an abandoned one.
    """
    if not export_dir:
        return
    try:
        stale = list(Path(export_dir).glob("guide_f*.png"))
    except Exception as exc:  # noqa: BLE001 — tidying, never the build
        logger.warning("assembly guide: could not list previous frames: %s", exc, exc_info=True)
        return
    for frame in stale:
        try:
            frame.unlink()
        except OSError as exc:
            logger.warning("assembly guide: could not clear %s: %s", frame.name, exc)


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
