"""Shared fixtures for the Overmind test suite."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest
from opentelemetry import trace as _otel_trace
from opentelemetry.sdk.trace import TracerProvider

import overmind.tracing as _overmind_tracing

# The coverage map is monorepo-wide: the platform's collection guard checks these tests too.
_platform_tests = types.ModuleType("platform_tests")
_platform_tests.__path__ = [str(Path(__file__).resolve().parents[2] / "tests")]
sys.modules["platform_tests"] = _platform_tests

from platform_tests.coverage_guard import (  # noqa: E402
    pytest_collection_modifyitems,  # noqa: F401
    pytest_configure,  # noqa: F401
)

#
# ``overmind.start_span`` / ``overmind.observe`` / ``overmind.set_tag`` all
# go through ``overmind.tracing.get_tracer()`` which raises until
# ``overmind.init()`` is called.  We don't want test runs to actually
# export anything, so we install a no-op ``TracerProvider`` (no exporter)
# and flip the SDK's internal ``_initialized`` flag manually.

_provider = TracerProvider()
_otel_trace.set_tracer_provider(_provider)
_overmind_tracing._tracer = _provider.get_tracer("overmind", "test")
_overmind_tracing._initialized = True
os.environ["OVERMIND_API_KEY"] = "test"
# Product analytics is opt-out; keep the suite from phone-homing.
os.environ["OVERMIND_ANALYTICS_ENABLED"] = "false"


@pytest.fixture(autouse=True)
def _isolated_claude_config(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-config")))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("user-config")))
    # Endpoint tests must declare their connection, not inherit the developer's local API.
    for name in ("OVERMIND_API_URL", "OVERMIND_BASE_URL", "OVERMIND_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
