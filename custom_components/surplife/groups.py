"""Group several controllers into one device with the full control surface.

The app's grouping is cloud-side only — a ``GroupControl`` record pushed over
MQTT, with no local-LAN group command (see ``docs/architecture.md``). So a group
here is a Home Assistant construct: one config entry that owns no socket, plus
the same entities a single controller has, all of them forwarded to the member
controllers.

**Why a config entry and not an entity helper.** Home Assistant's stock
``group`` helper produces an entity with no device behind it, and it can only
forward the generic light attributes. A real device gives one place in the UI
for the whole roof — device page, area, rename, and one entity id to point
dashboards and automations at. It also lets the ``surplife.*`` services accept
the group and expand it to its members, which is what makes "apply the
Christmas preset to the roof" a single call rather than three.

**Why a coordinator and not a state-tracking light.** The first version of this
module derived the group light from the members' *entity states* and forwarded
commands through the ``light.turn_on`` service. That worked for a toggle and a
colour wheel, but every other control on a controller — the effect select, the
speed slider, the palette text, the scene dropdown — needs a coordinator-shaped
object with a ``runtime``. So the group owns a
:class:`SurplifeGroupCoordinator`: a real ``DataUpdateCoordinator`` whose data
is *derived* from the member coordinators and whose commands fan out to them.
Every entity platform then works on a group unmodified, because it sees the
same surface it sees on a controller.

**What is not merged.** LED count and hardware timers are facts about one strip.
A group of three would have to invent a number, so those diagnostic sensors stay
on the controllers. Everything that is a *command* — power, colour, white,
effects, palettes, per-LED painting, speed, density, direction, scenes, clock
sync, refresh — is meaningful for every strip at once and is exposed here.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import replace
from typing import Any, Final

from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, MANUFACTURER
from .coordinator import SurplifeCoordinator, SurplifeRuntime, resolve_palette_edit
from .effect_profile import palette_for
from .protocol import ScribbleLED, SurplifeState
from .protocol.constants import EFFECT_NAME_TO_ID
from .settings import (
    EffectSettings,
    effect_settings,
    look_settings,
    merge_colours,
    replace_settings,
    with_effect,
)

_LOGGER = logging.getLogger(__name__)

#: Key in a group entry's ``data`` holding the member config-entry ids. Its
#: presence is what makes an entry a group: a controller entry always has a
#: host and never has this key, so the two can never be confused after an
#: upgrade.
GROUP_MEMBERS_KEY: Final = "members"

GROUP_MODEL: Final = "Surplife Permanent Lighting group"


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


def _expand(hass: HomeAssistant, entry_id: str) -> list[SurplifeCoordinator]:
    """The controllers behind one entry id, expanding a group if necessary."""
    container = hass.data.get(DOMAIN, {})
    coordinator = container.get(entry_id)
    if coordinator is None:
        return []
    if isinstance(coordinator, SurplifeGroupCoordinator):
        return coordinator.member_coordinators
    if isinstance(coordinator, SurplifeCoordinator):
        return [coordinator]
    return []


def coordinators_for_entities(
    hass: HomeAssistant, entity_ids: list[str]
) -> list[SurplifeCoordinator]:
    """Resolve Surplife light entity ids to their controllers.

    A member light maps to one coordinator through its ``surplife_entry_id``
    attribute. A group light maps to every member through
    ``surplife_group_members``, so one service call aimed at the group reaches
    all three strips — otherwise ``surplife.apply_preset`` would have to be
    called once per controller, which is the inconvenience the group exists to
    remove.

    A group entry id is expanded as well, defensively: nothing publishes one as
    ``surplife_entry_id`` today, but resolving it to the group coordinator would
    mean services reach the strips twice (once through the group, once through
    the member list) and send duplicate commands.

    Args:
        hass: The running instance.
        entity_ids: Light entity ids named by a service call.

    Returns:
        Controllers in request order, de-duplicated, so naming both a group and
        one of its members sends each command once.

    Raises:
        HomeAssistantError: if an entity is unknown, is not a Surplife light, or
            belongs to an entry that is not loaded.
    """
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
            coordinators = _expand(hass, entry_id)
            if not coordinators:
                raise HomeAssistantError(
                    f"{entity_id} includes a controller that is not loaded"
                )
            for coordinator in coordinators:
                if coordinator.entry.entry_id in seen:
                    continue
                seen.add(coordinator.entry.entry_id)
                result.append(coordinator)
    return result


class SurplifeGroupCoordinator(DataUpdateCoordinator[SurplifeRuntime]):
    """One control surface for several controllers.

    **Derived state, forwarded commands.** ``runtime`` is rebuilt from the
    members' runtimes whenever one of them changes, using the same rules the
    group light used to apply to entity states: on when *any* member is on,
    brightness the mean of the lit members, effect the most common among them,
    colour from the first lit member. Every ``async_set_*`` fans the request out
    to each member coordinator, so the optimistic colour bookkeeping and the
    per-controller write locks stay in exactly one place.

    **No polling.** The group has no socket and nothing of its own to read. It
    subscribes to the member coordinators instead, which already poll, so a
    group cannot go stale on its own or add a second set of queries.

    **Availability follows the members.** ``last_update_success`` is recomputed
    in :meth:`_derive`, so a group whose strips are all unreachable goes
    unavailable instead of claiming to be off.
    """

    entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the facade from the group's config entry."""
        self.entry = entry
        self.runtime = SurplifeRuntime()
        self._applied_options: dict[str, Any] | None = None
        self._member_unsubs: dict[str, Any] = {}
        self._unsub_entry_changed: Any = None
        self._unsub_registry: Any = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} group {entry.title}",
            update_interval=None,
        )

    # -- metadata ----------------------------------------------------------

    @property
    def device_name(self) -> str:
        """Friendly name for error messages."""
        return self.entry.title

    @property
    def scan_interval(self) -> int:
        """Seconds between polls.

        The group does not poll, but the entity platforms and the options
        listener read this attribute on every controller, so it answers with
        the same option rather than raising.
        """
        return int(self.entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL))

    @property
    def device_info(self) -> DeviceInfo:
        """Registry entry for the group's own device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"group:{self.entry.entry_id}")},
            name=self.entry.title,
            manufacturer=MANUFACTURER,
            model=GROUP_MODEL,
            model_id="group",
        )

    # -- membership --------------------------------------------------------

    @property
    def member_entry_ids(self) -> list[str]:
        """The member config-entry ids that still exist."""
        return live_members(self.hass, self.entry)

    @property
    def member_coordinators(self) -> list[SurplifeCoordinator]:
        """The member controllers, in the order the entry names them.

        A member that is not loaded yet simply has no coordinator in
        ``hass.data`` and is skipped; :meth:`_entry_changed` re-resolves the
        list when it appears, so a boot where the group loads before its
        controllers still ends up wired.
        """
        container = self.hass.data.get(DOMAIN, {})
        coordinators: list[SurplifeCoordinator] = []
        for entry_id in self.member_entry_ids:
            candidate = container.get(entry_id)
            if isinstance(candidate, SurplifeCoordinator):
                coordinators.append(candidate)
        return coordinators

    @property
    def member_entity_ids(self) -> list[str]:
        """The member light entity ids, resolved through the entity registry.

        Resolving by entry id rather than by name means renaming a controller —
        or its entity — cannot break the group.
        """
        registry = er.async_get(self.hass)
        entity_ids: list[str] = []
        for entry_id in self.member_entry_ids:
            entity_ids.extend(
                entity.entity_id
                for entity in er.async_entries_for_config_entry(registry, entry_id)
                if entity.domain == "light" and entity.disabled_by is None
            )
        return entity_ids

    @property
    def all_members_on(self) -> bool:
        """Whether every member is powered, for the group light's ``turn_on``."""
        members = self.member_coordinators
        return bool(members) and all(member.runtime.is_on for member in members)

    # -- wiring ------------------------------------------------------------

    async def async_setup(self) -> None:
        """Start following the members and the entity registry."""
        self._sync_members()
        self._unsub_registry = self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_updated
        )
        self._unsub_entry_changed = async_dispatcher_connect(
            self.hass, SIGNAL_CONFIG_ENTRY_CHANGED, self._entry_changed
        )
        self._derive()

    async def async_shutdown(self) -> None:
        """Stop following the members."""
        self._clear_member_subscriptions()
        if self._unsub_entry_changed is not None:
            self._unsub_entry_changed()
            self._unsub_entry_changed = None
        if self._unsub_registry is not None:
            self._unsub_registry()
            self._unsub_registry = None
        await super().async_shutdown()

    @callback
    def _clear_member_subscriptions(self) -> None:
        """Drop every member listener."""
        for unsubscribe in self._member_unsubs.values():
            unsubscribe()
        self._member_unsubs.clear()

    @callback
    def _sync_members(self) -> None:
        """Point the member listeners at the current set of coordinators."""
        self._clear_member_subscriptions()
        for coordinator in self.member_coordinators:
            self._member_unsubs[coordinator.entry.entry_id] = (
                coordinator.async_add_listener(self._member_updated)
            )

    @callback
    def _member_updated(self) -> None:
        """Re-derive from the members and publish."""
        self._derive()
        self.async_update_listeners()

    @callback
    def _registry_updated(self, event: Event) -> None:
        """Publish when a member light is renamed or re-created.

        The group's ``entity_id`` attribute lists member entity ids, and those
        follow a rename. Only light registry events matter, and they are rare,
        so the check stays cheap.
        """
        if str(event.data.get("entity_id", "")).startswith("light."):
            self.async_update_listeners()

    @callback
    def _entry_changed(self, change: Any, entry: ConfigEntry) -> None:
        """Pick up a member that has just loaded, unloaded or been removed."""
        if entry.domain != DOMAIN:
            return
        live = {coordinator.entry.entry_id for coordinator in self.member_coordinators}
        if live != set(self._member_unsubs):
            self._sync_members()
        self._derive()
        self.async_update_listeners()

    # -- derived state -----------------------------------------------------

    async def _async_update_data(self) -> SurplifeRuntime:
        """Return the derived runtime.

        The group has no interval, so this is only reached through an explicit
        refresh — which :meth:`async_request_refresh` overrides to ask the
        members to poll as well.
        """
        self._derive()
        return self.runtime

    @callback
    def _derive(self) -> None:
        """Rebuild ``runtime`` from the member runtimes."""
        members = self.member_coordinators
        runtimes = [member.runtime for member in members]
        lit = [runtime for runtime in runtimes if runtime.is_on]
        active = lit or runtimes

        self.last_update_success = (
            any(member.last_update_success for member in members) if members else False
        )

        derived = SurplifeRuntime()
        if active:
            effect = _most_common([runtime.effect_name for runtime in active])
            speed = _most_common([runtime.effect_speed for runtime in active])
            density = _most_common([runtime.effect_density for runtime in active])
            direction = _most_common([runtime.effect_direction for runtime in active])

            derived.effect_name = effect
            derived.effect_speed = 50 if speed is None else speed
            derived.effect_density = 50 if density is None else density
            derived.effect_direction = 1 if direction is None else direction
            derived.look = _most_common([runtime.look for runtime in active])
            derived.scene = _most_common([runtime.scene for runtime in active])
            derived.commanded_non_effect = all(
                runtime.commanded_non_effect for runtime in active
            )

            # Colour: the first lit member's mix. An average of two colours is
            # a colour neither strip is showing, which would make the wheel lie
            # about a state the user can see out the window.
            sample = active[0]
            derived.rgbw = sample.rgbw
            derived.rgb = sample.rgb
            derived.white_level = sample.white_level

            # The palette that belongs to the effect the group is reporting, so
            # a majority of strips showing Chase report Chase's own colours.
            matching = [
                runtime for runtime in active if runtime.effect_name == derived.effect_name
            ]
            if matching:
                derived.palette = list(matching[0].palette)

            if lit:
                levels = [runtime.brightness for runtime in lit]
                derived.brightness = round(sum(levels) / len(levels))
            else:
                derived.brightness = sample.brightness

            borrowed = _borrowed_state(active)
            if borrowed is not None:
                counts = [
                    runtime.state.led_count
                    for runtime in runtimes
                    if runtime.state is not None
                ]
                # ``preset`` and ``pattern`` are per-strip frame bytes. Report
                # the most common one so the Mode sensor and the light's
                # attributes describe what the roof is mostly doing; a single
                # straggler then cannot rename the group's mode.
                preset = _most_common(
                    [
                        runtime.state.preset
                        for runtime in active
                        if runtime.state is not None
                    ]
                )
                pattern = _most_common(
                    [
                        runtime.state.pattern
                        for runtime in active
                        if runtime.state is not None
                    ]
                )
                derived.state = replace(
                    borrowed,
                    power=bool(lit),
                    speed=derived.effect_speed,
                    preset=borrowed.preset if preset is None else preset,
                    pattern=borrowed.pattern if pattern is None else pattern,
                    led_count=min(counts) if counts else borrowed.led_count,
                )

        self.runtime = derived

    # -- options bookkeeping ----------------------------------------------

    def expect_no_reapply(self, options: dict[str, Any]) -> None:
        """Record that ``options`` has just been applied by hand.

        Same contract as :meth:`SurplifeCoordinator.expect_no_reapply`. The
        group's update listener does not re-apply anything, because the panel
        applies each change itself, but keeping the claim honest means a future
        listener cannot double-command the strips.
        """
        self._applied_options = dict(options)

    def consume_skip_reapply(self) -> bool:
        """Take the pending claim and report whether it covers this change."""
        expected = self._applied_options
        if expected is None:
            return False
        self._applied_options = None
        return dict(self.entry.options) == expected

    # -- command plumbing --------------------------------------------------

    async def _fan_out(self, method: str, *args: Any, **kwargs: Any) -> list[Any]:
        """Call one coordinator method on every member.

        A dead strip must not stop the roof from changing: the request is sent
        to every member that answers, failures are logged, and the error is
        only raised when *every* member failed. That is the same trade the
        light makes for a single strip being offline, applied to a set.
        """
        members = self.member_coordinators
        results: list[Any] = []
        failures: list[HomeAssistantError] = []
        for member in members:
            try:
                results.append(await getattr(member, method)(*args, **kwargs))
            except HomeAssistantError as err:
                failures.append(err)
                _LOGGER.warning(
                    "%s: %s failed on %s: %s",
                    self.entry.title,
                    method,
                    member.device_name,
                    err,
                )
        self._derive()
        self.async_update_listeners()
        if failures and len(failures) == len(members):
            raise failures[0]
        return results

    # -- power and colour --------------------------------------------------

    async def async_set_power(self, turn_on: bool) -> None:
        """Turn every member on or off."""
        await self._fan_out("async_set_power", turn_on)

    async def async_set_rgb(self, rgb: tuple[int, int, int], brightness: int) -> None:
        """Set a flat RGB colour on every member."""
        await self._fan_out("async_set_rgb", rgb, brightness)

    async def async_set_white(self, level: int) -> None:
        """Drive the white channel on every member."""
        await self._fan_out("async_set_white", level)

    async def async_set_rgbw(
        self, rgb: tuple[int, int, int], white: int, brightness: int
    ) -> None:
        """Set an RGBW mix on every member."""
        await self._fan_out("async_set_rgbw", rgb, white, brightness)

    async def async_set_brightness(self, brightness: int) -> None:
        """Change brightness on every member, keeping each one's channel."""
        await self._fan_out("async_set_brightness", brightness)

    # -- effects -----------------------------------------------------------

    async def async_recolour_active_effect(
        self, colours: list[tuple[int, ...]], *, index: int | None = None
    ) -> bool:
        """Recolour the running effect on every member, with one palette.

        The palette is resolved here, against the group's runtime, rather than
        letting each member substitute into its own. Two strips that were
        configured separately would otherwise end up showing different colours
        from one touch of the group's wheel. The resolved list is then saved to
        the *group's* options so the choice survives re-selecting the effect.

        Returns:
            True when an effect was showing and has been recoloured, False when
            there is nothing to recolour and the caller should set a flat
            colour instead.
        """
        name = self.runtime.effect_name
        if not name or self.runtime.commanded_non_effect:
            return False

        resolved = resolve_palette_edit(self.runtime, colours, index)
        await self.async_set_effect(name, colors=resolved)

        saved = effect_settings(self.entry.options, name) or EffectSettings(
            colors=tuple(self.runtime.palette),
            speed=self.runtime.effect_speed,
            density=self.runtime.effect_density,
            direction=self.runtime.effect_direction,
        )
        updated = with_effect(
            dict(self.entry.options),
            name,
            replace_settings(saved, colors=tuple(resolved)),
        )
        self.expect_no_reapply(updated)
        self.hass.config_entries.async_update_entry(self.entry, options=updated)
        return True

    async def async_set_effect(
        self,
        name: str,
        *,
        speed: int | None = None,
        density: int | None = None,
        direction: int | None = None,
        option: int = 0,
        colors: list[tuple[int, ...]] | None = None,
    ) -> None:
        """Apply one native effect to every member, with one shared palette.

        The group resolves speed, density, direction and the palette itself and
        passes them explicitly, so "Chase" means the same thing on all three
        strips. When the group has nothing saved for this effect it falls back
        to what the members are already showing, which is the group's derived
        runtime — not each member's own setting, which would reintroduce the
        difference the group exists to remove.
        """
        if name.lower() not in EFFECT_NAME_TO_ID:
            raise HomeAssistantError(f"{self.device_name}: unknown effect {name!r}")

        saved = effect_settings(self.entry.options, name)
        resolved_colours = (
            colors
            if colors is not None
            else list(saved.colors)
            if saved is not None and saved.colors
            else list(self.runtime.palette)
        )
        resolved_speed = speed if speed is not None else (saved.speed if saved else None)
        resolved_density = (
            density if density is not None else (saved.density if saved else None)
        )
        resolved_direction = (
            direction if direction is not None else (saved.direction if saved else None)
        )

        await self._fan_out(
            "async_set_effect",
            name,
            speed=(
                self.runtime.effect_speed if resolved_speed is None else resolved_speed
            ),
            density=(
                self.runtime.effect_density
                if resolved_density is None
                else resolved_density
            ),
            direction=(
                self.runtime.effect_direction
                if resolved_direction is None
                else resolved_direction
            ),
            option=option,
            colors=list(palette_for(name, resolved_colours)),
        )

    # -- segments and per-LED ---------------------------------------------

    async def async_set_segments(self, colors: list[tuple[int, ...] | None]) -> None:
        """Apply the same static segments to every member."""
        await self._fan_out("async_set_segments", colors)

    async def async_set_scribble(
        self,
        leds: list[ScribbleLED],
        *,
        effect: int = 0,
        direction: int = 1,
        density: int = 80,
        speed: int = 100,
        enter: bool = True,
        look: str | None = None,
    ) -> int:
        """Render a per-LED configuration on every member.

        The LED list is truncated to each member's own LED count. Strips of
        different lengths would otherwise make the paint fail outright, and
        truncating keeps the painted pattern aligned from the start of the run.
        Controllers of the same model have the same count, so this is a guard
        rather than a compromise.

        Returns:
            The largest number of paints any one member needed.
        """
        paints = 0
        failures: list[HomeAssistantError] = []
        members = self.member_coordinators
        for member in members:
            count = member.runtime.led_count
            try:
                paints = max(
                    paints,
                    await member.async_set_scribble(
                        list(leds)[:count],
                        effect=effect,
                        direction=direction,
                        density=density,
                        speed=speed,
                        enter=enter,
                        look=look,
                    ),
                )
            except HomeAssistantError as err:
                failures.append(err)
                _LOGGER.warning(
                    "%s: per-LED paint failed on %s: %s",
                    self.entry.title,
                    member.device_name,
                    err,
                )
        self._derive()
        self.async_update_listeners()
        if failures and len(failures) == len(members):
            raise failures[0]
        return paints

    async def async_set_look(
        self,
        name: str,
        *,
        palette: list[tuple[int, ...]] | None = None,
        direction: int | None = None,
        density: int | None = None,
        speed: int | None = None,
        effect: int | None = None,
    ) -> int:
        """Render a built-in per-LED look on every member.

        Each member builds the look with its own LED count, so the pattern is
        sized correctly per strip rather than scaled from one controller.

        The palette is resolved once from the *group's* saved settings, then
        passed explicitly. Letting each member resolve its own would give three
        differently-coloured "stripes", which is not what one group control
        means.
        """
        from .scribble_looks import LOOKS

        saved = look_settings(self.entry.options, name)
        if palette is None and saved is not None and saved.palette:
            defaults = LOOKS[name].palette if name in LOOKS else ()
            resolved_palette = list(
                merge_colours(tuple(defaults), {"palette": saved.palette})
            )
        else:
            resolved_palette = palette

        results = await self._fan_out(
            "async_set_look",
            name,
            palette=resolved_palette,
            direction=(
                direction
                if direction is not None
                else (saved.direction if saved else None)
            ),
            density=(
                density if density is not None else (saved.density if saved else None)
            ),
            speed=speed if speed is not None else (saved.speed if saved else None),
            effect=effect if effect is not None else (saved.effect if saved else None),
        )
        return max(results, default=0)

    # -- scenes ------------------------------------------------------------

    async def async_mark_scene(self, name: str) -> None:
        """Record which scene is showing on every member."""
        await self._fan_out("async_mark_scene", name)

    async def async_set_scene(self, scene: Any) -> None:
        """Apply a scene to every member.

        ``scene.async_apply`` calls back into this coordinator's own
        ``async_set_*`` methods, which fan out — so the scene is resolved once
        and reaches the strips through the same path a manual command does.
        """
        await scene.async_apply(self)

    # -- clock and polling -------------------------------------------------

    async def async_sync_clock(self) -> Any:
        """Push Home Assistant's time to every member."""
        results = await self._fan_out("async_sync_clock")
        return next((result for result in results if result is not None), None)

    async def async_request_refresh(self) -> None:
        """Ask every member to re-read its state, then re-derive."""
        for member in self.member_coordinators:
            await member.async_request_refresh()
        self._derive()
        self.async_update_listeners()


def _most_common(values: list[Any]) -> Any:
    """The most frequent value, ties going to the first seen.

    ``None`` is a value, not a gap: two strips on a flat colour and one in a
    pattern should report the flat colour, so a single straggler cannot rename
    the group's effect.
    """
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def _borrowed_state(runtimes: list[SurplifeRuntime]) -> SurplifeState | None:
    """The first state frame among ``runtimes``, if any.

    The group has no frame of its own. It borrows the first lit member's frame
    and patches the fields it derives, which keeps ``runtime.state.speed`` and
    ``runtime.led_count`` meaningful for the entities that read them.
    """
    for runtime in runtimes:
        if runtime.state is not None:
            return runtime.state
    return None


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
    "SurplifeGroupCoordinator",
    "controller_entries",
    "coordinators_for_entities",
    "default_group_name",
    "group_entries",
    "is_group_entry",
    "live_members",
]
