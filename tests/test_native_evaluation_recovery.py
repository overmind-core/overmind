import hashlib
import json
import math
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
from conftest import frozen_dataset
from django.utils import timezone

from modal_shared.decision_inference import input_digest
from overbae.models import DecisionProviderRequest, Project
from overbae.services import (
    decision_providers,
    model_workflows,
    native_evaluation,
    native_evaluation_monitor,
)
from overbae.tasks.native_evaluation import advance_plan, reconcile

pytestmark = pytest.mark.django_db


@pytest.fixture
def evaluation(settings, monkeypatch, request):
    monkeypatch.setenv("OPENROUTER_API_KEY", "recovery-test-key")
    monkeypatch.setenv("JEV_REQUESTS_PER_MINUTE", "10000000")
    monkeypatch.setenv("JEV_TOKENS_PER_SECOND", "10000000")
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": "redis://localhost:6379/15",
        }
    }
    project = Project.objects.create(name="Recovery", slug="automatic-recovery")
    cell = frozen_dataset(
        project,
        [
            {
                "input": {
                    "decision": {
                        "state": f"Evidence {i}",
                        "question": "Choose",
                        "kind": "choice",
                        "options": ["a", "b"],
                    }
                },
                "expected_output": {"probabilities": [1, 0]},
            }
            for i in range(getattr(request, "param", 2))
        ],
        contract="eval",
    ).active_cell
    plan = native_evaluation.create_plan(
        project,
        name="Recover evaluation",
        request_key="recover",
        final_cell=cell,
        participants=[
            {
                "key": "external",
                "name": "External",
                "kind": "external",
                "model": "typesafe/jev-1.13",
                "served_model": "typesafe/jev-1.13-20260917",
            }
        ],
        baseline="external",
        bootstrap_samples=2,
        inference={"concurrency": 1},
    )
    with patch.object(advance_plan, "delay"):
        native_evaluation.launch(plan)
    native_evaluation.advance(plan.pk, stage="verify_inputs")
    plan.refresh_from_db()
    return plan


def answer():
    return {
        "id": "response",
        "provider": "TypeSafe",
        "model": "typesafe/jev-1.13-20260917",
        "usage": {"cost": 0.002},
        "answers": {
            "decision": {"type": "choice", "choice": "0", "probabilities": {"0": 0.9, "1": 0.1}}
        },
    }


def retry_due(plan, stage="final_external"):
    plan.refresh_from_db()
    plan.calls[stage]["retry_at"] = (timezone.now() - timedelta(seconds=1)).isoformat()
    plan.save()


def test_transport_interruption_recovers_only_missing_prediction_and_finishes(evaluation, scripted):
    plan = evaluation
    provider = scripted("https://openrouter.ai/api/v1/systemone")
    provider.fail(httpx.ReadTimeout("response lost")).reply(json_body=answer())
    native_evaluation.advance(plan.pk, stage="final_external")
    plan.refresh_from_db()
    assert plan.state == "running"
    assert plan.calls["final_external"]["state"] == "retrying"
    assert plan.requests.filter(state="completed").count() == 1
    assert not model_workflows.guidance("native_evaluation", plan)["input_required"]
    native_evaluation.advance(plan.pk, stage="final_external")
    assert len(provider.calls) == 2  # Backoff does not occupy a worker or replay work.
    retry_due(plan)
    with patch.object(advance_plan, "delay") as dispatch:
        reconcile()
    assert str(plan.pk) in [call.args[0] for call in dispatch.call_args_list]
    native_evaluation.advance(plan.pk, stage="final_external")
    native_evaluation.advance(plan.pk, stage="score")
    plan.refresh_from_db()
    assert plan.state == "completed", plan.error
    assert len(provider.calls) == 3
    assert plan.requests.filter(state="completed").count() == 2
    assert sorted(len(r.attempts) for r in plan.requests.all()) == [1, 2]
    assert plan.results["costs"]["external"]["unknown_api_attempts"] == 1
    assert plan.results["costs"]["external"]["recorded_evaluation_api_usd"] == pytest.approx(0.004)


