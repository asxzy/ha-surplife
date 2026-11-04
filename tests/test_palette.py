"""Tests for the effect-palette text entity.

The light entity can carry one colour, so this entity is the only surface in
the UI that can express a multi-colour effect. These tests check the things
that would make it silently useless: that it shows the running palette, that it
accepts the documented grammar, and that it refuses a palette the effect cannot
use instead of quietly sending one colour.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.surplife.settings import EFFECTS_KEY

PALETTE = "text.soffit_west_effect_colours"
LIGHT = "light.soffit_west"


async def _set(hass: HomeAssistant, text: str) -> None:
    await hass.services.async_call(
        "text",
        "set_value",
        {"entity_id": PALETTE, "value": text},
        blocking=True,
    )


async def _run_effect(hass: HomeAssistant, name: str) -> None:
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "effect": name},
        blocking=True,
    )


async def test_palette_entity_exists_and_is_enabled(hass, loaded_entry) -> None:
    """It is a normal control, not a hidden escape hatch.

    The per-LED pattern text is disabled by default because it is a command
    grammar. This one is the everyday way to set several colours, so it has to
    be visible without being switched on first.
    """
    state = hass.states.get(PALETTE)
    assert state is not None, "the palette entity was not created"
    assert state.attributes["friendly_name"] == "Soffit West Effect colours"


async def test_palette_shows_the_running_effects_colours(hass, loaded_entry) -> None:
    """The state is the palette, in order, so the order is visible at a glance."""
    options = dict(loaded_entry.options)
    options[EFFECTS_KEY] = {
        "Chase": {
            "colors": [[255, 0, 0, 0], [0, 255, 0, 0]],
            "speed": 50,
            "density": 50,
            "direction": 1,
        }
    }
    hass.config_entries.async_update_entry(loaded_entry, options=options)
    await hass.async_block_till_done()
    await _run_effect(hass, "Chase")

    assert hass.states.get(PALETTE).state == "#ff0000, #00ff00"


async def test_setting_a_palette_applies_it_to_the_running_effect(
    hass, loaded_entry, patched_client
) -> None:
    """Typing a list recolours the pattern rather than stopping it."""
    await _run_effect(hass, "Chase")
    patched_client.set_effect.reset_mock()
    patched_client.set_levels.reset_mock()

    await _set(hass, "red, #00ff00, #0000ff")

    assert patched_client.set_effect.called
    patched_client.set_levels.assert_not_called()
    palette = hass.states.get(LIGHT).attributes["palette"]
    assert len(palette) == 3
    assert palette[0].startswith("#ff0000")
    assert palette[2].startswith("#0000ff")
    # The effect is still running, which is the whole point.
    assert hass.states.get(LIGHT).attributes["effect"] == "Chase"


async def test_palette_survives_reselecting_the_effect(hass, loaded_entry) -> None:
    """What was typed is saved, so re-picking the effect restores it."""
    await _run_effect(hass, "Chase")
    await _set(hass, "#112233, #445566")

    saved = loaded_entry.options[EFFECTS_KEY]["Chase"]["colors"]
    assert [tuple(colour) for colour in saved] == [(17, 34, 51, 0), (68, 85, 102, 0)]


async def test_white_level_is_accepted_in_the_palette(hass, loaded_entry) -> None:
    """``w<n>`` drives the real W channel, not a simulated grey."""
    await _run_effect(hass, "Chase")
    await _set(hass, "#000000, w100, #0000ff")

    saved = loaded_entry.options[EFFECTS_KEY]["Chase"]["colors"]
    assert tuple(saved[0]) == (0, 0, 0, 100), "the white level did not reach the W channel"


async def test_a_palette_longer_than_the_effect_allows_is_refused(
    hass, loaded_entry, patched_client
) -> None:
    """``Fireworks`` takes three colours; a fourth must be an error.

    Silently dropping the extra colour would leave the user believing they had
    set four.
    """
    await _run_effect(hass, "Fireworks")
    with pytest.raises(Exception) as err:
        await _set(hass, "red, green, blue, yellow")
    assert "at most 3" in str(err.value)


async def test_a_single_colour_effect_refuses_a_palette(
    hass, loaded_entry, patched_client
) -> None:
    """``Wave`` uses one colour, so a second would be inert."""
    await _run_effect(hass, "Wave")
    with pytest.raises(Exception) as err:
        await _set(hass, "red, blue")
    assert "at most 1" in str(err.value)


async def test_a_generated_effect_refuses_a_palette(
    hass, loaded_entry, patched_client
) -> None:
    """``Rainbow Bridge`` paints its own colours, so a palette is meaningless."""
    await _run_effect(hass, "Rainbow Bridge")
    with pytest.raises(Exception) as err:
        await _set(hass, "red, blue")
    assert "paints its own colours" in str(err.value)


async def test_garbage_is_rejected_without_sending_anything(
    hass, loaded_entry, patched_client
) -> None:
    """A typo must not half-apply, and must say what was wrong."""
    await _run_effect(hass, "Chase")
    patched_client.set_effect.reset_mock()
    with pytest.raises(Exception) as err:
        await _set(hass, "not-a-colour")
    assert "invalid palette" in str(err.value)
    patched_client.set_effect.assert_not_called()


async def test_the_entity_says_what_the_effect_accepts(
    hass, loaded_entry, patched_client
) -> None:
    """The attributes make the field self-explaining, with no docs to read."""
    await _run_effect(hass, "Fireworks")
    attributes = hass.states.get(PALETTE).attributes
    assert attributes["effect"] == "Fireworks"
    assert attributes["max_colours"] == 3
    assert "3" in attributes["hint"]
    assert "w" in attributes["grammar"]


async def test_a_single_colour_sets_a_flat_colour_when_no_effect_runs(
    hass, loaded_entry, patched_client
) -> None:
    """With no effect showing, the field is a plain colour control.

    ``Static`` is in the effect list but is not an effect: it shows one flat
    colour. Refusing it here would leave the field useless for exactly the
    case its state already describes, so one colour is applied as a colour.
    """
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT, "effect": "Static"}, blocking=True
    )
    patched_client.set_levels.reset_mock()

    await _set(hass, "#ff8000")

    assert patched_client.set_levels.called
    state = hass.states.get(LIGHT)
    assert tuple(state.attributes["rgb_color"]) == (255, 128, 0)
    assert hass.states.get(PALETTE).state == "#ff8000"


async def test_a_list_is_refused_when_no_effect_runs(
    hass, loaded_entry, patched_client
) -> None:
    """A list with no effect would silently use only its first colour."""
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT, "effect": "Static"}, blocking=True
    )
    with pytest.raises(Exception) as err:
        await _set(hass, "red, blue")
    assert "single colour" in str(err.value)
