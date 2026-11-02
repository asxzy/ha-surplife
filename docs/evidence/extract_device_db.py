#!/usr/bin/env python3
"""
extract_device_db.py -- reproducible extractor for the Surplife / Zengge
shipped device database.

Target product
--------------
    Marketing model : AK001-ZJ21413
    Protocol/model #: 0xB6  (== 182 decimal)
    MODEL_INFO      : ZG-BL-UFO
    Product         : "Surplife Outdoor Permanent Lighting"

What this script does
---------------------
The app ships *three* independent device databases that describe the same
product space.  This script reads all of them and writes raw JSON extracts:

  1. `60_surplife.js`      -- minified JS "device DB" bundle
                              (bleDevices / wifiDevices / sigMeshDevices /
                               panel / functions / chip / symphonyConfig)
                              Evaluated with node in a sandbox; no network,
                              no DOM, no timers.
  2. `wifi_devices.json`,
     `wifi_dp_cmd.json`,
     `wifi_dp_response.json`,
     `wifi_device_panel.json`
                            -- older, plain-JSON generation of the same DB.
  3. `data.mdb`            -- LMDB database.  Values are FlatBuffers tables
                              (not JSON).  Parsed here with a minimal
                              FlatBuffer reader; the `lmdb` python module is
                              required.

Nothing is guessed: if a lookup misses, the script says so and writes
`found: false` rather than fabricating a record.

Outputs (written next to this script)
-------------------------------------
    device_db_AK001-ZJ21413.json   full entry, all sources, provenance
    all_products_summary.json      every productId in every source + function counts
    command_family_coverage.json   E1 21 / E1 22 / E1 23 / E1 26 / E0 05 / E0 06 scan
    lmdb_dump.json                 decoded LMDB tables (devices/functions/protocols/panels)

Usage
-----
    python3 extract_device_db.py                 # default /Applications path
    python3 extract_device_db.py --app /path/to/Surplife.app
    python3 extract_device_db.py --no-lmdb       # skip data.mdb

Requirements
------------
    node  (>= 14)              -- to evaluate the JS bundle
    python3 + lmdb             -- for data.mdb  (pip install --user lmdb)
                                  if lmdb is missing, LMDB output is skipped
                                  with an explicit note, not a silent pass.

The script is read-only with respect to the .app bundle.  It never writes
outside its own directory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

DEFAULT_APP = "/Applications/Surplife.app"

#: The product we care about.
TARGET_PRODUCT_ID = 182          # 0xB6
TARGET_MODEL_STRING = "AK001-ZJ21413"
TARGET_MODEL_INFO = "ZG-BL-UFO"

#: Byte patterns of the command families we are auditing.  Matched
#: case-insensitively against raw artifact bytes.
COMMAND_FAMILIES = {
    "E1 21": ["e121"],
    "E1 22": ["e122"],
    "E1 23": ["e123"],
    "E1 26": ["e126"],
    "E0 05": ["e005"],
    "E0 06": ["e006"],
}

HERE = os.path.dirname(os.path.abspath(__file__))


def asset_paths(app: str) -> Dict[str, str]:
    fa = os.path.join(
        app, "Wrapper", "Surplife.app", "Frameworks", "App.framework",
        "flutter_assets", "packages",
    )
    mh = os.path.join(fa, "magichome2_home_data_provide", "assets")
    return {
        "js_bundle": os.path.join(
            fa, "zg_app_builtin_assest", "assets", "js_bundle",
            "60_surplife.js",
        ),
        "wifi_devices": os.path.join(mh, "wifi_devices.json"),
        "ble_devices": os.path.join(mh, "ble_devices.json"),
        "wifi_dp_cmd": os.path.join(mh, "wifi_dp_cmd.json"),
        "wifi_dp_response": os.path.join(mh, "wifi_dp_response.json"),
        "wifi_device_panel": os.path.join(mh, "wifi_device_panel.json"),
        "hikariya_dp_cmd": os.path.join(mh, "HIKARIYA", "wifi_dp_cmd.json"),
        "lmdb_dir": os.path.join(
            fa, "briturn_core", "assets", "zg_device_db",
        ),
        "dart_app": os.path.join(
            app, "Wrapper", "Surplife.app", "Frameworks", "App.framework", "App",
        ),
        # The Swift/ObjC runner Mach-O that sits beside App.framework.
        "runner_binary": os.path.join(
            app, "Wrapper", "Surplife.app", "Surplife",
        ),
    }


# --------------------------------------------------------------------------
# Source 1: the JS bundle
# --------------------------------------------------------------------------

# The bundle is a single line of plain ES5/ES2015 `var a=..., b=...;` plus
# function declarations.  We evaluate it in a node VM sandbox and pull out
# only the data variables.  This is far more reliable than brace-matching a
# minified blob, and it is exactly what the app itself does.
JS_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const ctx = { console };
vm.createContext(ctx);
const want = process.argv[3].split(',');
vm.runInContext(
  src + '\n;globalThis.__OUT={' + want.map(n => n + ':' + n).join(',') + '};',
  ctx, { timeout: 120000 }
);
const out = ctx.__OUT;
// symphonyConfig is an object of functions; materialise the config it
// generates for the "surplife" appKey so the output is plain JSON.
try {
  out.__symphonyConfigJson = JSON.parse(out.symphonyConfig.getConfigJson('surplife'));
} catch (e) { out.__symphonyConfigJson = null; }
delete out.symphonyConfig;
process.stdout.write(JSON.stringify(out));
"""

