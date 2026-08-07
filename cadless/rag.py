"""Dynamic RAG retrieval into the codegen prompt.

At generation time we ground the model on the system's own past successes. The
request (NL intent + structural feature signature) is embedded **the same way B3
embedded KB entries** — by reusing :func:`cadless.distill.feature_tags` and
:func:`cadless.distill.signature_text` — so retrieval similarity is meaningful
(query and corpus live in the same embedding space). We then retrieve the top-k
known-good entries via *hybrid* retrieval:

* **intent-embedding cosine similarity** — B2's brute-force vector index
  (:meth:`Store.query_kb_by_vector`); and
* **a feature-tag filter** — entries are preferred (or, when configured, required)
  to share at least one structural feature tag with the request.

A **similarity floor** guards the prompt: if the best candidate scores below the
floor, nothing is injected, so a weak query degrades to today's no-retrieval
behaviour rather than poisoning the prompt with noise.

Ranking is **similarity-first, success-weighted**: the blended score is the cosine
similarity plus a small, bounded bonus derived from the entry's provenance success
signal (passed-assertions / kept / branched-from counts). Similarity dominates;
the success signal only breaks near-ties — see :func:`blended_score`.

The retained entries are rendered as a grounding block framed explicitly as
*suggestions to adapt, not templates to copy* and woven into the codegen user
message. The whole feature is **purely additive**: an empty KB (or all candidates
below the floor) produces exactly the legacy prompt — see the tests.
"""

from __future__ import annotations

import math

from cadless.config import Settings, settings
from cadless.distill import feature_tags, signature_text
from cadless.llm.provider import ChatProvider, EmbeddingsUnsupported
from cadless.scoped_store import AnyStore
from cadless.store import KBEntry

# Provenance metric fields that signal a "good" entry, with their blend weights.
# These mirror the work item's success signals (passed-assertions / kept /
# branched-from). Missing fields contribute nothing — provenance from B3 may not
# track them yet, so the score degrades gracefully to 0.0 (similarity-only).
_SUCCESS_FIELDS = {
    "passed_assertions": 1.0,
    "kept": 1.0,
    "branched_from": 1.0,
}

# How much of the code body to include per grounding example. Bounded so a large
# KB script cannot blow up the prompt.
_CODE_CHAR_BUDGET = 1200


def success_score(provenance: dict) -> float:
    """A bounded ``[0, 1)`` success signal derived from an entry's provenance.

    Pure and unit-testable. Reads the provenance ``metrics`` for the success
    counters (passed-assertions / kept / branched-from); each present, positive
    counter adds a saturating contribution so more successes => higher score
    without ever reaching 1.0. Absent counters contribute nothing, so an entry
    whose provenance does not yet track these fields scores ``0.0`` and ranking
    falls back to pure similarity. The saturation (``1 - exp(-x)``) keeps the
    signal a tie-breaker rather than letting one wildly successful entry dominate.
    """
    metrics = (provenance or {}).get("metrics") or {}
    weighted = 0.0
    for field, weight in _SUCCESS_FIELDS.items():
        value = metrics.get(field)
        if isinstance(value, (int, float)) and value > 0:
            weighted += weight * float(value)
    if weighted <= 0.0:
        return 0.0
    return 1.0 - math.exp(-weighted)


# Small additive bonus for a candidate that shares >=1 feature tag with the
# request, applied in the default "prefer overlap" mode (when overlap is not
# hard-required). Kept tiny so it, like the success signal, only breaks near-ties
# and never overrides a materially higher similarity.
_TAG_OVERLAP_BONUS = 0.05


def blended_score(
    similarity: float, provenance: dict, *, success_weight: float, tag_overlap: bool = False
) -> float:
    """Similarity-first, success-weighted blend used to rank candidates.

    The score is ``similarity + success_weight * success_score(provenance)``,
    plus a small fixed bonus when ``tag_overlap`` (the candidate shares >=1 feature
    tag with the request — the "prefer overlap" half of the hybrid filter). Because
    ``success_score`` is in ``[0, 1)``, ``success_weight`` is small (config default
    0.2) and the tag bonus is tiny, these terms can only nudge ranking — a
    materially higher similarity always wins, while at near-equal similarity the
    higher-success / tag-overlapping entry sorts first. Pure: no I/O, unit-testable.
    """
    score = similarity + success_weight * success_score(provenance)
    if tag_overlap:
        score += _TAG_OVERLAP_BONUS
    return score


