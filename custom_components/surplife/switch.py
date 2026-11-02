"""Switch platform: reserved for future per-device toggles.

The stock ``flux_led`` integration exposes "remote access" and "music mode"
switches. Neither applies here: remote access is a cloud feature and the 0xB6
has no music mode on the local protocol. Rather than create entities that do
nothing, this platform registers nothing and exists only so the platform list
stays stable.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register no switches."""
    return
