import json
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
        observation = training_monitoring.observe(monitoring_job, payload, collect_examples=True)
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
@pytest.mark.parametrize("objective", ["assistant_cross_entropy", "decision_supervised"])
def test_status_observation_never_downloads_examples(monitoring_job, fake_modal, objective):
    monitoring_job.provider = "modal"
    monitoring_job.hyperparameters = {"objective": objective}
    monitoring_job.save()
    payload = {"checks": [receipt(artifact={"path": "examples.json", "sha256": "a" * 64})]}

    summary = training_monitoring.observe(monitoring_job, payload)

    assert summary["collection"]["state"] == "pending"
    assert monitoring_job.validation_runs.get().metrics["eval_loss"] == 0.5
    assert not monitoring_job.validation_runs.get().evidence
    assert fake_modal.log == []


@pytest.mark.django_db
@pytest.mark.parametrize("objective", ["assistant_cross_entropy", "decision_supervised"])
def test_background_collection_reads_full_artifact_once(monitoring_job, fake_modal, objective):
    from overbae.tasks.finetuning import collect_training_evidence

    monitoring_job.provider = "modal"
    monitoring_job.remote_job_id = "run:fc-monitoring"
    monitoring_job.status = "succeeded"
    monitoring_job.hyperparameters = {"objective": objective}
    monitoring_job.requested_configuration = {"runtime": {"app": "sft", "environment": "test"}}
    monitoring_job.progress = {"monitoring": {"optimizer_seconds": 999}}
    monitoring_job.save()
    examples = [{"row": n, "probabilities": [0.2, 0.8]} for n in range(27989)]
    artifact = {"path": "examples.json", "sha256": fingerprint(examples)}
    payload = {"checks": [receipt(artifact=artifact)], "optimizer_seconds": 1}
    fake_modal.deploy("sft", "get_progress", lambda _: {"monitoring": payload})
    fake_modal.adopt("fc-monitoring")
    fake_modal.files["/runs/run/examples.json"] = json.dumps(
        {**artifact, "examples": examples}
    ).encode()

    collect_training_evidence.run(str(monitoring_job.pk))
    check = monitoring_job.validation_runs.get()
    saved_name = check.evidence.name
    page = training_monitoring.examples(monitoring_job, str(check.pk), offset=27980, limit=10)
    assert page["items"] == examples[27980:]
    assert page["next_offset"] is None
    # A terminal job still collects evidence, without replacing newer monitoring facts.
    monitoring_job.refresh_from_db()
    assert monitoring_job.status == "succeeded"
    assert monitoring_job.progress["monitoring"]["optimizer_seconds"] == 999
    assert monitoring_job.progress["monitoring"]["collection"]["state"] == "observed"
    fake_modal.files.clear()
    collect_training_evidence.run(str(monitoring_job.pk))
    check.refresh_from_db()
    assert check.evidence.name == saved_name
    assert fake_modal.spawns() == []
    assert all(name != "get_monitoring_examples" for _, name, _, _ in fake_modal.log)


@pytest.mark.django_db
@pytest.mark.parametrize("failure", ["missing", "checksum", "path"])
def test_failed_evidence_download_preserves_metrics(monitoring_job, fake_modal, failure):
    monitoring_job.provider = "modal"
    monitoring_job.remote_job_id = "run:fc-monitoring"
    monitoring_job.requested_configuration = {"runtime": {"app": "sft", "environment": "test"}}
    monitoring_job.save()
    examples = [{"row": 1}]
    artifact = {"path": "examples.json", "sha256": fingerprint(examples)}
    if failure == "checksum":
        fake_modal.files["/runs/run/examples.json"] = json.dumps(
            {**artifact, "examples": [{"row": 2}]}
        ).encode()
    if failure == "path":
        artifact["path"] = "../another-run/examples.json"
    summary = training_monitoring.observe(
        monitoring_job, {"checks": [receipt(artifact=artifact)]}, collect_examples=True
    )
    assert summary["collection"]["state"] == "unavailable"
    assert monitoring_job.validation_runs.get().metrics["eval_loss"] == 0.5
    assert not monitoring_job.validation_runs.get().evidence


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