def test_exhausted_recovery_completes_with_missing_coverage(evaluation, scripted):
    plan = evaluation
    provider = scripted("https://openrouter.ai/api/v1/systemone").fail(httpx.ReadTimeout("offline"))
    for _ in range(3):
        native_evaluation.advance(plan.pk, stage="final_external")
        retry_due(plan)
    plan.refresh_from_db()
    native_evaluation.advance(plan.pk, stage="score")
    plan.refresh_from_db()
    assert plan.state == "completed", plan.error
    completion = plan.results["completion"]
    assert completion["status"] == "completed_with_issues"
    assert completion["coverage"]["final"]["external"] == {
        "expected": 2,
        "scored": 0,
        "missing_predictions": 2,
        "invalid_predictions": 0,
        "incompatible_inputs": 0,
    }
    assert completion["issues"]
    assert not plan.results["comparisons"]["external"]["raw"]["macro_across_benchmarks"]
    assert all(len(r.attempts) == 3 for r in plan.requests.all())
    native_evaluation.advance(plan.pk)
    assert len(provider.calls) == 6


def test_permanent_provider_failure_does_not_retry(evaluation, scripted):
    provider = scripted("https://openrouter.ai/api/v1/systemone").reply(401)
    native_evaluation.advance(evaluation.pk, stage="final_external")
    evaluation.refresh_from_db()
    native_evaluation.advance(evaluation.pk, stage="score")
    evaluation.refresh_from_db()
    assert evaluation.state == "completed"
    assert evaluation.results["completion"]["status"] == "completed_with_issues"
    native_evaluation.advance(evaluation.pk, stage="final_external")
    assert len(provider.calls) == 2


@pytest.mark.parametrize("baseline_success", [False, True])
def test_partial_baseline_keeps_other_scores_and_only_pairs_available_rows(
    evaluation, scripted, baseline_success
):
    plan = evaluation
    plan.config["participants"].append(
        {**plan.config["participants"][0], "key": "other", "name": "Other"}
    )
    plan.save()
    provider = scripted("https://openrouter.ai/api/v1/systemone")
    provider.fail(httpx.ReadTimeout("offline"))
    if baseline_success:
        provider.reply(json_body=answer())
    else:
        provider.fail(httpx.ReadTimeout("offline"))
    native_evaluation.advance(plan.pk, stage="final_external")
    provider.fail(httpx.ReadTimeout("offline")).fail(httpx.ReadTimeout("offline"))
    for _ in range(2):
        retry_due(plan)
        native_evaluation.advance(plan.pk, stage="final_external")
    provider.reply(json_body=answer())
    native_evaluation.advance(plan.pk, stage="final_other")
    native_evaluation.advance(plan.pk, stage="score")
    plan.refresh_from_db()
    assert plan.state == "completed", plan.error
    completion = plan.results["completion"]
    assert completion["coverage"]["final"]["external"]["scored"] == int(baseline_success)
    assert completion["coverage"]["final"]["other"]["scored"] == 2
    compared = plan.results["comparisons"]["other"]["raw"]
    assert sum(b["paired_decisions"] for b in compared["benchmarks"].values()) == int(
        baseline_success
    )
    assert len(provider.calls) == 8 - 2 * int(baseline_success)


