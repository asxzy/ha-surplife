"""Integration tests for scenes, per-LED looks and date-driven selection.

Everything above the socket is real: the config entry is loaded, the scene store
writes through Home Assistant's own storage helper, and the services are called
the way an automation would call them.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import Mock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.surplife.const import DOMAIN
from custom_components.surplife.protocol import SurplifeState, Timer
from custom_components.surplife.scenes import Scene, SceneStore

from . import golden

HOST = "192.168.1.50"
LIGHT = "light.soffit_west"


def _state() -> SurplifeState:
    from custom_components.surplife.protocol.state import parse_state

    frame = bytearray(golden.STATE_PRISTINE_INNER)
    frame[6] = 0x23
    return parse_state(bytes(frame))


@pytest.fixture
def client():
    """A stand-in client with the surface the coordinator uses."""
    with patch(
        "custom_components.surplife.coordinator.SurplifeClient", autospec=True
    ) as factory:
        instance = factory.return_value
        instance.get_state = Mock(side_effect=_state)
        instance.set_power = Mock()
        instance.set_color = Mock()
        instance.set_warm_white = Mock()
        instance.set_effect = Mock()
        instance.set_segments = Mock()
        instance.set_scribble = Mock(return_value=2)
        instance.get_timers = Mock(return_value=[Timer(slot=1, active=False)])
        instance.close = Mock()
        yield instance


@pytest.fixture
async def entry(hass: HomeAssistant, client):
    """A loaded config entry."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Soffit West",
        data={
            CONF_HOST: HOST,
            CONF_NAME: "Soffit West",
            "mac": "AABBCCDDEEFF",
            "hw_version": "AK001-ZJ21413",
            "firmware_info": "+ok=B6_26_20240514_ZG-BL-UFO",
            "led_count": 35,
        },
        unique_id="AABBCCDDEEFF",
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


@pytest.fixture
def store(hass: HomeAssistant, entry) -> SceneStore:
    """The integration-wide scene store created at setup."""
    return hass.data[DOMAIN]["__scenes__"]


# -- store lifecycle -------------------------------------------------------


async def test_store_is_created_at_setup(hass: HomeAssistant, entry, store) -> None:
    assert isinstance(store, SceneStore)
    assert len(store) == 0


async def test_store_is_shared_not_per_entry(hass: HomeAssistant, entry, store) -> None:
    """A second controller must see the same scenes, not its own copy."""
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Soffit East",
        data={
            CONF_HOST: "192.168.1.51",
            CONF_NAME: "Soffit East",
            "mac": "AABBCCDDEE11",
            "hw_version": "AK001-ZJ21413",
            "led_count": 35,
        },
        unique_id="AABBCCDDEE11",
    )
    other.add_to_hass(hass)
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DOMAIN]["__scenes__"] is store


