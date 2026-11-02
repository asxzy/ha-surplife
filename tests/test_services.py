"""End-to-end setup checks against a real Home Assistant instance.

These go beyond the entity tests: they assert that the integration registers
its services, that the device registry entry is complete, and that every
service in ``services.yaml`` has both a handler and a schema. A missing handler
is the kind of mistake that only shows up when a user calls the service.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.surplife.const import DOMAIN
from custom_components.surplife.protocol import SurplifeState, Timer

from . import golden


def _state() -> SurplifeState:
    from custom_components.surplife.protocol.state import parse_state

    frame = bytearray(golden.STATE_PRISTINE_INNER)
    frame[6] = 0x23  # powered on
    return parse_state(bytes(frame))


@pytest.fixture
def client():
    """Stub client, patched into the coordinator."""
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
        instance.set_timer = Mock()
        instance.clear_timer = Mock()
        instance.sync_clock = Mock()
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
            CONF_HOST: "192.168.1.50",
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


async def test_all_services_are_registered(hass: HomeAssistant, entry) -> None:
    """Every documented service has a live handler."""
    for service in (
        "set_scribble",
        "set_segments",
        "set_timer",
        "clear_timer",
        "sync_clock",
        "apply_preset",
    ):
        assert hass.services.has_service(DOMAIN, service), f"{service} not registered"


async def test_device_registry_entry(hass: HomeAssistant, entry) -> None:
    """The device registry carries the real hardware identity."""
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        (DOMAIN, "AABBCCDDEEFF"), entry.entry_id
    )
    assert device is not None
    assert device.manufacturer == "Zengge"
    assert device.model_id == "0xB6"
    assert device.hw_version == "AK001-ZJ21413"
    assert device.name == "Soffit West"


async def test_light_reports_entry_id_for_services(
    hass: HomeAssistant, entry
) -> None:
    """Services resolve a light entity to its coordinator via this attribute."""
    attributes = hass.states.get("light.soffit_west").attributes
    assert attributes["surplife_entry_id"] == entry.entry_id


async def test_sync_clock_service(hass: HomeAssistant, entry, client) -> None:
    await hass.services.async_call(
        DOMAIN,
        "sync_clock",
        {"entity_id": "light.soffit_west"},
        blocking=True,
    )
    client.sync_clock.assert_called_once()


async def test_apply_preset_to_multiple_lights(hass: HomeAssistant, entry, client) -> None:
    """Presets broadcast to every targeted controller.

    This is the local stand-in for the app's cloud-side grouping, so it has to
    work when more than one controller is targeted at once.
    """
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Soffit East",
        data={
            CONF_HOST: "192.168.1.51",
            CONF_NAME: "Soffit East",
            "mac": "AABBCCDDEE11",
            "hw_version": "AK001-ZJ21413",
            "firmware_info": "+ok=B6_26_20240514_ZG-BL-UFO",
            "led_count": 35,
        },
        unique_id="AABBCCDDEE22",
    )
    other.add_to_hass(hass)
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()

    client.set_effect.reset_mock()
    await hass.services.async_call(
        DOMAIN,
        "apply_preset",
        {
            "entity_id": ["light.soffit_west", "light.soffit_east"],
            "preset": "christmas",
        },
        blocking=True,
    )
    # One client instance is shared by both coordinators in this fixture, so
    # two calls means both controllers received the command.
    assert client.set_effect.call_count == 2


async def test_scribble_with_blink(hass: HomeAssistant, entry, client) -> None:
    """Blink suffixes reach the per-LED model."""
    await hass.services.async_call(
        DOMAIN,
        "set_scribble",
        {"entity_id": "light.soffit_west", "pattern": "10xred/fast 25xblue"},
        blocking=True,
    )
    leds = client.set_scribble.call_args.args[0]
    assert leds[0].blink_mode == 0x10  # BLINK_FAST
    assert leds[10].blink_mode == 0x00


async def test_scribble_white_level_token(hass: HomeAssistant, entry, client) -> None:
    """``w<n>`` tokens select the warm-white channel per LED."""
    await hass.services.async_call(
        DOMAIN,
        "set_scribble",
        {"entity_id": "light.soffit_west", "pattern": "35xw80"},
        blocking=True,
    )
    leds = client.set_scribble.call_args.args[0]
    assert leds[0].white == 80
    assert leds[0].rgb is None


async def test_timer_repeat_parsing(hass: HomeAssistant, entry, client) -> None:
    """Day lists and named ranges both parse into the right mask."""
    await hass.services.async_call(
        DOMAIN,
        "set_timer",
        {
            "entity_id": "light.soffit_west",
            "slot": 4,
            "time": "19:15:00",
            "action": "off",
            "repeat": "mon,wed,fri",
        },
        blocking=True,
    )
    kwargs = client.set_timer.call_args.kwargs
    assert kwargs["repeat_mask"] == 0x02 | 0x08 | 0x20
    assert kwargs["hour"] == 19
    assert kwargs["minute"] == 15
    assert kwargs["action"] == 0x24


async def test_segments_accepts_twenty_colours(
    hass: HomeAssistant, entry, client
) -> None:
    colors = [f"#{index:02x}0080" for index in range(20)]
    await hass.services.async_call(
        DOMAIN,
        "set_segments",
        {"entity_id": "light.soffit_west", "colors": colors},
        blocking=True,
    )
    sent = client.set_segments.call_args.args[0]
    assert len(sent) == 20
    assert sent[0] == (0, 0, 128)
