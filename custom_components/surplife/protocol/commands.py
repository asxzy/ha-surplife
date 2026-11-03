"""Inner-payload builders for every command the 0xB6 understands.

Each function returns an **inner** payload; pass it to
:func:`~.framing.wrap` to obtain a complete frame. Keeping the two steps
separate makes the builders trivially testable against captured golden
packets.

Layouts are from ``docs/protocol_0xB6.md`` and were re-verified on hardware
2026-11-02. Byte offsets in the comments below are inner-payload offsets.
"""

from __future__ import annotations

import colorsys
import datetime as dt
from typing import Final

from .constants import (
    BLINK_NONE,
    DIRECTION_L2R,
    SEGMENT_COUNT,
    TIMER_SLOTS,
    WRITE_ALL,
    WRITE_COLOR,
    WRITE_WHITE,
)

OP_POWER: Final = 0x71
OP_LEVEL: Final = 0xE0
OP_EFFECT: Final = 0xE1
OP_GET_TIME: Final = 0x11
OP_SET_TIME: Final = 0x10
OP_TIMERS: Final = 0xE0

SUB_LEVEL: Final = 0x01
SUB_EFFECT_CUSTOM: Final = 0x21
SUB_EFFECT_SEGMENTS: Final = 0x22
SUB_SCRIBBLE_INIT: Final = 0x23
SUB_SCRIBBLE_PAINT: Final = 0x26
SUB_TIMER_GET: Final = 0x06
SUB_TIMER_SET: Final = 0x05

POWER_ON_BYTE: Final = 0x23
POWER_OFF_BYTE: Final = 0x24


def _clamp(value: int, low: int, high: int, name: str) -> int:
    if not low <= value <= high:
        raise ValueError(f"{name} must be {low}..{high}, got {value}")
    return value


def rgb_to_hsv_bytes(red: int, green: int, blue: int) -> tuple[int, int, int]:
    """Convert 0-255 RGB to the device's ``(H/2, S, V)`` byte triple.

    The device stores hue halved so it fits in one byte, saturation and value
    as percentages.
    """
    h, s, v = colorsys.rgb_to_hsv(
        _clamp(red, 0, 255, "red") / 255,
        _clamp(green, 0, 255, "green") / 255,
        _clamp(blue, 0, 255, "blue") / 255,
    )
    return int(h * 180), int(s * 100), int(v * 100)


def hsv_to_rgb(hue: int, saturation: int, value: int) -> tuple[int, int, int]:
    """Convert the device's HSV fields back to 0-255 RGB."""
    r, g, b = colorsys.hsv_to_rgb(
        _clamp(hue, 0, 359, "hue") / 360.0,
        _clamp(saturation, 0, 100, "saturation") / 100.0,
        _clamp(value, 0, 100, "value") / 100.0,
    )
    return min(int(r * 255), 255), min(int(g * 255), 255), min(int(b * 255), 255)


# --------------------------------------------------------------------------
# Power and levels
# --------------------------------------------------------------------------


def power(turn_on: bool) -> bytes:
    """Build a power command (``71 23/24 0F``).

    **Verified**: the state frame's power byte flips immediately.
    """
    return bytes([OP_POWER, POWER_ON_BYTE if turn_on else POWER_OFF_BYTE, 0x0F])


def levels(
    *,
    red: int = 0,
    green: int = 0,
    blue: int = 0,
    white: int = 0,
    write_mode: int | None = None,
) -> bytes:
    """Build a combined ``E0 01`` level command.

    This is the one place the colour and white channels are packed, so the
    single-channel shortcuts (:func:`color`, :func:`warm_white`) and the
    combined RGBW form cannot drift apart.

    Args:
        red, green, blue: RGB channels, 0-255.
        white: W channel level, 0-100.
        write_mode: Which channels the device should rewrite. Defaults to
            ``WRITE_ALL`` when both colour and white are present (a real RGBW
            mix), otherwise the single channel being set. The firmware ignores
            channels whose mode is not selected, which is what makes "set the
            W channel without disturbing RGB" possible.

    **Verified**: renders on hardware. The device does **not** echo any of this
    in its state frame, so callers must track it optimistically.
    """
    h2, s, v = rgb_to_hsv_bytes(red, green, blue)
    if write_mode is None:
        if white and (red or green or blue):
            write_mode = WRITE_ALL
        else:
            write_mode = WRITE_WHITE if white else WRITE_COLOR
    return bytes(
        [
            OP_LEVEL,
            SUB_LEVEL,
            0x00,
            _clamp(write_mode, 0, 0xFF, "write mode"),
            h2,
            s,
            v,
            0x00,  # white temperature: no cold-white LED on this hardware
            _clamp(white, 0, 100, "white level"),
            0x00,
            0x00,
            0x14,  # constant
            0x00,
            0x00,
        ]
    )


