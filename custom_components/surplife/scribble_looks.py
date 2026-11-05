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

from .protocol.constants import BLINK_FAST, BLINK_MODES, BLINK_NONE, BLINK_SLOW
from .protocol.scribble import ScribbleLED
from .settings import ScribbleRun

Color = tuple[int, int, int]

#: A palette entry. Three channels mean colour only; a fourth means the W
#: channel level 0-100 as well, which is how a *real* white is requested.
Paint = tuple[int, int, int] | tuple[int, int, int, int]

RED: Final = (255, 0, 0)
GREEN: Final = (0, 200, 60)
BLUE: Final = (0, 80, 255)
WARM_WHITE: Final = (255, 190, 120)
WHITE: Final = (255, 255, 255)
ORANGE: Final = (255, 96, 0)
PURPLE: Final = (128, 0, 255)
PINK: Final = (255, 40, 120)
GOLD: Final = (255, 200, 0)

#: Builder signature: given the palette and the LED count, return one setting
#: per LED.
Builder = Callable[[Sequence[Paint], int], list[ScribbleLED]]


def _paint(colour: Paint, **settings: object) -> ScribbleLED:
    """Turn a palette entry into an LED setting.

    A four-element entry lights the white LED as well. When its colour channels
    are all zero *and* the white level is non-zero it becomes "white only", so
    the LED is driven by the W channel alone rather than by three colour LEDs
    imitating white. A black entry stays black: ``(0, 0, 0, 0)`` is a dark LED,
    not a white one.
    """
    if len(colour) == 4:
        red, green, blue, white = colour
        if white and not (red or green or blue):
            return ScribbleLED(rgb=None, white=int(white), **settings)  # type: ignore[arg-type]
        return ScribbleLED(
            rgb=(red, green, blue),
            white=int(white),
            **settings,  # type: ignore[arg-type]
        )
    return ScribbleLED(rgb=tuple(colour), **settings)  # type: ignore[arg-type]


def _channels(colour: Paint) -> tuple[int, ...]:
    """Pad a palette entry to four channels for blending."""
    if len(colour) == 4:
        return tuple(colour)
    return (*colour, 0)


def _cycle(palette: Sequence[Paint], index: int) -> Paint:
    """Pick a palette colour by position, wrapping around."""
    return palette[index % len(palette)]


def _solid(palette: Sequence[Paint], count: int) -> list[ScribbleLED]:
    """Every LED one colour."""
    return [_paint(palette[0]) for _ in range(count)]