JS_VARS = [
    "bleDevices", "bleDevicesHikariya", "wifiDevices", "wifiDevicesHikariya",
    "sigMeshDevices", "sigMeshDevicesFVT", "sigMeshGWDevices",
    "panel", "functions", "chip", "symphonyIC", "customProduct",
    "deviceTableDepend", "entityTypeTablesMap",
]


def load_js_bundle(path: str) -> Dict[str, Any]:
    """Evaluate 60_surplife.js in node and return its data tables."""
    if not os.path.exists(path):
        return {"__error": f"missing file: {path}"}
    node = shutil.which("node")
    if not node:
        return {"__error": "node not found on PATH; cannot evaluate JS bundle"}

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(JS_HARNESS)
        harness = fh.name
    try:
        proc = subprocess.run(
            [node, harness, path, ",".join(JS_VARS)],
            capture_output=True, text=True, timeout=180,
        )
    finally:
        os.unlink(harness)

    if proc.returncode != 0:
        return {"__error": f"node failed: {proc.stderr.strip()[:2000]}"}
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------
# Source 2: plain JSON assets
# --------------------------------------------------------------------------

def load_json(path: str) -> Any:
    if not os.path.exists(path):
        return {"__error": f"missing file: {path}"}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# Source 3: LMDB (FlatBuffers)
# --------------------------------------------------------------------------

class FlatBuf:
    """Minimal read-only FlatBuffers table accessor."""

    def __init__(self, buf: bytes):
        self.b = buf

    def u32(self, o: int) -> int:
        return struct.unpack_from("<I", self.b, o)[0]

    def i32(self, o: int) -> int:
        return struct.unpack_from("<i", self.b, o)[0]

    def u16(self, o: int) -> int:
        return struct.unpack_from("<H", self.b, o)[0]

    def root(self) -> int:
        return self.u32(0)

    def fields(self) -> Dict[int, Optional[int]]:
        """field index -> absolute buffer offset (or None if absent)."""
        r = self.root()
        vt = r - self.i32(r)
        vlen = self.u16(vt)
        n = (vlen - 4) // 2
        out: Dict[int, Optional[int]] = {}
        for i in range(n):
            off = self.u16(vt + 4 + 2 * i)
            out[i] = r + off if off else None
        return out

    def string(self, p: int) -> Optional[str]:
        q = p + self.u32(p)
        ln = self.u32(q)
        return self.b[q + 4:q + 4 + ln].decode("utf-8", "replace")


