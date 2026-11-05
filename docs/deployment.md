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

### Marketplace (Home Assistant 2026.11 and later)

Home Assistant 2026.11 replaced the HACS custom integration with the built-in
**Marketplace** integration, which installs the same kind of GitHub repository
without anything extra to set up.

1. **Settings → Devices & Services → Add Integration → Marketplace**
2. Add `https://github.com/asxzy/surplife` as a custom repository, category
   **Integration**.
3. Install, then restart Home Assistant.

### HACS (older Home Assistant)

1. HACS → Integrations → ⋮ → **Custom repositories**
2. `https://github.com/asxzy/surplife`, category **Integration**
3. Install, then restart Home Assistant.

### Deploying a new release from this checkout

The store owns the files under `/config/custom_components/surplife`, so writing
them by hand leaves its record disagreeing with the disk and the next install
undoes the change. Publish the release, then let the store install it:

```bash
set -a && . ./.env && set +a
export HA_URL=http://192.168.1.10:8123     # not in .env; it is per-box
python3 tools/ha_deploy.py           # refresh, install, restart, verify
python3 tools/ha_deploy.py --status  # report only, change nothing
```

It needs only `HA_URL` and `HA_TOKEN` from `.env` — a long-lived access token,
no shell access to the box — so it works against a stock Home Assistant OS
install. The script detects which store is running: Marketplace if present,
otherwise HACS. The two speak different websocket dialects, and the three names
that differ are kept in one `Store` object so nothing else has to care:

| Step | Marketplace | HACS | Why it is needed |
|---|---|---|---|
| refresh | `marketplace/repository/refresh` | `hacs/repository/refresh` | the store caches its idea of the latest release, so one published minutes ago looks absent without this |
| install | `marketplace/repository/install` | `hacs/repository/download` | fetches the `surplife.zip` asset and extracts it into the domain directory |
| restart | — | — | the running process still holds the old modules, so even a same-version reinstall needs a restart |
| wait for a full boot | — | — | `/api/config` answers tens of seconds before platforms finish loading, so the entity count is the real signal |

Two websocket details cost real debugging time and are now handled:

- **Message ids must strictly increase.** Home Assistant rejects a repeated or
  lower id with `{"code": "id_reuse"}`. The store probe runs first on the same
  connection, so ids cannot be constants; `_Ids` hands out a monotonic counter.
- **`max_msg_size=0` is required.** `repository/info` includes the repository
  README, and this one is large enough to exceed aiohttp's 4 MiB default, which
  fails as `MESSAGE_TOO_BIG` rather than as anything obviously size-related.

The script exits non-zero when the store reports a version that does not match
the deploy target, or when any `surplife` entry is not `loaded`. Both cases are
worth failing loudly: the store will happily report a release as installed while
the files on disk are something else, and a half-loaded integration looks like an
empty integration rather than an error.

### Deploying one commit, without cutting a release

A stable release means "this passed the tests". Deploying work in progress should
not create one, so a commit is published as a **prerelease** and installed by
tag:

```bash
python3 tools/publish_dev_tag.py             # tag HEAD v0.1.7-dev1, publish prerelease
python3 tools/publish_dev_tag.py --dry-run   # show the tag, publish nothing
python3 tools/ha_deploy.py --tag v0.1.7-dev1 # install that exact commit
```

The tag is `v<manifest version>-dev<n>`, counting up from the highest existing
`-dev` tag for that version, so a second commit gets `-dev2` instead of moving a
tag that is already published. `publish_dev_tag.py` refuses to run with
uncommitted changes to tracked files, because the tag would then not match the
code it names.

**Why a tag and not a bare commit hash.** With `zip_release: true` the store only
fetches *release assets* — it builds `releases/download/<ref>/surplife.zip`, and
a SHA is not a release, so there is nothing to download. It does understand
`archive/<sha>.zip`, but only on its repo-archive path, which `zip_release`
disables. Publishing the commit as a prerelease keeps the predictable archive
layout and still deploys exactly one commit.

Prereleases are not advertised as an upgrade, so a dev deploy does not offer
itself as an update. `--tag` verifies that the installed version equals the tag
you asked for, so a silent fallback to the latest stable release is a failure
rather than a success.

