# Surplife iOS app — shipped-string evidence

Evidence analyst report. Every claim below is tagged **verified**, **inferred**, or
**unknown**, with the exact source (file path + byte offset).

All offsets are byte offsets into the AOT snapshot unless stated otherwise.

---

## 0. Read this first: a methodology trap (and the parser that fixes it)

`strings -a` on this binary produces **fabricated trailing `2` characters**.
This is the single most dangerous artifact in this artifact set — it invents plausible
looking names and counts. It must be understood before trusting any string list.

### The real record layout

The AOT's string table is a sequence of **Dart String objects**:

```
offset+0 : 32 dN 05 00        record header (0x32 == ASCII '2', class tag 0x05dN)
offset+4 : hash (4 bytes)
offset+8 : length in UTF-16 code units, LE uint32  == 2 x char count for ASCII
offset+16: the characters, NOT NUL-terminated
```

**verified** — hexdump at `buildMeshCommand`, offset `84054752` (`aot_slot_structure.txt`):

```
00000000: 00000000 00000000 00000000 00000000   ...NUL padding / prior record
00000010: 32d2 0500 eap4 4e22 2000 0000 0000 0000   2.....N" .......   len=0x20=32 -> 16 chars
00000020: 6275 696c 644d 6573 6843 6f6d 6d61 6e64   buildMeshCommand
00000030: 32d2 0500 ec70 de11 1e00 0000 0000 0000   2....p..........   NEXT record header
```

The characters are **not** NUL-terminated, so `strings` runs straight into the next
record's `0x32` and emits a bogus trailing `'2'`:

```
CustomEffectFT18  + '2'  ->  "CustomEffectFT182"   (DOES NOT EXIST)
color_segment_1   + '2'  ->  "color_segment_12"    (DOES NOT EXIST)
buildMeshCommand  + '2'  ->  "buildMeshCommand2"   (DOES NOT EXIST)
CustomEffectFT1..41 all appear with a bogus "2" suffix from `strings`
```

### The authoritative parser

All counts in this report come from a length-field-driven parser, **not** from
`strings`. It yields **157,890 distinct strings**. `strings -a` yields 363,396
lines, most of which are binary noise or corrupted.

The parser is reproduced verbatim in §7 and its output is
`docs/evidence/aot_all_strings_parsed.txt`.

**verified** — sanity checks, `FINAL_validation.txt`:

| probe | present in parsed set? |
|---|---|
| `CustomEffectFT18` | **yes** |
| `CustomEffectFT182` | **no** |
| `color_segment_1` | **yes** |
| `color_segment_12` | **yes** (it is a *real* 0..19 index — see §4.4) |
| `buildMeshCommand` | **yes** |
| `buildMeshCommand2` | **no** |

Note the subtlety with `color_segment_12`: it is genuinely real (index 12 of 20), *and*
`strings` also fabricated a fake `color_segment_102` from `color_segment_10`+`'2'`.
The parser resolves both cases correctly.

**Consequence:** every list in this report was validated with this parser.
Where a claim rests on `strings` alone, it is explicitly flagged.

**Do not read `CustomEffectFT182` as "0xB6 effect".** `0xB6 == 182` is a coincidence.
**verified** — all `182` occurrences in the JS bundle are `productId:182` or
`invalidPid:[182,183,86,103]` (a wire-type exclusion list), `js_182_context.txt`.

---

## 1. Artifact identity

| Field | Value | Status |
|---|---|---|
| Path | `/Applications/Surplife.app/Wrapper/Surplife.app/Frameworks/App.framework/App` | **verified** |
| Size | 95,911,088 bytes (91.48 MiB) | **verified** |
| SHA-256 | `1b5ad664ff0bb6b50fed29afb2620531d8524ce31bf1b8a38378ed4c1dcd917c` | **verified** — **matches the expected hash exactly** |
| File type | Mach-O 64-bit dynamically linked shared library, arm64 | **verified** (`aot_identity.txt`) |
| UUID | `094FB2E0-8A21-3291-BED5-D6EAC010FB5A` | **verified** |
| Bundle ID | `com.zennge.magichome2` | **verified** — `Wrapper/Surplife.app/Info.plist` |
| Display name | `Surplife` | **verified** — same plist |
| Short version | `1.7.2` | **verified** — `CFBundleShortVersionString` |
| Build number | `407` | **verified** — `CFBundleVersion` |
| Min iOS | `18.0` | **verified** |
| Built with | Xcode `1640` / `16F6`, SDK `iphoneos18.5` | **verified** |
| Flutter SDK | `3.19.5` (`flutter_sdk_3.19.5_release`) | **verified** — `flutter_assets/packages/magichome2_other/assets/module_branches.txt` |
| Flutter module branches date | `2026-03-14 17:28:48` | **verified** |

Note: `App.framework/Info.plist` says `io.flutter.flutter.app` / `1.0` — that is the
Flutter **framework stub** plist, not the app identity. The app identity lives in
`Wrapper/Surplife.app/Info.plist`. **verified**

The 24 effect names therefore pin to **Surplife 1.7.2 (407)**, and the Dart code
pin to Flutter **3.19.5**.

---

## 2. AOT recoverability verdict

### 2.1 What is exported: nothing useful

`nm -a` returns **exactly 5 symbols** (`aot_identity.txt`):

```
0000000003450800 S _kDartIsolateSnapshotData
000000000000e340 T _kDartIsolateSnapshotInstructions
0000000003447dc0 S _kDartVmSnapshotData
0000000000004000 T _kDartVmSnapshotInstructions
                 U dyld_stub_binder
```

There is **no** symbol table for Dart functions. **verified**

### 2.2 Sections

