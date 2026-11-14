"""Buttons: sync clock, re-read the device, and draw a fresh theme."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SurplifeCoordinator
from .groups import SurplifeGroupCoordinator
from .themes import generate_scene

#: A controller and a group both present the command surface these buttons call.
ControlCoordinator = SurplifeCoordinator | SurplifeGroupCoordinator



async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up buttons."""
    coordinator: ControlCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SurplifeSyncClockButton(coordinator),
            SurplifeRefreshButton(coordinator),
            SurplifeNewThemeButton(coordinator),
        ]
    )


class _SurplifeButton(CoordinatorEntity[ControlCoordinator], ButtonEntity):
    """Shared device wiring for this integration's buttons."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ControlCoordinator, key: str) -> None:
        """Initialise the button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry."""
        return self.coordinator.device_info


class SurplifeSyncClockButton(_SurplifeButton):
    """Push Home Assistant's local time to the controller.

    The controller's clock is what drives its hardware timers, and it drifts.
    Syncing after a power cut or DST change keeps the timers honest.
    """

    _attr_translation_key = "sync_clock"
    _attr_icon = "mdi:clock-check"

    def __init__(self, coordinator: ControlCoordinator) -> None:
        super().__init__(coordinator, "sync_clock")

    async def async_press(self) -> None:
        """Sync the clock."""
        await self.coordinator.async_sync_clock()


class SurplifeRefreshButton(_SurplifeButton):
    """Force an immediate state read.

    Useful after using the official app, whose changes would otherwise wait for
    the next poll.
    """

    _attr_translation_key = "refresh"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: ControlCoordinator) -> None:
        super().__init__(coordinator, "refresh")

    async def async_press(self) -> None:
        """Re-read the state frame."""
        await self.coordinator.async_request_refresh()


class SurplifeNewThemeButton(_SurplifeButton):
    """Draw and apply a fresh theme for this strip.

    Same operation as ``surplife.apply_theme`` aimed at this controller, but
    from the device page: the draw comes from the unseeded generator, so every
    press is a new one rather than a repeat of what is already showing.
    """

    _attr_translation_key = "new_theme"
    _attr_icon = "mdi:palette"

    def __init__(self, coordinator: ControlCoordinator) -> None:
        super().__init__(coordinator, "new_theme")

    async def async_press(self) -> None:
        """Generate a fresh display and apply it to this controller."""
        from homeassistant.util import dt as dt_util

        scene = generate_scene(dt_util.now().date())
        await scene.async_apply(self.coordinator)
