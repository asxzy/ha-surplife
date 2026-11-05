"""Tests for the named per-LED looks.

A look's whole value is that it expands to a specific, reproducible set of LEDs,
so these tests assert exact output rather than vague properties.
"""

from __future__ import annotations

import pytest

from custom_components.surplife.protocol.constants import (
    BLINK_FAST,
    BLINK_NONE,
    BLINK_SLOW,
)
from custom_components.surplife.scribble_looks import (
    LOOKS,
    RED,
    WHITE,
    Look,
    build_look,
    build_runs,
)
from custom_components.surplife.settings import (
    ScribbleRun,
    scribble_runs,
    with_scribble_runs,
)

LED_COUNT = 35


@pytest.mark.parametrize("name", sorted(LOOKS))
def test_every_look_builds_exact_count(name: str) -> None:
    leds = build_look(name, LED_COUNT)
    assert len(leds) == LED_COUNT


@pytest.mark.parametrize("name", sorted(LOOKS))
def test_every_look_is_deterministic(name: str) -> None:
    assert build_look(name, LED_COUNT) == build_look(name, LED_COUNT)


@pytest.mark.parametrize("count", [1, 2, 7, 80, 100])
@pytest.mark.parametrize("name", sorted(LOOKS))
def test_every_look_handles_any_length(name: str, count: int) -> None:
    assert len(build_look(name, count)) == count


@pytest.mark.parametrize("name", sorted(LOOKS))
def test_look_rejects_zero_leds(name: str) -> None:
    with pytest.raises(ValueError, match="positive"):
        build_look(name, 0)


def test_unknown_look_lists_alternatives() -> None:
    with pytest.raises(ValueError, match="available:"):
        build_look("plaid", LED_COUNT)


def test_solid_fills_everything_one_colour() -> None:
    leds = build_look("solid", LED_COUNT, [(1, 2, 3)])
    assert {led.rgb for led in leds} == {(1, 2, 3)}
    assert all(led.blink_mode == BLINK_NONE for led in leds)


def test_stripes_alternate_in_blocks_of_three() -> None:
    leds = build_look("stripes", 6, [RED, WHITE])
    assert [led.rgb for led in leds] == [RED, RED, RED, WHITE, WHITE, WHITE]


def test_stripes_cycle_palette() -> None:
    leds = build_look("stripes", 9, [RED, WHITE, (0, 0, 255)])
    assert [led.rgb for led in leds] == [
        RED, RED, RED,
        WHITE, WHITE, WHITE,
        (0, 0, 255), (0, 0, 255), (0, 0, 255),
    ]


def test_gradient_endpoints_are_the_palette() -> None:
    leds = build_look("gradient", 5, [(0, 0, 0), (255, 255, 255)])
    assert leds[0].rgb == (0, 0, 0)
    assert leds[-1].rgb == (255, 255, 255)
    # Monotone interpolation, no overshoot.
    values = [led.rgb[0] for led in leds]
    assert values == sorted(values)


def test_gradient_single_colour_is_solid() -> None:
    leds = build_look("gradient", 4, [(9, 9, 9)])
    assert {led.rgb for led in leds} == {(9, 9, 9)}


def test_rainbow_sweeps_hues_and_ignores_palette() -> None:
    a = build_look("rainbow", 8)
    b = build_look("rainbow", 8, [RED])
    assert a == b
    assert a[0].rgb != a[4].rgb


def test_rainbow_starts_at_red() -> None:
    assert build_look("rainbow", 5)[0].rgb == (255, 0, 0)


def test_firefly_is_mostly_dark_but_not_all_dark() -> None:
    leds = build_look("firefly", LED_COUNT, [(255, 200, 0)])
    lit = [led for led in leds if led.rgb is not None]
    assert lit, "the scatter should light something"
    assert len(lit) < LED_COUNT, "the scatter should leave gaps"
    assert all(led.blink_mode == BLINK_SLOW for led in lit)


def test_firefly_off_leds_have_no_colour() -> None:
    leds = build_look("firefly", LED_COUNT)
    for led in leds:
        if led.rgb is None:
            assert led.blink_mode == BLINK_NONE


def test_twinkle_blinks_every_third_led_fast() -> None:
    leds = build_look("twinkle", 9, [WHITE, WHITE])
    assert [led.blink_mode for led in leds] == [
        BLINK_FAST, BLINK_SLOW, BLINK_SLOW,
        BLINK_FAST, BLINK_SLOW, BLINK_SLOW,
        BLINK_FAST, BLINK_SLOW, BLINK_SLOW,
    ]


