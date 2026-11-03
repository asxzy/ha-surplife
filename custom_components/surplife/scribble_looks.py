"""Named per-LED looks — the usable face of per-LED ("scribble") painting.

The wire protocol wants a full per-LED spec, which is fine for a script but
unreasonable for a person and impossible to pick from a dropdown. A *look* is a
named, parameterised pattern generator that expands to exactly the
``led_count`` entries the protocol needs.

Every look is **deterministic**: the same name, palette and LED count always
produce the same LEDs. That is deliberate. It means a scene built on a look is
reproducible, a daily automation cannot drift, and a test can assert the exact
output. Any apparent "randomness" (fireflies, twinkles) comes from hashing the
LED index, not from a random number generator.
"""

from __future__ import annotations

import colorsys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

from .protocol.constants import BLINK_FAST, BLINK_NONE, BLINK_SLOW
from .protocol.scribble import ScribbleLED

Color = tuple[int, int, int]

RED: Final[Color] = (255, 0, 0)
GREEN: Final[Color] = (0, 200, 60)
BLUE: Final[Color] = (0, 80, 255)
WARM_WHITE: Final[Color] = (255, 190, 120)
WHITE: Final[Color] = (255, 255, 255)
ORANGE: Final[Color] = (255, 96, 0)
PURPLE: Final[Color] = (128, 0, 255)
PINK: Final[Color] = (255, 40, 120)
GOLD: Final[Color] = (255, 200, 0)

#: Builder signature: given the palette and the LED count, return one setting
#: per LED.
Builder = Callable[[Sequence[Color], int], list[ScribbleLED]]


def _cycle(palette: Sequence[Color], index: int) -> Color:
    """Pick a palette colour by position, wrapping around."""
    return palette[index % len(palette)]


def _solid(palette: Sequence[Color], count: int) -> list[ScribbleLED]:
    """Every LED one colour."""
    return [ScribbleLED(rgb=palette[0]) for _ in range(count)]


