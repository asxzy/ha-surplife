"""Effect speed and density."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SurplifeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up speed and density numbers."""
    coordinator: SurplifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SurplifeEffectNumber(
                coordinator, key="speed", name="Effect speed", icon="mdi:speedometer"
            ),
            SurplifeEffectNumber(
                coordinator,
                key="density",
                name="Effect density",
                icon="mdi:format-align-middle",
            ),
        ]
    )


class SurplifeEffectNumber(
    CoordinatorEntity[SurplifeCoordinator], NumberEntity, RestoreEntity
):
    """One 0-100 effect parameter.

    The device reports speed in its state frame but not density, and the value
    only takes effect when an effect is next applied, so both are restored
    locally and sent with the next effect command.
    """

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: SurplifeCoordinator,
        *,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        """Initialise the number."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_value = 50.0

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry."""
        return self.coordinator.device_info

    async def async_added_to_hass(self) -> None:
        """Restore the last value."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                self._attr_native_value = float(last.state)
            except (TypeError, ValueError):
                pass

    @property
    def native_value(self) -> float:
        """The current value."""
        if self._key == "speed" and self.coordinator.runtime.state is not None:
            # Speed is genuinely reported by the device, so prefer it.
            return float(self.coordinator.runtime.state.speed)
        return float(self._attr_native_value or 0)

    async def async_set_native_value(self, value: float) -> None:
        """Store the value for the next effect command."""
        self._attr_native_value = value
        self.async_write_ha_state()
