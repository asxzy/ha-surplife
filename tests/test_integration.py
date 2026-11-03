"""Integration tests using Home Assistant's own test harness.

The device is replaced with a fake so these run offline, but everything above
the socket — coordinator, entity state, service plumbing — is exercised for
real.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import Mock, patch

import pytest
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_NAME,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.surplife.const import (
    CONF_MAC,
    DOMAIN,
    SERVICE_APPLY_PRESET,
    SERVICE_CLEAR_TIMER,
    SERVICE_SET_SCRIBBLE,
    SERVICE_SET_SEGMENTS,
    SERVICE_SET_TIMER,
)
from custom_components.surplife.protocol import SurplifeState, Timer

from . import golden

HOST = "192.168.1.50"
LIGHT = "light.soffit_west"


def _state(power: bool = True, pattern: int = 0x65, speed: int = 80) -> SurplifeState:
    from custom_components.surplife.protocol.state import parse_state

    frame = bytearray(golden.STATE_PRISTINE_INNER)
    frame[6] = 0x23 if power else 0x24
    frame[8] = pattern
    frame[9] = speed
    return parse_state(bytes(frame))


@pytest.fixture
def fake_client():
    """A stand-in for SurplifeClient with the same surface.

    Every client method is a plain ``Mock``, not an ``AsyncMock``: the
    coordinator calls the client through :func:`asyncio.to_thread`, so an
    ``AsyncMock`` would return a coroutine object instead of a value.
    """
    with patch(
        "custom_components.surplife.coordinator.SurplifeClient", autospec=True
    ) as factory:
        client = factory.return_value
        client.get_state = Mock(side_effect=lambda: _state())
        client.set_power = Mock()
        client.set_color = Mock()
        client.set_warm_white = Mock()
        client.set_effect = Mock()
        client.set_segments = Mock()
        client.set_scribble = Mock(return_value=2)
        client.sync_clock = Mock(return_value=dt.datetime(2026, 11, 2, 12, 0))
        client.get_timers = Mock(
            return_value=[
                Timer(slot=1, active=True, hour=20, minute=0, action=0x23, repeat_mask=0xFE),
                *[Timer(slot=n, active=False) for n in range(2, 7)],
            ]
        )
        client.set_timer = Mock()
        client.clear_timer = Mock()
        client.close = Mock()
        yield client


@pytest.fixture
async def entry(hass: HomeAssistant, fake_client):
    """Set up the integration with a fake device."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Soffit West",
        data={
            CONF_HOST: HOST,
            CONF_NAME: "Soffit West",
            CONF_MAC: "AABBCCDDEEFF",
            "hw_version": "AK001-ZJ21413",
            "firmware_info": "+ok=B6_26_20240514_ZG-BL-UFO",
            "led_count": 35,
        },
        unique_id="AABBCCDDEEFF",
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def test_light_is_created(hass: HomeAssistant, entry) -> None:
    """The controller appears as a light entity."""
    state = hass.states.get(LIGHT)
    assert state is not None
    assert state.state == STATE_ON


async def test_light_reports_supported_features(hass: HomeAssistant, entry) -> None:
    """RGB, white and effects are advertised; colour temp is not."""
    attributes = hass.states.get(LIGHT).attributes
    assert ColorMode.RGB in attributes["supported_color_modes"]
    assert ColorMode.WHITE in attributes["supported_color_modes"]
    assert ColorMode.COLOR_TEMP not in attributes["supported_color_modes"]
    assert attributes["supported_features"] & 4  # EFFECT


async def test_effect_list_is_the_native_set(hass: HomeAssistant, entry) -> None:
    """The effect list is the 24 native effects, not flux_led's generic ones."""
    effects = hass.states.get(LIGHT).attributes["effect_list"]
    assert len(effects) == 24
    assert "Wave" in effects
    assert "Rainbow Bridge" in effects
    assert "Static Gradient" in effects
    # flux_led's generic list must not leak in.
    assert "colorloop" not in effects
    assert "red_fade" not in effects


async def test_turn_off(hass: HomeAssistant, entry, fake_client) -> None:
    await hass.services.async_call(
        "light", "turn_off", {ATTR_ENTITY_ID: LIGHT}, blocking=True
    )
    fake_client.set_power.assert_called_once_with(False)


async def test_turn_on_with_rgb(hass: HomeAssistant, entry, fake_client) -> None:
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: LIGHT, ATTR_RGB_COLOR: (255, 0, 0), ATTR_BRIGHTNESS: 255},
        blocking=True,
    )
    fake_client.set_color.assert_called_once_with(255, 0, 0)


async def test_rgb_is_scaled_by_brightness(hass: HomeAssistant, entry, fake_client) -> None:
    """Brightness multiplies the colour, since the device has one value byte."""
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: LIGHT, ATTR_RGB_COLOR: (255, 0, 0), ATTR_BRIGHTNESS: 128},
        blocking=True,
    )
    fake_client.set_color.assert_called_once_with(128, 0, 0)


