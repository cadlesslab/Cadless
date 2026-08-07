"""Put a received `.cls` into the catalog on this machine.

Imported items do not go where the bundled ones live. That directory ships with
the image and the deployment mounts it read-only — which is right, it is a
product asset, not somewhere downloads accumulate. What someone receives is
their own data, so it lands beside their settings and their store.

The write follows the shape an authored item has, for the same reasons:
probe that the target can be written before unpacking anything, build the item
in a staging directory, and move it into place in one step. `discover_houses`
treats any directory holding a `manifest.json` as an item, so a directory that
is half-written is a catalog item that is half-there.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_core import PydanticSerializationError

from cadless.catalog.manifest import CatalogManifest, discover_houses, read_source_json
from cadless.catalog.origins import (
    ENGINE_PROVENANCE_KEYS,
    ItemOrigin,
    origin_of,
)
from cadless.catalog.origins import (
    recorded_text as _recorded_text,
)
from cadless.catalog.pack import DIGEST_EXCLUDED, META_NAME, ClsPackage, verify_steps
from cadless.config import settings

# Where this machine's side of a failed import goes. Nothing signs in to import
# a package, so every word of a refusal is public — and the paths under the data
# directory are the part of one that only helps whoever is already inside. What
# failed and where is written here; what the caller is told is the rest.
logger = logging.getLogger(__name__)

IMPORTED_DIRNAME = "imported-catalog"
# Long enough for any id a person would write, short enough that the directory
# it names is one every filesystem can hold.
MAX_ITEM_ID_LENGTH = 128
# An artifact's kind becomes the extension of the file the loader writes, so the
# shape this allows is the shape a file extension actually is.
ARTIFACT_KIND_SHAPE = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
_DIR_NAME_SHAPE = re.compile(r"[^a-z0-9]+")
# How a provenance sentence opens, for an arrival that writes one. The prefix
# and the reader that matches it are the whole contract between the code that
# fetches and the code that decides where an item came from — so they belong to
# whichever build implements that arrival, not here. See
# ``cadless.catalog.origins``.
# The note ends with the digest, so a reader anchors its match there: a content
# version is free text and could otherwise offer a digest of its own earlier in
# the same line.
NOTE_DIGEST = re.compile(r"digest ([0-9a-f]{64})\s*\Z")


class CatalogImportError(RuntimeError):
    """The package will not be written: something about it or the target is wrong."""


class CatalogImportConflict(CatalogImportError):
    """Something already here answers to this package's id.

    ``occupant`` is the id of the thing in the way, which is not always the id
    of the package that met it: the directory a package would land in is a lossy
    fold of its id, so `My Part` can collide with an item called `my-part`. A
    caller offering to replace what is there has to name what is there, and
    reading it back out of the message is not a contract worth having.
    """

    def __init__(self, message: str, *, occupant: str) -> None:
        super().__init__(message)
        self.occupant = occupant


@dataclass(frozen=True)
class Occupation:
    """One thing standing where a package would go.

    ``where`` is the directory it sits in, or ``None`` when the store names an
    id nothing on disk claims — a project left behind by an item that is gone,
    which is a record to tidy rather than something in the way.

    Where it sits, not what it is called, decides whether it may be cleared: a
    bundled sample and a received item can answer to the same name, and only one
    of them is this app's to remove.
    """

    occupant: str
    where: Path | None
    message: str

    def is_bundled(self) -> bool:
        """Whether this ships with the image, and so is not ours to take away.

        Only a directory outside the received root counts. Nowhere on disk at
        all is not evidence of anything — an id the store holds and no directory
        claims is a stale record, and refusing on it would leave that record
        blocking every future import of the name with no way to clear it.
        """
        return self.where is not None and not self.where.is_relative_to(imported_catalog_root())


class CatalogImportUnavailable(CatalogImportError):
    """The catalog on this machine cannot be written.

    Kept apart from the rest because it is the one refusal that says nothing
    about the package. A deployment that mounted its data directory read-only
    is not a file anyone can fix by editing it, and reporting it as one sends
    its owner looking for a fault that is not there.
    """


@dataclass(frozen=True)
class ImportResult:
    item_dir: Path
    package: ClsPackage
    # The manifest as checked and written — not `package.manifest`, which is the
    # document as it arrived and is not what anything downstream should quote.
    manifest: CatalogManifest


def imported_catalog_root() -> Path:
    """Where received catalogs live — user data, beside the settings and store."""
    return settings.data_dir / IMPORTED_DIRNAME


def imported_domain_dir(domain: str) -> Path:
    # Imported lazily, matching how config resolves the bundled equivalent.
    from cadless.catalog.domains import get_domain

    return imported_catalog_root() / get_domain(domain).content_dir


def _received_items(unread: list[Path] | None = None) -> Iterator[tuple[str, Path]]:
    """Received items, minus any directory that leaves the root it was found in.

    Removing an item deletes the directory yielded here, so this is where the
    containment check belongs. No import can put a link under the received root
    — `_write_entries` writes each entry with ``write_bytes`` rather than
    extracting the archive, so an entry naming a link becomes a file of that
    name — but keeping the check beside the deletion means a later change to the
    write path cannot quietly widen what `rmtree` reaches.

    `_reject_taken` deliberately does not come through here. A linked directory
    is still an item an import would collide with, and skipping it there would
    let a package take over the bundled sample it points at.
    """
    root = imported_catalog_root()
    if not root.exists():
        return
    resolved_root = root.resolve()
    for item_id, item_dir in _items_under(root, unread):
        if item_dir.is_symlink() or not item_dir.resolve().is_relative_to(resolved_root):
            continue
        yield item_id, item_dir


def imported_item_dir(item_id: str) -> Path | None:
    """Where a received item lives, or ``None`` if this id is not one of ours.

    Which root a directory sits under is the whole answer to whether an item can
    be removed. A bundled item is a product asset the deployment mounts
    read-only, and clearing one would last only until the next load walked that
    root again — so an id found anywhere but here is not a candidate.

    Read off the manifest rather than reconstructed from the id: the directory
    name is a lossy fold of the id, and the manifest is what `discover_houses`
    and the ledger agree on.
    """
    for existing, item_dir in _received_items():
        if existing == item_id:
            return item_dir
    return None


@dataclass(frozen=True)
class RootScan:
    """What one walk of a catalog root found, and how much of it the walk saw.

    Two things can make an id missing from ``ids`` without the id being missing
    from the disk, and they fail differently enough to be recorded separately.

    ``complete`` is false when something refused to be read — a root that could
    not be listed, a domain directory that could not be walked, a manifest that
    could not be opened. Each is an error the walk stepped over, and an id
    absent because of one has not been shown to be absent.

    ``present`` is false when the root itself is not there. Nothing failed, and
    that is exactly the difficulty: a machine that never had this root and one
    whose volume did not come back up look the same from here. Whether an empty
    answer may be acted on therefore depends on which root it is, which is why
    this is reported rather than folded into ``complete``.
    """

    ids: frozenset[str]
    complete: bool
    present: bool

    def claims(self, item_id: str) -> bool:
        return item_id in self.ids


def scan_root(root: Path) -> RootScan:
    """Walk one catalog root, reporting what it holds and how much of it was seen."""
    unread: list[Path] = []
    ids = {item_id for item_id, _ in _items_under(root, unread)}
    return RootScan(ids=frozenset(ids), complete=not unread, present=Path(root).exists())


def scan_startup_catalog() -> RootScan:
    """The catalog root this app loads at startup, walked.

    The other half of `imported_item_dir`. That one answers None for an item in
    here and for an id nothing on disk claims at all, and those two are opposite
    answers to "may this be cleared": what is in here comes back at the next
    start, the other one is a record with nothing left to describe. Removing the
    first would undo itself; refusing on the second leaves it blocking every
    future import of that name.
    """
    return scan_root(settings.catalog_root)


def unclaimed_places_of(item_id: str) -> list[Path]:
    """Received directories standing where this id would go and claiming no id.

    An import lands an item in a directory named by a fold of its id, and it
    refuses when something is already there — by id *or* by directory. So an id
    can be absent from every walk while a directory of ours still holds its
    place: one whose manifest is not valid json, or one with no manifest at all.
    Clearing the record without this would answer "you can receive it again" and
    be wrong, which is the dead end the clear exists to end.

    Only a directory that claims no id of its own. One that claims another id is
    that item — ids fold onto directory names lossily, so `My Part` and `my-part`
    share a directory — and it is not this removal's to take.

    Every domain, not the first one that answers: a folded name can be free in
    one domain's directory and taken in another, and leaving the second behind
    would keep the promise false for exactly the item being received back.

    Containment is checked here for the same reason `_received_items` checks it:
    what this returns gets deleted. A directory whose manifest could not be
    *read* is not returned either — this function's answer is "claims no id",
    and a file that would not open has not said what it claims.
    """
    # Imported here rather than at module scope, matching `imported_domain_dir`
    # below it: the registry reads config, and config reads this module.
    from cadless.catalog.domains import all_domains

    name = _DIR_NAME_SHAPE.sub("-", item_id.lower()).strip("-")
    if not name:
        return []
    root = imported_catalog_root()
    if not root.exists():
        return []
    resolved_root = root.resolve()
    places: list[Path] = []
    for domain in all_domains():
        try:
            candidate = imported_domain_dir(domain.key) / name
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            if not candidate.resolve().is_relative_to(resolved_root):
                continue
        except OSError:
            logger.exception("catalog directory could not be examined: %s", name)
            continue
        unread: list[Path] = []
        if _manifest_id(candidate, unread) is None and not unread:
            places.append(candidate)
    return places


def received_origins(unread: list[Path] | None = None) -> dict[str, ItemOrigin]:
    """Every received item's id and where it came from, in one walk.

    The same walk that answers whether an item can be removed, reading one more
    small file per item while it is there. A listing needs both answers at once
    and this is what keeps that to a single pass over the root.

    ``unread`` collects what the walk could not read, so a caller can tell an id
    this root does not hold from one it failed to look for (`RootScan` above
    says why that matters). Callers that only browse can ignore it.
    """
    return {
        item_id: origin_of(read_source_json(item_dir))
        for item_id, item_dir in _received_items(unread)
    }


def import_package(
    package: ClsPackage,
    *,
    origin: str,
    already_loaded: Collection[str] = (),
    recorded: Mapping[str, Any] | None = None,
) -> ImportResult:
    """Write a package into the catalog, or refuse to.

    ``origin`` is where it came from, in words — an address it was fetched from
    or a description of the file. It is recorded rather than inferred: an
    imported item's provenance is the import, not whatever the package says
    about how it was originally authored.

    ``recorded`` is the same fact in the terms of whatever arrival brought it,
    keyed by that arrival's origin (``{"depot": {...}}``), and only an arrival
    that has such terms passes one: the ids to ask that source about this copy
    later. The sentence above is for a person to read and cannot be matched
    against a listing. Absent for a package handed over directly, and left out
    of the record entirely rather than written empty — an empty reference is a
    claim to a listing this item has none of.

    This engine never reads those keys back. The origin that wrote one is the
    one that recognises it (``cadless.catalog.origins``), which is what lets an
    arrival this build does not implement still record everything it needs.

    ``already_loaded`` is every catalog id the store holds a project for. It is
    passed in because this module does not reach the store — and it is asked for
    at all because what is loaded, not what is on disk, is what decides a
    takeover: a directory can be removed without the item being cleared, leaving
    a project that still answers to that id. A caller that can ask the store and
    does not pass the answer is offering to have somebody's project deleted.
    """
    # Checked before anything is unpacked, and before the code gate below, since
    # it costs one set intersection. A caller handing over a key this engine
    # writes itself is a build with a bug in it, and finding out after the whole
    # package has been staged makes it an unnamed failure at the end of a long
    # operation instead of a refusal at the start of a short one.
    overwritten = sorted(set(recorded or ()) & ENGINE_PROVENANCE_KEYS)
    if overwritten:
        raise CatalogImportError(
            f"an arrival cannot record {', '.join(repr(k) for k in overwritten)}: "
            f"the provenance record must stay what the import witnessed"
        )

    # Before anything is written. The gate is the reason this import exists: the
    # publisher's gate may have seen the package once, and a package that
    # arrived any other way was never seen at all. Code that fails it must not
    # reach the catalog, where the next load would run it.
    refused = verify_steps(package)
    if refused:
        detail = "; ".join(f"{item.path}: {', '.join(item.reasons)}" for item in refused)
        raise CatalogImportError(f"this package's code was refused and was not imported — {detail}")

    manifest, item_dir = _placement(package)
    document = _serialised(manifest)
    item_id = manifest.id

    _ensure_writable(item_dir.parent)
    _reject_taken(item_id, item_dir, already_loaded)

    staging = Path(tempfile.mkdtemp(dir=_staging_root(), prefix="importing-"))
    try:
        _write_entries(package, staging)
        (staging / "source.json").write_text(
            json.dumps(_provenance(package, item_id, origin, recorded), indent=2) + "\n"
        )
        # Last: a directory holding a manifest is an item, so writing it earlier
        # would publish a half-built one to anything that scans while we work.
        # What is written is the manifest as checked, not as received — anything
        # this side did not validate has no business being obeyed later.
        (staging / "manifest.json").write_text(document)
        staging.rename(item_dir)
    except OSError as exc:
        logger.exception("could not write the imported catalog into %s", staging)
        raise CatalogImportUnavailable("could not write the imported catalog") from exc
    finally:
        # A successful rename leaves nothing here; anything else does, and a
        # leftover would be found by the next import looking for a free name.
        shutil.rmtree(staging, ignore_errors=True)

    return ImportResult(item_dir=item_dir, package=package, manifest=manifest)


def _serialised(manifest: CatalogManifest) -> str:
    """The manifest as it will be on disk, proved readable before it is written.

    Checked as received is not the same as checked as written, and the round
    trip through JSON is lossy where the model does not mind. `NaN` is a float
    the model accepts and serialisation turns into `null`, which `bbox` has no
    room for; a lone surrogate is a string it accepts and UTF-8 cannot encode at
    all. Either one, left to the write, produces an item the loader refuses —
    after the write, with the id taken and nothing in the app able to give it
    back, which is the one outcome this whole path exists to avoid.

    So the bytes are made first and read back with the model that will read them
    later. Refusing here costs a package we could not have kept anyway.
    """
    try:
        document = manifest.model_dump_json(indent=2) + "\n"
        CatalogManifest.model_validate_json(document)
    except (ValueError, PydanticSerializationError) as exc:
        raise CatalogImportError(
            f"this package's manifest cannot be written and read back: {exc}"
        ) from exc
    return document


def _checked_manifest(package: ClsPackage) -> CatalogManifest:
    """The manifest this package carries, checked before any of it is obeyed.

    It is the document the loader obeys: it decides which file is a step's code,
    which files are that step's artifacts, and which one is the thumbnail. None
    of that has to describe anything the package actually carries, and none of
    it has to stay inside the item — `load_house` joins each value onto the item
    directory, reads it, and copies the result into the store, which the API
    serves. Written through unchecked, `../../settings.json` is a valid answer,
    and that file holds every provider key.

    So every path it names must be an entry of this package, and a step's code
    must be one of the entries the gate just read. The second rule is the one
    that matters most: the gate reads `steps/**`, the loader reads whatever this
    says, and without tying them together a manifest can point its step at a
    file the gate never opened — which reports a package whose steps all passed
    while running code that passed nothing.
    """
    if not package.manifest:
        raise CatalogImportError("this package carries no catalog item to import")
    try:
        manifest = CatalogManifest.model_validate(package.manifest)
    except ValueError as exc:
        # `load_manifest` would raise this after the write otherwise, leaving an
        # item nothing can read holding its id against every later attempt.
        raise CatalogImportError(f"this package's manifest is not one we can read: {exc}") from exc

    if not manifest.id.strip():
        raise CatalogImportError("this package carries no catalog item to import")
    if len(manifest.id) > MAX_ITEM_ID_LENGTH:
        # Bounded here so it is refused as a package we will not take, rather
        # than as this machine failing to write a name too long for it.
        raise CatalogImportError(
            f"this package's item id is longer than {MAX_ITEM_ID_LENGTH} characters"
        )

    manifest.steps.sort(key=lambda step: step.index)
    indices = [step.index for step in manifest.steps]
    if indices != list(range(1, len(manifest.steps) + 1)):
        raise CatalogImportError(f"step indices must be contiguous from 1, got {indices}")

    # What will actually be on disk — not every entry. `cls.json` and the two
    # the digest excludes are read here and never written, so a manifest naming
    # one of them names a file that will not be there.
    carried = {name for name in package.entries if name != META_NAME} - DIGEST_EXCLUDED
    # Exactly the set `verify_steps` just read, taken from the package rather
    # than re-derived, so the two can never come to disagree about what a step is.
    gated = set(package.steps())
    for step in manifest.steps:
        if step.code not in gated:
            raise CatalogImportError(
                f"step {step.index} names {step.code!r}, which is not one of the step files "
                "this package carries and the code check read"
            )
        for kind, relative in step.artifacts.items():
            if not ARTIFACT_KIND_SHAPE.match(kind):
                # Not a label: the loader spells a filename out of it. A kind
                # carrying a separator, a control character or three hundred
                # characters fails partway through loading an item that is
                # already on disk, and the id it took stays taken.
                raise CatalogImportError(
                    f"step {step.index} names an artifact kind this tool will not "
                    f"write a file for: {kind!r}"
                )
            if relative not in carried:
                raise CatalogImportError(
                    f"step {step.index}'s {kind} artifact names {relative!r}, "
                    "which this package does not carry"
                )
    if manifest.thumbnail is not None and manifest.thumbnail not in carried:
        raise CatalogImportError(
            f"this package's thumbnail names {manifest.thumbnail!r}, which it does not carry"
        )
    return manifest


def _staging_root() -> Path:
    """Where an item is built before it is a catalog item.

    Outside the scanned roots, on the same filesystem so the move into place is
    still one step. Staging inside the root it is destined for would make the
    half-built directory discoverable for as long as the write takes —
    `discover_houses` does not skip dot-prefixed names, so a concurrent startup
    load or another import's name check would find it.
    """
    root = settings.data_dir / ".importing"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.exception("could not prepare an import under %s", root)
        raise CatalogImportUnavailable("could not prepare an import") from exc
    return root


def discard_item_dir(item_dir: Path) -> None:
    """Take a received item's directory out of the catalog, then delete it.

    The move is what makes the item gone, mirroring the move that made it an
    item in the first place: after it, nothing scans the directory and a fresh
    import of the same package is free to take the name back.

    Deleting a tree is not one step. Stopping partway through one — the manifest
    gone, the directory still there — is the worst state available here: nothing
    lists or reloads the item, and no import may take its name. Moving first
    means whatever a failed delete leaves behind sits outside every scanned root,
    where it is inert rather than in the way.
    """
    staging = Path(tempfile.mkdtemp(dir=_staging_root(), prefix="removing-"))
    item_dir.rename(staging / item_dir.name)
    try:
        shutil.rmtree(staging)
    except OSError:
        # The item is already out of the catalog; this is leftover bytes, not a
        # failed removal. Say so rather than reporting a removal that did not
        # finish, and rather than swallowing it.
        logger.exception("removed catalog item left files behind in staging: %s", staging)


def _write_entries(package: ClsPackage, staging: Path) -> None:
    """Write the package's files, without ever handing a name to the filesystem
    that has not been checked.

    Nothing here unpacks the archive wholesale: `extractall` follows whatever
    names the archive carries, and the reader's name rules exist precisely
    because those names come from someone else.
    """
    root = staging.resolve()
    for name, blob in package.entries.items():
        if name == META_NAME:
            continue  # its contents are what the manifest and provenance are built from
        if name in DIGEST_EXCLUDED:
            # Reserved for a signature block and outside the digest by design.
            # Nothing here reads them, and writing them would put files the
            # fingerprint never covered inside an item reported as matching it.
            continue
        destination = (staging / name).resolve()
        if not destination.is_relative_to(root):
            raise CatalogImportError(f"{name!r} would be written outside the item")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(blob)


def _provenance(
    package: ClsPackage, item_id: str, origin: str, recorded: Mapping[str, Any] | None = None
) -> dict:
    """What is actually known about where this item came from.

    Not the original authoring record — the package does not carry one, and
    claiming it would be describing something we did not witness. The author and
    the handle are what the publisher confirmed when the package was made, and
    are left out when the package was made by nobody signed in.

    The handle is carried alongside the name rather than instead of it. It is
    the stronger of the two — unique, chosen, and refused by a publisher when
    a package claims one its uploader does not hold — so dropping it while
    keeping the name would record the weaker claim and discard the one worth
    having.

    ``recorded`` is kept apart from the sentence above and from the note,
    because what belongs in it is only what the arrival itself answers for. The
    content version sits in the note instead: the uploader writes it into the
    package, and a value nobody checked would read as verified next to ids that
    were. Its keys are refused where they would overwrite one of this engine's
    own — an arrival may add to the record, never rewrite what the import
    witnessed.

    What the package says it was itself derived from is kept beside ``recorded``
    rather than folded into it: where this copy came from and what it was made
    from are two different facts, and the second is the publisher's claim rather
    than anything this side saw.

    Nothing here reads it back yet. It is recorded because this file is the only
    place it survives — the package is not kept after the import — and a copy
    made from this item names *this* listing, which is the honest answer and
    does not need it. Which keys this engine owns, and what an arrival may add
    beside them, is in `docs/extending/README.md` under "Recording what your
    build knows"; read it before giving any of this a second meaning.
    """
    record = {
        "dataset": f"imported from {origin}",
        "representation": "imported",
        "license": package.meta.get("license"),
        "id": item_id,
        "note": (
            f"content version {package.meta.get('content_version')}, "
            f"digest {package.canonical_digest}"
        ),
    }
    for key in ("author", "author_handle"):
        claimed = package.meta.get(key)
        if isinstance(claimed, str) and claimed.strip():
            record[key] = claimed
    # Refused up in `import_package`, before anything was unpacked, so by here
    # there is nothing left to check — only to copy.
    for key, value in (recorded or {}).items():
        record[key] = dict(value) if isinstance(value, Mapping) else value
    carried = _carried_derivation(package.meta.get("derived_from"))
    if carried is not None:
        record["derived_from"] = carried
    return record


def _carried_derivation(claimed: object) -> dict[str, Any] | None:
    """The package's own derivation claim, if it is one at all.

    Read defensively for the same reason every other value out of a package is:
    this file is written now and read later, and a value of the wrong type would
    be carried into a listing nobody can ask about. A record naming no catalogue
    is not a reference and is dropped rather than written empty.
    """
    if not isinstance(claimed, Mapping):
        return None
    catalog_id = _recorded_text(claimed.get("catalog_id"))
    if not catalog_id:
        return None
    carried: dict[str, Any] = {"catalog_id": catalog_id}
    for name in ("version_id", "digest"):
        value = _recorded_text(claimed.get(name))
        if value:
            carried[name] = value
    carried["unchanged"] = bool(claimed.get("unchanged"))
    return carried


def _ensure_writable(target: Path) -> None:
    """Probe once, before unpacking anything.

    A catalog root that cannot be written is a deployment fact, not a per-item
    failure, and discovering it halfway through leaves part of an item behind.
    """
    try:
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target, prefix=".write-probe-"):
            pass
    except OSError as exc:
        logger.exception("the imported catalog directory %s is not writable", target)
        raise CatalogImportUnavailable("the imported catalog directory is not writable") from exc


def _placement(package: ClsPackage) -> tuple[CatalogManifest, Path]:
    """The manifest as checked, and the directory the item would occupy.

    Split out because deciding whether to replace what is already there has to
    ask the same question the import asks, and asking it a second way is how the
    two answers come to differ.
    """
    manifest = _checked_manifest(package)
    try:
        target = imported_domain_dir(manifest.domain)
    except ValueError as exc:
        raise CatalogImportError(
            f"this package declares an unknown domain: {manifest.domain!r}"
        ) from exc

    dir_name = _DIR_NAME_SHAPE.sub("-", manifest.id.lower()).strip("-")
    if not dir_name:
        raise CatalogImportError(
            f"{manifest.id!r} has no letters or digits to name a directory with"
        )
    return manifest, target / dir_name


def occupants_of(package: ClsPackage, already_loaded: Collection[str] = ()) -> list[Occupation]:
    """Everything standing where this package would go, without moving any of it.

    The import refuses at the first one it meets, which is the right answer when
    the answer is no. It is the wrong shape for a caller offering to clear the
    way: removing what the refusal named and trying again can simply meet the
    next one, having already taken something away. This reports all of them, so
    that decision can be made before anything is removed.
    """
    manifest, item_dir = _placement(package)
    seen: dict[tuple[str, Path | None], Occupation] = {}
    for occupation in _occupations(manifest.id, item_dir, already_loaded):
        # Keyed on where as well as who. One id can be answered to twice — a
        # received item and a bundled sample both called `l-bracket` — and
        # folding those together by name would hide the one that cannot be
        # removed behind the one that can.
        seen.setdefault((occupation.occupant, occupation.where), occupation)
    return list(seen.values())


def _occupations(
    item_id: str, item_dir: Path, already_loaded: Collection[str]
) -> Iterator[Occupation]:
    """Each way something already here answers to a package's name.

    Three checks because the three records disagree. The store is the one that
    decides a takeover and the one that outlives what it describes: an item
    removed without being cleared leaves a project behind, and an import
    matching it would have `load_house` clear the project that id named.

    Two more because the other two names do not mean the same thing. The catalog
    keys on the manifest id rather than the directory, so an import sharing an
    id would take over the existing item at the next load, silently and
    including a bundled sample. The directory, meanwhile, is a lossy fold of
    the id: `My Part` and `my-part` are different items by every check that
    looks at the id and the same directory on disk, so guarding only the id
    would let the second import replace the first one's files while the store
    went on pointing at them.
    """
    if item_id in already_loaded:
        yield Occupation(
            occupant=item_id,
            # Where an id loaded in the store actually lives is not something
            # the store records, so it is looked up: a bundled sample and a
            # received item can both answer to one, and only one of them is
            # ours to take away.
            where=imported_item_dir(item_id),
            message=(
                f"a catalog item called {item_id!r} is already loaded here; "
                "importing would replace it"
            ),
        )
    if item_dir.exists():
        # Named by the fold rather than by the path: the fold is theirs, made
        # out of the id they sent, and it is the part they can do something
        # about. Where it sits is this machine's, and this endpoint answers
        # anyone.
        yield Occupation(
            # What is in the way, which need not be what met it: the directory
            # is a fold of the id, so the occupant may answer to another one.
            # Falling back to the incoming id names a directory nothing claims
            # — which is still the right thing to report, because the directory
            # itself is real and `where` says so.
            occupant=_manifest_id(item_dir) or item_id,
            where=item_dir,
            message=(
                f"{item_id!r} is stored under the name {item_dir.name!r}, and a catalog item "
                "is already there; importing would replace it"
            ),
        )
    for root in (settings.catalog_root, imported_catalog_root()):
        for existing, existing_dir in _items_under(root):
            if existing == item_id:
                yield Occupation(
                    occupant=existing,
                    where=existing_dir,
                    message=(
                        f"a catalog item called {item_id!r} is already here; "
                        "importing would take it over"
                    ),
                )


def _reject_taken(item_id: str, item_dir: Path, already_loaded: Collection[str]) -> None:
    """Refuse an id — or a directory — something already here answers to."""
    for occupation in _occupations(item_id, item_dir, already_loaded):
        if occupation.where is not None:
            # Where it sits is this machine's and goes to the log; the id is
            # theirs and is told.
            logger.warning("an import of %r is blocked by %s", item_id, occupation.where)
        raise CatalogImportConflict(occupation.message, occupant=occupation.occupant)


def _items_under(root: Path, unread: list[Path] | None = None) -> Iterator[tuple[str, Path]]:
    """Every (id, directory) pair a catalog root holds.

    A directory whose manifest cannot be read claims no id and is skipped — it
    is not an item anything can look up, only one `discover_houses` counts.

    A directory that cannot be read at all costs only itself, the way `load_all`
    treats an item it cannot load: this walk answers `GET /catalog` now, and one
    unreadable directory should not take the whole listing down with it.

    ``unread`` collects what could not be read, for the caller that needs to
    tell "walked it all and this id is not here" from "did not get to look".
    Absence and failure look identical in what is yielded, and a caller acting
    on absence has to know which one it has.
    """
    root = Path(root)
    if not root.exists():
        return
    try:
        domain_dirs = sorted(path for path in root.iterdir() if path.is_dir())
    except OSError:
        logger.exception("catalog root could not be listed: %s", root)
        if unread is not None:
            unread.append(root)
        return
    for domain_dir in domain_dirs:
        try:
            names = discover_houses(domain_dir)
        except OSError:
            logger.exception("catalog directory could not be listed: %s", domain_dir)
            if unread is not None:
                unread.append(domain_dir)
            continue
        for name in names:
            item_dir = domain_dir / name
            item_id = _manifest_id(item_dir, unread)
            if item_id is not None:
                yield item_id, item_dir


def _manifest_id(item_dir: Path, unread: list[Path] | None = None) -> str | None:
    """The id a directory claims, without validating the rest of it.

    Read as a document rather than through the manifest model: an item this
    version cannot fully parse still occupies its id.

    Both ways of coming back empty answer None, because to a lookup they are the
    same: nothing here claims an id. They are not the same to a caller about to
    act on that, so a manifest that could not be *read* is recorded in ``unread``
    while one that is not valid json is not — nobody can read the second one, and
    that is a fact about the file rather than about this attempt at it.
    """
    try:
        document = json.loads((item_dir / "manifest.json").read_text())
    except OSError:
        if unread is not None:
            unread.append(item_dir)
        return None
    except ValueError:
        return None
    identifier = document.get("id") if isinstance(document, dict) else None
    return identifier if isinstance(identifier, str) else None
