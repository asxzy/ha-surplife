"""Tests for state frame parsing against captured frames."""

from __future__ import annotations

import pytest

from custom_components.surplife.protocol import state

from . import golden


def test_parse_pristine_state() -> None:
    """The factory state: off, 35 LEDs, reporting the default pattern."""
    parsed = state.parse_state(golden.STATE_PRISTINE_INNER)
    assert parsed.power is False
    assert parsed.preset == 0x25
    assert parsed.preset_label == "static"
    # The device always populates the pattern byte. In its factory state it
    # reports 0x65, which is the documented "Static Gradient" effect id, so it
    # is genuinely an effect rather than "no effect".
    assert parsed.pattern == 0x65
    assert parsed.effect_id == 0x65
    assert parsed.effect_name == "Static Gradient"
    assert parsed.led_count == 35
    assert parsed.model == 0xB6
    assert parsed.firmware == 9


def test_parse_off_state_with_effect_remembered() -> None:
    """After a Wave effect, powering off keeps the effect and speed bytes."""
    parsed = state.parse_state(golden.STATE_OFF_INNER)
    assert parsed.power is False
    assert parsed.effect_id == 1
    assert parsed.effect_name == "Wave"
    assert parsed.speed == 50


def test_parse_on_state() -> None:
    parsed = state.parse_state(golden.STATE_ON_INNER)
    assert parsed.power is True


def test_parse_accepts_longer_buffer() -> None:
    """parse_state tolerates leading data, as it sees a whole read buffer."""
    parsed = state.parse_state(b"\x00\x01\x02" + golden.STATE_PRISTINE_INNER)
    assert parsed.led_count == 35


def test_parse_rejects_non_state_frame() -> None:
    with pytest.raises(ValueError, match="EA 81"):
        state.parse_state(b"\x00\x11\x14\x1a\x0b")


def test_parse_rejects_short_frame() -> None:
    with pytest.raises(ValueError, match="too short"):
        state.parse_state(golden.STATE_PRISTINE_INNER[:20])


def test_preset_labels() -> None:
    """Each preset byte the device emits has a readable name."""
    scribble = bytearray(golden.STATE_PRISTINE_INNER)
    scribble[7] = 0x66
    assert state.parse_state(bytes(scribble)).preset_label == "scribble"

    segments = bytearray(golden.STATE_PRISTINE_INNER)
    segments[7] = 0x24
    assert state.parse_state(bytes(segments)).preset_label == "segments"


def test_unknown_preset_falls_back_to_hex() -> None:
    frame = bytearray(golden.STATE_PRISTINE_INNER)
    frame[7] = 0xAB
    assert state.parse_state(bytes(frame)).preset_label == "0xab"


def test_effect_id_is_none_for_undocumented_pattern() -> None:
    """An undocumented pattern byte is not presented as a named effect.

    The device reuses this byte for non-effect modes (a plain colour command
    reports 0x6E), so only documented effect IDs are surfaced as effects.
    """
    frame = bytearray(golden.STATE_PRISTINE_INNER)
    frame[8] = 0x7F
    parsed = state.parse_state(bytes(frame))
    assert parsed.pattern == 0x7F
    assert parsed.effect_id is None
    assert parsed.effect_name is None


def test_plain_colour_pattern_is_not_an_effect() -> None:
    """0x6E is what the device reports after a plain E0 01 colour command."""
    frame = bytearray(golden.STATE_PRISTINE_INNER)
    frame[8] = 0x6E
    parsed = state.parse_state(bytes(frame))
    assert parsed.effect_id is None
    assert parsed.effect_name is None


def test_rgb_derived_from_hsv() -> None:
    """Hue 0, saturation 100, value 100 is pure red."""
    frame = bytearray(golden.STATE_PRISTINE_INNER)
    frame[11] = 0
    frame[12] = 100
    frame[13] = 100
    assert state.parse_state(bytes(frame)).rgb == (255, 0, 0)


def test_hue_is_doubled_from_wire_value() -> None:
    """The wire stores hue halved; 60 becomes 120 degrees."""
    frame = bytearray(golden.STATE_PRISTINE_INNER)
    frame[11] = 60
    assert state.parse_state(bytes(frame)).hue == 120


def test_hue_clamped_below_360() -> None:
    """A raw 180 means 360 degrees, which must clamp to 359."""
    frame = bytearray(golden.STATE_PRISTINE_INNER)
    frame[11] = 180
    assert state.parse_state(bytes(frame)).hue == 359


def test_brightness_scales_to_255() -> None:
    frame = bytearray(golden.STATE_PRISTINE_INNER)
    frame[13] = 100
    assert state.parse_state(bytes(frame)).brightness == 255
    frame[13] = 50
    assert state.parse_state(bytes(frame)).brightness == 128


def test_raw_is_preserved() -> None:
    parsed = state.parse_state(golden.STATE_PRISTINE_INNER)
    assert parsed.raw == golden.STATE_PRISTINE_INNER


def test_build_state_query() -> None:
    assert state.build_state_query() == golden.STATE_QUERY_INNER