async def test_store_persists_across_reload(hass: HomeAssistant, entry, store) -> None:
    """Scenes survive a config-entry reload, which is the normal HA lifecycle."""
    await hass.services.async_call(
        DOMAIN,
        "save_scene",
        {"name": "christmas", "mode": "effect", "effect": "Chase"},
        blocking=True,
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    # The store is integration-wide, so the reload keeps the same object and
    # its scenes; the point of the test is that the scenes are still there.
    reloaded: SceneStore = hass.data[DOMAIN]["__scenes__"]
    assert reloaded is store
    assert "christmas" in reloaded.scenes


async def test_store_reads_scenes_from_disk(hass: HomeAssistant) -> None:
    """A fresh store object repopulates from `.storage` — i.e. a real restart."""
    first = SceneStore(hass)
    await first.async_load()
    await first.async_put(Scene(name="christmas", mode="rgb", colors=((255, 0, 0),)))

    second = SceneStore(hass)
    await second.async_load()
    assert second.names() == ["christmas"]
    assert second.require("christmas").colors == ((255, 0, 0),)


async def test_store_skips_corrupt_records(hass: HomeAssistant) -> None:
    """One bad record must not take the rest of the collection down."""
    from homeassistant.helpers.storage import Store

    Store(hass, 1, "surplife.scenes")
    first = SceneStore(hass)
    await first.async_load()
    await first.async_put(Scene(name="good", mode="rgb"))

    # Write a record that cannot validate, as a hand-edited file might.
    raw = await first._store.async_load()
    raw["scenes"].append({"name": "bad", "mode": "effect", "effect": "Nope"})
    await first._store.async_save(raw)

    second = SceneStore(hass)
    await second.async_load()
    assert second.names() == ["good"]


async def test_store_survives_missing_file(hass: HomeAssistant) -> None:
    """An empty store loads cleanly rather than raising."""
    store = SceneStore(hass)
    await store.async_load()
    assert store.names() == []


# -- save / delete ---------------------------------------------------------


async def test_save_scene_service(hass: HomeAssistant, entry, store) -> None:
    await hass.services.async_call(
        DOMAIN,
        "save_scene",
        {
            "name": "Halloween",
            "mode": "effect",
            "effect": "Chase",
            "colors": ["#ff6000", "#8000ff"],
            "speed": 30,
            "density": 70,
        },
        blocking=True,
    )
    scene = store.require("halloween")
    assert scene.title == "Halloween"
    assert scene.colors == ((255, 96, 0), (128, 0, 255))
    assert scene.speed == 30


async def test_save_scene_is_an_upsert(hass: HomeAssistant, entry, store) -> None:
    for speed in (10, 90):
        await hass.services.async_call(
            DOMAIN,
            "save_scene",
            {"name": "repeat", "mode": "rgb", "speed": speed},
            blocking=True,
        )
    assert len(store) == 1
    assert store.require("repeat").speed == 90


async def test_save_scene_accepts_rgb_triples(hass: HomeAssistant, entry, store) -> None:
    await hass.services.async_call(
        DOMAIN,
        "save_scene",
        {"name": "triple", "colors": [[10, 20, 30]]},
        blocking=True,
    )
    assert store.require("triple").colors == ((10, 20, 30),)


async def test_save_scene_reports_bad_effect(hass: HomeAssistant, entry) -> None:
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="unknown effect"):
        await hass.services.async_call(
            DOMAIN,
            "save_scene",
            {"name": "bad", "mode": "effect", "effect": "Not An Effect"},
            blocking=True,
        )


async def test_save_scene_rejects_warm_white_colour(
    hass: HomeAssistant, entry
) -> None:
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="warm-white"):
        await hass.services.async_call(
            DOMAIN,
            "save_scene",
            {"name": "bad", "colors": ["w80"]},
            blocking=True,
        )


async def test_delete_scene_service(hass: HomeAssistant, entry, store) -> None:
    await hass.services.async_call(
        DOMAIN, "save_scene", {"name": "temp", "mode": "rgb"}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN, "delete_scene", {"scene": "temp"}, blocking=True
    )
    assert store.names() == []


async def test_delete_unknown_scene_reports(hass: HomeAssistant, entry) -> None:
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="unknown scene"):
        await hass.services.async_call(
            DOMAIN, "delete_scene", {"scene": "ghost"}, blocking=True
        )


# -- import ----------------------------------------------------------------


async def test_import_scenes(hass: HomeAssistant, entry, store) -> None:
    await hass.services.async_call(
        DOMAIN,
        "import_scenes",
        {
            "scenes": [
                {"name": "a", "mode": "rgb", "colors": [[255, 0, 0]]},
                {"name": "b", "mode": "white", "white": 128},
            ]
        },
        blocking=True,
    )
    assert store.names() == ["a", "b"]


async def test_import_scenes_replaces_all(hass: HomeAssistant, entry, store) -> None:
    await hass.services.async_call(
        DOMAIN, "save_scene", {"name": "old", "mode": "rgb"}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN,
        "import_scenes",
        {"scenes": [{"name": "new", "mode": "rgb"}], "replace_all": True},
        blocking=True,
    )
    assert store.names() == ["new"]


async def test_import_scenes_rejects_invalid_entry(
    hass: HomeAssistant, entry
) -> None:
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="unknown effect"):
        await hass.services.async_call(
            DOMAIN,
            "import_scenes",
            {"scenes": [{"name": "x", "mode": "effect", "effect": "Nope"}]},
            blocking=True,
        )


# -- apply -----------------------------------------------------------------


