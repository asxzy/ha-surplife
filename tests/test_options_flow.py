"""Tests for the Configure dialog's per-effect and per-look panels.

This is the UI that replaces "send a raw command and look at the strip", so the
tests assert what a user actually sees: that the panel for a single-colour
effect asks for one colour and not eight, that a generated effect asks for
none, that the W-channel level survives the round trip, and that saving pushes
the values to the controller.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.surplife.const import DOMAIN
from custom_components.surplife.settings import EFFECTS_KEY, LOOKS_KEY


@pytest.fixture
async def options_flow(hass: HomeAssistant, loaded_entry):
    """Start the options flow for the loaded entry."""
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return result


def _fields(result: dict) -> dict:
    """The form's field names, in order."""
    schema = result["data_schema"]
    return {str(key): key for key in schema.schema}


def _default(result: dict, name: str) -> object:
    """The default value the form would show for a field."""
    for key in result["data_schema"].schema:
        if str(key) == name:
            return key.default() if callable(key.default) else key.default
    raise AssertionError(f"{name} is not in the form")


# -- menu ------------------------------------------------------------------


async def test_menu_offers_the_three_panels(options_flow) -> None:
    assert set(options_flow["menu_options"]) == {"effect", "look", "interval"}


async def test_menu_reports_how_much_is_configured(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            EFFECTS_KEY: {"Wave": {"speed": 10}},
            LOOKS_KEY: {"stripes": {"speed": 10}},
        },
    )
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert result["description_placeholders"]["effects_configured"] == "1"
    assert result["description_placeholders"]["looks_configured"] == "1"


# -- poll interval ---------------------------------------------------------


async def test_interval_step_saves_into_options(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "interval"}
    )
    assert result["step_id"] == "interval"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["scan_interval"] == 120


async def test_interval_change_updates_the_coordinator(
    hass: HomeAssistant, loaded_entry
) -> None:
    """The field must actually change the poll cadence, not just be stored."""
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    import datetime as dt

    before = coordinator.update_interval
    hass.config_entries.async_update_entry(loaded_entry, options={"scan_interval": 300})
    await hass.async_block_till_done()
    assert coordinator.update_interval == dt.timedelta(seconds=300)
    assert before != coordinator.update_interval


# -- effect panel ----------------------------------------------------------


async def test_effect_list_labels_what_each_effect_uses(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "effect"}
    )
    assert result["step_id"] == "effect"
    labels = [
        option["label"]
        for option in result["data_schema"]
        .schema[next(iter(result["data_schema"].schema))]
        .config["options"]
    ]
    by_name = {label.split(" — ")[0]: label for label in labels}
    assert "1 colour" in by_name["Wave"]
    assert "no colour" in by_name["Rainbow Bridge"]
    assert "colours" in by_name["Static Gradient"]


async def test_single_colour_effect_asks_for_one_picker(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    """This is the whole point: no inert colour pickers."""
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "effect"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Wave"}
    )
    assert result["step_id"] == "effect_settings"
    fields = _fields(result)
    assert "color_1" in fields
    assert "color_2" not in fields
    assert "speed" in fields
    assert "density" in fields
    assert "direction" in fields


async def test_generated_effect_asks_for_no_colour(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "effect"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Rainbow Bridge"}
    )
    fields = _fields(result)
    assert not any(field.startswith("color_") for field in fields)
    assert "speed" in fields


async def test_gradient_effect_asks_for_several_colours(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "effect"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Static Gradient"}
    )
    fields = _fields(result)
    assert {"color_1", "color_2", "color_3", "color_4"} <= set(fields)
    # A still image has nothing to reverse or repeat.
    assert "direction" not in fields
    assert "density" not in fields


async def test_effect_panel_saves_and_applies(
    hass: HomeAssistant, loaded_entry, options_flow, patched_client
) -> None:
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "effect"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Chase"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "color_1": [255, 0, 0],
            "white_1": 0,
            "color_2": [0, 0, 255],
            "white_2": 0,
            "speed": 30,
            "density": 70,
            "direction": "r2l",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options[EFFECTS_KEY]["Chase"] == {
        "colors": [[255, 0, 0, 0], [0, 0, 255, 0]],
        "speed": 30,
        "density": 70,
        "direction": 2,
    }
    # Saving applies, so the strip changes without a second step.
    patched_client.set_effect.assert_called()
    kwargs = patched_client.set_effect.call_args.kwargs
    assert kwargs["speed"] == 30
    assert kwargs["density"] == 70
    assert kwargs["direction"] == 2