async def test_rgb_state_is_optimistic(hass: HomeAssistant, entry) -> None:
    """Colour survives even though the firmware never echoes it."""
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: LIGHT, ATTR_RGB_COLOR: (0, 0, 255), ATTR_BRIGHTNESS: 255},
        blocking=True,
    )
    state = hass.states.get(LIGHT)
    assert state.attributes["rgb_color"] == (0, 0, 255)
    assert state.attributes["color_mode"] == ColorMode.RGB


async def test_effect_can_be_set(hass: HomeAssistant, entry, fake_client) -> None:
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Meteor"},
        blocking=True,
    )
    fake_client.set_effect.assert_called()
    assert hass.states.get(LIGHT).attributes["effect"] == "Meteor"


async def test_white_mode_is_tracked(hass: HomeAssistant, entry, fake_client) -> None:
    """Setting warm white switches the reported colour mode."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_set_white(200)
    fake_client.set_warm_white.assert_called_once_with(78)  # 200/255 -> 78%
    assert hass.states.get(LIGHT).attributes["color_mode"] == ColorMode.WHITE


async def test_scribble_service(hass: HomeAssistant, entry, fake_client) -> None:
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_SCRIBBLE,
        {ATTR_ENTITY_ID: LIGHT, "pattern": "20xred 15xblue"},
        blocking=True,
    )
    fake_client.set_scribble.assert_called()
    leds = fake_client.set_scribble.call_args.args[0]
    assert len(leds) == 35
    assert leds[0].rgb == (255, 0, 0)
    assert leds[34].rgb == (0, 0, 255)


async def test_scribble_service_pads_short_pattern(
    hass: HomeAssistant, entry, fake_client
) -> None:
    """A short pattern is padded with dark LEDs so rendering is deterministic."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_SCRIBBLE,
        {ATTR_ENTITY_ID: LIGHT, "pattern": "5xred"},
        blocking=True,
    )
    leds = fake_client.set_scribble.call_args.args[0]
    assert len(leds) == 35
    assert all(led.is_off for led in leds[5:])


async def test_scribble_rejects_overflow(hass: HomeAssistant, entry) -> None:
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="35"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_SCRIBBLE,
            {ATTR_ENTITY_ID: LIGHT, "pattern": "40xred"},
            blocking=True,
        )


async def test_segments_service(hass: HomeAssistant, entry, fake_client) -> None:
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_SEGMENTS,
        {ATTR_ENTITY_ID: LIGHT, "colors": ["#ff0000", "green", [0, 0, 255]]},
        blocking=True,
    )
    colors = fake_client.set_segments.call_args.args[0]
    assert colors[0] == (255, 0, 0)
    assert colors[1] == (0, 255, 0)
    assert colors[2] == (0, 0, 255)


async def test_set_timer_service(hass: HomeAssistant, entry, fake_client) -> None:
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_TIMER,
        {
            ATTR_ENTITY_ID: LIGHT,
            "slot": 3,
            "time": dt.time(18, 30),
            "action": "on",
            "repeat": "weekdays",
        },
        blocking=True,
    )
    kwargs = fake_client.set_timer.call_args.kwargs
    assert kwargs["slot"] == 3
    assert kwargs["hour"] == 18
    assert kwargs["minute"] == 30
    assert kwargs["repeat_mask"] == 0x3E


async def test_clear_timer_service(hass: HomeAssistant, entry, fake_client) -> None:
    await hass.services.async_call(
        DOMAIN, SERVICE_CLEAR_TIMER, {ATTR_ENTITY_ID: LIGHT, "slot": 2}, blocking=True
    )
    fake_client.clear_timer.assert_called_once_with(2)


async def test_apply_preset_service(hass: HomeAssistant, entry, fake_client) -> None:
    await hass.services.async_call(
        DOMAIN,
        SERVICE_APPLY_PRESET,
        {ATTR_ENTITY_ID: LIGHT, "preset": "christmas"},
        blocking=True,
    )
    fake_client.set_effect.assert_called()


async def test_led_count_sensor(hass: HomeAssistant, entry) -> None:
    state = hass.states.get("sensor.soffit_west_led_count")
    assert state is not None
    assert state.state == "35"


async def test_mode_sensor(hass: HomeAssistant, entry) -> None:
    state = hass.states.get("sensor.soffit_west_mode")
    assert state is not None
    assert state.state == "static"


async def test_effect_select(hass: HomeAssistant, entry, fake_client) -> None:
    state = hass.states.get("select.soffit_west_effect")
    assert state is not None
    assert "Wave" in state.attributes["options"]
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.soffit_west_effect", "option": "Strobe"},
        blocking=True,
    )
    fake_client.set_effect.assert_called()


