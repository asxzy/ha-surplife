"""Tests for the shared text-form parsers.

``protocol/spec.py`` is the single implementation behind both the Home
Assistant services and the standalone CLI, so these tests are the contract for
both surfaces.
"""

from __future__ import annotations

import pytest

from custom_components.surplife.protocol.constants import (
    REPEAT_EVERYDAY,
    REPEAT_WEEKDAYS,
    REPEAT_WEEKEND,
)
from custom_components.surplife.protocol.spec import (
    format_repeat,
    parse_color,
    parse_led_spec,
    parse_repeat,
)

# -- colours ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("red", ((255, 0, 0), None)),
        ("Green", ((0, 255, 0), None)),
        ("  blue  ", ((0, 0, 255), None)),
        ("warm_white", ((255, 200, 120), None)),
        ("#ff8000", ((255, 128, 0), None)),
        ("12,34,56", ((12, 34, 56), None)),
        ("w80", (None, 80)),
        ("w0", (None, 0)),
        ("off", (None, None)),
        ("black", (None, None)),
        ("none", (None, None)),
        ([1, 2, 3], ((1, 2, 3), None)),
        ((9, 8, 7), ((9, 8, 7), None)),
    ],
)
def test_parse_color(token, expected) -> None:
    assert parse_color(token) == expected


@pytest.mark.parametrize(
    "token",
    ["mauve", "#fff", "#gggggg", "1,2", "1,2,3,4", "1,x,3", "w", "w101", [1, 2]],
)
def test_parse_color_rejects(token) -> None:
    with pytest.raises(ValueError):
        parse_color(token)


# -- per-LED patterns ------------------------------------------------------


def test_pattern_pads_with_dark_leds() -> None:
    """Unmentioned LEDs must be covered, or the framebuffer keeps old values."""
    leds = parse_led_spec("10xred", 35)
    assert len(leds) == 35
    assert all(led.rgb == (255, 0, 0) for led in leds[:10])
    assert all(led.is_off for led in leds[10:])


def test_pattern_runs_and_singles() -> None:
    leds = parse_led_spec("2xred green", 35)
    assert [led.rgb for led in leds[:4]] == [
        (255, 0, 0),
        (255, 0, 0),
        (0, 255, 0),
        None,
    ]


def test_pattern_blink_modes() -> None:
    leds = parse_led_spec("5xred/fast 5xblue/slow 5xgreen", 35)
    assert leds[0].blink_mode == 0x10
    assert leds[5].blink_mode == 0x08
    assert leds[10].blink_mode == 0x00


def test_pattern_blink_speed() -> None:
    leds = parse_led_spec("3xred/slow/25", 35)
    assert leds[0].blink_speed == 25
    assert leds[0].blink_mode == 0x08


def test_pattern_white_run() -> None:
    leds = parse_led_spec("35xw80", 35)
    assert all(led.white == 80 for led in leds)
    assert all(led.rgb is None for led in leds)


def test_pattern_skips_global_options() -> None:
    """``key=value`` tokens are consumed by the caller, not the LED list."""
    leds = parse_led_spec("density=20 10xred", 35)
    assert leds[0].rgb == (255, 0, 0)
    assert len(leds) == 35


def test_pattern_exactly_full() -> None:
    leds = parse_led_spec("35xred", 35)
    assert len(leds) == 35
    assert all(led.rgb == (255, 0, 0) for led in leds)


@pytest.mark.parametrize(
    "spec",
    [
        "40xred",
        "",
        "0xred",
        "10xmauve",
        "10xred/strobe",
        "red 40xblue",
    ],
)
def test_pattern_rejects(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_led_spec(spec, 35)


def test_pattern_error_names_the_counts() -> None:
    """The message must tell the user the strip length, not just \"too long\"."""
    with pytest.raises(ValueError, match="40.*35"):
        parse_led_spec("40xred", 35)


# -- repeat masks ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "mask"),
    [
        ("once", 0),
        ("none", 0),
        ("", 0),
        ("everyday", REPEAT_EVERYDAY),
        ("weekdays", REPEAT_WEEKDAYS),
        ("weekend", REPEAT_WEEKEND),
        ("mon", 0x02),
        ("mon,wed,fri", 0x02 | 0x08 | 0x20),
        ("Mon, Wed, Fri", 0x02 | 0x08 | 0x20),
    ],
)
def test_parse_repeat(text: str, mask: int) -> None:
    assert parse_repeat(text) == mask


@pytest.mark.parametrize("text", ["monday", "mon,funday", "1,2"])
def test_parse_repeat_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_repeat(text)


@pytest.mark.parametrize(
    "mask",
    [0, REPEAT_EVERYDAY, REPEAT_WEEKDAYS, REPEAT_WEEKEND, 0x02 | 0x08 | 0x20],
)
def test_format_repeat_round_trips(mask: int) -> None:
    assert parse_repeat(format_repeat(mask)) == mask
