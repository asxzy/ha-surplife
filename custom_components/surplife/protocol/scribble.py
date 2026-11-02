"""Per-LED ("scribble") configuration model.

The device renders per-LED state through grouped ``E1 26`` paints: LEDs that
share a colour *and* blink setting go into one paint, and every LED is covered
exactly once so the result is deterministic regardless of what the strip was
showing before.

**Verified**: this grouping strategy reproduces the app's behaviour, including
independent per-group blink.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, NamedTuple

from .constants import BLINK_NONE, BLINK_SLOW, BLINK_FAST


class ScribbleLED(NamedTuple):
    """One LED's desired setting.

    Colour and white are mutually exclusive:

    * ``rgb`` set — colour mode; brightness comes from the RGB triple.
    * ``white`` set — white mode; ``white`` is the level, 0-100.
    * both ``None`` — the LED is off.
    """

    rgb: tuple[int, int, int] | None = None
    white: int | None = None
    blink_mode: int = BLINK_NONE
    blink_speed: int = 100

    @property
    def is_off(self) -> bool:
        """Whether this LED is dark."""
        return self.rgb is None and not self.white

    @property
    def group_key(self) -> tuple[object, ...]:
        """Key that determines which LEDs can share one paint."""
        return (self.rgb, self.white, self.blink_mode, self.blink_speed)


@dataclass(frozen=True, slots=True)
class PaintGroup:
    """A set of LEDs that can be rendered by a single ``E1 26``.

    Attributes:
        leds: The shared :class:`ScribbleLED` setting.
        indices: LED indices this paint applies to.
    """

    leds: ScribbleLED
    indices: tuple[int, ...]


def group_leds(leds: Iterable[ScribbleLED]) -> list[PaintGroup]:
    """Group per-LED settings into the minimum number of paints.

    LEDs are grouped by identical ``(rgb, white, blink_mode, blink_speed)``.
    Groups are returned in ascending order of their first LED index, which
    keeps the wire order stable and therefore testable.

    Args:
        leds: One :class:`ScribbleLED` per LED, in physical order.
    """
    buckets: dict[tuple[object, ...], list[int]] = defaultdict(list)
    settings: dict[tuple[object, ...], ScribbleLED] = {}
    for index, led in enumerate(leds):
        buckets[led.group_key].append(index)
        settings.setdefault(led.group_key, led)
    return [
        PaintGroup(leds=settings[key], indices=tuple(indices))
        for key, indices in sorted(buckets.items(), key=lambda item: item[1][0])
    ]


def normalize(leds: Iterable[ScribbleLED], led_count: int) -> list[ScribbleLED]:
    """Pad or truncate a per-LED list to exactly ``led_count`` entries.

    Short lists are padded with dark LEDs so that every physical LED is
    covered by some paint.

    Args:
        leds: The supplied settings.
        led_count: The controller's configured LED count.

    Raises:
        ValueError: if more LEDs are supplied than the device has.
    """
    result = list(leds)
    if len(result) > led_count:
        raise ValueError(
            f"{len(result)} LEDs supplied but the controller has {led_count}"
        )
    result.extend(ScribbleLED() for _ in range(led_count - len(result)))
    return result


BLINK_NAME_TO_MODE = {
    "none": BLINK_NONE,
    "slow": BLINK_SLOW,
    "fast": BLINK_FAST,
}

BLINK_MODE_TO_NAME = {value: key for key, value in BLINK_NAME_TO_MODE.items()}
