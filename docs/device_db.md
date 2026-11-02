# Device DB analysis — `AK001-ZJ21413` / `0xB6` / `ZG-BL-UFO`

Target: Zengge / Surplife **`AK001-ZJ21413`**, protocol/model number **`0xB6`** (= 182 decimal),
MODEL_INFO **`ZG-BL-UFO`**, sold as "Surplife Outdoor Permanent Lighting".

Analysis of the shipped Surplife.app bundle (macOS, app bundle dated 2026-03-22).

**Legend** — every factual statement below is tagged:

| Tag | Meaning |
|---|---|
| **verified** | read directly out of a shipped artifact; file path + byte offset given |
| **inferred** | conclusion drawn from verified facts, not stated verbatim anywhere |
| **unknown** | the artifacts do not contain the information |

Raw extracts: `docs/evidence/device_db_AK001-ZJ21413.json`, `docs/evidence/all_products_summary.json`,
`docs/evidence/command_family_coverage.json`, `docs/evidence/lmdb_dump.json`.
Reproducer: `docs/evidence/extract_device_db.py`.

---

## 1. Summary

- **The bundle has no `AK001-ZJ21413` entry, and no `ZG-BL-UFO` string, anywhere.** All three shipped
  databases are keyed by *numeric* `productId` only. The only `AK001-*` strings in the whole app are
  three Dart AOT string slots: `AK001-ZJ2`, `AK001-ZJ21`, `AK001-ZJ215` — none of which is
  `AK001-ZJ21413`. **verified** (`App` @ 75562672, 77854848, 79570000).
- **`0xB6` = 182 decimal, and `productId: 182` *does* exist**, in the JS bundle and in both JSON
  device files. This is the entry the task is about, and it is fully present. **verified**
  (`60_surplife.js` @ 194495; `wifi_devices.json` @ 265167; `ble_devices.json` @ 266405).
- **The product is a `symphonyE1` / `hc3` "symphony line" pixel-strip controller**: 41 functions in the
  JS DB (33 in the older JSON DB), 11 UI tabs, protocols `common` + `common1_0` + `common2_0` +
  `symphonyE1`, state protocol `wifibleSymphony`. **verified**.
- **`hexCmdForms` is absent for productId 182 in every artifact.** Not empty — *absent* in the JS entry,
  and present-but-`{}` in both JSON entries. The app instead routes this product through the
  `symphonyE1` protocol, whose frames are built in Dart code, not in the DB. **verified**.
- **`E1 21` / `E1 22` / `E1 23` / `E1 26` and `E0 05` appear in no artifact.** `E0 06` appears exactly
  once, as the `get_timer_list_v2` command form in `wifi_dp_cmd.json` — the only one of the six
  families the DB actually documents. **verified** (byte scans, `command_family_coverage.json`).
- **The closest sibling is productId 560**, which has 40 of 41 functions and lacks only
  `use_ble_music_mode`. **verified** (`all_products_summary.json`).

---

## 2. The `AK001-ZJ21413` / productId 182 entry

### 2.1 Identity block

The identity block is identical in all three sources. **verified**:

`60_surplife.js` byte 194495 (`{productId:182,icon:"enum://dengdai",category:"zm",categoryCode:"hc3",…`):

```json
{
  "productId": 182,
  "icon": "enum://dengdai",
  "category": "zm",
  "categoryCode": "hc3"
}
```

- `categoryCode: "hc3"` — **verified**. `hc*` is the family the app treats as "symphony/pixel" devices;
  the JS itself branches on it: `_getConfigDevices(e,c){if(e.categoryCode?.startsWith("hc"))…`
  (**verified**, `60_surplife.js` @ 424724).
- `entityType: "TCP_WF/BLE"` in the LMDB — **verified** for the *schema* (`data.mdb` table
  `ZGDeviceEntity`, key `0x…0b`), but productId 182 itself is **not** in that table (see §5). The JS
  bundle registers 182 under **both** `wifiDevices` and `bleDevices` tables — **verified**
  (`60_surplife.js` @ 194495 is the `wifiDevices` record; the JSON equivalents are
  `wifi_devices.json` @ 265167 and `ble_devices.json` @ 266405).
- The two JSON entries for 182 are **byte-for-byte the same 5318-byte object** — **verified** (both
  spans are exactly 5318 bytes and their JSON is identical).

### 2.2 `functions` — the app's own vocabulary (41 entries, verbatim)

Source of truth: `60_surplife.js` byte 194495, object spans bytes **194494–195641** (1148 bytes).
Order is verbatim as shipped. `deviceMinVer` shown where the entry overrides the global default.

