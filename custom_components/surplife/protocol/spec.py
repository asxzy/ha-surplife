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
from collections.abc import Sequence
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


def parse_color(
    token: Any,
) -> tuple[tuple[int, int, int] | None, int | None]:
    """Parse one colour token into ``(rgb, white_level)``.

    Accepts a colour name, ``#rrggbb``, ``r,g,b``, ``r,g,b,w``, or ``w<n>`` for
    a white level. ``black``, ``off`` and ``none`` mean "dark" and return
    ``(None, None)``. A list or tuple of three integers is taken as RGB; four
    integers are RGBW.

    **The white level is always a 0-100 percentage** — from ``w<n>`` and from
    the fourth element of an RGBW token alike. Home Assistant's ``rgbw_color``
    uses 0-255 instead, so the conversion happens at that boundary, not here.
    Keeping one unit inside the protocol layer means the value can be written
    straight into the wire byte without a second chance to get it wrong.

    Selecting white through the W channel is deliberate: the controller drives
    colour and white separately, so ``255,255,255`` (a simulated RGB white) and
    ``0,0,0,100`` (the actual white LED) are different requests. The latter
    leaves the colour channels dark and lights the white LED, which is brighter
    and a truer white on this hardware.

    Raises:
        ValueError: if the token is not a recognised colour.
    """
    if isinstance(token, (list, tuple)):
        values = [int(value) for value in token]
        if len(values) == 4:
            return (values[0], values[1], values[2]), _as_white_level(values[3])
        if len(values) != 3:
            raise ValueError(f"expected 3 RGB or 4 RGBW values, got {len(values)}")
        return (values[0], values[1], values[2]), None

    text = str(token).strip().lower()
    if text in _OFF_TOKENS:
        return None, None

    match = _WHITE_LEVEL.match(text)
    if match:
        return None, _as_white_level(int(match.group(1)))

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
        parts = [part.strip() for part in text.split(",")]
        if len(parts) not in (3, 4):
            raise ValueError(f"expected 3 RGB or 4 RGBW values in {token!r}")
        try:
            values = [int(part) for part in parts]
        except ValueError as err:
            raise ValueError(f"invalid RGB triple {token!r}") from err
        if len(values) == 4:
            return (values[0], values[1], values[2]), _as_white_level(values[3])
        return tuple(values), None  # type: ignore[return-value]

    raise ValueError(f"unrecognised colour {token!r}")


def _as_white_level(value: int) -> int:
    """Validate a W-channel level.

    Raises:
        ValueError: if it is outside 0-100.
    """
    if not 0 <= value <= 100:
        raise ValueError(f"white level {value} is outside 0-100")
    return value


def parse_palette(text: str, *, max_colours: int = 8) -> list[tuple[int, int, int, int]]:
    """Parse a list of colours into RGBW tuples, in the order given.

    Accepts everything :func:`parse_color` accepts, separated by commas. A bare
    ``w<n>`` clause attaches to the colour before it, so both spellings work::

        red, blue
        #ff0000, #0000ff
        255,0,0, 0,0,255
        255,0,0,w30, 0,0,255

    This exists because the light entity can only ever carry a single colour,
    while the wire protocol takes up to eight. A typed list is the one surface
    that expresses the whole palette without a custom frontend card.

    Args:
        text: Comma-separated colour tokens.
        max_colours: The protocol's ceiling for this effect.

    Returns:
        RGBW tuples with the white level in 0-100.

    Raises:
        ValueError: if the list is empty, too long, or a token does not parse.
    """
    tokens = [part.strip() for part in text.split(",")]
    colours: list[list[int]] = []

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token:
            index += 1
            continue

        # A bare ``w<n>`` modifies the colour before it rather than adding one.
        white = _WHITE_LEVEL.match(token)
        if white:
            if not colours:
                raise ValueError(f"{token!r} has no colour to apply to")
            colours[-1][3] = _as_white_level(int(white.group(1)))
            index += 1
            continue

        # ``r,g,b`` spans three tokens; three consecutive bare integers are the
        # only case where that is unambiguous, so look ahead for them.
        triple = tokens[index : index + 3]
        if len(triple) == 3 and all(_is_int(part) for part in triple):
            colours.append([int(part) for part in triple] + [0])
            index += 3
            continue

        rgb, level = parse_color(token)
        if rgb is None:
            raise ValueError(f"{token!r} is not a lit colour")
        colours.append([rgb[0], rgb[1], rgb[2], level or 0])
        index += 1

    if not colours:
        raise ValueError("no colours given")
    if len(colours) > max_colours:
        raise ValueError(
            f"{len(colours)} colours given but this effect takes at most "
            f"{max_colours}"
        )
    return [(r, g, b, w) for r, g, b, w in colours]


def _is_int(token: str) -> bool:
    """Whether a token is a bare integer, which makes an ``r,g,b`` triple."""
    try:
        int(token)
    except ValueError:
        return False
    return True


def format_palette(
    colours: Sequence[tuple[int, ...]] | Sequence[Sequence[int]],
) -> str:
    """Render a palette as the text :func:`parse_palette` accepts.

    Uses ``#rrggbb`` for each colour and adds a separate ``w<n>`` token when the
    white level is non-zero, so a pure-colour palette stays short and the white
    channel is only mentioned when it is actually doing something. The ``w<n>``
    is its own token because that is the form :func:`parse_palette` reads back
    as "modify the previous colour", which keeps the two a true round trip.

    Args:
        colours: RGB or RGBW tuples, white level in 0-100.

    Returns:
        A comma-separated string, or ``""`` for an empty palette.
    """
    parts: list[str] = []
    for colour in colours:
        if len(colour) < 3:
            continue
        red, green, blue = (max(0, min(255, int(v))) for v in colour[:3])
        parts.append(f"#{red:02x}{green:02x}{blue:02x}")
        white = int(colour[3]) if len(colour) > 3 else 0
        if white:
            parts.append(f"w{max(0, min(100, white))}")
    return ", ".join(parts)


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
