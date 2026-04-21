"""Tests for overclaw.optimize.cassette — record/replay store."""

from __future__ import annotations

import json
from pathlib import Path

from overclaw.optimize.cassette import (
    Cassette,
    CassetteEntry,
    NullCassette,
    make_key,
    open_cassette,
)


class TestMakeKey:
    def test_same_inputs_same_key(self):
        k1 = make_key(
            "llm", "gpt-4o", {"messages": [{"role": "user", "content": "hi"}]}
        )
        k2 = make_key(
            "llm", "gpt-4o", {"messages": [{"role": "user", "content": "hi"}]}
        )
        assert k1 == k2

    def test_different_kind_different_key(self):
        k1 = make_key("llm", "gpt-4o", {})
        k2 = make_key("tool", "gpt-4o", {})
        assert k1 != k2

    def test_different_identifier_different_key(self):
        k1 = make_key("llm", "gpt-4o", {})
        k2 = make_key("llm", "claude", {})
        assert k1 != k2

    def test_dict_ordering_is_canonical(self):
        k1 = make_key("llm", "x", {"b": 2, "a": 1})
        k2 = make_key("llm", "x", {"a": 1, "b": 2})
        assert k1 == k2

    def test_nested_dict_stability(self):
        k1 = make_key("llm", "x", {"m": [{"role": "u", "content": "hi"}]})
        k2 = make_key("llm", "x", {"m": [{"content": "hi", "role": "u"}]})
        assert k1 == k2


class TestCassetteRecordReplay:
    def test_record_then_replay(self, tmp_path: Path):
        cass = Cassette(tmp_path / "cass.jsonl")
        cass.record(
            kind="llm",
            identifier="gpt-4o",
            payload={"messages": [{"role": "user", "content": "hi"}]},
            result={"choices": [{"message": {"content": "hello!"}}]},
        )
        got = cass.replay(
            kind="llm",
            identifier="gpt-4o",
            payload={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert got is not None
        assert got.result["choices"][0]["message"]["content"] == "hello!"

    def test_miss_returns_none(self, tmp_path: Path):
        cass = Cassette(tmp_path / "cass.jsonl")
        assert cass.replay("llm", "x", {"foo": "bar"}) is None

    def test_has_shortcut(self, tmp_path: Path):
        cass = Cassette(tmp_path / "cass.jsonl")
        cass.record(kind="tool", identifier="search", payload={"q": "x"}, result={})
        assert cass.has("tool", "search", {"q": "x"}) is True
        assert cass.has("tool", "search", {"q": "y"}) is False

    def test_persistence_across_instances(self, tmp_path: Path):
        path = tmp_path / "cass.jsonl"
        a = Cassette(path)
        a.record(kind="llm", identifier="m", payload={"k": 1}, result={"v": 2})
        # New instance reads the file on first access.
        b = Cassette(path)
        got = b.replay("llm", "m", {"k": 1})
        assert got is not None
        assert got.result == {"v": 2}

    def test_overwrite_same_key(self, tmp_path: Path):
        cass = Cassette(tmp_path / "cass.jsonl")
        cass.record(kind="llm", identifier="m", payload={"p": 1}, result={"v": 1})
        cass.record(kind="llm", identifier="m", payload={"p": 1}, result={"v": 2})
        got = cass.replay("llm", "m", {"p": 1})
        assert got is not None
        assert got.result == {"v": 2}

    def test_count_by_kind(self, tmp_path: Path):
        cass = Cassette(tmp_path / "cass.jsonl")
        cass.record(kind="llm", identifier="m", payload={"p": 1}, result={})
        cass.record(kind="llm", identifier="m", payload={"p": 2}, result={})
        cass.record(kind="http", identifier="u", payload={"p": 1}, result={})
        by_kind = cass.count_by_kind()
        assert by_kind == {"llm": 2, "http": 1}


class TestCassetteRobustness:
    def test_missing_file_is_empty(self, tmp_path: Path):
        cass = Cassette(tmp_path / "does_not_exist.jsonl")
        assert len(cass) == 0
        assert cass.replay("llm", "m", {}) is None

    def test_malformed_line_is_skipped(self, tmp_path: Path):
        path = tmp_path / "cass.jsonl"
        # One broken line + one good line
        broken = "not-json\n"
        good = (
            json.dumps(
                {
                    "kind": "llm",
                    "identifier": "m",
                    "key": make_key("llm", "m", {"p": 1}),
                    "payload": {"p": 1},
                    "result": {"v": 1},
                    "metadata": {},
                    "version": 1,
                }
            )
            + "\n"
        )
        path.write_text(broken + good, encoding="utf-8")
        cass = Cassette(path)
        assert cass.replay("llm", "m", {"p": 1}) is not None


class TestNullCassette:
    def test_never_replays(self):
        cass = NullCassette()
        cass.record(kind="llm", identifier="m", payload={}, result={"v": 1})
        assert cass.replay("llm", "m", {}) is None
        assert len(cass) == 0

    def test_open_cassette_with_none(self):
        cass = open_cassette(None)
        assert isinstance(cass, NullCassette)


class TestCassetteEntry:
    def test_roundtrip_asdict(self):
        entry = CassetteEntry(
            kind="llm",
            identifier="m",
            key="abc",
            payload={"x": 1},
            result={"y": 2},
        )
        assert entry.version == 1
        # asdict round-trip
        from dataclasses import asdict

        d = asdict(entry)
        back = CassetteEntry(**d)
        assert back == entry