# LMDB table -> (table name, {field index: (field name, type)})
# Field indices were recovered by inspecting the schema (meta) tables that
# LMDB stores under key 0x000000000000000N, plus empirical checks.
LMDB_SCHEMA = {
    "18000008": ("CommandProtocol",
                 {0: ("id", "u32"), 1: ("productId", "u32"),
                  2: ("name", "str"), 3: ("deviceMinVer", "u32")}),
    "18000010": ("DeviceFunction",
                 {0: ("id", "u32"), 1: ("code", "str")}),
    "18000014": ("DeviceFunctionEntity",
                 {0: ("id", "u32"), 1: ("deviceMinVer", "u32"),
                  2: ("productId", "u32"), 3: ("functionId", "u32")}),
    "18000028": ("StateProtocol",
                 {0: ("id", "u32"), 1: ("productId", "u32"),
                  2: ("name", "str"), 3: ("deviceMinVer", "u32")}),
    "1800002c": ("ZGDeviceEntity",
                 {0: ("id", "u32"), 1: ("productId", "u32"),
                  2: ("icon", "str"), 3: ("category", "str"),
                  4: ("categoryCode", "str"), 5: ("entityType", "str"),
                  6: ("minSupportVersion", "u32")}),
    "18000030": ("ZGDevicePanelEntity",
                 {0: ("id", "u32"), 1: ("productId", "u32"),
                  2: ("type", "str")}),
    "18000034": ("ZGDevicePanelTabEntity",
                 {0: ("id", "u32"), 1: ("productId", "u32"),
                  2: ("pageProperty", "str"), 3: ("index", "u32"),
                  4: ("deviceMinVer", "u32")}),
}


def decode_lmdb(lmdb_dir: str) -> Dict[str, Any]:
    """Decode data.mdb into {tableName: [row, ...]} plus schema notes."""
    try:
        import lmdb  # type: ignore
    except ImportError:
        return {"__error": (
            "python module 'lmdb' not installed. "
            "Run: pip3 install --user lmdb   (or python3 -m pip install --user lmdb)"
        )}

    mdb = os.path.join(lmdb_dir, "data.mdb")
    if not os.path.exists(mdb):
        return {"__error": f"missing file: {mdb}"}

    env = lmdb.open(lmdb_dir, readonly=True, lock=False)
    tables: Dict[str, List[Dict[str, Any]]] = {}
    schema_tables: Dict[str, List[str]] = {}
    try:
        with env.begin() as txn:
            pairs = list(txn.cursor())
    finally:
        env.close()

    for key, val in pairs:
        if not val:
            continue
        prefix = key[:4].hex()
        if prefix == "00000000":
            # Schema / meta tables: keep their embedded field-name strings.
            schema_tables[key.hex()] = [
                s.decode("ascii", "replace")
                for s in re.findall(rb"[\x20-\x7e]{4,}", val)
            ]
            continue
        spec = LMDB_SCHEMA.get(prefix)
        if not spec:
            continue
        name, fieldspec = spec
        fb = FlatBuf(val)
        f = fb.fields()
        row: Dict[str, Any] = {"_key": key.hex()}
        for idx, (fname, ftype) in fieldspec.items():
            p = f.get(idx)
            if p is None:
                row[fname] = None
                continue
            if ftype == "str":
                try:
                    row[fname] = fb.string(p)
                except Exception:
                    row[fname] = None
            else:
                row[fname] = fb.u32(p)
        tables.setdefault(name, []).append(row)

    return {
        "entry_count": len(pairs),
        "schema_tables": schema_tables,
        "tables": {k: v for k, v in tables.items()},
        "table_sizes": {k: len(v) for k, v in tables.items()},
    }


# --------------------------------------------------------------------------
# Command-family byte scan
# --------------------------------------------------------------------------

def scan_command_families(paths: Dict[str, str]) -> Dict[str, Any]:
    """Raw-byte, case-insensitive scan for each command family per artifact."""
    artifacts = {
        "js_bundle": paths["js_bundle"],
        "wifi_devices.json": paths["wifi_devices"],
        "wifi_dp_cmd.json": paths["wifi_dp_cmd"],
        "wifi_dp_response.json": paths["wifi_dp_response"],
        "wifi_device_panel.json": paths["wifi_device_panel"],
        "HIKARIYA/wifi_dp_cmd.json": paths["hikariya_dp_cmd"],
        "data.mdb": os.path.join(paths["lmdb_dir"], "data.mdb"),
        "Dart App binary": paths["dart_app"],
    }
    out: Dict[str, Any] = {}
    for label, path in artifacts.items():
        if not os.path.exists(path):
            out[label] = {"__error": f"missing file: {path}"}
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        rec: Dict[str, Any] = {}
        for fam, pats in COMMAND_FAMILIES.items():
            offsets: List[int] = []
            for pat in pats:
                for m in re.finditer(re.escape(pat.encode()), data, re.I):
                    offsets.append(m.start())
            rec[fam] = {
                "status": "present" if offsets else "absent",
                "offsets": offsets[:20],
                "hit_count": len(offsets),
            }
        # Also record every cmdForm string in wifi_dp_cmd if that's the file.
        if os.path.basename(path) == "wifi_dp_cmd.json":
            try:
                cmd = json.loads(data)
                rec["_all_cmdForms"] = {
                    k: v.get("cmdForm") for k, v in cmd.items()
                    if isinstance(v, dict)
                }
            except Exception:
                pass
        out[label] = rec
    return out