def test_saved_timeout_receipt_can_resume_without_repeating_successes(evaluation, scripted):
    plan = evaluation
    source = native_evaluation.seal_suite(plan, "final")
    request = next(native_evaluation.records(source))
    DecisionProviderRequest.objects.create(
        plan=plan,
        participant="external",
        role="final",
        input_sha256=request["input_sha256"],
        state="submission_unknown",
        error="submission_unknown",
        attempts=[
            {
                "intent_at": timezone.now().isoformat(),
                "error": "submission_unknown",
                "usage": {"response_cost": None},
            }
        ],
    )
    plan.state = "submission_unknown"
    plan.calls["final_external"] = {"state": "submission_unknown"}
    plan.save()
    provider = scripted("https://openrouter.ai/api/v1/systemone").reply(json_body=answer())
    native_evaluation.resume(plan)
    native_evaluation.advance(plan.pk, stage="final_external")
    plan.refresh_from_db()
    assert plan.calls["final_external"]["state"] == "completed"
    assert len(provider.calls) == 2
    assert (
        plan.requests.get(input_sha256=input_digest(request["decision"])).attempts[0]["usage"][
            "response_cost"
        ]
        is None
    )


@pytest.mark.parametrize("state", ["paused", "submission_unknown"])
def test_orphaned_external_intent_resumes_other_inputs_without_replay(evaluation, scripted, state):
    plan = evaluation
    row = next(
        native_evaluation.records(native_evaluation.directory(plan, "final") / "inputs.jsonl")
    )
    record = DecisionProviderRequest.objects.create(
        plan=plan,
        participant="external",
        role="final",
        input_sha256=row["input_sha256"],
        state="submitting",
        attempts=[{"usage": {"response_cost": None}}],
    )
    plan.state = state
    plan.calls["final_external"] = {"state": "submission_unknown"}
    plan.save()
    provider = scripted("https://openrouter.ai/api/v1/systemone").reply(json_body=answer())
    native_evaluation.resume(plan, stage="final_external")
    native_evaluation.advance(plan.pk, stage="final_external")
    native_evaluation.advance(plan.pk, stage="score")
    plan.refresh_from_db()
    record.refresh_from_db()
    assert plan.state == "completed", plan.error
    assert record.state == "submission_unknown"
    assert len(record.attempts) == 1
    assert len(provider.calls) == 1
    assert plan.results["completion"]["coverage"]["final"]["external"]["missing_predictions"] == 1


def test_native_collection_recovers_same_call_without_new_submission(evaluation, fake_modal):
    plan = evaluation
    plan.config["participants"] = [
        {"key": "base", "name": "Base", "kind": "foundation", "model": "fixture"}
    ]
    plan.config["baseline"] = "base"
    plan.config["runtime"] = {"app": "fixture", "environment": "test"}
    plan.calls["final_prepare"] = {"state": "running", "id": "fc-retained"}
    plan.save()
    remote = fake_modal.adopt("fc-retained", unreachable=ConnectionError("observer offline"))
    native_evaluation.advance(plan.pk, stage="final_prepare")
    plan.refresh_from_db()
    assert plan.state == "running"
    assert plan.calls["final_prepare"]["state"] == "retrying"
    retry_due(plan, "final_prepare")
    remote.unreachable = None
    native_evaluation.advance(plan.pk, stage="final_prepare")
    plan.refresh_from_db()
    assert plan.calls["final_prepare"]["state"] == "running"
    assert plan.calls["final_prepare"]["id"] == "fc-retained"
    assert fake_modal.spawns() == []


