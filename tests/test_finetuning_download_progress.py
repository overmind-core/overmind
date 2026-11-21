"""Wire contract: train.py emits BT_STAGE / BT_DOWNLOAD lines during the
otherwise-silent HF weight download; the runner parses them into PollSnapshot.
"""

from __future__ import annotations

import pytest

from overbae.services.finetuning_runner import (
    PollSnapshot,
    filter_activity_logs,
    progress_from_snapshot,
)

pytestmark = pytest.mark.covers("measured-facts")


def _logs(*messages: str) -> list[dict]:
    return [{"message": m, "timestamp": str(1_000_000 + i)} for i, m in enumerate(messages)]


def test_filter_activity_logs_keeps_stage_lines_only():
    logs = _logs(
        'BT_STAGE {"stage": "downloading_base_model", "model": "Qwen"}',
        'BT_DOWNLOAD {"pct": 10, "downloaded_gb": 2.8, "total_gb": 28.0}',
        'BT_DOWNLOAD {"pct": 90, "downloaded_gb": 25.2, "total_gb": 28.0}',
        'BT_STAGE {"stage": "loading_model"}',
        'BT_STAGE {"stage": "model_loaded"}',
    )
    msgs = [e["message"] for e in filter_activity_logs(logs)]
    assert msgs == [
        "Downloading base model (Qwen)…",
        "Loading base model onto GPU…",
        "Base model loaded — starting training",
    ]


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
