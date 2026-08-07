"""Auto-distill known-good versions into KB entries.

The ``/distill`` extraction action and the flywheel that drives it. Whenever a turn
settles ``ok+asserted`` (executed ok AND assertions passed or none specified — the
Phase A quality floor) we extract a reusable KB entry and persist it via the B2
store (:meth:`cadless.store.Store.add_kb_entry`). There is no user action: this
is the flywheel that grows the knowledge base from the system's own successes.

Two deliberate choices follow the work item:

* **Embed intent + structural signature, not raw code.** The retrieval vector is
  computed over the NL intent plus a structural *feature-tag signature* derived
  from the geometry metrics (Phase A) and lightweight tags scraped from the
  build123d code (primitives/operations used). The raw code is still *stored* on
  the entry (B2's shape has a code field) so it can be reused, but it is never the
  embedded text — embeddings should match on what the user *asked for* and the
  shape produced, not on incidental code formatting.
* **Best-effort side effect.** :func:`auto_distill` never raises. A distill
  failure (embedding error, DB hiccup) is logged and swallowed so it can never
  fail the user's turn.

``/distill`` is exposed as :func:`distill` — a plain callable the auto path uses.
There is no manual slash-command registry in the agent for it yet; the automatic
flywheel on turn settlement is the acceptance core.
"""

from __future__ import annotations

import logging
import re

from cadless.llm.provider import ChatProvider, EmbeddingsUnsupported
from cadless.scoped_store import AnyStore
from cadless.store import KBEntry

logger = logging.getLogger(__name__)

# build123d primitive/operation tokens we recognise in source. The value is the
# normalised tag; matching is case-insensitive on whole identifiers so a substring
# like ``Boxed`` does not match ``Box``.
_PRIMITIVES = {
    "Box": "box",
    "Cylinder": "cylinder",
    "Sphere": "sphere",
    "Cone": "cone",
    "Torus": "torus",
    "Wedge": "wedge",
    "Rectangle": "rectangle",
    "Circle": "circle",
    "Polygon": "polygon",
    "RegularPolygon": "regular_polygon",
    "Ellipse": "ellipse",
    "Text": "text",
}
_OPERATIONS = {
    "fillet": "fillet",
    "chamfer": "chamfer",
    "extrude": "extrude",
    "revolve": "revolve",
    "loft": "loft",
    "sweep": "sweep",
    "offset": "offset",
    "mirror": "mirror",
    "split": "split",
    "scale": "scale",
    "shell": "shell",
}


def _identifiers(code: str) -> set[str]:
    """Whole-word identifiers appearing in ``code`` (for primitive/op matching)."""
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code))


def feature_tags(code: str, metrics: dict | None = None) -> list[str]:
    """Derive a deterministic structural feature-tag signature for a result.

    Combines lightweight structural tags scraped from the build123d ``code``
    (which primitives and operations it uses) with geometry tags derived from the
    Phase A ``metrics`` signature (bounding-box size band, part count). The tags
    are returned sorted so the embedded signature text is stable for identical
    inputs. Tags are coarse on purpose: they describe *kind of shape*, not exact
    numbers, so semantically similar results cluster together for retrieval.
    """
    idents = _identifiers(code or "")
    tags: set[str] = set()
    for token, tag in _PRIMITIVES.items():
        if token in idents:
            tags.add(f"primitive:{tag}")
    for token, tag in _OPERATIONS.items():
        if token in idents:
            tags.add(f"op:{tag}")
    # Boolean operators used directly in the code: a name/closing-paren followed by
    # the operator and another name (matches both ``a - b`` and ``f() - b``).
    if re.search(r"[)\w]\s*-\s*[A-Za-z_]", code or ""):
        tags.add("op:subtract")
    if re.search(r"[)\w]\s*&\s*[A-Za-z_]", code or ""):
        tags.add("op:intersect")
    if re.search(r"\)\s*\+\s*[A-Za-z_(]", code or ""):
        tags.add("op:union")

    metrics = metrics or {}
    bbox = metrics.get("bbox")
    if bbox:
        tags.add(f"bbox:{_size_band(bbox)}")
    part_count = metrics.get("part_count")
    if part_count is not None:
        tags.add(f"parts:{part_count}")
    if metrics.get("manifold") is True:
        tags.add("manifold")
    return sorted(tags)


