# Surplife Permanent Lighting for Home Assistant

A HACS custom component that unlocks the **full** capability of Surplife /
Zengge **0xB6** outdoor permanent lighting controllers (`AK001-ZJ21413`) on the
local network — the feature set the stock `flux_led` integration deliberately
does not expose.

The stock integration drives these controllers fine for on/off, RGB, warm
white and brightness, but it only offers the *generic* LEDENET effect list
(`colorloop`, `red_fade`, …), which does essentially nothing useful on a 0xB6.
This component speaks the device's native `0xE1` command family instead:

| Capability | Command | Status |
|---|---|---|
| Power, RGB, warm white, brightness | `0x71`, `0xE0 0x01` | verified |
| 24 named animated effects (+ direction / density / speed / variant) | `0xE1 0x21` | verified |
| 20 fixed colour segments | `0xE1 0x22` | verified |
| Per-LED ("scribble") colour, white, blink | `0xE1 0x23` + `0xE1 0x26` | verified |
| 6 hardware timer slots (read + write) | `0xE0 0x06` / `0xE0 0x05` | verified |
| Device clock read + sync | `0x11 0x1A`, `0x10 0x14` | verified |
| UDP discovery | `HF-A11ASSISTHREAD` on 48899 | verified |

## Why a separate integration?

Home Assistant pins `flux-led` in the stock `flux_led` manifest. A custom
component that depends on unmerged `flux-led` branches cannot coexist with the
built-in integration and breaks on every HA upgrade. This component instead
**vendors a small, stdlib-only protocol layer** (framing, state parsing,
commands, discovery, client) and depends on nothing outside HA core.

It is designed to run **alongside** the existing `flux_led` entries so you can
verify behaviour before disabling the old ones.

## Install

### HACS (recommended)

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/asxzy/surplife` as an **Integration**
3. Install **Surplife Permanent Lighting**, restart Home Assistant
4. Settings → Devices & Services → **Add Integration** → *Surplife Permanent
   Lighting*

### Manual

Copy `custom_components/surplife/` into your HA `config/custom_components/`
directory and restart.

## Configuration

The config flow discovers controllers over UDP automatically. If discovery is
blocked (VLANs, mDNS filters), enter the IP address manually.

Each controller becomes one device with:

- `light.<name>` — on/off, brightness, RGB, warm white, and the 24 native
  effects
- `select.<name>_effect` — effect picker
- `number.<name>_effect_speed` / `_effect_density` — animation tuning
- `select.<name>_direction` — left→right / right→left
- `sensor.<name>_led_count` / `_firmware` — device facts
- `button.<name>_sync_clock` — push HA's clock to the controller

### Services

```yaml
# Per-LED (scribble) configuration
service: surplife.set_scribble
target:
  entity_id: light.soffit_east
data:
  pattern: "40xred/fast 40xblue"
  effect: flowing
  direction: r2l
  speed: 38

# 20 fixed segments
service: surplife.set_segments
target:
  entity_id: light.soffit_east
data:
  colors: ["#ff0000", "#00ff00", "#0000ff"]

# Hardware timers
service: surplife.set_timer
target:
  entity_id: light.soffit_east
data:
  slot: 1
  time: "20:00"
  action: on
  repeat: everyday
```

## Grouping

The Surplife app's device grouping is **cloud-side only** — it is a
`GroupControl` record pushed over MQTT, with no local-LAN group command. To get
the same effect locally, create a Home Assistant
[light group](https://www.home-assistant.io/integrations/light.group/) over the
three controllers, or use the `surplife.apply_preset` service with
`target: all` to broadcast one command to every controller at once.

## Protocol documentation

- [`docs/protocol_0xB6.md`](docs/protocol_0xB6.md) — verified wire protocol
- [`docs/device_db.md`](docs/device_db.md) — shipped device-database evidence
- [`docs/app_evidence.md`](docs/app_evidence.md) — app UI labels and limits
- [`docs/architecture.md`](docs/architecture.md) — component design

Every wire-level claim in this repository is marked **verified** (probed against
real hardware), **inferred** (consistent with captures but unproven), or
**unknown**. Nothing is presented as fact without evidence.

## CLI

`tools/surplife_cli.py` is a standalone stdlib client for poking devices
without Home Assistant:

```bash
python3 tools/surplife_cli.py 192.168.1.50 info
python3 tools/surplife_cli.py 192.168.1.50 scribble "20xred 15x0,0,255"
python3 tools/surplife_cli.py 192.168.1.50 effect wave --speed 60
python3 tools/surplife_cli.py --discover
```

## License

MIT — see [LICENSE](LICENSE).
