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
    CONF_LED_COUNT,
    CONF_MAC,
    CONF_MODEL_INFO,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .protocol import (
    ScribbleLED,
    SurplifeClient,
    SurplifeConnectionError,
    SurplifeError,
    SurplifeState,
    Timer,
)

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
        white_level: Last warm-white level we commanded, 0-255.
        effect_name: Name of the active effect, if any.
        timers: Cached hardware timers.
        scene: Name of the last scene applied, tracked optimistically.
        look: Name of the last per-LED look applied, tracked optimistically.
    """

    state: SurplifeState | None = None
    rgb: tuple[int, int, int] | None = None
    brightness: int = 255
    white_level: int = 0
    effect_name: str | None = None
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
        """``white`` when in warm-white mode, else ``rgb``."""
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
        self.runtime = SurplifeRuntime()

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {self.host}",
            update_interval=dt.timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

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
        """Set an RGB colour at a Home Assistant brightness (0-255)."""
        scaled = _scale(rgb, brightness)
        await self._run(self._client.set_color, *scaled)
        self.runtime.rgb = scaled
        self.runtime.brightness = brightness
        self.runtime.white_level = 0
        self.runtime.effect_name = None
        self._clear_recipe()
        self.async_update_listeners()

    async def async_set_white(self, level: int) -> None:
        """Set warm-white brightness, 0-255."""
        percent = round(level * 100 / 255)
        await self._run(self._client.set_warm_white, percent)
        self.runtime.rgb = None
        self.runtime.white_level = level
        self.runtime.brightness = level
        self.runtime.effect_name = None
        self._clear_recipe()
        self.async_update_listeners()

    async def async_set_brightness(self, brightness: int) -> None:
        """Change brightness without changing the colour."""
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
        speed: int = 50,
        density: int = 50,
        direction: int = 1,
        option: int = 0,
        colors: list[tuple[int, int, int]] | None = None,
    ) -> None:
        """Apply a native ``E1 21`` effect by name."""
        from .protocol.constants import EFFECT_NAME_TO_ID

        effect_id = EFFECT_NAME_TO_ID.get(name.lower())
        if effect_id is None:
            raise HomeAssistantError(f"{self.device_name}: unknown effect {name!r}")
        await self._run(
            self._client.set_effect,
            effect_id,
            colors or [self.runtime.rgb or (255, 255, 255)],
            speed=speed,
            density=density,
            direction=direction,
            option=option,
            brightness=round(self.runtime.brightness * 100 / 255),
        )
        self.runtime.effect_name = name
        self.runtime.look = None
        if self.runtime.state is not None:
            self.runtime.state = _replace_pattern(self.runtime.state, effect_id)
        self.async_update_listeners()

    # -- segments ----------------------------------------------------------

    async def async_set_segments(
        self, colors: list[tuple[int, int, int] | None]
    ) -> None:
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
        palette: list[tuple[int, int, int]] | None = None,
        direction: int = 1,
        density: int = 80,
        speed: int = 100,
        effect: int = 0,
    ) -> int:
        """Render one of the built-in named per-LED looks."""
        from .scribble_looks import build_look

        leds = build_look(name, self.runtime.led_count, palette)
        return await self.async_set_scribble(
            leds,
            effect=effect,
            direction=direction,
            density=density,
            speed=speed,
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