async def test_apply_rgb_scene(hass: HomeAssistant, entry, client, store) -> None:
    await hass.services.async_call(
        DOMAIN,
        "save_scene",
        {"name": "red", "mode": "rgb", "colors": ["#ff0000"], "brightness": 255},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN, "apply_scene", {"entity_id": LIGHT, "scene": "red"}, blocking=True
    )
    client.set_color.assert_called_with(255, 0, 0)


async def test_apply_white_scene(hass: HomeAssistant, entry, client) -> None:
    await hass.services.async_call(
        DOMAIN,
        "save_scene",
        {"name": "warm", "mode": "white", "white": 200},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN, "apply_scene", {"entity_id": LIGHT, "scene": "warm"}, blocking=True
    )
    client.set_warm_white.assert_called_once_with(78)


async def test_apply_effect_scene(hass: HomeAssistant, entry, client) -> None:
    await hass.services.async_call(
        DOMAIN,
        "save_scene",
        {
            "name": "chase",
            "mode": "effect",
            "effect": "Chase",
            "colors": ["#ff0000"],
            "speed": 30,
            "density": 70,
        },
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN, "apply_scene", {"entity_id": LIGHT, "scene": "chase"}, blocking=True
    )
    client.set_effect.assert_called_once()
    kwargs = client.set_effect.call_args.kwargs
    assert kwargs["speed"] == 30
    assert kwargs["density"] == 70


async def test_apply_segments_scene(hass: HomeAssistant, entry, client) -> None:
    await hass.services.async_call(
        DOMAIN,
        "save_scene",
        {
            "name": "flags",
            "mode": "segments",
            "segments": ["#ff0000", "#ffffff", "#ff0000"],
        },
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN, "apply_scene", {"entity_id": LIGHT, "scene": "flags"}, blocking=True
    )
    sent = client.set_segments.call_args.args[0]
    assert sent[:3] == [(255, 0, 0), (255, 255, 255), (255, 0, 0)]


async def test_apply_scribble_scene(hass: HomeAssistant, entry, client) -> None:
    await hass.services.async_call(
        DOMAIN,
        "save_scene",
        {"name": "half", "mode": "scribble", "pattern": "18xred 17xblue"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN, "apply_scene", {"entity_id": LIGHT, "scene": "half"}, blocking=True
    )
    leds = client.set_scribble.call_args.args[0]
    assert len(leds) == 35
    assert leds[0].rgb == (255, 0, 0)
    assert leds[18].rgb == (0, 0, 255)


async def test_apply_look_scene(hass: HomeAssistant, entry, client) -> None:
    await hass.services.async_call(
        DOMAIN,
        "save_scene",
        {"name": "candy", "mode": "look", "look": "candy_cane"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN, "apply_scene", {"entity_id": LIGHT, "scene": "candy"}, blocking=True
    )
    leds = client.set_scribble.call_args.args[0]
    assert len(leds) == 35
    colours = {led.rgb for led in leds if led.rgb is not None}
    assert len(colours) == 2


async def test_apply_scene_broadcasts(hass: HomeAssistant, entry, client) -> None:
    """One call drives every targeted controller, like apply_preset."""
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Soffit East",
        data={
            CONF_HOST: "192.168.1.51",
            CONF_NAME: "Soffit East",
            "mac": "AABBCCDDEE11",
            "hw_version": "AK001-ZJ21413",
            "led_count": 35,
        },
        unique_id="AABBCCDDEE11",
    )
    other.add_to_hass(hass)
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN, "save_scene", {"name": "bright", "mode": "rgb"}, blocking=True
    )
    client.set_color.reset_mock()
    await hass.services.async_call(
        DOMAIN,
        "apply_scene",
        {"entity_id": [LIGHT, "light.soffit_east"], "scene": "bright"},
        blocking=True,
    )
    assert client.set_color.call_count == 2


async def test_apply_unknown_scene_reports(hass: HomeAssistant, entry) -> None:
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="unknown scene"):
        await hass.services.async_call(
            DOMAIN, "apply_scene", {"entity_id": LIGHT, "scene": "ghost"}, blocking=True
        )


