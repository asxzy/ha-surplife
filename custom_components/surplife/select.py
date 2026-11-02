"""Effect selection."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SurplifeCoordinator
from .protocol.constants import EFFECT_NAMES

STATIC = "Static"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up effect and direction selects."""
    coordinator: SurplifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SurplifeEffectSelect(coordinator),
            SurplifeDirectionSelect(coordinator),
        ]
    )


class SurplifeEffectSelect(CoordinatorEntity[SurplifeCoordinator], SelectEntity):
    """Pick a native effect by name.

    The light entity also exposes effects; this select exists so effects can be
    driven from automations without building a full ``light.turn_on`` call, and
    so the current effect is visible as its own state.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "effect"
    _attr_icon = "mdi:creation"

    def __init__(self, coordinator: SurplifeCoordinator) -> None:
        """Initialise the select."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_effect_select"
        self._attr_options = [STATIC, *EFFECT_NAMES.values()]

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry."""
        return self.coordinator.device_info

    @property
    def current_option(self) -> str | None:
        """The active effect, or ``Static`` when none is set."""
        effect = self.coordinator.runtime.effect_name
        return effect if effect in self._attr_options else STATIC

    async def async_select_option(self, option: str) -> None:
        """Apply the selected effect, or return to a static colour."""
        if option == STATIC:
            runtime = self.coordinator.runtime
            await self.coordinator.async_set_rgb(
                runtime.rgb or (255, 255, 255), runtime.brightness
            )
            return
        await self.coordinator.async_set_effect(option)


class SurplifeDirectionSelect(CoordinatorEntity[SurplifeCoordinator], SelectEntity):
    """Animation direction for effects."""

    _attr_has_entity_name = True
    _attr_translation_key = "direction"
    _attr_icon = "mdi:arrow-left-right"
    _attr_options = ["left_to_right", "right_to_left"]

    def __init__(self, coordinator: SurplifeCoordinator) -> None:
        """Initialise the select."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_direction"
        self._direction = "left_to_right"

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry."""
        return self.coordinator.device_info

    @property
    def current_option(self) -> str:
        """The current direction."""
        return self._direction

    async def async_select_option(self, option: str) -> None:
        """Store the direction; it is applied with the next effect."""
        self._direction = option
        self.async_write_ha_state()

    @property
    def direction_byte(self) -> int:
        """The wire value for the current direction."""
        return 1 if self._direction == "left_to_right" else 2
