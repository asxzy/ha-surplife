"""State frame parsing.

A 0xB6 controller answers a state query with a single 27-byte inner payload
beginning ``EA 81``::

    ea 81 01 00 b6 09 [PWR] [PRESET] [EFFECT] [SPEED] f0 b4 [H2] [SAT] [VAL] \
    ff 00 05 00 [COUNT] 00 00 00 20 0c 01 02 03
     0  1  2  3  4  5    6      7        8       9     10 11  12   13    14 15 16 17  18

Field meanings below were established by differential probing on
2026-11-02 — changing one input at a time and reading the frame back. The
notable surprise is that **this firmware does not report the colour or
brightness set by a plain ``E0 01`` colour command**: bytes 11-13 keep their
last ``E1 21`` values. Only effect-driven colours are echoed. That is a
firmware behaviour, not a parsing gap, and the Home Assistant entity therefore
keeps its own optimistic colour state.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

from .constants import (
    EFFECT_NAMES,
    POWER_ON,
    PRESET_SCRIBBLE,
    PRESET_SEGMENTS,
    PRESET_STATIC,
    STATE_HEAD,
    STATE_LEN,
)

PRESET_LABELS = {
    PRESET_STATIC: "static",
    PRESET_SEGMENTS: "segments",
    PRESET_SCRIBBLE: "scribble",
}


@dataclass(frozen=True, slots=True)
class SurplifeState:
    """A decoded state frame.

    Attributes:
        power: Whether the controller reports itself powered on.
        preset: Raw preset byte. See :data:`PRESET_LABELS`.
        pattern: Raw pattern byte. See :attr:`effect_id` for interpretation.
        speed: Effect speed, 0-100.
        hue: Effect hue in degrees, 0-359 (derived from the halved wire value).
        saturation: Effect saturation, 0-100.
        value: Effect brightness, 0-100.
        led_count: Configured LED count used for bitmap sizing.
        model: Model number byte (``0xB6``).
        firmware: Firmware version byte (``9``).
    """

    power: bool
    preset: int
    pattern: int
    speed: int
    hue: int
    saturation: int
    value: int
    led_count: int
    model: int
    firmware: int
    raw: bytes

    @property
    def preset_label(self) -> str:
        """Human-readable preset name, or the raw hex value if unknown."""
        return PRESET_LABELS.get(self.preset, f"0x{self.preset:02x}")

    @property
    def effect_id(self) -> int | None:
        """The active ``E1 21`` effect ID, or ``None`` if the pattern byte is
        not one of the documented effect IDs.

        The device always populates this byte, including in non-effect modes
        where it holds other values (the factory state reports ``0x65`` and a
        plain colour command reports ``0x6E``). Only the documented IDs are
        treated as effects; everything else is exposed through :attr:`pattern`
        so nothing is silently mislabelled.
        """
        return self.pattern if self.pattern in EFFECT_NAMES else None

    @property
    def effect_name(self) -> str | None:
        """Human-readable name of the active effect, if any."""
        effect_id = self.effect_id
        if effect_id is None:
            return None
        return EFFECT_NAMES[effect_id]

    @property
    def rgb(self) -> tuple[int, int, int]:
        """Effect colour as an RGB triple, for display purposes only.

        This is *not* the colour of a plain ``E0 01`` colour command; see the
        module docstring.
        """
        r, g, b = colorsys.hsv_to_rgb(
            self.hue / 360.0, self.saturation / 100.0, self.value / 100.0
        )
        return (
            min(int(r * 255), 255),
            min(int(g * 255), 255),
            min(int(b * 255), 255),
        )

    @property
    def brightness(self) -> int:
        """Effect brightness scaled to Home Assistant's 0-255 range."""
        return round(self.value * 255 / 100)


def parse_state(inner: bytes) -> SurplifeState:
    """Parse a 27-byte ``EA 81`` inner payload.

    Args:
        inner: The unwrapped payload, or a longer buffer containing it.

    Raises:
        ValueError: if no ``EA 81`` header is found or the payload is short.
    """
    idx = inner.find(STATE_HEAD)
    if idx < 0:
        raise ValueError("not a state frame (missing EA 81)")
    body = inner[idx:]
    if len(body) < STATE_LEN:
        raise ValueError(f"state frame too short ({len(body)} bytes)")

    effect_id = body[8]
    return SurplifeState(
        power=body[6] == POWER_ON,
        preset=body[7],
        pattern=body[8],
        speed=body[9],
        # Hue is transmitted halved to fit a byte: 0-180 covers 0-360 degrees.
        hue=min(body[11] * 2, 359),
        saturation=min(body[12], 100),
        value=min(body[13], 100),
        led_count=body[18],
        model=body[4],
        firmware=body[5],
        raw=bytes(body[:STATE_LEN]),
    )


def build_state_query() -> bytes:
    """Return the inner payload of a state query (``EA 81 8A 8B``).

    **Verified**: this is the query the device answers; there is no separate
    "extended state" query despite the extended response.
    """
    return bytes.fromhex("ea818a8b")
