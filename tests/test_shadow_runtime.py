"""Tests for overmind.optimize.shadow_runtime — bootstrap generation + sidecar IO."""

from __future__ import annotations

import json
from pathlib import Path

from overmind.optimize.shadow_runtime import (
    ShadowConfig,
    bootstrap_source,
    read_provenance_file,
)


class TestShadowConfig:
    def test_env_disabled(self):
        cfg = ShadowConfig(enabled=False)
        env = cfg.env()
        assert "OVERMIND_SHADOW_MODE" not in env

    def test_env_enabled_full(self):
        cfg = ShadowConfig(
            enabled=True,
            cassette_path="/tmp/c.jsonl",
            provenance_path="/tmp/p.jsonl",
        )
        env = cfg.env()
        assert env["OVERMIND_SHADOW_MODE"] == "1"
        assert env["OVERMIND_CASSETTE_FILE"] == "/tmp/c.jsonl"
        assert env["OVERMIND_PROVENANCE_FILE"] == "/tmp/p.jsonl"
        assert env["OVERMIND_SIMULATE_BROWSER"] == "1"
        assert env["OVERMIND_SIMULATE_NETWORK"] == "1"

    def test_env_can_disable_browser_network(self):
        cfg = ShadowConfig(enabled=True, simulate_browser=False, simulate_network=False)
        env = cfg.env()
        assert "OVERMIND_SIMULATE_BROWSER" not in env
        assert "OVERMIND_SIMULATE_NETWORK" not in env


class TestBootstrapSource:
    def test_disabled_gets_argv_guard_only(self):
        src = bootstrap_source(None)
        assert "sys.argv" in src
        # Minimal guard should NOT contain the LLM intercept.
        assert "litellm" not in src
        # Sanity: it's valid Python.
        compile(src, "<guard>", "exec")

    def test_enabled_gets_full_bootstrap(self):
        cfg = ShadowConfig(enabled=True)
        src = bootstrap_source(cfg)
        assert "OVERMIND_SHADOW_MODE" in src
        assert "litellm" in src
        # Valid Python.
        compile(src, "<shadow>", "exec")

    def test_bootstrap_can_execute_without_env(self):
        """Running the bootstrap when no env vars are set must not crash."""
        cfg = ShadowConfig(enabled=True)
        src = bootstrap_source(cfg)
        # Execute in a fresh namespace — should complete silently because
        # the env vars that drive interception aren't set.
        namespace: dict = {}
        exec(compile(src, "<shadow>", "exec"), namespace, namespace)


class TestReadProvenanceFile:
    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert read_provenance_file(tmp_path / "nope.jsonl") == []

    def test_reads_valid_lines(self, tmp_path: Path):
        path = tmp_path / "prov.jsonl"
        path.write_text(
            json.dumps({"name": "llm:gpt-4o", "source": "llm_real", "reason": "r"})
            + "\n"
            + json.dumps(
                {"name": "browser_use.Agent", "source": "simulated", "reason": "x"}
            )
            + "\n",
            encoding="utf-8",
        )
        entries = read_provenance_file(path)
        assert len(entries) == 2
        assert entries[0]["source"] == "llm_real"
        assert entries[1]["source"] == "simulated"

    def test_skips_malformed_lines(self, tmp_path: Path):
        path = tmp_path / "prov.jsonl"
        path.write_text(
            "garbage\n" + json.dumps({"source": "llm_real"}) + "\n",
            encoding="utf-8",
        )
        entries = read_provenance_file(path)
        assert len(entries) == 1
