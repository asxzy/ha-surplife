"""Golden-packet tests for every command builder.

Each test asserts the builder produces the exact bytes captured from the
device. These are the tests that catch a regression in byte layout.
"""

from __future__ import annotations

import datetime as dt

import pytest

from custom_components.surplife.protocol import commands, framing
from custom_components.surplife.protocol.constants import (
    WRITE_ALL,
    WRITE_COLOR,
    WRITE_WHITE,
)

from . import golden

# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------


def test_power_on() -> None:
    assert commands.power(True) == golden.POWER_ON_INNER


def test_power_off() -> None:
    assert commands.power(False) == golden.POWER_OFF_INNER


# ---------------------------------------------------------------------------
# Colour and white
# ---------------------------------------------------------------------------


def test_color_red() -> None:
    assert commands.color(255, 0, 0) == golden.COLOR_RED_INNER


@pytest.mark.parametrize(
    ("rgb", "expected"),
    [
        ((255, 0, 0), (0, 100, 100)),
        ((0, 255, 0), (60, 100, 100)),
        ((0, 0, 255), (120, 100, 100)),
        ((255, 255, 0), (30, 100, 100)),
        ((0, 0, 0), (0, 0, 0)),
        ((255, 255, 255), (0, 0, 100)),
    ],
)
def test_rgb_to_hsv_bytes(
    rgb: tuple[int, int, int], expected: tuple[int, int, int]
) -> None:
    assert commands.rgb_to_hsv_bytes(*rgb) == expected


def test_hsv_roundtrip_is_stable() -> None:
    """Converting to the wire format and back returns the same colour."""
    for rgb in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 128, 128)]:
        h2, s, v = commands.rgb_to_hsv_bytes(*rgb)
        back = commands.hsv_to_rgb(h2 * 2, s, v)
        assert all(abs(a - b) <= 2 for a, b in zip(rgb, back, strict=True))


def test_warm_white() -> None:
    assert commands.warm_white(100) == golden.WARM_WHITE_100_INNER


def test_warm_white_uses_white_write_mode() -> None:
    assert commands.warm_white(50)[3] == 0xB1


def test_color_uses_colour_write_mode() -> None:
    assert commands.color(1, 2, 3)[3] == 0xA1


def test_levels_white_only_leaves_colour_channels_dark() -> None:
    """``levels(white=...)`` is the white LED, not an RGB imitation.

    This is the distinction the UI needs: a white request must not brighten
    three colour LEDs. Only the W byte carries a value.
    """
    frame = commands.levels(white=100, write_mode=WRITE_ALL)
    assert frame[3] == WRITE_ALL
    assert frame[4:7] == b"\x00\x00\x00"  # hue, saturation, value
    assert frame[8] == 100  # W level


def test_levels_rgbw_sets_both_channels() -> None:
    frame = commands.levels(red=255, green=0, blue=0, white=80, write_mode=WRITE_ALL)
    assert frame[3] == WRITE_ALL
    assert frame[4:7] == bytes([0, 100, 100])  # red as H/2 S V
    assert frame[8] == 80


def test_levels_infers_all_when_mixing() -> None:
    """A colour plus white with no explicit mode is a mix, so write both."""
    assert commands.levels(red=1, green=2, blue=3, white=10)[3] == WRITE_ALL
    assert commands.levels(red=1, green=2, blue=3)[3] == WRITE_COLOR
    assert commands.levels(white=10)[3] == WRITE_WHITE


def test_color_and_warm_white_are_the_shortcuts() -> None:
    """The single-channel helpers must produce the same bytes as before."""
    assert commands.color(255, 0, 0) == commands.levels(
        red=255, green=0, blue=0, write_mode=WRITE_COLOR
    )
    assert commands.warm_white(100) == commands.levels(
        white=100, write_mode=WRITE_WHITE
    )


@pytest.mark.parametrize("level", [-1, 101])
def test_warm_white_rejects_out_of_range(level: int) -> None:
    with pytest.raises(ValueError, match="white level"):
        commands.warm_white(level)


# ---------------------------------------------------------------------------
# E1 21 effects
# ---------------------------------------------------------------------------


def test_effect_wave_red() -> None:
    assert commands.effect(1, [(255, 0, 0)], speed=50, density=50) == (
        golden.EFFECT_WAVE_RED_INNER
    )


def test_effect_frame_wraps_correctly() -> None:
    inner = commands.effect(1, [(255, 0, 0)], speed=50, density=50)
    assert framing.wrap(inner, 0x02, 0x01) == golden.EFFECT_WAVE_RED_FRAME