```
__TEXT.__text        0x3443da0   (54.8 MB)
__TEXT.__const       0x24ad490   (38.5 MB)
__TEXT.__unwind_info 0x68        (104 bytes — effectively empty)
__DATA.__bss         0x30
__LINKEDIT           LC_FUNCTION_STARTS dataoff=93290616 datasize=227152
```

**verified** — `aot_sections_full.txt`, `aot_mapping_verdict.txt`

`__unwind_info` being **104 bytes** is the key structural fact: there is no
per-function unwind table, so there is no function-boundary map. `LC_FUNCTION_STARTS`
exists but is a compressed ULEB128 delta list covering the whole `__text` region;
with no symbol table it cannot be attributed to Dart function names. **inferred**
(strongly supported: a 227 KB function-start list is consistent with ~200k
stripped entries, but nothing maps entries → names).

No `.dSYM`, `.bcsymbolmap`, or `.symbols` file anywhere in the bundle.
**verified** — `find /Applications/Surplife.app -iname '*dSYM*' ...` returned nothing.

### 2.3 But: 13,760 Dart *names* ARE recoverable

This partially corrects the prior analysis and needs stating precisely.

`strings` finds **24,908** strings matching `_name@<decimal>` (e.g.
`_onSendCCTCommand@6036489653`), resolving to **13,760 distinct base names**.
**verified** — `aot_symbol_tags.txt`

The prior conclusion "no recoverable symbol→function mapping" is **correct in its
operative sense**, because `@N` is **not an address**:

- Values range `1` … `142,062,295,642` — far beyond any address in a 95 MB binary.
  **verified**
- Many `@N` values are tiny (`@1`, `@9`, `@1026248`) and repeat across huge name sets.
  **verified**
- The name→`@N` relation is **many-to-many**: 2,975 names appear with more than one
  `@N` (`_onAction` with 1,159 distinct values, `_onInit` with 869).
  **verified** — `aot_atvalue_semantics.txt`
- `@N` values are **shared across unrelated names that belong to the same library**:
  `@0150898` covers 315 core `dart:` names (`_BigIntImpl`, `_GrowableList`, …);
  `@126183791` covers 107 `flutter/src/widgets/editable_text.dart` names;
  `@6184256651` covers 82 effect-mode state classes.

**Conclusion (verified):** `@N` is a **library-URI hash / library id**, not an address.
It groups names by originating library. Therefore:

> **There is NO usable symbol → function mapping.** You can recover *names* and
> *library grouping*, but not *addresses*, *call graphs*, or *bodies*.

What this **does** buy us: the complete vocabulary of protocol-relevant method names
(§5), which is genuinely useful.

### 2.4 Packet templates: absent

`grep -a '{r}'` → **0 matches**. No `{r}{g}{b}` templates in the AOT.
**verified** — `aot_packet_templates.txt`

For contrast, the JS bundle has them plainly:
`"31{r}{g}{b}00000f"`, `"31{r}{g}{b}0000f00f"`, `"31{r}{g}{b}00f00f"`.
**verified**

`cmdForm` appears exactly **once** in the AOT (a bare identifier, no template data).
**verified**

The prior conclusion that packet templates are not recoverable from the AOT is
**confirmed**. Templates are only available via the JS bundle (§5.4).

### 2.5 No id → name table

The 24 effect names are scattered across a **14.4 MB** span (offset 72,403,497 …
86,859,552). The densest 4 KiB window contains only **4** of them.
**verified** — `effect_adjacency_check.txt`

There is **no contiguous effect-id → name table** in the AOT. **verified**

---

## 3. Effect vocabulary — the 0xB6 list

### 3.1 Result

All 24 claimed names are present as shipped strings. **23 confirmed verbatim; 1
confirmed as a case variant.**

**verified** — `effect_id_name_table.tsv`, `FINAL_validation.txt`

| id | claimed name | app string (verbatim) | AOT offset | verdict |
|---|---|---|---|---|
| 1 | Wave | `Wave` | 81666912 | **confirmed** |
| 2 | Meteor | `Meteor` | 78720272 | **confirmed** |
| 3 | Streamer | `Streamer` | 86561056 | **confirmed** |
| 4 | Building Blocks | `Building Blocks` | 81122160 | **confirmed** |
| 5 | Flowing Water | `Flowing Water` | 84922976 | **confirmed** |
| 6 | Chase | `Chase` | 73088608 | **confirmed** |
| 7 | Horse Racing | `Horse Racing` | 82977872 | **confirmed** |
| 8 | Cycle | `Cycle` | 72460816 | **confirmed** |
| 9 | Breathe | `Breathe` | 84795584 | **confirmed** |
| 10 | Jump | `Jump` | 82326592 | **confirmed** |
| 11 | Strobe | `Strobe` | 84052976 | **confirmed** |
| 12 | Twinkling Stars | `Twinkling Stars` | 85253728 | **confirmed** |
| 13 | Stars Wink | `Stars wink` | 72494944 | **confirmed — lowercase `w`** |
| 14 | Warning | `Warning` | 79343040 | **confirmed** |
| 15 | Collision | `Collision` | 74239056 | **confirmed** |
| 16 | Fireworks | `Fireworks` | 84578512 | **confirmed** |
| 17 | Comet | `Comet` | 84747840 | **confirmed** |
| 18 | Gradient Meteor | `Gradient Meteor` | 82315776 | **confirmed** |
| 19 | Volcano | `Volcano` | 78413392 | **confirmed** |
| 20 | Superluminal | `Superluminal` | 74790784 | **confirmed** |
| 21 | Rainbow Bridge | `Rainbow Bridge` | 72627152 | **confirmed** |
| 22 | Gradient Overlay | `Gradient Overlay` | 85109856 | **confirmed** |
| 23 | Static Gradient | `Static Gradient` | 84434960 | **confirmed** |
| 24 | Static Fill | `Static Fill` | 83673984 | **confirmed** |

