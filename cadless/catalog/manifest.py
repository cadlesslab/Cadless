"""House Catalog manifest schema + parsing (Phase 1).

JSON (not YAML) is used for the manifest so there is no extra dependency. A
``manifest.json`` describes one house and the ordered ladder of steps that build
it; each step references a cumulative build123d script and (after baking) its
geometry + pre-rendered artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from cadless.catalog.domains import find_domain


class StepGeometry(BaseModel):
    """Geometry metrics captured for a step (filled by the bake helper)."""

    volume: float | None = None
    bbox: tuple[float, float, float] | None = None


class StepTranscript(BaseModel):
    """A realistic chat turn for one step (re-authoring).

    The ``user_prompt`` is what a user would type to request this step and the
    ``assistant_message`` is the assistant's natural-language reply. When present,
    the loader replays these verbatim instead of synthesizing a placeholder turn.
    """

    user_prompt: str
    assistant_message: str


class CatalogStep(BaseModel):
    """One rung of the instruction ladder."""

    index: int  # 1-based position in the ladder
    instruction: str  # the natural-language instruction for this step
    code: str  # path to the step's build123d script, relative to the house dir
    geometry: StepGeometry = Field(default_factory=StepGeometry)
    artifacts: dict[str, str] = Field(default_factory=dict)  # kind -> relative path
    assertions: dict = Field(default_factory=dict)  # e.g. {"volume_tol": 0.05}
    # Multi-body convention (#40): the number of disjoint solids this step's
    # ``result`` deliberately produces (a build123d Compound of positioned
    # solids). Absent means the conventional default of 1 — undeclared steps
    # keep their exact pre-#40 eval output, while a declared value makes the
    # authoring pipeline's gates assert the body count.
    expected_bodies: int | None = Field(default=None, ge=1)
    # Re-authoring: a realistic conversation turn for this step. ``None``
    # on legacy manifests so the loader falls back to its placeholder behavior.
    transcript: StepTranscript | None = None


class CatalogManifest(BaseModel):
    """A whole house: metadata + the ordered steps that build it."""

    id: str
    name: str
    # Re-authoring: a kebab-case slug for the descriptive name, and
    # whether the stored transcript was geometrically verified to reproduce the
    # final code (vs a best-effort rewrite over the original code). Both ``None``
    # on legacy manifests, which the loader/reader treat as absent.
    slug: str | None = None
    verified: bool | None = None
    domain: str = "house"
    source: str | None = None
    storey_height: float | None = None
    # Discovery metadata (#21): an optional taxonomy category (e.g. "bungalow"),
    # free-form search tags, and a one-line description. All optional so every
    # existing manifest stays valid.
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    # Path (relative to the item dir) of the baked item thumbnail PNG, filled by
    # the bake helper from the final step's mesh; ``None`` until (re)baked.
    thumbnail: str | None = None
    # The content version this item was last published under. Recorded here
    # because a publisher cannot be asked what the last one was, and without it
    # every upload from a fresh clone starts over at 1.0.0. ``None`` on an item
    # that has never been published. Read by this build, written by whichever
    # one publishes.
    content_version: str | None = None
    steps: list[CatalogStep]


def load_manifest(house_dir: Path) -> CatalogManifest:
    """Read + validate ``<house_dir>/manifest.json``.

    Steps are sorted by ``index`` and validated to be contiguous from 1; the
    ``domain`` must be registered in the domain registry; every referenced
    ``code`` file must exist. Raises ``ValueError`` otherwise.
    """
    house_dir = Path(house_dir)
    manifest_path = house_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"no manifest.json in {house_dir}")
    manifest = CatalogManifest.model_validate(json.loads(manifest_path.read_text()))
    if find_domain(manifest.domain) is None:
        raise ValueError(
            f"unregistered catalog domain {manifest.domain!r} in {manifest_path} "
            "(see cadless.catalog.domains)"
        )
    manifest.steps.sort(key=lambda s: s.index)
    actual = [s.index for s in manifest.steps]
    expected = list(range(1, len(manifest.steps) + 1))
    if actual != expected:
        raise ValueError(f"step indices must be contiguous from 1, got {actual}")
    for step in manifest.steps:
        if not (house_dir / step.code).exists():
            raise ValueError(f"step {step.index} code file missing: {house_dir / step.code}")
    return manifest


def read_source_json(item_dir: Path) -> dict | None:
    """The item's ``source.json`` provenance record, or ``None`` (#23).

    Single reader shared by the loader (ledger surfacing), the
    pipeline (idempotency skip), and the dedup index (stored fingerprints),
    so all three agree on what counts as a readable provenance record.
    """
    try:
        data = json.loads((Path(item_dir) / "source.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def discover_houses(catalog_dir: Path) -> list[str]:
    """Sorted names of sub-directories of ``catalog_dir`` that contain a manifest."""
    catalog_dir = Path(catalog_dir)
    if not catalog_dir.exists():
        return []
    return sorted(
        p.name for p in catalog_dir.iterdir() if p.is_dir() and (p / "manifest.json").exists()
    )
