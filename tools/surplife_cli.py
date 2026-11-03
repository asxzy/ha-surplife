#!/usr/bin/env python3
"""Standalone stdlib client for Surplife / Zengge 0xB6 controllers.

Talks to the hardware directly, with no Home Assistant and no third-party
packages. Useful for verifying a controller before installing the integration,
and for reproducing protocol evidence.

    python3 tools/surplife_cli.py --discover
    python3 tools/surplife_cli.py 192.168.1.50 info
    python3 tools/surplife_cli.py 192.168.1.50 on
    python3 tools/surplife_cli.py 192.168.1.50 color 255 0 0
    python3 tools/surplife_cli.py 192.168.1.50 color 0,0,0,100
    python3 tools/surplife_cli.py 192.168.1.50 white 80
    python3 tools/surplife_cli.py 192.168.1.50 rgbw 255 0 0 80
    python3 tools/surplife_cli.py 192.168.1.50 effect wave --speed 60
    python3 tools/surplife_cli.py 192.168.1.50 effects
    python3 tools/surplife_cli.py 192.168.1.50 segments red green blue
    python3 tools/surplife_cli.py 192.168.1.50 scribble "20xred 15xblue/fast"
    python3 tools/surplife_cli.py 192.168.1.50 timers
    python3 tools/surplife_cli.py 192.168.1.50 sync-clock
    python3 tools/surplife_cli.py 192.168.1.50 get-time

Colour is RGBW. ``color`` takes ``R G B`` or a single token, and a four-part
token is RGBW where the fourth value is the W level 0-100 — so ``color
0,0,0,100`` lights the white LED alone instead of imitating white with RGB.
``white 80`` sets only the W channel and leaves the colour channels alone, and
``rgbw R G B W`` sets both together.

Every command prints the state frame it reads back afterwards, so the effect of
a change is visible rather than assumed.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any

# Load the vendored protocol package directly from disk rather than through
# ``custom_components.surplife``: that package's ``__init__`` imports Home
# Assistant, and the whole point of this CLI is to run with nothing installed.
#
# Importing by file location (instead of putting the directory on ``sys.path``)
# also avoids shadowing stdlib modules — the integration directory contains a
# ``select.py``, which would otherwise hide the stdlib ``select`` that asyncio
# imports.
_PROTOCOL_DIR = (
    Path(__file__).resolve().parent.parent / "custom_components" / "surplife" / "protocol"
)
_spec = importlib.util.spec_from_file_location(
    "protocol",
    _PROTOCOL_DIR / "__init__.py",
    submodule_search_locations=[str(_PROTOCOL_DIR)],
)
if _spec is None or _spec.loader is None:  # pragma: no cover - broken checkout
    raise SystemExit(f"cannot load the protocol package from {_PROTOCOL_DIR}")
protocol = importlib.util.module_from_spec(_spec)
sys.modules["protocol"] = protocol
_spec.loader.exec_module(protocol)

from protocol import (  # noqa: E402
    EFFECT_NAMES,
    SCRIBBLE_EFFECT_NAMES,
    ScribbleLED,
    SurplifeClient,
    SurplifeError,
    discover,
)
from protocol.constants import (  # noqa: E402
    DIRECTIONS,
    SCRIBBLE_EFFECT_NAME_TO_ID,
    WRITE_ALL,
)
from protocol.spec import parse_color, parse_led_spec  # noqa: E402


def show_state(client: SurplifeClient) -> None:
    """Print the decoded state frame."""
    state = client.get_state()
    print(f"  power        : {'on' if state.power else 'off'}")
    print(f"  mode         : {state.preset_label}")
    print(f"  pattern byte : 0x{state.pattern:02x}", end="")
    if state.effect_name:
        print(f"  ({state.effect_name})")
    else:
        print("  (not a documented effect)")
    print(f"  speed        : {state.speed}")
    print(f"  effect colour: {state.rgb}  (device-reported, may be stale)")
    print(f"  LED count    : {state.led_count}")
    print(f"  model/fw     : 0x{state.model:02x} / {state.firmware}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Standalone client for Surplife / Zengge 0xB6 controllers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("host", nargs="?", help="controller IP address")
    parser.add_argument(
        "command",
        nargs="?",
        default="info",
        help="info, on, off, color, white, rgbw, effect, effects, segments, "
        "scribble, timers, get-time, sync-clock",
    )
    parser.add_argument("args", nargs="*", help="command arguments")
    parser.add_argument("--discover", action="store_true", help="find controllers")
    parser.add_argument("--speed", type=int, default=50, help="effect speed 0-100")
    parser.add_argument("--density", type=int, default=50, help="effect density 0-100")
    parser.add_argument(
        "--direction", default="l2r", choices=sorted(DIRECTIONS), help="effect direction"
    )
    parser.add_argument("--effect", default="static", help="scribble effect name")
    parser.add_argument("--port", type=int, default=5577)
    opts = parser.parse_args(argv)

    if opts.discover:
        devices = discover(timeout=3.0)
        if not devices:
            print("No controllers answered.", file=sys.stderr)
            return 1
        for device in devices:
            print(
                f"{device.host:16s} {device.mac}  {device.hw_version}  "
                f"{device.firmware_info or '-'}"
            )
        return 0

    if not opts.host:
        parser.error("a host is required unless --discover is used")

    try:
        with SurplifeClient(opts.host, opts.port) as client:
            return _run_command(client, opts)
    except SurplifeError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


def _run_command(client: SurplifeClient, opts: argparse.Namespace) -> int:
    command = opts.command

    if command == "info":
        show_state(client)

    elif command == "on":
        client.set_power(True)
        show_state(client)

    elif command == "off":
        client.set_power(False)
        show_state(client)

    elif command == "color":
        if len(opts.args) == 1:
            rgb, white = parse_color(opts.args[0])
            if rgb is None and white is None:
                raise ValueError("colour mode needs an RGB or RGBW value")
            if white:
                # W is present, so this is an RGBW request: light the white LED
                # (colour channels dark for a bare ``w80``) alongside the colour.
                red, green, blue = rgb or (0, 0, 0)
                client.set_levels(
                    red=red,
                    green=green,
                    blue=blue,
                    white=white,
                    write_mode=WRITE_ALL,
                )
            else:
                client.set_color(*rgb)  # type: ignore[misc]
        elif len(opts.args) == 3:
            red, green, blue = (int(value) for value in opts.args)
            client.set_color(red, green, blue)
        elif len(opts.args) == 4:
            red, green, blue, white = (int(value) for value in opts.args)
            client.set_levels(
                red=red, green=green, blue=blue, white=white, write_mode=WRITE_ALL
            )
        else:
            raise ValueError(
                "color takes one name/hex/RGBW token, three R G B values, "
                "or four R G B W values"
            )
        show_state(client)

    elif command == "white":
        level = int(opts.args[0]) if opts.args else 100
        client.set_warm_white(level)
        show_state(client)

    elif command == "rgbw":
        if len(opts.args) != 4:
            raise ValueError("rgbw takes four values: R G B W (W is 0-100)")
        red, green, blue, white = (int(value) for value in opts.args)
        client.set_levels(
            red=red, green=green, blue=blue, white=white, write_mode=WRITE_ALL
        )
        show_state(client)

    elif command == "effects":
        for effect_id, name in sorted(EFFECT_NAMES.items()):
            print(f"  {effect_id:3d}  0x{effect_id:02x}  {name}")
        print("\nScribble effects:")
        for effect_id, name in sorted(SCRIBBLE_EFFECT_NAMES.items()):
            print(f"  {effect_id:3d}  0x{effect_id:02x}  {name}")

    elif command == "effect":
        if not opts.args:
            raise ValueError("effect needs a name or numeric id; try 'effects'")
        target = opts.args[0]
        if target.isdigit():
            effect_id = int(target)
        else:
            matches = {
                effect_id: name
                for effect_id, name in EFFECT_NAMES.items()
                if name.lower() == target.lower()
            }
            if not matches:
                raise ValueError(f"unknown effect {target!r}; try 'effects'")
            effect_id = next(iter(matches))
        colour = parse_color(opts.args[1])[0] if len(opts.args) > 1 else None
        client.set_effect(
            effect_id,
            [colour] if colour else None,
            speed=opts.speed,
            density=opts.density,
            direction=DIRECTIONS[opts.direction],
        )
        show_state(client)

    elif command == "segments":
        if not opts.args:
            raise ValueError("segments needs at least one colour")
        entries: list[Any] = []
        for token in opts.args:
            rgb, white = parse_color(token)
            if rgb is None and white is None:
                entries.append(None)
            elif rgb is None:
                entries.append((0, 0, 0, white))
            elif white is not None:
                entries.append((*rgb, white))
            else:
                entries.append(rgb)
        client.set_segments(entries)
        show_state(client)

    elif command == "scribble":
        if not opts.args:
            raise ValueError('scribble needs a pattern, e.g. "20xred 15xblue"')
        effect = opts.effect
        if effect.isdigit():
            effect_id = int(effect)
        else:
            # Accept the same spellings the Home Assistant service accepts
            # (``stars_wink``), and also the display names (``Stars Wink``).
            key = effect.lower().replace(" ", "_")
            effect_id = SCRIBBLE_EFFECT_NAME_TO_ID.get(key)
            if effect_id is None:
                effect_id = next(
                    (
                        candidate
                        for candidate, name in SCRIBBLE_EFFECT_NAMES.items()
                        if name.lower() == effect.lower()
                    ),
                    None,
                )
            if effect_id is None:
                raise ValueError(f"unknown scribble effect {effect!r}")
        leds = parse_led_spec(opts.args[0], client.get_state().led_count)
        paints = client.set_scribble(
            leds,
            effect_id=effect_id,
            direction=DIRECTIONS[opts.direction],
            density=opts.density,
            speed=100,
        )
        print(f"  sent {paints} paint(s) for {len(leds)} LEDs")
        show_state(client)

    elif command == "timers":
        for timer in client.get_timers():
            if timer.active:
                colour = f" {timer.color}" if timer.color else ""
                print(
                    f"  slot {timer.slot}: {timer.time_label} "
                    f"{timer.action_name} {timer.repeat_label}{colour}"
                )
            else:
                print(f"  slot {timer.slot}: unset")

    elif command == "get-time":
        when = client.get_time()
        print(f"  device clock: {when.isoformat() if when else 'unreadable'}")

    elif command == "sync-clock":
        print(f"  synced to: {client.sync_clock()}")

    else:
        raise ValueError(f"unknown command {command!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
