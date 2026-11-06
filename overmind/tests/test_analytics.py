"""Product-analytics (PostHog) for the SDK/CLI — not customer OTLP."""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

import overmind.analytics as analytics

pytestmark = pytest.mark.covers("privacy")


@pytest.fixture(autouse=True)
def _reset_analytics(tmp_path, monkeypatch):
    """Isolate each test from suite-wide opt-out and shared identity state."""
    monkeypatch.delenv("OVERMIND_ANALYTICS_ENABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(analytics, "_ANALYTICS_ID_PATH", tmp_path / "analytics_id")
    monkeypatch.setattr(analytics, "_IDENTITY_CACHE_PATH", tmp_path / "analytics_identity")
    monkeypatch.setattr(analytics, "_identify_attempted", False)
    monkeypatch.setattr(analytics, "_identified_distinct_id", None)
    monkeypatch.setattr(analytics, "_client", None)
    monkeypatch.setattr(analytics, "Posthog", MagicMock())
    yield


@pytest.fixture()
def posthog_client(monkeypatch):
    """Stand-in for the posthog SDK client; captures enqueue in-process."""
    client = MagicMock()
    monkeypatch.setattr(analytics, "_client", client)
    return client


def test_enabled_defaults_on(monkeypatch):
    monkeypatch.delenv("OVERMIND_ANALYTICS_ENABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert analytics.enabled() is True


@pytest.mark.covers("sdk-install")
def test_cli_and_client_work_without_posthog(tmp_path):
    script = """
import sys
from importlib.abc import MetaPathFinder

class WithoutPosthog(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "posthog" or fullname.startswith("posthog."):
            raise ModuleNotFoundError("PostHog is not installed", name=fullname)

sys.meta_path.insert(0, WithoutPosthog())
import requests
requests_made = []
def unexpected_request(*args, **kwargs):
    requests_made.append((args, kwargs))
    raise AssertionError("Analytics must not make requests without PostHog")
requests.Session.request = unexpected_request

from overmind import analytics, Client
assert not analytics.enabled()
assert not analytics.ensure_identified(api_key="test", base_url="https://example.invalid")
analytics.capture("sdk_init")
analytics.track_cli_invocation(command="skills list")
analytics.flush()
Client(api_key="test", base_url="https://example.invalid")
assert not analytics._ANALYTICS_ID_PATH.exists()
assert not analytics._IDENTITY_CACHE_PATH.exists()

from typer.testing import CliRunner
from overmind.__main__ import app
result = CliRunner().invoke(app, ["skills", "list"])
assert result.exit_code == 0, result.output
assert not requests_made
"""
    env = {**os.environ, "HOME": str(tmp_path), "OVERMIND_ANALYTICS_ENABLED": "true"}
    env.pop("CI", None)
    env.pop("DO_NOT_TRACK", None)
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "env,value",
    [
        ("OVERMIND_ANALYTICS_ENABLED", "false"),
        ("OVERMIND_ANALYTICS_ENABLED", "0"),
        ("OVERMIND_ANALYTICS_ENABLED", "off"),
        ("DO_NOT_TRACK", "1"),
        ("CI", "true"),
    ],
)
def test_enabled_opt_out(monkeypatch, env, value):
    monkeypatch.setenv(env, value)
    assert analytics.enabled() is False


def test_capture_noop_when_disabled(monkeypatch, posthog_client):
    monkeypatch.setenv("OVERMIND_ANALYTICS_ENABLED", "false")
    analytics.capture("sdk_init", outcome="success")
    posthog_client.capture.assert_not_called()


def test_capture_sends_allowed_properties_only(monkeypatch, posthog_client):
    monkeypatch.setenv("OVERMIND_ANALYTICS_ENABLED", "true")
    monkeypatch.setattr(analytics, "ensure_identified", lambda **_: False)

    analytics.capture(
        "sdk_init",
        outcome="success",
        providers=["openai"],
        has_capability_id=True,
        surface="library",
        api_key="om_secret_should_never_appear",
        prompt="user secret prompt",
    )

    posthog_client.capture.assert_called_once()
    call = posthog_client.capture.call_args
    assert call.args == ("sdk_init",)
    assert call.kwargs["distinct_id"]
    props = call.kwargs["properties"]
    assert props["outcome"] == "success"
    assert props["sdk_version"]
    assert "api_key" not in props
    assert "prompt" not in props


@pytest.mark.covers("sdk-install")
def test_capture_swallows_client_errors(monkeypatch, posthog_client):
    monkeypatch.setenv("OVERMIND_ANALYTICS_ENABLED", "true")
    monkeypatch.setattr(analytics, "ensure_identified", lambda **_: False)
    posthog_client.capture.side_effect = RuntimeError("network down")
    analytics.capture("sdk_init", outcome="keyless")


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["overmind", "init", "--brief", "private business plan"], "init"),
        (["overmind", "dataset", "upload", "/private/customers.jsonl", "--json"], "dataset upload"),
        (["overmind", "connector", "add", "private-provider"], "connector add"),
        (["overmind", "unknown-private-text"], ""),
    ],
)
def test_cli_tracks_registered_commands_only(monkeypatch, posthog_client, argv, expected):
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.delenv("OVERMIND_API_KEY", raising=False)
    assert analytics.cli_command_from_argv() == expected
    analytics.track_cli_invocation()
    properties = posthog_client.capture.call_args.kwargs["properties"]
    assert properties["command_path"] == expected
    assert not {"command", "flags", "args"} & properties.keys()


def test_ensure_identified_caches_per_key(monkeypatch, tmp_path, posthog_client):
    monkeypatch.setenv("OVERMIND_ANALYTICS_ENABLED", "true")
    monkeypatch.setattr(analytics, "_IDENTITY_CACHE_PATH", tmp_path / "identity")
    monkeypatch.setattr(
        analytics,
        "_resolve_api_credentials",
        lambda api_key=None, base_url=None: ("om_test_key_abcdefghijklmnop", "https://api.test"),
    )
    me_calls = {"n": 0}

    def fake_me(api_key, base_url):
        me_calls["n"] += 1
        return {
            "id": "user-1",
            "clerk_user_id": "user_clerk_1",
            "email": "user@example.com",
            "plan": "free",
            "is_guest": False,
        }

    monkeypatch.setattr(analytics, "_fetch_me", fake_me)

    assert analytics.ensure_identified() is True
    monkeypatch.setattr(analytics, "_identify_attempted", False)
    assert analytics.ensure_identified() is True

    assert me_calls["n"] == 1
    posthog_client.capture.assert_called_once()
    call = posthog_client.capture.call_args
    assert call.args == ("$identify",)
    assert call.kwargs["distinct_id"] == "user_clerk_1"
    assert call.kwargs["properties"]["$anon_distinct_id"]
    assert call.kwargs["properties"]["$set"]["email"] == "user@example.com"
    assert call.kwargs["properties"]["$set"]["plan"] == "free"
