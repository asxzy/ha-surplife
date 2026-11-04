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
        # RGBW: the fourth value is the W level, always 0-100.
        ("255,0,0,80", ((255, 0, 0), 80)),
        ("0,0,0,100", ((0, 0, 0), 100)),
        ([255, 0, 0, 40], ((255, 0, 0), 40)),
        ((1, 2, 3, 4), ((1, 2, 3), 4)),
    ],
)
def test_parse_color(token, expected) -> None:
    assert parse_color(token) == expected


@pytest.mark.parametrize("token", ["255,0,0,101", [255, 0, 0, 101], "0,0,0,255"])
def test_parse_color_rejects_a_w_level_above_100(token) -> None:
    """The W level is a percentage, not a 0-255 byte.

    Home Assistant's ``rgbw_color`` uses 0-255, so a value like 255 is a real
    mistake worth reporting rather than silently clamping to full.
    """
    with pytest.raises(ValueError, match="white level"):
        parse_color(token)


@pytest.mark.parametrize(
    "token",
    ["mauve", "#fff", "#gggggg", "1,2", "1,2,3,4,5", "1,x,3", "w", "w101", [1, 2]],
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


# -- palettes ---------------------------------------------------------------


def test_palette_accepts_names_hex_and_triples() -> None:
    """All three spellings the docs mention must work, in the same list."""
    from custom_components.surplife.protocol.spec import parse_palette

    assert parse_palette("red, #00ff00, 0,0,255") == [
        (255, 0, 0, 0),
        (0, 255, 0, 0),
        (0, 0, 255, 0),
    ]


def test_palette_keeps_the_order_it_was_given() -> None:
    """Order is the whole point: a gradient runs through the list."""
    from custom_components.surplife.protocol.spec import parse_palette

    colours = parse_palette("blue, red, green")
    assert [c[:3] for c in colours] == [(0, 0, 255), (255, 0, 0), (0, 255, 0)]


def test_palette_attaches_a_white_level_to_the_previous_colour() -> None:
    """``w<n>`` modifies the colour before it rather than adding one."""
    from custom_components.surplife.protocol.spec import parse_palette

    colours = parse_palette("#000000, w100, #0000ff")
    assert len(colours) == 2
    assert colours[0] == (0, 0, 0, 100)
    assert colours[1] == (0, 0, 255, 0)


def test_palette_refuses_more_than_the_cap() -> None:
    """A cap that silently truncates would lie about what was set."""
    from custom_components.surplife.protocol.spec import parse_palette

    with pytest.raises(ValueError, match=r"at most 2"):
        parse_palette("red, green, blue", max_colours=2)


def test_palette_refuses_an_empty_list() -> None:
    """An empty field is a mistake, not "clear the palette"."""
    from custom_components.surplife.protocol.spec import parse_palette

    with pytest.raises(ValueError, match="no colours"):
        parse_palette("   ")


def test_palette_round_trips_through_the_text_form() -> None:
    """Format then parse must return the same colours, or the entity lies."""
    from custom_components.surplife.protocol.spec import format_palette, parse_palette

    original = [(255, 0, 0, 0), (0, 255, 0, 30), (0, 0, 255, 0)]
    assert parse_palette(format_palette(original)) == original


def test_format_palette_omits_a_zero_white_level() -> None:
    """The white channel is only mentioned when it is doing something."""
    from custom_components.surplife.protocol.spec import format_palette

    assert format_palette([(255, 0, 0, 0)]) == "#ff0000"
    assert format_palette([(255, 0, 0, 100)]) == "#ff0000, w100"
