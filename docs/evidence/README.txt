Raw evidence extracts for docs/app_evidence.md
Artifact: /Applications/Surplife.app/Wrapper/Surplife.app/Frameworks/App.framework/App
SHA-256:  1b5ad664ff0bb6b50fed29afb2620531d8524ce31bf1b8a38378ed4c1dcd917c
App:      Surplife 1.7.2 (build 407), com.zennge.magichome2, Flutter 3.19.5

All offsets are byte offsets into the AOT snapshot file.

================================================================
METHODOLOGY WARNING - READ BEFORE USING ANY FILE HERE
================================================================
`strings -a` on this binary FABRICATES trailing "2" characters.

The string table is a sequence of Dart String objects:
    +0 : 32 dN 05 00      record header (0x32 = ASCII '2')
    +4 : hash
    +8 : length in UTF-16 units (LE uint32) = 2 x ASCII char count
    +16: characters, NOT NUL-terminated

Because the chars are not NUL-terminated, `strings` runs into the next
record's 0x32 header and emits a bogus trailing '2':

    CustomEffectFT18 + '2'  ->  "CustomEffectFT182"   <-- DOES NOT EXIST
    color_segment_1  + '2'  ->  "color_segment_12"    <-- ambiguous (12 IS real)
    color_segment_10 + '2'  ->  "color_segment_102"   <-- DOES NOT EXIST
    buildMeshCommand + '2'  ->  "buildMeshCommand2"   <-- DOES NOT EXIST

AUTHORITATIVE OUTPUT = aot_all_strings_parsed.txt (157,890 distinct strings),
produced by the length-field parser reproduced in docs/app_evidence.md section 7.

Files whose names end in _validated, _raw, or that are tsv/txt summary tables
were produced with the correct parser. Older probe files produced with
`strings` are retained for audit but may contain the '2' artifact; where that
matters it is called out in the main report.

================================================================
FILE INDEX
================================================================

-- identity / structure --
aot_identity.txt                 file type, Mach-O header, segments, nm
aot_sections_full.txt            sections, linked dylibs, dSYM search
aot_mapping_verdict.txt          LC_FUNCTION_STARTS, UUID, tag-value range
aot_atvalue_semantics.txt        PROOF @N is a library-URI hash, not an address
aot_slot_structure.txt           hexdumps proving the Dart String object layout
aot_strings_probe.txt            strings counts and '@' probes
aot_symbol_tags.txt              symbol-tag recovery counts (13,760 names)
aot_locales.txt                  21 intl locales; no .arb assets
aot_locale_table_region.txt      ordered dump of a locale region

-- authoritative parsed output --
aot_all_strings_parsed.txt       157,890 distinct strings (USE THIS)
strings_build_commands_raw.txt   297 build*Command (USE THIS)
effect_id_name_table.tsv         FINAL id -> app string table (tab separated)
effect_id_name_table.txt         same, human readable
FINAL_validation.txt             end-to-end re-validation of every report claim

-- effect names --
aot_effect_name_offsets.txt      raw grep -abo offsets for the 24 names
aot_effect_cluster_raw.txt       raw context around the effect-name cluster
aot_two_misses.txt               "Stars Wink" / "Gradient Overlay" byte checks
aot_customeffectft_raw.txt       proof "CustomEffectFT182" is a strings artifact
effect_name_validation.txt       per-name exact/standalone counts
effect_slot_check.txt            earlier per-name confirmation (superseded)
effect_adjacency_check.txt       proves no contiguous id->name table in AOT
strings_effect_names.txt         exact whole-string hit counts for the 24 names
strings_effect_localized.txt     localized (de/fr/es/it/pt) probes
strings_effect_localized_slots.txt  broad localized string dump
strings_effect_ids.txt           CustomEffectFT / DynamicEffect numbered classes
strings_effecttype_enum.txt      effect-type enum probes (negative)
strings_dynamic_effect.txt       customDynamics / dynamic-effect vocabulary
strings_dynamic_names.txt        *DynamicEffect / *Dynamics class names
strings_modestate_full.txt       134 *ModeState effect implementation classes
strings_colorful_modes.txt       Colorful* UI strings and mode classes
aot_packet_templates.txt         proof no {r}{g}{b} templates in AOT

-- scribble / doodle --
strings_scribble.txt             scribble / doodle / graffiti / diy / smear
strings_scribble_effects.txt     doodle URLs; static/flowing/accumulate probes
strings_doodle_templates.txt     11 feast_base/doodle JSON template names
strings_doodle_ui.txt            doodle UI strings and asset paths
strings_ui_short.txt             short strings matching UI keywords
strings_blink_speed.txt          blink / flash / speed vocabulary

-- segment / colorful --
strings_segment.txt              segment vocabulary
strings_segments20.txt           color_segment_* probe (contains artifacts)
segment20_validation.txt         proof: exactly 20 segments (0..19)
segment_slot_names.txt           segment / colorful names
strings_distribution.txt         distribution / blocking vocabulary

-- timer --
strings_timer.txt                timer base names
strings_timer2.txt               weekday / everyday / repeat strings
strings_timer3.txt               timer limits, cycle/loop, i18n keys
strings_timer_count.txt          timer-count corroboration (ALL NEGATIVE)
timer_slot_names.txt             timer slot names
timer_digit_check.txt            raw check: only aquatic_timer0/1 + timer2

-- protocol / commands --
strings_build_commands.txt       via `strings` - SUPERSEDED, contains artifacts
build_command_validation.txt     280 vs 305 discrepancy analysis (superseded)
build_command_suspects.txt       raw-byte proof those "suspects" are real names
build_commands_0xb6_candidates.txt  35 effect/dynamic/colorful/smear commands
strings_protocol_classes.txt     Protocol / HexCmdForm / Command names
strings_protocol_0xb6.txt        0xB6 literals, cmdType/cmdId, protocol packages
js_protocols.txt                 19 protocols + 37 stateProtocols + 14 hexCmdForms
js_dynamic.txt                   JS dynamic/effect probes (negative)
js_182_context.txt               all '182' occurrences in JS bundle
asset_effect_json_probe.txt      JSON asset probes for effect tables (negative)
asset_misc_probe.txt             test.json, genAsstes.zgarm, module_branches.txt

================================================================
NOT MINE (created by a sibling agent, same docs/evidence/ dir)
================================================================
all_products_summary.json, command_family_coverage.json,
device_db_AK001-ZJ21413.json, extract_device_db.py, lmdb_dump.json
These came from a different task (LMDB device-database extraction).
They are unrelated to the AOT string analysis above.
