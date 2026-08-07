"""House Catalog: on-disk benchmark fixtures + offline loader (Phase 1).

A catalog house is a directory under ``$CADLESS_CATALOG_ROOT/house-catalog/<id>/`` holding a
``manifest.json`` (house metadata + an ordered ladder of build steps), cumulative
``steps/NN.py`` build123d scripts (each defines a top-level ``result``), and
pre-rendered ``artifacts/NN/`` (STEP/GLB). The loader inserts a house into the
backend :class:`cadless.store.Store` so it appears in the UI; a sidecar ledger
tracks catalog-owned projects so ``clear`` never touches user projects.
"""
