"""Pytest configuration for Home Assistant integration tests.

The ``pytest_homeassistant_custom_component`` plugin blocks sockets so unit
tests cannot accidentally reach the network. The live tests are the exception:
they exist to talk to real hardware, so they opt back in with the plugins'
own ``socket_enabled`` fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components in every test."""
    return
