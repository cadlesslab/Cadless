"""The joint's sizes, held to what ``split_and_join`` actually builds.

The brief and the checks state these numbers without the geometry kernel, so a
number drifting from the helper would tell the model one size and build another.
"""

import pytest
from build123d import Axis, Box, Cylinder, Plane, Pos, Rot

from cadless.joinery import JoineryError, split_and_join
from cadless.joint_sizes import (
    PEG_MOST,
    REACH_MOST_MM,
    least_band_mm,
    least_peg_spot_mm,
    pieces_along,
    stated_band_mm,
    stated_spot_mm,
)

pytestmark = pytest.mark.build123d

#: x = 0, facing +X: the half on -X carries the tabs, which reach into +X.
SEAM = Plane.YZ
#: Below and above the clearance where the margin stops being its 1 mm floor.
CLEARANCES = [0.2, 0.6]


def _rod(diameter: float):
    """A rod along X, cut across by :data:`SEAM`."""
    return Rot(0, 90, 0) * Cylinder(diameter / 2, 300)


def test_the_half_behind_a_seam_reaches_past_the_cut_as_far_as_stated():
    # Wide enough for the widest dovetail, whose tab stands furthest past the cut.
    behind, ahead = split_and_join(Box(400, 160, 10), SEAM, Axis.Z, 0.2)

    assert behind.bounding_box().max.X == pytest.approx(REACH_MOST_MM)
    assert ahead.bounding_box().min.X == pytest.approx(0.0, abs=1e-6)


def test_a_peg_stands_past_the_cut_by_its_widest_diameter_at_most():
    # A rod thick enough for the widest peg, put in along its length.
    behind, _ahead = split_and_join(_rod(30), SEAM, Axis.X, 0.2)

    assert behind.bounding_box().max.X == pytest.approx(PEG_MOST)


@pytest.mark.parametrize("clearance", CLEARANCES)
def test_a_dovetail_takes_the_least_band_stated_and_nothing_narrower(clearance):
    # A slab cut across and put in along its height: the cut face is a band as
    # wide as the slab is thick, crossed along Z in one run as long as it is tall.
    band = least_band_mm(clearance)

    split_and_join(Box(300, band + 0.02, 30), SEAM, Axis.Z, clearance)
    with pytest.raises(JoineryError, match="no place on the cut face"):
        split_and_join(Box(300, band - 0.02, 30), SEAM, Axis.Z, clearance)


def test_a_bed_side_at_most_twice_the_reach_is_counted_by_the_ratio():
    # Counted with the joint, a side a hair past the reach would need hundreds of
    # thousands of pieces; at twice the reach and below, the ratio is said.
    assert pieces_along(40.0, 2 * REACH_MOST_MM) == 2
    assert pieces_along(40.0, 2 * REACH_MOST_MM + 0.5) == 3


@pytest.mark.parametrize(("width", "height"), [(60, 40), (160, 80)])
def test_a_wall_of_the_stated_band_inside_a_wide_face_takes_a_dovetail(width, height):
    # A tube's side wall is a band within a wider cut face, where the positions
    # sampled across the face can straddle its edges. The figure the brief and the
    # refusal state has to hold there, not only where the band is the whole face.
    wall = stated_band_mm(0.2)
    tube = Box(300, width, height) - Box(310, width - 2 * wall, height - 2 * wall)

    split_and_join(tube, SEAM, Axis.Z, 0.2)


@pytest.mark.parametrize("clearance", [0.2, 0.823])
def test_a_rib_of_the_stated_band_inside_a_wide_face_takes_a_dovetail(clearance):
    # Walls too thin for a joint around a rib that is not: the rib's two edges
    # both fall between the samples, so each has to be found -- and at 0.823 mm,
    # where rounding up leaves less room than the two edges are found to, the
    # figure has to cover that as well.
    rib = stated_band_mm(clearance)
    shell = Box(300, 160, 80) - Box(310, 156, 76) + Box(300, rib, 77)

    split_and_join(shell, SEAM, Axis.Z, clearance)


@pytest.mark.parametrize("clearance", [0.2, 0.5])
def test_a_wall_of_the_stated_spot_cut_through_its_height_takes_a_peg(clearance):
    # A shell cut across its height and set down onto the piece below takes pegs
    # in its walls: points sampled on a grid across the face fall off a thin
    # wall's middle, so the spot the figure promises has to be found there too.
    wall = stated_spot_mm(clearance)
    shell = Box(300, 120, 80) - Pos(0, 0, -wall) * Box(300 - 2 * wall, 120 - 2 * wall, 80)

    split_and_join(shell, Plane.XY, Axis.Z, clearance)


def _pegs_on(behind):
    """The pegs standing past a cut on Plane.XY, out of the half behind it."""
    return (behind & Pos(0, 0, 50) * Box(400, 400, 100)).solids()


def test_a_boss_keeps_room_for_its_second_peg():
    # The roomiest points on a post walk to its middle; the points themselves
    # stay candidates, so a second peg still finds room beside the first.
    shell = Box(300, 120, 80) - Box(296, 116, 76) + Cylinder(13, 78)

    behind, _ahead = split_and_join(shell, Plane.XY, Axis.Z, 0.2)

    assert len(_pegs_on(behind)) == 2


def test_a_post_no_point_of_the_whole_face_lands_on_takes_a_peg():
    # Each part of the cut face is sampled on its own, so a post too small for
    # the face's grid to land on is still found.
    shell = Box(300, 120, 80) - Box(296, 116, 76) + Pos(6.25, 2.5, 0) * Cylinder(4.5, 78)

    behind, _ahead = split_and_join(shell, Plane.XY, Axis.Z, 0.2)

    assert len(_pegs_on(behind)) >= 1


def test_a_wall_as_thick_as_the_stated_band_takes_a_dovetail():
    # At 0.1 mm the band is 6.83 mm: stated to the nearest tenth, 6.8, the one
    # wall the brief would then ask for is the one the joint does not fit.
    stated = stated_band_mm(0.1)

    assert stated == 6.9
    split_and_join(Box(300, stated, 30), SEAM, Axis.Z, 0.1)


@pytest.mark.parametrize("clearance", CLEARANCES)
def test_a_peg_takes_the_least_spot_stated_and_nothing_smaller(clearance):
    # Put in along its length, a rod takes a peg in the middle of its round cut
    # face, as far from the edge as the rod's radius.
    spot = least_peg_spot_mm(clearance)

    split_and_join(_rod(spot + 0.02), SEAM, Axis.X, clearance)
    with pytest.raises(JoineryError, match="no place on the cut face"):
        split_and_join(_rod(spot - 0.02), SEAM, Axis.X, clearance)
