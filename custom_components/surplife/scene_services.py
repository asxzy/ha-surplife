"""Services for user-defined scenes, per-LED looks and date-driven selection."""

from __future__ import annotations

import functools
import logging
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_BRIGHTNESS,
    ATTR_COLORS,
    ATTR_DATE,
    ATTR_DENSITY,
    ATTR_DIRECTION,
    ATTR_EFFECT,
    ATTR_END,
    ATTR_LOOK,
    ATTR_MODE,
    ATTR_NAME,
    ATTR_PALETTE,
    ATTR_PATTERN,
    ATTR_PRIORITY,
    ATTR_RANDOMIZE,
    ATTR_REPLACE_ALL,
    ATTR_SCENE,
    ATTR_SCENES,
    ATTR_SCRIBBLE_EFFECT,
    ATTR_SEGMENTS,
    ATTR_SPEED,
    ATTR_START,
    ATTR_TAGS,
    ATTR_TITLE,
    ATTR_TOPIC,
    ATTR_WHITE,
    DOMAIN,
    SERVICE_APPLY_LOOK,
    SERVICE_APPLY_SCENE,
    SERVICE_APPLY_SCENE_FOR_DATE,
    SERVICE_DELETE_SCENE,
    SERVICE_IMPORT_SCENES,
    SERVICE_SAVE_SCENE,
)
from .groups import coordinators_for_entities
from .scenes import (
    MAX_SCENES,
    SCENE_MODES,
    Scene,
    SceneStore,
    known_effects,
    known_scribble_effects,
    normalize_name,
    parse_tags,
    select_for_date,
)
from .scribble_looks import LOOKS

_LOGGER = logging.getLogger(__name__)

#: Shared field validators. Kept loose on purpose — :class:`Scene` does the
#: authoritative validation, so the schema and the model cannot disagree.
_COLOR_LIST = vol.All(cv.ensure_list, [vol.Any(str, [int])])
_MMDD = vol.Any(None, cv.string)

SAVE_SCENE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_NAME): cv.string,
        vol.Optional(ATTR_MODE, default="rgb"): vol.In(SCENE_MODES),
        vol.Optional(ATTR_TITLE): cv.string,
        vol.Optional(ATTR_COLORS): _COLOR_LIST,
        vol.Optional(ATTR_BRIGHTNESS, default=255): vol.All(
            int, vol.Range(min=0, max=255)
        ),
        vol.Optional(ATTR_WHITE, default=0): vol.All(int, vol.Range(min=0, max=255)),
        vol.Optional(ATTR_EFFECT): cv.string,
        vol.Optional(ATTR_SPEED, default=50): vol.All(int, vol.Range(min=0, max=100)),
        vol.Optional(ATTR_DENSITY, default=50): vol.All(int, vol.Range(min=0, max=100)),
        vol.Optional(ATTR_DIRECTION, default="l2r"): cv.string,
        vol.Optional(ATTR_SEGMENTS): _COLOR_LIST,
        vol.Optional(ATTR_PATTERN): cv.string,
        vol.Optional(ATTR_LOOK): vol.In(sorted(LOOKS)),
        vol.Optional(ATTR_SCRIBBLE_EFFECT, default=0): vol.All(
            int, vol.Range(min=0, max=8)
        ),
        vol.Optional(ATTR_START): _MMDD,
        vol.Optional(ATTR_END): _MMDD,
        vol.Optional(ATTR_TAGS): vol.Any(cv.string, [cv.string]),
        vol.Optional(ATTR_PRIORITY, default=0): vol.All(
            int, vol.Range(min=-100, max=100)
        ),
    },
    extra=vol.ALLOW_EXTRA,
)

DELETE_SCENE_SCHEMA = vol.Schema({vol.Required(ATTR_SCENE): cv.string})

APPLY_SCENE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_SCENE): cv.string,
    }
)

APPLY_SCENE_FOR_DATE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional(ATTR_DATE): cv.date,
        vol.Optional(ATTR_TOPIC): cv.string,
        vol.Optional(ATTR_RANDOMIZE, default=False): cv.boolean,
    }
)

IMPORT_SCENES_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SCENES): vol.All(cv.ensure_list, [dict]),
        vol.Optional(ATTR_REPLACE_ALL, default=False): cv.boolean,
    }
)

APPLY_LOOK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_LOOK): vol.In(sorted(LOOKS)),
        vol.Optional(ATTR_PALETTE): _COLOR_LIST,
        vol.Optional(ATTR_DIRECTION, default="l2r"): cv.string,
        vol.Optional(ATTR_DENSITY, default=80): vol.All(int, vol.Range(min=0, max=100)),
        vol.Optional(ATTR_SPEED, default=100): vol.All(int, vol.Range(min=0, max=100)),
        vol.Optional(ATTR_SCRIBBLE_EFFECT, default=0): vol.All(
            int, vol.Range(min=0, max=8)
        ),
    }
)


