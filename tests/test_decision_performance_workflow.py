import json
from unittest.mock import Mock, patch

import pytest
from test_decision_provider_recovery import fixture, payload

from modal_shared.decision_inference import input_digest
from overbae.models import DecisionPerformanceRequest, DecisionPerformanceRun
from overbae.services import decision_performance, native_evaluation
from overbae.services.decision_performance import validate_response

pytestmark = pytest.mark.django_db


def test_saved_performance_workload_measures_client_requests_and_retains_failures():

    plan, _, _ = fixture()
    native_evaluation.seal_suite(plan, "final")
    plan.state = "completed"
    plan.save()
    run = decision_performance.create(
        plan,
        name="Latency",
        request_key="latency",
        workload={
            "sample_size": 1,
            "repetitions": 3,
            "concurrency": 1,
            "seed": 12,
            "questions_per_request": 1,
        },
    )
    answers = [
        (payload(), {"response_cost": 0.002}),
        TimeoutError("unresolved"),
        (payload(), {"response_cost": 0.002}),
    ]
    with patch("overbae.core.decisions.request_once", side_effect=answers) as transport:
        decision_performance.advance(run.pk)
        decision_performance.advance(run.pk)
    assert transport.call_count == 3
    run.refresh_from_db()
    assert run.state == "completed"
    report = run.results["participants"]["external"]
    assert report["valid_decisions"] == 2
    assert report["failed_requests"] == 0
    assert report["unknown_requests"] == 1
    assert report["recorded_cost_usd"] == pytest.approx(0.004)
    assert report["latency_ms"]["count"] == 2
    assert report["conditions"]["provider_cache"] == "uncontrolled"
    assert report["conditions"]["native_worker"] is False
    assert DecisionPerformanceRun.objects.count() == 1


def test_unknown_performance_submission_is_not_replayed():

    plan, _, _ = fixture()
    native_evaluation.seal_suite(plan, "final")
    plan.state = "completed"
    plan.save()
    run = decision_performance.create(
        plan,
        name="Interrupted",
        request_key="interrupted",
        workload={
            "sample_size": 1,
            "repetitions": 1,
            "concurrency": 1,
            "seed": 12,
            "questions_per_request": 1,
        },
    )
    DecisionPerformanceRequest.objects.create(
        run=run, participant="external", position=0, state="submitting"
    )
    with patch("overbae.core.decisions.request_once", side_effect=AssertionError("do not replay")):
        decision_performance.advance(run.pk)
    run.refresh_from_db()
    assert run.results["participants"]["external"]["unknown_requests"] == 1


def test_saved_performance_response_is_recovered_without_repeating_a_request():

    plan, _, _ = fixture()
    native_evaluation.seal_suite(plan, "final")
    plan.state = "completed"
    plan.save()
    run = decision_performance.create(
        plan,
        name="Recovery",
        request_key="recover-response",
        workload={
            "sample_size": 1,
            "repetitions": 1,
            "concurrency": 1,
            "seed": 12,
            "questions_per_request": 1,
        },
    )
    DecisionPerformanceRequest.objects.create(
        run=run,
        participant="external",
        position=0,
        state="submitting",
        response=payload(),
        usage={"response_cost": 0.002},
        latency_ms=10,
    )
    with patch("overbae.core.decisions.request_once", side_effect=AssertionError("do not replay")):
        decision_performance.advance(run.pk)
    run.refresh_from_db()
    report = run.results["participants"]["external"]
    assert report["valid_requests"] == 1
    assert report["valid_decisions_per_active_second"] is None


def test_native_performance_rejects_changed_model_and_runtime():

    participant = {"kind": "trained", "quality_identity": "sealed", "quality_runtime": "runtime"}
    with pytest.raises(ValueError, match="identity"):
        validate_response(
            participant,
            [],
            {"predictions": [], "model_identity": "other", "runtime_fingerprint": "runtime"},
        )
    with pytest.raises(ValueError, match="runtime"):
        validate_response(
            participant,
            [],
            {"predictions": [], "model_identity": "sealed", "runtime_fingerprint": "other"},
        )


