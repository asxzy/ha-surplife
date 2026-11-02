"""Bundled holiday presets.

Each preset is a small recipe applied through the coordinator's normal command
methods, so a preset can never do anything the services cannot. They are
deliberately simple and readable: the point is that a user can copy one and
tweak the colours.

Presets exist partly to replace the app's cloud-side device grouping. Applying
the same preset to several controllers from one automation keeps them in sync,
and because each preset is deterministic it does not matter which order the
controllers are addressed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from ..coordinator import SurplifeCoordinator

Color = tuple[int, int, int]

ORANGE: Final[Color] = (255, 96, 0)
PURPLE: Final[Color] = (128, 0, 255)
RED: Final[Color] = (255, 0, 0)
GREEN: Final[Color] = (0, 200, 60)
WARM_WHITE: Final[Color] = (255, 190, 120)
AMBER: Final[Color] = (255, 160, 40)
WHITE: Final[Color] = (255, 255, 255)
PINK: Final[Color] = (255, 40, 120)
GOLD: Final[Color] = (255, 200, 0)
BLUE: Final[Color] = (0, 60, 255)


@dataclass(frozen=True, slots=True)
class Preset:
    """A named lighting recipe.

    Attributes:
        name: Machine name, used as the service value.
        title: Human-readable label.
        description: What it looks like, in one line.
        effect: Native effect name to use, or ``None`` for a static colour.
        colors: Palette; a single colour means a solid fill.
        speed: Effect speed 0-100.
        density: Effect density 0-100.
        segments: Optional explicit 20-segment palette. When set, the preset
            renders as segments instead of an effect or a solid colour.
    """

    name: str
    title: str
    description: str
    effect: str | None = None
    colors: tuple[Color, ...] = (WHITE,)
    speed: int = 50
    density: int = 80
    segments: tuple[Color | None, ...] | None = None

    async def apply(self, coordinator: SurplifeCoordinator) -> None:
        """Apply the preset to one controller."""
        if self.segments is not None:
            await coordinator.async_set_segments(list(self.segments))
        elif self.effect is not None:
            await coordinator.async_set_effect(
                self.effect,
                speed=self.speed,
                density=self.density,
                colors=list(self.colors),
            )
        else:
            await coordinator.async_set_rgb(self.colors[0], 255)
        if not coordinator.runtime.is_on:
            await coordinator.async_set_power(True)


def _alternating(first: Color, second: Color, block: int = 2) -> tuple[Color, ...]:
    """Build a 20-segment palette alternating two colours in blocks."""
    palette: list[Color] = []
    for index in range(20):
        palette.append(first if (index // block) % 2 == 0 else second)
    return tuple(palette)


PRESETS: Final[dict[str, Preset]] = {
    preset.name: preset
    for preset in (
        Preset(
            name="halloween",
            title="Halloween",
            description="Orange and purple alternating segments with a slow chase.",
            effect="Chase",
            colors=(ORANGE, PURPLE),
            speed=30,
            density=70,
        ),
        Preset(
            name="christmas",
            title="Christmas",
            description="Red and green alternating segments, flowing slowly.",
            effect="Flowing Water",
            colors=(RED, GREEN),
            speed=25,
            density=75,
        ),
        Preset(
            name="thanksgiving",
            title="Thanksgiving",
            description="Warm amber glow with a gentle breathe.",
            effect="Breathe",
            colors=(AMBER, WARM_WHITE),
            speed=20,
            density=60,
        ),
        Preset(
            name="canada_day",
            title="Canada Day",
            description="Red and white alternating blocks, static.",
            segments=_alternating(RED, WHITE, block=2),
        ),
        Preset(
            name="valentines",
            title="Valentine's Day",
            description="Red and pink alternating blocks with a slow twinkle.",
            effect="Twinkling Stars",
            colors=(RED, PINK),
            speed=25,
            density=60,
        ),
        Preset(
            name="new_years",
            title="New Year's Eve",
            description="Gold and white sparkle.",
            effect="Fireworks",
            colors=(GOLD, WHITE),
            speed=70,
            density=80,
        ),
        Preset(
            name="warm_white",
            title="Warm white",
            description="Neutral warm white — the HOA-safe everyday setting.",
            colors=(WARM_WHITE,),
        ),
    )
}
