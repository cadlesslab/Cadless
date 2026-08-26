"""Whether a model fits the printer, and what is offered when it does not.

"Too big for the bed" is only worth saying if the bed is the reader's own rather
than a constant somebody chose once, so these run against a build volume built
from saved settings.

The numbers in the refusal cases are the ones measured on the real failure: a
1100 x 600 x 450 mm desk against the 210 x 200 x 195 mm default bed, where
PrusaSlicer exits 0, writes nothing, and says "All objects are outside of the
print volume." on stderr. The scaling cases are the shipped dining table,
1600 x 900 x 750 mm.

The last class here is about the flags a fit decision becomes, which is where
this stops being arithmetic and starts being an argv.
"""

from __future__ import annotations

from cadless import print_fit, printer_profile, slicing


class TestWhetherItCouldFitAtAll:
    def default(self):
        return printer_profile.build_volume({})

    def test_the_default_volume_is_the_default_profile_s(self):
        volume = self.default()
        assert (volume.width, volume.depth, volume.height) == (210.0, 200.0, 195.0)

    def test_a_saved_bed_moves_the_volume(self):
        volume = printer_profile.build_volume(
            {"printer_bed_width": 300, "printer_bed_depth": 250, "printer_max_height": 400}
        )
        assert (volume.width, volume.depth, volume.height) == (300.0, 250.0, 400.0)

    def test_a_model_within_the_bed_is_not_refused(self):
        assert print_fit.too_big_for([70.0, 70.3, 12.0], self.default()) == ""

    def test_the_measured_desk_is_refused_with_both_sets_of_numbers(self):
        # The case that produced the useless message. Both sizes belong in the
        # sentence: one of them is the thing the reader can change.
        why = print_fit.too_big_for([1100.0, 600.0, 450.0], self.default())
        assert why
        assert "1100" in why and "600" in why and "450" in why
        assert "210" in why and "200" in why and "195" in why

    def test_a_model_that_would_fit_turned_is_not_refused(self):
        # This check exists to produce a better message than the slicer's, so
        # refusing something that could be printed is the worse error. On a
        # 210 x 200 bed, 195 x 205 overruns the 200 as given and clears both
        # once turned a quarter turn -- which costs the operator nothing.
        assert print_fit.too_big_for([195.0, 205.0, 10.0], self.default()) == ""

    def test_a_model_that_fits_in_no_orientation_is_refused(self):
        # The other side of the same rule: 250 exceeds the longer bed axis, so
        # turning it does not help and the refusal is correct.
        assert print_fit.too_big_for([250.0, 150.0, 10.0], self.default()) != ""

    def test_a_model_taller_than_the_ceiling_is_refused_whatever_its_footprint(self):
        why = print_fit.too_big_for([10.0, 10.0, 500.0], self.default())
        assert why
        assert "500" in why

    def test_a_bbox_in_authoring_units_is_not_refused_and_that_is_known(self):
        """The gap this check cannot see, pinned so it cannot be forgotten.

        ``bbox`` is in the project's authoring units while the exported STL is
        always millimetres, so a domain authored in metres -- the shipped
        ``house`` one is -- produces numbers a thousand times too small here.
        The 12 x 8 x 0.3 in this case is the demo house's real manifest: twelve
        metres, and this returns "fits".

        It fails **open**, which is why it is recorded rather than guarded: the
        reader gets the slicer's own message instead of this one. Closing it
        needs a version-to-domain link the store does not carry.
        """
        assert print_fit.too_big_for([12.0, 8.0, 0.3], self.default()) == ""
        # The same object in millimetres is refused, which is what shows the
        # check works and the units are the whole of the gap.
        assert print_fit.too_big_for([12000.0, 8000.0, 300.0], self.default()) != ""

    def test_the_sentence_does_not_read_out_floating_point_noise(self):
        why = print_fit.too_big_for([1100.0000000000002, 600.0, 450.0], self.default())
        assert "1100 x 600 x 450" in why

    def test_a_bbox_that_is_not_three_numbers_is_not_refused(self):
        # An older version row, or one that never recorded a bbox. Refusing on
        # missing information would block prints that are perfectly printable.
        for bbox in (None, [], [1.0, 2.0], ["a", "b", "c"]):
            assert print_fit.too_big_for(bbox, self.default()) == ""


