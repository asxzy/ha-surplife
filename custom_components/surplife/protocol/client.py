"""TCP client for a 0xB6 controller.

The controllers are small embedded devices with a fragile TCP stack: they
occasionally drop a connection, ignore a first command on a fresh socket, and
stop answering if several sockets are opened in quick succession. This client
therefore:

* keeps **one** long-lived connection and serialises all writes through a lock
* retries once on a fresh socket when a write or read fails
* paces requests, and never opens more than one connection at a time

**Verified**: a single persistent connection survives many commands, and the
device tolerates a second concurrent connection (so this integration can run
alongside the stock ``flux_led`` entry during migration).

Every method is synchronous. Home Assistant wraps calls in
:func:`asyncio.to_thread`; see ``custom_components/surplife/client.py``.
"""

from __future__ import annotations

import datetime as dt
import logging
import socket
import threading
import time
from typing import Final

from . import commands
from .constants import DEFAULT_PORT
from .framing import (
    VERSION_EXTENDED,
    VERSION_LEGACY,
    Frame,
    FrameReader,
    wrap,
)
from .scribble import PaintGroup, ScribbleLED, group_leds, normalize
from .state import SurplifeState, build_state_query, parse_state
from .timers import Timer, parse_time, parse_timers

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT: Final = 6.0
DEFAULT_READ_TIMEOUT: Final = 2.0
#: Delay before retrying a failed exchange, multiplied by the attempt number.
#: The backoff matters: after a multi-paint scribble render the controller stops
#: answering for up to ~2.6s (measured), so a fixed short retry fires while it is
#: still busy and burns the whole attempt budget.
RECONNECT_DELAY: Final = 0.5
MAX_ATTEMPTS: Final = 3
#: Minimum spacing between writes. The controllers dislike bursts.
MIN_WRITE_INTERVAL: Final = 0.05


class SurplifeError(Exception):
    """Base error for the Surplife protocol layer."""


class SurplifeConnectionError(SurplifeError):
    """Raised when the controller cannot be reached or stops responding."""


