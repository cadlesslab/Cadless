"""The printer the user actually has, and saying so when a model will not fit.

Two halves of one problem. A slicer profile is only worth configuring if
something reads it, and "too big for the bed" is only worth saying if the bed is
the reader's own rather than a constant someone chose once.

The numbers in the refusal cases are the ones measured on the real failure: a
1100 x 600 x 450 mm desk against the 210 x 200 x 195 mm default bed, where
PrusaSlicer exits 0, writes nothing, and says "All objects are outside of the
print volume." on stderr.
"""

from __future__ import annotations

import pytest

from cadless import slicing, user_settings


class TestTheProfileFollowsTheSavedPrinter:
    def test_nothing_saved_slices_exactly_as_it_does_today(self):
        # The upgrade path. An install that has never opened Settings must not
        # start producing different G-code because this landed.
        assert slicing.profile_from_settings({}) == slicing.DEFAULT_PROFILE
        assert slicing.profile_from_settings(None) == slicing.DEFAULT_PROFILE

    def test_a_saved_bed_reaches_the_bed_shape(self):
        profile = slicing.profile_from_settings(
            {"printer_bed_width": 300, "printer_bed_depth": 250}
        )
        assert profile["bed-shape"] == "0x0,300x0,300x250,0x250"

    def test_a_saved_height_reaches_the_ceiling(self):
        profile = slicing.profile_from_settings({"printer_max_height": 400})
        assert profile["max-print-height"] == "400"

    def test_a_saved_nozzle_and_filament_reach_their_flags(self):
        profile = slicing.profile_from_settings(
            {"printer_nozzle_diameter": 0.6, "printer_filament_diameter": 2.85}
        )
        assert profile["nozzle-diameter"] == "0.6"
        assert profile["filament-diameter"] == "2.85"

    def test_the_first_layer_moves_with_the_temperature_it_follows(self):
        # The default profile runs the first layer hotter than the rest; saving
        # a hotter filament must keep that relationship rather than leaving the
        # first layer at the old constant.
        profile = slicing.profile_from_settings({"printer_nozzle_temperature": 240})
        assert profile["temperature"] == "240"
        assert int(profile["first-layer-temperature"]) > 240

    def test_a_saved_bed_temperature_reaches_both_flags(self):
        profile = slicing.profile_from_settings({"printer_bed_temperature": 100})
        assert profile["bed-temperature"] == "100"
        assert profile["first-layer-bed-temperature"] == "100"

    def test_every_default_key_survives_a_partial_profile(self):
        # A saved bed must not drop the temperatures. The argv's shape is what
        # keeps the output independent of which slicer build is installed.
        profile = slicing.profile_from_settings({"printer_bed_width": 300})
        assert set(profile) == set(slicing.DEFAULT_PROFILE)

    @pytest.mark.parametrize("junk", ["not a number", "", None, [], float("nan"), float("inf")])
    def test_a_corrupted_value_falls_back_rather_than_breaking_the_argv(self, junk):
        # Fail closed at the point of use. Whatever is in settings.json -- hand
        # edited, half written, written by an older build -- the command that
        # reaches a printer stays a usable one.
        profile = slicing.profile_from_settings({"printer_bed_width": junk})
        assert profile["bed-shape"] == slicing.DEFAULT_PROFILE["bed-shape"]