async def test_effect_panel_keeps_the_white_channel(
    hass: HomeAssistant, loaded_entry, options_flow, patched_client
) -> None:
    """White is a separate LED, so a level must survive the panel."""
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "effect"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Wave"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "color_1": [0, 0, 0],
            "white_1": 100,
            "speed": 50,
            "density": 50,
            "direction": "l2r",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options[EFFECTS_KEY]["Wave"]["colors"] == [[0, 0, 0, 100]]
    colours = patched_client.set_effect.call_args.args[1]
    assert colours == [(0, 0, 0, 100)]


async def test_empty_colour_picker_means_no_opinion(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    """Leaving a colour empty must not send black to the strip."""
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "effect"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Static Gradient"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"speed": 40}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # No colour stored at all -- distinct from storing black, which would mean
    # "turn the LEDs off".
    assert loaded_entry.options[EFFECTS_KEY]["Static Gradient"].get("colors", []) == []
    assert loaded_entry.options[EFFECTS_KEY]["Static Gradient"]["speed"] == 40


async def test_effect_panel_prefills_saved_values(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            EFFECTS_KEY: {
                "Chase": {
                    "colors": [[1, 2, 3, 40]],
                    "speed": 10,
                    "density": 20,
                    "direction": 2,
                }
            }
        },
    )
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "effect"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Chase"}
    )
    assert _default(result, "white_1") == 40
    assert _default(result, "speed") == 10
    assert _default(result, "density") == 20
    assert _default(result, "direction") == "r2l"


# -- look panel ------------------------------------------------------------


async def test_look_list_states_how_many_colours_each_uses(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "look"}
    )
    assert result["step_id"] == "look"
    selector = result["data_schema"].schema[next(iter(result["data_schema"].schema))]
    labels = {option["value"]: option["label"] for option in selector.config["options"]}
    assert "1 colour" in labels["solid"]
    assert "2 colours" in labels["stripes"]
    assert "fixed colours" in labels["rainbow"]


async def test_look_panel_offers_one_colour_per_slot(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "look"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"look": "stripes"}
    )
    fields = _fields(result)
    assert {"color_1", "color_2"} <= set(fields)
    assert "color_3" not in fields
    assert "scribble_effect" in fields


async def test_rainbow_look_has_no_colour_pickers(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    """Its palette is fixed, so offering pickers would be misleading."""
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "look"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"look": "rainbow"}
    )
    fields = _fields(result)
    assert not any(field.startswith("color_") for field in fields)
    assert "speed" in fields


async def test_look_panel_saves_and_applies(
    hass: HomeAssistant, loaded_entry, options_flow, patched_client
) -> None:
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "look"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"look": "stripes"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "color_1": [255, 0, 0],
            "white_1": 0,
            "color_2": [0, 0, 0],
            "white_2": 100,
            "scribble_effect": "1",
            "speed": 60,
            "density": 40,
            "direction": "r2l",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options[LOOKS_KEY]["stripes"] == {
        "palette": [[255, 0, 0, 0], [0, 0, 0, 100]],
        "speed": 60,
        "density": 40,
        "direction": 2,
        "effect": 1,
    }
    patched_client.set_scribble.assert_called()


async def test_look_panel_prefills_saved_palette(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={LOOKS_KEY: {"stripes": {"palette": [[9, 8, 7, 55]]}}},
    )
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "look"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"look": "stripes"}
    )
    assert _default(result, "white_1") == 55
    # The untouched second slot keeps the look's own default.
    assert _default(result, "color_2") is not None


# -- applying --------------------------------------------------------------


async def test_saving_without_an_active_effect_still_saves(
    hass: HomeAssistant, loaded_entry, options_flow, patched_client
) -> None:
    """Configuring an effect before using it must not fail."""
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    coordinator.runtime.effect_name = None
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "effect"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Wave"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"color_1": [1, 2, 3], "white_1": 0, "speed": 5, "density": 5},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "Wave" in loaded_entry.options[EFFECTS_KEY]


async def test_unreachable_controller_does_not_lose_the_settings(
    hass: HomeAssistant, loaded_entry, options_flow, patched_client
) -> None:
    """A device that is offline must not discard what the user entered."""
    from homeassistant.exceptions import HomeAssistantError

    patched_client.set_effect = Mock(side_effect=HomeAssistantError("offline"))
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "effect"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Wave"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"color_1": [1, 2, 3], "white_1": 0, "speed": 5, "density": 5},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options[EFFECTS_KEY]["Wave"]["speed"] == 5
