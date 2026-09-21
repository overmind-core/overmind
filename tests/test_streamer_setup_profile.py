import sys
from unittest.mock import Mock

import pytest

from experiments.inference_upgrade import streamer_setup_profile as profile


def test_profile_preserves_request_and_reports_callable():
    calls = []

    class Streamer:
        def stream_files(self, files, **kwargs):
            calls.append((files, kwargs))

    files = ["/artifacts/part-0000.safetensors"]
    result = profile.profile_setup(Streamer(), files)
    assert calls == [(files, {"device": "cpu", "is_distributed": False})]
    assert result["wall_s"] >= 0
    assert any(row["function"] == "stream_files" for row in result["functions"])
    assert all(row["cumulative_s"] >= row["self_s"] for row in result["functions"])
    assert result["process_counters"]["user_cpu_s"] >= 0


def test_profile_propagates_failure_and_disables_profiler():
    assert sys.getprofile() is None
    streamer = Mock()
    streamer.stream_files.side_effect = RuntimeError("header read failed")
    with pytest.raises(RuntimeError, match="header read failed"):
        profile.profile_setup(streamer, [])
    assert sys.getprofile() is None


def test_counters_allow_unavailable_proc_io(monkeypatch):
    monkeypatch.setattr(profile.Path, "read_text", Mock(side_effect=PermissionError))
    assert "read_bytes" not in profile.process_counters()
    assert profile.counter_delta({"user_cpu_s": 1, "read_bytes": 100}, {"user_cpu_s": 3}) == {
        "user_cpu_s": 2
    }


def test_counters_report_only_relevant_io_fields(monkeypatch):
    monkeypatch.setattr(
        profile.Path,
        "read_text",
        Mock(return_value="rchar: 100\nsyscr: 2\nread_bytes: 64\nwchar: 5"),
    )
    result = profile.process_counters()
    assert (result["rchar"], result["syscr"], result["read_bytes"]) == (100, 2, 64)
    assert "wchar" not in result