# --------------------------------------------------------------------------
# Model-string search
# --------------------------------------------------------------------------

def search_model_strings(paths: Dict[str, str]) -> Dict[str, Any]:
    """
    Search every artifact for the human model string and the MODEL_INFO.

    The DB is keyed by numeric productId, so these strings are expected to be
    absent from the DB tables; we report exactly where they do/don't appear.
    """
    needles = {
        "model": TARGET_MODEL_STRING.encode(),
        "model_info": TARGET_MODEL_INFO.encode(),
        "model_prefix": b"AK001",
        "protocol_hex": b"0xB6",
    }
    targets = {
        "js_bundle": paths["js_bundle"],
        "wifi_devices.json": paths["wifi_devices"],
        "data.mdb": os.path.join(paths["lmdb_dir"], "data.mdb"),
        "Dart App binary": paths["dart_app"],
    }
    out: Dict[str, Any] = {}
    for label, path in targets.items():
        if not os.path.exists(path):
            out[label] = {"__error": f"missing file: {path}"}
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        rec: Dict[str, Any] = {}
        for name, needle in needles.items():
            hits = [m.start() for m in re.finditer(re.escape(needle), data)]
            rec[name] = {"hit_count": len(hits), "offsets": hits[:10]}
            if name == "model_prefix":
                # Show the distinct strings that follow the prefix, to prove
                # whether the exact model exists or only a prefix does.
                seen = []
                for off in hits:
                    s = data[off:off + 40].split(b"\x00")[0]
                    txt = s.decode("ascii", "replace")
                    if txt not in seen:
                        seen.append(txt)
                rec[name]["distinct_strings"] = seen
        out[label] = rec
    return out


# --------------------------------------------------------------------------
# Provenance: byte offsets for the records we quote
# --------------------------------------------------------------------------