class TestWhetherItCouldFitAtAll:
    def default(self):
        return slicing.build_volume({})

    def test_the_default_volume_is_the_default_profile_s(self):
        volume = self.default()
        assert (volume.width, volume.depth, volume.height) == (210.0, 200.0, 195.0)

    def test_a_saved_bed_moves_the_volume(self):
        volume = slicing.build_volume(
            {"printer_bed_width": 300, "printer_bed_depth": 250, "printer_max_height": 400}
        )
        assert (volume.width, volume.depth, volume.height) == (300.0, 250.0, 400.0)

    def test_a_model_within_the_bed_is_not_refused(self):
        assert slicing.too_big_for([70.0, 70.3, 12.0], self.default()) == ""

    def test_the_measured_desk_is_refused_with_both_sets_of_numbers(self):
        # The case that produced the useless message. Both sizes belong in the
        # sentence: one of them is the thing the reader can change.
        why = slicing.too_big_for([1100.0, 600.0, 450.0], self.default())
        assert why
        assert "1100" in why and "600" in why and "450" in why
        assert "210" in why and "200" in why and "195" in why

    def test_a_model_that_would_fit_turned_is_not_refused(self):
        # This check exists to produce a better message than the slicer's, so
        # refusing something that could be printed is the worse error. On a
        # 210 x 200 bed, 195 x 205 overruns the 200 as given and clears both
        # once turned a quarter turn -- which costs the operator nothing.
        assert slicing.too_big_for([195.0, 205.0, 10.0], self.default()) == ""

    def test_a_model_that_fits_in_no_orientation_is_refused(self):
        # The other side of the same rule: 250 exceeds the longer bed axis, so
        # turning it does not help and the refusal is correct.
        assert slicing.too_big_for([250.0, 150.0, 10.0], self.default()) != ""

    def test_a_model_taller_than_the_ceiling_is_refused_whatever_its_footprint(self):
        why = slicing.too_big_for([10.0, 10.0, 500.0], self.default())
        assert why
        assert "500" in why

    def test_a_bbox_that_is_not_three_numbers_is_not_refused(self):
        # An older version row, or one that never recorded a bbox. Refusing on
        # missing information would block prints that are perfectly printable.
        for bbox in (None, [], [1.0, 2.0], ["a", "b", "c"]):
            assert slicing.too_big_for(bbox, self.default()) == ""


class TestSavingTheProfile:
    @pytest.fixture(autouse=True)
    def _isolated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(user_settings.settings, "data_dir", tmp_path)
        monkeypatch.setattr(user_settings.settings, "require_identity", False)

    def test_a_sane_profile_is_saved_and_read_back(self):
        user_settings.save({"printer_bed_width": 300.0, "printer_nozzle_diameter": 0.6})
        saved = user_settings.load()
        assert saved["printer_bed_width"] == 300.0
        assert saved["printer_nozzle_diameter"] == 0.6

    @pytest.mark.parametrize(
        "patch",
        [
            {"printer_bed_width": 0},
            {"printer_bed_depth": -1},
            {"printer_max_height": 100_000},
            {"printer_nozzle_diameter": 0.0},
            {"printer_nozzle_diameter": 50},
            {"printer_filament_diameter": 0.01},
            {"printer_nozzle_temperature": 1000},
            {"printer_bed_temperature": -5},
            {"printer_bed_width": "wide"},
            {"printer_bed_width": float("nan")},
        ],
    )
    def test_an_unusable_value_is_refused_at_save_time(self, patch):
        # Refused where it is entered, not discovered at slice time after the
        # reader has waited for a slicer to run.
        with pytest.raises(ValueError):
            user_settings.save(patch)

    def test_a_refused_patch_writes_nothing(self):
        user_settings.save({"printer_bed_width": 300.0})
        with pytest.raises(ValueError):
            user_settings.save({"printer_bed_width": 0})
        assert user_settings.load()["printer_bed_width"] == 300.0

    def test_no_profile_value_is_exported_to_the_environment(self, monkeypatch):
        # The reason these sit beside `printer_address` rather than among the
        # plain fields: `cadless/worker.py` hands its environment to generated
        # code, so anything exported there is readable by it.
        import os

        before = dict(os.environ)
        user_settings.save({"printer_bed_width": 300.0, "printer_bed_temperature": 100.0})
        leaked = [key for key in os.environ if key not in before and "PRINTER" in key.upper()]
        assert leaked == []

    def test_the_profile_can_be_cleared(self):
        user_settings.save({"printer_bed_width": 300.0})
        user_settings.clear("printer_bed_width")
        assert "printer_bed_width" not in user_settings.load()

    def test_the_status_reports_the_profile(self):
        user_settings.save({"printer_bed_width": 300.0})
        assert user_settings.status()["printer_bed_width"] == 300.0