def _stripes(palette: Sequence[Paint], count: int) -> list[ScribbleLED]:
    """Alternating blocks of three LEDs."""
    return [_paint(_cycle(palette, i // 3)) for i in range(count)]


def _candy_cane(palette: Sequence[Paint], count: int) -> list[ScribbleLED]:
    """Two-colour diagonal banding: blocks of two, offset every other row."""
    return [_paint(_cycle(palette, (i + i // 2) // 2)) for i in range(count)]


def _gradient(palette: Sequence[Paint], count: int) -> list[ScribbleLED]:
    """Smooth interpolation across the palette, white channel included."""
    stops = [_channels(colour) for colour in palette]
    if len(stops) == 1:
        return _solid(palette, count)
    span = max(count - 1, 1)
    leds: list[ScribbleLED] = []
    for index in range(count):
        position = index / span * (len(stops) - 1)
        lower = min(int(position), len(stops) - 2)
        blend = position - lower
        first, second = stops[lower], stops[lower + 1]
        blended = tuple(
            round(a + (b - a) * blend) for a, b in zip(first, second, strict=True)
        )
        leds.append(_paint(blended))  # type: ignore[arg-type]
    return leds


def _rainbow(palette: Sequence[Paint], count: int) -> list[ScribbleLED]:
    """A full hue sweep, ignoring the palette's colours but not its length."""
    span = max(count - 1, 1)
    leds: list[ScribbleLED] = []
    for index in range(count):
        red, green, blue = colorsys.hsv_to_rgb(index / span, 1.0, 1.0)
        leds.append(
            ScribbleLED(rgb=(round(red * 255), round(green * 255), round(blue * 255)))
        )
    return leds


def _firefly(palette: Sequence[Paint], count: int) -> list[ScribbleLED]:
    """Mostly dark, with a deterministic scatter of single lit LEDs."""
    leds: list[ScribbleLED] = []
    for index in range(count):
        # A cheap integer hash keeps the scatter stable and spread out without
        # importing `random` and without a seed to get wrong.
        lit = ((index * 2654435761) >> 7) % 5 == 0
        leds.append(
            _paint(
                _cycle(palette, index),
                blink_mode=BLINK_SLOW,
            )
            if lit
            else ScribbleLED()
        )
    return leds


def _twinkle(palette: Sequence[Paint], count: int) -> list[ScribbleLED]:
    """Alternating blocks where every third LED blinks fast."""
    leds: list[ScribbleLED] = []
    for index in range(count):
        colour = _cycle(palette, index // 3)
        blink = BLINK_FAST if index % 3 == 0 else BLINK_SLOW
        leds.append(_paint(colour, blink_mode=blink))
    return leds


def _chase(palette: Sequence[Paint], count: int) -> list[ScribbleLED]:
    """A single bright block on a dark background, with lit blinkers."""
    block = max(count // 4, 3)
    leds: list[ScribbleLED] = []
    for index in range(count):
        lit = index < block
        leds.append(
            _paint(_cycle(palette, 0), blink_mode=BLINK_FAST, blink_speed=100)
            if lit
            else ScribbleLED()
        )
    return leds


def _wreath(palette: Sequence[Paint], count: int) -> list[ScribbleLED]:
    """Half-lit alternating blocks — a garland look."""
    leds: list[ScribbleLED] = []
    for index in range(count):
        if index % 2 == 0:
            leds.append(_paint(_cycle(palette, index // 2), blink_mode=BLINK_SLOW))
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
    palette: tuple[Paint, ...] = (WARM_WHITE,)
    builder: Builder = field(default=_solid)
    supports_palette: bool = True

    @property
    def palette_size(self) -> int:
        """How many colour pickers this look can use.

        This is the length of the look's own palette, which is exactly the
        number of colours its builder distinguishes. A look built from one
        colour asks for one picker rather than showing empty ones that would be
        ignored.
        """
        return len(self.palette)

    def build(
        self, led_count: int, palette: Sequence[Paint] | None = None
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
    name: str, led_count: int, palette: Sequence[Paint] | None = None
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


def build_runs(runs: Sequence[ScribbleRun], led_count: int) -> list[ScribbleLED]:
    """Expand hand-painted runs into exactly ``led_count`` LEDs.

    This is the painting canvas in data form: a run says "the next *n* LEDs are
    this colour, blinking this way", and the runs are laid end to end. It is the
    same output the app's doodle view produces, expressed as something a Home
    Assistant form can render and a user can edit without learning a grammar.

    Two rules keep it predictable:

    * **Short patterns are padded with dark LEDs.** The device keeps a stateful
      per-LED framebuffer, so an LED left unmentioned would keep whatever it was
      showing and the result would depend on history.
    * **A run longer than the strip is trimmed; a run with no colour is dark.**
      Trimming is not an error because shrinking the run count is a normal edit,
      and dark is not black because both are the same wire payload but the
      editor means "off" by an empty row.

    Raises:
        ValueError: if the LED count is not positive.
    """
    if led_count < 1:
        raise ValueError(f"LED count must be positive, got {led_count}")

    leds: list[ScribbleLED] = []
    for run in runs:
        if len(leds) >= led_count:
            break
        blink_mode = BLINK_MODES.get(run.blink, BLINK_NONE)
        if run.colour is None:
            setting = ScribbleLED(blink_mode=blink_mode, blink_speed=run.blink_speed)
        else:
            setting = _paint(
                tuple(run.colour),
                blink_mode=blink_mode,
                blink_speed=run.blink_speed,
            )
        leds.extend(setting for _ in range(min(max(1, run.length), led_count - len(leds))))

    leds.extend(ScribbleLED() for _ in range(led_count - len(leds)))
    return leds
