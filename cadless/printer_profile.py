"""The printer somebody actually owns, as the slicer needs to hear it.

One place for the measurements a machine is described by -- bed, nozzle,
filament, temperatures -- and for turning them into the flags PrusaSlicer takes.
Kept apart from the running of the slicer because the two answer to different
things: this changes when somebody corrects their printer's dimensions, and
:mod:`cadless.slicing` changes when the way a job is produced does.

Every value is passed explicitly. Leaning on a slicer's built-in defaults would
make the output depend on which build is installed, and a print that silently
changes with a package upgrade is worse than one that fails.

The range table here is read at both ends and for different failures.
:mod:`cadless.user_settings` refuses a value outside it at save time, so the
reader is told at the input; :func:`_number` falls back to the default for one
that got in anyway, so a hand-edited file cannot put ``1e-09`` on a command
line. Two tables would drift, and the drift would be invisible until a printer
did something odd.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The build volume assumed when the user has not said what they own, in
#: millimetres. Named rather than written into the profile string, because the
#: fit check below and the ``bed-shape`` flag have to be the same numbers -- two
#: copies would drift, and the one that drifts is the one telling somebody their
#: model does not fit.
DEFAULT_BED_WIDTH = 210.0

DEFAULT_BED_DEPTH = 200.0

DEFAULT_MAX_HEIGHT = 195.0

#: Grams per cubic centimetre of filament. PLA's figure, because that is what
#: the rest of this profile is written for.
#:
#: Without it PrusaSlicer cannot turn the length it extrudes into a weight, and
#: reports ``filament used [g] = 0.00`` — which is the number the confirmation
#: dialog has been showing since printing shipped. Measured on a washer: no
#: density gives 0.00 g, `--filament-density 1.24` gives 2.07 g.
DEFAULT_FILAMENT_DENSITY = 1.24

#: What each saved measurement has to be to be *usable*, in millimetres and
#: degrees Celsius. Not what is sensible -- what the slicer can act on.
#:
#: One table, read at both ends and for different failures. `cadless/user_settings.py`
#: refuses a value outside it at save time, so the reader is told at the input.
#: `_number` below falls back to the default for one that got in anyway, so a
#: hand-edited file cannot put `1e-09` on a command line. Two tables would drift,
#: and the drift would be invisible until a printer did something odd.
PRINTER_PROFILE_LIMITS: dict[str, tuple[float, float]] = {
    "printer_bed_width": (1.0, 2000.0),
    "printer_bed_depth": (1.0, 2000.0),
    "printer_max_height": (1.0, 2000.0),
    "printer_nozzle_diameter": (0.1, 2.0),
    "printer_filament_diameter": (0.5, 5.0),
    # Roughly PLA at the bottom of the range and a filled filament at the top;
    # anything outside is not a thermoplastic.
    "printer_filament_density": (0.5, 3.0),
    # What a full cartridge holds. Read by nothing that reaches the slicer -- it
    # is here so "needs 12 g" and "98% left" can be said in one unit, and there
    # is deliberately no default, because a guessed capacity turns a helpful
    # number into a confident claim about whether a ten-hour print will survive.
    "printer_cartridge_grams": (1.0, 10000.0),
    # The floor is the physical one: an extruder refuses to move cold, and
    # nothing extrudes near room temperature. Zero is a real answer for a bed
    # (there are printers without a heated one) and not for a nozzle.
    "printer_nozzle_temperature": (150.0, 500.0),
    "printer_bed_temperature": (0.0, 200.0),
}

#: How much hotter the first layer runs than the rest. The default profile
#: already does this (205 then 210), and saving a different filament has to move
#: the first layer with it rather than leaving it on the old constant.
FIRST_LAYER_BONUS_C = 5.0


def fmt(value: float) -> str:
    """A number as a flag value: no trailing ``.0`` on a whole one."""
    whole = int(value)
    return str(whole) if value == whole else str(value)


def _bed_shape(width: float, depth: float) -> str:
    """The four corners PrusaSlicer wants, anticlockwise from the origin."""
    return f"0x0,{fmt(width)}x0,{fmt(width)}x{fmt(depth)},0x{fmt(depth)}"


#: The print profile, as explicit flags. These are the values a first print on a
#: 210 x 200 x 195 mm FDM machine with a 0.4 mm nozzle and 1.75 mm PLA wants;
#: they are deliberately conservative rather than fast.
DEFAULT_PROFILE: dict[str, str] = {
    "layer-height": "0.2",
    "first-layer-height": "0.3",
    "nozzle-diameter": "0.4",
    "filament-diameter": "1.75",
    "filament-density": fmt(DEFAULT_FILAMENT_DENSITY),
    "temperature": "205",
    "first-layer-temperature": "210",
    "bed-temperature": "60",
    "first-layer-bed-temperature": "60",
    "fill-density": "20%",
    "perimeters": "2",
    "top-solid-layers": "4",
    "bottom-solid-layers": "3",
    "skirts": "1",
    "gcode-flavor": "marlin",
    "bed-shape": _bed_shape(DEFAULT_BED_WIDTH, DEFAULT_BED_DEPTH),
    "max-print-height": fmt(DEFAULT_MAX_HEIGHT),
}


@dataclass(frozen=True)
class BuildVolume:
    """What the printer can physically hold, in millimetres."""

    width: float
    depth: float
    height: float


def _number(saved: Mapping[str, Any] | None, field_name: str) -> float | None:
    """A saved value as a usable number, or ``None`` when it is not one.

    Fail closed at the point of use rather than trusting what was validated on
    the way in. ``settings.json`` can be hand-edited, half-written, or left by an
    older build, and the command that reaches a printer has to stay one the
    slicer can act on -- a bed of ``nan`` is not a bed, and a nozzle of ``1e-09``
    is a flag value in scientific notation.

    The range is :data:`PRINTER_PROFILE_LIMITS`, the same one the save path
    refuses on. Whether zero is allowed is a property of the field rather than of
    the caller, so it lives in that table too: a bed at 0 degrees is a printer
    without a heated bed, and a nozzle at 0 is nothing.
    """
    if not saved:
        return None
    raw = saved.get(field_name)
    # `bool` is an `int`, and `True` would otherwise become a 1 mm nozzle.
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    low, high = PRINTER_PROFILE_LIMITS[field_name]
    if not low <= value <= high:
        return None
    return value


def _or_default(value: float | None, fallback: float) -> float:
    """``value`` unless it is absent. Written out rather than ``or``.

    ``or`` would also replace a valid zero. No dimension can be zero today --
    every one has a floor above it in the table -- so this is not a bug being
    fixed but a trap being removed: the next field to allow zero would otherwise
    inherit a silent substitution nobody wrote.
    """
    return fallback if value is None else value


def build_volume(saved: Mapping[str, Any] | None = None) -> BuildVolume:
    """The printer's build volume: what the user saved, or the default."""
    return BuildVolume(
        width=_or_default(_number(saved, "printer_bed_width"), DEFAULT_BED_WIDTH),
        depth=_or_default(_number(saved, "printer_bed_depth"), DEFAULT_BED_DEPTH),
        height=_or_default(_number(saved, "printer_max_height"), DEFAULT_MAX_HEIGHT),
    )