> Do not deploy by copying files over SSH or by running code inside Home
> Assistant. Both work once and then drift, because the next install overwrites
> the directory from its own record.

### Manual

```bash
rsync -a custom_components/surplife/ \
    root@192.168.1.10:/config/custom_components/surplife/
```

Restart Home Assistant. Only for a box with neither Marketplace nor HACS; if a
store manages this integration, use the deploy script instead.

### What the store needs in a release archive

This integration sets `"zip_release": true` with `"filename": "surplife.zip"`, so
the store downloads that asset instead of the repository branch. It extracts the
archive **directly** into `/config/custom_components/surplife/` and never strips
a leading directory, so the archive must have the integration files at its
**root**:

```
manifest.json
__init__.py
coordinator.py
light.py
groups.py
protocol/
translations/
brand/
```

Do **not** wrap them in `custom_components/surplife/`. That produces

```
/config/custom_components/surplife/custom_components/surplife/__init__.py
```

Home Assistant looks only at `/config/custom_components/surplife/__init__.py`,
finds nothing, and never loads the integration — no entities, no services, no
config entry. The store still reports the release as installed, because it tracks
its own record rather than the files. The symptom is the confusing pair of "the
store says it is up to date" and "the integration is not there at all".

Build the asset with the checked-in script, which enforces the layout and drops
`__pycache__`, `*.pyc`, and AppleDouble `._*` files:

```bash
python3 tools/build_release.py            # -> dist/surplife.zip
python3 tools/build_release.py --check    # verify an existing archive
```

Attach `dist/surplife.zip` to the GitHub release named after the version in
`manifest.json`. `tests/test_release_layout.py` fails if the archive regresses to
the nested shape.

### The integration's icon

`brand/icon.png` and `brand/icon@2x.png` are served straight from disk for
custom integrations, so they travel in the archive and appear the moment the
integration is installed — no brands-repository pull request, no CDN cache to
wait out. Sizes follow the brands specification (`256x256` and `512x512`,
square, PNG); `tests/test_brand_assets.py` enforces that.

The artwork is the vendor's own app icon, taken from the Surplife iOS app
(bundle `com.zennge.magichome2`, 1.7.2) — the same icon a user sees on their
phone when they set the roof up. It is a square brand mark, so only icons ship;
the brands specification says `icon.png` doubles as the logo fallback.

Bump `manifest.json` when the icon changes. The archive is downloaded by tag, so
a new image inside an already-published tag never reaches anyone.

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

The app's grouping is cloud-side and does not exist on the local protocol, so it
is recreated as a Home Assistant device:

1. **Settings → Devices & Services → Add Integration → Surplife Permanent
   Lighting**.
2. Choose **Group controllers into one device** (shown once at least one
   controller exists).
3. Select the controllers and save.

The result is one device with the same controls as a single controller — power,
colour, W, effects, effect speeds, palettes, per-LED looks, scenes, clock sync
and refresh. Commands are forwarded to every member; state is derived from them
(on if any member is on, brightness is the mean of the lit strips, colour from
the first lit strip, effect the most common one). Palettes and effect parameters
are resolved once for the group, so every strip receives the same values.

**LED count** and **Timers** are the two diagnostic sensors a group does not
get: they describe one physical strip, so a group of three has no single honest
value. The group's **Mode** sensor reports what most of the roof is doing.

The `surplife.*` services accept the group entity and expand it, so holiday
scenes become one call:

```yaml
service: surplife.apply_preset
target:
  entity_id: light.soffit    # the group
data:
  preset: christmas
```

Available presets: `halloween`, `christmas`, `thanksgiving`, `canada_day`,
`valentines`, `new_years`, `warm_white`.

Each controller keeps its own device and its own full control set, including the
two per-strip diagnostics the group omits. Use **Reconfigure** on the group to
change membership,
or on a controller to correct its IP or name without losing entity ids. A stock
`light.group` helper also still works if you only want on/off and colour, but it
has no device page and the `surplife.*` services will not accept it.

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
| The store says up to date but no entities/services exist | the release zip is nested; see "What the store needs in a release archive" |
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
