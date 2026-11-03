"""End-to-end behaviour of the effect UI across its three entry points.

An effect can be configured from the light card's dropdown, the Configure
dialog, or the speed/density sliders, and all three write to the same saved
settings. These tests exist because that agreement is the feature: they caught
a real bug where the light card overrode a multi-colour palette with a single
colour, and another where the sliders saved nothing.
"""

from __future__ import annotations

from homeassistant.components.light import ATTR_EFFECT
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant

from custom_components.surplife.const import DOMAIN
from custom_components.surplife.settings import EFFECTS_KEY, effect_settings

LIGHT = "light.soffit_west"
SPEED = "number.soffit_west_effect_speed"
DENSITY = "number.soffit_west_effect_density"
DIRECTION = "select.soffit_west_direction"
EFFECT_SELECT = "select.soffit_west_effect"


async def test_effect_from_the_light_card_uses_the_saved_palette(
    hass: HomeAssistant, loaded_entry, patched_client
) -> None:
    """Picking a multi-colour effect must not collapse it to one colour.

    The light card has no way to express a palette, so it must not invent one:
    the saved palette is what the effect was configured with.
    """
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            EFFECTS_KEY: {
                "Static Gradient": {"colors": [[255, 0, 0, 0], [0, 0, 255, 0]]}
            }
        },
    )
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Static Gradient"},
        blocking=True,
    )
    colours = patched_client.set_effect.call_args.args[1]
    assert colours == [(255, 0, 0, 0), (0, 0, 255, 0)]


async def test_effect_from_the_light_card_uses_saved_parameters(
    hass: HomeAssistant, loaded_entry, patched_client
) -> None:
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={EFFECTS_KEY: {"Chase": {"speed": 12, "density": 34, "direction": 2}}},
    )
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Chase"},
        blocking=True,
    )
    kwargs = patched_client.set_effect.call_args.kwargs
    assert kwargs["speed"] == 12
    assert kwargs["density"] == 34
    assert kwargs["direction"] == 2


async def test_effect_selector_uses_the_saved_palette(
    hass: HomeAssistant, loaded_entry, patched_client
) -> None:
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={EFFECTS_KEY: {"Static Fill": {"colors": [[0, 0, 0, 100]]}}},
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: EFFECT_SELECT, "option": "Static Fill"},
        blocking=True,
    )
    assert patched_client.set_effect.call_args.args[1] == [(0, 0, 0, 100)]


async def test_explicit_colour_still_wins(
    hass: HomeAssistant, loaded_entry, patched_client
) -> None:
    """An automation passing a colour must override the saved palette."""
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={EFFECTS_KEY: {"Chase": {"colors": [[1, 2, 3, 0]]}}},
    )
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    await coordinator.async_set_effect("Chase", colors=[(9, 9, 9)])
    assert patched_client.set_effect.call_args.args[1] == [(9, 9, 9)]


async def test_unknown_effect_is_still_rejected(
    hass: HomeAssistant, loaded_entry
) -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    with pytest.raises(HomeAssistantError, match="unknown effect"):
        await coordinator.async_set_effect("Hyperdrive")


# -- sliders ---------------------------------------------------------------


async def test_speed_slider_reapplies_and_saves(
    hass: HomeAssistant, loaded_entry, patched_client
) -> None:
    """A slider move is a live control, and it persists."""
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    await coordinator.async_set_effect("Chase")
    patched_client.set_effect.reset_mock()

    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: SPEED, "value": 17},
        blocking=True,
    )
    patched_client.set_effect.assert_called()
    assert patched_client.set_effect.call_args.kwargs["speed"] == 17
    assert effect_settings(loaded_entry.options, "Chase").speed == 17


async def test_density_slider_reapplies_and_saves(
    hass: HomeAssistant, loaded_entry, patched_client
) -> None:
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    await coordinator.async_set_effect("Chase")
    patched_client.set_effect.reset_mock()

    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: DENSITY, "value": 63},
        blocking=True,
    )
    patched_client.set_effect.assert_called()
    assert patched_client.set_effect.call_args.kwargs["density"] == 63
    assert effect_settings(loaded_entry.options, "Chase").density == 63


async def test_slider_without_an_active_effect_only_remembers(
    hass: HomeAssistant, loaded_entry, patched_client
) -> None:
    """There is nothing to re-apply, but the value must still be used next time."""
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    coordinator.runtime.effect_name = None
    patched_client.set_effect.reset_mock()

    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: SPEED, "value": 17},
        blocking=True,
    )
    patched_client.set_effect.assert_not_called()
    assert coordinator.runtime.effect_speed == 17