class TestOfferingToScaleItDown:
    """Turning "it will not fit" into a question.

    The numbers are the shipped dining table's, measured: 1600 x 900 x 750 mm
    against the default 210 x 200 x 195 bed.
    """

    TABLE = [1600.0, 900.0, 750.0]

    def default(self):
        return printer_profile.build_volume({})

    def test_a_model_that_fits_is_not_offered_anything(self):
        # There is no question to ask.
        assert print_fit.scale_offer([70.0, 70.0, 12.0], self.default()) is None

    def test_the_table_is_offered_a_scale_that_fits(self):
        offer = print_fit.scale_offer(self.TABLE, self.default())
        assert offer is not None
        assert 0 < offer.percent < 100
        # And what comes out is inside the bed, with room left for a skirt --
        # axis against axis, in the orientation it will be printed in, because
        # that is the comparison the slicer makes. Sorting the pair first would
        # pass a model laid the wrong way round.
        self._fits_as_it_will_be_laid(offer, print_fit.scaled_target(self.default()))

    @staticmethod
    def _fits_as_it_will_be_laid(offer, target):
        laid = (offer.size[1], offer.size[0]) if offer.turned else offer.size[:2]
        assert laid[0] <= target.width + 0.1, (offer, target)
        assert laid[1] <= target.depth + 0.1, (offer, target)
        assert offer.size[2] <= target.height + 0.1, (offer, target)

    def test_the_size_offered_is_the_size_that_gets_printed(self):
        # The slicer fits each axis against the matching one, so which way round
        # the model lies is part of the arithmetic rather than a detail left to
        # it. An offer that assumes a turn nobody takes is a number somebody is
        # shown and then does not get -- and they never find out, because the
        # confirmation afterwards reports time and grams and never dimensions.
        for bbox, saved in (
            ([1600.0, 900.0, 750.0], {}),
            ([900.0, 1600.0, 750.0], {}),
            ([100.0, 1000.0, 10.0], {"printer_bed_width": 400.0, "printer_bed_depth": 200.0}),
            ([1000.0, 100.0, 10.0], {"printer_bed_width": 200.0, "printer_bed_depth": 400.0}),
        ):
            volume = printer_profile.build_volume(saved)
            target = print_fit.scaled_target(volume)
            offer = print_fit.scale_offer(bbox, volume)
            assert offer is not None, bbox
            self._fits_as_it_will_be_laid(offer, target)

            # And it is not merely inside the bed but the size actually promised:
            # what `--scale-to-fit` will do to that box, turned first if the turn
            # is being asked for.
            width, depth, height = bbox
            if offer.turned:
                width, depth = depth, width
            factor = min(target.width / width, target.depth / depth, target.height / height)
            assert offer.percent == round(factor * 100, 1), (bbox, offer)

    def test_nothing_is_offered_when_the_answer_rounds_away(self):
        # "About 0%", or a model one of whose sides is 0 mm, is not something a
        # person can agree to -- and the button beside it starts a print.
        volume = self.default()
        for bbox in ([1600.0, 900.0, 0.2], [0.001, 0.001, 300.0], [1.0, 1.0, 1e6]):
            assert print_fit.scale_offer(bbox, volume) is None, bbox

    def test_the_target_leaves_the_bed_room_for_a_skirt(self):
        # A model scaled to the exact bed is a model whose skirt does not fit.
        volume = self.default()
        target = print_fit.scaled_target(volume)
        assert target.width < volume.width
        assert target.depth < volume.depth
        # Height needs none: nothing is drawn beside it upward.
        assert target.height == volume.height

    def test_a_bigger_printer_changes_the_offer(self):
        # The offer is about this printer, not about this model.
        small = print_fit.scale_offer(self.TABLE, self.default())
        big = print_fit.scale_offer(
            self.TABLE,
            printer_profile.build_volume(
                {"printer_bed_width": 900, "printer_bed_depth": 900, "printer_max_height": 900}
            ),
        )
        assert big is not None and small is not None
        assert big.percent > small.percent

    def test_an_unusable_bounding_box_is_offered_nothing(self):
        # The same silence `too_big_for` keeps.
        for bbox in (None, [], [1.0, 2.0], ["a", "b", "c"], [0.0, 0.0, 0.0]):
            assert print_fit.scale_offer(bbox, self.default()) is None
        # A zero side with a usable height reaches further in than the others: it
        # is the positive-number guard that has to stop it, not the unpacking.
        assert print_fit.scale_offer([0.0, 0.0, 300.0], self.default()) is None

    def test_a_model_that_only_fits_turned_is_turned(self):
        # `too_big_for` allows the quarter turn, so something has to take it --
        # otherwise the allowance is a claim about a capability nothing has.
        volume = self.default()  # 210 x 200
        assert print_fit.needs_quarter_turn([195.0, 205.0, 10.0], volume) is True
        assert print_fit.needs_quarter_turn([70.0, 70.0, 12.0], volume) is False
        assert print_fit.needs_quarter_turn(None, volume) is False


class TestWhatTheSlicerIsTold:
    def test_a_fit_target_reaches_the_argv(self):
        argv = slicing.build_command(
            "prusa-slicer",
            "m.stl",
            "out.gcode",
            printer_profile.DEFAULT_PROFILE,
            fit_to=printer_profile.BuildVolume(200.0, 190.0, 195.0),
        )
        assert "--scale-to-fit" in argv
        assert argv[argv.index("--scale-to-fit") + 1] == "200,190,195"

    def test_a_quarter_turn_reaches_the_argv(self):
        argv = slicing.build_command(
            "prusa-slicer", "m.stl", "out.gcode", printer_profile.DEFAULT_PROFILE, rotate_degrees=90
        )
        assert argv[argv.index("--rotate") + 1] == "90"

    def test_neither_appears_when_neither_was_asked_for(self):
        # An ordinary print must produce exactly the command it always did.
        argv = slicing.build_command(
            "prusa-slicer", "m.stl", "out.gcode", printer_profile.DEFAULT_PROFILE
        )
        assert "--scale-to-fit" not in argv
        assert "--rotate" not in argv

    def test_the_mesh_stays_the_final_positional(self):
        argv = slicing.build_command(
            "prusa-slicer",
            "m.stl",
            "out.gcode",
            printer_profile.DEFAULT_PROFILE,
            fit_to=printer_profile.BuildVolume(200.0, 190.0, 195.0),
            rotate_degrees=90,
        )
        assert argv[-1] == "m.stl"
