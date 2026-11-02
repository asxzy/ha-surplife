"""Diagnostic sensors: LED count, firmware, mode, timers."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SurplifeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up diagnostic sensors."""
    coordinator: SurplifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SurplifeLedCountSensor(coordinator),
            SurplifeModeSensor(coordinator),
            SurplifeTimerSensor(coordinator),
        ]
    )


class _SurplifeSensor(CoordinatorEntity[SurplifeCoordinator], SensorEntity):
    """Shared device wiring for this integration's sensors."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SurplifeCoordinator, key: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry."""
        return self.coordinator.device_info


class SurplifeLedCountSensor(_SurplifeSensor):
    """The controller's configured LED count.

    This is the value that sizes every per-LED bitmap, so surfacing it makes
    scribble misbehaviour diagnosable rather than mysterious.
    """

    _attr_translation_key = "led_count"
    _attr_icon = "mdi:led-strip-variant"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SurplifeCoordinator) -> None:
        super().__init__(coordinator, "led_count")

    @property
    def native_value(self) -> int | None:
        """The configured LED count."""
        state = self.coordinator.runtime.state
        return state.led_count if state else None

    @property
    def native_unit_of_measurement(self) -> str:
        """LEDs."""
        return "LEDs"


class SurplifeModeSensor(_SurplifeSensor):
    """Which mode the controller is in: static, segments or scribble."""

    _attr_translation_key = "mode"
    _attr_icon = "mdi:palette"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["static", "segments", "scribble", "unknown"]

    def __init__(self, coordinator: SurplifeCoordinator) -> None:
        super().__init__(coordinator, "mode")

    @property
    def native_value(self) -> str:
        """The current preset label."""
        state = self.coordinator.runtime.state
        if state is None:
            return "unknown"
        label = state.preset_label
        return label if label in self._attr_options else "unknown"


class SurplifeTimerSensor(_SurplifeSensor):
    """A summary of the hardware timers.

    Reading timers costs a round trip, so this reports from the coordinator's
    cache and is refreshed by the ``set_timer`` service or the refresh button.
    """

    _attr_translation_key = "timers"
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: SurplifeCoordinator) -> None:
        super().__init__(coordinator, "timers")

    @property
    def native_value(self) -> int:
        """Number of configured timer slots."""
        return sum(1 for timer in self.coordinator.runtime.timers if timer.active)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Human-readable timer list."""
        return {
            f"slot_{timer.slot}": (
                f"{timer.time_label} {timer.action_name} {timer.repeat_label}"
                if timer.active
                else "unset"
            )
            for timer in self.coordinator.runtime.timers
        }
