# Credits and third-party notices

The source in this repository is licensed under the MIT License (see
[LICENSE](./LICENSE)). Bundled catalog items and third-party dependencies are
recorded below.

## Bundled catalog items

<!--
  Keep these lists in step with the contents of `catalog/` (guarded by
  tests/test_public_assets.py, which checks every item's recorded dataset and
  licence, not merely that it recorded something). Every item needs a recorded
  origin before release — an item whose provenance is unknown does not ship.
-->

Every bundled item is an original parametric design authored by InnoLingua Inc.
for the Cadless sample catalog (`cadless-samples`) and is released under the MIT
License. Each item repeats its own origin in its `source.json`, including whether
it was hand-authored or synthetic. Nothing under `catalog/` is derived from a
third-party CAD corpus, or from surveyed, listed or scanned real-world property
data.

**Mechanical** (`catalog/mech-catalog/`) — `bearing-block`, `connecting-rod`,
`crankshaft`, `enclosure`, `end-cap`, `engine-block`, `flanged-shaft`,
`flat-washer`, `flywheel`, `hex-standoff`, `hinge`, `l-bracket`,
`l-mounting-bracket`, `mounting-plate`, `piston`, `piston-assembly`, `spur-gear`,
`v-pulley`

**Furniture** (`catalog/furniture-catalog/`) — `bar-stool`, `bedside-table`,
`bench`, `bookshelf`, `cabinet`, `coffee-table`, `desk`, `dining-table`,
`tv-stand`, `wall-shelf`

**Enclosures & fixtures** (`catalog/fixture-catalog/`) — `cable-clamp`,
`corner-bracket`, `din-rail-clip-mount`, `drill-jig`, `panel-mount-plate`,
`project-box`, `raspberry-pi-mounting-plate`, `sensor-bracket`,
`terminal-block-cover`, `v-block`

**House** (`catalog/house-catalog/`) — `demo-house`, a synthetic reference house

Items authored with this tool are generated build123d code. If you publish or
sell items you created, check the terms of the model provider you used — some
tie commercial-use rights to account type or subscription tier.

## Core dependencies

| Project | Role | License |
|---------|------|---------|
| [build123d](https://github.com/gumyr/build123d) | Parametric CAD modelling API | Apache-2.0 |
| [OCCT](https://dev.opencascade.org/) (via build123d) | B-Rep kernel, STEP export | LGPL-2.1 with exception |
| [FastAPI](https://fastapi.tiangolo.com/) | Backend web layer | MIT |
| [three.js](https://threejs.org/) | Browser rendering | MIT |
| [Vite](https://vitejs.dev/) | Frontend build | MIT |

The full resolved dependency tree is in `pyproject.toml` and
`frontend/package.json`.

> **Note on OCCT**: the geometry kernel reached through build123d is licensed
> LGPL-2.1 with an exception. Using it through the published Python API — which
> is what this project does — does not impose LGPL terms on your own designs or
> on code you write against this tool. If you redistribute a modified OCCT
> build, its terms apply to that build.
