"""Tests for per-LED grouping and bitmap sizing."""

from __future__ import annotations

import pytest

from custom_components.surplife.protocol import scribble
from custom_components.surplife.protocol.constants import BLINK_FAST, BLINK_NONE

RED = (255, 0, 0)
BLUE = (0, 0, 255)


def test_group_merges_identical_leds() -> None:
    leds = [scribble.ScribbleLED(RED)] * 20 + [scribble.ScribbleLED(BLUE)] * 15
    groups = scribble.group_leds(leds)
    assert len(groups) == 2
    assert groups[0].leds.rgb == RED
    assert groups[0].indices == tuple(range(20))
    assert groups[1].leds.rgb == BLUE
    assert groups[1].indices == tuple(range(20, 35))


def test_group_keeps_blink_settings_separate() -> None:
    """LEDs differing only in blink mode cannot share a paint."""
    leds = [
        scribble.ScribbleLED(RED, blink_mode=BLINK_NONE),
        scribble.ScribbleLED(RED, blink_mode=BLINK_FAST),
    ]
    groups = scribble.group_leds(leds)
    assert len(groups) == 2


def test_group_keeps_blink_speed_separate() -> None:
    leds = [
        scribble.ScribbleLED(RED, blink_mode=BLINK_FAST, blink_speed=50),
        scribble.ScribbleLED(RED, blink_mode=BLINK_FAST, blink_speed=80),
    ]
    assert len(scribble.group_leds(leds)) == 2


def test_group_keeps_colour_and_white_separate() -> None:
    leds = [scribble.ScribbleLED(RED), scribble.ScribbleLED(white=80)]
    assert len(scribble.group_leds(leds)) == 2


def test_group_covers_every_led_exactly_once() -> None:
    """Every LED must be painted, or the result depends on prior state."""
    leds = [
        scribble.ScribbleLED(RED),
        scribble.ScribbleLED(),
        scribble.ScribbleLED(BLUE),
        scribble.ScribbleLED(white=50),
    ] * 3
    groups = scribble.group_leds(leds)
    covered = [index for group in groups for index in group.indices]
    assert sorted(covered) == list(range(len(leds)))
    assert len(covered) == len(set(covered))


def test_group_order_is_stable_by_first_index() -> None:
    leds = [scribble.ScribbleLED(BLUE), scribble.ScribbleLED(RED)]
    groups = scribble.group_leds(leds)
    assert groups[0].leds.rgb == BLUE
    assert groups[1].leds.rgb == RED


def test_group_empty_input() -> None:
    assert scribble.group_leds([]) == []


def test_led_is_off() -> None:
    assert scribble.ScribbleLED().is_off is True
    assert scribble.ScribbleLED(RED).is_off is False
    assert scribble.ScribbleLED(white=50).is_off is False
    assert scribble.ScribbleLED(white=0).is_off is True


def test_normalize_pads_with_dark_leds() -> None:
    result = scribble.normalize([scribble.ScribbleLED(RED)], 35)
    assert len(result) == 35
    assert result[0].rgb == RED
    assert all(led.is_off for led in result[1:])


def test_normalize_passes_through_exact_length() -> None:
    leds = [scribble.ScribbleLED(RED)] * 35
    assert scribble.normalize(leds, 35) == leds


def test_normalize_rejects_overflow() -> None:
    with pytest.raises(ValueError, match="35"):
        scribble.normalize([scribble.ScribbleLED(RED)] * 36, 35)


def test_blink_name_maps() -> None:
    assert scribble.BLINK_NAME_TO_MODE["fast"] == BLINK_FAST
    assert scribble.BLINK_MODE_TO_NAME[BLINK_FAST] == "fast"
    assert scribble.BLINK_MODE_TO_NAME[BLINK_NONE] == "none"