| # | function code | type | deviceMinVer | meaning (from JSON `name`/`desc`) |
|---:|---|---|---|---|
| 1 | `use_ble_music_mode` | Hex | 20 | BLE music mode (only on this product + 5 siblings) |
| 2 | `symphony_scene_v2` | Hex | — | symphony scene v2 |
| 3 | `smart_ai` | Hex | — | AI effect generation |
| 4 | `sbl_dt_symphony` | Hex | — | symphony "digital twin"/distribution |
| 5 | `rgb_w_mix` | Hex | — | rgbw混光 (RGBW mix) |
| 6 | `multiple_screen_data` | Hex | 2 | 幻彩联屏 (multi-screen sync) |
| 7 | `firmware_data` | Hex | — | 固件升级 |
| 8 | `timer_data` | Hex | — | 设置定时 |
| 9 | `state_upload` | Hex | — | 状态上报 |
| 10 | `state_upload_v2` | Hex | — | 状态上报2.0 |
| 11 | `function_setting` | Hex | 9 | 功能设置 |
| 12 | `function_inquiry` | Hex | — | 功能查询 |
| 13 | `set_time` | Json | — | 校时命令, year从2000年开始 |
| 14 | `control_data` | Hex | — | 透传命令 (pass-through) |
| 15 | `switch_led_v2` | Json | — | 统一命令的开关 |
| 16 | `switch_led_v3` | Json | — | 统一命令2.0的开关 |
| 17 | `bright_value_v2` | Json | — | 统一命令调节亮度 |
| 18 | `timer_data_v2` | Hex | — | 统一命令设置定时2.0 |
| 19 | `colour_data_v2` | Hex | — | 统一命令调rgb |
| 20 | `colour_data_v3` | Hex | — | 统一命令2.0调rgb |
| 21 | `switch_led` | Json | — | 开关, 老版本 |
| 22 | `get_time` | Hex | — | 获取设备时间 |
| 23 | `get_timer_list` | Hex | — | 获取定时列表 |
| 24 | `get_timer_list_v2` | Hex | — | 统一命令2.0定时列表 |
| 25 | `wake_up` | Hex | — | 唤醒 |
| 26 | `wake_up_inquire` | Hex | — | 唤醒查询 |
| 27 | `fall_asleep` | Hex | — | 入睡 |
| 28 | `fall_asleep_inquire` | Hex | — | 入睡查询 |
| 29 | `countdown` | Hex | — | 倒计时 |
| 30 | `music_data_hc3` | Hex | — | 新幻彩音乐模式命令 |
| 31 | `custom_data_hc3` | Hex | — | 幻彩自定义模式 |
| 32 | `linkage_hc3` | Hex | — | 幻彩联动模式 |
| 33 | `static_colorful_hc3` | Hex | — | 幻彩多彩静态模式 |
| 34 | `smear_hc3` | Hex | — | 幻彩涂抹 (smear/doodle) |
| 35 | `set_wiring_preview` | Hex | — | 统一命令2.0排序设置-预览 |
| 36 | `smear_hc3_v2` | Hex | 3 | 幻彩涂抹V2 |
| 37 | `microphone_V2` | Hex | — | 硬件麦克风 |
| 38 | `ic_RGBW_TM1814` | Hex | 9 | RGBW_TM1814 driver IC |
| 39 | `symphony_scene_hc3` | Hex | — | symphony scene (hc3) |
| 40 | `symphony_mode_white` | Hex | — | white mode for symphony |
| 41 | `symphony_music_mode_v2` | Hex | 10 | symphony music mode v2 |

**verified** for all 41 rows: `60_surplife.js` @ 194494–195641. `deviceMinVer` values come from the
per-product override in the same object; the `type` column comes from the global `functions` schema
table (`60_surplife.js` @ 44584), joined by `code`.

The **older JSON DB** (`wifi_devices.json` @ 265167, 5318 bytes) has **33** of these — the same list
minus the 8 newest: `use_ble_music_mode`, `symphony_scene_v2`, `smart_ai`, `sbl_dt_symphony`,
`ic_RGBW_TM1814`, `symphony_scene_hc3`, `symphony_mode_white`, `symphony_music_mode_v2`.
**verified** (set difference computed in `all_products_summary.json`).

> Note: the JSON entries carry human-readable `name`/`desc` (Chinese) that the minified JS bundle omits.
> The Chinese glosses in the table above are quoted from `wifi_devices.json` @ 265167–270485.

### 2.3 `protocols` / `stateProtocol`

**verified**, `60_surplife.js` @ 194495 (same values in `wifi_devices.json` @ 265167):