def test_chase_lights_a_leading_block_only() -> None:
    leds = build_look("chase", LED_COUNT, [(0, 80, 255)])
    lit = [led for led in leds if led.rgb is not None]
    assert 3 <= len(lit) < LED_COUNT
    assert all(led.rgb == (0, 80, 255) for led in lit)
    assert all(led.blink_mode == BLINK_FAST for led in lit)


def test_wreath_alternates_lit_and_dark() -> None:
    leds = build_look("wreath", 6, [(0, 200, 60), (255, 0, 0)])
    assert [led.rgb is not None for led in leds] == [True, False] * 3


def test_candy_cane_is_two_colour_banding() -> None:
    leds = build_look("candy_cane", 8, [RED, WHITE])
    colours = [led.rgb for led in leds]
    assert set(colours) == {RED, WHITE}
    assert colours != [RED, RED, RED, RED, WHITE, WHITE, WHITE, WHITE]


def test_palette_override_is_used() -> None:
    leds = build_look("solid", 3, [(7, 8, 9)])
    assert all(led.rgb == (7, 8, 9) for led in leds)


def test_empty_palette_falls_back_to_default() -> None:
    look = LOOKS["solid"]
    leds = look.build(3, [])
    assert all(led.rgb == look.palette[0] for led in leds)


def test_look_has_all_metadata() -> None:
    for look in LOOKS.values():
        assert isinstance(look, Look)
        assert look.name in LOOKS
        assert look.title and look.description
        assert look.palette


def test_look_names_are_slug_shaped() -> None:
    for name in LOOKS:
        assert name == name.lower()
        assert " " not in name


# -- hand-painted runs -----------------------------------------------------


def test_runs_are_laid_end_to_end() -> None:
    """A run says "the next n LEDs"; the runs concatenate in order."""
    leds = build_runs(
        (
            ScribbleRun(length=3, colour=(255, 0, 0, 0)),
            ScribbleRun(length=2, colour=(0, 0, 255, 0)),
        ),
        5,
    )
    assert [led.rgb for led in leds] == [
        (255, 0, 0),
        (255, 0, 0),
        (255, 0, 0),
        (0, 0, 255),
        (0, 0, 255),
    ]


def test_a_short_pattern_is_padded_with_dark_leds() -> None:
    """The device keeps a framebuffer, so unmentioned LEDs must be cleared."""
    leds = build_runs((ScribbleRun(length=2, colour=(255, 0, 0, 0)),), 6)
    assert len(leds) == 6
    assert all(led.is_off for led in leds[2:])


def test_a_run_longer_than_the_strip_is_trimmed() -> None:
    """Shrinking the strip must not produce more LEDs than it has."""
    leds = build_runs((ScribbleRun(length=99, colour=(255, 0, 0, 0)),), 4)
    assert len(leds) == 4
    assert all(led.rgb == (255, 0, 0) for led in leds)


def test_a_run_with_no_colour_is_dark_not_white() -> None:
    """An empty run means "off", not "white" and not "black"."""
    leds = build_runs((ScribbleRun(length=2),), 2)
    assert all(led.is_off for led in leds)


def test_a_white_only_run_uses_the_white_channel() -> None:
    """A real white is a W-channel request, never three RGB channels."""
    leds = build_runs((ScribbleRun(length=1, colour=(0, 0, 0, 80)),), 1)
    assert leds[0].rgb is None
    assert leds[0].white == 80


def test_a_mixed_run_keeps_both_channels() -> None:
    leds = build_runs((ScribbleRun(length=1, colour=(255, 0, 0, 40)),), 1)
    assert leds[0].rgb == (255, 0, 0)
    assert leds[0].white == 40


def test_blink_reaches_every_led_in_the_run() -> None:
    leds = build_runs(
        (ScribbleRun(length=3, colour=(255, 0, 0, 0), blink="slow"),), 3
    )
    assert all(led.blink_mode == BLINK_SLOW for led in leds)


def test_runs_round_trip_through_the_options() -> None:
    """The editor re-renders from stored options, so this must be lossless."""
    runs = (
        ScribbleRun(length=10, colour=(255, 0, 0, 0)),
        ScribbleRun(length=5, colour=(0, 0, 0, 80), blink="fast"),
        ScribbleRun(length=20),
    )
    assert scribble_runs(with_scribble_runs({}, runs)) == runs


def test_stored_runs_survive_a_corrupt_length() -> None:
    """Options are hand-editable, so one bad row must not break the others."""
    options = with_scribble_runs(
        {}, (ScribbleRun(length=4, colour=(1, 2, 3, 0)),)
    )
    options["scribble"]["runs"].append({"length": "nonsense"})
    assert len(scribble_runs(options)) == 1


def test_no_stored_runs_means_never_painted() -> None:
    assert scribble_runs({}) == ()
    assert scribble_runs(None) == ()
