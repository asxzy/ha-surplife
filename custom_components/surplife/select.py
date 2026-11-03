"""Effect selection."""

from __future__ import annotations

import datetime as dt
from typing import Any, ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SurplifeCoordinator
from .protocol.constants import EFFECT_NAMES
from .scenes import Scene, SceneStore, select_for_date
from .scribble_looks import LOOKS

STATIC = "Static"

#: Shown by the look select when the strip is showing something typed into the
#: pattern text entity rather than one of the curated looks.
CUSTOM = "custom"

#: Shown by the scene select when no stored scene is active.
NO_SCENE = "none"

#: Shown by the scene select to represent "whichever scene the calendar
#: chooses for today".
TODAY = "today"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up effect, direction, look and scene selects."""
    coordinator: SurplifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    store: SceneStore = hass.data[DOMAIN]["__scenes__"]
    async_add_entities(
        [
            SurplifeEffectSelect(coordinator),
            SurplifeDirectionSelect(coordinator),
            SurplifeScribbleLookSelect(coordinator),
            SurplifeSceneSelect(coordinator, store),
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
    _attr_options: ClassVar[list[str]] = ["left_to_right", "right_to_left"]

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


class SurplifeScribbleLookSelect(CoordinatorEntity[SurplifeCoordinator], SelectEntity):
    """Pick a curated per-LED look.

    The look list is short and fixed, which is the point: it is the subset of
    per-LED painting that is genuinely useful from a phone. Anything more
    specific goes through the pattern text entity or the service.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "scribble_look"
    _attr_icon = "mdi:palette-swatch"

    def __init__(self, coordinator: SurplifeCoordinator) -> None:
        """Initialise the select."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_scribble_look"
        self._attr_options = [CUSTOM, *sorted(LOOKS)]

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry."""
        return self.coordinator.device_info

    @property
    def current_option(self) -> str:
        """The look currently showing, or ``custom``."""
        look = self.coordinator.runtime.look
        return look if look in LOOKS else CUSTOM

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose what the selected look does, for the UI."""
        look = LOOKS.get(self.current_option)
        if look is None:
            return {"description": "a pattern typed into the scribble pattern entity"}
        return {
            "description": look.description,
            "palette": [
                f"#{red:02x}{green:02x}{blue:02x}"
                for red, green, blue in look.palette
            ],
        }

    async def async_select_option(self, option: str) -> None:
        """Render the selected look.

        ``custom`` is a no-op: it is the state shown after a typed pattern, not
        something to apply.
        """
        if option == CUSTOM:
            self.coordinator.runtime.look = CUSTOM
            self.async_write_ha_state()
            return
        if option not in LOOKS:
            raise ValueError(f"unknown look {option!r}")
        await self.coordinator.async_set_look(option)


class SurplifeSceneSelect(CoordinatorEntity[SurplifeCoordinator], SelectEntity):
    """Apply a stored scene, or the one the calendar chooses for today.

    The option list is rebuilt on every read rather than cached at setup,
    because scenes are created and deleted at runtime. ``today`` sits at the top
    because it is the one option that makes the whole feature useful from an
    automation: it resolves through the date windows and the stored tags.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "scene"
    _attr_icon = "mdi:calendar-star"

    def __init__(self, coordinator: SurplifeCoordinator, store: SceneStore) -> None:
        """Initialise the select."""
        super().__init__(coordinator)
        self._store = store
        self._attr_unique_id = f"{coordinator.entry.entry_id}_scene"

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry."""
        return self.coordinator.device_info

    @property
    def _scene_names(self) -> list[str]:
        return self._store.names()

    @property
    def options(self) -> list[str]:
        """``today``, ``none``, then every stored scene."""
        return [TODAY, NO_SCENE, *self._scene_names]

    @property
    def current_option(self) -> str:
        """The active scene, or ``none``."""
        scene = self.coordinator.runtime.scene
        return scene if scene in self._store.scenes else NO_SCENE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Describe the stored scenes so the picker is self-documenting."""
        return {
            "scenes": {
                scene.name: {
                    "title": scene.title,
                    "summary": scene.describe(),
                    "season": scene.season,
                    "tags": list(scene.tags),
                }
                for scene in self._store.all()
            },
            "today": _today_choice(self._store, self.hass),
        }

    async def async_select_option(self, option: str) -> None:
        """Apply a stored scene, or resolve ``today``.

        Raises:
            ValueError: if the option is unknown, or ``today`` matches nothing.
        """
        if option == NO_SCENE:
            self.coordinator.runtime.scene = None
            self.async_write_ha_state()
            return
        if option == TODAY:
            scene = _select_today(self._store, self.hass)
            if scene is None:
                raise ValueError(
                    "no stored scene is in season today; give a scene a date "
                    "window or apply one by name"
                )
            await scene.async_apply(self.coordinator)
            return
        scene = self._store.require(option)
        await scene.async_apply(self.coordinator)


def _today(hass: HomeAssistant) -> dt.date:
    """Today's date in Home Assistant's configured time zone."""
    return dt_util.now().date()


def _select_today(store: SceneStore, hass: HomeAssistant) -> Scene | None:
    """The deterministic 'today' pick, shared by the select and its attributes."""
    return select_for_date(store.all(), _today(hass), randomize=False)


def _today_choice(store: SceneStore, hass: HomeAssistant) -> str | None:
    """The scene ``today`` currently resolves to, for the attributes."""
    scene = _select_today(store, hass)
    return scene.title if scene else None