```json
"protocols": [
  {"name": "common",      "deviceMinVer": 0},
  {"name": "common1_0",   "deviceMinVer": 0},
  {"name": "common2_0",   "deviceMinVer": 0},
  {"name": "symphonyE1",  "deviceMinVer": 0}
],
"stateProtocol": [
  {"name": "wifibleSymphony", "deviceMinVer": 0}
]
```

The JSON DB adds the descs: `symphonyE1` = "幻彩统一命令" (*symphony unified command*),
`wifibleSymphony` = "统一命令2.0 灯具标准状态" (*unified command 2.0 standard lamp state*).
**verified** (`wifi_devices.json`, the 4 protocol objects preceding byte 265167).

`common`, `common1_0`, `common2_0` are the legacy families (whose command forms *are* in
`wifi_dp_cmd.json`). `symphonyE1` is the family this product actually uses — and it has **no command
forms in any DB file**.

### 2.4 `hexCmdForms` — ABSENT

This is the single most important negative result.

| Artifact | `hexCmdForms` for productId 182 | Evidence |
|---|---|---|
| `60_surplife.js` | **key absent entirely** (not `{}`) | **verified** — the 182 object has exactly 7 keys: `productId, icon, category, categoryCode, functions, protocols, stateProtocol` |
| `wifi_devices.json` | **`{}`** (present, empty) | **verified** @ 265167–270485 |
| `ble_devices.json` | **`{}`** | **verified** @ 266405–271723 |
| `data.mdb` | table does not exist | **verified** — LMDB has no `HexCmdForm` table (see `lmdb_dump.json`) |

So **there are no hex command templates for this product in the shipped DB** — complete frames,
fragments, or otherwise. Nothing to quote.

This is not an accident of this one product. Across all 211 `wifiDevices` entries in the JS bundle,
**100 declare `symphonyE1` and 0 of them have a non-empty `hexCmdForms`**; the 66 products that *do*
have `hexCmdForms` are all non-`symphonyE1` (111 non-symphony products, 66 with forms).
**verified** (`all_products_summary.json`).

**inferred**: the `symphonyE1` command set is generated procedurally in Dart (the AOT binary contains
53 distinct `*Symphony*Command*` / `buildSymphony*HEXCommand` symbols, e.g.
`buildSymphonyControllerHEXCommand`, `buildSymphonyCustomHEXCommand`, `buildSymphonyMusicHEXCommand`,
`sendSymphonyCommand`). **verified** those symbols exist (`App`, e.g. `sendSymphonyCommand`);
**inferred** that they are what produces the wire bytes for this product.

### 2.5 `dpCode` vocabulary

**The `dpCode` concept exists in the DB, but productId 182 has none.**

- `dpCode` is a key inside `responseHexDescribes` entries, i.e. it labels *inbound* state frames, not
  outbound commands. **verified** (`wifi_dp_response.json` @ 11 = `"dpCode": "state_upload"`,
  @ 191 = `"get_time"`, @ 864 = `"get_symp_config_v2"`).
- The full `dpCode` vocabulary shipped in `wifi_dp_response.json` is only 6 entries — **verified**,
  complete file (1035 bytes):

| dpCode | hexForm | length | flags |
|---|---|---|---|
| `state_upload` | `81{devType}{open}{mode}{zg://wiring_sort}{speed}{color}{color}{color}{warm}{ver}{cool}{flag}{cs}` | 14 | idx0 flag129 |
| `get_time` | `{from}1114{year}{month}{day}{hour}{minute}{sec}{week}00` | 12 | idx1 flag17, idx2 flag20 |
| `get_mic_info` | `{from}72{open}{sensitivity}{cs}` | 5 | idx1 flag114 |
| `AT_Rsp` | `""` (AT command reply) | — | idx0 43, idx1 111, idx2 107 |
| `get_symp_config` | `63{pointCount}{pointCount}{icType}{code_H_1}{code_L_1}{code_H_0}{code_L_0}{resetCode}{resetCode}{sort}{cs}` | 12 | idx0 flag99 |
| `get_symp_config_v2` | `{supportType}63{pointCount}{pointCount}{part}{part}{icType}{sort}{musicPart}{musicPart}{cs}` | 11 | idx1 flag99 |

- **productId 182's `responseHexDescribes` is absent in the JS bundle and `[]` in both JSON files.**
  **verified**. Only 20 of 211 JS wifi products have a non-empty `responseHexDescribes`, and 182 is
  not one of them. So the app has **no declared inbound-state parser** for this device in the DB.