def color(red: int, green: int, blue: int) -> bytes:
    """Build a whole-strip colour command (``E0 01 00 A1 H/2 S V ...``).

    **Verified**: renders on hardware. Note that the device does **not** echo
    this colour in its state frame — see :mod:`.state`.
    """
    return levels(red=red, green=green, blue=blue, write_mode=WRITE_COLOR)


def warm_white(level: int) -> bytes:
    """Build a warm-white command (``E0 01 00 B1 00 00 00 00 LVL ...``).

    Args:
        level: White brightness 0-100.

    **Verified**: renders on hardware. The strip has no cold-white LED, so the
    temperature byte stays at zero.
    """
    return levels(white=level, write_mode=WRITE_WHITE)


def brightness(level: int) -> bytes:
    """Build a brightness-only command.

    The device has no dedicated dimmer opcode, so this re-issues the last
    colour at the requested value. Callers that track their own colour should
    use :func:`color` with a scaled triple instead; this helper exists for the
    simple "dim whatever is showing" case and applies to white mode.

    Args:
        level: Brightness 0-100.
    """
    return warm_white(level)


# --------------------------------------------------------------------------
# E1 21 — whole-strip animated effects
# --------------------------------------------------------------------------


def effect(
    effect_id: int,
    colors: list[tuple[int, ...]] | None = None,
    *,
    speed: int = 50,
    density: int = 50,
    direction: int = DIRECTION_L2R,
    option: int = 0x00,
    brightness: int = 100,
) -> bytes:
    """Build an ``E1 21`` extended custom effect.

    Inner layout::

        e1 21 01 64 PP OO DD NN SS 00 00 00 00 00 00 CC [H/2 S V 00 WLVL] x CC

    Args:
        effect_id: One of :class:`~.constants.ExtendedEffect` (1-22, 101, 102).
        colors: Up to 8 colours, each an RGB triple or an RGBW tuple whose
            fourth value is the W-channel level 0-100. Defaults to white.
        speed: Animation speed 0-100.
        density: Animation density 0-100.
        direction: ``0x01`` left→right, ``0x02`` right→left.
        option: Pattern-specific variant, ``0x00``-``0x02``.
        brightness: Value byte applied to every colour, 0-100.

    **Verified**: every effect ID round-trips through the state frame's effect
    and speed bytes. The W byte shares the layout of the verified ``E1 26``
    paint colour record.
    """
    _clamp(effect_id, 1, 0xFF, "effect id")
    _clamp(speed, 0, 100, "speed")
    _clamp(density, 0, 100, "density")
    _clamp(direction, 1, 2, "direction")
    _clamp(option, 0, 2, "option")
    _clamp(brightness, 0, 100, "brightness")

    palette = colors or [(255, 255, 255)]
    if not 1 <= len(palette) <= 8:
        raise ValueError(f"need 1..8 colours, got {len(palette)}")

    msg = bytearray(
        [
            OP_EFFECT,
            SUB_EFFECT_CUSTOM,
            0x01,
            0x64,
            effect_id,
            option,
            direction,
            density,
            speed,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            len(palette),
        ]
    )
    for entry in palette:
        h2, s, v = rgb_to_hsv_bytes(*entry[:3])
        # Scale the value by the requested brightness so callers can dim an
        # effect without recomputing colours.
        msg.extend([h2, s, round(v * brightness / 100), 0x00, _white_of(entry)])
    return bytes(msg)


