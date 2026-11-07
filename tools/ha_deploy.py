"""Deploy the integration to Home Assistant through its add-on store.

This is the supported deploy path: the store owns the files under
``/config/custom_components/surplife``, so writing them out of band would leave
its own record disagreeing with the disk. The only robust sequence is

    refresh its view of the repo -> install the release -> restart

which is what this script does. It needs a long-lived access token, not shell
access to the box, so it works against a stock Home Assistant OS install.

**Two stores, one script.** Home Assistant 2026.11 replaced the HACS custom
integration with the built-in ``marketplace`` integration. Both do the same job
through a websocket API, but the command names and argument names differ, so the
script detects which one is running and speaks that dialect. On an instance that
has both, Marketplace wins: it is the one that owns installs from now on.

Usage::

    set -a && . ./.env && set +a
    .venv/bin/python tools/ha_deploy.py            # sync to the latest release
    .venv/bin/python tools/ha_deploy.py --watch    # ... and wait until loaded
    .venv/bin/python tools/ha_deploy.py --status   # report only, change nothing

Environment:

    HA_URL    base URL, e.g. http://192.168.1.10:8123
    HA_TOKEN  long-lived access token

Exit code is 0 only when the store reports the installed version equal to the
``manifest.json`` version in this checkout and every ``surplife`` config entry is
loaded.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "custom_components" / "surplife" / "manifest.json"

#: The store's internal id for this repository, stable for its lifetime. It is
#: the GitHub repository id, which both HACS and Marketplace use.
REPOSITORY_ID = "1449019577"


@dataclass(frozen=True)
class Store:
    """The websocket dialect of whichever store owns the install.

    ``info`` and ``refresh`` take the same shape in both, but ``refresh`` names
    its argument ``repository`` while ``info`` names it ``repository_id``, and
    the install command is called ``download`` on HACS and ``install`` on
    Marketplace. Keeping the three names here means the rest of the script never
    asks which one is running.
    """

    name: str
    info: str
    refresh: str
    install: str
    #: Marketplace spells the install target ``repository``; HACS does too, so
    #: only the command name differs. Kept explicit rather than derived.
    install_key: str = "repository"


MARKETPLACE = Store(
    name="Marketplace",
    info="marketplace/repository/info",
    refresh="marketplace/repository/refresh",
    install="marketplace/repository/install",
)

HACS = Store(
    name="HACS",
    info="hacs/repository/info",
    refresh="hacs/repository/refresh",
    install="hacs/repository/download",
)


def _settings() -> tuple[str, str]:
    """Base URL and token, or a clear error rather than a traceback."""
    url = os.environ.get("HA_URL", "").rstrip("/")
    token = os.environ.get("HA_TOKEN", "")
    if not url:
        sys.exit("HA_URL is not set (copy .env.example to .env and fill it in)")
    if not token:
        sys.exit("HA_TOKEN is not set (copy .env.example to .env and fill it in)")
    return url, token


def _expected_version() -> str:
    """The version this checkout would release."""
    return json.loads(MANIFEST.read_text())["version"]


class _Ids:
    """Monotonic websocket message ids.

    Home Assistant rejects an id lower than one it has already seen with
    ``id_reuse``, and the store probe runs before the real commands, so ids
    cannot simply be constants.
    """

    def __init__(self) -> None:
        self._next = 1

    def take(self) -> int:
        """The next id."""
        ident = self._next
        self._next += 1
        return ident


async def _call(
    ws: aiohttp.ClientWebSocketResponse,
    ids: _Ids,
    kind: str,
    *,
    retries: int = 12,
    delay: float = 10.0,
    **kw,
):
    """Run one websocket command, retrying while the store is still loading.

    Right after a restart the websocket answers ``auth_ok`` before the store has
    registered its commands, so an immediate call fails with ``unknown_command``
    even though nothing is wrong. Retrying turns that race into a short wait
    instead of a confusing error.
    """
    error: dict | None = None
    for attempt in range(retries):
        await ws.send_json({"id": ids.take(), "type": kind, **kw})
        reply = await ws.receive_json()
        error = reply.get("error") or {}
        if error.get("code") == "unknown_command" and attempt < retries - 1:
            await asyncio.sleep(delay)
            continue
        return reply.get("success"), reply.get("result"), reply.get("error")
    return False, None, error


async def _detect_store(
    ws: aiohttp.ClientWebSocketResponse, ids: _Ids
) -> Store:
    """Which store is running: Marketplace if present, otherwise HACS."""
    ok, _, _ = await _call(ws, ids, "marketplace/info", retries=1)
    return MARKETPLACE if ok else HACS


async def _connect(
    session: aiohttp.ClientSession, url: str, token: str
) -> aiohttp.ClientWebSocketResponse:
    """Open an authenticated Home Assistant websocket."""
    ws = await session.ws_connect(
        f"{url}/api/websocket", heartbeat=60, max_msg_size=0
    )
    await ws.receive_json()
    await ws.send_json({"type": "auth", "access_token": token})
    reply = await ws.receive_json()
    if reply.get("type") != "auth_ok":
        await ws.close()
        sys.exit(f"websocket auth failed: {reply.get('message', reply)}")
    return ws


async def _wait_for_api(url: str, token: str, timeout: float = 180.0) -> bool:
    """Wait until ``/api/config`` answers, for runs that start mid-restart."""
    deadline = time.monotonic() + timeout
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        while time.monotonic() < deadline:
            try:
                async with session.get(
                    f"{url}/api/config",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(10),
                ) as response:
                    if response.status == 200:
                        return True
            except (TimeoutError, aiohttp.ClientError):
                pass
            await asyncio.sleep(5)
    return False


async def _repository(
    ws: aiohttp.ClientWebSocketResponse, store: Store, ids: _Ids
) -> dict | None:
    """The store's record for this repository, or ``None``."""
    ok, info, err = await _call(
        ws, ids, store.info, repository_id=REPOSITORY_ID
    )
    if not ok or not info:
        print(f"  info request failed: {err}", file=sys.stderr)
        return None
    return info