def locate_offsets(paths: Dict[str, str]) -> Dict[str, Any]:
    """
    Record exact byte offsets for the quoted records so every claim in the
    report can be traced to a byte position in a shipped artifact.
    """
    out: Dict[str, Any] = {}

    def offsets_of(path: str, needles: List[bytes]) -> Dict[str, List[int]]:
        if not os.path.exists(path):
            return {"__error": f"missing file: {path}"}
        with open(path, "rb") as fh:
            data = fh.read()
        return {n.decode("latin1"): [m.start() for m in re.finditer(re.escape(n), data)]
                for n in needles}

    out["60_surplife.js"] = {
        "file": paths["js_bundle"],
        "size": os.path.getsize(paths["js_bundle"]) if os.path.exists(paths["js_bundle"]) else None,
        "offsets": offsets_of(paths["js_bundle"], [
            b"bleDevices=[",
            b"wifiDevices=[",
            b"panel=[",
            b"functions=[",
            b"productId:182,",
            b"symphonyE1",
            b"wifibleSymphony",
            b"symphony_line",
        ]),
    }
    out["wifi_devices.json"] = {
        "file": paths["wifi_devices"],
        "offsets": offsets_of(paths["wifi_devices"], [
            b'{"productId":182', b'"symphonyE1"', b'"wifibleSymphony"',
        ]),
    }
    out["ble_devices.json"] = {
        "file": paths["ble_devices"],
        "offsets": offsets_of(paths["ble_devices"], [b'{"productId":182']),
    }
    out["wifi_device_panel.json"] = {
        "file": paths["wifi_device_panel"],
        "offsets": offsets_of(paths["wifi_device_panel"], [b'{"productId":182']),
    }
    out["wifi_dp_cmd.json"] = {
        "file": paths["wifi_dp_cmd"],
        # NOTE: two families of needle here. Quoted needles ('"e006"') point at the
        # opening quote of the JSON key/value token; unquoted needles (b"e006") point
        # at the first byte of the cmdForm *value*, which is what the report cites.
        "offsets": offsets_of(paths["wifi_dp_cmd"], [
            b'"switch_led_v3"', b'"scene_data_v3"', b'"get_timer_list_v2"',
            b'"countdown"', b'"e006"', b"e001{preview}", b"e002{preview}",
            b"e006", b"e00a", b"e008", b"e00d", b"ea818a8b",
        ]),
    }
    out["wifi_dp_response.json"] = {
        "file": paths["wifi_dp_response"],
        # Quoted key form ('"state_upload"') = opening quote of the key;
        # the '"dpCode":"..."' form = first byte of the whole dpCode token.
        "offsets": offsets_of(paths["wifi_dp_response"], [
            b'"state_upload"', b'"get_time"', b'"get_symp_config_v2"',
            b'"dpCode":"state_upload"', b'"dpCode":"get_time"',
            b'"dpCode":"get_symp_config_v2"',
        ]),
    }
    out["data.mdb"] = {
        "file": os.path.join(paths["lmdb_dir"], "data.mdb"),
        "offsets": offsets_of(os.path.join(paths["lmdb_dir"], "data.mdb"), [
            b"CommandProtocol", b"ZGDeviceEntity", b"DeviceFunctionEntity",
            b"symphonyE1", b"wifibleSymphony", b"symphony_line",
        ]),
    }
    out["Dart App binary"] = {
        "file": paths["dart_app"],
        "offsets": offsets_of(paths["dart_app"], [
            b"AK001-ZJ2", b"AK001-ZJ21", b"AK001-ZJ215", b"AK001-ZJ21413",
            b"ZG-BL-UFO", b"symphonyE1",
        ]),
    }
    # The Swift/ObjC runner binary next to App.framework. It carries a *different*
    # subset of the AK001-* literals (no AK001-ZJ215), which is why the report cites
    # it separately rather than treating "the app" as one binary.
    out["Swift runner binary"] = {
        "file": paths["runner_binary"],
        "offsets": offsets_of(paths["runner_binary"], [
            b"AK001-ZJ2", b"AK001-ZJ21", b"AK001-ZJ215", b"AK001-ZJ21413",
            b"ZG-BL-UFO",
        ]),
    }
    return out


# --------------------------------------------------------------------------
# Product entry assembly
# --------------------------------------------------------------------------

def find_product(devices: List[Dict[str, Any]], pid: int) -> Optional[Dict[str, Any]]:
    for d in devices or []:
        if isinstance(d, dict) and d.get("productId") == pid:
            return d
    return None


def function_set(entry: Optional[Dict[str, Any]]) -> List[str]:
    if not entry:
        return []
    return [f.get("code") for f in entry.get("functions", []) if f.get("code")]


