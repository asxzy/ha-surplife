"""Effect speed and density."""

from __future__ import annotations

import contextlib

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SurplifeCoordinator
from .settings import (
    EffectSettings,
    effect_settings,
    replace_settings,
    with_effect,
)


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

    Changing one of these while an effect is showing re-applies that effect
    immediately, so the slider is a live control rather than a value that sits
    waiting for the next effect change.
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
            with contextlib.suppress(TypeError, ValueError):
                self._attr_native_value = float(last.state)

    @property
    def native_value(self) -> float:
        """The current value."""
        if self._key == "speed" and self.coordinator.runtime.state is not None:
            # Speed is genuinely reported by the device, so prefer it.
            return float(self.coordinator.runtime.state.speed)
        if self._key == "density":
            return float(self.coordinator.runtime.effect_density)
        return float(self._attr_native_value or 0)

    async def async_set_native_value(self, value: float) -> None:
        """Store the value, and apply it if an effect is showing.

        The value is saved to the config entry's options *before* the effect is
        re-applied. Order matters: ``async_set_effect`` prefers the saved value
        for the effect over the runtime's, so saving afterwards made the first
        command carry the previous value and only a second, listener-driven
        command carry the new one.
        """
        self._attr_native_value = value
        runtime = self.coordinator.runtime
        if self._key == "speed":
            runtime.effect_speed = int(value)
        else:
            runtime.effect_density = int(value)

        # The flag is set *before* saving: Home Assistant dispatches the update
        # listener eagerly, so it runs inside ``_persist`` rather than after it.
        self.coordinator.skip_next_reapply()
        await self._persist(int(value))
        if runtime.effect_name is not None:
            await self.coordinator.async_set_effect(runtime.effect_name)
        self.async_write_ha_state()

    async def _persist(self, value: int) -> None:
        """Save this slider's value against the active effect, if there is one.

        Speed and density are per-effect settings, so there is nowhere to store
        them when no effect is showing. In that case the runtime value is still
        used for the next effect, which is the useful behaviour anyway.
        """
        effect = self.coordinator.runtime.effect_name
        if effect is None:
            return
        stored = effect_settings(self.coordinator.entry.options, effect)
        settings = stored or EffectSettings()
        updated = (
            replace_settings(settings, speed=value)
            if self._key == "speed"
            else replace_settings(settings, density=value)
        )
        self.hass.config_entries.async_update_entry(
            self.coordinator.entry,
            options=with_effect(self.coordinator.entry.options, effect, updated),
        )
