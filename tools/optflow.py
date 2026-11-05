"""Drive a Home Assistant options flow over the API, for live checks.

The Configure dialog is not routable by URL, and its form lives inside shadow
roots, so clicking through it in a browser is both fragile and a poor record of
what was actually submitted. This drives the same HTTP endpoints the dialog
drives, ``/api/config/config_entries/options/flow``, so a check can say "open the
per-LED editor, paint two groups, submit" and print exactly what came back.

Not part of the shipped integration and not imported by it -- this is a
development tool, like ``ha_deploy.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import aiohttp

DEFAULT_URL = os.environ.get("HA_URL", "http://192.168.1.253:8123")


class Flow:
    """One options flow, driven over the config-entries HTTP API."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self._base = f"{url}/api/config/config_entries/options/flow"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._session.post(
            f"{self._base}{path}", json=payload, headers=self._headers
        ) as response:
            body = await response.json()
            if response.status != 200:
                raise RuntimeError(f"{response.status}: {body}")
            return body

    async def start(self, entry_id: str) -> dict[str, Any]:
        return await self._post("", {"handler": entry_id})

    async def open_step(self, flow_id: str, step_id: str) -> dict[str, Any]:
        """Pick a menu item, which is how every panel is reached."""
        return await self._post(f"/{flow_id}", {"next_step_id": step_id})

    async def submit(self, flow_id: str, user_input: dict[str, Any]) -> dict[str, Any]:
        return await self._post(f"/{flow_id}", user_input)


def _fields(schema) -> dict[str, Any]:
    """``name -> default`` for each field, as the dialog would render it."""
    fields: dict[str, Any] = {}
    for entry in schema or ():
        if isinstance(entry, dict) and entry.get("name") is not None:
            fields[entry["name"]] = entry.get("default")
        elif isinstance(entry, dict) and "schema" in entry:
            fields.update(_fields(entry["schema"]))
        else:
            fields[str(entry)] = None
    return fields


def describe(result: dict[str, Any]) -> dict[str, Any]:
    """The parts of a flow result worth printing."""
    keep = {
        key: result[key]
        for key in (
            "type",
            "step_id",
            "errors",
            "reason",
            "description_placeholders",
            "menu_options",
        )
        if key in result
    }
    if "data_schema" in result:
        keep["fields"] = _fields(result["data_schema"])
    return keep


async def amain(args) -> None:
    token = os.environ.get("HA_TOKEN")
    if not token:
        raise SystemExit("set HA_TOKEN (or source .env)")
    async with aiohttp.ClientSession() as session:
        flow = Flow(session, args.url, token)
        result = await flow.start(args.entry)
        print("start:", json.dumps(describe(result), ensure_ascii=False))

        for step in args.steps:
            if step == "show":
                # An empty submit is how the dialog re-renders a form.
                result = await flow.submit(result["flow_id"], {})
            elif step.lstrip().startswith("{"):
                result = await flow.submit(result["flow_id"], json.loads(step))
            else:
                result = await flow.open_step(result["flow_id"], step)
            print("step:", json.dumps(describe(result), ensure_ascii=False))
            if result["type"] != "form":
                break


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", required=True, help="surplife config entry id")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--steps",
        nargs="*",
        default=[],
        metavar="STEP",
        help='a step id to open, a JSON object to submit, or "show" to re-render',
    )
    args = parser.parse_args()
    asyncio.run(amain(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
