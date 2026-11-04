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


async def test_menu_offers_the_panels(options_flow) -> None:
    assert set(options_flow["menu_options"]) == {
        "recolour",
        "effect",
        "look",
        "interval",
    }


async def test_recolour_is_the_first_menu_option(options_flow) -> None:
    """The everyday question -- "make this a different colour" -- comes first.

    Configure an effect is where an effect is set up once; recolouring is what
    you do to it afterwards. Putting the setup panel first meant the common
    action sat below the uncommon one.
    """
    assert options_flow["menu_options"][0] == "recolour"


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
    # Four useful stops, not the wire's full eight plus eight white sliders.
    assert "color_5" not in fields
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


async def test_effect_panel_sends_the_effect_once(
    hass: HomeAssistant, loaded_entry, options_flow, patched_client
) -> None:
    """Saving must not send the effect twice.

    Two things apply it: the flow itself, and the entry's update listener,
    which re-pushes the active effect after any options change. Home Assistant
    dispatches that listener eagerly, so without a skip flag both run and a
    strip that was already showing the effect flashes a second time.
    """
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    coordinator.runtime.effect_name = "Chase"

    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "effect"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Chase"}
    )
    patched_client.set_effect.reset_mock()
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "color_1": [255, 0, 0],
            "white_1": 0,
            "color_2": [0, 0, 255],
            "white_2": 0,
            "speed": 30,
            "density": 70,
            "direction": "l2r",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert patched_client.set_effect.call_count == 1, (
        "saving the panel should send the effect exactly once, got "
        f"{patched_client.set_effect.call_count}"
    )
    kwargs = patched_client.set_effect.call_args.kwargs
    assert (kwargs["speed"], kwargs["density"], kwargs["direction"]) == (30, 70, 1)
    # The skip flag must be consumed, or the next unrelated save would be
    # silently swallowed.
    assert coordinator.consume_skip_reapply() is False


async def test_look_panel_sends_the_look_once(
    hass: HomeAssistant, loaded_entry, options_flow, patched_client
) -> None:
    """The per-LED panel must also render exactly once.

    A look render clears ``effect_name``, so the listener's "re-apply the
    active effect" branch has nothing to do here. The skip flag is still set as
    belt-and-braces, and more importantly it must be *consumed*: a flag left
    set would silently swallow the next unrelated re-apply.
    """
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "look"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"look": "stripes"}
    )
    patched_client.set_scribble.reset_mock()
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
    await hass.async_block_till_done()

    assert patched_client.set_scribble.call_count == 1, (
        "saving the look panel should render exactly once, got "
        f"{patched_client.set_scribble.call_count}"
    )
    assert patched_client.set_scribble.call_args.kwargs["direction"] == 2


async def test_failed_apply_leaves_no_claim(
    hass: HomeAssistant, loaded_entry, options_flow, patched_client
) -> None:
    """An offline controller must not swallow the listener's retry.

    When the panel's own apply fails, there is nothing to skip -- the listener
    is the best-effort retry, and it may succeed where the direct call did not.
    """
    from unittest.mock import Mock

    from homeassistant.exceptions import HomeAssistantError

    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
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
    await hass.async_block_till_done()
    assert coordinator.consume_skip_reapply() is False


async def test_a_no_op_save_does_not_swallow_the_next_change(
    hass: HomeAssistant, loaded_entry, patched_client
) -> None:
    """A claim must never outlive the save it was made for.

    Home Assistant returns early from ``async_update_entry`` when nothing
    changed, so the update listener never runs and a bare skip *flag* would
    survive. It would then silently suppress the next genuine re-apply. Keying
    the claim to the exact options payload makes a stale claim unmatchable, and
    it is cleared on consumption either way.
    """
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    await coordinator.async_set_effect("Chase")

    # Claim one payload, then save a different one: the listener must run.
    coordinator.expect_no_reapply({"effects": {"Wave": {"speed": 1}}})
    patched_client.set_effect.reset_mock()
    hass.config_entries.async_update_entry(
        loaded_entry, options={EFFECTS_KEY: {"Chase": {"speed": 90}}}
    )
    await hass.async_block_till_done()
    assert patched_client.set_effect.call_count == 1
    assert patched_client.set_effect.call_args.kwargs["speed"] == 90


