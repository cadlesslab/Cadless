"""Seed a real catalog item into a test store.

Catalog membership is ``projects.catalog_item_id``, written by the insert the
loader makes (#27) — so a test that needs a read-only project has to go through
the real loader rather than creating a project and marking it. The ledger beside
the store db keeps what the catalog panel displays, and a test may find it empty
without any of the read-only gates changing their answer. The suites that assert
those gates each need an item, so it lives here rather than being copied into
each of them. Two older suites (``test_versions_api``'s rerun gate and
``test_reparametrize_api``) still hand-roll the same block inline and have not
been migrated.

``root`` decides more than where the files land. Which root a directory sits
under is what tells a bundled item from a received one from a record nothing on
disk claims, and `DELETE /catalog/{id}` answers differently for each — so a
caller that wants a bundled item passes ``settings.domain_catalog_dir(...)``, one
that wants a received item passes ``imported_domain_dir(...)``, and anywhere else
is a fourth thing the app cannot see the files of.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cadless.catalog.ledger import Ledger
from cadless.catalog.loader import load_house
from cadless.store import Store


def load_catalog_item(store: Store, root: Path, *, house_id: str = "cat-1", steps: int = 2) -> int:
    """Write a minimal catalog item under ``root``, load it, and return its project id.

    Defaults to two steps so the project has a version to move its current
    pointer *away* from — the shape every read-only regression here needs.
    """
    house = root / house_id
    (house / "steps").mkdir(parents=True, exist_ok=True)
    manifest_steps = []
    for i in range(1, steps + 1):
        (house / "steps" / f"{i:02d}.py").write_text(f"result = {i}\n")
        manifest_steps.append({"index": i, "instruction": f"s{i}", "code": f"steps/{i:02d}.py"})
    (house / "manifest.json").write_text(
        json.dumps(
            {
                "id": house_id,
                "name": "Read Only Item",
                "domain": "house",
                "steps": manifest_steps,
            }
        )
    )
    # The ledger has to land where catalog_state.ledger_for looks: beside the db.
    ledger = Ledger(Path(store.db_path).parent / "catalog-ledger.json")

    async def go() -> int | None:
        await store.init()
        return await load_house(store, ledger, house)

    project_id = asyncio.run(go())
    assert project_id is not None, "catalog fixture failed to load"
    return project_id
