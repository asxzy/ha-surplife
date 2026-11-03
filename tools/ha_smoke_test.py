"""Boot a real Home Assistant with the component installed as a custom integration.

The unit tests use the HA test harness, which stubs a lot. This script does the
opposite: it starts an actual ``hass`` instance from a throwaway config
directory, loads ``custom_components/surplife`` the way a user's installation
would, and reports whether setup succeeds. It is the closest thing to a
deployment smoke test that does not need shell access to the real box.

Usage::

    .venv/bin/python tools/ha_smoke_test.py

Exit code is 0 when the component imports, the config flow starts, and the
services register against a loaded entry.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COMPONENT = REPO / "custom_components" / "surplife"


def prepare_config(config_dir: Path) -> None:
    """Lay out a minimal config directory with the component installed."""
    (config_dir / "custom_components").mkdir(parents=True)
    shutil.copytree(COMPONENT, config_dir / "custom_components" / "surplife")
    # Translation files must be present for entity names to resolve.
    assert (config_dir / "custom_components" / "surplife" / "translations").is_dir()


async def run() -> int:
    """Start Home Assistant and exercise the integration."""
    from homeassistant import bootstrap, core, loader

    with tempfile.TemporaryDirectory(prefix="surplife-ha-") as tmp:
        config_dir = Path(tmp)
        prepare_config(config_dir)

        hass = core.HomeAssistant(str(config_dir))
        loader.async_setup(hass)
        await hass.config.async_load()

        config = {"homeassistant": {}, "logger": {"default": "warning"}}
        if await bootstrap.async_from_config_dict(config, hass) is None:
            print("  [FAIL] home assistant failed to start")
            return 1

        results: list[tuple[str, bool, str]] = []

        # 1. The component is discoverable by the loader and its manifest is
        #    valid -- a bad manifest fails here, not in unit tests.
        integration = await loader.async_get_integration(hass, "surplife")
        results.append(
            (
                "manifest loads",
                integration.version is not None,
                f"version={integration.version} "
                f"requirements={integration.requirements or 'none'}",
            )
        )

        # 2. A config flow can be started. This exercises strings.json,
        #    translations, the flow module and the manifest together -- all
        #    the things that break on a real install but not in unit tests.
        flow = await hass.config_entries.flow.async_init(
            "surplife", context={"source": "user"}
        )
        results.append(
            (
                "config flow starts",
                flow.get("type") in ("form", "create_entry"),
                f"type={flow.get('type')} step={flow.get('step_id')}",
            )
        )

        # 3. The service handlers register against a loaded entry. Hardware is
        #    absent here, so a fake entry is added directly; the coordinator's
        #    first poll fails and is tolerated, which is exactly the path a
        #    temporarily-offline controller takes.
        import yaml

        services_yaml = COMPONENT / "services.yaml"
        declared = set(yaml.safe_load(services_yaml.read_text()))
        registered = set()
        try:
            from homeassistant.config_entries import ConfigEntry

            entry = ConfigEntry(
                domain="surplife",
                title="Smoke Test",
                data={"host": "192.0.2.1", "name": "Smoke Test", "led_count": 35},
                entry_id="smoke",
                unique_id="smoke",
                version=1,
                minor_version=1,
                source="user",
                options={},
                discovery_keys={},
                subentries_data={},
            )
            # ``async_add`` loads the entry, which runs the integration's
            # async_setup_entry and registers the services.
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            ok = entry.state.name == "LOADED"
            registered = {
                name for name in declared if hass.services.has_service("surplife", name)
            }
            results.append(
                (
                    "services register",
                    ok and registered == declared,
                    f"{len(registered)}/{len(declared)} handlers",
                )
            )
        except Exception as err:
            results.append(("services register", False, f"{type(err).__name__}: {err}"))

        # 4. The per-effect control panel must actually render. A schema that
        #    builds but does not serialize is the silent failure this exists to
        #    catch: the form opens and every field is missing, which looks like
        #    "the integration has no options" rather than an error.
        try:
            flow = await hass.config_entries.options.async_init("smoke")
            steps_seen = [flow.get("step_id")]
            # Walk into the effect panel for a multi-colour effect.
            flow = await hass.config_entries.options.async_configure(
                flow["flow_id"], {"next_step_id": "effect"}
            )
            steps_seen.append(flow.get("step_id"))
            flow = await hass.config_entries.options.async_configure(
                flow["flow_id"], {"effect": "Static Gradient"}
            )
            steps_seen.append(flow.get("step_id"))
            schema = flow["data_schema"]
            fields = {str(key) for key in schema.schema}
            serialized = schema.schema and all(key.schema for key in schema.schema)
            ok = (
                flow.get("type") == "form"
                and "color_1" in fields
                and "color_4" in fields
                and "color_5" not in fields
                and "direction" not in fields
                and bool(serialized)
            )
            results.append(
                (
                    "effect panel renders",
                    ok,
                    f"steps={'/'.join(str(s) for s in steps_seen)} "
                    f"fields={len(fields)}",
                )
            )
            hass.config_entries.options.async_abort(flow["flow_id"])
        except Exception as err:
            results.append(
                ("effect panel renders", False, f"{type(err).__name__}: {err}")
            )

        # 5. The per-LED look panel, which has a different generated shape.
        try:
            flow = await hass.config_entries.options.async_init("smoke")
            flow = await hass.config_entries.options.async_configure(
                flow["flow_id"], {"next_step_id": "look"}
            )
            flow = await hass.config_entries.options.async_configure(
                flow["flow_id"], {"look": "stripes"}
            )
            fields = {str(key) for key in flow["data_schema"].schema}
            ok = (
                flow.get("type") == "form"
                and {"color_1", "color_2", "scribble_effect"} <= fields
                and "color_3" not in fields
            )
            results.append(("look panel renders", ok, f"fields={len(fields)}"))
            hass.config_entries.options.async_abort(flow["flow_id"])
        except Exception as err:
            results.append(
                ("look panel renders", False, f"{type(err).__name__}: {err}")
            )

        await hass.async_stop()

        width = max(len(name) for name, _, _ in results)
        failures = 0
        for name, ok, detail in results:
            mark = "PASS" if ok else "FAIL"
            if not ok:
                failures += 1
            print(f"  [{mark}] {name:<{width}}  {detail}")

        return 0 if failures == 0 else 1


def main() -> int:
    """Entry point."""
    logging.basicConfig(level=logging.WARNING)
    try:
        return asyncio.run(run())
    except Exception as err:
        print(f"  [FAIL] home assistant boot: {type(err).__name__}: {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
