"""Tests for the per-effect capability model.

The value of this module is that the UI asks for the right controls, so the
tests pin the classifications that are *evidenced* rather than the ones that
would merely be convenient. Where the evidence is weak the test says so: the
protocol accepts up to eight colours for every effect and :func:`palette_for`
must not silently discard them on the strength of an inference.
"""

from __future__ import annotations

import pytest

from custom_components.surplife.effect_profile import (
    MAX_PROTOCOL_COLOURS,
    PaletteMode,
    palette_for,
    panel_colours,
    profile_for,
)
from custom_components.surplife.protocol.constants import EFFECT_NAMES

#: The plain/``Colorful`` pairs the app ships, which is the evidence that the
#: plain version is the single-colour one.
SINGLE_COLOUR = [
    "Wave",
    "Meteor",
    "Horse Racing",
    "Breathe",
    "Jump",
    "Strobe",
    "Twinkling Stars",
    "Static Fill",
]


@pytest.mark.parametrize("name", SINGLE_COLOUR)
def test_single_colour_effects_ask_for_one_picker(name: str) -> None:
    profile = profile_for(name)
    assert profile.palette is PaletteMode.SINGLE
    assert profile.panel_colours == 1


def test_every_effect_has_a_profile() -> None:
    """An effect with no profile would render a panel with no controls."""
    for name in EFFECT_NAMES.values():
        profile = profile_for(name)
        assert profile.description, f"{name} has no description"
        assert profile.panel_colours >= 0


def test_rainbow_bridge_takes_no_colour() -> None:
    """It generates its own colours, so a picker would be a lie."""
    profile = profile_for("Rainbow Bridge")
    assert profile.palette is PaletteMode.GENERATED
    assert profile.panel_colours == 0


def test_gradient_effects_offer_a_useful_number_of_stops() -> None:
    """A gradient's whole point is multiple stops, but not seventeen controls.

    The panel is a phone form; offering the protocol's full eight slots plus
    eight white sliders is a wall of fields. Four stops is the useful number,
    and ``palette_for`` still carries all eight for automations.
    """
    from custom_components.surplife.effect_profile import GRADIENT_STOPS

    assert GRADIENT_STOPS == 4
    for name in ("Static Gradient", "Gradient Meteor", "Gradient Overlay"):
        assert profile_for(name).panel_colours == GRADIENT_STOPS


def test_the_panel_offers_fewer_colours_than_the_wire_allows() -> None:
    """The UI limit and the wire limit are deliberately different numbers."""
    for name in EFFECT_NAMES.values():
        assert profile_for(name).panel_colours <= MAX_PROTOCOL_COLOURS


def test_unknown_effect_gets_a_permissive_profile() -> None:
    """An unrecognised name still gets controls rather than none."""
    profile = profile_for("Hyperdrive")
    assert profile.palette is PaletteMode.COLOURS
    assert profile.panel_colours > 0


@pytest.mark.parametrize("name", ["Wave", "static fill", "STROBE", "  Chase  "])
def test_lookup_is_case_and_space_insensitive(name: str) -> None:
    """The effect list has mixed casing, so lookups must be tolerant."""
    assert profile_for(name).description


def test_static_effects_have_no_direction_or_density() -> None:
    """There is nothing to reverse or repeat in a still image."""
    for name in ("Static Gradient", "Static Fill"):
        profile = profile_for(name)
        assert profile.direction is False
        assert profile.density is False


def test_strobe_ignores_density_but_not_speed() -> None:
    profile = profile_for("Strobe")
    assert profile.density is False
    assert profile.speed is True


# -- palette handling ------------------------------------------------------


def test_palette_is_not_trimmed_to_the_panel_hint() -> None:
    """The panel hint is a suggestion; the wire allows eight colours.

    Trimming here would silently change an automation's command based on an
    inference about what the firmware uses, which is worse than sending a
    colour the effect may ignore.
    """
    colours = [(index, index, index) for index in range(5)]
    assert palette_for("Wave", colours) == colours


def test_palette_is_clamped_to_the_protocol_limit() -> None:
    colours = [(1, 1, 1)] * (MAX_PROTOCOL_COLOURS + 3)
    assert len(palette_for("Chase", colours)) == MAX_PROTOCOL_COLOURS


def test_generated_effect_sends_one_colour() -> None:
    """The wire still needs a valid count, even when it is ignored."""
    colours = [(1, 2, 3), (4, 5, 6)]
    assert palette_for("Rainbow Bridge", colours) == [(1, 2, 3)]


@pytest.mark.parametrize("name", ["Wave", "Rainbow Bridge"])
def test_empty_palette_never_reaches_the_wire(name: str) -> None:
    """An empty palette would be rejected by the protocol packer."""
    assert len(palette_for(name, [])) == 1


def test_panel_colours_helper_matches_the_profile() -> None:
    assert panel_colours("Wave") == profile_for("Wave").panel_colours


def test_profiles_are_frozen() -> None:
    """Profiles are shared constants, so a caller must not be able to edit one."""
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        profile_for("Wave").panel_colours = 99  # type: ignore[misc]
