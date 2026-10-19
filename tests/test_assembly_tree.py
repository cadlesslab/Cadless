"""A build read as the tree build123d makes. Marked build123d: builds shapes in process."""

import pytest
from build123d import Box, Color, Compound, Cylinder, Pos, Rot, Shell

from cadless import assembly_tree
from cadless.assembly_tree import build_tree

pytestmark = pytest.mark.build123d


def _named(shape, label, color=None):
    shape.label = label
    if color is not None:
        shape.color = color
    return shape


def _bare_solid():
    return Pos(5, 0, 0) * Rot(0, 0, 30) * Box(4, 6, 8)


def _four_labelled():
    return Compound(
        children=[_named(Pos(20 * i, 0, 0) * Box(2 + i, 2, 2), f"block{i}") for i in range(4)]
    )


def _car():
    chassis = _named(Box(30, 40, 4), "chassis")
    axle = _named(Box(20, 1, 1), "axle_f")
    wheel_fl = _named(Pos(-10, 0, 0) * Cylinder(3, 2), "wheel_fl", Color(1, 0, 0))
    wheel_fr = _named(Pos(10, 0, 0) * Cylinder(3, 2), "wheel_fr", Color(1, 0, 0))
    front = Compound(children=[axle, wheel_fl, wheel_fr], label="front_axle")
    front.location = Pos(0, 25, 0)
    car = Compound(children=[chassis, front], label="car")
    car.location = Pos(100, 0, 0)
    return car


def _boolean_two_body():
    return Box(10, 10, 10) + Pos(30, 0, 0) * Box(5, 5, 5)


SHAPES = {
    "a bare solid": (_bare_solid, ["part0"], [[]]),
    "four labelled solids": (_four_labelled, ["block0", "block1", "block2", "block3"], [[]] * 4),
    "a nested car": (
        _car,
        ["chassis", "axle_f", "wheel_fl", "wheel_fr"],
        [["car"], ["car", "front_axle"], ["car", "front_axle"], ["car", "front_axle"]],
    ),
    "a boolean two-body shape": (_boolean_two_body, ["part0", "part1"], [[], []]),
}


def _box(shape):
    bb = shape.bounding_box()
    return [*bb.min, *bb.max]


@pytest.mark.parametrize("make", list(SHAPES), ids=list(SHAPES))
def test_the_parts_are_the_result_s_solids_in_order_with_their_names(make):
    """The part index is the order ``solids()`` returns, so the tree changes no
    part's number, only what it is called."""
    build, names, paths = SHAPES[make]
    result = build()
    solids = result.solids()

    tree = build_tree(result)

    assert [p.index for p in tree.parts] == list(range(len(solids)))
    assert [p.name for p in tree.parts] == names
    assert [list(p.path) for p in tree.parts] == paths
    for part, solid in zip(tree.parts, solids, strict=True):
        assert part.solid.volume == pytest.approx(solid.volume, rel=1e-6)
        assert tuple(part.solid.center()) == pytest.approx(tuple(solid.center()), abs=1e-6)
        assert _box(part.solid) == pytest.approx(_box(solid), abs=1e-6)


@pytest.mark.parametrize("make", list(SHAPES), ids=list(SHAPES))
def test_every_part_is_baked_into_the_world_frame(make):
    """No consumer is handed a placement to get wrong: the geometry already sits
    where the part sits in the assembled model."""
    tree = build_tree(SHAPES[make][0]())

    assert all(p.solid.location.wrapped.IsIdentity() for p in tree.parts)


@pytest.mark.parametrize(
    "place",
    [
        lambda kit: Pos(0, 0, 10) * kit,
        lambda kit: Rot(90, 0, 0) * kit,
        lambda kit: kit.moved(Pos(5, 0, 0)),
    ],
    ids=["Pos * kit", "Rot * kit", "kit.moved"],
)
def test_a_sub_assembly_placed_as_a_copy_keeps_its_names(place):
    """``Pos(...) * kit`` is how a sub-assembly is usually placed, and it is a copy.

    The copy's shape holds new solids while its children are the originals, so
    names are paired with solids by position rather than by identity.
    """
    base = _named(Box(4, 4, 1), "base")
    cap = _named(Pos(0, 0, 3) * Box(2, 2, 1), "cap")
    kit = Compound(children=[base, cap], label="kit")
    solo = _named(Pos(50, 0, 0) * Box(1, 1, 1), "solo")

    tree = build_tree(Compound(children=[place(kit), solo], label="outer"))

    assert [(p.name, list(p.path)) for p in tree.parts] == [
        ("base", ["outer", "kit"]),
        ("cap", ["outer", "kit"]),
        ("solo", ["outer"]),
    ]


def test_a_labelled_leaf_of_several_solids_numbers_its_parts():
    pair = _named(_boolean_two_body(), "pair")
    solo = _named(Pos(0, 50, 0) * Box(2, 2, 2), "solo")

    tree = build_tree(Compound(children=[pair, solo]))

    assert [p.name for p in tree.parts] == ["pair_0", "pair_1", "solo"]


