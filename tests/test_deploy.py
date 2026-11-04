"""Tests for the HACS deploy script.

The script's job is to notice when Home Assistant and HACS disagree, so most of
these tests are about the *checks* rather than the happy path: a deploy that
reports success while installing nothing is the failure mode worth guarding.

Nothing here talks to a real server. The websocket is faked with a small
responder that mimics the shapes HACS and the core return.
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


async def test_hacs_call_retries_while_hacs_is_still_loading(deploy) -> None:
    """``unknown_command`` right after a restart is a race, not a failure.

    Home Assistant answers ``auth_ok`` before the HACS custom integration has
    registered its commands, so the first call after a restart legitimately
    fails. The script must wait rather than declare the repo missing.
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
    ok, result, _ = await deploy._hacs_call(ws, 1, "hacs/repository/info", delay=0)
    assert ok is True
    assert result == {"ok": True}
    assert calls["n"] == 2


async def test_hacs_call_gives_up_after_the_retry_budget(deploy) -> None:
    """A genuinely missing HACS must fail rather than hang forever."""
    ws = FakeWS({})
    ws.send_json = lambda payload: _unknown(ws, payload)  # type: ignore[assignment]
    ok, result, error = await deploy._hacs_call(
        ws, 1, "hacs/repository/info", retries=2, delay=0
    )
    assert ok is False
    assert result is None
    assert error["code"] == "unknown_command"


async def _unknown(ws, payload):
    ws._pending.append(
        {
            "id": payload["id"],
            "success": False,
            "error": {"code": "unknown_command", "message": "Unknown command."},
        }
    )


async def test_a_version_mismatch_fails_the_deploy(deploy, monkeypatch) -> None:
    """HACS claiming a different version than the checkout must stop the run.

    This is the trap the script exists to catch: HACS can report a release as
    installed while the files under ``custom_components`` are something else.
    Trusting HACS's own record would report a false success.
    """
    monkeypatch.setenv("HA_URL", "http://example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "token")

    async def fake_status(url, token):
        return {
            "installed": "v0.0.9",
            "available": "v0.0.9",
            "pending": False,
            "entries": [("Soffit West", "loaded")],
        }

    async def fake_sync(url, token):
        return None

    async def fake_restart(url, token):
        raise AssertionError("must not restart when the version does not match")

    monkeypatch.setattr(deploy, "_status", fake_status)
    monkeypatch.setattr(deploy, "_sync", fake_sync)
    monkeypatch.setattr(deploy, "_restart", fake_restart)

    args = deploy.argparse.Namespace(status=False, watch=False)
    code = await deploy.run(args)
    assert code == 1


async def test_an_unloaded_entry_fails_the_deploy(deploy, monkeypatch) -> None:
    """A stale module leaves entities unavailable; that must not pass."""
    monkeypatch.setenv("HA_URL", "http://example.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", "token")
    expected = deploy._expected_version()

    async def fake_status(url, token):
        return {
            "installed": f"v{expected}",
            "available": f"v{expected}",
            "pending": False,
            "entries": [("Soffit West", "setup_error")],
        }

    monkeypatch.setattr(deploy, "_status", fake_status)
    args = deploy.argparse.Namespace(status=True, watch=False)
    code = await deploy.run(args)
    assert code == 1
