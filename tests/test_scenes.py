"""Tests for the scene model and date-driven selection.

These are pure-stdlib tests: no Home Assistant, no sockets. The model is the part
that decides what "the Christmas scene" means and which scene belongs to a given
day, so it is worth testing without any of the platform around it.
"""

from __future__ import annotations

import datetime as dt

import pytest

from custom_components.surplife.scenes import (
    MAX_SCENES,
    Scene,
    normalize_name,
    parse_tags,
    select_for_date,
)
from custom_components.surplife.scenes.model import parse_tags as model_parse_tags

CHRISTMAS = Scene(
    name="christmas",
    mode="effect",
    effect="Flowing Water",
    colors=((255, 0, 0), (0, 200, 60)),
    speed=25,
    density=75,
    start="11-15",
    end="12-31",
    tags=("holidays",),
    priority=10,
)

HALLOWEEN = Scene(
    name="halloween",
    mode="effect",
    effect="Chase",
    colors=((255, 96, 0), (128, 0, 255)),
    start="10-01",
    end="10-31",
    tags=("holidays",),
)

WARM = Scene(name="warm_white", mode="white", white=200, tags=("hoa_safe",))


# -- names and tags --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Christmas", "christmas"),
        ("New Year's Eve", "new_year_s_eve"),
        ("  candy  cane  ", "candy_cane"),
        ("a/b", "a_b"),
        ("Résumé", "r_sum"),
        ("A" * 80, "a" * 48),
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "///", "!!!"])
def test_normalize_name_rejects_empty(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_name(raw)


def test_normalize_name_collapses_runs() -> None:
    assert normalize_name("a---___---b") == "a_b"


def test_parse_tags_accepts_string_and_list() -> None:
    assert parse_tags("a, b ,,a") == ("a", "b")
    assert parse_tags(["B", " a "]) == ("b", "a")
    assert parse_tags(None) == ()
    assert model_parse_tags("x") == ("x",)


# -- validation ------------------------------------------------------------


def test_title_defaults_to_pretty_name() -> None:
    assert Scene(name="candy_cane").title == "Candy Cane"


def test_explicit_title_is_kept() -> None:
    assert Scene(name="x", title="Merry").title == "Merry"


def test_unknown_mode_rejected() -> None:
    with pytest.raises(ValueError, match="mode must be"):
        Scene(name="x", mode="disco")


def test_effect_scene_needs_known_effect() -> None:
    with pytest.raises(ValueError, match="need an effect name"):
        Scene(name="x", mode="effect")
    with pytest.raises(ValueError, match="unknown effect"):
        Scene(name="x", mode="effect", effect="Nope")


def test_effect_names_are_case_insensitive() -> None:
    assert Scene(name="x", mode="effect", effect="chase").effect == "chase"


def test_scribble_scene_needs_pattern() -> None:
    with pytest.raises(ValueError, match="per-LED pattern"):
        Scene(name="x", mode="scribble")


def test_look_scene_needs_known_look() -> None:
    with pytest.raises(ValueError, match="need a look name"):
        Scene(name="x", mode="look")
    with pytest.raises(ValueError, match="unknown look"):
        Scene(name="x", mode="look", look="plaid")


def test_segments_scene_limited_to_twenty() -> None:
    with pytest.raises(ValueError, match="at most 20"):
        Scene(
            name="x",
            mode="segments",
            segments=tuple((1, 2, 3) for _ in range(21)),
        )


def test_segments_scene_allows_dark_entries() -> None:
    scene = Scene(
        name="x", mode="segments", segments=((255, 0, 0), None, (0, 0, 255))
    )
    assert scene.segments[1] is None


@pytest.mark.parametrize("field", ["brightness", "white", "speed", "density"])
@pytest.mark.parametrize("value", [-1, 256])
def test_range_validation(field: str, value: int) -> None:
    with pytest.raises(ValueError, match=field):
        Scene(name="x", **{field: value})


def test_brightness_range_is_255() -> None:
    assert Scene(name="x", brightness=255).brightness == 255


def test_speed_upper_bound_is_100() -> None:
    with pytest.raises(ValueError, match=r"speed must be 0\.\.100"):
        Scene(name="x", speed=101)


def test_direction_must_be_known() -> None:
    with pytest.raises(ValueError, match="direction must be"):
        Scene(name="x", direction="sideways")


@pytest.mark.parametrize("field", ["start", "end"])
@pytest.mark.parametrize("value", ["1-01", "13-01", "01-32", "2026-01-01", "Jan"])
def test_bad_dates_rejected(field: str, value: str) -> None:
    with pytest.raises(ValueError, match="MM-DD"):
        Scene(name="x", **{field: value})


def test_empty_dates_become_none() -> None:
    scene = Scene(name="x", start="", end=None)
    assert scene.start is None and scene.end is None


def test_colour_validation() -> None:
    with pytest.raises(ValueError, match="invalid colour"):
        Scene(name="x", colors=((1, 2),))
    with pytest.raises(ValueError, match="0-255"):
        Scene(name="x", colors=((1, 2, 300),))


# -- serialisation ---------------------------------------------------------


def test_round_trip_preserves_everything() -> None:
    original = Scene(
        name="full",
        title="Full",
        mode="look",
        look="candy_cane",
        colors=((255, 0, 0), (255, 255, 255)),
        brightness=200,
        white=0,
        speed=30,
        density=70,
        direction="r2l",
        scribble_effect=2,
        start="12-01",
        end="01-06",
        tags=("holidays", "outdoor"),
        priority=5,
    )
    assert Scene.from_dict(original.to_dict()) == original


def test_to_dict_omits_empty_fields() -> None:
    data = Scene(name="x").to_dict()
    assert "pattern" not in data
    assert "look" not in data
    assert "start" not in data
    assert "tags" not in data


def test_from_dict_ignores_unknown_keys() -> None:
    scene = Scene.from_dict({"name": "x", "mode": "rgb", "future_field": 1})
    assert scene.name == "x"


# -- seasons ---------------------------------------------------------------


def test_no_window_always_matches() -> None:
    assert WARM.in_window(dt.date(2026, 6, 15))
    assert WARM.season == "all year"


def test_window_is_inclusive() -> None:
    assert CHRISTMAS.in_window(dt.date(2026, 11, 15))
    assert CHRISTMAS.in_window(dt.date(2026, 12, 31))
    assert not CHRISTMAS.in_window(dt.date(2026, 11, 14))
    assert not CHRISTMAS.in_window(dt.date(2027, 1, 1))


def test_window_wraps_year_end() -> None:
    nye = Scene(name="nye", start="12-01", end="01-06")
    assert nye.in_window(dt.date(2026, 12, 25))
    assert nye.in_window(dt.date(2027, 1, 3))
    assert not nye.in_window(dt.date(2027, 1, 7))
    assert not nye.in_window(dt.date(2026, 11, 30))


def test_partial_window_defaults() -> None:
    from_march = Scene(name="a", start="03-01")
    assert from_march.in_window(dt.date(2026, 3, 1))
    assert from_march.in_window(dt.date(2026, 12, 31))
    assert not from_march.in_window(dt.date(2026, 2, 28))

    to_march = Scene(name="b", end="03-01")
    assert to_march.in_window(dt.date(2026, 1, 1))
    assert not to_march.in_window(dt.date(2026, 3, 2))


def test_matches_filters_by_tag() -> None:
    day = dt.date(2026, 10, 20)
    assert HALLOWEEN.matches(day)
    assert HALLOWEEN.matches(day, "holidays")
    assert not HALLOWEEN.matches(day, "hoa_safe")


# -- selection -------------------------------------------------------------


def test_select_prefers_priority_when_not_random() -> None:
    low = Scene(name="low", priority=1)
    high = Scene(name="high", priority=9)
    assert select_for_date([low, high], dt.date(2026, 6, 1), randomize=False) is high


def test_select_returns_none_when_nothing_matches() -> None:
    assert select_for_date([CHRISTMAS], dt.date(2026, 6, 1)) is None
    assert select_for_date([], dt.date(2026, 6, 1)) is None


def test_select_filters_by_topic() -> None:
    scene = select_for_date(
        [CHRISTMAS, WARM], dt.date(2026, 12, 25), topic="hoa_safe", randomize=False
    )
    assert scene is WARM


def test_randomized_choice_is_stable_for_the_same_day() -> None:
    scenes = [CHRISTMAS, HALLOWEEN, WARM]
    day = dt.date(2026, 10, 20)
    first = select_for_date(scenes, day)
    assert first is not None
    for _ in range(20):
        assert select_for_date(scenes, day) is first


def test_randomized_choice_does_not_depend_on_input_order() -> None:
    scenes = [CHRISTMAS, HALLOWEEN, WARM]
    day = dt.date(2026, 10, 20)
    forward = select_for_date(scenes, day)
    backward = select_for_date(list(reversed(scenes)), day)
    assert forward is backward


def test_randomized_choice_varies_across_days() -> None:
    # Not a guarantee for any single pair of days, but over a season the pick
    # must move at least once or the feature is pointless.
    scenes = [Scene(name=f"scene_{index}") for index in range(6)]
    days = [dt.date(2026, 10, day) for day in range(1, 29)]
    picks = {select_for_date(scenes, day).name for day in days}  # type: ignore[union-attr]
    assert len(picks) > 1


def test_describe_covers_every_mode() -> None:
    scenes = [
        Scene(name="a", mode="rgb", colors=((1, 2, 3),)),
        Scene(name="b", mode="white", white=10),
        Scene(name="c", mode="effect", effect="Chase"),
        Scene(name="d", mode="segments", segments=((1, 2, 3),)),
        Scene(name="e", mode="scribble", pattern="35xred"),
        Scene(name="f", mode="look", look="rainbow"),
    ]
    for scene in scenes:
        assert scene.describe().startswith(scene.mode)


def test_max_scenes_constant_is_sane() -> None:
    assert 10 <= MAX_SCENES <= 1000