There is **no `work_mode`, `switch_led`-style Tuya/DP vocabulary** in these artifacts at all —
`work_mode` appears **0 times** in the entire `flutter_assets` tree. **verified**
(`grep -rl work_mode .../flutter_assets` returns nothing; `grep -c work_mode 60_surplife.js` → 0).
The function codes in §2.2 *are* the app's vocabulary; there is no second `dpCode` layer for commands.

### 2.6 Panel / UI schema

**verified**, `60_surplife.js` @ 66387 and `wifi_device_panel.json` @ 10568 (640-byte object, identical):

```json
{
  "productId": 182,
  "type": "symphony_line",
  "tab_ui": [
    {"pageProperty": "colorful",   "index": 0,  "deviceMinVer": 0},
    {"pageProperty": "scene",      "index": 1,  "deviceMinVer": 0},
    {"pageProperty": "carousel",   "index": 2,  "deviceMinVer": 0},
    {"pageProperty": "smear",      "index": 3,  "deviceMinVer": 0},
    {"pageProperty": "musicMic",   "index": 4,  "deviceMinVer": 0},
    {"pageProperty": "power",      "index": 5,  "deviceMinVer": 0},
    {"pageProperty": "timing",     "index": 6,  "deviceMinVer": 0},
    {"pageProperty": "settings",   "index": 7,  "deviceMinVer": 0},
    {"pageProperty": "linkage",    "index": 8,  "deviceMinVer": 0},
    {"pageProperty": "white",      "index": 9,  "deviceMinVer": 0},
    {"pageProperty": "layout",     "index": 10, "deviceMinVer": 0}
  ]
}
```

`type: "symphony_line"` — 11 tabs. This is the **full extent** of the DB's UI description: tab names
and order. There is **no** colour-picker schema, **no** effect list, **no** segment editor geometry in
the DB. The strings `segment`, `Segment`, `color_segment`, `effect` appear **0 times** in
`60_surplife.js`. **verified**.

Notably `layout` (index 10) is present — this is the wiring/point-order editor, matching the
`set_wiring_preview` and `sbl_dt_symphony` functions.

### 2.7 Driver-IC compatibility (a genuine constraint on this product)

`symphonyICs` in the generated symphony config lists **10 ICs that explicitly exclude productId 182**
via `wireType.invalidPid: [182, 183, 86, 103]`. **verified** — the exact literal
`invalidPid:[182,183,86,103]` occurs 10 times in `60_surplife.js`, once per excluded IC, first at
byte 422150:

Excluded for 182: `{mc_universal} RGBW` (5), `RGBW_TM1814` (8), `AS1720(RGBW)` (15),
`SM16705P(RGBW)` (16), `P9873A(RGBW)` (17), `UCS7604(RGBW)` (20), `WS2805(RGBCW)` (21),
`{mc_universal} RGBCW` (9), `WS2815B(RGBW)` (23), `SM16714(RGBW)` (25).

**inferred**: 182 is a 3-channel (RGB, not RGBW/RGBCW) controller — the excluded set is exactly the
RGBW/RGBCW wire types, so the app hides those IC choices in the UI for this product. This sits oddly
against 182 declaring `rgb_w_mix` (RGBW mix) and `ic_RGBW_TM1814`; the data is genuinely inconsistent
here, and I record it rather than resolve it.

The product **is** present in `RGBW_TM1814Config` and `configDevices` for both `TCP_WF` and `BLE`
entity types — **verified** (`symphonyConfig.getConfigJson('surplife')`). Note this is a *different*
mechanism from `invalidPid`, not a contradiction of it: those two lists are generated by function-flag
predicates, e.g. `_RGBW_TM1814Config(e,c){if(e.functions.find(e=>"ic_RGBW_TM1814"===e.code))return
{productId:e.productId,entityType:c}}` (**verified**, `60_surplife.js` @ 425296) — so 182 appears
there *because* it declares `ic_RGBW_TM1814`. `invalidPid` only filters the user-facing IC picker.

---

## 3. Command-family coverage

Raw byte scan, case-insensitive, over each artifact. `present` = at least one byte match.
Full data with offsets: `docs/evidence/command_family_coverage.json`.

| Family | `60_surplife.js` | `wifi_devices.json` | `wifi_dp_cmd.json` | `wifi_dp_response.json` | `wifi_device_panel.json` | `data.mdb` | Dart `App` binary |
|---|---|---|---|---|---|---|---|
| `E1 21` | absent | absent | absent | absent | absent | absent | **absent** (only false positive `#ffiClosure121`) |
| `E1 22` | absent | absent | absent | absent | absent | absent | absent (only PNG pixel data) |
| `E1 23` | absent | absent | absent | absent | absent | absent | absent (only PNG pixel data) |
| `E1 26` | absent | absent | absent | absent | absent | absent | absent |
| `E0 05` | absent | absent | absent | absent | absent | absent | absent |
| `E0 06` | absent | absent | **present** | absent | absent | absent | **absent** (only `common_e006`, a UI key) |

