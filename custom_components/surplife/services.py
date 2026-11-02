"""Service handlers: segments, scribble, timers, presets."""

from __future__ import annotations

import functools
import logging

import voluptuous as vol

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform

from .const import (
    ATTR_ACTION,
    ATTR_ACTIVE,
    ATTR_COLOR,
    ATTR_COLORS,
    ATTR_DENSITY,
    ATTR_DIRECTION,
    ATTR_EFFECT,
    ATTR_ENTER,
    ATTR_PATTERN,
    ATTR_PRESET,
    ATTR_REPEAT,
    ATTR_SLOT,
    ATTR_SPEED,
    ATTR_TIME,
    DOMAIN,
    SERVICE_APPLY_PRESET,
    SERVICE_CLEAR_TIMER,
    SERVICE_SET_SCRIBBLE,
    SERVICE_SET_SEGMENTS,
    SERVICE_SET_TIMER,
    SERVICE_SYNC_CLOCK,
)
from .coordinator import SurplifeCoordinator
from .presets import PRESETS
from .protocol.constants import (
    DIRECTIONS,
    SCRIBBLE_EFFECT_NAME_TO_ID,
    TIMER_ACTION_NAME_TO_VALUE,
)
from .protocol.spec import (
    parse_color,
    parse_led_spec,
    parse_repeat,
)

_LOGGER = logging.getLogger(__name__)

SET_SEGMENTS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_COLORS): vol.All(cv.ensure_list, [vol.Any(str, list)]),
    }
)

SET_SCRIBBLE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_PATTERN): cv.string,
        vol.Optional(ATTR_EFFECT): vol.Any(cv.string, int),
        vol.Optional(ATTR_DIRECTION, default="l2r"): cv.string,
        vol.Optional(ATTR_DENSITY, default=80): vol.All(int, vol.Range(min=0, max=100)),
        vol.Optional(ATTR_SPEED, default=100): vol.All(int, vol.Range(min=0, max=100)),
        vol.Optional(ATTR_ENTER, default=True): cv.boolean,
    }
)

SET_TIMER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_SLOT): vol.All(int, vol.Range(min=1, max=6)),
        vol.Required(ATTR_TIME): cv.time,
        vol.Optional(ATTR_ACTION, default="on"): vol.In(TIMER_ACTION_NAME_TO_VALUE),
        vol.Optional(ATTR_COLOR): vol.Any(str, list),
        vol.Optional(ATTR_REPEAT, default="everyday"): cv.string,
        vol.Optional(ATTR_ACTIVE, default=True): cv.boolean,
    }
)

CLEAR_TIMER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_SLOT): vol.All(int, vol.Range(min=1, max=6)),
    }
)

SYNC_CLOCK_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_ids})

APPLY_PRESET_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_PRESET): vol.In(sorted(PRESETS)),
    }
)


def _coordinators(hass: HomeAssistant, entity_ids: list[str]) -> list[SurplifeCoordinator]:
    """Resolve light entity IDs to their coordinators."""
    result: list[SurplifeCoordinator] = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None:
            raise HomeAssistantError(f"unknown entity {entity_id}")
        entry_id = state.attributes.get("surplife_entry_id")
        if entry_id is None:
            raise HomeAssistantError(f"{entity_id} is not a Surplife light")
        coordinator = hass.data[DOMAIN].get(entry_id)
        if coordinator is None:
            raise HomeAssistantError(f"{entity_id} is not loaded")
        result.append(coordinator)
    return result


def _user_errors(handler):
    """Turn the shared parsers' ``ValueError`` into a service-visible error.

    The colour/pattern/repeat parsers live in the stdlib protocol layer, which
    raises plain ``ValueError`` so it can be used without Home Assistant. The
    services layer converts that into ``HomeAssistantError``, which is what
    Home Assistant renders as a service failure for the user.
    """

    @functools.wraps(handler)
    async def wrapper(call: ServiceCall) -> None:
        try:
            await handler(call)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

    return wrapper


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services."""

    @_user_errors
    async def _set_segments(call: ServiceCall) -> None:
        colors = [parse_color(token)[0] for token in call.data[ATTR_COLORS]]
        for coordinator in _coordinators(hass, call.data[ATTR_ENTITY_ID]):
            await coordinator.async_set_segments(colors)

    @_user_errors
    async def _set_scribble(call: ServiceCall) -> None:
        effect = call.data.get(ATTR_EFFECT, 0)
        if isinstance(effect, str):
            if effect.lower() in SCRIBBLE_EFFECT_NAME_TO_ID:
                effect = SCRIBBLE_EFFECT_NAME_TO_ID[effect.lower()]
            else:
                effect = int(effect)
        direction = DIRECTIONS.get(
            str(call.data[ATTR_DIRECTION]).lower(), DIRECTIONS["l2r"]
        )
        for coordinator in _coordinators(hass, call.data[ATTR_ENTITY_ID]):
            leds = parse_led_spec(call.data[ATTR_PATTERN], coordinator.runtime.led_count)
            await coordinator.async_set_scribble(
                leds,
                effect=int(effect),
                direction=direction,
                density=call.data[ATTR_DENSITY],
                speed=call.data[ATTR_SPEED],
                enter=call.data[ATTR_ENTER],
            )

    @_user_errors
    async def _set_timer(call: ServiceCall) -> None:
        when = call.data[ATTR_TIME]
        action = TIMER_ACTION_NAME_TO_VALUE[call.data[ATTR_ACTION]]
        color = None
        if call.data.get(ATTR_COLOR) is not None:
            color = parse_color(call.data[ATTR_COLOR])[0]
        repeat_mask = parse_repeat(call.data[ATTR_REPEAT])
        for coordinator in _coordinators(hass, call.data[ATTR_ENTITY_ID]):
            await coordinator.async_set_timer(
                slot=call.data[ATTR_SLOT],
                active=call.data[ATTR_ACTIVE],
                hour=when.hour,
                minute=when.minute,
                repeat_mask=repeat_mask,
                action=int(action),
                color=color,
            )

    @_user_errors
    async def _clear_timer(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass, call.data[ATTR_ENTITY_ID]):
            await coordinator.async_clear_timer(call.data[ATTR_SLOT])

    @_user_errors
    async def _sync_clock(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass, call.data[ATTR_ENTITY_ID]):
            await coordinator.async_sync_clock()

    @_user_errors
    async def _apply_preset(call: ServiceCall) -> None:
        preset = PRESETS[call.data[ATTR_PRESET]]
        for coordinator in _coordinators(hass, call.data[ATTR_ENTITY_ID]):
            await preset.apply(coordinator)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_SEGMENTS, _set_segments, schema=SET_SEGMENTS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_SCRIBBLE, _set_scribble, schema=SET_SCRIBBLE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_TIMER, _set_timer, schema=SET_TIMER_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_TIMER, _clear_timer, schema=CLEAR_TIMER_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SYNC_CLOCK, _sync_clock, schema=SYNC_CLOCK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_APPLY_PRESET, _apply_preset, schema=APPLY_PRESET_SCHEMA
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the integration's services."""
    for service in (
        SERVICE_SET_SEGMENTS,
        SERVICE_SET_SCRIBBLE,
        SERVICE_SET_TIMER,
        SERVICE_CLEAR_TIMER,
        SERVICE_SYNC_CLOCK,
        SERVICE_APPLY_PRESET,
    ):
        hass.services.async_remove(DOMAIN, service)
