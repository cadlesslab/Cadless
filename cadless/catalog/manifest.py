"""House Catalog manifest schema + parsing (Phase 1).

JSON (not YAML) is used for the manifest so there is no extra dependency. A
``manifest.json`` describes one house and the ordered ladder of steps that build
it; each step references a cumulative build123d script and (after baking) its
geometry + pre-rendered artifacts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from cadless.artifact_kinds import ASSEMBLY_FILES, PART_KINDS
from cadless.assembly_tree import MAX_PARTS, NAME_MAX, readable
from cadless.catalog.domains import find_domain
from cadless.catalog.pack import PackError, safe_entry_name

# An artifact's kind becomes the extension of the file the loader writes, so the
# shape this allows is the shape a file extension actually is.
_KIND = r"[A-Za-z0-9_-]{1,32}"
ARTIFACT_KIND_SHAPE = re.compile(_KIND + r"\Z")
# A later part of a kind is keyed `<kind>:<part>`, counting from 1: the first part
# is the bare kind, so each part has exactly one spelling and two keys cannot name
# the same part.
PART_KEY_SEPARATOR = ":"
_PART_KEY = re.compile(rf"(?P<kind>{_KIND}){PART_KEY_SEPARATOR}(?P<part>[1-9][0-9]*)\Z")


def parse_artifact_key(key: str) -> tuple[str, int]:
    """The (kind, part) a step's artifact key names.

    A key with no separator is the kind itself and its first part, as every key
    was before a step could carry several files of one kind. A key with one has
    to be a kind and a part number from 1 exactly, or it is refused.
    """
    if PART_KEY_SEPARATOR not in key:
        return key, 0
    match = _PART_KEY.match(key)
    if match is None:
        raise ValueError(f"artifact key {key!r} is not <kind> or <kind>:<part number from 1>")
    return match["kind"], int(match["part"])


class StepGeometry(BaseModel):
    """Geometry metrics captured for a step (filled by the bake helper)."""

    volume: float | None = None
    bbox: tuple[float, float, float] | None = None

    def in_millimetres(self, mm_per_unit: float) -> StepGeometry:
        """These metrics, recorded in a domain's authoring units, in millimetres:
        lengths times ``mm_per_unit``, the volume times its cube."""
        return StepGeometry(
            volume=None if self.volume is None else self.volume * mm_per_unit**3,
            bbox=None if self.bbox is None else tuple(side * mm_per_unit for side in self.bbox),
        )

    def in_authoring_units(self, mm_per_unit: float) -> StepGeometry:
        """These metrics, in millimetres as a version records them, in a domain's
        authoring units, as a manifest records them: the inverse of
        :meth:`in_millimetres`, for whatever writes a version's metrics into an
        item. Divided rather than multiplied by the reciprocal, so the decimals an
        item was authored with come back as they were."""
        return StepGeometry(
            volume=None if self.volume is None else self.volume / mm_per_unit**3,
            bbox=None if self.bbox is None else tuple(side / mm_per_unit for side in self.bbox),
        )


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
    code: str  # path to the step's build123d script, relative to and inside the item dir
    geometry: StepGeometry = Field(default_factory=StepGeometry)
    # Key -> relative path, inside the item. The key is the kind for the first
    # file of a kind and `<kind>:<part>` for each later one (see parse_artifact_key).
    artifacts: dict[str, str] = Field(default_factory=dict)
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
    # The step's parts by the names its build's tree gives them, in part order.
    # ``None`` on a step baked before parts had names, which loads as it always
    # did.
    parts: list[str] | None = None

    @field_validator("artifacts")
    @classmethod
    def _each_key_names_one_part(cls, artifacts: dict[str, str]) -> dict[str, str]:
        """Refuse keys the loader could not write a file for, or file as they say.

        The kind becomes the extension of the copy the loader writes, so it is
        held to the shape of one here, for every manifest whichever way it
        arrived. The store numbers a kind's files in the order they are added, so
        part n can only be filed as part n when every number below it is named
        too; a kind whose numbers skip one is refused before anything is copied.
        """
        parts: dict[str, list[int]] = {}
        for key in artifacts:
            kind, part = parse_artifact_key(key)
            if not ARTIFACT_KIND_SHAPE.match(kind):
                raise ValueError(f"artifact key {key!r} names a kind that is not a file extension")
            parts.setdefault(kind, []).append(part)
        for kind, numbers in parts.items():
            if sorted(numbers) != list(range(len(numbers))):
                raise ValueError(
                    f"artifact keys for {kind!r} name parts {sorted(numbers)}, "
                    "with one missing below the last"
                )
            if kind in ASSEMBLY_FILES and len(numbers) > 1:
                raise ValueError(f"{kind!r} is one file for the whole build, keyed once")
        return artifacts

    @model_validator(mode="after")
    def _names_its_parts(self) -> CatalogStep:
        """Refuse names that are not one per part, each a name a reader can read.

        Counted against the step's part files -- the formats each part is
        exported to -- because those are what a part is numbered by: a list of
        another length would put a name on the wrong part, which is worse than a
        number. A name is held to the rule the store keeps names by, so a name
        loaded here is one the version keeps.
        """
        if self.parts is None:
            return self
        for name in self.parts:
            if len(name) > NAME_MAX or not readable(name):
                raise ValueError(
                    f"part name {name!r} shows nothing, is longer than {NAME_MAX} characters, "
                    "or holds a control, line separator, bidi embedding, override or isolate, "
                    "tag or lone surrogate"
                )
        files = [len(keys) for kind, keys in self.artifact_parts().items() if kind in PART_KINDS]
        held = max(files, default=0)
        if held > MAX_PARTS:
            raise ValueError(
                f"the step's files hold {held} parts, more than the {MAX_PARTS} "
                "a version keeps names for"
            )
        if len(self.parts) != held:
            raise ValueError(
                f"`parts` gives {len(self.parts)} names, but the step's files hold {held} parts"
            )
        return self

    def artifact_parts(self) -> dict[str, list[str]]:
        """Each kind's artifact keys, in part order."""
        parts: dict[str, list[tuple[int, str]]] = {}
        for key in self.artifacts:
            kind, part = parse_artifact_key(key)
            parts.setdefault(kind, []).append((part, key))
        return {kind: [key for _, key in sorted(numbered)] for kind, numbered in parts.items()}


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
    # Path (relative to and inside the item dir) of the baked item thumbnail PNG,
    # filled by the bake helper from the final step's mesh; ``None`` until (re)baked.
    thumbnail: str | None = None
    # The content version this item was last published under. Recorded here
    # because a publisher cannot be asked what the last one was, and without it
    # every upload from a fresh clone starts over at 1.0.0. ``None`` on an item
    # that has never been published. Read by this build, written by whichever
    # one publishes.
    content_version: str | None = None
    steps: list[CatalogStep]