def format_grounding(entries: list[KBEntry]) -> str:
    """Render retained KB entries as an adapt-not-copy grounding block.

    Returns the empty string for an empty list (the additive no-op). Each entry
    contributes its NL intent, a bounded slice of its build123d code, and its
    structured params when present — enough to be useful without unbounded prompt
    growth. The framing tells the model these are *suggestions to adapt, not
    templates to copy*.
    """
    if not entries:
        return ""
    lines = [
        "Here are known-good build123d examples from past successful builds, "
        "retrieved as grounding. They are suggestions to adapt, NOT templates to "
        "copy verbatim — reuse the patterns and techniques that fit, and ignore "
        "anything that does not match the current request.",
        "",
    ]
    for i, entry in enumerate(entries, start=1):
        lines.append(f"Example {i} — intent: {entry.nl_intent.strip()}")
        if entry.params:
            lines.append(f"params: {entry.params}")
        code = entry.code.strip()
        if len(code) > _CODE_CHAR_BUDGET:
            code = code[:_CODE_CHAR_BUDGET].rstrip() + "\n# ... (truncated)"
        lines.append("```python")
        lines.append(code)
        lines.append("```")
        lines.append("")
    return "\n".join(lines).strip()


async def retrieve_grounding(
    store: AnyStore,
    provider: ChatProvider,
    *,
    intent: str,
    code: str = "",
    metrics: dict | None = None,
    top_k: int | None = None,
    floor: float | None = None,
    success_weight: float | None = None,
    require_tag_overlap: bool | None = None,
    config: Settings | None = None,
) -> str:
    """Embed the request, hybrid-retrieve top-k known-good entries, render grounding.

    The request is embedded with the **same** construction B3 used to embed entries
    (``signature_text(intent, feature_tags(code, metrics))``) so the query vector
    lives in the corpus's embedding space. Candidates come from B2's
    :meth:`Store.query_kb_by_vector` (cosine top-k); we then apply the feature-tag
    filter and the similarity floor, re-rank by :func:`blended_score`
    (similarity-first, success-weighted), and render the top-k via
    :func:`format_grounding`.

    Returns the grounding block, or ``""`` when the KB is empty or no candidate
    clears the floor (and, when ``require_tag_overlap``, the tag filter) — i.e.
    the additive no-retrieval path. Defaults for ``top_k``/``floor``/
    ``success_weight``/``require_tag_overlap`` come from config.
    """
    cfg = config or settings
    top_k = cfg.rag_top_k if top_k is None else top_k
    floor = cfg.rag_similarity_floor if floor is None else floor
    success_weight = cfg.rag_success_weight if success_weight is None else success_weight
    if require_tag_overlap is None:
        require_tag_overlap = cfg.rag_require_tag_overlap
    if top_k <= 0:
        return ""

    query_tags = set(feature_tags(code, metrics or {}))
    try:
        query_vec = provider.embed(signature_text(intent, sorted(query_tags)))
    except EmbeddingsUnsupported:
        # No embeddings on this provider (e.g. anthropic): the additive no-retrieval
        # path — exactly the legacy prompt, not an error (ADR-0002).
        return ""

    # Pull a generous candidate pool (similarity-ordered) so the tag filter and
    # success re-rank have room to work before we trim to top_k.
    pool = await store.query_kb_by_vector(query_vec, top_k=max(top_k * 4, top_k))

    scored: list[tuple[KBEntry, float]] = []
    for entry, similarity in pool:
        if similarity < floor:
            continue
        entry_tags = set(entry.geometry_signature.get("feature_tags") or [])
        overlaps = bool(query_tags & entry_tags)
        if require_tag_overlap and not overlaps:
            continue
        scored.append(
            (
                entry,
                blended_score(
                    similarity,
                    entry.provenance,
                    success_weight=success_weight,
                    tag_overlap=overlaps,
                ),
            )
        )

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return format_grounding([entry for entry, _ in scored[:top_k]])