**Verified** by `command_family_coverage.json`. Every non-`wifi_dp_cmd.json` hit was re-read in context
and rejected as a false positive: `e121` in the Dart binary is the symbol `#ffiClosure121`, `e006` is
the localisation key `common_e006`, `e122`/`e123` land in PNG pixel data, and `E02F0100`/`E023` are
unrelated AOT string slots. The only genuine command opcode found is `e006` (and it is
`get_timer_list_v2`, not a family member of interest here).

### What `wifi_dp_cmd.json` *does* contain

Complete file, 31 entries, 2743 bytes. **verified**. This is the legacy `common*` command table — it
applies to the `common`/`common1_0`/`common2_0` protocols that 182 also declares, but **it is keyed by
function name, not by productId**, so it is the best available proxy for the legacy half of this
device's protocol.

| key | `cmdForm` | byte offset |
|---|---|---|
| `state_upload` | `818A8B96` | — |
| `bright_value_v2` | `3b010000{value}00{value}{delay}{delay}{delay}{gradient}{gradient}` | — |
| `switch_led` | `71{open}0f` | — |
| `switch_led_v2` | `3b{open}0000000000{gradient}{gradient}{gradient}{delay}{delay}` | — |
| `symp_app_volume` | `74{volume}` | — |
| `symp_get_mic_info` | `720072` | — |
| `symp_scene_v2` | `42{model}{speed}{bright}` | — |
| `twinkle_data` | `3A{exeType}{exeValue}{exeValue}{exeValue}{lightTime}{slakeTime}{count}{finishState}{transitionTime}` | — |
| `music_data_v2` | `36{exeType}{exeValue}{exeValue}{exeValue}{volume}{transitionTime}{transitionTime}` | — |
| `get_timer_list` | `222a2b` | — |
| `get_custom` | `525a5b` | — |
| `get_time` | `111a1b` | — |
| `get_mic_info` | `7200` | — |
| `get_mic_info_v2` | `7500` | — |
| `get_symp_fixed_data` | `444a4b` | — |
| `get_symp_config_v2` | `631221f0` | — |
| `sdd_get_colour_data` | `b0babb` | — |
| `set_time` | `1014{year}{month}{day}{hour}{minute}{sec}{week}00` | — |
| `get_lver` | `41542b4c5645520d` (AT+LVER\r) | — |
| `reset` | `41542b52454c440d` (AT+RELD\r) | — |
| `candle_data` | `39{exeType}{exeValue}{exeValue}{exeValue}{speed}{bright}{extent}` | — |
| `scene_data_v2` | `38{model}{speed}{bright}` | — |
| `temp_value_v2` | `3bb1000000{cct}{bright}00001e0000` | — |
| `function_inquiry` | `2b2c2d` | — |
| `switch_led_v3` | `e001{preview}3b{open}0000000000{gradient}{gradient}{gradient}{delay}{delay}` | 2172 |
| `scene_data_v3` | `e002{preview}{model}{speed}{bright}` | 2299 |
| `fall_asleep_inquire` | `e00a` | 2391 |
| `wake_up_inquire` | `e008` | 2467 |
| `get_timer_list_v2` | **`e006`** | 2545 |
| `state_upload_v2` | `ea818a8b` | 2622 |
| `countdown` | `e00d` | 2695 |

**Are these complete frames or fragments?** **Fragments.** Every `cmdForm` here is a *body* template
with `{placeholder}` slots and no header/length/checksum — the `needChecksum: true` flag on 20 of the
31 entries means a checksum byte is appended at runtime, `needSetOnline: true` on 7 means a session
preamble is sent first, and 2 are `ATCmd: true` (raw AT strings, the only complete frames in the file).
**verified** (the flags are in the file). The frames on the wire are
`cmdForm` + runtime-substituted placeholders + checksum.

Note the **`E0` sub-family that *is* present**: `e001` (switch v3), `e002` (scene v3), `e006`
(get timer list v2), `e008` (wake-up inquire), `e00a` (fall-asleep inquire), `e00d` (countdown).
These are the only `E0 xx` opcodes in the entire bundle. **verified**.

