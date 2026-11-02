"""UDP discovery of Surplife controllers.

Controllers listen on UDP port 48899 and answer the plain-text probe
``HF-A11ASSISTHREAD`` with a comma-separated line::

    192.168.1.50,AABBCCDDEEFF,AK001-ZJ21413,<device id>

Sending ``AT+LVER`` returns the firmware string, for example
``+ok=B6_26_20240514_ZG-BL-UFO``.

**Verified** against all three controllers in this installation.
"""

from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import dataclass

from .constants import (
    DISCOVERY_PORT,
    DISCOVERY_PROBE,
    MODEL_HW_VERSION,
    MODEL_INFO,
    MODEL_NAME,
)

LVER_PROBE = b"AT+LVER"
DEFAULT_TIMEOUT = 3.0


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    """A controller found on the local network.

    Attributes:
        host: IPv4 address.
        mac: MAC address as reported, uppercase hex without separators.
        hw_version: Hardware identifier, e.g. ``AK001-ZJ21413``.
        device_id: Device ID as reported.
        firmware_info: Reply to ``AT+LVER``, or ``None`` if not queried.
    """

    host: str
    mac: str
    hw_version: str
    device_id: str
    firmware_info: str | None = None

    @property
    def unique_id(self) -> str:
        """Stable identifier, preferring the MAC address."""
        return self.mac or self.device_id or self.host

    @property
    def model_info(self) -> str | None:
        """Parse ``MODEL_INFO`` out of the firmware string, if present."""
        if not self.firmware_info:
            return None
        text = self.firmware_info
        if text.startswith("+ok="):
            text = text[4:]
        # e.g. B6_26_20240514_ZG-BL-UFO
        for part in text.split("_"):
            if part.startswith("ZG-"):
                return part
        return None

    @property
    def firmware_version(self) -> str | None:
        """Parse the human-readable firmware version, e.g. ``9.26``.

        The ``AT+LVER`` reply looks like ``+ok=B6_26_20240514_ZG-BL-UFO``. The
        ``B6`` prefix is the model number and the next field is the firmware
        version as reported by the device registry (``9.26``).
        """
        if not self.firmware_info:
            return None
        text = self.firmware_info
        if text.startswith("+ok="):
            text = text[4:]
        parts = text.split("_")
        if len(parts) >= 2 and parts[0].upper().startswith("B6"):
            return f"9.{parts[1]}"
        return None

    @property
    def model(self) -> str:
        """Model description for the device registry."""
        return MODEL_NAME


def _parse_reply(data: bytes, host: str) -> DiscoveredDevice | None:
    text = data.decode("ascii", errors="replace").strip()
    if not text or "," not in text:
        return None
    parts = [part.strip() for part in text.split(",")]
    if len(parts) < 3:
        return None
    return DiscoveredDevice(
        host=parts[0] or host,
        mac=parts[1].upper(),
        hw_version=parts[2],
        device_id=parts[3] if len(parts) > 3 else "",
    )


def _query_lver(host: str, timeout: float) -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(LVER_PROBE, (host, DISCOVERY_PORT))
            data, _ = sock.recvfrom(1024)
            return data.decode("ascii", errors="replace").strip()
    except OSError:
        return None


def discover(
    timeout: float = DEFAULT_TIMEOUT,
    *,
    query_firmware: bool = True,
    rounds: int = 3,
) -> list[DiscoveredDevice]:
    """Broadcast for controllers on the local network.

    The controllers drop UDP replies intermittently — in testing, one of three
    devices failed to answer roughly one probe in three. A single broadcast
    therefore finds a random subset, so this sends several and merges the
    results.

    Args:
        timeout: How long to wait for replies in each round, in seconds.
        query_firmware: Also send ``AT+LVER`` to each responder to obtain the
            firmware string.
        rounds: Number of broadcast rounds. Each waits ``timeout`` seconds.

    Returns:
        One entry per responder, de-duplicated by MAC address and sorted by IP.

    **Verified**: reliably returns all three controllers in this installation.
    """
    found: dict[str, DiscoveredDevice] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(max(1, rounds)):
            sock.settimeout(timeout)
            sock.sendto(DISCOVERY_PROBE, ("255.255.255.255", DISCOVERY_PORT))
            deadline = time.monotonic() + timeout
            while (remaining := deadline - time.monotonic()) > 0:
                sock.settimeout(remaining)
                try:
                    data, addr = sock.recvfrom(1024)
                except TimeoutError:
                    break
                device = _parse_reply(data, addr[0])
                if device is not None:
                    found.setdefault(device.unique_id, device)

    if query_firmware:
        found = {
            key: DiscoveredDevice(
                host=device.host,
                mac=device.mac,
                hw_version=device.hw_version,
                device_id=device.device_id,
                firmware_info=_query_lver(device.host, timeout),
            )
            for key, device in found.items()
        }
    return sorted(found.values(), key=lambda d: tuple(int(p) for p in d.host.split(".")))


async def async_discover(
    timeout: float = DEFAULT_TIMEOUT,
    *,
    query_firmware: bool = True,
    rounds: int = 3,
) -> list[DiscoveredDevice]:
    """Async wrapper around :func:`discover`, run in the default executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: discover(timeout, query_firmware=query_firmware, rounds=rounds),
    )


def is_supported(device: DiscoveredDevice) -> bool:
    """Return whether a discovered device looks like a 0xB6 controller."""
    if device.hw_version.upper() == MODEL_HW_VERSION:
        return True
    return device.model_info == MODEL_INFO