def _stripes(palette: Sequence[Color], count: int) -> list[ScribbleLED]:
    """Alternating blocks of three LEDs."""
    return [ScribbleLED(rgb=_cycle(palette, i // 3)) for i in range(count)]


def _candy_cane(palette: Sequence[Color], count: int) -> list[ScribbleLED]:
    """Two-colour diagonal banding: blocks of two, offset every other row."""
    return [
        ScribbleLED(rgb=_cycle(palette, (i + i // 2) // 2)) for i in range(count)
    ]


def _gradient(palette: Sequence[Color], count: int) -> list[ScribbleLED]:
    """Smooth interpolation across the palette."""
    stops = list(palette)
    if len(stops) == 1:
        return _solid(stops, count)
    span = max(count - 1, 1)
    leds: list[ScribbleLED] = []
    for index in range(count):
        position = index / span * (len(stops) - 1)
        lower = min(int(position), len(stops) - 2)
        blend = position - lower
        first, second = stops[lower], stops[lower + 1]
        leds.append(
            ScribbleLED(
                rgb=tuple(
                    round(a + (b - a) * blend) for a, b in zip(first, second, strict=True)
                )  # type: ignore[arg-type]
            )
        )
    return leds


def _rainbow(palette: Sequence[Color], count: int) -> list[ScribbleLED]:
    """A full hue sweep, ignoring the palette's colours but not its length."""
    span = max(count - 1, 1)
    leds: list[ScribbleLED] = []
    for index in range(count):
        red, green, blue = colorsys.hsv_to_rgb(index / span, 1.0, 1.0)
        leds.append(
            ScribbleLED(rgb=(round(red * 255), round(green * 255), round(blue * 255)))
        )
    return leds


def _firefly(palette: Sequence[Color], count: int) -> list[ScribbleLED]:
    """Mostly dark, with a deterministic scatter of single lit LEDs."""
    leds: list[ScribbleLED] = []
    for index in range(count):
        # A cheap integer hash keeps the scatter stable and spread out without
        # importing `random` and without a seed to get wrong.
        lit = ((index * 2654435761) >> 7) % 5 == 0
        leds.append(
            ScribbleLED(
                rgb=_cycle(palette, index) if lit else None,
                blink_mode=BLINK_SLOW if lit else BLINK_NONE,
            )
        )
    return leds


def _twinkle(palette: Sequence[Color], count: int) -> list[ScribbleLED]:
    """Alternating blocks where every third LED blinks fast."""
    leds: list[ScribbleLED] = []
    for index in range(count):
        colour = _cycle(palette, index // 3)
        blink = BLINK_FAST if index % 3 == 0 else BLINK_SLOW
        leds.append(ScribbleLED(rgb=colour, blink_mode=blink))
    return leds


def _chase(palette: Sequence[Color], count: int) -> list[ScribbleLED]:
    """A single bright block on a dark background, with lit blinkers."""
    block = max(count // 4, 3)
    leds: list[ScribbleLED] = []
    for index in range(count):
        lit = index < block
        leds.append(
            ScribbleLED(
                rgb=_cycle(palette, 0) if lit else None,
                blink_mode=BLINK_FAST if lit else BLINK_NONE,
                blink_speed=100,
            )
        )
    return leds


def _wreath(palette: Sequence[Color], count: int) -> list[ScribbleLED]:
    """Half-lit alternating blocks — a garland look."""
    leds: list[ScribbleLED] = []
    for index in range(count):
        if index % 2 == 0:
            leds.append(ScribbleLED(rgb=_cycle(palette, index // 2), blink_mode=BLINK_SLOW))
        else:
            leds.append(ScribbleLED())
    return leds


@dataclass(frozen=True, slots=True)
class Look:
    """One named per-LED pattern.

    Attributes:
        name: Machine name, used as the scene field and select option.
        title: Human-readable label.
        description: What it looks like, in one line.
        palette: Default colours, used when a scene does not override them.
        builder: Expands the palette to one :class:`ScribbleLED` per LED.
        supports_palette: Whether a scene may override the colours. A rainbow
            sweep has no meaningful palette, so it does not.
    """

    name: str
    title: str
    description: str
    palette: tuple[Color, ...] = (WARM_WHITE,)
    builder: Builder = field(default=_solid)
    supports_palette: bool = True

    def build(
        self, led_count: int, palette: Sequence[Color] | None = None
    ) -> list[ScribbleLED]:
        """Expand the look to exactly ``led_count`` LEDs.

        Raises:
            ValueError: if the LED count is not positive.
        """
        if led_count < 1:
            raise ValueError(f"LED count must be positive, got {led_count}")
        colours = tuple(palette or self.palette)
        if not colours:
            colours = self.palette
        if not self.supports_palette:
            colours = self.palette
        leds = self.builder(colours, led_count)
        if len(leds) != led_count:  # pragma: no cover - defensive
            raise ValueError(
                f"look {self.name!r} produced {len(leds)} LEDs, expected {led_count}"
            )
        return leds


LOOK_LIST: Final[tuple[Look, ...]] = (
    Look(
        name="solid",
        title="Solid",
        description="Every LED the same colour.",
        palette=(WARM_WHITE,),
        builder=_solid,
    ),
    Look(
        name="stripes",
        title="Stripes",
        description="Alternating three-LED blocks in palette order.",
        palette=(RED, GREEN),
        builder=_stripes,
    ),
    Look(
        name="candy_cane",
        title="Candy cane",
        description="Diagonal two-colour banding.",
        palette=(RED, WHITE),
        builder=_candy_cane,
    ),
    Look(
        name="gradient",
        title="Gradient",
        description="Smooth blend between the palette colours.",
        palette=(ORANGE, PURPLE),
        builder=_gradient,
    ),
    Look(
        name="rainbow",
        title="Rainbow",
        description="Full hue sweep across the strip.",
        palette=(WHITE,),
        builder=_rainbow,
        supports_palette=False,
    ),
    Look(
        name="firefly",
        title="Fireflies",
        description="Scattered blinking dots on a dark strip.",
        palette=(GOLD,),
        builder=_firefly,
    ),
    Look(
        name="twinkle",
        title="Twinkle",
        description="Colour blocks where every third LED blinks fast.",
        palette=(WHITE, GOLD),
        builder=_twinkle,
    ),
    Look(
        name="chase",
        title="Chase block",
        description="One lit block chasing around the strip.",
        palette=(BLUE,),
        builder=_chase,
    ),
    Look(
        name="wreath",
        title="Garland",
        description="Alternating lit and dark LEDs, slowly breathing.",
        palette=(GREEN, RED),
        builder=_wreath,
    ),
)

#: Every look by machine name.
LOOKS: Final[dict[str, Look]] = {look.name: look for look in LOOK_LIST}


def build_look(
    name: str, led_count: int, palette: Sequence[Color] | None = None
) -> list[ScribbleLED]:
    """Expand a named look, raising a useful error for an unknown name.

    Raises:
        ValueError: if the look does not exist.
    """
    try:
        look = LOOKS[name]
    except KeyError as err:
        raise ValueError(
            f"unknown look {name!r}; available: {', '.join(sorted(LOOKS))}"
        ) from err
    return look.build(led_count, palette)
