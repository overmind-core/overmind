import json
import math

import pytest

from modal_shared.training_monitoring import (
    MonitoringSchedule,
    classification_metrics,
    freeze_probe,
    generation_tokens,
    paired_generation_metrics,
    resolve_policy,
)
from modal_shared.training_monitoring_runtime import Monitor
from overbae.services.training_policies import profile_options


@pytest.mark.parametrize(
    "policy",
    [
        {"mode": "guess"},
        {"loss_sample": 0},
        {"loss_sample": True},
        {"overhead_fraction": float("nan")},
        {"overhead_fraction": 1},
        {"mode": "steps"},
        {"mode": "off", "selection": "development_loss"},
        {"generation": {"kind": "classification", "labels": ["yes", "yes"]}},
        {"early_stopping": {"patience": 0}},
        {"unrecognised": "must not disappear"},
    ],
)
def test_monitoring_rejects_ambiguous_or_impossible_contract(policy):
    with pytest.raises(ValueError):
        resolve_policy(policy, has_development=True, provider="modal")


def test_monitoring_requires_real_provider_and_development_support():
    with pytest.raises(ValueError, match="development"):
        resolve_policy({"mode": "adaptive"}, has_development=False, provider="modal")
    with pytest.raises(ValueError, match="provider"):
        resolve_policy({"mode": "adaptive"}, has_development=True, provider="together_ai")
    assert resolve_policy(None, has_development=False, provider="modal")["mode"] == "off"


def test_training_time_limit_bounds_full_run_without_changing_optimizer_steps():
    assert profile_options({"runtime_limit_seconds": 7200}) == {"timeout": 7200, "retries": 0}
    for value in (0, -1, True, 1.5, 86401, "7200"):
        with pytest.raises(ValueError):
            profile_options({"runtime_limit_seconds": value})
    with pytest.raises(ValueError, match="one"):
        profile_options(
            {
                "runtime_limit_seconds": 7200,
                "runtime_profile": {"max_steps": 4, "max_seconds": 60},
                "max_steps": 4,
            }
        )


def test_frozen_probe_keeps_duplicate_visits_and_groups_without_target_leakage():
    rows = [
        {"key": "a", "group": "one", "input_ids": [1], "labels": [1]},
        {"key": "a", "group": "one", "input_ids": [1], "labels": [1]},
        {"key": "b", "group": "one", "input_ids": [2], "labels": [2]},
        {"key": "c", "group": "two", "input_ids": [3], "labels": [3]},
    ]
    probe = freeze_probe(rows, target=2, seed=42)
    assert probe == freeze_probe(rows, target=2, seed=42)
    indices = probe["indices"]
    for group in {rows[i]["group"] for i in indices}:
        assert {i for i, row in enumerate(rows) if row["group"] == group} <= set(indices)
    assert probe["actual_rows"] == len(indices)
    assert probe["requested_rows"] == 2
    assert "input_ids" not in json.dumps(probe)
    assert freeze_probe(rows[:3], target=99, seed=42)["indices"] == [0, 1, 2]
    changed = [*rows[:3], {**rows[3], "labels": [9]}]
    assert (
        freeze_probe(changed, target=99, seed=42)["fingerprint"]
        != freeze_probe(rows, target=99, seed=42)["fingerprint"]
    )


