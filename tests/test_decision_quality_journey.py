import json

import pytest

from modal_shared.training_monitoring import fingerprint
from overbae.models import Dataset, FinetuningJob, Project
from overbae.services import training_monitoring


@pytest.mark.django_db
def test_native_quality_survives_receipts_replay_and_later_loss_check():
    project = Project.objects.create(name="Decision observations", slug="decision-observations")
    policy = {"mode": "steps", "initial": True}
    job = FinetuningJob.objects.create(
        project=project,
        dataset=Dataset.objects.create(project=project, name="Decision observations"),
        base_model="Qwen/Qwen3-0.6B",
        hyperparameters={"objective": "decision_cross_entropy", "monitoring": policy},
    )
    family = {
        "question": "Sentiment?",
        "kind": "choice",
        "options": ["negative", "positive"],
        "expected": 4,
        "scored": 4,
        "support": [1, 3],
        "predicted": [4, 0],
        "confusion_matrix": [[1, 0], [3, 0]],
    }
    record = {
        "key": "1:1:development",
        "attempt": 1,
        "stream": "development",
        "step": 1,
        "state": "completed",
        "policy_fingerprint": fingerprint(policy),
        "sample_fingerprint": "frozen-probe",
        "started_at": 100,
        "observed_at": 102,
        "metrics": {
            "eval_loss": 1.1,
            "decisions": 4,
            "distribution_decisions": 4,
            "mean_decisions": 0,
            "brier": 0.7,
            "hard_label_accuracy": 0.25,
            "hard_label_decisions": 4,
            "categorical_families": {
                fingerprint({k: family[k] for k in ("question", "kind", "options")}): family
            },
        },
        "coverage": {"expected": 4, "scored": 4},
        "facts": {},
    }
    training_monitoring.ingest(job, {"checks": [record]})
    training_monitoring.ingest(job, {"checks": [record]})
    later = {
        **record,
        "key": "1:2:development",
        "step": 2,
        "observed_at": 103,
        "metrics": {"eval_loss": 0.9},
    }
    training_monitoring.ingest(job, {"checks": [later]})
    summary = training_monitoring.summary(job)
    assert summary["check_count"] == 2
    assert summary["latest_check"]["step"] == 2
    quality = summary["latest_decision_check"]
    assert quality["step"] == 1
    assert quality["metrics"]["hard_label_accuracy"] == 0.25
    assert quality["assessment"]["state"] == "measured"
    assert {f["code"] for f in quality["assessment"]["findings"]} == {
        "represented_labels_not_predicted",
        "below_probe_majority_baseline",
    }
    assert "Sentiment?" not in json.dumps(quality["assessment"])


@pytest.mark.django_db
def test_distribution_and_mean_checks_do_not_invent_classification_assessments():
    project = Project.objects.create(name="Soft decisions", slug="soft-decisions")
    policy = {"mode": "steps"}
    job = FinetuningJob.objects.create(
        project=project,
        dataset=Dataset.objects.create(project=project, name="Soft decisions"),
        hyperparameters={"objective": "decision_supervised", "monitoring": policy},
    )
    training_monitoring.ingest(
        job,
        {
            "checks": [
                {
                    "key": "1:1:development",
                    "attempt": 1,
                    "stream": "development",
                    "step": 1,
                    "state": "completed",
                    "policy_fingerprint": fingerprint(policy),
                    "sample_fingerprint": "means-and-soft-labels",
                    "observed_at": 102,
                    "metrics": {
                        "eval_loss": 0.4,
                        "decisions": 2,
                        "distribution_decisions": 1,
                        "mean_decisions": 1,
                        "expected_score_mae": 0.2,
                        "brier": 0.1,
                        "hard_label_accuracy": None,
                        "hard_label_decisions": 0,
                        "categorical_families": {},
                    },
                    "coverage": {"expected": 2, "scored": 2},
                    "facts": {},
                }
            ]
        },
    )
    quality = training_monitoring.summary(job)["latest_decision_check"]
    assert quality["metrics"]["expected_score_mae"] == 0.2
    assert quality["assessment"] is None