def build_target_entry(js: Dict[str, Any], wifi_devices: Any,
                       panels: Any, lmdb: Dict[str, Any],
                       ble_devices: Any = None) -> Dict[str, Any]:
    js_dev = find_product(js.get("wifiDevices", []), TARGET_PRODUCT_ID)
    js_ble = find_product(js.get("bleDevices", []), TARGET_PRODUCT_ID)
    js_panel = find_product(js.get("panel", []), TARGET_PRODUCT_ID)
    json_dev = find_product(wifi_devices if isinstance(wifi_devices, list) else [],
                            TARGET_PRODUCT_ID)
    ble_dev = find_product(ble_devices if isinstance(ble_devices, list) else [],
                           TARGET_PRODUCT_ID)
    json_panel = find_product(panels if isinstance(panels, list) else [],
                              TARGET_PRODUCT_ID)

    # Attach the global `functions` schema definitions the app uses to
    # interpret each function code.
    fdefs = {f["code"]: f for f in js.get("functions", [])
             if isinstance(f, dict) and "code" in f}
    js_funcs_resolved = []
    for f in (js_dev or {}).get("functions", []):
        d = dict(fdefs.get(f["code"], {}))
        merged = {**d, **f}   # per-product fields (deviceMinVer) win
        js_funcs_resolved.append(merged)

    lmdb_tables = (lmdb or {}).get("tables", {}) if isinstance(lmdb, dict) else {}
    lmdb_pid = TARGET_PRODUCT_ID

    def lmdb_rows(table: str, field: str = "productId") -> List[Dict[str, Any]]:
        return [r for r in lmdb_tables.get(table, [])
                if r.get(field) == lmdb_pid]

    # Resolve LMDB functionIds to codes.
    fn_by_id = {r["id"]: r["code"] for r in lmdb_tables.get("DeviceFunction", [])}
    lmdb_funcs = []
    for r in lmdb_rows("DeviceFunctionEntity"):
        row = dict(r)
        row["functionCode"] = fn_by_id.get(r.get("functionId"))
        lmdb_funcs.append(row)

    return {
        "_meta": {
            "target": {
                "model": TARGET_MODEL_STRING,
                "model_info": TARGET_MODEL_INFO,
                "productId": TARGET_PRODUCT_ID,
                "productId_hex": hex(TARGET_PRODUCT_ID),
                "marketing_name": "Surplife Outdoor Permanent Lighting",
            },
            "sources": {
                "js_bundle": "zg_app_builtin_assest/assets/js_bundle/60_surplife.js",
                "wifi_devices": "magichome2_home_data_provide/assets/wifi_devices.json",
                "wifi_device_panel": "magichome2_home_data_provide/assets/wifi_device_panel.json",
                "lmdb": "briturn_core/assets/zg_device_db/data.mdb",
            },
            "note": (
                "The shipped DB is keyed by numeric productId only. No artifact "
                "contains the string 'AK001-ZJ21413' or 'ZG-BL-UFO'; the mapping "
                "to productId 182 (0xB6) comes from the caller's protocol/model "
                "number, not from the bundle. See model_string_search."
            ),
        },
        "js_bundle": {
            "found": js_dev is not None,
            "wifiDevices_entry": js_dev,
            "functions_resolved": js_funcs_resolved,
            "function_count": len(js_funcs_resolved),
            "has_hexCmdForms": bool((js_dev or {}).get("hexCmdForms")),
            "hexCmdForms": (js_dev or {}).get("hexCmdForms"),
            "protocols": (js_dev or {}).get("protocols"),
            "stateProtocol": (js_dev or {}).get("stateProtocol"),
            "panel": js_panel,
            "also_in_bleDevices": js_ble is not None,
        },
        "json_assets": {
            "found": json_dev is not None,
            "wifi_devices_entry": json_dev,
            "function_count": len(function_set(json_dev)),
            "has_hexCmdForms": bool((json_dev or {}).get("hexCmdForms")),
            "hexCmdForms": (json_dev or {}).get("hexCmdForms"),
            "runtimeScripts": (json_dev or {}).get("runtimeScripts"),
            "responseHexDescribes": (json_dev or {}).get("responseHexDescribes"),
            "protocols": (json_dev or {}).get("protocols"),
            "stateProtocol": (json_dev or {}).get("stateProtocol"),
            "panel": json_panel,
            "ble_devices_entry": ble_dev,
        },
        "lmdb": {
            "found": any(lmdb_rows(t) for t in (
                "ZGDeviceEntity", "CommandProtocol", "StateProtocol",
                "ZGDevicePanelEntity", "ZGDevicePanelTabEntity",
                "DeviceFunctionEntity")),
            "device": lmdb_rows("ZGDeviceEntity"),
            "protocols": lmdb_rows("CommandProtocol"),
            "state_protocols": lmdb_rows("StateProtocol"),
            "functions": lmdb_funcs,
            "function_count": len(lmdb_funcs),
            "panel": lmdb_rows("ZGDevicePanelEntity"),
            "panel_tabs": lmdb_rows("ZGDevicePanelTabEntity"),
        },
    }