async def test_unload(hass: HomeAssistant, entry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    # An unloaded entity's state lingers as ``unavailable`` with the restored
    # flag set, which is normal Home Assistant behaviour; what matters is that
    # it is no longer live.
    state = hass.states.get(LIGHT)
    assert state is None or state.state == STATE_UNAVAILABLE


# -- poll failure tolerance ------------------------------------------------


async def test_single_poll_failure_keeps_entities_available(
    hass: HomeAssistant, entry, fake_client
) -> None:
    """One missed poll must not mark the strip unavailable.

    The controllers stop answering for a couple of seconds after a multi-paint
    scribble render, which can span a whole poll at the default interval.
    """
    from custom_components.surplife.protocol import SurplifeConnectionError

    fake_client.get_state.side_effect = SurplifeConnectionError("no reply")
    await hass.data[DOMAIN][entry.entry_id].async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(LIGHT)
    assert state is not None
    assert state.state == STATE_ON


async def test_repeated_poll_failures_mark_unavailable(
    hass: HomeAssistant, entry, fake_client
) -> None:
    """A run of failures is a real outage and must be reported."""
    from custom_components.surplife.protocol import SurplifeConnectionError

    fake_client.get_state.side_effect = SurplifeConnectionError("no reply")
    for _ in range(3):
        await hass.data[DOMAIN][entry.entry_id].async_refresh()
        await hass.async_block_till_done()

    state = hass.states.get(LIGHT)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


async def test_poll_recovers_after_transient_failure(
    hass: HomeAssistant, entry, fake_client
) -> None:
    """A good poll clears the failure count."""
    from custom_components.surplife.protocol import SurplifeConnectionError

    fake_client.get_state.side_effect = SurplifeConnectionError("no reply")
    await hass.data[DOMAIN][entry.entry_id].async_refresh()
    await hass.async_block_till_done()

    fake_client.get_state.side_effect = None
    fake_client.get_state.return_value = _state()
    await hass.data[DOMAIN][entry.entry_id].async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(LIGHT).state == STATE_ON
    assert hass.data[DOMAIN][entry.entry_id]._failures == 0


async def test_white_selects_the_warm_white_channel(
    hass: HomeAssistant, entry, fake_client
) -> None:
    """``light.turn_on`` with ``white`` must use the white channel, not RGB.

    The controller has a separate warm-white channel. Falling through to the
    brightness branch would send grey RGB instead, which looks different and
    wastes the strip's white LED.
    """
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT, "white": 180},
        blocking=True,
    )
    fake_client.set_warm_white.assert_called_once_with(71)  # 180/255 -> 71%
    fake_client.set_color.assert_not_called()

    state = hass.states.get(LIGHT)
    assert state.attributes["color_mode"] == "white"


async def test_white_then_brightness_stays_on_the_white_channel(
    hass: HomeAssistant, entry, fake_client
) -> None:
    """A later brightness change must not silently switch back to RGB."""
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT, "white": 255}, blocking=True
    )
    fake_client.set_warm_white.reset_mock()
    fake_client.set_color.reset_mock()

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": LIGHT, "brightness": 100}, blocking=True
    )
    fake_client.set_color.assert_not_called()
    fake_client.set_warm_white.assert_called_once()


# -- timer sensor ----------------------------------------------------------


async def test_timer_sensor_reports_device_timers(hass: HomeAssistant, entry) -> None:
    """The timers sensor must reflect the hardware, not an empty cache.

    It previously reported 0 forever: the sensor reads ``runtime.timers``, but
    nothing ever populated it. The device in fact has one timer configured.
    """
    state = hass.states.get("sensor.soffit_west_timers")
    assert state is not None
    assert state.state == "1"
    assert state.attributes["slot_1"].startswith("20:00")
    assert state.attributes["slot_2"] == "unset"


async def test_writing_a_timer_refreshes_the_sensor(
    hass: HomeAssistant, entry, fake_client
) -> None:
    """After a timer write the sensor must show the new list immediately."""
    fake_client.get_timers.return_value = [
        Timer(slot=1, active=True, hour=20, minute=0, action=0x23, repeat_mask=0xFE),
        Timer(slot=5, active=True, hour=6, minute=30, action=0x23, repeat_mask=0x3E),
    ]

    await hass.services.async_call(
        "surplife",
        "set_timer",
        {
            "entity_id": LIGHT,
            "slot": 5,
            "time": "06:30:00",
            "action": "on",
            "repeat": "weekdays",
        },
        blocking=True,
    )

    state = hass.states.get("sensor.soffit_west_timers")
    assert state.state == "2"
    assert state.attributes["slot_5"].startswith("06:30")


async def test_timer_read_failure_does_not_block_setup(
    hass: HomeAssistant, entry, fake_client
) -> None:
    """A controller that will not answer a timer query is still usable.

    Timers are diagnostic; the light itself works without them.
    """
    from custom_components.surplife.protocol import SurplifeConnectionError

    fake_client.get_timers.side_effect = SurplifeConnectionError("no reply")
    await hass.data[DOMAIN][entry.entry_id].async_load_timers()

    assert hass.states.get(LIGHT).state == STATE_ON
    # The last known timers are kept rather than wiped, so the sensor still
    # describes the controller instead of claiming it has none.
    assert hass.states.get("sensor.soffit_west_timers").state == "1"