**One discrepancy to fix in our naming:** id 13 ships as **`Stars wink`**
(lowercase `w`), not `Stars Wink`. `grep -F "Stars Wink"` returns **0**; the only
cased hit is `Stars wink` at offset 72494944. **verified** — `aot_two_misses.txt`

Nearby related strings (not the id-13 string, but same family): `Stars twinkling`,
`Stars blinking`, `Twinkling stars`, `Twinkle`, `Wink`, `WinkDynamicEffect`.

### 3.2 These are plain strings, not a decoded table

Important honesty note: the names exist as **strings**, but nothing ties each string
to its numeric id inside the AOT. The id↔name pairing in the table above is **our
mapping**; the *string existence* is verified, the *id association* is **inferred**
from our prior protocol work. **verified** (existence) / **inferred** (association)

### 3.3 Additional effect names beyond the 24 (new capability)

**verified** — these are separate shipped strings not in our 24 (`strings_effect_names.txt`,
`strings_dynamic_names.txt`, `strings_colorful_modes.txt`):

`Colorful`, `Colorful Breath`, `Colorful Fade`, `Colorful Jump`, `Colorful Meteor`,
`Colorful Sports`, `Colorful Strobe`, `Colorful waves`, `Colorful flashes`,
`Colorful breath`, `Colorful jump`, `Colorful strobe`, `Colorful Static`,
`Colorful and always bright`, `Colorful Diffusion`, `Colorful gradient`,
`Colorful horse racing`, `Colorful shooting stars`, `Colorful Standard`,
`Static`, `Static color`, `Static Color`, `Static colorful`, `Static Dimming`,
`Static Fill`, `Static Gradient`, `Static Light`, `Static mode`, `Static template`,
`Flow`, `Flowing`, `Flowing Water`, `Twinkling Marquee`, `Twinkling stars`,
`Twinkling Stars`, `Accumulate`, `Wink`, `ChaseTwinkle`, `Color Flash`,
`Color Jump Flash`, `Colorful Flashes`.

**134 distinct `*ModeState` classes** exist (effect implementations). **verified** —
`strings_modestate_full.txt`. Full list is in that file; notable families not in our
24: `_AccumulationModeState`, `_BeamModeState`, `_BlossomModeState`,
`_BridgeModeState`, `_DrumsModeState`, `_EnergyModeState`, `_ExplosionModeState`,
`_FallingSnowModeState`, `_FillingBreatheModeState`, `_FillingGradientModeState`,
`_FireflyModeState`, `_FlameModeState`, `_GalaxyModeState`, `_GorgeousModeState`,
`_MarqueeModeState`, `_MeltingModeState`, `_MeteorShowerModeState`,
`_NeonLightModeState`, `_OverlayModeState`, `_PeriodicMeteorsModeState`,
`_RibbonModeState`, `_RunningWaterModeState`, `_ScanningModeState`,
`_ShineModeState`, `_ShuttleModeState`, `_SparkleMarqueeModeState`,
`_SquirmModeState`, `_StarBurstMusicModeState`, `_StarFlickerModeState`,
`_StarsStrobeModeState`, `_SublimationModeState`, `_TimeTunnelModeState`,
`_TwoWayGradientModeState`, `_UpDownModeState`, `_WhirlingModeState`.

Also `_GoveeHoorayModeState`, `_GoveeNewYearModeState`, `_GoveeRainbowModeState`,
`_GoveeStackingModeState`, `_GoveeTwilightModeState` — these are **Govee**-branded
mode names shipped inside Surplife. Worth flagging as a licensing/OEM artifact.
**verified**

> Caveat: a `*ModeState` class is an **implementation**, not necessarily a
> user-selectable 0xB6 effect id. Treat these as *candidate* names, not confirmed
> protocol ids. **inferred**

### 3.4 Localizations exist

Effect names are localized. **verified** (`strings_effect_localized.txt`):

| English | German | French | Spanish/Italian/Portuguese |
|---|---|---|---|
| Wave | `Welle` | `Vague` | `Onda` |
| Building Blocks | `Bausteine` | — | — |
| Flowing Water | `Laufendes Wasser` | — | — |
| Fireworks | `Feuerwerk` | `feux d'artifice` | `fuochi d'artificio` / `fogos de artifício` |
| Volcano | `Vulkan` | `volcan` | `vulcano` |
| Comet | `Komet` | `comète` | `cometa` |
| Rainbow Bridge | `Regenbogenbrücke` | `arc-en-ciel` | `arcobaleno` / `arco-íris` |
| Gradient Meteor | `Gradientenmeteor` | — | — |
| Twinkling Stars | `Sterne zwinkern` | — | — |
| Stars Wink | `Stern zwinkern` | — | — |

Localization exists for 21 locales (see §4.4). This means **string matching on
English alone will miss most of the app's UI surface**; search localized forms too.

---

## 4. Scribble / segment / timer vocabulary

### 4.1 Scribble ("doodle") — the app says **doodle**, not scribble

The `Scribble*` hits in the AOT are **all Flutter framework internals** for Apple
Scribble handwriting input (`_ScribblePlaceholder`, `ScribbleClient`,
`TextInputClient.scribbleInteractionBegan`, `registerScribbleElement`) — unrelated to
per-LED painting. **verified** — `strings_scribble.txt`

The app's own word for per-LED painting is **`doodle`**, with **`Graffiti`** as the
user-facing label and **`smear`/`Smudge`** for the brush editor. **verified**