def build_all_products_summary(js: Dict[str, Any], wifi_devices: Any,
                               lmdb: Dict[str, Any]) -> Dict[str, Any]:
    """Every productId in every source, with function counts and protocols."""
    target_funcs = set(function_set(
        find_product(js.get("wifiDevices", []), TARGET_PRODUCT_ID)))

    rows = []
    for table in ("wifiDevices", "bleDevices", "sigMeshDevices",
                  "sigMeshDevicesFVT", "sigMeshGWDevices",
                  "wifiDevicesHikariya", "bleDevicesHikariya"):
        for d in js.get(table, []) or []:
            fns = function_set(d)
            fset = set(fns)
            rows.append({
                "source": "60_surplife.js",
                "table": table,
                "productId": d.get("productId"),
                "icon": d.get("icon"),
                "category": d.get("category"),
                "categoryCode": d.get("categoryCode"),
                "function_count": len(fns),
                "functions": fns,
                "protocols": [p.get("name") for p in d.get("protocols", [])],
                "stateProtocol": [p.get("name") for p in d.get("stateProtocol", [])],
                "has_hexCmdForms": bool(d.get("hexCmdForms")),
                "shared_with_target": len(fset & target_funcs),
                "missing_vs_target": sorted(target_funcs - fset),
                "extra_vs_target": sorted(fset - target_funcs),
                "jaccard_vs_target": round(
                    len(fset & target_funcs) / len(fset | target_funcs), 4
                ) if (fset | target_funcs) else None,
            })

    for d in (wifi_devices if isinstance(wifi_devices, list) else []):
        fns = function_set(d)
        rows.append({
            "source": "wifi_devices.json",
            "table": "wifiDevices",
            "productId": d.get("productId"),
            "icon": d.get("icon"),
            "category": d.get("category"),
            "categoryCode": d.get("categoryCode"),
            "function_count": len(fns),
            "functions": fns,
            "protocols": [p.get("name") for p in d.get("protocols", [])],
            "stateProtocol": [p.get("name") for p in d.get("stateProtocol", [])],
            "has_hexCmdForms": bool(d.get("hexCmdForms")),
            "shared_with_target": len(set(fns) & target_funcs),
            "missing_vs_target": sorted(target_funcs - set(fns)),
            "extra_vs_target": sorted(set(fns) - target_funcs),
            "jaccard_vs_target": round(
                len(set(fns) & target_funcs) / len(set(fns) | target_funcs), 4
            ) if (set(fns) | target_funcs) else None,
        })

    lmdb_tables = (lmdb or {}).get("tables", {}) if isinstance(lmdb, dict) else {}
    fn_by_id = {r["id"]: r["code"] for r in lmdb_tables.get("DeviceFunction", [])}
    lmdb_devices: Dict[int, Dict[str, Any]] = {}
    for r in lmdb_tables.get("ZGDeviceEntity", []):
        pid = r.get("productId")
        if pid is None:
            continue
        lmdb_devices.setdefault(pid, {"productId": pid, "functions": [],
                                      "protocols": [], "stateProtocol": [],
                                      "entityTypes": []})
        lmdb_devices[pid]["icon"] = r.get("icon")
        lmdb_devices[pid]["category"] = r.get("category")
        lmdb_devices[pid]["categoryCode"] = r.get("categoryCode")
        if r.get("entityType"):
            lmdb_devices[pid]["entityTypes"].append(r["entityType"])
    for r in lmdb_tables.get("DeviceFunctionEntity", []):
        d = lmdb_devices.get(r.get("productId"))
        if d is not None:
            code = fn_by_id.get(r.get("functionId"))
            if code:
                d["functions"].append(code)
    for r in lmdb_tables.get("CommandProtocol", []):
        d = lmdb_devices.get(r.get("productId"))
        if d is not None and r.get("name"):
            d["protocols"].append(r["name"])
    for r in lmdb_tables.get("StateProtocol", []):
        d = lmdb_devices.get(r.get("productId"))
        if d is not None and r.get("name"):
            d["stateProtocol"].append(r["name"])

    for pid, d in sorted(lmdb_devices.items()):
        fns = d["functions"]
        rows.append({
            "source": "data.mdb (LMDB)",
            "table": "ZGDeviceEntity+DeviceFunctionEntity",
            "productId": pid,
            "icon": d.get("icon"),
            "category": d.get("category"),
            "categoryCode": d.get("categoryCode"),
            "entityTypes": d.get("entityTypes"),
            "function_count": len(fns),
            "functions": fns,
            "protocols": d["protocols"],
            "stateProtocol": d["stateProtocol"],
            "has_hexCmdForms": False,   # LMDB has no hexCmdForms table
            "shared_with_target": len(set(fns) & target_funcs),
            "missing_vs_target": sorted(target_funcs - set(fns)),
            "extra_vs_target": sorted(set(fns) - target_funcs),
            "jaccard_vs_target": round(
                len(set(fns) & target_funcs) / len(set(fns) | target_funcs), 4
            ) if (set(fns) | target_funcs) else None,
        })

    # Sibling ranking: closest products by Jaccard similarity on functions.
    siblings = [r for r in rows
                if r["source"] == "60_surplife.js"
                and r["table"] == "wifiDevices"
                and r["productId"] != TARGET_PRODUCT_ID
                and r["jaccard_vs_target"] is not None]
    siblings.sort(key=lambda r: (-r["jaccard_vs_target"], -r["shared_with_target"]))

    return {
        "_meta": {
            "target_product_id": TARGET_PRODUCT_ID,
            "target_function_count": len(target_funcs),
            "note": (
                "`shared_with_target` / `missing_vs_target` / `extra_vs_target` "
                "compare each product's function *code* set against productId 182."
            ),
        },
        "counts_by_source": {
            "60_surplife.js wifiDevices": len(js.get("wifiDevices", []) or []),
            "60_surplife.js bleDevices": len(js.get("bleDevices", []) or []),
            "60_surplife.js sigMeshDevices": len(js.get("sigMeshDevices", []) or []),
            "60_surplife.js sigMeshGWDevices": len(js.get("sigMeshGWDevices", []) or []),
            "wifi_devices.json": len(wifi_devices) if isinstance(wifi_devices, list) else 0,
            "data.mdb ZGDeviceEntity": len(lmdb_tables.get("ZGDeviceEntity", [])),
        },
        "closest_siblings_js_wifi": siblings[:25],
        "products": rows,
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", default=DEFAULT_APP, help="path to Surplife.app")
    ap.add_argument("--out", default=HERE, help="output directory")
    ap.add_argument("--no-lmdb", action="store_true", help="skip data.mdb")
    args = ap.parse_args()

    paths = asset_paths(args.app)
    os.makedirs(args.out, exist_ok=True)

    print("[1/5] evaluating JS bundle ...", file=sys.stderr)
    js = load_js_bundle(paths["js_bundle"])
    if "__error" in js:
        print("  WARNING:", js["__error"], file=sys.stderr)

    print("[2/5] loading JSON assets ...", file=sys.stderr)
    wifi_devices = load_json(paths["wifi_devices"])
    ble_devices = load_json(paths["ble_devices"])
    wifi_panel = load_json(paths["wifi_device_panel"])

    print("[3/5] decoding LMDB ...", file=sys.stderr)
    lmdb_data: Dict[str, Any] = {}
    if not args.no_lmdb:
        lmdb_data = decode_lmdb(paths["lmdb_dir"])
        if "__error" in lmdb_data:
            print("  WARNING:", lmdb_data["__error"], file=sys.stderr)
    else:
        lmdb_data = {"__skipped": "--no-lmdb"}

    print("[4/5] scanning command families ...", file=sys.stderr)
    coverage = scan_command_families(paths)
    model_search = search_model_strings(paths)

    print("[5/5] writing outputs ...", file=sys.stderr)

    target = build_target_entry(js, wifi_devices, wifi_panel, lmdb_data, ble_devices)
    target["command_family_coverage"] = coverage
    target["model_string_search"] = model_search
    target["provenance_offsets"] = locate_offsets(paths)
    _write(os.path.join(args.out, "device_db_AK001-ZJ21413.json"), target)

    summary = build_all_products_summary(js, wifi_devices, lmdb_data)
    _write(os.path.join(args.out, "all_products_summary.json"), summary)

    _write(os.path.join(args.out, "command_family_coverage.json"), {
        "families": list(COMMAND_FAMILIES),
        "note": (
            "Raw byte scan, case-insensitive. `offsets` are byte offsets into "
            "the named artifact. False positives are NOT filtered here. Known "
            "false positives, all verified by reading the surrounding bytes: "
            "(1) 'e121' in the Dart App binary at 76466688 is the symbol "
            "'#ffiClosure121'; (2) 'e006' in the Dart App binary at 84415024 "
            "is the localisation key 'common_e006'; (3) 'e122'/'e123' hits in "
            "PNG/TTF files are image/font pixel or glyph data. Only "
            "wifi_dp_cmd.json (and its HIKARIYA twin) contain real command "
            "opcodes; the genuine ones are e001, e002, e006, e008, e00a, e00d."
        ),
        "coverage": coverage,
        "model_string_search": model_search,
    })

    if lmdb_data and "__error" not in lmdb_data:
        _write(os.path.join(args.out, "lmdb_dump.json"), lmdb_data)

    print("done. outputs in", args.out, file=sys.stderr)
    return 0


def _write(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("  wrote", path, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
