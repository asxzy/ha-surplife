"""Config flow tests.

Discovery hits the real network, so every test here patches it. The point is to
cover the branches a user actually reaches: picking a discovered device,
falling back to manual entry, rejecting the wrong hardware, and refusing to add
the same controller twice.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.surplife.const import CONF_LED_COUNT, CONF_MAC, DOMAIN
from custom_components.surplife.protocol import DiscoveredDevice

HOST = "192.168.1.50"


def _discovered(
    host: str = HOST,
    mac: str = "AABBCCDDEEFF",
    firmware: str = "+ok=B6_26_20240514_ZG-BL-UFO",
) -> DiscoveredDevice:
    return DiscoveredDevice(
        host=host,
        mac=mac,
        hw_version="AK001-ZJ21413",
        device_id=mac,
        firmware_info=firmware,
    )


def _state(model: int = 0xB6):
    from custom_components.surplife.protocol.state import parse_state

    from . import golden

    frame = bytearray(golden.STATE_PRISTINE_INNER)
    frame[4] = model
    return parse_state(bytes(frame))


@pytest.fixture(autouse=True)
def _patch_discovery():
    """Never touch the real network from a unit test."""
    with (
        patch(
            "custom_components.surplife.protocol.discovery.discover",
            return_value=[],
        ),
        patch(
            "custom_components.surplife.protocol.discovery._query_lver",
            return_value=None,
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def _patch_coordinator_client():
    """Stub the coordinator's client.

    A successful config flow creates a config entry, and Home Assistant then
    sets the integration up immediately — which would otherwise open a real
    socket to whatever IP the test used.
    """
    with patch(
        "custom_components.surplife.coordinator.SurplifeClient", autospec=True
    ) as factory:
        client = factory.return_value
        client.get_state = Mock(side_effect=_state)
        client.close = Mock()
        client.set_power = Mock()
        yield client


async def test_manual_entry_when_nothing_discovered(hass: HomeAssistant) -> None:
    """With no replies, the flow goes straight to asking for an IP."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"


async def test_manual_entry_creates_entry(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.surplife.config_flow.SurplifeClient"
    ) as factory:
        factory.return_value.get_state = Mock(return_value=_state())
        factory.return_value.close = Mock()

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: HOST, CONF_NAME: "Soffit West"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Soffit West"
    assert result["data"][CONF_HOST] == HOST
    assert result["data"][CONF_LED_COUNT] == 35


async def test_discovery_offers_devices(hass: HomeAssistant) -> None:
    """A discovered controller is offered as a selectable option."""
    with patch(
        "custom_components.surplife.config_flow.async_discover",
        return_value=[_discovered()],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_discovery_ignores_unsupported_hardware(hass: HomeAssistant) -> None:
    """A non-0xB6 responder falls back to manual rather than offering itself."""
    other = DiscoveredDevice(
        host="192.168.1.99",
        mac="AABBCCDDEEFF",
        hw_version="AK001-ZJ2101",
        device_id="x",
    )
    with patch(
        "custom_components.surplife.config_flow.async_discover",
        return_value=[other],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["step_id"] == "manual"


async def test_unreachable_device_shows_error(hass: HomeAssistant) -> None:
    from custom_components.surplife.protocol import SurplifeConnectionError

    with patch(
        "custom_components.surplife.config_flow.SurplifeClient"
    ) as factory:
        factory.return_value.get_state = Mock(
            side_effect=SurplifeConnectionError("no route")
        )
        factory.return_value.close = Mock()

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_NAME: "Soffit West"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_wrong_model_is_rejected(hass: HomeAssistant) -> None:
    """A device that answers but is not a 0xB6 must not be added."""
    with patch(
        "custom_components.surplife.config_flow.SurplifeClient"
    ) as factory:
        factory.return_value.get_state = Mock(return_value=_state(model=0x35))
        factory.return_value.close = Mock()

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_NAME: "Soffit West"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_supported"}


async def test_duplicate_device_aborts(hass: HomeAssistant) -> None:
    """The same MAC address cannot be added twice."""
    with patch(
        "custom_components.surplife.config_flow.SurplifeClient"
    ) as factory:
        factory.return_value.get_state = Mock(return_value=_state())
        factory.return_value.close = Mock()

        for _ in range(2):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_HOST: HOST, CONF_NAME: "Soffit West"},
            )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_unique_id_is_the_mac(hass: HomeAssistant) -> None:
    """The MAC is the identity, so a DHCP change does not duplicate the device."""
    with patch(
        "custom_components.surplife.config_flow.SurplifeClient"
    ) as factory:
        factory.return_value.get_state = Mock(return_value=_state())
        factory.return_value.close = Mock()

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_NAME: "Soffit West"}
        )

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == HOST  # no MAC known for a manual entry
    assert result["data"][CONF_MAC] == HOST


async def test_manual_entry_uses_reported_mac_when_discovered(
    hass: HomeAssistant,
) -> None:
    """A discovered device keeps its MAC as the unique id."""
    with (
        patch(
            "custom_components.surplife.config_flow.async_discover",
            return_value=[_discovered()],
        ),
        patch(
            "custom_components.surplife.config_flow.SurplifeClient"
        ) as factory,
    ):
        factory.return_value.get_state = Mock(return_value=_state())
        factory.return_value.close = Mock()

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == "AABBCCDDEEFF"