def _size_band(bbox) -> str:
    """Coarse size bucket for a bounding box's largest extent (mm)."""
    try:
        longest = max(float(x) for x in bbox)
    except (TypeError, ValueError):
        return "unknown"
    if longest < 10:
        return "tiny"
    if longest < 50:
        return "small"
    if longest < 200:
        return "medium"
    return "large"


def signature_text(intent: str, tags: list[str]) -> str:
    """The text fed to ``embed`` — the NL intent plus the feature-tag signature.

    The raw build123d code is intentionally excluded: retrieval should match on
    intent and structure, not on code formatting.
    """
    joined = " ".join(tags)
    return f"{intent.strip()}\nfeatures: {joined}".strip()


async def distill(
    store: AnyStore,
    provider: ChatProvider,
    *,
    project_id: int,
    version_id: int | None,
    intent: str,
    code: str,
    metrics: dict | None = None,
) -> KBEntry:
    """The ``/distill`` extraction action: write one KB entry for a known-good result.

    Builds the structural feature-tag signature, embeds *intent + signature* (never
    the raw code), and persists via :meth:`Store.add_kb_entry` with the raw code
    stored on the entry. Returns the created :class:`KBEntry`. Raises on failure —
    callers that need best-effort behaviour use :func:`auto_distill`.
    """
    metrics = metrics or {}
    tags = feature_tags(code, metrics)
    embedding = provider.embed(signature_text(intent, tags))
    params = metrics.get("parameters") or {}
    geometry_signature = {
        "bbox": metrics.get("bbox"),
        "volume": metrics.get("volume"),
        "feature_tags": tags,
    }
    provenance = {
        "project_id": project_id,
        "version_id": version_id,
        "metrics": {k: v for k, v in metrics.items() if k != "parameters"},
    }
    return await store.add_kb_entry(
        nl_intent=intent,
        code=code,
        embedding=embedding,
        params=params,
        geometry_signature=geometry_signature,
        provenance=provenance,
    )


async def auto_distill(
    store: AnyStore,
    provider: ChatProvider,
    *,
    project_id: int,
    version_id: int | None,
    intent: str,
    code: str | None,
    ok: bool,
    metrics: dict | None = None,
    assertions_failed: bool = False,
    require_assertions: bool = False,
) -> KBEntry | None:
    """Best-effort flywheel hook: distill a known-good turn, never failing the turn.

    Gate: the turn must have executed ``ok`` and (when ``require_assertions``) its
    assertions must not have failed. The minimal cut seeds from ok-executing
    versions alone, so ``require_assertions`` defaults to ``False`` — an ok turn
    with no assertions (the common chat case) still distills. Any error during
    extraction or persistence is logged and swallowed; this is a side effect and
    must never propagate to the caller. Returns the created entry, or ``None`` when
    the gate is not met or distillation failed.
    """
    if not ok or not code:
        return None
    if require_assertions and assertions_failed:
        return None
    try:
        return await distill(
            store,
            provider,
            project_id=project_id,
            version_id=version_id,
            intent=intent,
            code=code,
            metrics=metrics,
        )
    except EmbeddingsUnsupported:
        # Expected on providers with no embeddings API (e.g. anthropic): the KB flywheel
        # is additive (ADR-0002), so skip quietly instead of warning every turn.
        logger.info(
            "provider has no embeddings API; skipping KB distill for project %s version %s",
            project_id,
            version_id,
        )
        return None
    except Exception:  # noqa: BLE001  (best-effort side effect — never fail the turn)
        logger.warning(
            "auto-distill failed for project %s version %s; skipping",
            project_id,
            version_id,
            exc_info=True,
        )
        return None
