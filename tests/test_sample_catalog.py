"""Bundled sample catalog guard (keyless first experience).

Pins that the shipped sample items exist, load as valid *mechanical* manifests
with baked artifacts, and reparametrize deterministically (no LLM) — the keyless
value proposition. These items ship in-repo so the tool works before a key is
entered; this test keeps a broken sample from being released.
"""

from __future__ import annotations

from pathlib import Path

from cadless.catalog.manifest import discover_houses, load_manifest
from cadless.config import settings
from cadless.params import apply_param_overrides, extract_params
from cadless.worker import run_code

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "catalog" / "mech-catalog"


def _offline_settings():
    """Execution settings with the offline timeout rather than the live one.

    ``exec_timeout_secs`` is tuned for a user waiting on codegen; re-running a
    shipped sample is pure OCCT and takes far longer, so it gets
    ``bake_exec_timeout_secs``. The authoring stack used to hand this out as
    ``bake_settings()`` and left with it, so the swap is spelled here.
    """
    return settings.model_copy(update={"exec_timeout_secs": settings.bake_exec_timeout_secs})


def _sample_ids() -> list[str]:
    return discover_houses(SAMPLES_DIR)


def test_ships_at_least_five_samples() -> None:
    ids = _sample_ids()
    assert len(ids) >= 5, f"expected >=5 bundled samples, found {ids}"


def test_every_sample_is_valid_mechanical_with_artifacts() -> None:
    ids = _sample_ids()
    assert ids, "no bundled samples found"
    for item_id in ids:
        item_dir = SAMPLES_DIR / item_id
        manifest = load_manifest(item_dir)  # validates schema, contiguous steps, code files
        assert manifest.domain == "mechanical", item_id
        assert manifest.thumbnail, f"{item_id}: no thumbnail baked"
        assert (item_dir / manifest.thumbnail).exists(), f"{item_id}: thumbnail missing"
        final = manifest.steps[-1]
        assert final.artifacts.get("step"), f"{item_id}: no STEP artifact"
        assert (item_dir / final.artifacts["step"]).exists(), f"{item_id}: STEP file missing"


def test_sample_reparametrizes_deterministically() -> None:
    """Changing a parameter re-bakes to different geometry, no LLM involved."""
    ids = _sample_ids()
    assert ids, "no samples to test"
    item_dir = SAMPLES_DIR / ids[0]
    manifest = load_manifest(item_dir)
    code = (item_dir / manifest.steps[-1].code).read_text()
    params = extract_params(code)
    assert params, f"{ids[0]}: final step exposes no editable params"
    key = next(k for k, v in params.items() if isinstance(v, (int, float)))
    base = run_code(code, config=_offline_settings())
    assert base.ok, base.error
    bumped = apply_param_overrides(code, {key: params[key] * 2})
    assert bumped != code
    after = run_code(bumped, config=_offline_settings())
    assert after.ok, after.error
    assert after.volume != base.volume, "parameter change did not alter geometry"