async def _status(url: str, token: str) -> dict:
    """Report what the store has installed and whether the entries are loaded.

    ``max_msg_size=0`` is deliberate: ``repository/info`` includes the README,
    and this repository's is large enough to trip aiohttp's 4 MiB default.
    """
    if not await _wait_for_api(url, token):
        sys.exit(f"Home Assistant at {url} is not answering")
    async with aiohttp.ClientSession() as session:
        ws = await _connect(session, url, token)
        try:
            ids = _Ids()
            store = await _detect_store(ws, ids)
            info = await _repository(ws, store, ids)
            if info is None:
                sys.exit(
                    f"{store.name} does not know repository {REPOSITORY_ID}; "
                    "add it as a custom repository first"
                )

            _, entries, err = await _call(ws, ids, "config_entries/get")
            if err:
                sys.exit(f"could not list config entries: {err}")
        finally:
            await ws.close()

    ours = [e for e in entries if e.get("domain") == "surplife"]
    return {
        "store": store.name,
        "installed": info.get("installed_version"),
        "available": info.get("available_version"),
        "pending": info.get("pending_upgrade"),
        "entries": [(e.get("title"), e.get("state")) for e in ours],
    }


async def _sync(url: str, token: str, tag: str | None = None) -> None:
    """Refresh the store's view of the repo, then install a release.

    ``tag`` selects which release to install. Without it the store installs the
    latest stable, which is the normal path. With it, a ``-dev`` prerelease can
    be installed by name -- see :func:`_tag_for_commit` in
    ``tools/publish_dev_tag.py`` for why a commit is deployed as a tag rather
    than as a bare SHA.
    """
    async with aiohttp.ClientSession() as session:
        ws = await _connect(session, url, token)
        try:
            ids = _Ids()
            store = await _detect_store(ws, ids)
            print(f"  store:        {store.name} (websocket)")

            # Without the refresh, the store serves its cached idea of the latest
            # release, so a release published minutes ago looks absent.
            ok, _, err = await _call(
                ws, ids, store.refresh, repository=REPOSITORY_ID
            )
            print(f"  refresh:      {'ok' if ok else err}")

            extra = {} if tag is None else {"version": tag}
            ok, _, err = await _call(
                ws, ids, store.install, **{store.install_key: REPOSITORY_ID}, **extra
            )
            print(f"  install:      {'ok' if ok else err}")
        finally:
            await ws.close()