async def test_a_claim_is_consumed_even_when_it_does_not_match(
    hass: HomeAssistant, loaded_entry
) -> None:
    """Consumption always clears, so a mismatch cannot linger either."""
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    coordinator.expect_no_reapply({"a": 1})
    # The saved options are not the claimed ones, so the listener must run...
    assert coordinator.consume_skip_reapply() is False
    # ...and the stale claim must be gone, not left to match something later.
    assert coordinator.consume_skip_reapply() is False


async def test_a_claim_matches_the_payload_it_was_made_for(
    hass: HomeAssistant, loaded_entry
) -> None:
    """The happy path, stated directly: same options in, claim honoured."""
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    hass.config_entries.async_update_entry(
        loaded_entry, options={EFFECTS_KEY: {"Chase": {"speed": 20}}}
    )
    coordinator.expect_no_reapply(dict(loaded_entry.options))
    assert coordinator.consume_skip_reapply() is True


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


# -- building a palette one colour at a time -------------------------------


async def test_colour_can_be_added_one_at_a_time(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    """A palette effect grows a colour per round trip, like the phone app.

    Home Assistant fixes a form's schema when the form is shown, so the only
    way to let a user add a colour is to return to the step with a larger
    schema. This asserts that the tick really does grow the form rather than
    being a checkbox that saves and does nothing.
    """
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "effect"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Chase"}
    )
    assert _fields(result)["color_2"]
    assert "color_3" not in _fields(result)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "color_1": [255, 0, 0],
            "white_1": 0,
            "color_2": [0, 0, 255],
            "white_2": 0,
            "speed": 50,
            "density": 50,
            "direction": "l2r",
            "add_colour": True,
        },
    )
    # Still a form, not a saved entry: adding a colour must not commit yet.
    assert result["type"] is FlowResultType.FORM
    fields = _fields(result)
    assert "color_3" in fields
    # The colours already picked are carried over, so nothing is retyped.
    assert _default(result, "color_1") == [255, 0, 0]
    assert _default(result, "color_2") == [0, 0, 255]


async def test_added_colour_is_saved_in_order(
    hass: HomeAssistant, loaded_entry, options_flow, patched_client
) -> None:
    """Three colours survive as three, in the order they were picked."""
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
            "color_2": [0, 255, 0],
            "white_2": 0,
            "speed": 50,
            "density": 50,
            "direction": "l2r",
            "add_colour": True,
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "color_1": [255, 0, 0],
            "white_1": 0,
            "color_2": [0, 255, 0],
            "white_2": 0,
            "color_3": [0, 0, 255],
            "white_3": 0,
            "speed": 50,
            "density": 50,
            "direction": "l2r",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options[EFFECTS_KEY]["Chase"]["colors"] == [
        [255, 0, 0, 0],
        [0, 255, 0, 0],
        [0, 0, 255, 0],
    ]


async def test_single_colour_effect_offers_no_add_button(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    """``Wave`` uses one colour, so it must not offer to add more.

    Offering eight slots to an effect that reads one is the wall-of-fields
    problem the generated panel exists to avoid.
    """
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "effect"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Wave"}
    )
    fields = _fields(result)
    assert "color_1" in fields
    assert "color_2" not in fields
    assert "add_colour" not in fields
    assert "remove_colour" not in fields


async def test_add_button_disappears_at_the_cap(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    """A gradient stops offering to add once it has the most useful stops.

    The panel's cap is a UI choice -- four stops for a gradient -- so the Add
    tick must vanish at four, not keep growing towards the wire's eight.
    """
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "effect"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Static Gradient"}
    )
    for expected in (4,):
        assert f"color_{expected}" in _fields(result)
    # Already at the cap on first open, so no Add tick at all.
    assert "add_colour" not in _fields(result)
    assert "color_5" not in _fields(result)