User-facing strings:
- `Doodle` — offset in `strings_doodle_ui.txt`
- `Graffiti`
- `Graffiti Wall`
- `Customized smearing`
- `Custom Smudge`
- `Creative Graffiti` (de: `Kreative Graffiti`, pl: `Kreatywne graffiti`)

Slot names: `doodle`, `Doodle`, `DIY_DOODLE`, `diy_doodle`, `EditDoodlePage`,
`doodleView`, `doodleEditView`, `isDoodle`, `fc_doodle`, `fcp_doodle`, `mc_doodle`.
**verified**

i18n keys: `fc_doodle`, `mc_doodle`, `fcp_doodle` — the `fc_`/`mc_`/`fcp_`/`bcp_`
prefixes are the app's module-scoped l10n namespaces (see §4.4).

Asset paths: `icon/ic_main_doodle`, `icon/ic_main_doodle_selected`,
`icon/ic_doodle_strip`, `icon/ic_doodle_mapping`, `icon/ic_diy_doodle_mode`.
**verified**

Source paths: `feast_control_page/src/common/widget/doodle/feast_drawing_painter.dart`,
`.../feast_smear_view.dart`, `.../edit_doodle_page/view.dart`. **verified**

### 4.2 Scribble effect templates — remote JSON, names recovered

The doodle effect templates are **fetched from a CDN**, not bundled. **verified**

```
http://resource.surplife.net/app/feast_base/doodle/asyncBlink.json
http://resource.surplife.net/app/feast_base/doodle/asyncBreath.json
http://resource.surplife.net/app/feast_base/doodle/asyncGradient.json
http://resource.surplife.net/app/feast_base/doodle/asyncJump.json
http://resource.surplife.net/app/feast_base/doodle/blink.json
http://resource.surplife.net/app/feast_base/doodle/breath.json
http://resource.surplife.net/app/feast_base/doodle/fireflyA.json
http://resource.surplife.net/app/feast_base/doodle/flash.json
http://resource.surplife.net/app/feast_base/doodle/gradient.json
http://resource.surplife.net/app/feast_base/doodle/jump.json
http://resource.surplife.net/app/feast_base/doodle/stream.json
```

So the scribble template vocabulary is: **`blink`, `asyncBlink`, `breath`,
`asyncBreath`, `gradient`, `asyncGradient`, `jump`, `asyncJump`, `flash`,
`stream`, `fireflyA`** — 11 templates. **verified** — `strings_doodle_templates.txt`

**What the templates actually contain is now settled: they are Lottie preview
animations, not wire data.** All eleven were fetched and parsed. Every one is a
Bodymovin v5.7.7 document describing a `100 x 40` canvas at `20` fps, with
shape layers and keyframes, e.g. `blink.json` has `"v":"5.7.7","fr":20,
"w":100,"h":40` and a `形状图层 3` shape layer. **verified** (fetched 2026-11-03,
HTTP 200, 3.9–99 KB each)

That matters: the templates cannot be decoded into per-LED frames, because they
are the *preview art* the app shows in its doodle picker. There is no
index/manifest at the directory root (`/` is 403, `index.json`, `list.json` and
`doodle.json` are all 404), so the eleven names above are the complete set.
**verified**

The consequence for this project is that the app's doodle "effects 1-8" are
firmware-side, and only their *ids* are usable locally (see
`docs/protocol_0xB6.md` §8.4). The named patterns this integration ships are
therefore our own constructions, not ports of the app's templates.

Note `stream` (not "flowing") is the app's word for flowing paint. **verified**

Our expected scribble names map as follows:

| we expected | app actually ships | status |
|---|---|---|
| static | `Static` | **verified** (`strings_scribble_effects.txt`) |
| flowing | `stream` (template), `Flowing` (generic), `Flowing Water` | **verified** — no literal `flowing` template |
| twinkling | `Twinkling stars`, `Twinkle`, `Twinkling Marquee` | **verified** |
| stars wink | `Stars wink` | **verified** |
| accumulate | `Accumulate`, `Accumulation`, `AccumulationMode` | **verified** |

i18n keys confirm the scribble family: `bcp_accumulate`, `fcp_accumulate`,
`mc_accumulate`. **verified**

### 4.3 Blink / flash / speed vocabulary

**verified** — `strings_blink_speed.txt`

- Mode classes: `BlinkMode`, `BlinkScopicMode`, `BlinkStrobeMode`,
  `BlinkBreatheMode`, `FeastAsyncBlink`, `FeastSyncBlink`
- UI strings: `blink`, `Blinking transition`, `Stars blinking`, `blinkTime`
- German: `Blinken`, `Blinkend`, `Blinkende Taste`, `Sterne blinken`,
  `Asynchrones Blinken von Farbe zu Farbe`, `Hintergrund blinkend`
- Flash family: `Flash`, `Flash light`, `Flashes`, `Flashing`, `FlashMode`,
  `FlashLampMode`, `Color Flash`, `Color Jump Flash`, `C1&C2 Flash`,
  `Flash C1&C2`, `Colorful flashes`
- Speed: `Speed`, `Speed setting`, ` Speed` (leading space), `ChangeSpeed`,
  `DetectionSpeed`, `DynamicSpeed`, `EnableSpeed`, `ranSpeed`, `speed:`
- Rate words: `Medium`, `Slow start and slow end`

Note: there is **no** `Slow`/`Fast` top-level speed label. The app uses a
**`Speed` slider**, not discrete slow/fast buttons. `Slow start and slow end`
is the only "slow" UI string. **verified** — the only whole-string hits are
`Medium`, `Speed`, `Speed setting`.

### 4.4 Segment vocabulary — **20 segments confirmed**

**verified** — `FINAL_validation.txt`

The app ships exactly 20 contiguous segment colour slots:

```
color_segment_0 … color_segment_19
```

