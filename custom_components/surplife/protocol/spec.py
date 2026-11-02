"""Parsing for the human-facing text forms used by the services and the CLI.

Both the Home Assistant services and ``tools/surplife_cli.py`` accept the same
colour tokens, the same per-LED pattern grammar and the same repeat phrases.
Keeping one implementation here means the two cannot drift apart — and it keeps
the grammar in the stdlib-only layer, where it can be tested without Home
Assistant.

Everything raises :class:`ValueError`; callers translate that into whatever
error type their own layer uses.
"""

from __future__ import annotations

import re
from typing import Any

from .constants import (
    BLINK_MODES,
    REPEAT_EVERYDAY,
    REPEAT_NAMES,
    REPEAT_WEEKDAYS,
    REPEAT_WEEKEND,
)
from .scribble import ScribbleLED

#: Colour names accepted in patterns and segment lists.
COLOR_NAMES: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "purple": (128, 0, 128),
    "orange": (255, 128, 0),
    "pink": (255, 105, 180),
    "white": (255, 255, 255),
    "warm_white": (255, 200, 120),
}

_RUN_LENGTH = re.compile(r"^(?:(\d+)x)?(.+)$")
_WHITE_LEVEL = re.compile(r"^w(\d+)$")

_OFF_TOKENS = ("black", "off", "none")


def parse_color(token: Any) -> tuple[tuple[int, int, int] | None, int | None]:
    """Parse one colour token into ``(rgb, white_level)``.

    Accepts a colour name, ``#rrggbb``, ``r,g,b``, or ``w<n>`` for a warm-white
    level. ``black``, ``off`` and ``none`` mean "dark" and return
    ``(None, None)``. A list or tuple of three integers is taken as RGB.

    Raises:
        ValueError: if the token is not a recognised colour.
    """
    if isinstance(token, (list, tuple)):
        values = [int(value) for value in token]
        if len(values) != 3:
            raise ValueError(f"expected 3 RGB values, got {len(values)}")
        return (values[0], values[1], values[2]), None

    text = str(token).strip().lower()
    if text in _OFF_TOKENS:
        return None, None

    match = _WHITE_LEVEL.match(text)
    if match:
        level = int(match.group(1))
        if not 0 <= level <= 100:
            raise ValueError(f"white level {level} is outside 0-100")
        return None, level

    if text in COLOR_NAMES:
        return COLOR_NAMES[text], None

    if text.startswith("#") and len(text) == 7:
        try:
            red, green, blue = (
                int(text[1:3], 16),
                int(text[3:5], 16),
                int(text[5:7], 16),
            )
        except ValueError as err:
            raise ValueError(f"invalid hex colour {token!r}") from err
        return (red, green, blue), None

    if "," in text:
        parts = text.split(",")
        if len(parts) != 3:
            raise ValueError(f"expected 3 RGB values in {token!r}")
        try:
            return tuple(int(part) for part in parts), None  # type: ignore[return-value]
        except ValueError as err:
            raise ValueError(f"invalid RGB triple {token!r}") from err

    raise ValueError(f"unrecognised colour {token!r}")


def parse_led_spec(spec: str, led_count: int) -> list[ScribbleLED]:
    """Expand a per-LED pattern into exactly ``led_count`` entries.

    Grammar, space separated:

    * ``<color>`` — one LED
    * ``<n>x<color>`` — a run of ``n`` LEDs
    * ``<color>/<blink>`` — add a blink mode (``slow`` or ``fast``)
    * ``<color>/<blink>/<speed>`` — with an explicit blink speed
    * ``key=value`` — a global option; skipped here, the caller consumes it

    Short patterns are padded with dark LEDs. That matters: the device keeps a
    stateful per-LED framebuffer, so an unmentioned LED would keep whatever it
    was showing before, and the result would depend on history.

    Raises:
        ValueError: if a token is malformed, or the pattern is too long.
    """
    entries: list[ScribbleLED] = []

    for token in spec.split():
        if "=" in token:
            continue

        match = _RUN_LENGTH.match(token)
        if not match:
            raise ValueError(f"malformed LED token {token!r}")

        count = int(match.group(1)) if match.group(1) else 1
        if count < 1:
            raise ValueError(f"run length must be at least 1 in {token!r}")

        blink_mode = BLINK_MODES["none"]
        blink_speed = 100

        parts = match.group(2).split("/")
        if len(parts) > 1:
            name = parts[1].lower()
            if name not in BLINK_MODES:
                raise ValueError(f"unknown blink mode {parts[1]!r}")
            blink_mode = BLINK_MODES[name]
            if len(parts) > 2:
                blink_speed = int(parts[2])

        rgb, white = parse_color(parts[0])
        entries.extend(
            ScribbleLED(
                rgb=rgb, white=white, blink_mode=blink_mode, blink_speed=blink_speed
            )
            for _ in range(count)
        )

    if not entries:
        raise ValueError("the pattern selects no LEDs")
    if len(entries) > led_count:
        raise ValueError(
            f"pattern has {len(entries)} LEDs but the controller has {led_count}"
        )

    entries.extend(ScribbleLED() for _ in range(led_count - len(entries)))
    return entries


def parse_repeat(text: str) -> int:
    """Parse a repeat description into the wire day mask.

    Accepts ``once``, ``everyday``, ``weekdays``, ``weekend``, or a comma
    separated day list such as ``mon,wed,fri``.

    Raises:
        ValueError: if a day name is not recognised.
    """
    key = str(text).strip().lower()
    if key in ("once", "none", ""):
        return 0
    if key == "everyday":
        return REPEAT_EVERYDAY
    if key == "weekdays":
        return REPEAT_WEEKDAYS
    if key == "weekend":
        return REPEAT_WEEKEND

    mask = 0
    for day in key.replace(" ", "").split(","):
        if day not in REPEAT_NAMES:
            raise ValueError(f"unknown day {day!r}")
        mask |= REPEAT_NAMES[day]
    return mask


def format_repeat(mask: int) -> str:
    """Render a day mask back into the compact text form."""
    if mask == 0:
        return "once"
    if mask == REPEAT_EVERYDAY:
        return "everyday"
    if mask == REPEAT_WEEKDAYS:
        return "weekdays"
    if mask == REPEAT_WEEKEND:
        return "weekend"
    return ",".join(
        name for name, bit in REPEAT_NAMES.items() if mask & bit
    )
