"""Surplife Permanent Lighting integration for Home Assistant.

Exposes the full feature surface of Zengge 0xB6 controllers — native effects,
segments, per-LED "scribble", hardware timers, and user-defined scenes that can
follow the calendar — which the stock ``flux_led`` integration deliberately
leaves out.

Two kinds of config entry share this module. A **controller** entry owns one
socket to one strip, through :class:`~.coordinator.SurplifeCoordinator`. A
**group** entry owns no socket at all: it is a device with one light that
forwards to the controllers it names, because the app's own grouping is
cloud-side and there is no local group command on the wire.
"""

from __future__ import annotations

import datetime as dt
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .coordinator import SurplifeCoordinator
from .groups import is_group_entry
from .scene_services import async_setup_scene_services, async_unload_scene_services
from .scenes import SceneStore
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

#: Platforms a controller entry sets up.
CONTROLLER_PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

#: Platforms a group entry sets up. Only a light, because every other control is
#: a property of a strip — an effect select or a clock button on a group would
#: have to arbitrate between members that may already disagree.
GROUP_PLATFORMS: list[Platform] = [Platform.LIGHT]

#: Kept for compatibility with anything importing the old single list.
PLATFORMS = CONTROLLER_PLATFORMS

#: Key in ``hass.data[DOMAIN]`` holding the integration-wide scene store. It is
#: a plain string rather than an entry id on purpose: scenes are shared
#: vocabulary across every controller, so there is exactly one store.
SCENE_STORE_KEY = "__scenes__"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a controller, or a group of controllers."""
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

    if is_group_entry(entry):
        # A group holds no connection and polls nothing, so there is no
        # coordinator to create and nothing to read. Its light derives its state
        # from the member entities through the state machine.
        #
        # No update listener either: membership lives in the entry's ``data``,
        # and the only thing that changes it is Reconfigure, which reloads the
        # entry itself. Adding a listener here would reload a second time.
        await hass.config_entries.async_forward_entry_setups(entry, GROUP_PLATFORMS)
        return True

    coordinator = SurplifeCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_load_timers()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, CONTROLLER_PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a controller or a group config entry."""
    platforms = GROUP_PLATFORMS if is_group_entry(entry) else CONTROLLER_PLATFORMS
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded:
        coordinator: SurplifeCoordinator | None = hass.data[DOMAIN].pop(
            entry.entry_id, None
        )
        if coordinator is not None:
            await coordinator.async_shutdown()
        if not any(key for key in hass.data[DOMAIN] if key not in _NON_ENTRY_KEYS):
            async_unload_services(hass)
            async_unload_scene_services(hass)
    return unloaded


#: Keys in ``hass.data[DOMAIN]`` that are not config-entry ids.
_NON_ENTRY_KEYS = frozenset({SCENE_STORE_KEY})


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply an options change without needlessly reloading the entry.

    Most options are *data* the coordinator reads on demand: an effect's saved
    palette and parameters, a look's palette. Reloading for those would tear
    down every entity and discard the optimistic colour/brightness state the
    firmware never echoes, so the panel would appear to "forget" what it just
    applied. Only the poll interval needs a reload, because it is baked into the
    coordinator's timer at construction.
    """
    coordinator: SurplifeCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is None:
        return

    interval = dt.timedelta(seconds=coordinator.scan_interval)
    if coordinator.update_interval != interval:
        coordinator.update_interval = interval
        _LOGGER.debug("poll interval for %s is now %s", coordinator.host, interval)

    # Re-push whatever is showing so an edit to the active effect is visible
    # immediately rather than on the next unrelated command. A controller that
    # is offline must not make saving the panel fail -- the settings are already
    # stored by the time this runs, and the next poll or command will apply them.
    #
    # The sliders, the direction selector and the Configure panel have already
    # applied this exact options payload and claimed it, so it is not sent again.
    if coordinator.consume_skip_reapply():
        return
    effect = coordinator.runtime.effect_name
    if effect is not None:
        try:
            await coordinator.async_set_effect(effect)
        except HomeAssistantError as err:
            _LOGGER.debug("could not re-apply %s after options change: %s", effect, err)
