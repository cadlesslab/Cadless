"""Where a binary glTF puts its geometry, in the CAD frame or as a viewer holds it.

A GLB's vertices are not where its parts are. Each mesh sits under a chain of
nodes, and every node in the chain moves, turns or scales what is below it, so
the place a viewer draws a part is the mesh's positions with every transform
from the scene root down composed onto them. A test that reads the positions
alone, or only the node that holds the mesh, passes on a file that a viewer
draws somewhere else — which is exactly the kind of misplacement the exporter
tests exist to catch. So the whole chain is composed here, the way a viewer
composes it.

``glb_world_bounds`` gives the answer back in the frame the CAD model was built
in — Z up, in millimetres — so a test can hold it against the shape's own
bounding box without converting anything itself. glTF is Y up and in metres; the
map back is the inverse of the one a Z-up model is exported through,
(x, y, z) -> (x, z, -y). ``glb_scene_bounds`` gives the same composed bounds as
a viewer's scene holds them, for a test that asks what a viewer reads.

Only what the exporter writes is read: float positions in buffer views of the
file's own binary chunk. Anything else raises rather than being read some
other way, because a reader that guesses at a layout it does not understand
reports bounds for a file it never really read.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

GLB_MAGIC = b"glTF"
# A GLB is a 12-byte header (magic, version, total length) and then chunks,
# each an 8-byte header (length, type) and its payload.
GLB_HEADER = struct.Struct("<4sII")
CHUNK_HEADER = struct.Struct("<II")
JSON_CHUNK = 0x4E4F534A  # "JSON"
BIN_CHUNK = 0x004E4942  # "BIN\0"

# The one accessor layout glTF allows for POSITION without an extension.
FLOAT_COMPONENT = 5126
VEC3 = "VEC3"
VEC3_FLOAT = struct.Struct("<3f")

METRES_TO_MM = 1000.0

#: How far a drawn bound may sit from the shape's own, in mm. A GLB stores
#: positions as float32 metres, and composing node transforms onto them adds
#: rounding of the same order; a misplaced part is off by whole millimetres.
PLACEMENT_TOLERANCE_MM = 0.01

Matrix = list[list[float]]
Point = tuple[float, float, float]


def glb_world_bounds(path: str | Path) -> tuple[Point, Point]:
    """The ``(min, max)`` corners of everything the file draws, Z up, in mm."""
    return _corners([_to_cad(p) for p in _scene_points(path)])


def glb_scene_bounds(path: str | Path) -> tuple[Point, Point]:
    """The ``(min, max)`` corners of everything the file draws, as a viewer's scene
    holds it: Y up, in the file's own unit."""
    return _corners(_scene_points(path))


def _scene_points(path: str | Path) -> list[Point]:
    document, binary = read_glb(Path(path))
    scenes = document.get("scenes")
    if not scenes:
        raise ValueError(f"{path}: no scene, so nothing in it is placed anywhere")
    roots = scenes[document.get("scene", 0)]["nodes"]

    points: list[Point] = []

    def visit(index: int, parent: Matrix) -> None:
        node = document["nodes"][index]
        world = _multiply(parent, _local_matrix(node))
        if "mesh" in node:
            for primitive in document["meshes"][node["mesh"]]["primitives"]:
                accessor = primitive["attributes"]["POSITION"]
                for position in _positions(document, binary, accessor):
                    points.append(_apply(world, position))
        for child in node.get("children", []):
            visit(child, world)

    for root in roots:
        visit(root, _identity())
    if not points:
        raise ValueError(f"{path}: the scene draws no geometry")
    return points


def _corners(points: list[Point]) -> tuple[Point, Point]:
    low = tuple(min(p[axis] for p in points) for axis in range(3))
    high = tuple(max(p[axis] for p in points) for axis in range(3))
    return low, high  # type: ignore[return-value]


def read_glb(path: str | Path) -> tuple[dict, bytes]:
    """A GLB's JSON document and its binary chunk."""
    data = Path(path).read_bytes()
    magic, _version, length = GLB_HEADER.unpack_from(data, 0)
    if magic != GLB_MAGIC:
        raise ValueError(f"{path}: not a binary glTF")
    document: dict | None = None
    binary = b""
    offset = GLB_HEADER.size
    while offset < length:
        size, kind = CHUNK_HEADER.unpack_from(data, offset)
        payload = data[offset + CHUNK_HEADER.size : offset + CHUNK_HEADER.size + size]
        if kind == JSON_CHUNK:
            document = json.loads(payload)
        elif kind == BIN_CHUNK:
            binary = payload
        offset += CHUNK_HEADER.size + size
    if document is None:
        raise ValueError(f"{path}: no JSON chunk")
    return document, binary


def _positions(document: dict, binary: bytes, index: int) -> list[Point]:
    accessor = document["accessors"][index]
    if accessor.get("componentType") != FLOAT_COMPONENT or accessor.get("type") != VEC3:
        raise ValueError(f"accessor {index} is not float VEC3 positions")
    if "sparse" in accessor or "bufferView" not in accessor:
        raise ValueError(f"accessor {index} does not read from a plain buffer view")
    view = document["bufferViews"][accessor["bufferView"]]
    if view.get("buffer", 0) != 0:
        raise ValueError(f"accessor {index} reads a buffer outside the GLB's binary chunk")
    # Interleaved vertex data is legal: a stride wider than one position means
    # the next position starts that many bytes on, not right after this one.
    stride = view.get("byteStride", VEC3_FLOAT.size)
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    return [VEC3_FLOAT.unpack_from(binary, start + i * stride) for i in range(accessor["count"])]


def _local_matrix(node: dict) -> Matrix:
    """A node's own transform: its matrix, or translation x rotation x scale."""
    if "matrix" in node:
        m = node["matrix"]  # column-major, as glTF stores it
        return [[m[column * 4 + row] for column in range(4)] for row in range(4)]
    tx, ty, tz = node.get("translation", (0.0, 0.0, 0.0))
    x, y, z, w = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
    sx, sy, sz = node.get("scale", (1.0, 1.0, 1.0))
    rotation = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    scale = (sx, sy, sz)
    translation = (tx, ty, tz)
    return [
        *[[rotation[r][c] * scale[c] for c in range(3)] + [translation[r]] for r in range(3)],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _identity() -> Matrix:
    return [[1.0 if row == column else 0.0 for column in range(4)] for row in range(4)]


def _multiply(a: Matrix, b: Matrix) -> Matrix:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def _apply(m: Matrix, p: Point) -> Point:
    return tuple(m[r][0] * p[0] + m[r][1] * p[1] + m[r][2] * p[2] + m[r][3] for r in range(3))  # type: ignore[return-value]


def _to_cad(p: Point) -> Point:
    """glTF Y-up metres back to Z-up millimetres: the inverse of (x, y, z) -> (x, z, -y)."""
    x, y, z = p
    return (x * METRES_TO_MM, -z * METRES_TO_MM, y * METRES_TO_MM)
