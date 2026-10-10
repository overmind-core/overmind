from unittest.mock import patch

import pytest

from modal_shared.training_monitoring import fingerprint
from overbae.models import (
    Dataset,
    FinetuningJob,
    OperationalEvent,
    OperationalRun,
    Project,
    TrainingValidationRun,
)
from overbae.services import training_monitoring
from overbae.services.training_cancellation import cancel


@pytest.fixture
def monitoring_job():
    project = Project.objects.create(name="Monitoring", slug="monitoring")
    dataset = Dataset.objects.create(project=project)
    return FinetuningJob.objects.create(project=project, dataset=dataset, base_model="fixture")


def receipt(state="completed", **extra):
    return {
        "key": "1:development:14",
        "attempt": 1,
        "stream": "development",
        "step": 14,
        "state": state,
        "policy_fingerprint": "a" * 64,
        "sample_fingerprint": "b" * 64,
        "started_at": 100,
        "observed_at": 110,
        "metrics": {"eval_loss": 0.5},
        "coverage": {"expected": 2048, "scored": 2048},
        **extra,
    }


def test_native_baseline_alias_and_schema_checks_have_one_preflight_contract():
    policy = training_monitoring.resolve(
        {"objective": "decision_cross_entropy", "pre_training_baseline": False},
        has_development=True,
        provider="modal",
    )
    assert policy["initial"] is False
    with pytest.raises(ValueError, match="initial"):
        training_monitoring.resolve(
            {
                "objective": "decision_cross_entropy",
                "pre_training_baseline": False,
                "monitoring": {"initial": True},
            },
            has_development=True,
            provider="modal",
        )
    with pytest.raises(ValueError, match="schema"):
        training_monitoring.resolve(
            {"monitoring": {"generation": {"kind": "json_schema", "schema": {"type": "invalid"}}}},
            has_development=True,
            provider="modal",
        )
    with pytest.raises(ValueError, match="reference"):
        training_monitoring.resolve(
            {
                "monitoring": {
                    "generation": {
                        "kind": "json_schema",
                        "schema": {"$ref": "https://example.com/schema"},
                    }
                }
            },
            has_development=True,
            provider="modal",
        )


@pytest.mark.django_db
def test_receipts_survive_duplicate_poll_stale_poll_and_cancel(monitoring_job):
    training_monitoring.ingest(monitoring_job, {"checks": [receipt("running")]})
    training_monitoring.ingest(monitoring_job, {"checks": [receipt()]})
    training_monitoring.ingest(monitoring_job, {"checks": [receipt()]})
    training_monitoring.ingest(monitoring_job, {"checks": [receipt("running")]})
    assert monitoring_job.validation_runs.count() == 1
    assert monitoring_job.validation_runs.get().state == "completed"
    operation = OperationalRun.objects.get(
        project=monitoring_job.project, kind="training_validation"
    )
    assert operation.snapshot["status"] == "complete"
    assert operation.snapshot["facts"]["check_id"] == str(monitoring_job.validation_runs.get().id)
    assert OperationalEvent.objects.filter(operation=operation).count() == 2
    pending = receipt("running", key="1:development:28", step=28)
    training_monitoring.ingest(monitoring_job, {"checks": [pending]})
    training_monitoring.interrupt(monitoring_job, reason="cancelled")
    assert list(
        monitoring_job.validation_runs.order_by("step").values_list("state", flat=True)
    ) == ["completed", "interrupted"]
    assert training_monitoring.snapshot(monitoring_job)["checks"][0]["metrics"]["eval_loss"] == 0.5


@pytest.mark.django_db
def test_terminal_receipts_are_immutable_and_attempts_are_distinct(monitoring_job):
    training_monitoring.ingest(monitoring_job, {"checks": [receipt()]})
    with pytest.raises(ValueError, match="changed"):
        training_monitoring.ingest(
            monitoring_job, {"checks": [receipt(metrics={"eval_loss": 0.01})]}
        )
    training_monitoring.ingest(
        monitoring_job, {"checks": [receipt(key="2:development:14", attempt=2)]}
    )
    assert monitoring_job.validation_runs.count() == 2