def test_effect_layout_offsets() -> None:
    """Pin the header offsets documented in docs/protocol_0xB6.md."""
    msg = commands.effect(0x15, [(255, 0, 0)], speed=60, density=40, direction=2, option=1)
    assert msg[0:2] == b"\xe1\x21"
    assert msg[2] == 0x01
    assert msg[3] == 0x64
    assert msg[4] == 0x15  # pattern id
    assert msg[5] == 0x01  # option
    assert msg[6] == 0x02  # direction
    assert msg[7] == 40  # density
    assert msg[8] == 60  # speed
    assert msg[15] == 1  # colour count
    assert len(msg) == 16 + 5


def test_effect_defaults_to_one_white_colour() -> None:
    msg = commands.effect(1)
    assert msg[15] == 1
    assert msg[16:21] == bytes([0, 0, 100, 0, 0])


def test_effect_brightness_scales_value() -> None:
    full = commands.effect(1, [(255, 0, 0)], brightness=100)
    half = commands.effect(1, [(255, 0, 0)], brightness=50)
    assert full[18] == 100
    assert half[18] == 50


def test_effect_accepts_eight_colours() -> None:
    msg = commands.effect(1, [(i, i, i) for i in range(8)])
    assert msg[15] == 8
    assert len(msg) == 16 + 8 * 5


def test_effect_rejects_nine_colours() -> None:
    with pytest.raises(ValueError, match="1..8"):
        commands.effect(1, [(0, 0, 0)] * 9)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"speed": 101}, "speed"),
        ({"density": -1}, "density"),
        ({"direction": 3}, "direction"),
        ({"option": 3}, "option"),
        ({"brightness": 101}, "brightness"),
    ],
)
def test_effect_validates_ranges(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        commands.effect(1, [(255, 0, 0)], **kwargs)


def test_effect_rejects_zero_id() -> None:
    """E1 21 pattern ids start at 1; 0 means "no effect" in the state frame."""
    with pytest.raises(ValueError, match="effect id"):
        commands.effect(0)


# ---------------------------------------------------------------------------
# E1 22 segments
# ---------------------------------------------------------------------------


def test_segments_rgb() -> None:
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)] * 7
    assert commands.segments(colors[:20]) == golden.SEGMENTS_RGB_INNER


def test_segments_always_twenty_slots() -> None:
    """Fewer than 20 colours still produces a 20-slot payload."""
    msg = commands.segments([(255, 0, 0)])
    assert msg[6] == 20
    assert len(msg) == 7 + 20 * 5


def test_segments_pads_with_off() -> None:
    msg = commands.segments([(255, 0, 0)])
    assert msg[12:] == bytes(19 * 5)


def test_segments_accepts_none_for_off() -> None:
    msg = commands.segments([(255, 0, 0), None, (0, 0, 0)])
    assert msg[7:12] == bytes([0, 100, 100, 0, 0])
    assert msg[12:17] == bytes(5)
    assert msg[17:22] == bytes(5)


def test_segments_rejects_more_than_twenty() -> None:
    with pytest.raises(ValueError, match="20 segments"):
        commands.segments([(1, 2, 3)] * 21)


# ---------------------------------------------------------------------------
# E1 23 / E1 26 scribble
# ---------------------------------------------------------------------------


def test_scribble_init_35() -> None:
    assert commands.scribble_init(35) == golden.SCRIBBLE_INIT_35_INNER


def test_scribble_init_length() -> None:
    """Inner length is 9 + 7*N, matching the documented formula."""
    assert len(commands.scribble_init(35)) == 9 + 7 * 35


def test_scribble_paint_all_red() -> None:
    assert (
        commands.scribble_paint(led_count=35, indices=range(35), red=255)
        == golden.SCRIBBLE_PAINT_ALL_RED_INNER
    )


def test_scribble_paint_frame() -> None:
    inner = commands.scribble_paint(led_count=35, indices=range(35), red=255)
    assert framing.wrap(inner, 0x02, 0x04) == golden.SCRIBBLE_PAINT_ALL_RED_FRAME


def test_bitmap_all_on_35_leds() -> None:
    """35 LEDs need 5 bytes; the final byte's low 5 bits stay clear."""
    assert commands.bitmap(range(35), 35) == bytes.fromhex("ffffffffe0")


def test_bitmap_is_msb_first() -> None:
    assert commands.bitmap([0], 8) == b"\x80"
    assert commands.bitmap([7], 8) == b"\x01"
    assert commands.bitmap([8], 16) == b"\x00\x80"