Also note `wifi_dp_response.json` is *inbound* framing and uses a leading `{from}`/`{supportType}`
source byte plus a trailing `{cs}` checksum — i.e. the same fragment convention, different direction.

---

## 4. Sibling comparison

Closest siblings by Jaccard similarity of function-code sets, computed over the 211 `wifiDevices`
entries in `60_surplife.js`. Full ranking: `all_products_summary.json` → `closest_siblings_js_wifi`.

| productId | #funcs | similarity | missing vs 182 | extra vs 182 |
|---|---:|---:|---|---|
| **182** | **41** | 1.00 | — | — |
| 560 | 40 | 0.98 | `use_ble_music_mode` | — |
| 537 | 38 | 0.93 | `use_ble_music_mode`, `symphony_scene_v2`, `smart_ai` | — |
| 543 | 38 | 0.93 | `use_ble_music_mode`, `symphony_scene_v2`, `smart_ai` | — |
| 217 | 37 | 0.90 | `use_ble_music_mode`, `symphony_scene_v2`, `smart_ai`, `sbl_dt_symphony` | — |
| 526 | 37 | 0.90 | `use_ble_music_mode`, `symphony_scene_v2`, `smart_ai`, `function_inquiry` | — |
| 183 | 38 | 0.88 | `ic_RGBW_TM1814`, `rgb_w_mix`, `sbl_dt_symphony`, `symphony_music_mode_v2` | `rgb_cw_mix` |
| 103 | 36 | 0.88 | `sbl_dt_symphony`, `smart_ai`, `symphony_music_mode_v2`, `symphony_scene_v2`, `use_ble_music_mode` | — |
| 133 | 36 | 0.88 | same as 103 | — |
| 550 | 39 | 0.86 | `function_inquiry`, `smart_ai`, `symphony_scene_v2`, `use_ble_music_mode` | `curtain_splicing`, `horizontal_flip` |
| 184 | 39 | 0.82 | `ic_RGBW_TM1814`, `rgb_w_mix`, `sbl_dt_symphony`, `symphony_mode_white`, `symphony_music_mode_v2` | `ic_2811FIC`, `ic_ASLI4040`, `pwm_w` |
| 170 | 44 | 0.79 | `sbl_dt_symphony`, `rgb_w_mix`, `symphony_mode_white` | `ic_ASLI4040`, `ic_2811FIC`, `set_wiring_v2`, `short_time_list`, `game`, `symphony_scene_alexa` |

All **verified** (`all_products_summary.json`).

**Key structural facts:**

- **182 is the only product with `sbl_dt_symphony` + `smart_ai` + `use_ble_music_mode` + `rgb_w_mix`
  together.** `sbl_dt_symphony` is shared with only 5 other products (526, 537, 543, 550, 560);
  `use_ble_music_mode` with only 5 (170, 183, 184, 564, 578). **verified**.
- **No sibling has a function that 182 lacks in a way that suggests a missing capability** — the
  differences are all *other* product lines' extras (`pwm_w`, `curtain_splicing`, `horizontal_flip`,
  `game`, `ic_ASLI4040`, `ic_2811FIC`), not symphony-core functions. 182 is essentially the maximal
  `symphonyE1` wifi product. **inferred**.
- **182 has no functions that are unique to it** — every one of its 41 functions is shared with at
  least 5 other products. **verified** (frequency over the 211 `wifiDevices` entries only):

| function | # of 211 wifi products that have it |
|---|---:|
| `use_ble_music_mode` | 6 |
| `sbl_dt_symphony` | 6 |
| `symphony_mode_white` | 11 |
| `symphony_scene_v2` | 14 |
| `rgb_w_mix` | 21 |
| `symphony_music_mode_v2` | 30 |
| `smear_hc3_v2` | 50 |
| `ic_RGBW_TM1814` | 51 |
| `smart_ai` | 58 |
| `linkage_hc3` | 99 |
| `smear_hc3` | 100 |
| `static_colorful_hc3` | 105 |
| `custom_data_hc3` | 104 |
| `multiple_screen_data` | 111 |
| `symphony_scene_hc3` | 121 |
| `music_data_hc3` | 131 |
| `microphone_V2` | 140 |
| `set_wiring_preview` | 149 |
| `countdown` | 152 |
| `timer_data` | 158 |
| `switch_led_v3` | 160 |
| `colour_data_v3` | 171 |
| `colour_data_v2` | 177 |
| `firmware_data` | 181 |
| `function_inquiry` | 186 |
| `wake_up` | 188 |
| `wake_up_inquire` | 188 |
| `fall_asleep` | 189 |
| `fall_asleep_inquire` | 189 |
| `get_timer_list_v2` | 192 |
| `function_setting` | 193 |
| `bright_value_v2` | 196 |
| `state_upload_v2` | 197 |
| `switch_led_v2` | 202 |
| `get_timer_list` | 205 |
| `set_time` | 210 |
| `get_time` | 210 |
| `state_upload` | 211 |
| `control_data` | 211 |
| `switch_led` | 211 |
| `timer_data_v2` | 188 |

