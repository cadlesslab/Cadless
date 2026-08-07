"""Catalog command-line interface.

``python -m cadless.catalog <command>``. Run ``--help`` for the current verb
list and their flags; ``--house`` and ``--part`` are interchangeable id
selectors.

Every verb here reads catalog content and writes only to the live database.
Authoring — turning source material into catalog items — is not part of this
tool; it runs in a private pipeline. See ``README.md`` beside this file.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from cadless.catalog.ledger import Ledger, default_ledger
from cadless.catalog.loader import (
    clear_all,
    clear_house,
    list_state,
    load_house,
)
from cadless.catalog.manifest import discover_houses
from cadless.config import settings
from cadless.store import Store

# Catalog content lives under settings.catalog_root (outside the repo, bind-mounted
# into docker). This is the default for the CLI verbs.
DEFAULT_CATALOG_DIR = str(settings.house_catalog_dir)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cadless.catalog", description="Load and inspect catalog items"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    # ``--part`` is a domain-neutral alias for ``--house`` (the catalog is
    # domain-agnostic; mechanical parts read better as "parts"). Both select the
    # same id and may be combined.
    for name in ("load", "reload"):
        sp = sub.add_parser(name)
        sp.add_argument("--house", action="append", default=None)
        sp.add_argument("--part", action="append", default=None)
        sp.add_argument("--all", action="store_true")
        sp.add_argument("--catalog-dir", default=DEFAULT_CATALOG_DIR)
    cp = sub.add_parser("clear")
    cp.add_argument("--house", action="append", default=None)
    cp.add_argument("--part", action="append", default=None)
    cp.add_argument("--all", action="store_true")
    lp = sub.add_parser("list")
    lp.add_argument("--catalog-dir", default=DEFAULT_CATALOG_DIR)
    return parser


def _explicit_ids(args: argparse.Namespace) -> list[str]:
    """Ids passed via --house and/or --part (domain-neutral alias), merged."""
    return list(args.house or []) + list(getattr(args, "part", None) or [])


def _selected_houses(args: argparse.Namespace, catalog_dir: str) -> list[str] | None:
    """Catalog ids from --all or --house/--part, or None if none was given."""
    if getattr(args, "all", False):
        return discover_houses(catalog_dir)
    ids = _explicit_ids(args)
    return ids or None


async def _run_load(
    args: argparse.Namespace, store: Store, ledger: Ledger, catalog_dir: str
) -> int:
    houses = _selected_houses(args, catalog_dir)
    if houses is None:
        print("error: specify --all or --house/--part ID")
        return 2
    await store.init()
    reload = args.command == "reload"
    # Load time is measured per item + total (#23) so bulk-catalog load cost
    # into the api container is observable instead of anecdotal.
    total = time.perf_counter()
    loaded = 0
    for hid in houses:
        started = time.perf_counter()
        pid = await load_house(store, ledger, Path(catalog_dir) / hid, reload=reload)
        elapsed = time.perf_counter() - started
        if pid:
            loaded += 1
            print(f"{hid}: loaded pid={pid} ({elapsed:.2f}s)")
        else:
            print(f"{hid}: already loaded (skipped)")
    print(f"total: {loaded}/{len(houses)} item(s) loaded in {time.perf_counter() - total:.2f}s")
    return 0


async def _run_clear(args: argparse.Namespace, store: Store, ledger: Ledger) -> int:
    if args.all:
        print(f"cleared: {await clear_all(store, ledger)}")
        return 0
    ids = _explicit_ids(args)
    if not ids:
        print("error: specify --all or --house/--part ID")
        return 2
    for hid in ids:
        ok = await clear_house(store, ledger, hid)
        print(f"{hid}: {'cleared' if ok else 'not loaded'}")
    return 0


def main(
    argv: list[str] | None = None,
    *,
    store: Store | None = None,
    ledger: Ledger | None = None,
) -> int:
    args = _build_parser().parse_args(argv)
    ledger = ledger or default_ledger()

    if args.command == "list":
        for row in asyncio.run(list_state(store or Store(), args.catalog_dir)):
            tag = f"loaded pid={row['project_id']}" if row["loaded"] else "not loaded"
            print(f"{row['id']}  [{tag}]")
        return 0

    store = store or Store()
    if args.command in ("load", "reload"):
        return asyncio.run(_run_load(args, store, ledger, args.catalog_dir))
    if args.command == "clear":
        return asyncio.run(_run_clear(args, store, ledger))
    return 2
