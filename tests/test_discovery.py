"""Tests for UDP discovery parsing.

The socket is never opened here; only the pure reply-parsing and metadata
derivation are exercised, so the suite runs offline.
"""

from __future__ import annotations

import pytest

from custom_components.surplife.protocol import discovery

from . import golden


def _parse(reply: bytes = golden.DISCOVERY_REPLY, host: str = "192.168.1.50"):
    device = discovery._parse_reply(reply, host)
    assert device is not None
    return device


def test_parse_reply_fields() -> None:
    device = _parse()
    assert device.host == "192.168.1.50"
    assert device.mac == "AABBCCDDEEFF"
    assert device.hw_version == "AK001-ZJ21413"
    assert device.device_id == "AABBCCDDEEFF"


def test_unique_id_prefers_mac() -> None:
    assert _parse().unique_id == "AABBCCDDEEFF"


def test_mac_is_uppercased() -> None:
    device = discovery._parse_reply(
        b"192.168.1.5,aabbccddeeff,AK001-ZJ21413,1", "192.168.1.5"
    )
    assert device is not None
    assert device.mac == "AABBCCDDEEFF"


def test_model_info_parsed_from_firmware() -> None:
    device = discovery.DiscoveredDevice(
        host="192.168.1.50",
        mac="AABBCCDDEEFF",
        hw_version="AK001-ZJ21413",
        device_id="x",
        firmware_info=golden.DISCOVERY_LVER_REPLY.decode(),
    )
    assert device.model_info == "ZG-BL-UFO"


def test_firmware_version_parsed() -> None:
    device = discovery.DiscoveredDevice(
        host="192.168.1.50",
        mac="x",
        hw_version="AK001-ZJ21413",
        device_id="x",
        firmware_info=golden.DISCOVERY_LVER_REPLY.decode(),
    )
    assert device.firmware_version == "9.26"


def test_model_info_none_without_firmware() -> None:
    device = discovery.DiscoveredDevice(
        host="1.2.3.4", mac="x", hw_version="AK001-ZJ21413", device_id="x"
    )
    assert device.model_info is None
    assert device.firmware_version is None


def test_is_supported_by_hardware_id() -> None:
    assert discovery.is_supported(_parse()) is True


def test_is_supported_by_model_info() -> None:
    device = discovery.DiscoveredDevice(
        host="1.2.3.4",
        mac="x",
        hw_version="SOMETHING-ELSE",
        device_id="x",
        firmware_info="+ok=B6_26_20240514_ZG-BL-UFO",
    )
    assert discovery.is_supported(device) is True


def test_is_supported_rejects_other_devices() -> None:
    device = discovery.DiscoveredDevice(
        host="1.2.3.4", mac="x", hw_version="AK001-ZJ2101", device_id="x"
    )
    assert discovery.is_supported(device) is False


@pytest.mark.parametrize(
    "reply",
    [b"", b"garbage", b"only,three", b"\xff\xfe\xfd"],
    ids=["empty", "no-commas", "two-fields", "binary"],
)
def test_parse_reply_rejects_malformed(reply: bytes) -> None:
    assert discovery._parse_reply(reply, "192.168.1.1") is None