# --------------------------------------------------------------------------
# E1 22 — 20 fixed segments
# --------------------------------------------------------------------------


def segments(
    colors: list[tuple[int, int, int] | tuple[int, int, int, int] | None],
) -> bytes:
    """Build an ``E1 22`` segment-colour command.

    Inner layout::

        e1 22 00 00 00 00 14 [H/2 S V 00 WLVL] x 20

    Args:
        colors: Up to 20 entries, or ``None``/``(0, 0, 0)`` for an off segment.
            An entry may be an RGB triple or an RGBW tuple, where the fourth
            value is the W-channel level 0-100. A pure-white segment is
            ``(0, 0, 0, level)``: colour channels dark, white LED lit. Missing
            trailing segments are turned off.

    **Verified**: renders 20 fixed zones; unspecified segments go dark. The
    per-colour W byte is the same field that the verified ``E1 26`` paint uses.
    """
    if len(colors) > SEGMENT_COUNT:
        raise ValueError(
            f"at most {SEGMENT_COUNT} segments, got {len(colors)}"
        )
    msg = bytearray([OP_EFFECT, SUB_EFFECT_SEGMENTS, 0x00, 0x00, 0x00, 0x00, SEGMENT_COUNT])
    for index in range(SEGMENT_COUNT):
        rgb = colors[index] if index < len(colors) else None
        if rgb is None or tuple(rgb[:3]) == (0, 0, 0) and not _white_of(rgb):
            msg.extend([0x00, 0x00, 0x00, 0x00, 0x00])
        else:
            h2, s, v = rgb_to_hsv_bytes(*rgb[:3])
            msg.extend([h2, s, v, 0x00, _white_of(rgb)])
    return bytes(msg)


def _white_of(entry: tuple[int, ...] | None) -> int:
    """Extract the W-channel level from an RGB or RGBW entry."""
    if entry is None or len(entry) < 4:
        return 0
    return _clamp(int(entry[3]), 0, 100, "white level")


# --------------------------------------------------------------------------
# E1 23 / E1 26 — per-LED ("scribble")
# --------------------------------------------------------------------------


def scribble_init(led_count: int) -> bytes:
    """Build an ``E1 23`` scribble-mode entry (non-rendering).

    Inner layout::

        e1 23 01 00 01 50 64 00 N [00 00 00 00 00 00 64] x N

    **Verified**: this does not light the strip on its own; its only
    observable effect is flipping the state frame's preset byte to ``0x66``.
    A following :func:`scribble_paint` is what renders.
    """
    _clamp(led_count, 1, 255, "led count")
    head = bytes([OP_EFFECT, SUB_SCRIBBLE_INIT, 0x01, 0x00, 0x01, 0x50, 0x64, 0x00, led_count])
    return head + bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x64]) * led_count