async def test_colour_can_be_removed_again(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    """Remove takes a row away without saving, and keeps the earlier colours."""
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
            "speed": 50,
            "density": 50,
            "direction": "l2r",
            "add_colour": True,
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "color_1": [255, 0, 0],
            "white_1": 0,
            "color_2": [0, 255, 0],
            "white_2": 0,
            "color_3": [0, 0, 255],
            "white_3": 0,
            "speed": 50,
            "density": 50,
            "direction": "l2r",
            "remove_colour": True,
        },
    )
    assert result["type"] is FlowResultType.FORM
    fields = _fields(result)
    assert "color_3" not in fields
    assert _default(result, "color_1") == [255, 0, 0]
    assert _default(result, "color_2") == [0, 255, 0]


async def test_holes_between_colours_keep_their_positions(
    hass: HomeAssistant, loaded_entry, options_flow, patched_client
) -> None:
    """An empty middle row must not renumber the colours after it.

    Adding a row reads the form back position by position. If an empty row were
    dropped, the colour below it would slide up and silently become a different
    stop of the gradient.
    """
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
            # Row 2 is left out entirely, which is what an untouched optional
            # colour picker submits.
            "speed": 50,
            "density": 50,
            "direction": "l2r",
            "add_colour": True,
        },
    )
    assert result["type"] is FlowResultType.FORM
    # The third row is new and empty; the first is untouched.
    assert _fields(result)["color_3"]
    assert _default(result, "color_1") == [255, 0, 0]
    # An empty row stays empty rather than being filled with the colour below.
    from voluptuous import UNDEFINED

    assert _default(result, "color_2") is UNDEFINED


