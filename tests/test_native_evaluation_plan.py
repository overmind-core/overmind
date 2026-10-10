import json
import math
from unittest.mock import Mock, patch

import pytest
from conftest import EVAL_ROWS, frozen_dataset
from django.urls import reverse
from rest_framework.test import APIClient

from overbae.models import Dataset, FinetuningJob, Project
from overbae.services import native_evaluation
from overbae.services.datasets import land

pytestmark = pytest.mark.django_db


def setup_plan(*, prepare=True, question_count=None):
    project = Project.objects.create(name="Research", slug="research")
    train = Dataset.objects.create(project=project, name="Training", intent="train")
    job = FinetuningJob.objects.create(
        project=project,
        dataset=train,
        base_model="fixture",
        hyperparameters={"objective": "decision_cross_entropy"},
        status="running",
    )
    cells = []
    for name in ("calibration", "final"):
        dataset = Dataset.objects.create(project=project, name=name, intent="eval")
        land.land_rows(
            dataset,
            [
                {
                    "input": {
                        "decision": {
                            "state": f"{name} evidence {i}",
                            "question": f"Choose for question {i}" if question_count else "Choose",
                            "kind": "choice",
                            "options": ["yes", "no"],
                        }
                    },
                    "expected_output": {"probabilities": [1, 0]},
                    "benchmark": "fixture",
                    "group": str(i),
                }
                for i in range(question_count or 3)
            ],
        )
        dataset.refresh_from_db()
        cells.append(dataset.active_cell)
    with patch(
        "overbae.services.native_evaluation.runtime",
        return_value={"app": "eval-release", "environment": "test"},
    ):
        plan = native_evaluation.schedule(job, calibration_cell=cells[0], final_cell=cells[1])
        assert (
            native_evaluation.schedule(job, calibration_cell=cells[0], final_cell=cells[1]).id
            == plan.id
        )
    if prepare:
        native_evaluation.advance(plan.pk, stage="verify_inputs")
    plan.refresh_from_db()
    return plan


