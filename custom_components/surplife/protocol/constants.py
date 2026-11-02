"""Constants for the Surplife 0xB6 protocol.

Every table here is tagged with its evidence level. See
``docs/protocol_0xB6.md`` for the captures behind them.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final

DEFAULT_PORT: Final = 5577
DISCOVERY_PORT: Final = 48899
DISCOVERY_PROBE: Final = b"HF-A11ASSISTHREAD"
MODEL_NUMBER: Final = 0xB6
MODEL_INFO: Final = "ZG-BL-UFO"
MODEL_NAME: Final = "Surplife Outdoor Permanent Lighting (0xB6)"
MANUFACTURER: Final = "Zengge"
MODEL_HW_VERSION: Final = "AK001-ZJ21413"

# State frame layout (27-byte inner payload starting EA 81).
STATE_HEAD: Final = b"\xea\x81"
STATE_LEN: Final = 27
STATE_QUERY_INNER: Final = b"\xea\x81\x8a\x8b"

#: Power byte values in the state frame (offset 6).
POWER_ON: Final = 0x23
POWER_OFF: Final = 0x24

#: Preset byte values in the state frame (offset 7). **Verified**: these are
#: the only three values observed, and each maps to a distinct command family.
PRESET_STATIC: Final = 0x25  # plain colour / warm white
PRESET_SEGMENTS: Final = 0x24  # E1 22 segment colours
PRESET_SCRIBBLE: Final = 0x66  # E1 23 / E1 26 per-LED

#: Offset of the configured LED count in the state frame. **Verified** on all
#: three controllers in this installation (35) and by upstream reference work
#: on other units (80, 100). Always read it from the device.
STATE_LED_COUNT_OFFSET: Final = 18

#: Write-mode byte in an ``E0 01`` level command.
WRITE_COLOR: Final = 0xA1
WRITE_WHITE: Final = 0xB1
WRITE_ALL: Final = 0x00

#: ``E1 26`` blink-mode byte.
BLINK_NONE: Final = 0x00
BLINK_SLOW: Final = 0x08
BLINK_FAST: Final = 0x10

BLINK_MODES: Final = {
    "none": BLINK_NONE,
    "slow": BLINK_SLOW,
    "fast": BLINK_FAST,
}

#: ``E1 26`` / ``E1 21`` direction byte.
DIRECTION_L2R: Final = 0x01
DIRECTION_R2L: Final = 0x02

DIRECTIONS: Final = {
    "l2r": DIRECTION_L2R,
    "left_to_right": DIRECTION_L2R,
    "r2l": DIRECTION_R2L,
    "right_to_left": DIRECTION_R2L,
}


class ExtendedEffect(IntEnum):
    """Whole-strip animated effect IDs for ``E1 21``.

    **Verified** against the app's own labels across a 70-command capture, and
    re-verified on hardware here by round-tripping every ID through the state
    frame's effect byte.
    """

    WAVE = 0x01
    METEOR = 0x02
    STREAMER = 0x03
    BUILDING_BLOCKS = 0x04
    FLOWING_WATER = 0x05
    CHASE = 0x06
    HORSE_RACING = 0x07
    CYCLE = 0x08
    BREATHE = 0x09
    JUMP = 0x0A
    STROBE = 0x0B
    TWINKLING_STARS = 0x0C
    STARS_WINK = 0x0D
    WARNING = 0x0E
    COLLISION = 0x0F
    FIREWORKS = 0x10
    COMET = 0x11
    GRADIENT_METEOR = 0x12
    VOLCANO = 0x13
    SUPERLUMINAL = 0x14
    RAINBOW_BRIDGE = 0x15
    GRADIENT_OVERLAY = 0x16
    STATIC_GRADIENT = 0x65
    STATIC_FILL = 0x66


#: Human-readable names for :class:`ExtendedEffect`, keyed by wire value.
EFFECT_NAMES: Final = {
    0x01: "Wave",
    0x02: "Meteor",
    0x03: "Streamer",
    0x04: "Building Blocks",
    0x05: "Flowing Water",
    0x06: "Chase",
    0x07: "Horse Racing",
    0x08: "Cycle",
    0x09: "Breathe",
    0x0A: "Jump",
    0x0B: "Strobe",
    0x0C: "Twinkling Stars",
    # The app ships this one with a lowercase second word. Kept verbatim so the
    # entity's effect list matches what users see in the official app.
    0x0D: "Stars wink",
    0x0E: "Warning",
    0x0F: "Collision",
    0x10: "Fireworks",
    0x11: "Comet",
    0x12: "Gradient Meteor",
    0x13: "Volcano",
    0x14: "Superluminal",
    0x15: "Rainbow Bridge",
    0x16: "Gradient Overlay",
    0x65: "Static Gradient",
    0x66: "Static Fill",
}

EFFECT_NAME_TO_ID: Final = {name.lower(): eid for eid, name in EFFECT_NAMES.items()}


class ScribbleEffect(IntEnum):
    """Per-LED effect IDs for ``E1 26`` byte 2.

    **Verified**: the device accepts ``0x00``-``0x08``; the app names five of
    them and leaves 3, 4, 6 and 7 unnamed but functional.
    """

    STATIC = 0x00
    FLOWING = 0x01
    TWINKLING = 0x02
    TWINKLING_ALT = 0x03
    UNNAMED_4 = 0x04
    STARS_WINK = 0x05
    UNNAMED_6 = 0x06
    UNNAMED_7 = 0x07
    ACCUMULATE = 0x08


#: Names for the scribble effects the app exposes.
SCRIBBLE_EFFECT_NAMES: Final = {
    0x00: "Static",
    0x01: "Flowing",
    0x02: "Twinkling",
    0x03: "Twinkling (variant)",
    0x04: "Effect 4",
    0x05: "Stars Wink",
    0x06: "Effect 6",
    0x07: "Effect 7",
    0x08: "Accumulate",
}

SCRIBBLE_EFFECT_NAME_TO_ID: Final = {
    "static": 0x00,
    "flowing": 0x01,
    "twinkling": 0x02,
    "twinkling_alt": 0x03,
    "stars_wink": 0x05,
    "accumulate": 0x08,
}

#: Number of fixed segments addressable by ``E1 22``. **Verified.**
SEGMENT_COUNT: Final = 20

#: Number of hardware timer slots. **Verified**: the device reports six slots,
#: of which the first two are populated by default.
TIMER_SLOTS: Final = 6


class TimerAction(IntEnum):
    """Action byte for a timer slot (``E0 05`` payload)."""

    ON = 0x23
    OFF = 0x24
    COLOR = 0xA1
    SCENE_GRADIENT = 0x29
    SCENE_SEGMENTS = 0x6B


TIMER_ACTION_NAMES: Final = {
    0x23: "on",
    0x24: "off",
    0xA1: "color",
    0x29: "gradient",
    0x6B: "segments",
}

TIMER_ACTION_NAME_TO_VALUE: Final = {v: k for k, v in TIMER_ACTION_NAMES.items()}

#: Timer repeat mask bits. Note the offset from the usual LEDENET layout: bit
#: 0 is reserved, Monday is bit 1.
REPEAT_MONDAY: Final = 0x02
REPEAT_TUESDAY: Final = 0x04
REPEAT_WEDNESDAY: Final = 0x08
REPEAT_THURSDAY: Final = 0x10
REPEAT_FRIDAY: Final = 0x20
REPEAT_SATURDAY: Final = 0x40
REPEAT_SUNDAY: Final = 0x80
REPEAT_WEEKDAYS: Final = 0x3E
REPEAT_WEEKEND: Final = 0xC0
REPEAT_EVERYDAY: Final = 0xFE
REPEAT_NONE: Final = 0x00

REPEAT_NAMES: Final = {
    "mon": REPEAT_MONDAY,
    "tue": REPEAT_TUESDAY,
    "wed": REPEAT_WEDNESDAY,
    "thu": REPEAT_THURSDAY,
    "fri": REPEAT_FRIDAY,
    "sat": REPEAT_SATURDAY,
    "sun": REPEAT_SUNDAY,
}

REPEAT_DAY_LABELS: Final = (
    (REPEAT_MONDAY, "Mon"),
    (REPEAT_TUESDAY, "Tue"),
    (REPEAT_WEDNESDAY, "Wed"),
    (REPEAT_THURSDAY, "Thu"),
    (REPEAT_FRIDAY, "Fri"),
    (REPEAT_SATURDAY, "Sat"),
    (REPEAT_SUNDAY, "Sun"),
)
