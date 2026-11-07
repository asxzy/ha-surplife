# Surplife Permanent Lighting for Home Assistant

A Home Assistant custom component that unlocks the **full** capability of
Surplife / Zengge **0xB6** outdoor permanent lighting controllers
(`AK001-ZJ21413`) on the local network — the feature set the stock `flux_led`
integration deliberately does not expose.

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

### Marketplace (Home Assistant 2026.11 and later, recommended)

1. Settings → Devices & Services → **Add Integration** → *Marketplace*
2. Add `https://github.com/asxzy/ha-surplife` as an **Integration**
3. Install **Surplife Permanent Lighting**, restart Home Assistant
4. Settings → Devices & Services → **Add Integration** → *Surplife Permanent
   Lighting*

### HACS (older Home Assistant)

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/asxzy/ha-surplife` as an **Integration**
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
- `text.<name>_per_led_pattern` — type a per-LED pattern ("scribble")
- `select.<name>_per_led_look` — nine one-tap per-LED patterns
- `select.<name>_scene` — apply a stored scene, or today's scene
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

A full walkthrough — including the calendar automation, timers and the
symptoms that look like bugs but are not — is in
[`docs/usage.md`](docs/usage.md).

## Per-LED ("scribble") painting

The wire protocol takes a full per-LED pattern; the app calls this
**doodle/Graffiti**. It is exposed three ways, from casual to exact:

```yaml
# One tap: a built-in look
service: surplife.apply_look
target:
  entity_id: light.soffit_east
data:
  look: candy_cane          # solid, stripes, candy_cane, gradient, rainbow,
  palette: ["#ff0000", "#ffffff"]   # firefly, twinkle, chase, wreath

# Type it: the text entity
#   text.soffit_east_per_led_pattern  <-  "12xred 23xblue/fast"

# Script it: the raw grammar
service: surplife.set_scribble
target:
  entity_id: light.soffit_east
data:
  pattern: "12xred/fast 23x0,0,255"
```

## Scenes that follow the calendar

A *scene* is a named recipe — colour, warm white, effect, segments, a per-LED
look or pattern — stored in Home Assistant and applicable to any controller.
Give it a season and an automation can apply "whatever belongs to today":

```yaml
service: surplife.save_scene
data:
  name: christmas
  mode: effect
  effect: Flowing Water
  colors: ["#ff0000", "#00c83c"]
  start: "11-15"     # inclusive, and windows wrap the year end
  end: "12-31"
  tags: holidays

service: surplife.apply_scene_for_date
target:
  entity_id:
    - light.soffit_east
    - light.soffit_west
    - light.soffit_south
data:
  randomize: true    # stable per day: seeded from the date, not an RNG
```

Full details in [`docs/scenes_and_looks.md`](docs/scenes_and_looks.md).

## Grouping

The Surplife app's device grouping is **cloud-side only** — it is a
`GroupControl` record pushed over MQTT, with no local-LAN group command. This
integration provides the equivalent as a **real device** instead:

1. **Settings → Devices & Services → Add Integration → Surplife Permanent
   Lighting**.
2. On an instance that already has controllers you get a menu — choose
   **Group controllers into one device**.
3. Pick two or more controllers, give the group a name (it suggests the words
   they have in common, so `Soffit East`/`Soffit West` becomes **Soffit**), and
   save.

You now have one device with the same controls as a single controller. On/off,
brightness, colour, white, effects, palettes, per-LED looks, direction, speed,
density, scenes, clock sync and the refresh button all go to every member, and
the group's state follows them:

- **on** when any member is on (turning the group on also relights a strip that
  was left dark);
- **brightness** is the mean of the strips actually lit;
- **colour** comes from the first lit strip (never an average, which would show
  a colour no strip is displaying);
- **effect** is the most common one among the lit strips.

The group resolves the palette, speed, density and direction **once** and sends
the same values to every member, so one tap cannot leave the strips running
three different variants of the same effect. Its Configure panel edits the
group's own effect/look settings, in the same shape as a controller's.

Two diagnostics stay on the controllers: **LED count** and **Timers**. Both
describe one physical strip — three controllers can report three different
counts and three different timer slots — so a merged number would be invented.
The group's **Mode** sensor reports what most of the roof is doing.

The `surplife.*` services accept the group too, so one call reaches the whole
roof:

```yaml
service: surplife.apply_preset
target:
  entity_id: light.soffit      # the group, not three entity ids
data:
  preset: christmas
```

Each controller remains its own device, so a single strip can still be adjusted
on its own when one run needs to differ.

To change which controllers are in the group, use **Reconfigure** on the group
entry: membership lives in the entry's data, not its options. Reconfigure also
works on a controller, where it updates the address (after a DHCP change) or
the name without losing entity ids.

If you only want on/off, brightness and colour and do not need a device page, a
Home Assistant
[light group](https://www.home-assistant.io/integrations/light.group/) helper
still works over the controllers. The built-in group has no device and cannot be
used with the `surplife.*` services, which is what the integration's own group
adds.

## Protocol documentation

- [`docs/protocol_0xB6.md`](docs/protocol_0xB6.md) — verified wire protocol
- [`docs/device_db.md`](docs/device_db.md) — shipped device-database evidence
- [`docs/app_evidence.md`](docs/app_evidence.md) — app UI labels and limits
- [`docs/usage.md`](docs/usage.md) — **how to use it**, day to day
- [`docs/scenes_and_looks.md`](docs/scenes_and_looks.md) — scenes, looks, calendar
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