@pytest.mark.parametrize("progress_when", ["none", "before_failure", "after_failure"])
def test_exhausted_native_collection_scores_only_verified_progress(
    evaluation, fake_modal, progress_when
):
    plan = evaluation
    plan.config["participants"] = [
        {"key": "base", "name": "Base", "kind": "foundation", "model": "fixture"}
    ]
    plan.config["baseline"] = "base"
    plan.config["runtime"] = {"app": "fixture", "environment": "test", "training": "pinned"}
    plan.calls["final_prepare"] = {
        "state": "completed",
        "receipt": {
            "artifact_identity": "fixture",
            "input_sha256": "inputs",
            "ready_decisions": 2,
        },
    }
    plan.calls["final_base"] = {"state": "running", "id": "fc-retained"}
    plan.save()
    remote = fake_modal.adopt("fc-retained")
    if progress_when != "none":
        row = next(
            native_evaluation.records(native_evaluation.directory(plan, "final") / "inputs.jsonl")
        )
        body = (
            json.dumps(
                {
                    "key": row["key"],
                    "input_sha256": row["input_sha256"],
                    "kind": "choice",
                    "model_identity": "fixture",
                    "probabilities": [0.9, 0.1],
                    "log_probabilities": [math.log(0.9), math.log(0.1)],
                }
            ).encode()
            + b"\n"
        )
        root = f"/decision-evaluations/{plan.pk}-final/progress/fc-retained"
        fake_modal.files[root + "/0.jsonl"] = body
        fake_modal.files[root + "/manifest.json"] = json.dumps(
            {
                "call_id": remote.object_id,
                "artifact_identity": "fixture",
                "input_sha256": "inputs",
                "runtime_fingerprint": "pinned",
                "expected": 2,
                "completed": 1,
                "chunks": [{"sha256": hashlib.sha256(body).hexdigest(), "rows": 1}],
            }
        ).encode()
        if progress_when == "before_failure":
            native_evaluation.advance(plan.pk, stage="final_base")
    remote.unreachable = ConnectionError("observer offline")
    for _ in range(3):
        native_evaluation.advance(plan.pk, stage="final_base")
        retry_due(plan, "final_base")
    native_evaluation.advance(plan.pk, stage="score")
    plan.refresh_from_db()
    assert plan.state == "completed", plan.error
    coverage = plan.results["completion"]["coverage"]["final"]["base"]
    assert coverage["scored"] == int(progress_when != "none")
    assert coverage["missing_predictions"] == 2 - int(progress_when != "none")
    assert plan.results["completion"]["issues"][0]["reason"] == "Automatic recovery exhausted."
    assert fake_modal.spawns() == []


def test_incomplete_calibration_does_not_block_final_raw_scores(evaluation, scripted):
    plan = evaluation
    plan.calibration_cell = plan.final_cell
    plan.config["suites"]["calibration"] = plan.config["suites"]["final"]
    plan.save()
    provider = scripted("https://openrouter.ai/api/v1/systemone").reply(401).reply(401)
    native_evaluation.advance(plan.pk, stage="calibration_external")
    native_evaluation.advance(plan.pk, stage="fit_calibration")
    provider.reply(json_body=answer())
    native_evaluation.advance(plan.pk, stage="final_external")
    native_evaluation.advance(plan.pk, stage="score")
    plan.refresh_from_db()
    assert plan.state == "completed", plan.error
    assert plan.calibration["arms"] == {}
    assert "external" in plan.calibration["unavailable"]
    assert set(plan.results["comparisons"]["external"]) == {"raw"}
    assert plan.results["completion"]["coverage"]["final"]["external"]["scored"] == 2


@pytest.mark.parametrize("evaluation", [130], indirect=True)
def test_failed_input_does_not_skip_later_chunks(evaluation, scripted):
    def respond(call):
        if call.json["state"] == "Evidence 0":
            return httpx.ReadTimeout("offline")
        return 200, {"content-type": "application/json"}, json.dumps(answer()).encode()

    provider = scripted("https://openrouter.ai/api/v1/systemone").then(respond)
    for _ in range(3):
        native_evaluation.advance(evaluation.pk, stage="final_external")
        retry_due(evaluation)
    native_evaluation.advance(evaluation.pk, stage="final_external")
    native_evaluation.advance(evaluation.pk, stage="score")
    evaluation.refresh_from_db()
    assert evaluation.state == "completed", evaluation.error
    counts = evaluation.results["completion"]["coverage"]["final"]["external"]
    assert counts["scored"] == 129
    assert counts["missing_predictions"] == 1
    assert len(provider.calls) == 132


