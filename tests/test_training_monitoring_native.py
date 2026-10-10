import json
from contextlib import nullcontext

from modal_shared.training_monitoring import resolve_policy
from overbae.services.sft_assets.native_monitor import NativeTrainingMonitor


class Rows:
    def __init__(self):
        self.rows = [
            {"key": str(i), "target_probabilities": [0.25, 0.75], "weight": 2} for i in range(6)
        ]
        self.monitoring_rows = self.rows

    def read(self, indices):
        return [self.rows[i] for i in indices]

    def __len__(self):
        return len(self.rows)


def test_native_monitor_preserves_distribution_metrics_and_probability_targets(tmp_path):
    rows, calls = Rows(), []

    def evaluate(selection, destination):
        selected = selection.read(range(len(selection)))
        calls.append(selected)
        assert all(
            row["target_probabilities"] == [0.25, 0.75] and row["weight"] == 2 for row in selected
        )
        destination.write_text(
            "".join(json.dumps({**row, "probabilities": [0.5, 0.5]}) + "\n" for row in selected)
        )
        return {
            "eval_loss": 0.5,
            "brier": 0.125,
            "expected_score_mae": None,
            "distribution_decisions": len(selected),
            "mean_decisions": 0,
        }

    policy = resolve_policy(
        {"loss_sample": 3, "train_sample": 2}, has_development=True, provider="modal"
    )
    monitor = NativeTrainingMonitor(
        tmp_path,
        policy,
        rows,
        rows,
        total_steps=10,
        evaluate=evaluate,
        retain=None,
        preserve=nullcontext,
        attempt=1,
    )
    result = monitor.check(0)
    assert result["metrics"]["brier"] == 0.125
    assert result["metrics"]["expected_score_mae"] is None
    assert result["metrics"]["distribution_decisions"] == 3
    assert len(calls) == 2
    assert monitor.check(0) == result
    assert len(calls) == 2


def test_native_checkpoint_retention_without_development_checks(tmp_path):
    rows = Rows()
    retained = []

    def retain(step):
        retained.append(step)
        return {
            "path": f"checkpoints/{step}",
            "artifact_identity": str(step),
            "reload_verification": {"decisions": 6},
        }

    policy = resolve_policy({"mode": "off"}, has_development=False, provider="modal")
    monitor = NativeTrainingMonitor(
        tmp_path,
        policy,
        rows,
        None,
        total_steps=4,
        evaluate=None,
        retain=retain,
        preserve=nullcontext,
        attempt=1,
    )
    monitor.retain_at_step(2)
    monitor.retain_at_step(2)
    monitor.retain_at_step(4)
    assert retained == [2, 4]
    assert monitor.monitor.data["checks"] == []
    assert monitor.monitor.data["selected_checkpoint"] == "1:4"
