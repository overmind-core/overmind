import json
from unittest.mock import MagicMock, patch

from modal_shared.training_telemetry import read_telemetry, record_stage
from overbae.services.finetuning_runner import ModalRunner, progress_from_snapshot


def test_stages_and_recovery_cross_provider_and_progress_boundaries(tmp_path):
    record_stage(tmp_path, "initial_validation", completed=12, total=30, unit="decisions")
    first = read_telemetry(tmp_path)
    assert first["stage"] == "initial_validation" and first["completed"] == 12
    record_stage(tmp_path, "initial_validation", completed=30, total=30, unit="decisions")
    record_stage(tmp_path, "training", completed=4, total=100, unit="steps", restored_step=4)
    record_stage(tmp_path, "training", completed=5, total=100, unit="steps", checkpoint_step=5)
    telemetry = read_telemetry(tmp_path)
    assert telemetry["restored_step"] == 4 and telemetry["checkpoint_step"] == 5
    assert telemetry["heartbeat_at"] >= first["heartbeat_at"]
    snap = {
        "meta": {"status": "running"},
        "telemetry": telemetry,
        "metrics": [
            {
                "event": "BT_EVAL",
                "step": 0,
                "eval_loss": 1.2,
                "argmax_target_agreement": 0.6,
                "brier": 0.4,
                "decisions": 30,
            },
            {"event": "BT_PROGRESS", "step": 5, "total_steps": 100, "loss": 0.7, "num_tokens": 50},
        ],
    }
    function = MagicMock()
    function.remote.return_value = snap
    call = MagicMock()
    call.get.side_effect = TimeoutError()
    with (
        patch("modal.Function.from_name", return_value=function),
        patch("modal.FunctionCall.from_id", return_value=call),
    ):
        poll = ModalRunner(release={"app": "fixture-release", "environment": "test"}).poll(
            "run:call"
        )
    progress = progress_from_snapshot(poll)
    assert progress["stage"] == "training"
    assert progress["diagnostics"]["restored_step"] == 4
    assert progress["eval_history"][0]["brier"] == 0.4
    assert progress["eval_history"][0]["decisions"] == 30
    assert progress["eval_history"][0]["argmax_target_agreement"] == 0.6
    assert "target_probabilities" not in json.dumps(progress)


def test_silent_stage_heartbeat_keeps_work_counter_and_attempt_clocks(tmp_path):
    from modal_shared.training_telemetry import record_heartbeat

    record_stage(tmp_path, "loading_weights", completed=12, total=100, unit="bytes")
    first = record_heartbeat(tmp_path, new_attempt=True)
    record_heartbeat(tmp_path)
    second = read_telemetry(tmp_path)
    assert second["completed"] == 12 and second["stage"] == "loading_weights"
    assert second["heartbeat_at"] >= first["heartbeat_at"]
    resumed = record_heartbeat(tmp_path, new_attempt=True)
    assert resumed["attempt"] == 2
    assert resumed["overall_started_at"] == first["overall_started_at"]
