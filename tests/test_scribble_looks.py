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