async def test_panel_description_has_no_raw_markdown() -> None:
    """Descriptions render as plain text, so ``**bold**`` would show literally."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for name in ("translations/en.json", "strings.json"):
        data = json.loads(
            (root / "custom_components/surplife" / name).read_text()
        )
        text = data["options"]["step"]["effect_settings"]["description"]
        assert "**" not in text, f"{name} has raw markdown in a description"


# -- recolouring the running effect ----------------------------------------


async def _start_effect(hass: HomeAssistant, name: str, colours=None) -> None:
    """Put an effect on the strip, with a saved palette if one is given."""
    from custom_components.surplife.settings import EffectSettings, with_effect

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    if colours is not None:
        hass.config_entries.async_update_entry(
            entry,
            options=with_effect(
                dict(entry.options),
                name,
                EffectSettings(
                    colors=tuple(colours), speed=50, density=50, direction=1
                ),
            ),
        )
        await hass.async_block_till_done()
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.soffit_west", "effect": name},
        blocking=True,
    )


async def test_recolour_shows_the_running_effects_colours(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    """The wheels start where the strip is, so nothing is retyped from scratch."""
    await _start_effect(
        hass, "Chase", [(255, 0, 0, 0), (0, 255, 0, 0), (0, 0, 255, 0)]
    )
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "recolour"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "recolour"
    # Three colours on the strip, three colour wheels.
    fields = _fields(result)
    for slot in range(1, 4):
        assert f"color_{slot}" in fields
        assert f"white_{slot}" in fields
    assert "color_4" not in fields
    assert tuple(_default(result, "color_1")) == (255, 0, 0)
    assert tuple(_default(result, "color_2")) == (0, 255, 0)
    assert tuple(_default(result, "color_3")) == (0, 0, 255)


async def test_recolour_applies_the_new_colours_and_keeps_running(
    hass: HomeAssistant, loaded_entry, options_flow, patched_client
) -> None:
    """Submitting recolours in place -- the effect is not cancelled."""
    await _start_effect(hass, "Chase", [(255, 0, 0, 0), (0, 255, 0, 0)])
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "recolour"}
    )
    patched_client.set_levels.reset_mock()
    patched_client.set_effect.reset_mock()

    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"],
        {
            "color_1": [10, 20, 30],
            "white_1": 0,
            "color_2": [40, 50, 60],
            "white_2": 0,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    patched_client.set_levels.assert_not_called()
    palette = hass.states.get("light.soffit_west").attributes["palette"]
    assert len(palette) == 2
    assert palette[0].startswith("#0a141e")
    assert hass.states.get("light.soffit_west").attributes["effect"] == "Chase"


async def test_recolour_offers_no_effect_picker(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    """The running effect is the target, so there is nothing to choose."""
    await _start_effect(hass, "Chase", [(255, 0, 0, 0)])
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "recolour"}
    )
    assert "effect" not in _fields(result)


async def test_recolour_says_so_when_nothing_is_running(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    """Empty pickers that would do nothing are worse than a clear message."""
    # ``Static`` is how the integration leaves pattern mode: it sets
    # ``commanded_non_effect``, so the runtime knows the strip is a flat colour
    # even though the controller keeps reporting the last effect id.
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.soffit_west", "effect": "Static"},
        blocking=True,
    )
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "recolour"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_running_effect"


async def test_recolour_aborts_on_an_effect_that_paints_its_own_colours(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    """``Rainbow Bridge`` ignores a palette, so offering wheels would lie."""
    await _start_effect(hass, "Rainbow Bridge")
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "recolour"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "effect_paints_its_own_colours"


async def test_recolour_adds_a_row_without_saving(
    hass: HomeAssistant, loaded_entry, options_flow, patched_client
) -> None:
    """Add is a round trip: the strip is untouched until Submit."""
    await _start_effect(hass, "Chase", [(255, 0, 0, 0), (0, 255, 0, 0)])
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "recolour"}
    )
    patched_client.set_effect.reset_mock()

    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"],
        {
            "color_1": [255, 0, 0],
            "white_1": 0,
            "color_2": [0, 255, 0],
            "white_2": 0,
            "add_colour": True,
        },
    )

    assert result["type"] is FlowResultType.FORM
    patched_client.set_effect.assert_not_called()
    fields = _fields(result)
    assert "color_3" in fields
    # The colours already picked survive the round trip.
    assert tuple(_default(result, "color_1")) == (255, 0, 0)
    assert tuple(_default(result, "color_2")) == (0, 255, 0)


async def test_an_untouched_row_is_skipped_not_treated_as_black(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    """Clearing a row must shorten the palette, not fade it to black.

    A row with no default is the only way to submit no colour at all, so this
    adds a row (which has none) and submits without filling it in.
    """
    await _start_effect(hass, "Chase", [(255, 0, 0, 0)])
    await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "recolour"}
    )
    # Add a second row, which starts empty.
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"],
        {"color_1": [255, 0, 0], "white_1": 0, "add_colour": True},
    )
    assert result["type"] is FlowResultType.FORM
    assert "color_2" in _fields(result)

    # Submit leaving the second row empty.
    result = await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"color_1": [255, 0, 0], "white_1": 0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    saved = entry.options[EFFECTS_KEY]["Chase"]["colors"]
    assert len(saved) == 1, f"the empty row became a colour: {saved}"
    assert tuple(saved[0]) == (255, 0, 0, 0)


async def test_recolour_keeps_the_white_channel(
    hass: HomeAssistant, loaded_entry, options_flow
) -> None:
    """The W level drives the real white LED, not a simulated grey."""
    await _start_effect(hass, "Chase", [(255, 0, 0, 0), (0, 255, 0, 0)])
    await hass.config_entries.options.async_configure(
        options_flow["flow_id"], {"next_step_id": "recolour"}
    )
    await hass.config_entries.options.async_configure(
        options_flow["flow_id"],
        {
            "color_1": [0, 0, 0],
            "white_1": 100,
            "color_2": [0, 0, 255],
            "white_2": 0,
        },
    )
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    saved = entry.options[EFFECTS_KEY]["Chase"]["colors"]
    assert tuple(saved[0]) == (0, 0, 0, 100)