(Sorted ascending except the final `timer_data_v2` row, placed last for readability; all values
**verified** from `all_products_summary.json`.)

- **The biggest capability gaps *in the other direction*** — functions that many siblings have and 182
  lacks (152 distinct codes in total). Top of the list: `candle_data` (62 siblings), `set_wiring_v2`
  (60), `custom_dynamic` (57), `candle_data_v2` (57), `scene_data_v3` (55),
  `disable_same_time_online` (55), `music_data_v2` (52), `temp_value_v2` (51), `short_time_list` (51),
  `energize_mode` (47). These are all **non-symphony** feature lines (candle/twinkle, CCT, v3 scene,
  custom dynamic) — consistent with 182 being a pure symphony-line product rather than a general
  RGB+CCT lamp. **verified** counts; **inferred** interpretation.

- **Panel is not a differentiator**: 183, 184, 186, 86, 83 all have the identical 11-tab
  `symphony_line` panel. Only 170 differs (`game` + `alexa` instead of `smear` + `musicMic`).
  **verified**.

### LMDB sibling data (a *different*, older DB generation)

The LMDB `data.mdb` is **not** a superset or subset — it is a separate snapshot with 141 devices and
**no productId 182 at all**. **verified** (`lmdb_dump.json`, `table_sizes.ZGDeviceEntity = 141`).
Its 52 `symphonyE1` products include 170, 171, 175, 181, 183, 184, 187, 188, 86, 83 — and also its
neighbours 185 and 187 — but not 182. **verified**.

**inferred**: `data.mdb` is a **stale snapshot** (`update.json` says version 27, 2025-06-04) that
predates productId 182's introduction; the JS bundle (built 2026-03-14) and the JSON assets are the
current data. This is corroborated by the fact that `data.mdb` also lacks 138 other JS productIds.

---

## 5. Gaps — what the DB does NOT tell us

Stated plainly, in order of how much they hurt:

