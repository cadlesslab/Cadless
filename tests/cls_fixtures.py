"""A `.cls` assembled without a packer, for the tests that need one to read.

Reading a package and building one are separate concerns, and this build keeps
only the reader. The tests for that reader still need a package to open, and
taking one from a packer that ships elsewhere would tie this suite to a build
that is not here — which is what this file exists to avoid.

**This is not a packer.** What is here only assembles a container from an item
already on disk, so that something well-formed exists to be read. It validates
nothing, and it should not — a fixture that refused its input would be testing
the fixture. Which items may be handed on, and under what terms, is a writer's
question, and it is asked wherever that writer lives.

That split is also why the duplication is bounded. If the format changes, this
file changes with it; if a *rule* changes, this file has nothing to say about
it.

It is the honest shape for these tests besides. A package handed over on a
drive was not built by our packer either — that is the whole reason the receive
path checks it.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

import cadless
from cadless.catalog.pack import META_NAME

# A fixture built twice should be the same bytes, so the times zip records
# cannot come from the filesystem. 1980 is the earliest one can express.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)

# Restated because it is the *writer's* declaration, and the writer is what this
# file stands in for. `META_NAME` is not: it is the layout both halves agree on,
# it lives in the half this fixture keeps depending on, and a second copy would
# leave the fixture quietly writing the old name if it were ever renamed.
CLS_FORMAT_VERSION = 1


def archive(members: Iterable[tuple[str, bytes]], *, compress: int = zipfile.ZIP_DEFLATED) -> bytes:
    """Pack exactly these entries, in this order — malformed cases included.

    Takes pairs rather than a mapping so a test can write one name twice, which
    is a package the reader has to refuse and a mapping cannot express.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        for name, blob in members:
            info = zipfile.ZipInfo(name, FIXED_DATE_TIME)
            info.compress_type = compress
            out.writestr(info, blob)
    return buffer.getvalue()


def packed(
    item_dir: Path | str,
    *,
    content_version: str = "1.0.0",
    author: str | None = None,
    author_handle: str | None = None,
    extra: Mapping[str, bytes] | None = None,
) -> bytes:
    """The container an item on disk would travel in.

    ``author`` and ``author_handle`` are left out entirely when they are absent
    rather than written as null, because that is what the reader is told apart:
    absent says nobody claimed this, and a null would read as a claim that
    failed.
    """
    item_dir = Path(item_dir)
    manifest = json.loads((item_dir / "manifest.json").read_text())
    source_path = item_dir / "source.json"
    source = json.loads(source_path.read_text()) if source_path.exists() else {}

    entries: dict[str, bytes] = {}
    for step in manifest.get("steps", []):
        name = Path(step["code"]).as_posix()
        entries[name] = (item_dir / name).read_bytes()
    artifacts = item_dir / "artifacts"
    if artifacts.is_dir():
        for path in sorted(artifacts.rglob("*")):
            if path.is_file():
                entries[path.relative_to(item_dir).as_posix()] = path.read_bytes()

    # The manifest rides whole so the item can be rebuilt, with the transcript
    # dropped — it is what the author typed, and it is not shared by default.
    carried = json.loads(json.dumps(manifest))
    carried["content_version"] = content_version
    for step in carried.get("steps", []):
        step["transcript"] = None

    meta: dict[str, object] = {
        "format_version": CLS_FORMAT_VERSION,
        "content_version": content_version,
        "license": source.get("license", "MIT"),
        "title": manifest.get("name") or manifest["id"],
        "min_tool_version": cadless.__version__,
        "domain": manifest["domain"],
        "tags": list(manifest.get("tags", [])),
        "included_fields": ["artifacts", "steps"],
        "cadless_manifest": carried,
    }
    if manifest.get("category"):
        meta["category"] = manifest["category"]
    if author:
        meta["author"] = author
    if author_handle:
        meta["author_handle"] = author_handle
    if source.get("derived_from"):
        meta["derived_from"] = source["derived_from"]
    entries[META_NAME] = json.dumps(meta, sort_keys=True, ensure_ascii=False).encode("utf-8")
    entries.update(extra or {})

    return archive(sorted(entries.items(), key=lambda pair: pair[0].encode("utf-8")))