def test_pre_dispatch_probe_is_reused_and_changed_tokens_are_rejected(tmp_path):
    from modal_shared.training_monitoring_runtime import freeze_run_files

    rows = [{"key": str(i), "input_ids": [1, i], "labels": [-100, i]} for i in range(8)]
    for name in ("data", "val"):
        (tmp_path / f"{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    policy = resolve_policy({"loss_sample": 3}, has_development=True, provider="modal")
    frozen = freeze_run_files(tmp_path, policy)
    monitor = Monitor(tmp_path, policy, rows, rows, total_steps=10, attempt=1)
    assert monitor.data["identity"] == frozen["identity"]
    changed = [{**row, "input_ids": [2, index]} for index, row in enumerate(rows)]
    with pytest.raises(ValueError, match="frozen"):
        Monitor(tmp_path, policy, changed, changed, total_steps=10, attempt=1)


def test_adaptive_schedule_records_conflicts_without_changing_the_probe():
    policy = resolve_policy(None, has_development=True, provider="modal")
    schedule = MonitoringSchedule(policy, total_steps=133)
    assert schedule.next_step == 14
    for _ in range(14):
        schedule.observe_step(30)
    decision = schedule.completed(step=14, duration=120)
    assert decision["overhead_interval_steps"] == 36
    assert schedule.next_step == 50
    assert decision["basis"] == "measured_optimizer_time"
    restored = MonitoringSchedule(policy, total_steps=133, state=schedule.state())
    assert restored.state() == schedule.state()
    assert not restored.due(49) and restored.due(50)
    for bad in (0, -1, math.inf, math.nan):
        with pytest.raises(ValueError):
            restored.observe_step(bad)


def test_schedule_cannot_queue_unbounded_checks_or_skip_short_run_final():
    policy = resolve_policy({"max_checks": 2}, has_development=True, provider="modal")
    schedule = MonitoringSchedule(policy, total_steps=4)
    schedule.completed(step=1, duration=0.01)
    schedule.completed(step=2, duration=0.01)
    assert schedule.next_step is None
    assert not schedule.due(4)
    assert schedule.final_required(4)
    off = MonitoringSchedule({**policy, "mode": "off"}, total_steps=4)
    assert not off.due(1) and not off.final_required(4)


@pytest.mark.parametrize("mode", ["adaptive", "steps", "epoch"])
def test_schedule_finishes_normally_without_a_spurious_budget_conflict(mode):
    options = {"mode": mode, "target_seconds": 2}
    if mode == "steps":
        options["interval_steps"] = 2
    policy = resolve_policy(options, has_development=True, provider="modal")
    schedule = MonitoringSchedule(policy, total_steps=4)
    schedule.observe_step(1)
    decision = schedule.completed(step=2, duration=0)
    assert decision["conflicts"] == []
    assert decision["next_step"] is None
    assert decision["stop_reason"] == ("epoch_boundary" if mode == "epoch" else "run_boundary")
    assert schedule.final_required(4)


def test_adaptive_schedule_keeps_noise_stable_and_restores_its_adaptation_state():
    policy = resolve_policy({"target_seconds": 10}, has_development=True, provider="modal")
    schedule = MonitoringSchedule(policy, total_steps=1000)
    schedule.observe_step(1)
    first = schedule.completed(step=100, duration=0)
    assert first["next_step"] == 110
    for _ in range(30):
        schedule.observe_step(0.95)
    noisy = schedule.completed(step=110, duration=0)
    assert noisy["next_step"] == 120
    assert noisy["adaptation"] == "within_hysteresis"
    restored = MonitoringSchedule(policy, total_steps=1000, state=schedule.state())
    for active in (schedule, restored):
        for _ in range(30):
            active.observe_step(0.2)
    slow_check = schedule.completed(step=120, duration=2)
    assert slow_check == restored.completed(step=120, duration=2)
    assert slow_check["candidate_interval_steps"] == 90
    assert slow_check["effective_interval_steps"] == 20
    assert slow_check["next_step"] == 140
    assert slow_check["adaptation"] == "bounded_change"
    assert slow_check["conflicts"] == [
        "monitoring_budget_conflict: bounded cadence cannot yet meet overhead target"
    ]


def test_adaptive_budget_conflict_distinguishes_overhead_from_normal_end():
    policy = resolve_policy({"target_seconds": 2}, has_development=True, provider="modal")
    schedule = MonitoringSchedule(policy, total_steps=10)
    schedule.observe_step(1)
    decision = schedule.completed(step=2, duration=2)
    assert decision["next_step"] is None
    assert decision["stop_reason"] == "run_boundary"
    assert decision["conflicts"] == [
        "monitoring_budget_conflict: overhead target prevents another periodic check"
    ]
    explicit = MonitoringSchedule({**policy, "mode": "steps", "interval_steps": 20}, 10)
    explicit.observe_step(1)
    assert explicit.completed(step=2, duration=200)["conflicts"] == []


def test_classification_keeps_invalid_and_failed_examples_in_coverage():
    result = classification_metrics(
        [
            {"reference": "yes", "prediction": "yes", "status": "completed"},
            {"reference": "no", "prediction": "yes", "status": "completed"},
            {"reference": "no", "prediction": "maybe", "status": "completed"},
            {"reference": "yes", "prediction": None, "status": "failed"},
        ],
        ["yes", "no"],
    )
    assert result["expected"] == 4
    assert result["technical_errors"] == 1
    assert result["invalid_labels"] == 1
    assert result["scored"] == 3
    assert result["accuracy"] == pytest.approx(1 / 3)
    assert result["coverage"] == 0.75
    assert result["confusion_matrix"] == [[1, 0], [1, 0]]
    assert result["per_class"]["no"]["recall"] == 0
    assert result["per_class"]["no"]["support"] == 2


def test_failed_generation_does_not_erase_declared_sample_label_coverage():
    result = classification_metrics(
        [{"reference": "yes", "prediction": None, "status": "failed"}],
        ["yes", "no"],
    )
    assert result["unrepresented_labels"] == ["no"]
    assert result["reference_distribution"] == {"yes": 1}
    assert result["accuracy"] is None
    assert result["coverage"] == 0
    assert all(metrics["precision"] is None for metrics in result["per_class"].values())


def test_precision_without_predictions_is_unknown_while_recall_records_missed_labels():
    result = classification_metrics(
        [{"reference": "yes", "prediction": "no", "status": "completed"}],
        ["yes", "no"],
    )
    assert result["per_class"]["yes"] == {
        "precision": None,
        "recall": 0,
        "f1": 0,
        "support": 1,
    }
    assert result["per_class"]["no"]["precision"] == 0
    assert result["per_class"]["no"]["recall"] is None


def test_paired_classification_excludes_references_outside_the_frozen_label_contract():
    before = [
        {"row": 0, "reference": "yes", "prediction": "no", "status": "completed"},
        {"row": 1, "reference": "unknown", "prediction": "yes", "status": "completed"},
    ]
    after = [
        {**before[0], "prediction": "yes"},
        {**before[1], "prediction": "unknown"},
    ]
    result = paired_generation_metrics(before, after, seed=42, labels=["yes", "no"])
    assert result["paired"] == 1
    assert result["unpaired"] == 1
    assert result["improved"] == 1
    assert result["interval_95"] is None


def test_worker_journey_persists_failed_checks_and_recovers_without_reexecution(tmp_path):
    rows = [{"key": str(i), "input_ids": [i], "labels": [i]} for i in range(8)]
    policy = resolve_policy({"train_sample": 0}, has_development=True, provider="modal")
    monitor = Monitor(tmp_path, policy, rows, rows, total_steps=20, attempt=1)
    calls = []

    def evaluate(indices, split):
        calls.append((indices, split))
        return {"eval_loss": 1.0, "eval_token_accuracy": 0.5}

    monitor.check(0, evaluate=evaluate)
    recovered = Monitor(tmp_path, policy, rows, rows, total_steps=20, attempt=1)
    recovered.check(0, evaluate=evaluate)
    assert len(calls) == 1
    assert recovered.summary()["checks"][0]["state"] == "completed"

    def broken(indices, split):
        raise RuntimeError("injected validation failure")

    recovered.check(2, evaluate=broken)
    checks = recovered.summary()["checks"]
    assert checks[1]["state"] == "failed"
    assert "injected validation failure" in checks[1]["error"]["message"]
    assert checks[0]["state"] == "completed"
    with pytest.raises(ValueError, match="frozen"):
        Monitor(tmp_path, {**policy, "seed": 99}, rows, rows, total_steps=20, attempt=1)


def test_required_check_failure_stops_and_nonfinite_cannot_select_checkpoint(tmp_path):
    rows = [{"key": "a", "input_ids": [1], "labels": [1]}]
    policy = resolve_policy(
        {"selection": "development_loss"}, has_development=True, provider="modal"
    )
    monitor = Monitor(tmp_path, policy, rows, rows, total_steps=10, attempt=1)
    with pytest.raises(ValueError, match="finite"):
        monitor.check(0, evaluate=lambda *_: {"eval_loss": float("nan")})
    assert monitor.summary()["checks"][0]["state"] == "failed"
    assert monitor.summary()["selected_checkpoint"] is None


def test_generation_never_receives_the_final_supervised_answer():
    prompt, answer = generation_tokens(
        {"input_ids": [1, 2, 3, 4, 5, 6], "labels": [-100, 2, -100, -100, 5, 6]}
    )
    assert prompt == [1, 2, 3, 4]
    assert answer == [5, 6]
    with pytest.raises(ValueError, match="supervised"):
        generation_tokens({"input_ids": [1, 2], "labels": [-100, -100]})
    with pytest.raises(ValueError, match="input"):
        generation_tokens({"input_ids": [1, 2], "labels": [1, 2]})


def test_paired_generation_keeps_duplicate_observations_and_failed_coverage():
    before = [
        {
            "row": i,
            "key": str(i // 2),
            "reference": "yes",
            "prediction": "no",
            "status": "completed",
        }
        for i in range(6)
    ]
    after = [{**row, "prediction": "yes"} for row in before]
    after[-1] = {**after[-1], "status": "failed"}
    report = paired_generation_metrics(before, after, seed=42)
    assert report["paired"] == 5
    assert report["unpaired"] == 1
    assert report["groups"] == 3
    assert report["improved"] == 5
    assert report["regressed"] == 0
    assert report["delta"] == 1
    assert report["interval_95"] == [1, 1]
    assert paired_generation_metrics(before[:2], after[:2], seed=42)["interval_95"] is None


def test_monitoring_failure_does_not_hide_completed_loss_and_invalid_reference(tmp_path):
    rows = [{"key": "a", "input_ids": [1], "labels": [1]}]
    policy = resolve_policy(None, has_development=True, provider="modal")
    monitor = Monitor(tmp_path, policy, rows, rows, total_steps=10, attempt=1)
    result = monitor.check(
        0, evaluate=lambda _, split: {"eval_loss": 0.5 if split == "development" else float("nan")}
    )
    assert result["state"] == "failed"
    assert result["metrics"]["eval_loss"] == 0.5
    assert result["facts"]["findings"][0]["code"] == "monitoring_check_failed"
    assert monitor.path.exists()


def test_completed_measurements_are_durable_before_slow_generation_or_checkpointing(tmp_path):
    rows = [{"key": str(i), "input_ids": [1, 2], "labels": [-100, 2]} for i in range(4)]
    policy = resolve_policy(
        {"generation": {"kind": "classification", "labels": ["yes", "no"]}, "generation_every": 1},
        has_development=True,
        provider="modal",
    )
    monitor = Monitor(tmp_path, policy, rows, rows, total_steps=10, attempt=1)

    def persisted():
        record = json.loads(monitor.path.read_text())["checks"][-1]
        assert record["state"] == "running"
        assert record["observed_at"] >= record["started_at"]
        return record

    def evaluate(indices, split):
        if split == "training_reference":
            record = persisted()
            assert record["metrics"]["eval_loss"] == 0.5
            assert record["coverage"] == {"expected": 4, "scored": 4}
        return {"eval_loss": 0.5 if split == "development" else 0.4}

    def generate(indices):
        assert persisted()["metrics"]["training_reference_loss"] == 0.4
        return [
            {"row": i, "reference": "yes", "prediction": "yes", "status": "completed"}
            for i in indices
        ]

    def checkpoint(step, metrics):
        assert persisted()["metrics"]["generation"]["accuracy"] == 1
        raise RuntimeError("checkpoint write interrupted")

    result = monitor.check(1, evaluate=evaluate, generate=generate, save_checkpoint=checkpoint)
    assert result["state"] == "failed"
    assert result["metrics"]["generation"]["accuracy"] == 1
    assert "checkpoint write interrupted" in result["error"]["message"]