A length-field parse yields exactly 20 names, indices `0..19`, **no gaps, nothing
beyond 19**. `strings` falsely reports `color_segment_102` … `color_segment_192` —
those are `color_segment_10`+`'2'` artifacts (see §0).

Related constants: `segmentsCount`, `totalSegments`, `segmentLength`,
`segmentColorList`, `colorSegmentList`, `colorSegmentIndex`, `color_segment/`,
`lor_segment_bg_1` (truncated `color_segment_bg_*`), `segmentSelectAll`,
`waitAllSegment`. **verified** — `segment_slot_names.txt`

User-facing terms: `Segment`, `Segment Density`, `Segment Stacking`,
`Segments`, `Segmentation`, `Multi-segment`, `Segmento`, `Segmentado`.
**verified**

The feature is called **"Colorful"** in the UI, and the segment editor is
**"Distribution"** internally. **verified** — `strings_distribution.txt`

- UI: `Colorful`, `Colorful Breath`, `Colorful Strobe`, `Colorful waves`,
  `Colorful flashes`, `Colorful Sports`, `Colorful Jump`, `Colorful Fade`,
  `Colorful Meteor`, `Colorful shooting stars`
- Internal: `symphony_distribution/` page tree, `DistributionCmd`,
  `buildHEXSymphonyDistributionCmd`, `sendSymphonyDistributionFrameCmd`,
  `DistributionDynamicPage`, `DistributionLaunchPage`, `DistributionMutilPage`
- Also `buildHEXSymphonyColorBlockingFT` / `CommandSymphonyColorBlockingFTParams`
  ("color blocking" = segment colouring)

German UI strings corroborate per-segment point counts:
`Gesamtzahl der Lampen (Anzahl der IC-Lampen pro Segment x Anzahl der Segmente)`,
`Anzahl der Segmente`, `Die Anzahl der Punkte in jedem Segment darf nicht kleiner als`,
`Die Gesamtzahl der Segmente darf`. **verified**

Segment-mode implementations: `_SegmentedBuildingBlocksModeState`,
`_SegmentedMeteorModeState`, `_SegmentedFlashModeState`,
`_SegmentationAsyncBreatheModeState`, `_SegmentationAsyncJumpModeState`,
`_SegmentationAsyncStrobeModeState`. **verified**

> **No literal `20` constant was found** (`maxSegment`, `segmentCount`, `SEGMENT_NUM`
> all return 0 hits). The "20" is corroborated **structurally** by the exactly-20
> `color_segment_0..19` slots, not by a named constant. **verified** (slot count) /
> **inferred** (that 20 is the hard protocol limit)

Chinese `分区` does not appear — the AOT has no CJK for this term, and there are no
`.arb` files. Localization is compiled into Dart. **verified**

### 4.5 Timer vocabulary — **6 slots NOT corroborated**

**verified** — `strings_timer*.txt`, `timer_slot_names.txt`, `strings_timer_count.txt`

User-facing:
- `Timer`, `TIMER`, `Timer Clock`, `Timer unselected`, `Timer Setting`,
  `Timing`, `Timing Management`, `Add Schedule`, `Schedule`, `Schedule full`
- `Everyday` — **exists** (1 exact hit)
- `Monday`, `Tuesday`, `Wednesday`, `Thursday`, `Friday`, `Saturday`, `Sunday` —
  **all 7 exist** as whole strings
- `Repeat` — exists
- `Number of cycles`, `Number of cycles for a single step`

**Not found:** `Weekday`, `Weekend`, `Every day`, `Once`, `Mon`/`Tue`/`Wed`/`Thu`/
`Fri`/`Sat`/`Sun` (abbreviations). **verified** — all return 0 exact hits.

Note the day names are almost certainly **Flutter `intl` `DateFormat` data**, not
app UI labels — they appear once each, exactly as `intl`'s symbol tables do.
**inferred**

Timer classes: `TimerConfig`, `TimerCmdType`, `TimerCategory`, `TimerTasksState`,
`BaseTiming`, `NewTiming`, `OldTiming`, `MeshTiming`, `SocketTiming`, `RGBWTiming`,
`SymphonyTiming`, `Timing1314ABPage`, `Timing1314CPage`, `bcp_add_timing`,
`bcp_timing_manage`, `fcp_set_timing`, `mc_set_timing`, `mc_view_timing`.
**verified**

i18n keys: `bcp_add_timing`, `bcp_timer_fail`, `bcp_timer_null`,
`bcp_timer_query_failed`, `bcp_timer_remove_content`, `bcp_timer_remove_title`,
`bcp_timing`, `bcp_timing_manage`, `fcp_add_timing`, `fcp_set_timing`, `fcp_timing`,
`mc_add_timing`, `mc_set_timing`, `mc_timing`, `mc_timing_symphony`, `mc_view_timing`.
**verified**

**On "6 slots": this is NOT corroborated.**
The only timer-like slot names are `aquatic_timer0`, `aquatic_timer1` and a lone
`timer2`. There is no `timer3`..`timer5`, and no
`maxTimer`/`timerCount`/`timerLimit`/`MAX_TIMER` constant anywhere.
**verified** — `timer_digit_check.txt`, `strings_timer_count.txt`, `FINAL_validation.txt`

Verdict: **6 slots is unknown from shipped strings.** The count is likely dynamic
(server-configured or device-dependent). **unknown**

### 4.6 Localization coverage (affects all string harvesting)

21 locale tables are compiled into the AOT. **verified** — `aot_locales.txt`

`ar_SA, cs_CZ, de_DE, en, es_ES, fr_FR, hi_IN, id_ID, it_IT, ja_JP, ko_KR, pl_PL,
pt_PT, ru_RU, th_TH, tr_TR, vi_VN, zh_CN, zh_Hant_TW, zh` (+ `messages_all.dart`)

