"""Pytest configuration for Home Assistant integration tests.

The ``pytest_homeassistant_custom_component`` plugin blocks sockets so unit
tests cannot accidentally reach the network. The live tests are the exception:
they exist to talk to real hardware, so they opt back in with the plugins'
own ``socket_enabled`` fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components in every test."""
    return


@pytest.fixture
def patched_client():
    """A fake ``SurplifeClient`` that answers every call the coordinator makes.

    Module-specific tests build their own variants; this one exists so that a
    test which only needs "the integration is loaded" does not have to
    duplicate fifteen lines of mock wiring.
    """
    from custom_components.surplife.protocol import Timer
    from custom_components.surplife.protocol.state import parse_state

    from . import golden

    def _state():
        frame = bytearray(golden.STATE_PRISTINE_INNER)
        frame[6] = 0x23
        return parse_state(bytes(frame))

    with patch(
        "custom_components.surplife.coordinator.SurplifeClient", autospec=True
    ) as factory:
        client = factory.return_value
        client.get_state = Mock(side_effect=_state)
        client.set_power = Mock()
        client.set_color = Mock()
        client.set_warm_white = Mock()
        client.set_effect = Mock()
        client.set_segments = Mock()
        client.set_scribble = Mock(return_value=2)
        client.get_timers = Mock(return_value=[Timer(slot=1, active=False)])
        client.sync_clock = Mock()
        client.set_timer = Mock()
        client.clear_timer = Mock()
        client.close = Mock()
        yield client


@pytest.fixture
async def loaded_entry(hass: HomeAssistant, patched_client):
    """One loaded config entry named ``Soffit West``."""
    from custom_components.surplife.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Soffit West",
        data={
            CONF_HOST: "192.168.1.50",
            CONF_NAME: "Soffit West",
            "mac": "AABBCCDDEEFF",
            "hw_version": "AK001-ZJ21413",
            "firmware_info": "+ok=B6_26_20240514_ZG-BL-UFO",
            "led_count": 35,
        },
        unique_id="AABBCCDDEEFF",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
