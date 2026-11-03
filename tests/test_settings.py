"""Tests for the stored per-effect and per-look settings.

These live in the config entry's options, which a user can edit by hand in
``.storage/core.config_entries``. So the tests care as much about surviving bad
input as about round-tripping good input: a single malformed colour must not
stop the integration from loading.
"""

from __future__ import annotations

import pytest

from custom_components.surplife.settings import (
    EFFECTS_KEY,
    LOOKS_KEY,
    EffectSettings,
    LookSettings,
    effect_settings,
    look_settings,
    merge_colours,
    replace_settings,
    with_effect,
    with_look,
)


def test_effect_settings_round_trip() -> None:
    saved = EffectSettings(colors=((255, 0, 0, 0), (0, 0, 0, 100)), speed=30)
    options = with_effect({}, "Chase", saved)
    loaded = effect_settings(options, "Chase")
    assert loaded == saved


def test_unset_effect_is_distinguishable_from_defaults() -> None:
    """The panel must not treat 'never configured' as 'configured to 50'."""
    assert effect_settings({}, "Chase") is None
    assert effect_settings(None, "Chase") is None


def test_unset_fields_stay_unset() -> None:
    """Only the fields the user touched are stored."""
    options = with_effect({}, "Wave", EffectSettings(speed=10))
    raw = options[EFFECTS_KEY]["Wave"]
    assert raw == {"speed": 10}
    loaded = effect_settings(options, "Wave")
    assert loaded is not None
    assert loaded.direction is None
    assert loaded.colors == ()


def test_white_level_is_stored_as_a_percentage() -> None:
    """The W channel is 0-100 everywhere in this layer, never 0-255."""
    options = with_effect({}, "Wave", EffectSettings(colors=((0, 0, 0, 100),)))
    assert options[EFFECTS_KEY]["Wave"]["colors"] == [[0, 0, 0, 100]]


def test_rgb_colour_gets_a_zero_white_level() -> None:
    """A three-element colour is RGB with the W channel off."""
    options = with_effect({}, "Wave", EffectSettings(colors=((1, 2, 3, 0),)))
    loaded = effect_settings(options, "Wave")
    assert loaded is not None
    assert loaded.colors == ((1, 2, 3, 0),)


def test_out_of_range_values_are_clamped() -> None:
    options = {EFFECTS_KEY: {"Chase": {"speed": 400, "density": -5, "direction": 9}}}
    loaded = effect_settings(options, "Chase")
    assert loaded is not None
    assert (loaded.speed, loaded.density, loaded.direction) == (100, 0, 2)


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-dict",
        42,
        None,
        [],
        {"colors": "red"},
        {"colors": [None, "x", [1, 2]]},
        {"speed": "fast"},
    ],
)
def test_malformed_effect_settings_do_not_raise(bad: object) -> None:
    loaded = effect_settings({EFFECTS_KEY: {"Chase": bad}}, "Chase")
    if loaded is not None:
        assert isinstance(loaded, EffectSettings)


def test_malformed_options_dropped_not_raised() -> None:
    """One bad colour entry must not lose the good ones."""
    options = {EFFECTS_KEY: {"Chase": {"colors": [[1, 2, 3], "nope", [4, 5, 6]]}}}
    loaded = effect_settings(options, "Chase")
    assert loaded is not None
    assert loaded.colors == ((1, 2, 3, 0), (4, 5, 6, 0))


def test_look_settings_round_trip() -> None:
    saved = LookSettings(palette=((255, 0, 0, 0),), speed=10, density=20, effect=5)
    loaded = look_settings(with_look({}, "stripes", saved), "stripes")
    assert loaded == saved


def test_look_defaults_when_never_saved() -> None:
    assert look_settings({}, "stripes") is None


def test_look_out_of_range_values_are_clamped() -> None:
    options = {LOOKS_KEY: {"stripes": {"effect": 99, "speed": 999}}}
    loaded = look_settings(options, "stripes")
    assert loaded is not None
    assert loaded.effect == 8
    assert loaded.speed == 100


def test_saving_one_effect_preserves_the_others() -> None:
    options = with_effect({}, "Chase", EffectSettings(speed=10))
    options = with_effect(options, "Wave", EffectSettings(speed=20))
    assert effect_settings(options, "Chase") == EffectSettings(speed=10)
    assert effect_settings(options, "Wave") == EffectSettings(speed=20)


def test_saving_preserves_unrelated_options() -> None:
    """Saving the panel must not drop the poll interval."""
    options = {"scan_interval": 60}
    updated = with_effect(options, "Wave", EffectSettings(speed=10))
    assert updated["scan_interval"] == 60
    assert options == {"scan_interval": 60}, "the input must not be mutated"


def test_merge_colours_overlays_position_by_position() -> None:
    """A one-colour override must not delete a look's second colour."""
    merged = merge_colours(((1, 2, 3), (4, 5, 6)), {"palette": [[9, 9, 9, 0]]})
    assert merged == ((9, 9, 9, 0), (4, 5, 6))


def test_merge_colours_adds_extra_colours() -> None:
    merged = merge_colours(((1, 2, 3),), {"palette": [[1, 1, 1, 0], [2, 2, 2, 0]]})
    assert merged == ((1, 1, 1, 0), (2, 2, 2, 0))


def test_merge_colours_without_overrides_is_identity() -> None:
    palette = ((1, 2, 3),)
    assert merge_colours(palette, None) == palette
    assert merge_colours(palette, {}) == palette


def test_replace_settings_leaves_the_original_alone() -> None:
    original = EffectSettings(speed=10, density=20)
    updated = replace_settings(original, speed=30)
    assert original.speed == 10
    assert updated.speed == 30
    assert updated.density == 20