def _inside_item(item: Path, rel: str, what: str) -> Path:
    """The file ``rel`` names, once it is known to sit inside ``item``.

    A manifest is data from whichever root the loader was pointed at, and the
    loader reads, copies and later serves what these paths name. The spelling
    is held to the package format's rule — relative, POSIX, no empty or ``..``
    segment — so no name a package would refuse gets through here; then the
    resolved path has to sit inside the resolved item, because a link
    inside the item can leave it with no ``..`` in sight.
    """
    refusal = f"{what} path {rel!r} does not stay inside the item directory"
    try:
        safe_entry_name(rel)
    except PackError as exc:
        raise ValueError(f"{refusal}: {exc}") from exc
    item = Path(item).resolve()
    resolved = (item / rel).resolve()
    if not resolved.is_relative_to(item):
        raise ValueError(f"{refusal}: it resolves outside it")
    return resolved


def step_code_path(item: Path, step: CatalogStep) -> Path:
    """Where ``step``'s code sits, checked to be inside ``item``.

    This and the two accessors after it are what a reader calls at the moment
    it opens a file, not only what ``load_manifest`` calls once: a path that
    passed the check earlier can have been swapped for a link since, so the
    check is repeated in the same thread as the read, and the window closes to
    the open itself.
    """
    return _inside_item(item, step.code, f"step {step.index} code")


def step_artifact_path(item: Path, step: CatalogStep, key: str) -> Path:
    """Where the artifact ``step`` keys as ``key`` sits, checked to be inside ``item``."""
    return _inside_item(item, step.artifacts[key], f"step {step.index} {key} artifact")


def thumbnail_path(item: Path, manifest: CatalogManifest) -> Path:
    """Where the item thumbnail sits, checked to be inside ``item``."""
    if manifest.thumbnail is None:
        raise ValueError("manifest names no thumbnail")
    return _inside_item(item, manifest.thumbnail, "thumbnail")


def load_manifest(house_dir: Path) -> CatalogManifest:
    """Read + validate ``<house_dir>/manifest.json``.

    Steps are sorted by ``index`` and validated to be contiguous from 1; the
    ``domain`` must be registered in the domain registry; every referenced
    ``code`` file must exist; every ``code``, artifact and ``thumbnail`` path
    must stay inside ``house_dir``, which itself must not be a symlink; every
    artifact key must pass ``CatalogStep``'s key check; and a step's ``parts``,
    if given, must name each of its parts once. Raises ``ValueError`` otherwise.
    """
    house_dir = Path(house_dir)
    if house_dir.is_symlink():
        raise ValueError(f"item directory must not be a symlink: {house_dir}")
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
        if not step_code_path(house_dir, step).exists():
            raise ValueError(f"step {step.index} code file missing: {house_dir / step.code}")
        # Artifacts and the thumbnail may be missing; here they only have to stay inside.
        for key in step.artifacts:
            step_artifact_path(house_dir, step, key)
    if manifest.thumbnail is not None:
        thumbnail_path(house_dir, manifest)
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
