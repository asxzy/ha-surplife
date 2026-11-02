"""Live integration check against a real controller.

Not part of the normal suite — it needs hardware on the LAN. The HA test plugin
blocks sockets by default, so allow the device explicitly::

    SURPLIFE_HOST=192.168.1.50 \\
        .venv/bin/python -m pytest tests/test_live.py -v \\
        --allow-hosts=192.168.1.50

The test leaves the controller powered off, which is the state it starts in.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest

from custom_components.surplife.protocol import (
    SurplifeClient,
    SurplifeState,
    discover,
)

HOST = os.environ.get("SURPLIFE_HOST")

pytestmark = pytest.mark.skipif(
    not HOST, reason="set SURPLIFE_HOST to run live tests"
)


@pytest.fixture(autouse=True)
def _allow_real_network():
    """Undo the Home Assistant socket guard for these tests only.

    ``pytest_homeassistant_custom_component`` runs ``pytest_runtest_setup``,
    which calls ``socket_allow_hosts(["127.0.0.1"])`` and ``disable_socket()``
    for *every* test, so a pytest marker cannot win. An autouse fixture runs
    after that hook, which makes this the one place able to re-open the guard.
    """
    import pytest_socket

    pytest_socket.enable_socket()
    pytest_socket.socket_allow_hosts([HOST, "255.255.255.255"])
    yield


@pytest.fixture
def client():
    """One connection per test.

    Connecting per test is cheap and keeps each test independent.
    """
    with SurplifeClient(HOST) as instance:
        yield instance


def test_state_is_readable(client: SurplifeClient) -> None:
    state = client.get_state()
    assert isinstance(state, SurplifeState)
    assert state.model == 0xB6
    assert 1 <= state.led_count <= 4096


def test_power_roundtrip(client: SurplifeClient) -> None:
    client.set_power(True)
    assert client.get_state().power is True
    client.set_power(False)
    assert client.get_state().power is False


def test_effect_reports_back(client: SurplifeClient) -> None:
    """The device echoes the effect id and speed in its state frame."""
    client.set_power(True)
    client.set_effect(2, [(255, 0, 0)], speed=42)  # Meteor
    state = client.get_state()
    assert state.pattern == 2
    assert state.speed == 42
    client.set_power(False)


def test_scribble_renders(client: SurplifeClient) -> None:
    """A grouped per-LED paint puts the device into scribble mode."""
    from custom_components.surplife.protocol import ScribbleLED

    client.set_power(True)
    count = client.get_state().led_count
    leds = [ScribbleLED((255, 0, 0))] * (count // 2) + [ScribbleLED((0, 0, 255))] * (
        count - count // 2
    )
    paints = client.set_scribble(leds)
    assert paints == 2
    assert client.get_state().preset_label == "scribble"
    client.set_power(False)


def test_timers_are_readable(client: SurplifeClient) -> None:
    timers = client.get_timers()
    assert len(timers) == 6
    assert [t.slot for t in timers] == [1, 2, 3, 4, 5, 6]


def test_clock_roundtrip(client: SurplifeClient) -> None:
    client.set_time(dt.datetime(2026, 11, 2, 12, 0, 0))
    read_back = client.get_time()
    assert read_back is not None
    assert (read_back.year, read_back.month, read_back.day) == (2026, 11, 2)
    assert read_back.hour == 12


def test_discovery_finds_controller() -> None:
    devices = discover(timeout=3)
    assert devices, "no controllers answered the UDP probe"
    assert any(device.host == HOST for device in devices)
