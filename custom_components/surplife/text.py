"""The per-LED ("scribble") pattern editor.

The protocol's per-LED command takes a full space-separated pattern string, which
is fine for a script but unusable from a phone. This platform turns it into an
editable entity per controller: type ``12xred 23xblue/fast`` and the strip
renders it immediately. The current pattern is the entity's state, so it is
visible in the UI and survives a restart via restore.

It writes through the coordinator's verified ``async_set_scribble`` path, so it
cannot do anything the ``surplife.set_scribble`` service cannot. The curated,
tap-friendly alternative lives next door as
``select.<name>_scribble_look``.
"""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SurplifeCoordinator
from .protocol.spec import parse_led_spec

_LOGGER = logging.getLogger(__name__)

#: Longest pattern string accepted by the text entity. 35 LEDs need at most
#: about 8 characters each, so this is generous while still bounded.
MAX_PATTERN_LENGTH: Final = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the scribble pattern entity."""
    coordinator: SurplifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SurplifeScribblePatternText(coordinator)])


class SurplifeScribblePatternText(
    CoordinatorEntity[SurplifeCoordinator], TextEntity, RestoreEntity
):
    """A typed per-LED pattern that renders as soon as it is set."""

    _attr_has_entity_name = True
    _attr_translation_key = "scribble_pattern"
    _attr_icon = "mdi:draw"
    # Home Assistant validates the entity's state against these bounds, and a
    # freshly created entity has no pattern yet, so the minimum must be 0 even
    # though *applying* an empty pattern is rejected in `async_set_value`.
    _attr_native_min = 0
    _attr_native_max = MAX_PATTERN_LENGTH
    _attr_mode = TextMode.TEXT

    def __init__(self, coordinator: SurplifeCoordinator) -> None:
        """Initialise the text entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_scribble_pattern"
        self._attr_native_value = ""

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry."""
        return self.coordinator.device_info

    async def async_added_to_hass(self) -> None:
        """Restore the last pattern."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._attr_native_value = last.state if last.state != "unknown" else ""

    async def async_set_value(self, value: str) -> None:
        """Render the pattern on the strip.

        A malformed pattern raises before anything is sent, because
        :func:`parse_led_spec` validates the whole string up front. That keeps
        a typo from half-painting the strip.
        """
        pattern = value.strip()
        if not pattern:
            raise HomeAssistantError("the per-LED pattern is empty")
        try:
            leds = parse_led_spec(pattern, self.coordinator.runtime.led_count)
        except ValueError as err:
            raise HomeAssistantError(f"invalid per-LED pattern: {err}") from err
        await self.coordinator.async_set_scribble(leds, look="custom")
        self._attr_native_value = pattern
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Remind the user of the grammar without leaving the UI."""
        return {
            "grammar": (
                "space-separated tokens; '<n>x<colour>' for a run; "
                "append '/slow' or '/fast' to blink; 'w<n>' for warm white"
            ),
            "example": "12xred 23xblue/fast",
        }