async def test_direction_selector_reapplies_and_saves(
    hass: HomeAssistant, loaded_entry, patched_client
) -> None:
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    await coordinator.async_set_effect("Chase")
    patched_client.set_effect.reset_mock()

    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: DIRECTION, "option": "right_to_left"},
        blocking=True,
    )
    patched_client.set_effect.assert_called()
    assert patched_client.set_effect.call_args.kwargs["direction"] == 2
    assert effect_settings(loaded_entry.options, "Chase").direction == 2


async def test_direction_selector_reports_the_stored_direction(
    hass: HomeAssistant, loaded_entry
) -> None:
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    coordinator.runtime.effect_direction = 2
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    state = hass.states.get(DIRECTION)
    assert state.state == "right_to_left"


# -- state attributes ------------------------------------------------------


async def test_light_describes_the_active_palette(
    hass: HomeAssistant, loaded_entry
) -> None:
    """A dashboard needs to see the palette to render anything meaningful."""
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            EFFECTS_KEY: {
                "Static Gradient": {"colors": [[255, 0, 0, 0], [0, 0, 0, 100]]}
            }
        },
    )
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Static Gradient"},
        blocking=True,
    )
    attributes = hass.states.get(LIGHT).attributes
    assert attributes["palette"] == ["#ff0000", "#000000 w100"]
    assert attributes["palette_mode"] == "colours"


async def test_light_says_which_controls_an_effect_uses(
    hass: HomeAssistant, loaded_entry
) -> None:
    """A split-flap of controls that do nothing is the bug this prevents."""
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Static Fill"},
        blocking=True,
    )
    attributes = hass.states.get(LIGHT).attributes
    assert attributes["effect_uses_density"] is False
    assert attributes["effect_uses_direction"] is False
    assert attributes["palette_mode"] == "single"


async def test_effect_select_describes_the_effect(
    hass: HomeAssistant, loaded_entry
) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: EFFECT_SELECT, "option": "Rainbow Bridge"},
        blocking=True,
    )
    attributes = hass.states.get(EFFECT_SELECT).attributes
    assert attributes["palette_mode"] == "generated"
    assert attributes["colours"] == 0
    assert attributes["uses_speed"] is True


async def test_look_select_reports_the_saved_palette(
    hass: HomeAssistant, loaded_entry
) -> None:
    from custom_components.surplife.settings import LOOKS_KEY

    hass.config_entries.async_update_entry(
        loaded_entry,
        options={LOOKS_KEY: {"stripes": {"palette": [[1, 2, 3, 0], [0, 0, 0, 80]]}}},
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.soffit_west_per_led_look", "option": "stripes"},
        blocking=True,
    )
    attributes = hass.states.get("select.soffit_west_per_led_look").attributes
    assert attributes["palette"] == ["#010203", "#000000 w80"]
    assert attributes["colours"] == 2


async def test_look_select_applies_the_saved_palette(
    hass: HomeAssistant, loaded_entry, patched_client
) -> None:
    """Picking a look restores the colours configured for it."""
    from custom_components.surplife.settings import LOOKS_KEY

    hass.config_entries.async_update_entry(
        loaded_entry,
        options={LOOKS_KEY: {"solid": {"palette": [[0, 0, 0, 100]]}}},
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.soffit_west_per_led_look", "option": "solid"},
        blocking=True,
    )
    leds = patched_client.set_scribble.call_args.args[0]
    # A pure-white entry drives the W channel alone, not three colour LEDs.
    assert all(led.rgb is None and led.white == 100 for led in leds)


async def test_look_ignores_its_saved_palette_when_given_one(
    hass: HomeAssistant, loaded_entry, patched_client
) -> None:
    """A scene passing a palette must beat the look's saved colours."""
    from custom_components.surplife.settings import LOOKS_KEY

    hass.config_entries.async_update_entry(
        loaded_entry,
        options={LOOKS_KEY: {"solid": {"palette": [[0, 0, 0, 100]]}}},
    )
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    await coordinator.async_set_look("solid", palette=[(7, 8, 9)])
    leds = patched_client.set_scribble.call_args.args[0]
    assert all(led.rgb == (7, 8, 9) for led in leds)
