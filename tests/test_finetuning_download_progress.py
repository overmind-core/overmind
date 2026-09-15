"""Wire contract: train.py emits BT_STAGE / BT_DOWNLOAD lines during the
otherwise-silent HF weight download; the runner parses them into PollSnapshot.
"""

from __future__ import annotations

from overbae.services.finetuning_runner import (
    PollSnapshot,
    _activity_message,
    filter_activity_logs,
    parse_download_stage,
    progress_from_snapshot,
)


def _logs(*messages: str) -> list[dict]:
    return [{"message": m, "timestamp": str(1_000_000 + i)} for i, m in enumerate(messages)]


def test_parse_download_stage_reads_latest_stage_and_download():
    logs = _logs(
        'BT_STAGE {"stage": "downloading_base_model", "model": "Qwen/Qwen2.5-14B-Instruct"}',
        'BT_DOWNLOAD {"pct": 12, "downloaded_gb": 3.4, "total_gb": 28.0}',
        'BT_DOWNLOAD {"pct": 42, "downloaded_gb": 11.8, "total_gb": 28.0}',
        'BT_STAGE {"stage": "loading_model"}',
    )
    stage, download = parse_download_stage(logs)
    assert stage == "loading_model"
    assert download == {"pct": 42, "downloaded_gb": 11.8, "total_gb": 28.0}


def test_parse_download_stage_backward_compatible():
    # Old jobs and Together emit neither line.
    logs = _logs("+ python -u train.py", 'BT_PROGRESS {"step": 1, "total_steps": 30, "loss": 2.5}')
    assert parse_download_stage(logs) == ("", None)
    assert parse_download_stage([]) == ("", None)


def test_activity_message_renders_stage_and_drops_download_spam():
    assert (
        _activity_message('BT_STAGE {"stage": "downloading_base_model", "model": "Qwen/Q"}')
        == "Downloading base model (Qwen/Q)…"
    )
    assert _activity_message('BT_STAGE {"stage": "model_loaded"}') == (
        "Base model loaded — starting training"
    )
    assert (
        _activity_message('BT_STAGE {"stage": "loading_model"}') == "Loading base model onto GPU…"
    )
    assert (
        _activity_message('BT_DOWNLOAD {"pct": 42, "downloaded_gb": 11.8, "total_gb": 28.0}')
        is None
    )


def test_filter_activity_logs_keeps_stage_lines_only():
    logs = _logs(
        'BT_STAGE {"stage": "downloading_base_model", "model": "Qwen"}',
        'BT_DOWNLOAD {"pct": 10, "downloaded_gb": 2.8, "total_gb": 28.0}',
        'BT_DOWNLOAD {"pct": 90, "downloaded_gb": 25.2, "total_gb": 28.0}',
        'BT_STAGE {"stage": "model_loaded"}',
    )
    msgs = [e["message"] for e in filter_activity_logs(logs)]
    assert msgs == ["Downloading base model (Qwen)…", "Base model loaded — starting training"]


def test_progress_from_snapshot_threads_stage_and_download():
    snap = PollSnapshot(
        state="running",
        phase="training",
        stage="downloading_base_model",
        download={"pct": 42, "downloaded_gb": 11.8, "total_gb": 28.0},
    )
    progress = progress_from_snapshot(snap)
    assert progress["stage"] == "downloading_base_model"
    assert progress["download"] == {"pct": 42, "downloaded_gb": 11.8, "total_gb": 28.0}

    plain = progress_from_snapshot(PollSnapshot(state="running", phase="training"))
    assert plain["stage"] == ""
    assert plain["download"] is None