def test_a_leaf_holding_no_solid_is_no_part():
    """A shell adds nothing to ``solids()``, so it adds no part either."""
    shell = Shell(Box(1, 1, 1).faces()[:3])
    tree = build_tree(Compound(children=[_named(Box(2, 2, 2), "block"), shell]))

    assert [p.name for p in tree.parts] == ["block"]


def test_colours_and_groups_come_from_the_tree():
    tree = build_tree(_car())

    assert tree.name == "car"
    assert [p.color for p in tree.parts] == [None, None, (1.0, 0.0, 0.0, 1.0), (1.0, 0.0, 0.0, 1.0)]
    assert [(name, list(path)) for name, path in tree.groups] == [
        ("car", []),
        ("front_axle", ["car"]),
    ]


def test_scale_converts_every_coordinate_of_a_part_including_its_position():
    """The export scale turns authoring units into millimetres, so a part placed
    100 units out ends up 100 scaled units out, not 100 millimetres out."""
    car = _car()
    unscaled = build_tree(car)

    scaled = build_tree(car, scale=2.0)

    for small, big in zip(unscaled.parts, scaled.parts, strict=True):
        assert _box(big.solid) == pytest.approx([2 * v for v in _box(small.solid)], abs=1e-6)
        assert big.solid.location.wrapped.IsIdentity()


def test_the_payload_is_plain_data_with_no_geometry():
    assert build_tree(_car()).as_payload() == {
        "name": "car",
        "parts": [
            {"index": 0, "name": "chassis", "path": ["car"], "color": None},
            {"index": 1, "name": "axle_f", "path": ["car", "front_axle"], "color": None},
            {
                "index": 2,
                "name": "wheel_fl",
                "path": ["car", "front_axle"],
                "color": [1.0, 0.0, 0.0, 1.0],
            },
            {
                "index": 3,
                "name": "wheel_fr",
                "path": ["car", "front_axle"],
                "color": [1.0, 0.0, 0.0, 1.0],
            },
        ],
        "groups": [{"name": "car", "path": []}, {"name": "front_axle", "path": ["car"]}],
    }


def test_a_solid_moved_to_another_parent_takes_no_name_from_its_old_slot():
    """A solid re-attached to a compound changes the children's order but not the
    compound's own shape, so position would hand each label the other solid."""
    left = _named((Pos(-10, 0, 0) * Box(2, 2, 2)).solid(), "left")
    right = _named((Pos(10, 0, 0) * Box(2, 2, 2)).solid(), "right")
    pair = Compound(children=[left, right], label="pair")
    left.parent = None
    left.parent = pair

    tree = build_tree(pair)

    assert [p.name for p in tree.parts] == ["part0", "part1"]
    # The group itself was read before its children were, so it keeps its name.
    assert [list(p.path) for p in tree.parts] == [["pair"], ["pair"]]


def test_a_solid_put_in_another_s_place_does_not_take_its_name():
    """Replacing a compound's only solid through ``.parent`` leaves the compound's
    shape holding the old one, which shares no shape with the new child -- as a
    copy would not either -- but has a different form."""
    box = _named(Box(2, 2, 2).solid(), "box")
    group = Compound(children=[box])
    cylinder = _named(Cylinder(1, 2).solid(), "cylinder")
    box.parent = None
    cylinder.parent = group

    tree = build_tree(group)

    assert [p.name for p in tree.parts] == ["part0"]


def test_a_solid_put_in_the_place_of_one_of_the_same_shape_does_not_take_its_name():
    """The same kind of shape, but not the same solid: a bigger box in a box's place."""
    small = _named(Box(2, 2, 2).solid(), "small")
    group = Compound(children=[small])
    big = _named(Box(5, 5, 5).solid(), "big")
    small.parent = None
    big.parent = group

    tree = build_tree(group)

    assert [p.name for p in tree.parts] == ["part0"]


def test_a_walk_that_does_not_line_up_with_the_solids_names_nothing(monkeypatch):
    """Where the walk's solids are not ``solids()``'s in order, the parts keep
    their geometry and order and are named by their index alone."""
    real_walk = assembly_tree._leaves
    monkeypatch.setattr(assembly_tree, "_leaves", lambda shape: list(reversed(real_walk(shape))))

    tree = build_tree(_car())

    assert [p.name for p in tree.parts] == ["part0", "part1", "part2", "part3"]
    assert [p.path for p in tree.parts] == [()] * 4
    assert [_box(p.solid) for p in tree.parts] == [_box(s) for s in _car().solids()]


def test_a_walk_that_finds_fewer_solids_names_nothing(monkeypatch):
    real_walk = assembly_tree._leaves
    monkeypatch.setattr(assembly_tree, "_leaves", lambda shape: real_walk(shape)[:-1])

    tree = build_tree(_car())

    assert [p.name for p in tree.parts] == ["part0", "part1", "part2", "part3"]


def test_names_that_cannot_be_read_leave_the_parts_unnamed(monkeypatch):
    """Reading the names is the one step allowed to fail; the parts still come back."""

    def unreadable(shape):
        raise RuntimeError("no names here")

    monkeypatch.setattr(assembly_tree, "_leaves", unreadable)

    tree = build_tree(_car())

    assert [p.name for p in tree.parts] == ["part0", "part1", "part2", "part3"]