1. **No mapping from `AK001-ZJ21413` or `ZG-BL-UFO` to productId 182.** The string
   `AK001-ZJ21413` occurs **0 times** in every artifact. `ZG-BL-UFO` occurs **0 times**. The only
   `AK001-*` strings in the app are `AK001-ZJ2`, `AK001-ZJ21`, `AK001-ZJ215` — **verified**
   (`App` @ 75562672 / 77854848 / 79570000; also `Surplife` @ 29780083 / 29786500 / 29798796).
   The identification `0xB6` → 182 is arithmetic (`0xB6 == 182`), and the step from
   `AK001-ZJ21413` → `0xB6` is **not** in these artifacts — it must come from the device's
   advertising/BLE handshake or a factory label. **unknown** from this bundle.
   *Caveat*: `AK001-ZJ2`/`AK001-ZJ21`/`AK001-ZJ215` are 11/12/13-char strings; `AK001-ZJ21413` is
   14 chars. In the Dart AOT string table, the 16-byte slot would fit `AK001-ZJ21413` exactly
   (14 + NUL). The three observed strings are **not** truncations of it, so this is not a
   `strings`-style artefact — the literal genuinely is absent. **verified** by raw byte read at each
   offset (each is NUL-padded to a 16-byte slot with the next record's `0x32` header byte following).
2. **No wire format for the `symphonyE1` protocol.** This is the product's primary protocol and it has
   zero command forms in any DB file. The `E1 21` / `E1 22` / `E1 23` / `E1 26` families the task
   asked about are **absent** — if they are real, they are produced by Dart code we did not decompile
   here, not by the DB.
3. **No `E0 05`.** Absent from every artifact. The `E0` family that *is* documented jumps
   `e001, e002, e006, e008, e00a, e00d` — there is no `e003/e004/e005/e007/e009`.
   **inferred**: `E0 05` is either unassigned or belongs to a code path not described by the DB.
4. **No `dpCode` set for 182.** Its `responseHexDescribes` is empty, so the DB does not say how the
   device reports state back. `state_upload` / `state_upload_v2` / `wake_up_inquire` /
   `fall_asleep_inquire` / `get_timer_list_v2` are *declared* as supported functions, but their
   response framing is only in the generic `wifi_dp_response.json` (6 generic dpCodes, none specific
   to symphony).
5. **No panel internals.** `tab_ui` gives 11 tab names. It does **not** give the colour picker model,
   the effect list, the scene catalogue, the smear/doodle canvas, the segment count, or the layout
   geometry. Strings `segment`, `effect`, `color_segment` are absent from the JS bundle.
   `segment20`/`color_segment_*` do exist elsewhere in the app (see the sibling evidence files in
   `docs/evidence/`, e.g. `segment20_validation.txt`) but **not** in the device DB.
6. **No firmware version gating per function beyond `deviceMinVer`.** `deviceMinVer` is a *DB schema*
   version (the bundle's own `homeDataProvideBundleInfo.version = "2"`), not a device firmware
   version. **inferred** from the field name and its use in `_setDeviceFunctions`.
7. **LMDB is stale and excludes 182**, so it cannot be used to cross-check 182's function list.
   `data.mdb` also lacks 138 of the 274 productIds present in the JS bundle. **verified**.
8. **No effect/scene IDs, no colour calibration, no white-point data** for 182 anywhere.

---

## 6. Reproducibility

```bash
# one-time
python3 -m venv /tmp/venv_lmdb
/tmp/venv_lmdb/bin/pip install lmdb

# run (defaults to /Applications/Surplife.app)
cd /Users/asxzy/src/surplife/docs/evidence
/tmp/venv_lmdb/bin/python extract_device_db.py

# or point at another install / skip LMDB
/tmp/venv_lmdb/bin/python extract_device_db.py --app /path/to/Surplife.app
/tmp/venv_lmdb/bin/python extract_device_db.py --no-lmdb
```

Requires `node` (to evaluate the JS bundle in a sandbox — the bundle is minified ES2015 and is far more
reliably read by executing it than by brace-matching) and `lmdb` for `data.mdb`.

### What the script does

| Step | Method |
|---|---|
| JS bundle | evaluates `60_surplife.js` in a `node:vm` context, extracts `bleDevices, wifiDevices, sigMeshDevices, sigMeshDevicesFVT, sigMeshGWDevices, wifiDevicesHikariya, bleDevicesHikariya, panel, functions, chip, symphonyIC, customProduct, deviceTableDepend, entityTypeTablesMap`, plus `symphonyConfig.getConfigJson('surplife')` |
| JSON assets | plain `json.load` of `wifi_devices.json`, `ble_devices.json`, `wifi_device_panel.json`, `wifi_dp_cmd.json`, `wifi_dp_response.json` |
| `data.mdb` | `lmdb` read-only; values are **FlatBuffers**, decoded by a ~40-line minimal table reader in the script (`FlatBuf` class). Field indices per table are hard-coded in `LMDB_SCHEMA` and were recovered from the schema tables LMDB stores under keys `0x…0001`–`0x…000d` |
| command families | raw case-insensitive byte scan, offsets recorded |
| provenance | byte offsets of every quoted record, written into `device_db_AK001-ZJ21413.json → provenance_offsets` |

### Outputs

| File | Contents |
|---|---|
| `device_db_AK001-ZJ21413.json` | full 182 entry from all 4 sources + command-family coverage + model-string search + byte offsets |
| `all_products_summary.json` | all 274 productIds across all tables, with function lists, counts, protocols, and Jaccard/missing/extra vs 182 |
| `command_family_coverage.json` | per-artifact × per-family status with byte offsets |
| `lmdb_dump.json` | all 7 decoded LMDB tables + schema tables |

Note: `all_products_summary.json` is ~1 MB and `lmdb_dump.json` is ~1 MB because they carry every
product's full function list and every LMDB row. That is intentional — it is what makes sibling
diffing possible without re-running the extractor.

The script is **read-only** with respect to the `.app` bundle and writes only into its own directory.

### Notes on method / things that would fool a naive scan

- `strings` on the Dart AOT binary fabricates trailing characters by merging a string with the next
  record's header byte. The three `AK001-*` strings were re-read at raw byte offsets and are genuine
  (16-byte NUL-padded slots). The earlier evidence files in this directory
  (`aot_customeffectft_raw.txt`, `README.txt`) document that same trap.
- Byte-grep for `e121`/`e122`/`e123`/`e005`/`e006` hits PNG and TTF data. All hits outside
  `wifi_dp_cmd.json` were opened in context and rejected as false positives; the rejections are listed
  in §3.
- `0xB6` is **not** a string in any artifact — it is a numeric protocol number. Searching for the
  literal text `0xB6` returns 0 hits everywhere, which is expected, not evidence of absence of the
  product.