async def test_apply_scene_turns_the_strip_on(
    hass: HomeAssistant, entry, client
) -> None:
    """Applying a scene while off should light it, not just stage a colour."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    from dataclasses import replace

    coordinator.runtime.state = replace(coordinator.runtime.state, power=False)

    await hass.services.async_call(
        DOMAIN, "save_scene", {"name": "on", "mode": "rgb"}, blocking=True
    )
    client.set_power.reset_mock()
    await hass.services.async_call(
        DOMAIN, "apply_scene", {"entity_id": LIGHT, "scene": "on"}, blocking=True
    )
    client.set_power.assert_called_once_with(True)


# -- date-driven -----------------------------------------------------------


async def _plan_season(hass: HomeAssistant) -> None:
    for name, start, end, effect in (
        ("halloween", "10-01", "10-31", "Chase"),
        ("christmas", "11-15", "12-31", "Flowing Water"),
    ):
        await hass.services.async_call(
            DOMAIN,
            "save_scene",
            {"name": name, "mode": "effect", "effect": effect, "start": start, "end": end},
            blocking=True,
        )
    await hass.services.async_call(
        DOMAIN,
        "save_scene",
        {"name": "warm_white", "mode": "white", "white": 200, "tags": "hoa_safe"},
        blocking=True,
    )


async def test_apply_scene_for_explicit_date(
    hass: HomeAssistant, entry, client
) -> None:
    await _plan_season(hass)
    client.set_effect.reset_mock()
    await hass.services.async_call(
        DOMAIN,
        "apply_scene_for_date",
        {"entity_id": LIGHT, "date": dt.date(2026, 10, 20)},
        blocking=True,
    )
    client.set_effect.assert_called_once()


async def test_apply_scene_for_date_falls_through_to_always_on(
    hass: HomeAssistant, entry, client
) -> None:
    """Outside every season, an all-year scene is still eligible."""
    await _plan_season(hass)
    client.set_warm_white.reset_mock()
    await hass.services.async_call(
        DOMAIN,
        "apply_scene_for_date",
        {"entity_id": LIGHT, "date": dt.date(2026, 6, 15)},
        blocking=True,
    )
    client.set_warm_white.assert_called_once()


async def test_apply_scene_for_date_topic_filter(
    hass: HomeAssistant, entry, client
) -> None:
    await _plan_season(hass)
    client.set_warm_white.reset_mock()
    client.set_effect.reset_mock()
    await hass.services.async_call(
        DOMAIN,
        "apply_scene_for_date",
        {
            "entity_id": LIGHT,
            "date": dt.date(2026, 12, 25),
            "topic": "hoa_safe",
        },
        blocking=True,
    )
    client.set_warm_white.assert_called_once()
    client.set_effect.assert_not_called()


async def test_apply_scene_for_date_with_nothing_eligible_reports(
    hass: HomeAssistant, entry
) -> None:
    from homeassistant.exceptions import HomeAssistantError

    await hass.services.async_call(
        DOMAIN, "save_scene", {"name": "winter", "start": "12-01", "end": "12-31"},
        blocking=True,
    )
    with pytest.raises(HomeAssistantError, match="no scene matches"):
        await hass.services.async_call(
            DOMAIN,
            "apply_scene_for_date",
            {"entity_id": LIGHT, "date": dt.date(2026, 6, 1)},
            blocking=True,
        )


async def test_randomized_daily_pick_is_repeatable(
    hass: HomeAssistant, entry, client, store
) -> None:
    """Two runs on the same day must not flip the lights between scenes."""
    for index in range(5):
        await hass.services.async_call(
            DOMAIN,
            "save_scene",
            {"name": f"option_{index}", "mode": "effect", "effect": "Chase"},
            blocking=True,
        )

    picked: set[str] = set()
    for _ in range(4):
        await hass.services.async_call(
            DOMAIN,
            "apply_scene_for_date",
            {"entity_id": LIGHT, "date": dt.date(2026, 7, 4), "randomize": True},
            blocking=True,
        )
        picked.add(hass.data[DOMAIN][entry.entry_id].runtime.scene)

    assert len(picked) == 1, f"the same day picked several scenes: {picked}"


# -- looks service ---------------------------------------------------------


async def test_apply_look_service(hass: HomeAssistant, entry, client) -> None:
    await hass.services.async_call(
        DOMAIN,
        "apply_look",
        {"entity_id": LIGHT, "look": "stripes", "palette": ["#ff0000", "#00ff00"]},
        blocking=True,
    )
    leds = client.set_scribble.call_args.args[0]
    assert len(leds) == 35
    assert {led.rgb for led in leds} == {(255, 0, 0), (0, 255, 0)}


async def test_apply_look_reports_bad_name(hass: HomeAssistant, entry) -> None:
    with pytest.raises(Exception, match="look"):
        await hass.services.async_call(
            DOMAIN,
            "apply_look",
            {"entity_id": LIGHT, "look": "plaid"},
            blocking=True,
        )


# -- entities --------------------------------------------------------------


async def test_scribble_pattern_text_entity(hass: HomeAssistant, entry, client) -> None:
    entity_id = "text.soffit_west_per_led_pattern"
    assert hass.states.get(entity_id) is not None

    await hass.services.async_call(
        "text",
        "set_value",
        {"entity_id": entity_id, "value": "35xw80"},
        blocking=True,
    )
    leds = client.set_scribble.call_args.args[0]
    assert leds[0].white == 80
    assert hass.states.get(entity_id).state == "35xw80"


async def test_scribble_pattern_rejects_bad_input(
    hass: HomeAssistant, entry, client
) -> None:
    entity_id = "text.soffit_west_per_led_pattern"
    client.set_scribble.reset_mock()
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "text",
            "set_value",
            {"entity_id": entity_id, "value": "99xnonsense"},
            blocking=True,
        )
    client.set_scribble.assert_not_called()


async def test_scribble_look_select_entity(hass: HomeAssistant, entry, client) -> None:
    entity_id = "select.soffit_west_per_led_look"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "custom"
    assert "rainbow" in state.attributes["options"]

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": "rainbow"},
        blocking=True,
    )
    assert hass.states.get(entity_id).state == "rainbow"
    assert client.set_scribble.call_count >= 1


async def test_scene_select_entity_options(hass: HomeAssistant, entry, store) -> None:
    entity_id = "select.soffit_west_scene"
    assert hass.states.get(entity_id).state == "none"

    await hass.services.async_call(
        DOMAIN, "save_scene", {"name": "christmas", "mode": "rgb"}, blocking=True
    )
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert "christmas" in state.attributes["options"]
    assert state.attributes["scenes"]["christmas"]["summary"].startswith("rgb")


async def test_scene_select_applies_a_scene(hass: HomeAssistant, entry, client) -> None:
    await hass.services.async_call(
        DOMAIN, "save_scene", {"name": "red", "colors": ["#ff0000"]}, blocking=True
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.soffit_west_scene", "option": "red"},
        blocking=True,
    )
    client.set_color.assert_called_with(255, 0, 0)
    assert hass.states.get("select.soffit_west_scene").state == "red"


async def test_strip_reports_active_scene(hass: HomeAssistant, entry, store) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await hass.services.async_call(
        DOMAIN, "save_scene", {"name": "blue", "colors": ["#0000ff"]}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN, "apply_scene", {"entity_id": LIGHT, "scene": "blue"}, blocking=True
    )
    assert coordinator.runtime.scene == "blue"
    assert hass.states.get("select.soffit_west_scene").state == "blue"


async def test_direct_colour_clears_active_scene(
    hass: HomeAssistant, entry, client
) -> None:
    """A manual colour change means the scene is no longer what is showing."""
    await hass.services.async_call(
        DOMAIN, "save_scene", {"name": "blue", "colors": ["#0000ff"]}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN, "apply_scene", {"entity_id": LIGHT, "scene": "blue"}, blocking=True
    )
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "rgb_color": (255, 0, 0)},
        blocking=True,
    )
    assert hass.states.get("select.soffit_west_scene").state == "none"


async def test_all_scene_services_registered(hass: HomeAssistant, entry) -> None:
    for service in (
        "apply_look",
        "save_scene",
        "delete_scene",
        "apply_scene",
        "apply_scene_for_date",
        "import_scenes",
    ):
        assert hass.services.has_service(DOMAIN, service), f"{service} missing"


async def test_scene_services_unload_with_last_entry(
    hass: HomeAssistant, entry
) -> None:
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, "save_scene")
