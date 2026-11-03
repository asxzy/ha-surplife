"""Surplife Permanent Lighting integration for Home Assistant.

Exposes the full feature surface of Zengge 0xB6 controllers — native effects,
segments, per-LED "scribble", hardware timers, and user-defined scenes that can
follow the calendar — which the stock ``flux_led`` integration deliberately
leaves out.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import SurplifeCoordinator
from .scene_services import async_setup_scene_services, async_unload_scene_services
from .scenes import SceneStore
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

#: Key in ``hass.data[DOMAIN]`` holding the integration-wide scene store. It is
#: a plain string rather than an entry id on purpose: scenes are shared
#: vocabulary across every controller, so there is exactly one store.
SCENE_STORE_KEY = "__scenes__"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a controller from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # The scene store is integration-wide, so it is created by the first entry
    # and survives the reload of any individual one.
    if SCENE_STORE_KEY not in hass.data[DOMAIN]:
        store = SceneStore(hass)
        await store.async_load()
        hass.data[DOMAIN][SCENE_STORE_KEY] = store

    if not hass.services.has_service(DOMAIN, "sync_clock"):
        await async_setup_services(hass)
        await async_setup_scene_services(hass)

    coordinator = SurplifeCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_load_timers()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: SurplifeCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
        if not any(
            key for key in hass.data[DOMAIN] if key not in _NON_ENTRY_KEYS
        ):
            async_unload_services(hass)
            async_unload_scene_services(hass)
    return unloaded


#: Keys in ``hass.data[DOMAIN]`` that are not config-entry ids.
_NON_ENTRY_KEYS = frozenset({SCENE_STORE_KEY})


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
