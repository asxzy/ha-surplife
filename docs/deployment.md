# Deployment and verification

Target in this installation: Home Assistant **2026.10.4** at
`http://192.168.1.10:8123`, timezone America/Edmonton.

> Addresses and MACs throughout the docs and tests are **placeholders**, not a
> real network. Substitute your own host and controller addresses; the shape is
> what matters, not the numbers.

## Preconditions

- The controllers are on the same L2 network as Home Assistant. This component
  speaks **local TCP/UDP only**; it cannot reach devices behind a different
  subnet without routing for both port 5577 (TCP) and 48899 (UDP).
- Each controller has a **DHCP reservation**. Identity is the MAC, so a changed
  IP updates the existing entry rather than creating a duplicate — but a
  reservation avoids the reconnect delay either way.
- The three controllers here: Soffit East `192.168.1.51`, Soffit West
  `192.168.1.50`, Soffit South `192.168.1.52`.

## Install

### HACS

1. HACS → Integrations → ⋮ → **Custom repositories**
2. `https://github.com/asxzy/surplife`, category **Integration**
3. Install, then restart Home Assistant.

### Manual

```bash
rsync -a custom_components/surplife/ \
    root@192.168.1.10:/config/custom_components/surplife/
```

Restart Home Assistant.

**Keep backups and old copies outside `custom_components/`.** Home Assistant
scans every subdirectory there as a potential integration, so a copy left at
`custom_components/surplife.old` is loaded alongside the real one. It carries
the same `domain` in its `manifest.json`, and the import then fails with:

```
Setup failed for custom integration 'surplife':
Unable to import component: No module named 'custom_components.surplife.old'
```

The visible symptom is worse than that message: every `surplife` entity comes
back `unavailable` with `restored: true`, and the integration's services
disappear from the service list entirely, because nothing loaded. Stage
replacements outside the tree (`/config/.surplife_backups/`, `/tmp`) and delete
the staging directory when done.

## Add a controller

Settings → Devices & Services → **Add Integration** → *Surplife Permanent
Lighting*.

The flow broadcasts for controllers. If discovery is blocked (VLAN, mDNS
filtering, AP isolation), choose **Enter an IP address** and type it in.

Discovery is lossy by nature, so if a controller is missing from the list,
re-run the flow rather than assuming it is unreachable. Verify directly first:

```bash
python3 tools/surplife_cli.py --discover
```

## Verify before switching over

The component is designed to run **alongside** the existing `flux_led` entries.
The devices accept multiple concurrent TCP connections, so both integrations can
hold a socket at once.

Recommended order:

1. Add all three controllers to Surplife. Leave the three `flux_led` entries
   enabled.
2. Check each `light.*` entity turns its strip on and off.
3. Set a colour and confirm the strip changes. The entity will report the
   colour you set — the device does not echo it, so this is expected, not a
   fault.
4. Pick each of the 24 effects once from `select.*_effect` and confirm the strip
   reacts.
5. Run a scribble pattern and a segment pattern:
   ```yaml
   service: surplife.set_scribble
   target:
     entity_id: light.soffit_west
   data:
     pattern: "20xred 15xblue/fast"
   ```
6. Read the timers and confirm they match what the app shows. In Home
   Assistant, `sensor.soffit_*_timers` should report the number of active slots
   with a readable `slot_N` attribute for each:
   ```bash
   python3 tools/surplife_cli.py 192.168.1.50 timers
   ```
7. Only then **disable the three `flux_led` entries** (Settings → Devices &
   Services → flux_led → ⋮ → Disable). Do not delete them until you are
   satisfied; disabling is reversible.

## Grouping the three controllers

The app's grouping is cloud-side and does not exist on the local protocol, so
recreate it in Home Assistant. Two options:

**Light group** (Settings → Devices & Services → Helpers → Group → Light), which
gives one entity for on/off, brightness and colour.

**Preset broadcast**, which is better for holiday scenes because every strip
receives an identical, deterministic command:

```yaml
service: surplife.apply_preset
target:
  entity_id:
    - light.soffit_east
    - light.soffit_west
    - light.soffit_south
data:
  preset: christmas
```

Available presets: `halloween`, `christmas`, `thanksgiving`, `canada_day`,
`valentines`, `new_years`, `warm_white`.

## Clock and timers

The controller's clock drives its hardware timers and it drifts, especially
after a power cut. Press `button.<name>_sync_clock`, or run:

```bash
python3 tools/surplife_cli.py 192.168.1.50 sync-clock
```

To set a timer:

```yaml
service: surplife.set_timer
target:
  entity_id: light.soffit_west
data:
  slot: 3
  time: "20:00"
  action: "on"
  repeat: everyday
```

`repeat` accepts `everyday`, `weekdays`, `weekend`, `once`, or a day list such
as `mon,wed,fri`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `cannot_connect` in the config flow | wrong IP, or port 5577 blocked |
| Entity goes `unavailable` | device lost power or Wi-Fi; the coordinator's poll failed |
| Colour reverts after a poll | expected if something *else* changed it — the device does not report colour, so HA cannot see external changes |
| Effect does nothing | confirm the strip is on; effects render regardless of power state but an off strip shows nothing |
| Scribble pattern looks wrong | check `sensor.<name>_led_count`; every bitmap is sized from it |
| Discovery finds 2 of 3 | re-run; replies are lossy by design |
| Timer set but never fires | sync the clock first — a drifted clock shifts every timer |

## Diagnosing the one field that misleads

The state frame's pattern byte is not "the effect" (protocol doc §3.1). The
light entity exposes it as the `pattern` attribute, so you can check what the
device really reports without a capture:

```bash
curl -s -H "Authorization: Bearer $HA_TOKEN" \
  http://192.168.1.10:8123/api/states/light.soffit_west \
  | python3 -m json.tool | grep -A2 pattern
```