async def _restart(url: str, token: str) -> None:
    """Restart the core process, which is what loads the new files."""
    try:
        async with aiohttp.ClientSession() as session, session.post(
            f"{url}/api/services/homeassistant/restart",
            headers={"Authorization": f"Bearer {token}"},
        ) as response:
            print(f"  restart:      HTTP {response.status}")
    except (TimeoutError, aiohttp.ClientError):
        # The process tears the connection down as it restarts, so a dropped
        # socket here is the expected outcome rather than a failure.
        print("  restart:      connection dropped (expected)")


async def _wait_until_loaded(url: str, token: str, timeout: float = 300.0) -> bool:
    """Poll until the API answers and the entity count looks like a full boot.

    A restarted Home Assistant answers ``/api/config`` tens of seconds before it
    has finished loading platforms, so the entity count is the signal that
    matters. Integration setup lands well after the API is back.
    """
    deadline = time.monotonic() + timeout
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        while time.monotonic() < deadline:
            try:
                async with session.get(
                    f"{url}/api/states",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(15),
                ) as response:
                    if response.status == 200:
                        count = len(await response.json())
                        if count > 3000:
                            print(f"  loaded:       {count} states")
                            return True
            except (TimeoutError, aiohttp.ClientError):
                pass
            await asyncio.sleep(8)
    print("  timed out waiting for a full boot")
    return False


async def run(args: argparse.Namespace) -> int:
    """Sync, restart if needed, then verify."""
    url, token = _settings()
    expected = args.tag or f"v{_expected_version()}"
    if args.tag:
        print(f"target tag:       {args.tag}")
    else:
        print(f"checkout version: {_expected_version()}")

    before = await _status(url, token)
    print(f"{before['store']} installed: {before['installed']}")

    if not args.status:
        await _sync(url, token, args.tag)

    after = await _status(url, token)
    stale = after["installed"] != expected and after["installed"] != expected.lstrip(
        "v"
    )
    if stale:
        print(
            f"error: {after['store']} reports {after['installed']} but the deploy "
            f"targets {expected}; publish the tag first (see docs/deployment.md)",
            file=sys.stderr,
        )
        return 1

    # The files on disk are new, but the running process still holds the old
    # modules, so even a same-version reinstall needs a restart to take effect.
    if not args.status:
        await _restart(url, token)

    if (args.watch or not args.status) and not await _wait_until_loaded(url, token):
        return 1

    final = await _status(url, token)
    print(f"running:          {final['installed']}")
    for title, state in final["entries"]:
        print(f"  {title}: {state}")
    loaded = [e for e in final["entries"] if e[1] == "loaded"]
    if not final["entries"]:
        print("error: no surplife config entries found", file=sys.stderr)
        return 1
    if len(loaded) != len(final["entries"]):
        print("error: some entries did not load", file=sys.stderr)
        return 1
    print("ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--status", action="store_true", help="report only; change nothing"
    )
    parser.add_argument(
        "--watch", action="store_true", help="wait for a full boot after restarting"
    )
    parser.add_argument(
        "--tag",
        metavar="TAG",
        help=(
            "install this release tag instead of the latest, e.g. a -dev "
            "prerelease built from one commit"
        ),
    )
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
