"""Reproduce the theme-to-wire palette gaps.

Prints, for a set of dates, the scene ``generate_scene`` returns and the
palette the look/effect builder would actually consume, so a dropped colour is
visible without a strip.
"""

from __future__ import annotations

import datetime as dt
import random

from custom_components.surplife.effect_profile import palette_for, profile_for
from custom_components.surplife.scribble_looks import LOOKS
from custom_components.surplife.themes import generate_scene, theme_for


def wire_palette(scene) -> tuple[tuple[int, ...], ...]:
    """What the builder actually consumes for this scene."""
    if scene.mode == "look":
        look = LOOKS[scene.look]
        if not look.supports_palette:
            return look.palette
        return tuple(scene.colors) or look.palette
    return tuple(palette_for(scene.effect, list(scene.colors)))


def main() -> None:
    for day in (
        dt.date(2026, 3, 20),
        dt.date(2026, 6, 15),
        dt.date(2026, 6, 1),
        dt.date(2026, 9, 20),
        dt.date(2026, 12, 20),
        dt.date(2026, 7, 4),
    ):
        theme = theme_for(day)
        print(f"== {day} {theme.key} ({theme.kind}) ==")
        seen: set[tuple[str, str | None]] = set()
        for _ in range(400):
            scene = generate_scene(day, random.Random())
            key = (scene.mode, scene.look or scene.effect)
            if key in seen:
                continue
            seen.add(key)
            wire = wire_palette(scene)
            consumed = profile_for(scene.effect).palette.value if scene.effect else "look"
            print(
                f"   mode={scene.mode:6} look={scene.look!s:9} effect={scene.effect!s:16}"
                f" consumed={consumed:9} declared={len(scene.colors)}"
                f" wire={len(wire)}"
            )
            print(f"      declared={scene.colors}")
            print(f"      wire    ={wire}")
        print()


if __name__ == "__main__":
    main()
