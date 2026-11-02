"""Service handlers: segments, scribble, timers, presets."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform

from .const import (
    ATTR_ACTION,
    ATTR_ACTIVE,
    ATTR_BLINK,
    ATTR_BLINK_SPEED,
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
    BLINK_MODES,
    DIRECTIONS,
    REPEAT_NAMES,
    REPEAT_WEEKDAYS,
    REPEAT_WEEKEND,
    REPEAT_EVERYDAY,
    SCRIBBLE_EFFECT_NAME_TO_ID,
    TIMER_ACTION_NAME_TO_VALUE,
    TimerAction,
)
from .protocol.scribble import ScribbleLED

_LOGGER = logging.getLogger(__name__)

_RUN_LENGTH = re.compile(r"^(?:(\d+)x)?(.+)$")
_WHITE_LEVEL = re.compile(r"^w(\d+)$")

COLOR_NAMES: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "purple": (128, 0, 128),
    "orange": (255, 128, 0),
    "pink": (255, 105, 180),
    "warm_white": (255, 200, 120),
    "white": (255, 255, 255),
}

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


def _parse_color(token: Any) -> tuple[tuple[int, int, int] | None, int | None]:
    """Parse a colour token into ``(rgb, white_level)``.

    Accepts a name, ``#rrggbb``, ``r,g,b``, or ``w<n>`` for a white level.
    Returns ``(None, None)`` for black/off.
    """
    if isinstance(token, (list, tuple)):
        values = [int(v) for v in token]
        if len(values) != 3:
            raise HomeAssistantError(f"expected 3 RGB values, got {len(values)}")
        return (values[0], values[1], values[2]), None

    text = str(token).strip().lower()
    if text in ("black", "off", "none"):
        return None, None
    if match := _WHITE_LEVEL.match(text):
        return None, int(match.group(1))
    if text in COLOR_NAMES:
        return COLOR_NAMES[text], None
    if text.startswith("#") and len(text) == 7:
        try:
            return (
                int(text[1:3], 16),
                int(text[3:5], 16),
                int(text[5:7], 16),
            ), None
        except ValueError as err:
            raise HomeAssistantError(f"invalid hex colour {token!r}") from err
    if "," in text:
        parts = text.split(",")
        if len(parts) == 3:
            try:
                return tuple(int(p) for p in parts), None  # type: ignore[return-value]
            except ValueError as err:
                raise HomeAssistantError(f"invalid RGB triple {token!r}") from err
    raise HomeAssistantError(f"unrecognised colour {token!r}")


def parse_led_spec(spec: str, led_count: int) -> list[ScribbleLED]:
    """Expand a per-LED spec into exactly ``led_count`` entries.

    Grammar, space separated:

    * ``<color>`` — one LED
    * ``<n>x<color>`` — run of ``n`` LEDs
    * ``<color>/<blink>[/<speed>]`` — with a blink suffix
    * ``density=``, ``speed=``, ``dir=``, ``blink=``, ``blinkspeed=``,
      ``enter=`` — global options, consumed elsewhere

    Short specs are padded with dark LEDs so the result always covers the whole
    strip, which is what makes rendering deterministic.
    """
    entries: list[ScribbleLED] = []
    for token in spec.split():
        if "=" in token:
            continue  # global option, handled by the caller
        match = _RUN_LENGTH.match(token)
        if not match:
            raise HomeAssistantError(f"malformed LED token {token!r}")
        count = int(match.group(1)) if match.group(1) else 1
        value = match.group(2)

        blink_mode = BLINK_MODES["none"]
        blink_speed = 100
        parts = value.split("/")
        if len(parts) > 1:
            name = parts[1].lower()
            if name not in BLINK_MODES:
                raise HomeAssistantError(f"unknown blink mode {parts[1]!r}")
            blink_mode = BLINK_MODES[name]
            if len(parts) > 2:
                blink_speed = int(parts[2])
        rgb, white = _parse_color(parts[0])
        entries.extend(
            [ScribbleLED(rgb=rgb, white=white, blink_mode=blink_mode, blink_speed=blink_speed)]
            * count
        )

    if not entries:
        raise HomeAssistantError("the pattern selects no LEDs")
    if len(entries) > led_count:
        raise HomeAssistantError(
            f"pattern has {len(entries)} LEDs but the controller has {led_count}"
        )
    entries.extend(ScribbleLED() for _ in range(led_count - len(entries)))
    return entries


def parse_repeat(text: str) -> int:
    """Parse a repeat description into a day mask."""
    key = text.strip().lower()
    if key in ("once", "none", ""):
        return 0
    if key == "everyday":
        return REPEAT_EVERYDAY
    if key == "weekdays":
        return REPEAT_WEEKDAYS
    if key == "weekend":
        return REPEAT_WEEKEND
    mask = 0
    for day in key.replace(" ", "").split(","):
        if day not in REPEAT_NAMES:
            raise HomeAssistantError(f"unknown day {day!r}")
        mask |= REPEAT_NAMES[day]
    return mask


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


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services."""

    async def _set_segments(call: ServiceCall) -> None:
        colors = [_parse_color(token)[0] for token in call.data[ATTR_COLORS]]
        for coordinator in _coordinators(hass, call.data[ATTR_ENTITY_ID]):
            await coordinator.async_set_segments(colors)

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

    async def _set_timer(call: ServiceCall) -> None:
        when = call.data[ATTR_TIME]
        action = TIMER_ACTION_NAME_TO_VALUE[call.data[ATTR_ACTION]]
        color = None
        if call.data.get(ATTR_COLOR) is not None:
            color = _parse_color(call.data[ATTR_COLOR])[0]
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

    async def _clear_timer(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass, call.data[ATTR_ENTITY_ID]):
            await coordinator.async_clear_timer(call.data[ATTR_SLOT])

    async def _sync_clock(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass, call.data[ATTR_ENTITY_ID]):
            await coordinator.async_sync_clock()

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