def profile_from_settings(saved: Mapping[str, Any] | None = None) -> dict[str, str]:
    """:data:`DEFAULT_PROFILE`, with whatever the user has saved about their printer.

    Every unset value keeps today's default, which is the whole upgrade path: an
    installation that has never opened Settings goes on producing exactly the
    G-code it produced before any of this existed.
    """
    profile = dict(DEFAULT_PROFILE)
    volume = build_volume(saved)
    profile["bed-shape"] = _bed_shape(volume.width, volume.depth)
    profile["max-print-height"] = fmt(volume.height)

    nozzle = _number(saved, "printer_nozzle_diameter")
    if nozzle is not None:
        profile["nozzle-diameter"] = fmt(nozzle)
    filament = _number(saved, "printer_filament_diameter")
    if filament is not None:
        profile["filament-diameter"] = fmt(filament)
    density = _number(saved, "printer_filament_density")
    if density is not None:
        profile["filament-density"] = fmt(density)
    hot = _number(saved, "printer_nozzle_temperature")
    if hot is not None:
        profile["temperature"] = fmt(hot)
        # Capped at the same ceiling the value itself is held to. Adding the
        # bonus unconditionally put the first layer above a limit the save path
        # calls unusable, which is the guard contradicting itself one line later.
        ceiling = PRINTER_PROFILE_LIMITS["printer_nozzle_temperature"][1]
        profile["first-layer-temperature"] = fmt(min(hot + FIRST_LAYER_BONUS_C, ceiling))
    bed = _number(saved, "printer_bed_temperature")
    if bed is not None:
        profile["bed-temperature"] = fmt(bed)
        profile["first-layer-bed-temperature"] = fmt(bed)
    return profile


def cartridge_grams(saved: Mapping[str, Any] | None = None) -> float | None:
    """What a full cartridge holds, or ``None`` when nobody has said.

    ``None`` is the ordinary answer and the honest one. The printer reports how
    much is left as a *percentage* and never says of what, so without this the
    only true sentence pairs a weight with a proportion; with it both become
    grams and "this print needs more than is left" becomes sayable.
    """
    return _number(saved, "printer_cartridge_grams")