def test_bitmap_empty() -> None:
    assert commands.bitmap([], 35) == bytes(5)


def test_bitmap_rejects_out_of_range_index() -> None:
    with pytest.raises(ValueError, match="out of range"):
        commands.bitmap([35], 35)


def test_bitmap_scales_with_led_count() -> None:
    """The same indices produce different bitmap lengths for other counts."""
    assert len(commands.bitmap([0], 80)) == 10
    assert len(commands.bitmap([0], 100)) == 13


def test_scribble_paint_white_mode_zeroes_colour() -> None:
    msg = commands.scribble_paint(led_count=8, indices=[0], white=80)
    assert msg[7:10] == bytes([0, 0, 0])  # H, S, V all zero
    assert msg[11] == 80  # white level


def test_scribble_paint_colour_mode_zeroes_white() -> None:
    msg = commands.scribble_paint(led_count=8, indices=[0], red=255)
    assert msg[11] == 0  # white level
    assert msg[7:10] == bytes([0, 100, 100])


def test_scribble_paint_off_is_all_zero() -> None:
    """An off LED is a paint with colour, white and blink all zero.

    The blink-speed byte is left at its default so the paint is a normal
    static one; only the colour and white fields carry the "off" meaning.
    """
    msg = commands.scribble_paint(led_count=8, indices=[0], blink_speed=0)
    assert msg[6] == 0  # blink mode
    assert msg[7:13] == bytes(6)  # H, S, V, pad, white, blink speed


def test_scribble_paint_layout_offsets() -> None:
    msg = commands.scribble_paint(
        led_count=8,
        indices=[0],
        red=255,
        effect_id=1,
        direction=2,
        density=40,
        speed=60,
        blink_mode=0x10,
        blink_speed=80,
    )
    assert msg[0:2] == b"\xe1\x26"
    assert msg[2] == 1  # effect
    assert msg[3] == 2  # direction
    assert msg[4] == 40  # density
    assert msg[5] == 60  # speed
    assert msg[6] == 0x10  # blink mode
    assert msg[12] == 80  # blink speed
    assert msg[13] == 0x80  # bitmap


def test_scribble_paint_rejects_bad_effect() -> None:
    with pytest.raises(ValueError, match="scribble effect"):
        commands.scribble_paint(led_count=8, indices=[0], effect_id=9)


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


def test_get_time() -> None:
    assert commands.get_time() == golden.GET_TIME_INNER


def test_set_time() -> None:
    when = dt.datetime(2026, 11, 2, 15, 40, 0)
    assert commands.set_time(when) == golden.SET_TIME_INNER


def test_set_time_encodes_weekday() -> None:
    """2026-11-02 is a Monday, which the device expects as isoweekday 1."""
    assert commands.set_time(dt.datetime(2026, 11, 2, 0, 0))[8] == 1


# ---------------------------------------------------------------------------
# Timers
# ---------------------------------------------------------------------------


def test_get_timers() -> None:
    assert commands.get_timers() == golden.GET_TIMERS_INNER


def test_set_timer_record_length() -> None:
    """The record is exactly 20 bytes after the slot byte."""
    msg = commands.set_timer(slot=3, hour=12, minute=30)
    assert len(msg) == 3 + 20
    assert msg[0:3] == bytes([0xE0, 0x05, 3])


def test_set_timer_active_flags() -> None:
    msg = commands.set_timer(slot=1, hour=20, minute=0, repeat_mask=0xFE)
    assert msg[3] == 0xF0  # active flag
    assert msg[4] == 20  # hour
    assert msg[5] == 0  # minute
    assert msg[7] == 0xFE  # repeat mask


def test_set_timer_inactive_is_all_zero() -> None:
    msg = commands.set_timer(slot=4, active=False)
    assert msg[3:] == bytes(20)


def test_set_timer_colour_action_encodes_hsv() -> None:
    msg = commands.set_timer(slot=2, hour=18, minute=30, action=0xA1, color=(255, 0, 0))
    assert msg[12] == 0xA1  # action byte at record offset 9
    assert msg[13:16] == bytes([0, 100, 100])


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"slot": 0}, "slot"),
        ({"slot": 7}, "slot"),
        ({"slot": 1, "hour": 24}, "hour"),
        ({"slot": 1, "minute": 60}, "minute"),
    ],
)
def test_set_timer_validates_ranges(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        commands.set_timer(**kwargs)