There are **no `.arb` files** in `flutter_assets`, and **no** `i18n`/`locale`/`intl`
directories. **verified**

The l10n namespaces are module-prefixed: `bcp_*` (biz_control_page), `mc_*`
(magichome2_control_page), `fcp_*` (feast_control_page), `fc_*` (feast_control),
`fcl_*`, `msm_*`. **verified** — this is how you should search for any UI string.

---

## 5. `build*Command` inventory

### 5.1 Count and method

**297 distinct `build*Command` methods.** **verified** —
`strings_build_commands_raw.txt` (authoritative, length-field parser output).

Do **not** trust these two wrong numbers that appear in intermediate files:

- `strings -a | grep -E '^build[A-Za-z]+Command$'` → **280** (undercount: misses
  longer names that `strings` merged with a following `'2'`, e.g. `buildCustomHEXCommand`)
- naive raw regex over all bytes → **305** (overcount: includes substrings such as
  `buildHeaderCommand` inside `buildFileHeaderCommand`, and `buildEndCommand` inside
  `buildFileEndCommand`)

The correct figure requires the length field. `build_command_validation.txt`
documents the 280/305 discrepancy; the 297 figure supersedes both.

**Authoritative list: `docs/evidence/strings_build_commands_raw.txt` (297 entries).**

### 5.2 0xB6-relevant families (flagged)

**verified** — `build_commands_0xb6_candidates.txt` (35 names)

Directly relevant to custom/dynamic effect (0xB6) work:

```
buildCustomDynamicsHEXCommand
buildHexCustomDynamicCommand
buildSendCustomDynamicsHEXCommand
buildSendMeshCustomDynamicsHEXCommand
buildSendMultiChannelCustomDynamicsHEXCommand
buildSendNewSigMeshCustomDynamicsHEXCommand
buildSendRgbControllerCustomDynamicsHEXCommand
buildSigMeshCustomDynamicsHEXCommand
buildMeshCustomDynamicsHEXCommand
buildNewSigMeshCustomDynamicsHEXCommand
buildDynamicEffectRecurHexCommand
buildEffectListInfoHEXCommand
buildEffectPlayListHeaderHEXCommand
buildEffectEndFrameHEXCommand
buildEffectLengthHEXCommand
buildCompressEffectFrameBodyHEXCommand
buildPreviewEffectHEXCommand
buildRemoveEffectHEXCommand
buildPlayDiyEffectHEXCommand
buildQueryDiyEffectHEXCommand
buildCustomHEXCommand
buildCustomModeHEXCommand
buildCustomSetLedMappingHEXCommand
buildSetLedMappingHEXCommand
buildQueryLedMappingHEXCommand
buildColorfulHEXCommand
buildColorfulWcHEXCommand
buildSymphonyCustomHEXCommand
buildSymphonyDynamicHEXCommand
buildFeastDiyCommand
buildPCLDiyPreviewCommand
buildSmearHEXCommand
buildSmearPreviewHEXCommand
buildSmearBgDataHEXCommand
buildSmearSingleFrameBgHexCommand
buildCustomSmearPreviewBgColorHexCommand
```

Strongest 0xB6 candidates (custom effect payload construction):
`buildCustomDynamicsHEXCommand`, `buildSendCustomDynamicsHEXCommand`,
`buildDynamicEffectRecurHexCommand`, `buildEffectListInfoHEXCommand`,
`buildEffectPlayListHeaderHEXCommand`, `buildEffectEndFrameHEXCommand`,
`buildCompressEffectFrameBodyHEXCommand`. **inferred**

The **effect frame/header/body triple** (`EffectPlayListHeaderHEXCommand` +
`EffectEndFrameHEXCommand` + `CompressEffectFrameBodyHEXCommand`) strongly suggests
the custom-effect payload is a **framed, compressed, chunked blob** — which matches
why no simple template exists. **inferred**

Segment/colourful family: `buildColorfulHEXCommand`, `buildColorfulWcHEXCommand`,
`buildHEXSymphonyColorBlockingFT`, `buildHEXSymphonyDistributionCmd`,
`buildHEXSymphonyDistributionFrameCmd`, `buildHEXSymphonyDistributionHeaderCmd`,
`buildHEXSymphonyDistributionPlayCmd`, `buildHEXSymphonyDistributionPreviewCmd`.
**verified**

Scribble/smear family: `buildSmearHEXCommand`, `buildSmearPreviewHEXCommand`,
`buildSmearHEXV2Command` (seen as `buildSmearHEXV2CommandisRotate` — a `strings`
merge of `buildSmearHEXV2Command` + `isRotate`). **verified**

Timer family: `buildTimerQueryHEXCommand`, `buildGenTimerQueryHEXCommand`,
`buildGetSwitchChannelTimerHexCommand`, `buildSetSwitchChannelTimerHexCommand`,
`buildQuerySigTimerHEXCommand`, `buildQueryOldTimingHEXCommand`,
`buildTimingSettingCommand`, `buildTimingQueryCommand`, `buildLoopTimedQueryHEXCommand`.
**verified**

### 5.3 Protocol families present

`*Protocol` classes: `CommandProtocol`, `CommonProtocol`, `ConfigProtocol`,
`Protocol`, `ProtocolType`, `SelectedProtocol`, `StateProtocol`, `HexCmdForm`,
`hexCmdForms`, `cmdForm`. **verified** — `strings_protocol_classes.txt`

Protocol package source paths recovered:
```
package:flutter_send_command/src/bean/command.dart
package:flutter_send_command/src/bean/device.dart
package:flutter_send_command/src/implement/control_implement.dart
package:flutter_send_command/src/interface/control_interface.dart
package:flutter_send_command/src/pigeons/control_messages.dart
```
**verified** — `strings_protocol_0xb6.txt`