def test_native_measurement_collects_saved_calls_and_reports_observer_latency_unknown():

    plan, _, decision = fixture()
    native_evaluation.seal_suite(plan, "final")
    plan.config["participants"] = [
        {"key": "base", "kind": "foundation", "model": "Qwen/Qwen3.5-4B"}
    ]
    plan.results = {
        "comparisons": {"base": {"raw": {"candidate": {"model_identity": "base-seal"}}}}
    }
    plan.calls = {
        "final_base": {"state": "completed", "receipt": {"runtime_fingerprint": "runtime"}}
    }
    (native_evaluation.directory(plan, "final") / "base.jsonl").write_text(
        json.dumps({"model_identity": "base-seal"}) + "\n"
    )
    plan.state = "completed"
    plan.save()
    run = decision_performance.create(
        plan,
        name="Native recovery",
        request_key="native-recovery",
        workload={
            "sample_size": 1,
            "repetitions": 1,
            "concurrency": 1,
            "seed": 1,
            "questions_per_request": 1,
        },
    )
    DecisionPerformanceRequest.objects.create(
        run=run, participant="base", position=0, state="submission_unknown", call_id="fc-existing"
    )
    reply = {
        "predictions": [{"input_sha256": input_digest(decision), "probabilities": [1, 0, 0]}],
        "model_identity": "base-seal",
        "runtime_fingerprint": "runtime",
        "worker_instance": "worker-1",
    }
    call = Mock(object_id="fc-existing")
    call.get.side_effect = [TimeoutError("observer timeout"), reply]
    worker = Mock()
    worker.predict.spawn.side_effect = AssertionError("never repeat a submitted call")
    with (
        patch.object(decision_performance, "endpoint", return_value=worker),
        patch.object(
            decision_performance.modal.FunctionCall, "from_id", return_value=call
        ) as lookup,
    ):
        decision_performance.advance(run.pk)
        run.refresh_from_db()
        assert run.state == "queued"
        decision_performance.advance(run.pk)
        decision_performance.advance(run.pk)
    assert lookup.call_count == 2
    worker.predict.spawn.assert_not_called()
    run.refresh_from_db()
    assert run.state == "completed"
    measurement = run.results["participants"]["base"]
    assert measurement["valid_decisions"] == 1
    assert measurement["unmeasured_latency_requests"] == 1
    assert measurement["latency_ms"]["p50"] is None
    assert measurement["marginal_recorded_usd_per_valid_decision"] is None
    assert measurement["amortized_recorded_component_usd_per_decision"] is None


def test_performance_can_qualify_a_pinned_external_model_before_quality_finishes():
    plan, _, _ = fixture()
    assert plan.state == "draft"
    run = decision_performance.create(
        plan,
        name="Qualification",
        request_key="qualification",
        workload={
            "sample_size": 1,
            "repetitions": 1,
            "concurrency": 1,
            "seed": 12,
            "questions_per_request": 1,
        },
    )
    with patch(
        "overbae.core.decisions.request_once", return_value=(payload(), {"response_cost": 0.002})
    ):
        decision_performance.advance(run.pk)
    run.refresh_from_db()
    assert run.state == "completed"
    plan.refresh_from_db()
    assert plan.state == "draft"


def test_early_performance_pins_first_snapshot_and_rejects_version_drift():
    plan, _, _ = fixture()
    plan.config["participants"][0].pop("served_model", None)
    plan.save()
    run = decision_performance.create(
        plan,
        name="Snapshot",
        request_key="snapshot",
        workload={
            "sample_size": 1,
            "repetitions": 2,
            "concurrency": 1,
            "seed": 12,
            "questions_per_request": 1,
        },
    )
    changed = {**payload(), "model": "typesafe/jev-1.13-other"}
    with patch(
        "overbae.core.decisions.request_once", side_effect=[(payload(), {}), (changed, {})]
    ) as request:
        decision_performance.advance(run.pk)
    run.refresh_from_db()
    assert request.call_args_list[1].args[0]["model"] == payload()["model"]
    assert run.results["participants"]["external"]["failed_requests"] == 1


def test_performance_acknowledges_before_scanning_frozen_workload():
    plan, _, _ = fixture()
    with patch.object(
        native_evaluation, "seal_suite", side_effect=AssertionError("do not scan in the request")
    ):
        run = decision_performance.create(
            plan,
            name="Background workload",
            request_key="background",
            workload={
                "sample_size": 1,
                "repetitions": 1,
                "concurrency": 1,
                "seed": 2,
                "questions_per_request": 1,
            },
        )
    assert run.state == "queued"
    assert "inputs" not in run.workload
    with patch("overbae.core.decisions.request_once", return_value=(payload(), {})):
        decision_performance.advance(run.pk)
    run.refresh_from_db()
    assert run.state == "completed"
    assert len(run.workload["inputs"]) == 1
