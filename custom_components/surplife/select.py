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
from .const import STATIC_EFFECT as STATIC
from .coordinator import SurplifeCoordinator
from .effect_profile import profile_for
from .protocol.constants import EFFECT_NAMES
from .scenes import Scene, SceneStore, select_for_date
from .scribble_looks import LOOKS
from .settings import (
    EffectSettings,
    effect_settings,
    look_settings,
    replace_settings,
    with_effect,
)

CUSTOM = "custom"

#: Shown by the scene select when no stored scene is active.
NO_SCENE = "none"

#: Shown by the scene select to represent "whichever scene the calendar
#: chooses for today".
TODAY = "today"


def _describe_colour(colour: tuple[int, ...]) -> str:
    """Render an RGB or RGBW palette entry for a state attribute.

    The W level is printed separately because it is a different LED channel, not
    a lighter shade of the colour.
    """
    rendered = "#{:02x}{:02x}{:02x}".format(*colour[:3])
    if len(colour) == 4 and colour[3]:
        return f"{rendered} w{colour[3]}"
    return rendered


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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Describe what the selected effect uses, so the picker is honest.

        The panel is where the colours are chosen, so this is the only place a
        dashboard can see what an effect was configured with. It also says
        which sliders matter for this effect, which is what stops a user from
        dragging a density slider that the firmware ignores.
        """
        effect = self.current_option
        if effect == STATIC:
            return {"description": "A steady colour on the whole run."}
        profile = profile_for(effect)
        return {
            "description": profile.description,
            "palette_mode": profile.palette.value,
            "colours": profile.panel_colours,
            "uses_speed": profile.speed,
            "uses_density": profile.density,
            "uses_direction": profile.direction,
        }

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

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry."""
        return self.coordinator.device_info

    @property
    def current_option(self) -> str:
        """The current direction."""
        return (
            "right_to_left"
            if self.coordinator.runtime.effect_direction == 2
            else "left_to_right"
        )

    async def async_select_option(self, option: str) -> None:
        """Store the direction, and apply it if an effect is showing."""
        runtime = self.coordinator.runtime
        runtime.effect_direction = 2 if option == "right_to_left" else 1
        effect = runtime.effect_name
        if effect is not None:
            stored = effect_settings(self.coordinator.entry.options, effect)
            updated = replace_settings(
                stored or EffectSettings(), direction=runtime.effect_direction
            )
            options = with_effect(self.coordinator.entry.options, effect, updated)
            # Persist first: the effect command resolves its direction from the
            # saved value, so saving afterwards would send the old one. The save
            # is claimed before it happens because Home Assistant dispatches the
            # update listener eagerly, inside the save call.
            self.coordinator.expect_no_reapply(options)
            self.hass.config_entries.async_update_entry(
                self.coordinator.entry, options=options
            )
            await self.coordinator.async_set_effect(effect)
        self.async_write_ha_state()

    @property
    def direction_byte(self) -> int:
        """The wire value for the current direction."""
        return self.coordinator.runtime.effect_direction


class SurplifeScribbleLookSelect(CoordinatorEntity[SurplifeCoordinator], SelectEntity):
    """Pick a curated per-LED look.

    The look list is short and fixed, which is the point: it is the subset of
    per-LED painting that is genuinely useful from a phone. Each look remembers
    the palette configured for it in the Configure panel, so this dropdown is
    the everyday control and the panel is where you customise it.
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
        saved = look_settings(self.coordinator.entry.options, self.current_option)
        palette = saved.palette if saved is not None and saved.palette else look.palette
        return {
            "description": look.description,
            "palette": [_describe_colour(colour) for colour in palette],
            "supports_palette": look.supports_palette,
            "colours": look.palette_size,
        }

    async def async_select_option(self, option: str) -> None:
        """Render the selected look with its saved palette.

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