@pytest.mark.django_db
def test_collector_failure_preserves_quality_and_late_receipt_does_not_resume_job(monitoring_job):
    payload = {"checks": [receipt(artifact={"sha256": "a" * 64})]}
    with patch.object(
        training_monitoring, "collect", side_effect=TimeoutError("storage unavailable")
    ):
        observation = training_monitoring.observe(monitoring_job, payload)
    assert observation["collection"]["state"] == "unavailable"
    assert monitoring_job.validation_runs.get().metrics["eval_loss"] == 0.5
    assert not monitoring_job.validation_runs.get().evidence
    pending = receipt("running", key="1:development:28", step=28)
    training_monitoring.ingest(monitoring_job, {"checks": [pending]})
    training_monitoring.interrupt(monitoring_job, reason="cancelled")
    monitoring_job.status = "cancelled"
    monitoring_job.save()
    training_monitoring.ingest(monitoring_job, {"checks": [{**pending, "state": "completed"}]})
    monitoring_job.refresh_from_db()
    assert monitoring_job.status == "cancelled"
    assert monitoring_job.validation_runs.get(key=pending["key"]).state == "completed"


@pytest.mark.django_db
def test_evidence_is_checksum_verified_paged_and_not_in_summary(monitoring_job):
    examples = [{"row": n, "prediction": "yes", "reference": "no"} for n in range(12)]
    record = receipt(artifact={"sha256": fingerprint(examples), "examples": examples})
    training_monitoring.ingest(monitoring_job, {"checks": [record]})
    check = monitoring_job.validation_runs.get()
    assert "examples" not in training_monitoring.snapshot(monitoring_job)["checks"][0]
    with patch(
        "modal.Function.from_name", side_effect=AssertionError("passive read called provider")
    ):
        page = training_monitoring.examples(monitoring_job, str(check.id), offset=5, limit=3)
    assert [example["row"] for example in page["items"]] == [5, 6, 7]
    assert page["next_offset"] == 8
    with pytest.raises(ValueError, match="checksum"):
        training_monitoring.ingest(
            monitoring_job,
            {
                "checks": [
                    receipt(
                        key="1:development:15",
                        step=15,
                        artifact={"sha256": "0" * 64, "examples": examples},
                    )
                ]
            },
        )
    assert TrainingValidationRun.objects.count() == 1


@pytest.mark.django_db
def test_failed_check_preserves_reason_and_cannot_become_success(monitoring_job):
    training_monitoring.ingest(
        monitoring_job,
        {
            "checks": [
                receipt("failed", error={"code": "scorer_invalid", "message": "Missing labels"})
            ]
        },
    )
    check = monitoring_job.validation_runs.get()
    assert check.failure["code"] == "scorer_invalid"
    with pytest.raises(ValueError, match="changed"):
        training_monitoring.ingest(monitoring_job, {"checks": [receipt()]})


@pytest.mark.django_db
def test_cancel_is_idempotent_preserves_checks_and_reports_provider_failure(monitoring_job):
    monitoring_job.status = "running"
    monitoring_job.remote_job_id = "run:call"
    monitoring_job.provider = "modal"
    monitoring_job.save()
    training_monitoring.ingest(monitoring_job, {"checks": [receipt()]})
    with (
        patch("overbae.services.training_cancellation.get_runner") as runner,
        patch("overbae.tasks.finetuning.collect_training_evidence.apply_async") as collect,
    ):
        runner.return_value.poll.side_effect = RuntimeError("unavailable")
        runner.return_value.cancel.side_effect = RuntimeError("provider did not acknowledge")
        cancel(monitoring_job)
        cancel(monitoring_job)
    assert runner.return_value.cancel.call_count == 1
    assert collect.call_count == 1
    monitoring_job.refresh_from_db()
    assert monitoring_job.status == "cancelled"
    assert "provider did not acknowledge" in monitoring_job.error_message
    assert monitoring_job.validation_runs.get().state == "completed"
