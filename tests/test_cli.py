"""Tests for the standalone CLI's wiring.

The colour and pattern grammar lives in ``protocol/spec.py`` and is covered by
``test_spec.py``; what matters here is that each subcommand reaches the right
client method with the right arguments.

The module is loaded by file path, exactly as the script does, because
importing through ``custom_components.surplife`` would drag in Home Assistant —
and the CLI exists precisely so it can run without Home Assistant installed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

CLI_PATH = Path(__file__).resolve().parent.parent / "tools" / "surplife_cli.py"


@pytest.fixture(scope="module")
def cli():
    """Load ``tools/surplife_cli.py`` without executing ``main()``."""
    spec = importlib.util.spec_from_file_location("surplife_cli_under_test", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.fixture
def client(cli, monkeypatch):
    """A fake client that ``main()`` will construct."""
    fake = Mock()
    fake.get_state.return_value = cli.protocol.SurplifeState(
        power=True,
        preset=0x25,
        pattern=0x01,
        speed=60,
        hue=0,
        saturation=100,
        value=100,
        led_count=35,
        model=0xB6,
        firmware=9,
        raw=b"",
    )
    fake.get_timers.return_value = []
    fake.set_scribble.return_value = 2

    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=fake)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(cli, "SurplifeClient", factory)
    return fake


def run(cli, *argv: str) -> int:
    """Run ``main`` with a host already supplied."""
    return cli.main(["192.168.1.50", *argv])


def test_info_prints_state(cli, client, capsys) -> None:
    assert run(cli, "info") == 0
    out = capsys.readouterr().out
    assert "power        : on" in out
    assert "Wave" in out
    assert "LED count    : 35" in out


def test_power_commands(cli, client) -> None:
    assert run(cli, "on") == 0
    client.set_power.assert_called_once_with(True)

    client.set_power.reset_mock()
    assert run(cli, "off") == 0
    client.set_power.assert_called_once_with(False)


def test_color_by_name(cli, client) -> None:
    assert run(cli, "color", "red") == 0
    client.set_color.assert_called_once_with(255, 0, 0)


def test_color_by_triple(cli, client) -> None:
    assert run(cli, "color", "1", "2", "3") == 0
    client.set_color.assert_called_once_with(1, 2, 3)


def test_color_rejects_white_level(cli, client) -> None:
    """``w80`` is a white level, not a colour, so ``color`` must refuse it."""
    assert run(cli, "color", "w80") == 1


def test_white(cli, client) -> None:
    assert run(cli, "white", "80") == 0
    client.set_warm_white.assert_called_once_with(80)


def test_white_defaults_to_full(cli, client) -> None:
    assert run(cli, "white") == 0
    client.set_warm_white.assert_called_once_with(100)


def test_effect_by_name(cli, client) -> None:
    assert run(cli, "effect", "wave", "--speed", "60") == 0
    kwargs = client.set_effect.call_args.kwargs
    assert client.set_effect.call_args.args[0] == 1
    assert kwargs["speed"] == 60


def test_effect_by_id(cli, client) -> None:
    assert run(cli, "effect", "22") == 0
    assert client.set_effect.call_args.args[0] == 22


def test_effect_with_colour(cli, client) -> None:
    assert run(cli, "effect", "meteor", "blue") == 0
    assert client.set_effect.call_args.args[1] == [(0, 0, 255)]


def test_effect_unknown_name(cli, client) -> None:
    assert run(cli, "effect", "nope") == 1


def test_effects_lists_every_id(cli, client, capsys) -> None:
    assert run(cli, "effects") == 0
    out = capsys.readouterr().out
    assert "Stars wink" in out
    assert "Static Gradient" in out


def test_segments(cli, client) -> None:
    assert run(cli, "segments", "red", "green", "blue") == 0
    client.set_segments.assert_called_once_with([(255, 0, 0), (0, 255, 0), (0, 0, 255)])


def test_scribble_expands_to_led_count(cli, client) -> None:
    assert run(cli, "scribble", "20xred 15xblue/fast") == 0
    leds = client.set_scribble.call_args.args[0]
    assert len(leds) == 35
    assert leds[0].rgb == (255, 0, 0)
    assert leds[20].blink_mode == 0x10


def test_scribble_effect_name(cli, client) -> None:
    assert run(cli, "scribble", "35xred", "--effect", "stars_wink") == 0
    assert client.set_scribble.call_args.kwargs["effect_id"] == 5


def test_timers_prints_slots(cli, client, capsys) -> None:
    Timer = cli.protocol.Timer

    client.get_timers.return_value = [
        Timer(slot=1, active=True, hour=20, minute=0, action=0x23, repeat_mask=0xFE),
        Timer(slot=2, active=False),
    ]
    assert run(cli, "timers") == 0
    out = capsys.readouterr().out
    assert "slot 1: 20:00 on Everyday" in out
    assert "slot 2: unset" in out


def test_sync_clock(cli, client, capsys) -> None:
    client.sync_clock.return_value = "2026-11-02 12:00:00"
    assert run(cli, "sync-clock") == 0
    client.sync_clock.assert_called_once()
    assert "2026-11-02 12:00:00" in capsys.readouterr().out


def test_unknown_command(cli, client) -> None:
    assert run(cli, "frobnicate") == 1


def test_host_required_without_discover(cli, client) -> None:
    # ``argparse`` writes usage to stderr and raises ``SystemExit``.
    with pytest.raises(SystemExit):
        cli.main(["--speed", "50"])


def test_discover_lists_devices(cli, client, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "discover",
        Mock(
            return_value=[
                cli.protocol.DiscoveredDevice(
                    host="192.168.1.50",
                    mac="AABBCCDDEEFF",
                    hw_version="AK001-ZJ21413",
                    device_id="x",
                )
            ]
        ),
    )
    assert cli.main(["--discover"]) == 0
    out = capsys.readouterr().out
    assert "192.168.1.50" in out
    assert "AABBCCDDEEFF" in out


def test_discover_reports_nothing_found(cli, client, monkeypatch) -> None:
    monkeypatch.setattr(cli, "discover", Mock(return_value=[]))
    assert cli.main(["--discover"]) == 1
