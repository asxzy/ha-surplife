"""Group several controllers into one device with one light.

The app's grouping is cloud-side only — a ``GroupControl`` record pushed over
MQTT, with no local-LAN group command (see ``docs/architecture.md``). So a group
here is a Home Assistant construct: one config entry that owns no socket, plus
one light entity that forwards every request to the member controllers and
reports the combined state back.

**Why a config entry and not an entity helper.** Home Assistant's stock
``group`` helper produces an entity with no device behind it, and it can only
forward the generic light attributes. A real device gives one place in the UI
for the whole roof — device page, area, rename, and one entity id to point
dashboards and automations at. It also lets the ``surplife.*`` services accept
the group and expand it to its members, which is what makes "apply the
Christmas preset to the roof" a single call rather than three.

**Why the group has no coordinator.** It holds no connection and polls nothing.
Its state is derived from the members' states through the state machine, so it
can never disagree with them or go stale on its own. Commands still go through
each member's own entity, which keeps the optimistic colour/brightness rules and
the per-controller write locks exactly where they were.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Final

from homeassistant.components import light as light_component
from homeassistant.components.light import (
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN, MANUFACTURER, STATIC_EFFECT
from .coordinator import SurplifeCoordinator
from .protocol.constants import EFFECT_NAMES

_LOGGER = logging.getLogger(__name__)

#: Key in a group entry's ``data`` holding the member config-entry ids. Its
#: presence is what makes an entry a group: a controller entry always has a
#: host and never has this key, so the two can never be confused after an
#: upgrade.
GROUP_MEMBERS_KEY: Final = "members"

GROUP_MODEL: Final = "Surplife Permanent Lighting group"

#: The same channel model a single controller advertises: a colour picker plus
#: a real W channel, never a simulated white. Kept in step with
#: :mod:`custom_components.surplife.light` by ``tests/test_group.py``.
SUPPORTED_COLOR_MODES: Final = {ColorMode.RGBW, ColorMode.WHITE}


def is_group_entry(entry: ConfigEntry) -> bool:
    """Whether a config entry describes a group rather than a controller."""
    return bool(entry.data.get(GROUP_MEMBERS_KEY))


def controller_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Every loaded-or-not controller entry, in the order Home Assistant has them."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if not is_group_entry(entry)
    ]


def group_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Every group entry."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if is_group_entry(entry)
    ]


def live_members(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """The member entry ids that still exist.

    A controller can be deleted while a group names it. Rather than failing the
    group's setup forever, the stale id is dropped; if that leaves fewer than
    two members the group keeps working with what is left and the user can
    re-point it from Reconfigure.
    """
    known = {member.entry_id for member in controller_entries(hass)}
    return [
        entry_id
        for entry_id in entry.data.get(GROUP_MEMBERS_KEY, [])
        if entry_id in known
    ]


def coordinators_for_entities(
    hass: HomeAssistant, entity_ids: list[str]
) -> list[SurplifeCoordinator]:
    """Resolve Surplife light entity ids to their coordinators.

    A member light maps to one coordinator through its ``surplife_entry_id``
    attribute. A group light maps to every member through
    ``surplife_group_members``, so one service call aimed at the group reaches
    all three strips — otherwise ``surplife.apply_preset`` would have to be
    called once per controller, which is the inconvenience the group exists to
    remove.

    Args:
        hass: The running instance.
        entity_ids: Light entity ids named by a service call.

    Returns:
        Coordinators in request order, de-duplicated, so naming both a group
        and one of its members sends each command once.

    Raises:
        HomeAssistantError: if an entity is unknown, is not a Surplife light, or
            belongs to an entry that is not loaded.
    """
    from homeassistant.exceptions import HomeAssistantError

    result: list[SurplifeCoordinator] = []
    seen: set[str] = set()
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None:
            raise HomeAssistantError(f"unknown entity {entity_id}")

        entry_ids: list[str] = []
        if (entry_id := state.attributes.get("surplife_entry_id")) is not None:
            entry_ids.append(entry_id)
        entry_ids.extend(state.attributes.get("surplife_group_members") or [])
        if not entry_ids:
            raise HomeAssistantError(f"{entity_id} is not a Surplife light")

        for entry_id in entry_ids:
            if entry_id in seen:
                continue
            coordinator = hass.data[DOMAIN].get(entry_id)
            if coordinator is None:
                raise HomeAssistantError(
                    f"{entity_id} includes a controller that is not loaded"
                )
            seen.add(entry_id)
            result.append(coordinator)
    return result


class SurplifeGroupLight(LightEntity):
    """One light for every controller in the group, with a device behind it.

    **State is derived, never commanded.** Every request goes to the member
    entities, so the group inherits their optimistic colour and brightness
    handling instead of keeping a second copy that could drift. The group's own
    state is recomputed from the members whenever one of them changes.

    **On means any member is on.** That is what makes the group safe to use as
    an indicator: turning the group off turns every strip off, and a single
    strip left running still shows as on rather than claiming the roof is dark.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_supported_color_modes = SUPPORTED_COLOR_MODES
    _attr_supported_features = LightEntityFeature.EFFECT
    #: Same list as a controller, for the same reason: ``Static`` first so the
    #: card can always leave pattern mode, then the 24 native effects.
    _attr_effect_list: tuple[str, ...] = (STATIC_EFFECT, *EFFECT_NAMES.values())

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the group light from its config entry."""
        self.hass = hass
        self._entry = entry
        self._member_entry_ids = live_members(hass, entry)
        self._entity_ids: list[str] = []
        self._unsubscribe = None
        self._attr_unique_id = f"{entry.entry_id}_group_light"
        self._attr_is_on = False
        self._attr_available = False
        self._attr_color_mode = ColorMode.RGBW
        self._attr_effect = None

    # -- device ------------------------------------------------------------

    @property
    def device_info(self) -> DeviceInfo:
        """Registry entry for the group's own device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"group:{self._entry.entry_id}")},
            name=self._entry.title,
            manufacturer=MANUFACTURER,
            model=GROUP_MODEL,
            model_id="group",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the membership.

        ``surplife_group_members`` is what the services read to expand the group
        into its controllers, so it is part of the contract rather than
        decoration. ``entity_id`` mirrors what Home Assistant's own light group
        publishes, for anyone debugging from the states page.
        """
        return {
            "surplife_group_members": list(self._member_entry_ids),
            ATTR_ENTITY_ID: list(self._entity_ids),
            "members": [
                entry.title
                for member in self._member_entry_ids
                if (entry := self.hass.config_entries.async_get_entry(member))
            ],
        }

    # -- wiring ------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Start tracking the members' states."""
        await super().async_added_to_hass()
        self._resubscribe()
        self.async_on_remove(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_updated
            )
        )

    def _member_entity_ids(self) -> list[str]:
        """Resolve member entry ids to their light entities.

        The entity registry is the only reliable map: a controller's entity id
        follows the device name, so a rename must not break the group. Resolving
        by entry id also means a member that is not loaded yet simply has no
        light entity, and the group picks it up as soon as it appears — which is
        what happens on a boot where the group loads first.
        """
        registry = er.async_get(self.hass)
        entity_ids: list[str] = []
        for entry_id in self._member_entry_ids:
            entity_ids.extend(
                entity.entity_id
                for entity in er.async_entries_for_config_entry(registry, entry_id)
                if entity.domain == "light" and entity.disabled_by is None
            )
        return entity_ids

    @callback
    def _resubscribe(self) -> None:
        """Point the state listener at the current set of member entities."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._entity_ids = self._member_entity_ids()
        if self._entity_ids:
            self._unsubscribe = async_track_state_change_event(
                self.hass, self._entity_ids, self._member_changed
            )
        else:
            _LOGGER.debug(
                "group %s has no member light entities yet", self._entry.title
            )
        self._recompute()

    @callback
    def _registry_updated(self, event: Event) -> None:
        """Follow members that appear, disappear or change entity id.

        Every registry event is checked rather than filtered by action: a
        rename, a move to another device and a re-enable all change which
        entities belong to the group, and none of them is worth guessing at
        from the payload when the resolution is two dictionary lookups.
        """
        if set(self._member_entity_ids()) != set(self._entity_ids):
            self._resubscribe()
            self.async_write_ha_state()

    @callback
    def _member_changed(self, event: Event[EventStateChangedData]) -> None:
        """Recompute from the members and publish."""
        self._recompute()
        self.async_write_ha_state()

    # -- state -------------------------------------------------------------

    @callback
    def _recompute(self) -> None:
        """Derive the group's state from its members' states."""
        states = [
            state
            for state in (self.hass.states.get(entity_id) for entity_id in self._entity_ids)
            if state is not None
        ]

        live = [state for state in states if state.state != STATE_UNAVAILABLE]
        self._attr_available = bool(live)
        on_states = [state for state in states if state.state == STATE_ON]
        self._attr_is_on = bool(on_states)

        # Brightness: the mean of what is actually lit, so a group where one
        # strip is dimmer reads as the average rather than as the dimmest one.
        levels = [
            int(state.attributes[light_component.ATTR_BRIGHTNESS])
            for state in on_states
            if state.attributes.get(light_component.ATTR_BRIGHTNESS) is not None
        ]
        self._attr_brightness = round(sum(levels) / len(levels)) if levels else None

        # Colour: report the first lit member's mix. An average of two colours
        # is a colour neither strip is showing, which would make the wheel lie
        # about a state the user can see out the window.
        sample = self._sample(on_states or states)
        other = self._sample(states)
        mode = self._most_common(
            [state.attributes.get(light_component.ATTR_COLOR_MODE) for state in (on_states or states)]
        )
        self._attr_color_mode = ColorMode.WHITE if mode == ColorMode.WHITE else ColorMode.RGBW
        if self._attr_color_mode is ColorMode.WHITE:
            self._attr_rgbw_color = None
            self._attr_rgb_color = None
        else:
            mix = None
            for state in (sample, other):
                if state is None:
                    continue
                candidate = state.attributes.get(light_component.ATTR_RGBW_COLOR)
                if candidate is not None and len(candidate) == 4:
                    mix = tuple(candidate)
                    break
            self._attr_rgbw_color = mix or (255, 255, 255, 0)
            self._attr_rgb_color = self._attr_rgbw_color[:3]

        # Effect: the most common one among the lit members, so three strips in
        # step report that effect and a single straggler cannot rename the group.
        effects = [
            state.attributes.get(light_component.ATTR_EFFECT)
            for state in (on_states or states)
            if state.attributes.get(light_component.ATTR_EFFECT)
        ]
        self._attr_effect = self._most_common(effects) if effects else None

    @staticmethod
    def _sample(states: list[Any]) -> Any:
        """The first state in a list, or ``None``."""
        return states[0] if states else None

    @staticmethod
    def _most_common(values: list[Any]) -> Any:
        """The most frequent value, ties going to the first seen."""
        if not values:
            return None
        return Counter(values).most_common(1)[0][0]

    # -- commands ----------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Forward the request to every member light.

        Forwarding through the light service — rather than calling coordinator
        methods directly — is what keeps one implementation of "a colour while
        an effect runs recolours it" and of the optimistic colour bookkeeping.
        """
        if not self._entity_ids:
            return
        await self.hass.services.async_call(
            light_component.DOMAIN,
            light_component.SERVICE_TURN_ON,
            {**kwargs, ATTR_ENTITY_ID: self._entity_ids},
            blocking=True,
            context=self._context,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn every member off."""
        if not self._entity_ids:
            return
        await self.hass.services.async_call(
            light_component.DOMAIN,
            light_component.SERVICE_TURN_OFF,
            {**kwargs, ATTR_ENTITY_ID: self._entity_ids},
            blocking=True,
            context=self._context,
        )

    @property
    def member_entry_ids(self) -> list[str]:
        """The member config-entry ids, for diagnostics and tests."""
        return list(self._member_entry_ids)


def default_group_name(names: list[str]) -> str:
    """A name from the words the members have in common.

    "Soffit East", "Soffit West" and "Soffit South" become "Soffit", which is
    what the group actually is; the alternative is making every user type a name
    before they can save, and a default of "Surplife group" tells nobody
    anything. The name is only a default — the field is editable and the group
    can be renamed like any other device.
    """
    words = [name.split() for name in names if name.strip()]
    if not words:
        return "Surplife group"
    shared: list[str] = []
    for index in range(min(len(parts) for parts in words)):
        column = {parts[index].lower() for parts in words}
        if len(column) != 1:
            break
        shared.append(words[0][index])
    return " ".join(shared) or "Surplife group"


__all__ = [
    "GROUP_MEMBERS_KEY",
    "GROUP_MODEL",
    "SUPPORTED_COLOR_MODES",
    "SurplifeGroupLight",
    "controller_entries",
    "coordinators_for_entities",
    "default_group_name",
    "group_entries",
    "is_group_entry",
    "live_members",
]