def bitmap(indices: list[int] | range, led_count: int) -> bytes:
    """Build an MSB-first bitmap selecting ``indices`` out of ``led_count`` LEDs.

    LED ``i`` maps to byte ``i // 8``, bit ``7 - (i % 8)``. A set bit means
    "apply this paint to LED i". Padding bits in the final byte stay zero.

    **Verified**: bitmap length is ``ceil(led_count / 8)``; for 35 LEDs that is
    5 bytes with the final byte's low 5 bits clear.
    """
    _clamp(led_count, 1, 4096, "led count")
    buf = bytearray((led_count + 7) // 8)
    for index in indices:
        if not 0 <= index < led_count:
            raise ValueError(f"LED index {index} out of range 0..{led_count - 1}")
        buf[index // 8] |= 0x80 >> (index % 8)
    return bytes(buf)


def scribble_paint(
    *,
    led_count: int,
    indices: list[int] | range,
    red: int = 0,
    green: int = 0,
    blue: int = 0,
    white: int = 0,
    effect_id: int = 0x00,
    direction: int = DIRECTION_L2R,
    density: int = 0x50,
    speed: int = 0x64,
    blink_mode: int = BLINK_NONE,
    blink_speed: int = 0x64,
) -> bytes:
    """Build an ``E1 26`` per-LED paint — the scribble render path.

    Inner layout::

        e1 26 EFFECT DIR DENSITY SPEED BLINK H/2 S V 00 WLVL BLINKSPD <bitmap>

    An LED's colour record is ``H/2 S V 00 WLVL``, so colour and white can be
    combined: pass both for an RGBW mix, only ``white`` for the white LED
    alone, or only RGB for colour alone. An off LED is a paint with everything
    zeroed.

    **Verified**: the device keeps a per-LED framebuffer, so each paint affects
    only its bitmap-selected LEDs and leaves the rest untouched.
    """
    if red or green or blue:
        h2, s, v = rgb_to_hsv_bytes(red, green, blue)
    else:
        h2 = s = v = 0
    white_level = _clamp(white, 0, 100, "white") if white else 0

    msg = bytes(
        [
            OP_EFFECT,
            SUB_SCRIBBLE_PAINT,
            _clamp(effect_id, 0, 8, "scribble effect"),
            _clamp(direction, 1, 2, "direction"),
            _clamp(density, 0, 100, "density"),
            _clamp(speed, 0, 100, "speed"),
            blink_mode,
            h2,
            s,
            v,
            0x00,
            white_level,
            _clamp(blink_speed, 0, 100, "blink speed"),
        ]
    )
    return msg + bitmap(indices, led_count)


# --------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------


def get_time() -> bytes:
    """Build a clock query (``11 1A 14 00 00 00 00 00 00``, wrapper v1).

    **Verified**: the reply is ``00 11 14 YY MM DD HH MM SS DOW 00``.
    """
    return bytes([OP_GET_TIME, 0x1A, 0x14, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])


def set_time(when: dt.datetime) -> bytes:
    """Build a clock-set command (``10 14 YY MM DD HH MM SS DOW 00 0F``, v1).

    Args:
        when: Local wall-clock time to write to the controller.

    **Verified**: the device accepts the write and reports the new time.
    """
    return bytes(
        [
            OP_SET_TIME,
            0x14,
            when.year - 2000,
            when.month,
            when.day,
            when.hour,
            when.minute,
            when.second,
            when.isoweekday(),
            0x00,
            0x0F,
        ]
    )


# --------------------------------------------------------------------------
# Timers
# --------------------------------------------------------------------------


def get_timers() -> bytes:
    """Build a timer query (``E0 06``, wrapper v1).

    **Verified**: the reply carries all six slots, each with a slot byte, a
    flag byte, and either 20 bytes of timer data or a zero flag for an empty
    slot.
    """
    return bytes([OP_TIMERS, SUB_TIMER_GET])


def set_timer(
    *,
    slot: int,
    active: bool = True,
    hour: int = 0,
    minute: int = 0,
    repeat_mask: int = 0,
    action: int = 0x24,
    color: tuple[int, int, int] | None = None,
) -> bytes:
    """Build a timer-slot write (``E0 05 SS <20-byte record>``, wrapper v1).

    Args:
        slot: Timer slot, 1-6.
        active: Whether the slot is enabled.
        hour: Start hour 0-23.
        minute: Start minute 0-59.
        repeat_mask: Day mask; see the ``REPEAT_*`` constants.
        action: One of :class:`~.constants.TimerAction`.
        color: RGB colour for a colour timer.

    **Verified**: writing slot 3 and reading it back round-trips, and the
    existing slots 1-2 are preserved.
    """
    _clamp(slot, 1, TIMER_SLOTS, "slot")
    _clamp(hour, 0, 23, "hour")
    _clamp(minute, 0, 59, "minute")

    record = bytearray(20)
    if active:
        record[0] = 0xF0
        record[1] = hour
        record[2] = minute
        record[3] = 0x00
        record[4] = repeat_mask
        record[5:10] = bytes([0x0E, 0xE0, 0x01, 0x00, action])
        if action == 0xA1 and color is not None:
            h2, s, v = rgb_to_hsv_bytes(*color)
            record[10], record[11], record[12] = h2, s, v

    return bytes([OP_TIMERS, SUB_TIMER_SET, slot]) + bytes(record)
