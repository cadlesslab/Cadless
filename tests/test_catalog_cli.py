"""Catalog CLI tests (Phase 1)."""

import json
from pathlib import Path

from cadless.catalog.cli import main
from cadless.catalog.ledger import Ledger
from cadless.store import Store


def _store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")


def _write_house(catalog_dir: Path, house_id: str) -> None:
    house = catalog_dir / house_id
    (house / "steps").mkdir(parents=True, exist_ok=True)
    (house / "steps" / "01.py").write_text("result = 1\n")
    (house / "manifest.json").write_text(
        json.dumps(
            {
                "id": house_id,
                "name": f"Name {house_id}",
                "steps": [{"index": 1, "instruction": "s1", "code": "steps/01.py"}],
            }
        )
    )


def test_list_then_load_then_clear(tmp_path, capsys):
    cat = tmp_path / "cat"
    _write_house(cat, "h1")
    s = _store(tmp_path)
    led = Ledger(tmp_path / "ledger.json")

    assert main(["list", "--catalog-dir", str(cat)], store=s, ledger=led) == 0
    assert "not loaded" in capsys.readouterr().out

    assert main(["load", "--all", "--catalog-dir", str(cat)], store=s, ledger=led) == 0
    assert led.get("h1") is not None

    assert main(["list", "--catalog-dir", str(cat)], store=s, ledger=led) == 0
    assert "loaded pid=" in capsys.readouterr().out

    assert main(["clear", "--all"], store=s, ledger=led) == 0
    assert led.get("h1") is None


def test_load_requires_target(tmp_path, capsys):
    cat = tmp_path / "cat"
    _write_house(cat, "h1")
    s = _store(tmp_path)
    led = Ledger(tmp_path / "ledger.json")
    rc = main(["load", "--catalog-dir", str(cat)], store=s, ledger=led)
    assert rc == 2
    assert "specify --all or --house" in capsys.readouterr().out