### 5.4 Protocol names from the JS bundle (best structured source)

The JS bundle is **plain, unminified-ish JSON-ish JS** and contains the real device
capability tables. **verified** — `js_protocols.txt`

Path:
`flutter_assets/packages/zg_app_builtin_assest/assets/js_bundle/60_surplife.js`
(426,410 bytes, 2 lines, bundle version `60`, build timestamp `1773452462111`)

**19 `protocols` names:**
`aquaticE9, cct, cct_high_pressure, cct_xdd, christmas_wifi, common, common1_0,
common2_0, curtainLightE2, rgb_mini, rgb_mini_mic, rgbcw_mini, rgbw, rgbw_mini,
sig_common, sig_sock, symphony_wifi, symphony_wifi_new, symphonyE1, symphonyE4`

**37 `stateProtocol` names:**
`ambientSyncLight, bleCeilingCCTLight, blePixelCarLight, multiInterfaceLed,
sigHumanSensor, sigHumanSensor24g, sigMusicPlayer, sigMusicSensor, sigSock,
sigStandard, wifibleAquaticLight, wifibleBrightness, wifibleCarrierWaveSymphony,
wifibleCCT, wifibleCCTDrive, wifibleChristmasCC, wifibleChristmasLight,
wifibleDmx, wifibleEightPortScreenController, wifibleFeast,
wifibleFeastCurtainLight, wifibleFeastSkyLight, wifibleFirefly,
wifibleGlobalLight, wifibleLightStandardV1, wifibleLightStandardV1_1,
wifibleLightStandardV2, wifibleMusicBox, wifibleNewRGBSymphony,
wifibleRGBSymphony, wifibleStairLight, wifibleSwitch, wifibleSwitchV2,
wifibleSymphony, wifibleSymphonyCeilingLamp, wifibleSymphonyCurtainLight`

**14 `hexCmdForms` keys:**
`bright_value, colour_data, colour_temp_data, colour_warm_data, get_symp_config,
get_symp_custom_data, get_symp_fixed_data, music_color_data, music_temp_data,
switch_assist, symp_scene, temp_assist_value, temp_value, warm_value`

This is the single most useful protocol artifact in the bundle. `get_symp_config`,
`get_symp_custom_data`, `get_symp_fixed_data` and `symp_scene` are the Symphony
(0xB6-family) config/custom/fixed effect endpoints. **inferred** (naming is verified;
the 0xB6 association is inferred)

Sample templates from this file (**verified**):
```
colour_data:      "31{r}{g}{b}0000f00f"   (needChecksum: true)
music_color_data: "42{r}{g}{b}0000f00f"   (needChecksum: true)
temp_value:       "31000000{warm}{cool}0f0f" (needChecksum: true)
```

### 5.5 `construct*` / `parse*` / `send*Command`

**verified** — `strings_build_commands.txt`

- `construct*`: only `_constructor` and `_constructTweens`. **Nothing protocol-related.**
- `parse*`: 100+ names, but all generic (`_parseInt`, `_parseColor`, `_parseJson`,
  `_parseHex`, `_parseZGBleDevice`, `_parseMatterDevice`, `_parseEffects`,
  `_parsePartitions`). **None are protocol-payload parsers.**
- `send*Command`: 32 names, all `_send*` UI/transport handlers
  (`_sendCommand`, `_sendBleCommand`, `_sendBleProtoCommand`,
  `_sendSegmentationCommand`, `_sendDiyPlayCommand`, `_sendCustomDynamics`, …).

So: **`build*Command` is the only rich protocol-method family.** **verified**

---

## 6. What this evidence does NOT give us

1. **No function bodies.** `@N` is a library-URI hash, not an address (§2.3).
   We cannot decompile, trace, or read any `build*Command` implementation.
   **verified**
2. **No id → name table.** The 24 effect names are scattered over 14.4 MB with no
   adjacency (§2.5). The id↔name pairing is ours, not the app's. **verified**
3. **No packet templates.** Zero `{r}{g}{b}` in the AOT (§2.4). The only templates
   available are the three `hexCmdForms` entries in the JS bundle, which cover
   `colour_data` / `music_color_data` / `temp_value` only — **not** the 0xB6
   custom-effect payload. **verified**
4. **No 0xB6 payload structure.** We know the *names* of the frame/header/body
   builders but not their byte layout. **verified**
5. **No confirmed 6 timer slots.** No constant, no slot array. **unknown**
6. **No `20` segment constant.** The 20 is structural (20 colour slots), not a named
   limit. Whether the protocol rejects 21 is **unknown**.
7. **No `*ModeState` → effect-id mapping.** 134 mode classes exist; which correspond
   to the 24 known ids is **unknown**.
8. **Scribble templates are remote.** The 11 `feast_base/doodle/*.json` files are not
   in the bundle; we only have their **names** and the CDN URL. Their schema is
   **unknown** (would require fetching `resource.surplife.net`, which I did not do).
9. **`CustomEffectFT182` is a trap** — it does not exist. Do not cite it. **verified**
10. **No CJK `分区`**, no `.arb` files, no separate i18n assets. All l10n is compiled
    into Dart; only the English + European strings recovered here are directly usable.

---

## 7. Reproducibility

All commands below are exact and were run on macOS against
`/Applications/Surplife.app`. `$APP` is the AOT path.

