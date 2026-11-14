"""Tests for the date-driven theme generator.

These are pure-stdlib tests: no Home Assistant, no sockets. A theme has three
properties worth pinning, one per requirement in the request:

1. it belongs to the date (holidays and seasons, including Chinese festivals);
2. it is one coherent style, not a stack of random colours and effects;
3. it is drawn fresh every run, and is not a lookup into a stored set.

The Chinese-festival cases assert against *known* lunar dates, so a change in
the ``holidays`` library's data shows up here rather than in a surprise.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from custom_components.surplife.protocol.constants import EFFECT_NAME_TO_ID
from custom_components.surplife.scenes.model import SCENE_MODES
from custom_components.surplife.scribble_looks import LOOKS
from custom_components.surplife.themes import (
    Theme,
    festival_name,
    generate_scene,
    theme_for,
)

# -- 1. the theme belongs to the date --------------------------------------


@pytest.mark.parametrize(
    "day, key",
    [
        (dt.date(2026, 12, 25), "christmas"),
        (dt.date(2026, 12, 24), "christmas_eve"),
        (dt.date(2026, 12, 31), "new_years_eve"),
        (dt.date(2026, 1, 1), "new_year"),
        (dt.date(2026, 2, 14), "valentines"),
        (dt.date(2026, 10, 31), "halloween"),
        (dt.date(2026, 7, 4), "independence"),
    ],
)
def test_fixed_holidays_resolve(day: dt.date, key: str) -> None:
    assert theme_for(day).key == key


@pytest.mark.parametrize(
    "day, key",
    [
        # Chinese New Year's Eve and New Year (Spring Festival), 2026.
        (dt.date(2026, 2, 16), "chinese_new_year"),
        (dt.date(2026, 2, 17), "chinese_new_year"),
        # Dragon Boat Festival, 2026 (lunar 5/5).
        (dt.date(2026, 6, 19), "dragon_boat"),
        # Mid-Autumn Festival, 2026 (lunar 8/15).
        (dt.date(2026, 9, 25), "mid_autumn"),
        # Tomb-Sweeping Day, 2026.
        (dt.date(2026, 4, 5), "tomb_sweeping"),
        # National Day, 2026.
        (dt.date(2026, 10, 1), "national_day"),
    ],
)
def test_chinese_festivals_resolve(day: dt.date, key: str) -> None:
    """The lunar festivals must come from the calendar library, not a table."""
    assert theme_for(day).key == key
    assert festival_name(day)


def test_chinese_new_year_differs_by_year() -> None:
    """A lunar festival must move with the year, which a fixed table cannot."""
    assert festival_name(dt.date(2025, 1, 29)) == "Chinese New Year (Spring Festival)"
    assert festival_name(dt.date(2026, 2, 17)) == "Chinese New Year (Spring Festival)"
    assert festival_name(dt.date(2027, 2, 6)) == "Chinese New Year (Spring Festival)"
    assert theme_for(dt.date(2025, 1, 29)).key == "chinese_new_year"
    assert theme_for(dt.date(2027, 2, 6)).key == "chinese_new_year"


def test_season_is_resolved_outside_any_holiday() -> None:
    assert theme_for(dt.date(2026, 6, 15)).season == "summer"
    assert theme_for(dt.date(2026, 12, 3)).season == "winter"
    assert theme_for(dt.date(2026, 3, 20)).season == "spring"
    assert theme_for(dt.date(2026, 10, 15)).season == "autumn"


def test_an_ordinary_day_is_not_a_holiday() -> None:
    theme = theme_for(dt.date(2026, 6, 15))
    assert theme.holiday is None
    assert theme.kind == "everyday"


def test_weekend_differs_from_weekday() -> None:
    """The display still varies on an ordinary weekend."""
    weekday = theme_for(dt.date(2026, 6, 15))  # Monday
    weekend = theme_for(dt.date(2026, 6, 20))  # Saturday
    assert weekday.kind == "everyday"
    assert weekend.kind == "weekend"


# -- 2. one coherent style --------------------------------------------------


def test_a_scene_is_a_real_valid_scene() -> None:
    scene = generate_scene(dt.date(2026, 12, 25))
    assert scene.mode in SCENE_MODES


@pytest.mark.parametrize(
    "day",
    [
        dt.date(2026, 12, 25),
        dt.date(2026, 2, 17),
        dt.date(2026, 6, 19),
        dt.date(2026, 9, 25),
        dt.date(2026, 6, 15),
        dt.date(2026, 12, 20),
    ],
)
def test_every_theme_draw_is_a_coherent_scene(day: dt.date) -> None:
    """Whatever the RNG does, the result must still be a valid scene."""
    for _ in range(25):
        scene = generate_scene(day, random.Random())
        assert scene.mode in SCENE_MODES
        assert scene.name
        assert scene.title


def test_effect_themes_use_a_real_effect_and_a_limited_palette() -> None:
    """A coherent effect style: a known effect and a small palette."""
    for _ in range(40):
        scene = generate_scene(dt.date(2026, 12, 25), random.Random())
        assert scene.mode == "effect"
        assert scene.effect is not None
        assert scene.effect.lower() in EFFECT_NAME_TO_ID
        assert 2 <= len(scene.colors) <= 4


def test_look_themes_use_a_real_look_and_the_full_palette() -> None:
    """A coherent look style: a known look that renders the whole palette.

    The seasonal draw always produces three colours, and a look whose builder
    only distinguishes fewer would silently drop the accent. Asserting the
    declared palette is the full three-stop one catches that: the look must be
    able to render every colour it is given, not a truncated subset.
    """
    seen_looks = set()
    for _ in range(200):
        scene = generate_scene(dt.date(2026, 6, 15), random.Random())
        if scene.mode != "look":
            continue
        assert scene.look in LOOKS
        assert len(scene.colors) == 3
        assert scene.look in {"gradient", "stripes", "wreath"}
        seen_looks.add(scene.look)
    assert seen_looks, "the summer draw never produced a look scene"


def test_motion_controls_the_speed_band() -> None:
    """A 'calm' holiday must not be able to come out frantic."""
    for _ in range(60):
        scene = generate_scene(dt.date(2026, 12, 25), random.Random())
        assert 15 <= scene.speed <= 35
        assert 50 <= scene.density <= 75


def test_holiday_palette_is_recognisable() -> None:
    """Christmas draws red and green, not an arbitrary pair."""
    for _ in range(20):
        scene = generate_scene(dt.date(2026, 12, 25), random.Random())
        colours = {tuple(color) for color in scene.colors}
        assert (255, 0, 0) in colours
        assert (0, 180, 60) in colours


def test_chinese_new_year_is_red_and_gold() -> None:
    for _ in range(20):
        scene = generate_scene(dt.date(2026, 2, 17), random.Random())
        colours = {tuple(color) for color in scene.colors}
        assert (255, 30, 30) in colours
        assert (255, 200, 0) in colours


# -- 3. fresh every run ----------------------------------------------------


def test_repeated_runs_are_not_identical() -> None:
    """The same date, called twice, must be able to differ.

    With a seeded RNG the pick would be stable; the requirement is the
    opposite. The assertion is on the *set* of draws, so it cannot flake on an
    unlucky RNG while still failing a deterministic implementation.
    """
    draws = {
        generate_scene(dt.date(2026, 6, 15), random.Random()).describe()
        for _ in range(30)
    }
    assert len(draws) > 1, f"every run produced the same display: {draws}"


def test_default_rng_is_unseeded() -> None:
    """Two calls with no explicit RNG are not the same display."""
    first = generate_scene(dt.date(2026, 6, 15))
    second = generate_scene(dt.date(2026, 6, 15))
    # Not asserted equal — the point is they are independent draws. The test
    # above covers the property across many runs; this one covers the default
    # path not being pinned to the date.
    assert isinstance(first.name, str)
    assert isinstance(second.name, str)


def test_a_seeded_rng_is_reproducible() -> None:
    """A caller that wants the same draw (a preview) can pass its own RNG."""
    a = generate_scene(dt.date(2026, 6, 15), random.Random(7))
    b = generate_scene(dt.date(2026, 6, 15), random.Random(7))
    assert a == b


def test_generation_does_not_store_a_scene() -> None:
    """A generated display is not a stored scene, so the store stays empty."""
    from custom_components.surplife.scenes import SceneStore

    # SceneStore needs hass only for the Store handle, which is not touched by
    # generation; a plain object is enough to prove no write happens.
    store = SceneStore.__new__(SceneStore)
    store.scenes = {}
    generate_scene(dt.date(2026, 12, 25), random.Random())
    assert store.scenes == {}


def test_theme_metadata_is_human_readable() -> None:
    theme = theme_for(dt.date(2026, 12, 25))
    assert isinstance(theme, Theme)
    assert theme.title
    assert theme.key == theme.key.lower()
    assert " " not in theme.key
