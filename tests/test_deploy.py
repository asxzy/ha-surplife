"""Tests for the integration-store deploy script.

The script's job is to notice when Home Assistant and the store disagree, so
most of these tests are about the *checks* rather than the happy path: a deploy
that reports success while installing nothing is the failure mode worth
guarding.

Nothing here talks to a real server. The websocket is faked with a small
responder that mimics the shapes Marketplace and Home Assistant return.

Home Assistant 2026.11 replaced the HACS custom integration with the built-in
``marketplace`` integration. The script speaks both dialects, so the tests cover
both: which one is picked, that each sends its own command names, and that the
websocket ids keep increasing — Home Assistant rejects a repeat with
``id_reuse``, which made the store probe and the real commands collide.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

DEPLOY_PATH = Path(__file__).resolve().parent.parent / "tools" / "ha_deploy.py"


@pytest.fixture(scope="module")
def deploy():
    """Load ``tools/ha_deploy.py`` without running ``main()``."""
    spec = importlib.util.spec_from_file_location("ha_deploy_under_test", DEPLOY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def test_expected_version_comes_from_the_manifest(deploy) -> None:
    """The version to install is the one in this checkout, not a constant."""
    manifest = json.loads(
        (
            DEPLOY_PATH.parent.parent
            / "custom_components"
            / "surplife"
            / "manifest.json"
        ).read_text()
    )
    assert deploy._expected_version() == manifest["version"]


def test_missing_token_is_a_clear_error(deploy, monkeypatch, capsys) -> None:
    """A missing token must not traceback, because that looks like a bug."""
    monkeypatch.setenv("HA_URL", "http://example.invalid:8123")
    monkeypatch.delenv("HA_TOKEN", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        deploy._settings()
    assert "HA_TOKEN" in str(excinfo.value)


def test_missing_url_is_a_clear_error(deploy, monkeypatch) -> None:
    """Same for the URL."""
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.setenv("HA_TOKEN", "token")
    with pytest.raises(SystemExit) as excinfo:
        deploy._settings()
    assert "HA_URL" in str(excinfo.value)


class FakeWS:
    """A websocket that answers the commands the deploy script sends."""

    def __init__(self, replies: dict[str, object]) -> None:
        self.replies = replies
        self.sent: list[dict] = []
        self._pending: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)
        kind = payload["type"]
        if kind == "auth":
            self._pending.append({"type": "auth_ok"})
            return
        reply = self.replies.get(kind, {"success": True, "result": {}})
        self._pending.append({"id": payload["id"], **reply})

    async def receive_json(self) -> dict:
        return self._pending.pop(0)

    async def close(self) -> None:
        return None


# -- message ids -----------------------------------------------------------


def test_ids_strictly_increase(deploy) -> None:
    """Home Assistant rejects a repeated or lower id with ``id_reuse``.

    The store probe runs before the real commands on the same connection, so
    constant ids -- which the first version used -- made the very next call fail.
    """
    ids = deploy._Ids()
    taken = [ids.take() for _ in range(5)]
    assert taken == sorted(taken)
    assert len(set(taken)) == len(taken)
    assert taken[0] >= 1


# -- choosing a dialect ----------------------------------------------------


def test_marketplace_wins_when_both_are_present(deploy) -> None:
    """An instance mid-migration has both; Marketplace owns installs from then on."""

    async def main():
        ws = FakeWS({"marketplace/info": {"success": True, "result": {}}})
        return await deploy._detect_store(ws, deploy._Ids())

    import asyncio

    assert asyncio.run(main()) is deploy.MARKETPLACE


def test_hacs_is_used_when_marketplace_is_absent(deploy) -> None:
    """Older Home Assistant has only HACS, and must keep working."""

    async def main():
        ws = FakeWS(
            {
                "marketplace/info": {
                    "success": False,
                    "error": {"code": "unknown_command", "message": "Unknown."},
                }
            }
        )
        return await deploy._detect_store(ws, deploy._Ids())

    import asyncio

    assert asyncio.run(main()) is deploy.HACS


def test_the_two_dialects_differ_in_the_expected_places(deploy) -> None:
    """Pin the names, so a typo cannot silently become a no-op command."""
    assert deploy.MARKETPLACE.info == "marketplace/repository/info"
    assert deploy.MARKETPLACE.refresh == "marketplace/repository/refresh"
    assert deploy.MARKETPLACE.install == "marketplace/repository/install"
    assert deploy.HACS.info == "hacs/repository/info"
    assert deploy.HACS.refresh == "hacs/repository/refresh"
    assert deploy.HACS.install == "hacs/repository/download"
    # Both spell the target ``repository``, but that is a fact to check rather
    # than assume: Marketplace added a ``repository_id`` to ``info`` alone.
    assert deploy.MARKETPLACE.install_key == "repository"
    assert deploy.HACS.install_key == "repository"


# -- retrying --------------------------------------------------------------


async def test_call_retries_while_the_store_is_still_loading(deploy) -> None:
    """``unknown_command`` right after a restart is a race, not a failure.

    Home Assistant answers ``auth_ok`` before the store has registered its
    commands, so the first call after a restart legitimately fails. The script
    must wait rather than declare the repo missing.
    """
    ws = FakeWS({})
    calls = {"n": 0}

    async def send_json(payload: dict) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            ws._pending.append(
                {
                    "id": payload["id"],
                    "success": False,
                    "error": {"code": "unknown_command", "message": "Unknown command."},
                }
            )
        else:
            ws._pending.append(
                {"id": payload["id"], "success": True, "result": {"ok": True}}
            )

    ws.send_json = send_json  # type: ignore[method-assign]
    ok, result, _ = await deploy._call(
        ws, deploy._Ids(), "marketplace/repository/info", delay=0
    )
    assert ok is True
    assert result == {"ok": True}
    assert calls["n"] == 2


async def test_call_gives_up_after_the_retry_budget(deploy) -> None:
    """A genuinely missing store must fail rather than hang forever."""
    ws = FakeWS({})
    ws.send_json = lambda payload: _unknown(ws, payload)  # type: ignore[assignment]
    ok, result, error = await deploy._call(
        ws, deploy._Ids(), "marketplace/repository/info", retries=2, delay=0
    )
    assert ok is False
    assert result is None
    assert error["code"] == "unknown_command"


async def test_call_does_not_retry_a_real_error(deploy) -> None:
    """Only ``unknown_command`` means "not loaded yet"; anything else is final."""
    ws = FakeWS(
        {
            "marketplace/repository/install": {
                "success": False,
                "error": {"code": "github_rate_limited", "message": "slow down"},
            }
        }
    )
    ok, result, error = await deploy._call(
        ws, deploy._Ids(), "marketplace/repository/install", delay=0
    )
    assert ok is False
    assert result is None
    assert error["code"] == "github_rate_limited"
    assert len(ws.sent) == 1, "a real error must be answered once, not retried"


async def _unknown(ws, payload):
    ws._pending.append(
        {
            "id": payload["id"],
            "success": False,
            "error": {"code": "unknown_command", "message": "Unknown command."},
        }
    )


# -- the checks that make a deploy trustworthy -----------------------------


def _status(installed: str, entries=None) -> dict:
    """A status payload in the shape ``_status`` returns."""
    return {
        "store": "Marketplace",
        "installed": installed,
        "available": installed,
        "pending": False,
        "entries": [("Soffit West", "loaded")] if entries is None else entries,
    }


async def test_a_version_mismatch_fails_the_deploy(deploy, monkeypatch) -> None:
    """The store claiming a different version than the target must stop the run.

    This is the trap the script exists to catch: the store can report a release
    as installed while the files under ``custom_components`` are something else.
    Trusting its own record would report a false success.
    """
    monkeypatch.setenv("HA_URL", "http://example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "token")

    async def fake_status(url, token):
        return _status("v0.0.9")

    async def fake_sync(url, token, tag=None):
        return None

    async def fake_restart(url, token):
        raise AssertionError("must not restart when the version does not match")

    monkeypatch.setattr(deploy, "_status", fake_status)
    monkeypatch.setattr(deploy, "_sync", fake_sync)
    monkeypatch.setattr(deploy, "_restart", fake_restart)

    args = deploy.argparse.Namespace(status=False, watch=False, tag=None)
    code = await deploy.run(args)
    assert code == 1


async def test_an_unloaded_entry_fails_the_deploy(deploy, monkeypatch) -> None:
    """A stale module leaves entities unavailable; that must not pass."""
    monkeypatch.setenv("HA_URL", "http://example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "token")
    expected = deploy._expected_version()

    async def fake_status(url, token):
        return _status(f"v{expected}", [("Soffit West", "setup_error")])

    monkeypatch.setattr(deploy, "_status", fake_status)
    args = deploy.argparse.Namespace(status=True, watch=False, tag=None)
    code = await deploy.run(args)
    assert code == 1


async def test_a_tag_deploy_verifies_the_tag_not_the_checkout(
    deploy, monkeypatch
) -> None:
    """``--tag`` must check the tag, not ``manifest.json``.

    A dev tag is deliberately *not* the manifest version -- it is a commit that
    has not been blessed. Comparing against the checkout would either fail a
    correct deploy or, worse, accept a silent fallback to the latest stable.
    """
    monkeypatch.setenv("HA_URL", "http://example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "token")
    calls: list[str | None] = []

    async def fake_status(url, token):
        return _status("v0.1.7-dev1")

    async def fake_sync(url, token, tag=None):
        calls.append(tag)

    async def fake_restart(url, token):
        return None

    async def fake_loaded(url, token):
        return True

    monkeypatch.setattr(deploy, "_status", fake_status)
    monkeypatch.setattr(deploy, "_sync", fake_sync)
    monkeypatch.setattr(deploy, "_restart", fake_restart)
    monkeypatch.setattr(deploy, "_wait_until_loaded", fake_loaded)

    args = deploy.argparse.Namespace(status=False, watch=False, tag="v0.1.7-dev1")
    code = await deploy.run(args)

    assert code == 0
    assert calls == ["v0.1.7-dev1"], "the tag was not passed through to the store"


async def test_a_tag_deploy_fails_when_the_store_installed_something_else(
    deploy, monkeypatch
) -> None:
    """A silent fallback to the latest stable release is a failure.

    The store is happy to install the newest stable when a prerelease is not
    visible to it. Reporting success then would leave the box running code
    nobody asked for, which is exactly the confusion a tag deploy avoids.
    """
    monkeypatch.setenv("HA_URL", "http://example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "token")

    async def fake_status(url, token):
        return _status("v0.1.6")

    async def fake_sync(url, token, tag=None):
        return None

    async def fake_restart(url, token):
        raise AssertionError("must not restart after installing the wrong version")

    monkeypatch.setattr(deploy, "_status", fake_status)
    monkeypatch.setattr(deploy, "_sync", fake_sync)
    monkeypatch.setattr(deploy, "_restart", fake_restart)

    args = deploy.argparse.Namespace(status=False, watch=False, tag="v0.1.7-dev1")
    assert await deploy.run(args) == 1


async def test_a_stable_deploy_uses_the_manifest_version(deploy, monkeypatch) -> None:
    """Without ``--tag`` the target is ``v<manifest version>``, not ``latest``.

    Comparing against a floating "latest" would pass a deploy of whatever the
    store happened to think was newest.
    """
    monkeypatch.setenv("HA_URL", "http://example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "token")
    expected = deploy._expected_version()

    async def fake_status(url, token):
        return _status(f"v{expected}")

    async def fake_sync(url, token, tag=None):
        return None

    async def fake_restart(url, token):
        return None

    async def fake_loaded(url, token):
        return True

    monkeypatch.setattr(deploy, "_status", fake_status)
    monkeypatch.setattr(deploy, "_sync", fake_sync)
    monkeypatch.setattr(deploy, "_restart", fake_restart)
    monkeypatch.setattr(deploy, "_wait_until_loaded", fake_loaded)

    args = deploy.argparse.Namespace(status=False, watch=False, tag=None)
    assert await deploy.run(args) == 0


async def test_a_status_run_changes_nothing(deploy, monkeypatch) -> None:
    """``--status`` must be safe to run against a working install."""

    async def fake_status(url, token):
        return _status(f"v{deploy._expected_version()}")

    async def fake_sync(url, token, tag=None):
        raise AssertionError("--status must not install")

    async def fake_restart(url, token):
        raise AssertionError("--status must not restart")

    monkeypatch.setenv("HA_URL", "http://example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "token")
    monkeypatch.setattr(deploy, "_status", fake_status)
    monkeypatch.setattr(deploy, "_sync", fake_sync)
    monkeypatch.setattr(deploy, "_restart", fake_restart)

    args = deploy.argparse.Namespace(status=True, watch=False, tag=None)
    assert await deploy.run(args) == 0


async def test_a_stale_install_is_reinstalled_and_restarted(
    deploy, monkeypatch
) -> None:
    """A same-version reinstall still needs a restart to load the new files."""
    monkeypatch.setenv("HA_URL", "http://example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "token")
    expected = deploy._expected_version()
    steps: list[str] = []

    async def fake_status(url, token):
        return _status(f"v{expected}")

    async def fake_sync(url, token, tag=None):
        steps.append("sync")

    async def fake_restart(url, token):
        steps.append("restart")

    async def fake_loaded(url, token):
        steps.append("wait")
        return True

    monkeypatch.setattr(deploy, "_status", fake_status)
    monkeypatch.setattr(deploy, "_sync", fake_sync)
    monkeypatch.setattr(deploy, "_restart", fake_restart)
    monkeypatch.setattr(deploy, "_wait_until_loaded", fake_loaded)

    args = deploy.argparse.Namespace(status=False, watch=False, tag=None)
    assert await deploy.run(args) == 0
    assert steps == ["sync", "restart", "wait"]


async def test_no_entries_at_all_is_a_failure(deploy, monkeypatch) -> None:
    """An install with no config entries has not really deployed anything."""
    monkeypatch.setenv("HA_URL", "http://example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "token")

    async def fake_status(url, token):
        return _status(f"v{deploy._expected_version()}", [])

    monkeypatch.setenv("HA_URL", "http://example.invalid:8123")
    monkeypatch.setattr(deploy, "_status", fake_status)

    args = deploy.argparse.Namespace(status=True, watch=False, tag=None)
    assert await deploy.run(args) == 1