def _colour_entries(value: Any, *, allow_off: bool = False) -> list[Any]:
    """Flatten colour-field input into tokens a :class:`Scene` accepts.

    The UI sends ``[{"color": [r, g, b], "white": n}, ...]``; YAML sends a list
    of strings or triples. A white level is folded into a fourth element, giving
    the scene an RGBW entry, so the W channel is not silently dropped.

    Args:
        value: Whatever the service field received.
        allow_off: Whether ``off``/``black`` entries are meaningful, as they are
            for segments (a dark gap in the run).

    Raises:
        ValueError: if an entry carries neither a colour nor a white level.
    """
    if not isinstance(value, (list, tuple)):
        value = [value]
    entries: list[Any] = []
    for item in value:
        if isinstance(item, dict):
            rgb = item.get("color")
            white = item.get("white")
            if white is not None and rgb is not None:
                entries.append((*rgb, int(white)))
            elif white is not None:
                entries.append((0, 0, 0, int(white)))
            elif rgb is not None:
                entries.append(rgb)
            elif allow_off:
                entries.append(None)
            else:
                raise ValueError("a colour entry needs a colour or a white level")
        else:
            entries.append(item)
    return entries


def scene_from_call(data: dict[str, Any]) -> Scene:
    """Build a validated :class:`Scene` from service call data.

    ``colors`` and ``segments`` arrive in one of two shapes: plain tokens
    (``#rrggbb`` strings or ``[r, g, b]`` lists) from YAML and scripts, or the
    UI's ``{"color": [r, g, b], "white": n}`` objects. Both are reduced here to
    the tokens :class:`Scene` accepts, with a white level folded into an RGBW
    tuple so the W channel survives the trip.
    """
    payload: dict[str, Any] = {
        "name": normalize_name(data[ATTR_NAME]),
        "mode": data.get(ATTR_MODE, "rgb"),
    }
    if (title := data.get(ATTR_TITLE)) is not None:
        payload["title"] = title
    if data.get(ATTR_COLORS) is not None:
        payload["colors"] = _colour_entries(data[ATTR_COLORS])
    if data.get(ATTR_SEGMENTS) is not None:
        payload["segments"] = _colour_entries(data[ATTR_SEGMENTS], allow_off=True)
    for key in (
        ATTR_BRIGHTNESS,
        ATTR_WHITE,
        ATTR_SPEED,
        ATTR_DENSITY,
        ATTR_SCRIBBLE_EFFECT,
        ATTR_PRIORITY,
    ):
        if key in data:
            payload[key] = data[key]
    for key in (ATTR_EFFECT, ATTR_PATTERN, ATTR_LOOK, ATTR_DIRECTION, ATTR_START, ATTR_END):
        if data.get(key) not in (None, ""):
            payload[key] = data[key]
    if (tags := data.get(ATTR_TAGS)) is not None:
        payload["tags"] = parse_tags(tags)
    return Scene(**payload)


def _store(hass: HomeAssistant) -> SceneStore:
    """The integration-wide scene store."""
    store = hass.data[DOMAIN].get("__scenes__")
    if store is None:
        raise HomeAssistantError("the Surplife scene store is not loaded")
    return store


def _scene_error(handler):
    """Turn the model's ``ValueError`` into a user-visible service error."""

    @functools.wraps(handler)
    async def wrapper(call: ServiceCall) -> None:
        try:
            await handler(call)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

    return wrapper