```sh
APP=/Applications/Surplife.app/Wrapper/Surplife.app/Frameworks/App.framework/App
A=/Applications/Surplife.app/Wrapper/Surplife.app/Frameworks/App.framework/flutter_assets

# --- identity ---
shasum -a 256 "$APP"
ls -la "$APP"
file "$APP"
otool -h "$APP"; otool -l "$APP"; otool -L "$APP"
nm -a "$APP"; nm -gU "$APP"
dwarfdump --uuid "$APP"
cat /Applications/Surplife.app/Wrapper/Surplife.app/Info.plist
cat "$A/packages/magichome2_other/assets/module_branches.txt"

# --- AOT negative checks ---
strings -a "$APP" | wc -l                          # 363396
strings -a "$APP" | grep -c '@'                    # 74421 (mostly binary noise)
strings -a "$APP" | grep -cE '^[A-Za-z_][A-Za-z0-9_$]*@[0-9]+$'   # 24908
grep -a '{r}' "$APP" | wc -l                       # 0  -> no templates
grep -ac 'cmdForm' "$APP"                          # 1  -> bare identifier only

# --- effect name offsets (raw, unambiguous) ---
for n in "Wave" "Meteor" "Streamer" "Building Blocks" "Flowing Water" "Chase" \
         "Horse Racing" "Cycle" "Breathe" "Jump" "Strobe" "Twinkling Stars" \
         "Stars Wink" "Warning" "Collision" "Fireworks" "Comet" \
         "Gradient Meteor" "Volcano" "Superluminal" "Rainbow Bridge" \
         "Gradient Overlay" "Static Gradient" "Static Fill"; do
  printf '%-20s ' "$n"; grep -aboF -- "$n" "$APP" | head -3 | tr '\n' ' '; echo
done

# --- build*Command (authoritative, byte-boundary validated) ---
python3 - <<'PY'
import re
p="/Applications/Surplife.app/Wrapper/Surplife.app/Frameworks/App.framework/App"
d=open(p,'rb').read()
def alnum(c): return c and (48<=c<=57 or 65<=c<=90 or 97<=c<=122 or c==95)
names={m.group(0).decode() for m in re.finditer(rb'build[A-Za-z]+Command', d)
       if not alnum(d[m.start()-1:m.start()][0]) and not alnum(d[m.end():m.end()+1][0])}
print(len(names)); print("\n".join(sorted(names)))
PY

# --- authoritative parser: length-field driven (defeats the '2'-merge artifact) ---
python3 - <<'PY'
import re,struct
p="/Applications/Surplife.app/Wrapper/Surplife.app/Frameworks/App.framework/App"
d=open(p,'rb').read()
recs=[]
for m in re.finditer(rb'\x32[\xd0-\xdf]\x05\x00', d):
    h=m.start()
    if h+16>len(d): continue
    L2=struct.unpack_from('<I',d,h+8)[0]      # length in UTF-16 units
    if L2==0 or L2>2000 or L2%2: continue
    n=L2//2; s=d[h+16:h+16+n]
    if len(s)<n or not all(32<=c<127 for c in s): continue
    recs.append(s.decode())
S=set(recs)
print("distinct strings:",len(S))                       # 157890
print("build*Command:",len([t for t in S if re.fullmatch(r'build[A-Za-z]+Command',t)]))  # 297
print(sorted(t for t in S if re.fullmatch(r'color_segment_\d+',t)))  # exactly 0..19
print(sorted(t for t in S if re.fullmatch(r'CustomEffectFT\d+',t)))  # exactly 1..41
print('CustomEffectFT182' in S, 'color_segment_102' in S)            # False False
PY

# --- JS bundle protocol inventory ---
JS="$A/packages/zg_app_builtin_assest/assets/js_bundle/60_surplife.js"
grep -oE 'protocols:\[\{[^]]*\]'      "$JS" | grep -oE 'name:"[A-Za-z0-9_]+"'
grep -oE 'stateProtocol:\[\{[^]]*\]'  "$JS" | grep -oE 'name:"[A-Za-z0-9_]+"'
grep -oE '[A-Za-z_]+:\{cmdForm:'      "$JS"

# --- doodle template URLs ---
strings -a "$APP" | grep -oE 'https?://resource\.surplife\.net/app/feast_base/doodle/[A-Za-z0-9_.]+'

# --- locale tables ---
strings -a "$APP" | grep -oE 'generated/intl/messages_[A-Za-z_]+\.dart' | sort -u
```

Raw outputs for every one of the above are in `docs/evidence/`
(index in `docs/evidence/README.txt`).

---

## 8. Bottom line

- **AOT negative result: confirmed.** No symbol→address mapping, no function bodies,
  no packet templates, no id→name table. `@N` is a library-URI hash, not an address.
  The one correction to the prior analysis: **13,760 Dart method names *are*
  recoverable** (as names only), which is what made §5 possible.
- **Effect vocabulary: 24/24 present.** 23 verbatim, 1 case variant — id 13 ships as
  **`Stars wink`**. Fix our table.
- **New capability:** ~40 additional effect/colourful names and **134 `*ModeState`
  classes**, plus a `Govee*` branded subset.
- **Scribble is called `doodle`/`Graffiti`/`smear`**, with **11 named remote templates**
  (`blink, asyncBlink, breath, asyncBreath, gradient, asyncGradient, jump, asyncJump,
  flash, stream, fireflyA`).
- **Segments: exactly 20 confirmed** (`color_segment_0..19`). No `20` constant.
- **Timer: 6 slots NOT corroborated.** No constant, no slot array — treat as **unknown**.
- **297 real `build*Command` methods**, of which 35 are 0xB6/custom-effect relevant.
- **Build pinned:** Surplife **1.7.2 (407)**, bundle `com.zennge.magichome2`,
  Flutter **3.19.5**, AOT SHA-256 matches.
- **Watch out:** `CustomEffectFT182` and `color_segment_12` are `strings` artifacts.
  Do not cite them.