def test_exhausted_native_preparation_does_not_submit_dependent_work(evaluation, fake_modal):
    plan = evaluation
    plan.config["participants"] = [
        {"key": "base", "name": "Base", "kind": "foundation", "model": "fixture"}
    ]
    plan.config["baseline"] = "base"
    plan.calls["final_prepare"] = {"state": "running", "id": "fc-retained"}
    plan.save()
    fake_modal.adopt("fc-retained", unreachable=ConnectionError("offline"))
    for _ in range(3):
        native_evaluation.advance(plan.pk, stage="final_prepare")
        retry_due(plan, "final_prepare")
    native_evaluation.advance(plan.pk, stage="final_base")
    native_evaluation.advance(plan.pk, stage="score")
    plan.refresh_from_db()
    assert plan.state == "completed", plan.error
    assert plan.results["completion"]["coverage"]["final"]["base"]["scored"] == 0
    assert fake_modal.spawns() == []


def test_partial_export_keeps_external_responses_not_yet_in_monitor(evaluation, scripted):
    scripted("https://openrouter.ai/api/v1/systemone").reply(json_body=answer())
    decision_providers.external_step(evaluation, "final", evaluation.config["participants"][0])
    receipt = native_evaluation_monitor.retain_partial(evaluation, "final_external")
    assert receipt["partial"]
    path = native_evaluation.directory(evaluation, "final") / "external.jsonl"
    assert len(list(native_evaluation.records(path))) == 2


def test_paused_retry_does_not_restart(evaluation, scripted):
    provider = scripted("https://openrouter.ai/api/v1/systemone").fail(httpx.ReadTimeout("offline"))
    native_evaluation.advance(evaluation.pk, stage="final_external")
    retry_due(evaluation)
    native_evaluation.pause(evaluation)
    native_evaluation.advance(evaluation.pk, stage="final_external")
    assert len(provider.calls) == 2


def test_retry_does_not_block_an_independent_participant(evaluation, scripted):
    plan = evaluation
    plan.config["participants"].append(
        {**plan.config["participants"][0], "key": "other", "name": "Other"}
    )
    plan.save()
    provider = scripted("https://openrouter.ai/api/v1/systemone")
    provider.fail(httpx.ReadTimeout("offline")).fail(httpx.ReadTimeout("offline")).reply(
        json_body=answer()
    )
    native_evaluation.advance(plan.pk, stage="final_external")
    native_evaluation.advance(plan.pk, stage="final_other")
    plan.refresh_from_db()
    assert plan.state == "running"
    assert plan.calls["final_external"]["state"] == "retrying"
    assert plan.calls["final_other"]["state"] == "completed"
    assert len(provider.calls) == 4


def test_invalid_local_input_stays_failed(evaluation):
    source = native_evaluation.directory(evaluation, "final") / "inputs.jsonl"
    source.write_text("changed inputs")
    evaluation.calls.pop("verify_inputs")
    evaluation.save()
    native_evaluation.advance(evaluation.pk, stage="verify_inputs")
    evaluation.refresh_from_db()
    assert evaluation.state == "failed"
    assert evaluation.calls["verify_inputs"]["state"] == "failed"


def test_preparation_recovery_preserves_the_explicit_launch_boundary(evaluation):
    plan = evaluation
    plan.state = "preparing"
    plan.calls = {
        "verify_inputs": {
            "state": "retrying",
            "retry_at": (timezone.now() + timedelta(seconds=30)).isoformat(),
        }
    }
    native_evaluation.settle(plan)
    plan.save()
    assert plan.state == "preparing"
    assert native_evaluation.ready_stages(plan) == []
    retry_due(plan, "verify_inputs")
    native_evaluation.advance(plan.pk, stage="verify_inputs")
    plan.refresh_from_db()
    assert plan.state == "prepared"
    assert native_evaluation.ready_stages(plan) == []
    assert not plan.requests.exists()
