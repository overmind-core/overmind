"""Product-analytics (PostHog) for the SDK/CLI — not customer OTLP."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import overmind.analytics as analytics


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


def test_capture_swallows_client_errors(monkeypatch, posthog_client):
    monkeypatch.setenv("OVERMIND_ANALYTICS_ENABLED", "true")
    monkeypatch.setattr(analytics, "ensure_identified", lambda **_: False)
    posthog_client.capture.side_effect = RuntimeError("network down")
    analytics.capture("sdk_init", outcome="keyless")


def test_distinct_id_persists(tmp_path, monkeypatch):
    path = tmp_path / "analytics_id"
    monkeypatch.setattr(analytics, "_ANALYTICS_ID_PATH", path)
    first = analytics._distinct_id()
    second = analytics._distinct_id()
    assert first == second
    assert path.read_text(encoding="utf-8").strip() == first


@pytest.mark.parametrize(
    "argv,command",
    [
        (["overmind", "sync"], "sync"),
        (["overmind", "dataset", "upload", "data.jsonl"], "dataset upload"),
        (["overmind", "optimise", "start", "--json"], "optimise start"),
        (["overmind", "--version"], ""),
    ],
)
def test_cli_command_from_argv(argv, command):
    assert analytics.cli_command_from_argv(argv) == command


def test_redact_argv_strips_secrets():
    line = analytics._redact_argv([
        "overmind",
        "sync",
        "--api-key",
        "om_super_secret_value_123456",
        "--project-id",
        "proj_abc",
    ])
    assert line == "overmind sync --api-key [redacted] --project-id proj_abc"
    assert "om_super_secret" not in line


@pytest.mark.parametrize(
    "argv,flags,args",
    [
        (
            ["overmind", "init", "--ide", "cursor", "--env", "production"],
            {"ide": "cursor", "env": "production"},
            [],
        ),
        (
            ["overmind", "dataset", "upload", "data.jsonl", "--json"],
            {"json": True},
            ["data.jsonl"],
        ),
        (
            ["overmind", "sync", "--api-key", "om_super_secret_value_123456"],
            {"api-key": "[redacted]"},
            [],
        ),
        (
            ["overmind", "init", "--ide=cursor"],
            {"ide": "cursor"},
            [],
        ),
    ],
)
def test_cli_flags_and_args(argv, flags, args):
    assert analytics.cli_flags_and_args(argv) == (flags, args)


def test_track_cli_invocation_emits_full_command(monkeypatch, posthog_client):
    monkeypatch.setenv("OVERMIND_ANALYTICS_ENABLED", "true")
    monkeypatch.setattr(analytics, "ensure_identified", lambda **_: False)
    monkeypatch.setattr(
        analytics,
        "_redact_argv",
        lambda argv=None: "overmind skills list --json",
    )
    monkeypatch.setattr(
        analytics,
        "cli_flags_and_args",
        lambda argv=None: ({"json": True}, []),
    )

    analytics.track_cli_invocation(command="skills list", exit_code=0, duration_ms=42)

    call = posthog_client.capture.call_args
    assert call.args == ("cli.invoked",)
    props = call.kwargs["properties"]
    # ``command`` is the full line people look for in PostHog.
    assert props["command"] == "overmind skills list --json"
    assert props["command_path"] == "skills list"
    assert props["flags"] == {"json": True}
    assert props["args"] == []
    assert props["exit_code"] == 0
    assert props["duration_ms"] == 42
    assert props["success"] is True
    assert props["surface"] == "cli"
    # The CLI process is short-lived: the event must be flushed, not queued.
    posthog_client.flush.assert_called_once()


def test_track_cli_invocation_records_failure(monkeypatch, posthog_client):
    monkeypatch.setenv("OVERMIND_ANALYTICS_ENABLED", "true")
    monkeypatch.setattr(analytics, "ensure_identified", lambda **_: False)
    monkeypatch.setattr(analytics, "_redact_argv", lambda argv=None: "overmind boom")

    analytics.track_cli_invocation(command="boom", exit_code=1, duration_ms=3)

    props = posthog_client.capture.call_args.kwargs["properties"]
    assert props["command"] == "overmind boom"
    assert props["command_path"] == "boom"
    assert props["exit_code"] == 1
    assert props["success"] is False


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
        return {"id": "user-1", "clerk_user_id": "user_clerk_1", "plan": "free", "is_guest": False}

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
    assert call.kwargs["properties"]["$set"]["plan"] == "free"