async def async_setup_scene_services(hass: HomeAssistant) -> None:
    """Register the scene, look and date services."""

    @_scene_error
    async def _save_scene(call: ServiceCall) -> None:
        scene = scene_from_call(dict(call.data))
        store = _store(hass)
        await store.async_put(scene)
        # A new option must appear in the picker without a restart.
        _reload_selects(hass)
        _LOGGER.debug("stored scene %s (%s)", scene.name, scene.describe())

    @_scene_error
    async def _delete_scene(call: ServiceCall) -> None:
        store = _store(hass)
        await store.async_delete(call.data[ATTR_SCENE])
        _reload_selects(hass)

    @_scene_error
    async def _apply_scene(call: ServiceCall) -> None:
        scene = _store(hass).require(call.data[ATTR_SCENE])
        for coordinator in _coordinators(hass, call.data[ATTR_ENTITY_ID]):
            await scene.async_apply(coordinator)

    @_scene_error
    async def _apply_scene_for_date(call: ServiceCall) -> None:
        from homeassistant.util import dt as dt_util

        day = call.data.get(ATTR_DATE) or dt_util.now().date()
        store = _store(hass)
        scene = select_for_date(
            store.all(),
            day,
            topic=call.data.get(ATTR_TOPIC),
            randomize=call.data.get(ATTR_RANDOMIZE, False),
        )
        if scene is None:
            raise HomeAssistantError(
                f"no scene matches {day.isoformat()}"
                + (f" with tag {call.data[ATTR_TOPIC]!r}" if call.data.get(ATTR_TOPIC) else "")
            )
        for coordinator in _coordinators(hass, call.data[ATTR_ENTITY_ID]):
            await scene.async_apply(coordinator)
        _LOGGER.debug("applied %s for %s", scene.name, day.isoformat())

    @_scene_error
    async def _import_scenes(call: ServiceCall) -> None:
        store = _store(hass)
        payload = call.data[ATTR_SCENES]
        if call.data.get(ATTR_REPLACE_ALL, False):
            await store.async_reload(payload)
        else:
            await store.async_put_many(Scene.from_dict(item) for item in payload)
        _reload_selects(hass)

    @_scene_error
    async def _apply_look(call: ServiceCall) -> None:
        from .protocol.constants import DIRECTIONS
        from .protocol.spec import parse_color

        palette = None
        if (raw := call.data.get(ATTR_PALETTE)) is not None:
            palette = []
            for token in _colour_entries(raw, allow_off=False):
                rgb, white = parse_color(token)
                if rgb is None:
                    raise ValueError("the palette does not accept warm-white levels")
                # A look palette is a list of colours; a white level becomes an
                # RGBW tuple, which the look builder ignores beyond the colour.
                palette.append((*rgb, white) if white is not None else rgb)
        direction = DIRECTIONS.get(
            str(call.data[ATTR_DIRECTION]).lower(), DIRECTIONS["l2r"]
        )
        for coordinator in _coordinators(hass, call.data[ATTR_ENTITY_ID]):
            await coordinator.async_set_look(
                call.data[ATTR_LOOK],
                palette=palette,
                direction=direction,
                density=call.data[ATTR_DENSITY],
                speed=call.data[ATTR_SPEED],
                effect=call.data[ATTR_SCRIBBLE_EFFECT],
            )

    for service, handler, schema in (
        (SERVICE_SAVE_SCENE, _save_scene, SAVE_SCENE_SCHEMA),
        (SERVICE_DELETE_SCENE, _delete_scene, DELETE_SCENE_SCHEMA),
        (SERVICE_APPLY_SCENE, _apply_scene, APPLY_SCENE_SCHEMA),
        (SERVICE_APPLY_SCENE_FOR_DATE, _apply_scene_for_date, APPLY_SCENE_FOR_DATE_SCHEMA),
        (SERVICE_IMPORT_SCENES, _import_scenes, IMPORT_SCENES_SCHEMA),
        (SERVICE_APPLY_LOOK, _apply_look, APPLY_LOOK_SCHEMA),
    ):
        hass.services.async_register(DOMAIN, service, handler, schema=schema)


def async_unload_scene_services(hass: HomeAssistant) -> None:
    """Remove the scene, look and date services."""
    for service in (
        SERVICE_SAVE_SCENE,
        SERVICE_DELETE_SCENE,
        SERVICE_APPLY_SCENE,
        SERVICE_APPLY_SCENE_FOR_DATE,
        SERVICE_IMPORT_SCENES,
        SERVICE_APPLY_LOOK,
    ):
        hass.services.async_remove(DOMAIN, service)


def _coordinators(hass: HomeAssistant, entity_ids: list[str]):
    """Resolve light entity IDs to their coordinators.

    Shared with the protocol services so a group light means the same thing
    everywhere: one entity in, every member controller out.
    """
    return coordinators_for_entities(hass, entity_ids)


def _reload_selects(hass: HomeAssistant) -> None:
    """Push a store change into the running scene selects."""
    for coordinator in hass.data[DOMAIN].values():
        if hasattr(coordinator, "async_update_listeners"):
            coordinator.async_update_listeners()


__all__ = [
    "APPLY_LOOK_SCHEMA",
    "APPLY_SCENE_FOR_DATE_SCHEMA",
    "APPLY_SCENE_SCHEMA",
    "DELETE_SCENE_SCHEMA",
    "IMPORT_SCENES_SCHEMA",
    "MAX_SCENES",
    "SAVE_SCENE_SCHEMA",
    "async_setup_scene_services",
    "async_unload_scene_services",
    "known_effects",
    "known_scribble_effects",
    "scene_from_call",
]