def test_many_question_report_stays_in_artifact_and_paused_scoring_recovers_without_provider(
    tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path
    plan = setup_plan(question_count=240)
    plan.config["bootstrap_samples"] = 2
    plan.save()
    for role in ("calibration", "final"):
        source = native_evaluation.seal_suite(plan, role)
        for arm in ("base", "candidate"):
            probability = 0.8 if arm == "candidate" else 0.6
            with (source.parent / f"{arm}.jsonl").open("w") as output:
                for row in native_evaluation.records(source):
                    output.write(
                        json.dumps(
                            {
                                "key": row["key"],
                                "input_sha256": row["input_sha256"],
                                "kind": "choice",
                                "model_identity": arm,
                                "probabilities": [probability, 1 - probability],
                                "log_probabilities": [
                                    math.log(probability),
                                    math.log(1 - probability),
                                ],
                            }
                        )
                        + "\n"
                    )
    plan.calibration = native_evaluation.fit_calibration(plan)
    plan.calls = {
        stage: {"state": "completed", "receipt": {}}
        for stage in native_evaluation.stage_sequence(plan)[:-1]
    }
    plan.save()
    with patch.object(native_evaluation, "submit", side_effect=AssertionError("no provider")):
        native_evaluation.advance(plan.pk, stage="score")
    plan.refresh_from_db()
    assert plan.state == "completed", plan.error
    assert len(json.dumps({"calls": plan.calls, "results": plan.results})) < 128 * 1024
    report = native_evaluation.directory(plan, "report") / "results.json"
    full = json.loads(report.read_text())
    saved = plan.results["comparisons"]["candidate"]["raw"]["candidate"]
    original = full["comparisons"]["candidate"]["raw"]["candidate"]
    assert saved["benchmarks"] == original["benchmarks"]
    assert saved["slice_count"] == len(original["slices"]) >= 240
    assert plan.calls["score"]["receipt"]["sha256"] == native_evaluation.digest_file(report)
    plan.calls["score"] = {
        "state": "submitting",
        "lease": "stopped-worker",
        "lease_started_at": native_evaluation.timezone.now().isoformat(),
    }
    plan.state = "paused"
    plan.save()
    native_evaluation.resume(plan, stage="score")
    with patch.object(native_evaluation, "submit", side_effect=AssertionError("no provider")):
        native_evaluation.advance(plan.pk, stage="score")
    plan.refresh_from_db()
    assert plan.state == "completed"
    assert json.loads(report.read_text())["comparisons"] == full["comparisons"]
    assert plan.calls["score"]["receipt"]["sha256"] == native_evaluation.digest_file(report)


def test_plan_keeps_references_local_and_freezes_calibration_before_final_predictions(
    tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path
    plan = setup_plan()
    assert plan.state == "running"
    uploaded = native_evaluation.seal_suite(plan, "calibration")
    rows = [json.loads(line) for line in uploaded.read_text().splitlines()]
    assert len(rows) == 3
    assert all(set(row) == {"key", "input_sha256", "decision"} for row in rows)
    assert "probabilities" not in uploaded.read_text()
    assert native_evaluation.stage_sequence(plan).index(
        "fit_calibration"
    ) < native_evaluation.stage_sequence(plan).index("final_base")
    predictions = uploaded.parent / "candidate.jsonl"
    predictions.write_text(
        "".join(
            json.dumps(
                {
                    **{k: v for k, v in row.items() if k != "decision"},
                    "kind": "choice",
                    "model_identity": "candidate",
                    "probabilities": [0.8, 0.2],
                    "log_probabilities": [math.log(0.8), math.log(0.2)],
                }
            )
            + "\n"
            for row in rows
        )
    )
    (uploaded.parent / "base.jsonl").write_bytes(predictions.read_bytes())
    fitted = native_evaluation.fit_calibration(plan)
    assert fitted["fitted_on"] == "calibration"
    assert fitted["decisions"] == 3
    assert native_evaluation.fit_calibration(plan) == fitted
    assert not (native_evaluation.directory(plan, "final") / "candidate.jsonl").exists()


def test_unknown_submission_is_not_repeated(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    plan = setup_plan()
    plan.job.status = "succeeded"
    plan.job.remote_job_id = "run:call"
    plan.job.save()
    with patch(
        "overbae.services.native_evaluation.submit", side_effect=ConnectionError("lost response")
    ) as submit:
        native_evaluation.advance(plan.pk)
        native_evaluation.advance(plan.pk)
    assert submit.call_count == 1
    plan.refresh_from_db()
    assert plan.state == "submission_unknown"
    assert plan.calls["calibration_prepare"]["state"] == "submission_unknown"


def test_recovery_requires_existing_call_identity_and_does_not_submit_again(settings):
    plan = setup_plan()
    plan.job.status = "succeeded"
    plan.job.remote_job_id = "run:call"
    plan.job.save()
    plan.state = "submission_unknown"
    plan.calls = {
        "verify_inputs": {"state": "completed", "receipt": {}},
        "calibration_prepare": {"state": "submission_unknown"},
    }
    plan.save()
    with pytest.raises(ValueError, match="call"):
        native_evaluation.resume(plan)
    native_evaluation.resume(plan, stage="calibration_prepare", call_id="fc-existing")
    with (
        patch.object(native_evaluation, "submit", side_effect=AssertionError("no new call")),
        patch.object(native_evaluation.modal.FunctionCall, "from_id") as lookup,
    ):
        lookup.return_value.get.side_effect = TimeoutError
        native_evaluation.advance(plan.pk)
    lookup.assert_called_once_with("fc-existing")


def test_retry_keeps_pinned_evaluation_release(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    plan = setup_plan()
    with patch(
        "overbae.services.native_evaluation.runtime",
        return_value={"app": "changed", "environment": "wrong"},
    ):
        again = native_evaluation.schedule(
            plan.job, calibration_cell=plan.calibration_cell, final_cell=plan.final_cell
        )
    assert again.pk == plan.pk
    assert again.config["runtime"]["app"] == "eval-release"


def test_paused_provider_collection_recovers_only_its_recorded_call(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    plan = setup_plan()
    plan.job.status = "succeeded"
    plan.job.remote_job_id = "run:training"
    plan.job.save()
    plan.state = "paused"
    plan.calls["calibration_prepare"] = {
        "state": "running",
        "id": "fc-existing",
        "lease": "interrupted-collector",
        "lease_started_at": native_evaluation.timezone.now().isoformat(),
    }
    plan.save()
    for call_id in (None, "fc-different"):
        with pytest.raises(ValueError):
            native_evaluation.resume(plan, stage="calibration_prepare", call_id=call_id)
    native_evaluation.resume(plan, stage="calibration_prepare", call_id="fc-existing")
    with (
        patch.object(native_evaluation, "submit", side_effect=AssertionError("no new call")),
        patch.object(native_evaluation.modal.FunctionCall, "from_id") as lookup,
    ):
        lookup.return_value.get.side_effect = TimeoutError
        native_evaluation.advance(plan.pk, stage="calibration_prepare")
    lookup.assert_called_once_with("fc-existing")
    plan.refresh_from_db()
    assert plan.calls["calibration_prepare"]["id"] == "fc-existing"
    assert "lease" not in plan.calls["calibration_prepare"]


def test_complete_plan_uses_six_provider_calls_and_retains_paired_coverage(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    plan = setup_plan()
    plan.config["bootstrap_samples"] = 10
    plan.save()
    plan.job.status = "succeeded"
    plan.job.remote_job_id = "run:call"
    plan.job.save()
    submitted = []

    def submit(plan, stage):
        role, arm = stage.split("_", 1)
        if role == "final":
            assert plan.calibration and plan.calibration["fitted_on"] == "calibration"
        source = native_evaluation.seal_suite(plan, role)
        if arm != "prepare":
            probability = 0.8 if arm == "candidate" else 0.6
            with (source.parent / f"{arm}.jsonl").open("w") as output:
                for line in source.read_text().splitlines():
                    row = json.loads(line)
                    output.write(
                        json.dumps(
                            {
                                "key": row["key"],
                                "input_sha256": row["input_sha256"],
                                "kind": "choice",
                                "model_identity": arm,
                                "probabilities": [probability, 1 - probability],
                                "log_probabilities": [
                                    math.log(probability),
                                    math.log(1 - probability),
                                ],
                            }
                        )
                        + "\n"
                    )
        submitted.append(stage)
        return "call-" + stage

    with (
        patch.object(native_evaluation, "submit", side_effect=submit),
        patch.object(native_evaluation, "collect"),
        patch("modal.FunctionCall.from_id", return_value=Mock(get=Mock(return_value={}))),
    ):
        for _ in range(18):
            native_evaluation.advance(plan.id)
    plan.refresh_from_db()
    assert plan.state == "completed", plan.error
    assert len(submitted) == len(set(submitted)) == 6
    result = plan.results["raw"]["benchmarks"]["fixture"]
    assert result["expected"] == result["paired_decisions"] == 3
    assert result["metrics"]["cross_entropy"]["candidate_minus_baseline"] < 0


def test_incomplete_calibration_cannot_fit_or_start_final(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    plan = setup_plan()
    source = native_evaluation.seal_suite(plan, "calibration").parent
    (source / "base.jsonl").write_text("")
    (source / "candidate.jsonl").write_text("")
    with pytest.raises(ValueError, match="coverage"):
        native_evaluation.fit_calibration(plan)
    plan.refresh_from_db()
    assert plan.calibration == {}
    assert not (native_evaluation.directory(plan, "final") / "candidate.jsonl").exists()


def test_embedded_probability_target_is_never_uploaded(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    plan = setup_plan(prepare=False)
    from types import SimpleNamespace

    malicious = SimpleNamespace(
        index=0,
        extra={},
        input={
            "decision": {
                "state": "evidence",
                "question": "Choose",
                "kind": "choice",
                "options": ["yes", "no"],
                "probabilities": [1, 0],
            }
        },
        expected_output={"probabilities": [1, 0]},
    )
    with patch.object(native_evaluation.rows, "iter_rows", return_value=iter([malicious])):
        source = native_evaluation.seal_suite(plan, "calibration")
    assert source.read_text() == ""
    assert len((source.parent / "failures.jsonl").read_text().splitlines()) == 1


def test_native_plan_rejects_chat_suite_before_any_use():
    plan = setup_plan()
    chat = frozen_dataset(plan.job.project, EVAL_ROWS, contract="eval")
    with pytest.raises(ValueError, match="native decision"):
        native_evaluation.schedule(
            plan.job, calibration_cell=chat.active_cell, final_cell=plan.final_cell
        )
    plan.refresh_from_db()
    assert plan.calibration_cell_id != chat.active_cell.id


def test_native_plan_api_is_idempotent_and_project_scoped(settings):
    from overbae.models import NativeEvaluationPlan, ProjectMembership, User

    settings.STRIPE_SECRET_KEY = ""
    plan = setup_plan()
    user = User.objects.create_user(email="native-api@example.com", password="fixture")
    ProjectMembership.objects.create(project=plan.job.project, user=user)
    client = APIClient()
    client.force_authenticate(user=user)
    url = reverse("finetuningjob-native-evaluation", kwargs={"id": plan.job_id})
    body = {
        "calibration_cell": str(plan.calibration_cell_id),
        "final_cell": str(plan.final_cell_id),
    }
    for _ in range(2):
        response = client.post(url, body, format="json")
        assert response.status_code == 202, response.content
        assert response.json()["id"] == str(plan.pk)
    other = Project.objects.create(name="Other", slug="other-native-api")
    foreign = frozen_dataset(other, EVAL_ROWS, contract="eval")
    response = client.post(url, {**body, "final_cell": str(foreign.active_cell.id)}, format="json")
    assert response.status_code == 404
    assert NativeEvaluationPlan.objects.filter(job=plan.job).count() == 1
    ProjectMembership.objects.filter(user=user).delete()
    assert client.post(url, body, format="json").status_code == 404


def test_overlapping_reconciliation_observes_provider_call_once(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    plan = setup_plan()
    plan.job.status = "succeeded"
    plan.job.remote_job_id = "run:call"
    plan.job.save()
    with patch.object(native_evaluation, "submit", return_value="call-existing"):
        native_evaluation.advance(plan.id)

    def get_result(timeout):
        native_evaluation.advance(plan.id)
        return {"ready": True}

    provider = Mock(get=Mock(side_effect=get_result))
    with (
        patch("modal.FunctionCall.from_id", return_value=provider),
        patch.object(native_evaluation, "collect") as collect,
    ):
        native_evaluation.advance(plan.id)
    provider.get.assert_called_once_with(timeout=0)
    collect.assert_called_once()
    plan.refresh_from_db()
    assert plan.calls["calibration_prepare"]["state"] == "completed"


def test_pending_provider_observation_can_be_polled_again(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    plan = setup_plan()
    plan.job.status = "succeeded"
    plan.job.remote_job_id = "run:call"
    plan.job.save()
    with patch.object(native_evaluation, "submit", return_value="call-existing") as submit:
        native_evaluation.advance(plan.id)
        provider = Mock(get=Mock(side_effect=[TimeoutError(), {"ready": True}]))
        with (
            patch("modal.FunctionCall.from_id", return_value=provider),
            patch.object(native_evaluation, "collect") as collect,
        ):
            native_evaluation.advance(plan.id)
            native_evaluation.advance(plan.id)
    assert submit.call_count == 1
    assert provider.get.call_count == 2
    collect.assert_called_once()
    plan.refresh_from_db()
    assert plan.calls["calibration_prepare"]["state"] == "completed"


def test_failed_training_dependency_does_not_wait_forever():
    plan = setup_plan()
    FinetuningJob.objects.filter(pk=plan.job_id).update(status="failed")
    with patch.object(native_evaluation, "submit") as submit:
        native_evaluation.advance(plan.pk)
    plan.refresh_from_db()
    assert plan.state == "failed"
    assert plan.calls["calibration_candidate"]["dependency_state"] == "failed"
    submit.assert_not_called()
