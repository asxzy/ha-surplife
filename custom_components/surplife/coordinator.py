"""Async wrapper around the synchronous protocol client.

The protocol layer is deliberately synchronous and stdlib-only so it can be
tested and scripted without Home Assistant. This module adapts it to HA's
event loop by running every call in a worker thread, and adds the
optimistic-state bookkeeping the firmware forces on us.

**Why optimistic state is necessary.** The device does not echo the colour or
brightness set by a plain ``E0 01`` command: bytes 11-13 of the state frame
keep whatever the last ``E1 21`` effect left there. Verified by sending six
different colours in a row and reading back an identical frame each time. So
the entity keeps its own colour and brightness and only trusts the device for
power, mode, effect id and speed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_FIRMWARE_INFO,
    CONF_HW_VERSION,
    CONF_MAC,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .effect_profile import palette_for
from .protocol import (
    ScribbleLED,
    SurplifeClient,
    SurplifeConnectionError,
    SurplifeError,
    SurplifeState,
    Timer,
)
from .protocol.constants import WRITE_ALL
from .settings import effect_settings, look_settings, merge_colours

_LOGGER = logging.getLogger(__name__)

#: Consecutive failed polls tolerated before entities go unavailable. The
#: controllers pause for ~2.6s after a multi-paint render, which can span a
#: whole poll at the default interval.
_MAX_POLL_FAILURES: Final = 3


@dataclass
class SurplifeRuntime:
    """Optimistic and cached state for one controller.

    Attributes:
        state: The last state frame read from the device.
        rgb: Last RGB colour we commanded, or ``None`` when in white mode.
        brightness: Last brightness we commanded, 0-255.
        white_level: Last W-channel level we commanded, 0-255.
        rgbw: Last RGBW mix we commanded, or ``None`` when the strip is driven
            by colour alone or white alone. When set, ``rgb`` is the colour
            channels and ``white_level`` the W channel, together.
        effect_name: Name of the active effect, if any.
        palette: The colours last commanded to an effect, as RGBW tuples.
        effect_speed: Effect speed last commanded, 0-100.
        effect_density: Effect density last commanded, 0-100.
        effect_direction: ``0x01`` left-to-right or ``0x02`` right-to-left.
        timers: Cached hardware timers.
        scene: Name of the last scene applied, tracked optimistically.
        look: Name of the last per-LED look applied, tracked optimistically.
    """

    state: SurplifeState | None = None
    rgb: tuple[int, int, int] | None = None
    brightness: int = 255
    white_level: int = 0
    rgbw: tuple[int, int, int, int] | None = None
    effect_name: str | None = None
    palette: list[tuple[int, ...]] = field(default_factory=lambda: [(255, 255, 255)])
    effect_speed: int = 50
    effect_density: int = 50
    effect_direction: int = 1
    timers: list[Timer] = field(default_factory=list)
    scene: str | None = None
    look: str | None = None

    @property
    def is_on(self) -> bool:
        """Whether the controller reports itself on."""
        return bool(self.state and self.state.power)

    @property
    def led_count(self) -> int:
        """Configured LED count, defaulting to 35 if never read."""
        return self.state.led_count if self.state else 35

    @property
    def color_mode(self) -> str:
        """``rgbw`` for a mix, ``white`` for the white channel alone, else ``rgb``."""
        if self.rgbw is not None:
            return "rgbw"
        return "white" if self.white_level and not self.rgb else "rgb"


class SurplifeCoordinator(DataUpdateCoordinator[SurplifeRuntime]):
    """Polls one controller and owns its connection."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator from a config entry."""
        self.entry = entry
        self.host: str = entry.data[CONF_HOST]
        self._client = SurplifeClient(self.host, DEFAULT_PORT, timeout=DEFAULT_TIMEOUT)
        self._write_lock = asyncio.Lock()
        self._failures = 0
        self._skip_reapply = False
        self.runtime = SurplifeRuntime()

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {self.host}",
            update_interval=dt.timedelta(seconds=self.scan_interval),
        )

    @property
    def scan_interval(self) -> int:
        """Seconds between polls, from the options or the default.

        The Configure dialog exposes this, so it has to be read from the options
        rather than hard-coded -- otherwise the field saves a value that nothing
        ever uses.
        """
        return int(self.entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL))

    # -- device metadata ---------------------------------------------------

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry for this controller."""
        data = self.entry.data
        return DeviceInfo(
            identifiers={(DOMAIN, data.get(CONF_MAC) or self.host)},
            name=self.entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
            model_id="0xB6",
            sw_version=data.get(CONF_FIRMWARE_INFO),
            hw_version=data.get(CONF_HW_VERSION),
            configuration_url=f"http://{self.host}",
        )

    @property
    def device_name(self) -> str:
        """Friendly name for entities."""
        return self.entry.data.get(CONF_NAME) or self.entry.title

    def skip_next_reapply(self) -> None:
        """Skip the options listener's re-apply for the next save.

        The sliders and the direction selector save to the options *and* apply
        the effect themselves. Without this their save would also trigger the
        update listener, sending the same effect a second time — a visible
        double flash on a strip that is already showing it.

        Only a flag is set here; the listener consumes and clears it.
        """
        self._skip_reapply = True

    def consume_skip_reapply(self) -> bool:
        """Take and clear the skip flag set by :meth:`skip_next_reapply`."""
        skip = self._skip_reapply
        self._skip_reapply = False
        return skip

    async def async_load_timers(self) -> None:
        """Read the hardware timers once, after the first state read.

        Timers are only needed for the diagnostic sensor, and reading them is a
        second round trip. Doing it once at setup (and again whenever a timer is
        written) keeps the poll loop to a single query, which matters because
        these controllers stop answering for seconds when pushed.
        """
        try:
            await self.async_get_timers()
        except (HomeAssistantError, SurplifeError) as err:
            # A diagnostic sensor is not worth failing setup over.
            _LOGGER.debug("could not read timers from %s: %s", self.host, err)

    # -- polling -----------------------------------------------------------

    async def _async_update_data(self) -> SurplifeRuntime:
        """Read the state frame and merge it into the runtime state.

        A single missed poll is tolerated: the controllers stop answering for
        a couple of seconds after a multi-paint scribble render, and dropping
        every entity to ``unavailable`` for that would be wrong. Only a run of
        consecutive failures is reported as an update failure.
        """
        try:
            state = await asyncio.to_thread(self._client.get_state)
        except (SurplifeConnectionError, SurplifeError) as err:
            self._failures += 1
            if self._failures < _MAX_POLL_FAILURES:
                _LOGGER.debug(
                    "Transient poll failure %d/%d for %s: %s",
                    self._failures,
                    _MAX_POLL_FAILURES,
                    self.host,
                    err,
                )
                # Keep the last known runtime; the entity stays available.
                return self.runtime
            raise UpdateFailed(f"cannot reach {self.host}: {err}") from err

        self._failures = 0
        self.runtime.state = state
        # The device always populates the pattern byte, and reuses it for
        # non-effect modes, so only trust it when it is a known effect.
        self.runtime.effect_name = state.effect_name
        return self.runtime

    async def async_shutdown(self) -> None:
        """Close the socket."""
        await asyncio.to_thread(self._client.close)

    # -- command plumbing --------------------------------------------------

    async def _run(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking client call, translating errors for HA."""
        async with self._write_lock:
            try:
                return await asyncio.to_thread(func, *args, **kwargs)
            except SurplifeConnectionError as err:
                raise HomeAssistantError(
                    f"{self.device_name} is unreachable: {err}"
                ) from err
            except (SurplifeError, ValueError) as err:
                raise HomeAssistantError(f"{self.device_name}: {err}") from err

    async def _run_and_refresh(self, func: Any, *args: Any, **kwargs: Any) -> None:
        """Run a command, then re-read the state frame."""
        await self._run(func, *args, **kwargs)
        await self.async_request_refresh()

    # -- power and colour --------------------------------------------------

    async def async_set_power(self, turn_on: bool) -> None:
        """Turn the strip on or off."""
        await self._run(self._client.set_power, turn_on)
        if self.runtime.state is not None:
            self.runtime.state = _replace_power(self.runtime.state, turn_on)
        self.async_update_listeners()

    async def async_set_rgb(self, rgb: tuple[int, int, int], brightness: int) -> None:
        """Set an RGB colour at a Home Assistant brightness (0-255).

        The W channel is explicitly written to zero: ``light.turn_on`` with a
        colour means "show this colour", so leaving white lit from a previous
        white request would tint the result.
        """
        scaled = _scale(rgb, brightness)
        await self._run(
            self._client.set_levels,
            red=scaled[0],
            green=scaled[1],
            blue=scaled[2],
            white=0,
            write_mode=WRITE_ALL,
        )
        self.runtime.rgb = scaled
        self.runtime.rgbw = None
        self.runtime.brightness = brightness
        self.runtime.white_level = 0
        self.runtime.effect_name = None
        self._clear_recipe()
        self.async_update_listeners()

    async def async_set_white(self, level: int) -> None:
        """Set white-channel brightness, 0-255.

        Only the W channel is written (``WRITE_WHITE``), so the colour channels
        keep whatever they had. This is the path ``light.turn_on`` takes for
        ``white:`` — the white LED, not a simulated RGB white.
        """
        percent = round(level * 100 / 255)
        await self._run(self._client.set_warm_white, percent)
        self.runtime.rgb = None
        self.runtime.rgbw = None
        self.runtime.white_level = level
        self.runtime.brightness = level
        self.runtime.effect_name = None
        self._clear_recipe()
        self.async_update_listeners()

    async def async_set_rgbw(
        self, rgb: tuple[int, int, int], white: int, brightness: int
    ) -> None:
        """Set an RGBW mix: colour channels plus the W channel, together.

        Args:
            rgb: Colour channels, 0-255.
            white: W channel level, 0-255, as Home Assistant reports it.
            brightness: Overall brightness, 0-255. Scales ``rgb`` the same way
                :meth:`async_set_rgb` does; ``white`` is already a level.

        A pure white request arrives as ``rgb=(0, 0, 0)`` with ``white`` set.
        That is exactly the case this method exists for: the strip lights its
        white LED and no colour, instead of three colour LEDs imitating white.
        """
        scaled = _scale(rgb, brightness)
        percent = round(white * 100 / 255)
        await self._run(
            self._client.set_levels,
            red=scaled[0],
            green=scaled[1],
            blue=scaled[2],
            white=percent,
            write_mode=WRITE_ALL,
        )
        self.runtime.rgb = scaled if any(scaled) else None
        self.runtime.white_level = white
        self.runtime.brightness = max(brightness, white)
        self.runtime.rgbw = (scaled[0], scaled[1], scaled[2], white)
        self.runtime.effect_name = None
        self._clear_recipe()
        self.async_update_listeners()

    async def async_set_brightness(self, brightness: int) -> None:
        """Change brightness without changing the colour."""
        if self.runtime.rgbw is not None:
            red, green, blue, white = self.runtime.rgbw
            await self.async_set_rgbw((red, green, blue), white, brightness)
            return
        if self.runtime.white_level and self.runtime.rgb is None:
            await self.async_set_white(brightness)
            return
        rgb = self.runtime.rgb or (255, 255, 255)
        await self.async_set_rgb(rgb, brightness)

    # -- effects -----------------------------------------------------------

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
        """Apply a native ``E1 21`` effect by name.

        Every parameter is optional and resolves in one of three ways, in this
        order: the value passed by the caller, the value saved for *this* effect
        in the Configure panel, then the runtime's last commanded value. That
        ordering is what makes the panel feel like remembered settings: picking
        ``Wave`` from the light card restores what Wave was configured with,
        while an automation that passes ``speed=`` still wins for that one call.
        """
        from .protocol.constants import EFFECT_NAME_TO_ID

        effect_id = EFFECT_NAME_TO_ID.get(name.lower())
        if effect_id is None:
            raise HomeAssistantError(f"{self.device_name}: unknown effect {name!r}")

        saved = effect_settings(self.entry.options, name)
        palette = palette_for(
            name,
            colors
            if colors is not None
            else list(saved.colors)
            if saved is not None and saved.colors
            else self.runtime.palette,
        )
        resolved_speed = (
            speed if speed is not None else (saved.speed if saved else None)
        )
        resolved_density = (
            density if density is not None else (saved.density if saved else None)
        )
        resolved_direction = (
            direction if direction is not None else (saved.direction if saved else None)
        )
        await self._run(
            self._client.set_effect,
            effect_id,
            palette,
            speed=self.runtime.effect_speed
            if resolved_speed is None
            else resolved_speed,
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
            brightness=round(self.runtime.brightness * 100 / 255),
        )
        self.runtime.effect_name = name
        self.runtime.palette = palette
        self.runtime.effect_speed = (
            self.runtime.effect_speed if resolved_speed is None else resolved_speed
        )
        self.runtime.effect_density = (
            self.runtime.effect_density
            if resolved_density is None
            else resolved_density
        )
        self.runtime.effect_direction = (
            self.runtime.effect_direction
            if resolved_direction is None
            else resolved_direction
        )
        self.runtime.look = None
        if self.runtime.state is not None:
            self.runtime.state = _replace_pattern(self.runtime.state, effect_id)
        self.async_update_listeners()

    # -- segments ----------------------------------------------------------

    async def async_set_segments(self, colors: list[tuple[int, ...] | None]) -> None:
        """Apply static segment colours."""
        await self._run(self._client.set_segments, colors)
        self.runtime.effect_name = None
        self._clear_recipe()
        self.async_update_listeners()

    # -- per-LED -----------------------------------------------------------

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
        """Render a full per-LED configuration.

        ``look`` records which built-in look produced ``leds``, purely so the
        select entity can report it; it does not change the wire payload.
        """
        paints = await self._run(
            self._client.set_scribble,
            leds,
            enter=enter,
            effect_id=effect,
            direction=direction,
            density=density,
            speed=speed,
        )
        self.runtime.effect_name = None
        self.runtime.scene = None
        self.runtime.look = look
        self.async_update_listeners()
        return int(paints)

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
        """Render one of the built-in named per-LED looks.

        A look remembers its own palette and animation parameters, so picking
        it from the look select restores what was configured for it rather than
        snapping back to the look's built-in defaults. Anything passed
        explicitly wins for that call, which is what scenes and the service use.
        """
        from .scribble_looks import LOOKS, build_look

        saved = look_settings(self.entry.options, name)
        if palette is None and saved is not None and saved.palette:
            defaults = LOOKS[name].palette if name in LOOKS else ()
            resolved_palette = list(
                merge_colours(tuple(defaults), {"palette": saved.palette})
            )
        else:
            resolved_palette = palette

        leds = build_look(name, self.runtime.led_count, resolved_palette)
        return await self.async_set_scribble(
            leds,
            effect=effect if effect is not None else (saved.effect if saved else 0),
            direction=(
                direction
                if direction is not None
                else (saved.direction if saved else 1)
            ),
            density=(
                density if density is not None else (saved.density if saved else 80)
            ),
            speed=speed if speed is not None else (saved.speed if saved else 100),
            look=name,
        )

    # -- scenes ------------------------------------------------------------

    async def async_mark_scene(self, name: str) -> None:
        """Record which scene is currently showing, without any I/O."""
        self.runtime.scene = name
        self.async_update_listeners()

    async def async_set_scene(self, scene: Any) -> None:
        """Apply a :class:`~.scenes.Scene` to this controller."""
        await scene.async_apply(self)

    # -- clock and timers --------------------------------------------------

    async def async_sync_clock(self) -> dt.datetime | None:
        """Push Home Assistant's local time to the controller."""
        return await self._run(self._client.sync_clock)

    async def async_get_timers(self) -> list[Timer]:
        """Read the hardware timer slots."""
        timers = await self._run(self._client.get_timers)
        self.runtime.timers = timers
        self.async_update_listeners()
        return timers

    async def async_set_timer(self, **kwargs: Any) -> None:
        """Write one hardware timer slot."""
        await self._run(self._client.set_timer, **kwargs)
        await self.async_get_timers()

    async def async_clear_timer(self, slot: int) -> None:
        """Disable a hardware timer slot."""
        await self._run(self._client.clear_timer, slot)
        await self.async_get_timers()

    def _clear_recipe(self) -> None:
        """Forget the scene/look bookkeeping after a direct colour command."""
        self.runtime.scene = None
        self.runtime.look = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scale(rgb: tuple[int, int, int], brightness: int) -> tuple[int, int, int]:
    """Scale an RGB triple by a 0-255 brightness."""
    if brightness >= 255:
        return rgb
    factor = max(brightness, 0) / 255
    return tuple(round(channel * factor) for channel in rgb)  # type: ignore[return-value]


def _replace_power(state: SurplifeState, power: bool) -> SurplifeState:
    """Return a copy of a state frame with the power flag changed."""
    from dataclasses import replace

    return replace(state, power=power)


def _replace_pattern(state: SurplifeState, pattern: int) -> SurplifeState:
    """Return a copy of a state frame with the pattern byte changed."""
    from dataclasses import replace

    return replace(state, pattern=pattern)