class SurplifeClient:
    """Synchronous client for one 0xB6 controller.

    Args:
        host: Controller IP address.
        port: TCP port, 5577 by default.
        timeout: Socket connect and write timeout.
        read_timeout: How long to wait for a reply.

    The client is safe to share between threads; all I/O is serialised.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.read_timeout = read_timeout
        self._sock: socket.socket | None = None
        self._reader = FrameReader()
        self._counter = 0
        self._lock = threading.RLock()
        self._last_write = 0.0

    # -- connection management ---------------------------------------------

    @property
    def connected(self) -> bool:
        """Whether a socket is currently open."""
        return self._sock is not None

    def connect(self) -> None:
        """Open the connection if it is not already open."""
        with self._lock:
            if self._sock is not None:
                return
            try:
                sock = socket.create_connection(
                    (self.host, self.port), timeout=self.timeout
                )
            except OSError as err:
                raise SurplifeConnectionError(
                    f"cannot connect to {self.host}:{self.port}: {err}"
                ) from err
            sock.settimeout(self.read_timeout)
            self._sock = sock
            self._reader = FrameReader()
            _LOGGER.debug("connected to %s:%s", self.host, self.port)

    def close(self) -> None:
        """Close the connection, ignoring errors."""
        with self._lock:
            sock, self._sock = self._sock, None
            self._reader = FrameReader()
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
            _LOGGER.debug("disconnected from %s:%s", self.host, self.port)

    def _disconnect(self) -> None:
        """Drop a broken connection without raising."""
        sock, self._sock = self._sock, None
        self._reader = FrameReader()
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _next_counter(self) -> int:
        counter = self._counter
        self._counter = (self._counter + 1) & 0xFF
        return counter

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_write
        if elapsed < MIN_WRITE_INTERVAL:
            time.sleep(MIN_WRITE_INTERVAL - elapsed)
        self._last_write = time.monotonic()

    def _send(self, inner: bytes, version: int) -> None:
        """Write one frame on the current connection."""
        if self._sock is None:
            raise SurplifeConnectionError("not connected")
        frame = wrap(inner, version, self._next_counter())
        self._pace()
        self._sock.sendall(frame)

    def _drain_input(self) -> None:
        """Discard bytes the device has already sent that we never read.

        Write commands are sent without waiting for a reply, but the device
        still answers some of them (paints especially) — sometimes seconds
        later. Those acknowledgements otherwise sit in the receive buffer and
        get mistaken for the reply to the *next* query, which surfaces as
        "not a state frame".
        """
        if self._sock is None:
            return
        self._sock.setblocking(False)
        dropped = 0
        try:
            while True:
                data = self._sock.recv(4096)
                if not data:
                    break
                dropped += len(data)
        except (BlockingIOError, TimeoutError):
            pass
        except OSError:
            pass
        finally:
            if self._sock is not None:
                self._sock.settimeout(self.read_timeout)
            # Anything that was already parsed out of an earlier read is stale
            # for the same reason.
            self._reader = FrameReader()
        if dropped:
            _LOGGER.debug("dropped %d stale byte(s) from %s", dropped, self.host)

    def _read_frame(self, timeout: float) -> Frame | None:
        """Read one frame, or return ``None`` on timeout."""
        if self._sock is None:
            raise SurplifeConnectionError("not connected")
        deadline = time.monotonic() + timeout
        while True:
            if (frame := self._reader.pop()) is not None:
                return Frame.parse(frame)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._sock.settimeout(remaining)
            try:
                data = self._sock.recv(4096)
            except TimeoutError:
                return None
            except OSError as err:
                raise SurplifeConnectionError(f"read failed: {err}") from err
            if not data:
                raise SurplifeConnectionError("connection closed by device")
            self._reader.feed(data)

    def _exchange(
        self,
        inner: bytes,
        version: int = VERSION_EXTENDED,
        *,
        expect_reply: bool = False,
        read_timeout: float | None = None,
    ) -> Frame | None:
        """Send a command and optionally read one reply, retrying with backoff.

        A failed attempt closes the socket so the retry starts clean. This is
        what makes the client survive two distinct device behaviours:

        * a freshly opened socket silently drops its first command, and
        * after a multi-paint scribble render the controller stops answering
          for up to ~2.6s (measured on hardware).

        Both are transient, so the retry delay grows with each attempt rather
        than staying fixed — a fixed short delay lands while the device is
        still busy and exhausts the budget.
        """
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                self.connect()
                self._send(inner, version)
                if not expect_reply:
                    # Writes are fire-and-forget, but the controller still
                    # acknowledges some of them (paints, seconds later). Clear
                    # the socket so that ack cannot be read as the reply to a
                    # later query.
                    self._drain_input()
                    return None
                frame = self._read_frame(
                    read_timeout if read_timeout is not None else self.read_timeout
                )
                if frame is not None:
                    return frame
                raise SurplifeConnectionError("no reply from device")
            except SurplifeConnectionError as err:
                last_error = err
                self._disconnect()
                if attempt + 1 < MAX_ATTEMPTS:
                    time.sleep(RECONNECT_DELAY * (attempt + 1))
            except OSError as err:
                last_error = SurplifeConnectionError(f"send failed: {err}")
                self._disconnect()
                if attempt + 1 < MAX_ATTEMPTS:
                    time.sleep(RECONNECT_DELAY * (attempt + 1))
        raise last_error or SurplifeConnectionError("command failed")

    # -- state -------------------------------------------------------------

    def get_state(self) -> SurplifeState:
        """Query and parse the device state frame.

        More than one reply can be waiting — the device acks some earlier
        write commands late — so every reply is inspected until one looks like
        a state frame. Draining first, then skipping, means a stale
        acknowledgement cannot hide the real answer.

        Raises:
            SurplifeConnectionError: if the device does not answer.
            SurplifeError: if no state frame arrives.
        """
        with self._lock:
            self.connect()
            self._drain_input()
            frame = self._exchange(
                build_state_query(), VERSION_EXTENDED, expect_reply=True
            )
            last_error: ValueError | None = None
            while frame is not None:
                try:
                    return parse_state(frame.inner)
                except ValueError as err:
                    # Not a state frame: an acknowledgement that arrived late.
                    last_error = err
                frame = self._read_frame(self.read_timeout)
        if last_error is not None:
            raise SurplifeError(f"unparseable state reply: {last_error}")
        raise SurplifeConnectionError("no state reply")

    # -- power and colour --------------------------------------------------

    def set_power(self, turn_on: bool) -> None:
        """Turn the strip on or off."""
        with self._lock:
            self._exchange(commands.power(turn_on))

    def set_color(self, red: int, green: int, blue: int) -> None:
        """Set a whole-strip RGB colour."""
        with self._lock:
            self._exchange(commands.color(red, green, blue))

    def set_warm_white(self, level: int) -> None:
        """Set warm-white brightness, 0-100."""
        with self._lock:
            self._exchange(commands.warm_white(level))

    def set_levels(
        self,
        *,
        red: int = 0,
        green: int = 0,
        blue: int = 0,
        white: int = 0,
        write_mode: int | None = None,
    ) -> None:
        """Set colour and/or the W channel in one ``E0 01`` command.

        Args:
            red, green, blue: RGB channels, 0-255.
            white: W channel level, 0-100.
            write_mode: Explicit channel selector. ``WRITE_ALL`` writes both,
                so the W channel can be lit while the colour channels are
                dark — a true white rather than a simulated RGB white.

        **Verified**: the firmware accepts the combined frame; it does not
        echo any of it back, so the coordinator tracks it optimistically.
        """
        with self._lock:
            self._exchange(
                commands.levels(
                    red=red,
                    green=green,
                    blue=blue,
                    white=white,
                    write_mode=write_mode,
                )
            )

    # -- effects -----------------------------------------------------------

    def set_effect(
        self,
        effect_id: int,
        colors: list[tuple[int, ...]] | None = None,
        *,
        speed: int = 50,
        density: int = 50,
        direction: int = 1,
        option: int = 0,
        brightness: int = 100,
    ) -> None:
        """Apply a whole-strip ``E1 21`` animated effect."""
        with self._lock:
            self._exchange(
                commands.effect(
                    effect_id,
                    colors,
                    speed=speed,
                    density=density,
                    direction=direction,
                    option=option,
                    brightness=brightness,
                )
            )

    def set_segments(self, colors: list[tuple[int, ...] | None]) -> None:
        """Apply up to 20 static segment colours."""
        with self._lock:
            self._exchange(commands.segments(colors))

    # -- per-LED -----------------------------------------------------------

    def set_scribble(
        self,
        leds: list[ScribbleLED],
        *,
        enter: bool = True,
        effect_id: int = 0x00,
        direction: int = 1,
        density: int = 0x50,
        speed: int = 0x64,
        led_count: int | None = None,
    ) -> int:
        """Render a full per-LED configuration.

        Sends one ``E1 26`` paint per distinct LED setting, covering every LED,
        optionally preceded by an ``E1 23`` mode entry.

        Args:
            leds: Desired per-LED settings, in physical order.
            enter: Send an ``E1 23`` first to guarantee scribble mode.
            effect_id: Scribble effect applied to every group, 0-8.
            direction: ``0x01`` left→right, ``0x02`` right→left.
            density: Effect density 0-100.
            speed: Effect speed 0-100.
            led_count: Override the device LED count. Read from the device when
                omitted.

        Returns:
            The number of paints sent.

        Raises:
            ValueError: if more LEDs are supplied than the device has.
        """
        with self._lock:
            count = led_count if led_count is not None else self.get_state().led_count
            normalized = normalize(leds, count)
            groups = group_leds(normalized)
            if enter:
                self._exchange(commands.scribble_init(count))
            for group in groups:
                self._exchange(self._paint_inner(group, count, effect_id, direction, density, speed))
        return len(groups)

    @staticmethod
    def _paint_inner(
        group: PaintGroup,
        led_count: int,
        effect_id: int,
        direction: int,
        density: int,
        speed: int,
    ) -> bytes:
        led = group.leds
        red, green, blue = led.rgb or (0, 0, 0)
        return commands.scribble_paint(
            led_count=led_count,
            indices=group.indices,
            red=red,
            green=green,
            blue=blue,
            white=led.white or 0,
            effect_id=effect_id,
            direction=direction,
            density=density,
            speed=speed,
            blink_mode=led.blink_mode,
            blink_speed=led.blink_speed,
        )

    # -- clock -------------------------------------------------------------

    def get_time(self) -> dt.datetime | None:
        """Read the controller's clock, or ``None`` if it is not set."""
        with self._lock:
            frame = self._exchange(
                commands.get_time(), VERSION_LEGACY, expect_reply=True
            )
        if frame is None:
            return None
        parsed = parse_time(frame.inner)
        if parsed is None:
            return None
        try:
            return dt.datetime(*parsed)  # noqa: DTZ001 - device wall-clock time
        except ValueError:
            return None

    def set_time(self, when: dt.datetime | None = None) -> None:
        """Write the clock, defaulting to local now."""
        with self._lock:
            self._exchange(commands.set_time(when or dt.datetime.now()), VERSION_LEGACY)

    def sync_clock(self) -> dt.datetime | None:
        """Set the clock to local now and return the new device time."""
        self.set_time()
        return self.get_time()

    # -- timers ------------------------------------------------------------

    def get_timers(self) -> list[Timer]:
        """Read all six hardware timer slots.

        Raises:
            SurplifeConnectionError: if the device does not answer.
            SurplifeError: if the reply cannot be parsed.
        """
        with self._lock:
            frame = self._exchange(
                commands.get_timers(),
                VERSION_LEGACY,
                expect_reply=True,
                read_timeout=3.0,
            )
        if frame is None:
            raise SurplifeConnectionError("no timer reply")
        try:
            return parse_timers(frame.inner)
        except ValueError as err:
            raise SurplifeError(f"unparseable timer reply: {err}") from err

    def set_timer(
        self,
        *,
        slot: int,
        active: bool = True,
        hour: int = 0,
        minute: int = 0,
        repeat_mask: int = 0,
        action: int = 0x24,
        color: tuple[int, int, int] | None = None,
    ) -> None:
        """Write one timer slot."""
        with self._lock:
            self._exchange(
                commands.set_timer(
                    slot=slot,
                    active=active,
                    hour=hour,
                    minute=minute,
                    repeat_mask=repeat_mask,
                    action=action,
                    color=color,
                ),
                VERSION_LEGACY,
            )

    def clear_timer(self, slot: int) -> None:
        """Disable a timer slot."""
        self.set_timer(slot=slot, active=False)

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> SurplifeClient:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __repr__(self) -> str:
        state = "connected" if self.connected else "disconnected"
        return f"<SurplifeClient {self.host}:{self.port} {state}>"
