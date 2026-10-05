import math
from unittest.mock import patch

import pytest
from conftest import frozen_dataset

from modal_shared.decision_inference import input_digest
from overbae.models import DecisionProviderRequest, Project
from overbae.services import decision_providers, native_evaluation
from overbae.services.decision_providers import interpret_response
from overbae.services.native_evaluation import create_plan

pytestmark = pytest.mark.django_db


def fixture():

    project = Project.objects.create(name="Provider recovery", slug="provider-recovery")
    decision = {
        "state": "Evidence",
        "question": "Pick",
        "kind": "choice",
        "options": ["a", "b", "c"],
    }
    dataset = frozen_dataset(
        project,
        [{"input": {"decision": decision}, "expected_output": {"probabilities": [1, 0, 0]}}],
        contract="eval",
    )
    participant = {
        "key": "external",
        "name": "External",
        "kind": "external",
        "model": "typesafe/jev-1.13",
        "served_model": "typesafe/jev-1.13-20260917",
    }
    with patch(
        "overbae.services.native_evaluation.runtime",
        return_value={"app": "offline", "environment": "test"},
    ):
        plan = create_plan(
            project,
            name="Recovery",
            request_key="recovery",
            participants=[participant],
            baseline="external",
            final_cell=dataset.active_cell,
        )
    return plan, participant, decision


def payload():
    return {
        "id": "provider-record",
        "model": "typesafe/jev-1.13-20260917",
        "provider": "TypeSafe",
        "usage": {"cost": 0.002, "input_tokens": 20, "output_tokens": 10},
        "answers": {
            "decision": {
                "type": "choice",
                "choice": "2",
                "probabilities": {"0": 0.334, "1": 0.333, "2": 0.334},
            }
        },
    }


def test_saved_response_is_recovered_without_paid_repeat_and_discrepancies_are_visible():

    plan, participant, request = fixture()
    DecisionProviderRequest.objects.create(
        plan=plan,
        participant="external",
        role="final",
        input_sha256=input_digest(request),
        state="received",
        response=payload(),
        usage={"response_cost": 0.002},
    )
    with patch(
        "overbae.core.decisions.request_once", side_effect=AssertionError("must not repeat")
    ):
        result = decision_providers.external_step(plan, "final", participant)
    assert result["completed"]
    record = plan.requests.get()
    assert record.state == "completed"
    assert sum(record.prediction["probabilities"]) == pytest.approx(1)
    assert record.diagnostics["original_probability_sum"] == pytest.approx(1.001)
    assert record.diagnostics["provider_choice_matches_common_tie_break"] is False
    assert record.response == payload()


def test_unresolved_transport_intent_blocks_replay():

    plan, participant, request = fixture()
    DecisionProviderRequest.objects.create(
        plan=plan,
        participant="external",
        role="final",
        input_sha256=input_digest(request),
        state="submitting",
    )
    with (
        patch("overbae.core.decisions.request_once", side_effect=AssertionError("must not replay")),
        pytest.raises(decision_providers.SubmissionUnknownError),
    ):
        decision_providers.external_step(plan, "final", participant)
    assert plan.requests.count() == 1


def test_adapter_rejects_model_drift_and_unexplainable_probability_mass():

    _, participant, request = fixture()
    for changed in (
        {**payload(), "model": "unrequested-model"},
        {
            **payload(),
            "answers": {
                "decision": {
                    "type": "choice",
                    "choice": "0",
                    "probabilities": {"0": 0.1, "1": 0.1, "2": 0.1},
                }
            },
        },
    ):
        with pytest.raises(ValueError):
            interpret_response(request, changed, participant)
    vector, diagnostics = interpret_response(request, payload(), participant)
    assert all(math.isfinite(x) for x in vector["log_probabilities"])
    assert diagnostics["logarithm_floor"] == 1e-12


def test_served_snapshot_is_reused_across_suites_without_changing_requested_configuration():

    plan, participant, request = fixture()
    participant.pop("served_model")
    DecisionProviderRequest.objects.create(
        plan=plan,
        participant="external",
        role="calibration",
        input_sha256=input_digest(request),
        state="completed",
        response=payload(),
    )
    with patch(
        "overbae.core.decisions.request_once", return_value=(payload(), {"response_cost": 0.002})
    ) as transport:
        decision_providers.external_step(plan, "final", participant)
    assert transport.call_args.args[0]["model"] == payload()["model"]


def test_incompatible_provider_input_remains_visible_in_coverage():

    plan, participant, _ = fixture()
    request = {
        "state": "Evidence",
        "question": "Rate",
        "kind": "score",
        "options": [str(i) for i in range(11)],
    }
    dataset = frozen_dataset(
        plan.project,
        [{"input": {"decision": request}, "expected_output": {"probabilities": [1] + [0] * 10}}],
        contract="eval",
    )
    plan.final_cell = dataset.active_cell
    plan.config["suites"]["final"].update(
        cell=str(dataset.active_cell.pk), fingerprint=dataset.active_cell.fingerprint
    )
    plan.save()
    with patch("overbae.core.decisions.request_once", side_effect=AssertionError("incompatible")):
        result = decision_providers.external_step(plan, "final", participant)
    assert result["completed"]
    report = native_evaluation.score(plan)["comparisons"]["external"]["raw"]
    assert sum(v["incompatible_inputs"] for v in report["candidate"]["benchmarks"].values()) == 1


def test_external_resume_verifies_changed_input_chunk_without_resubmitting():
    plan, participant, request = fixture()
    with patch(
        "overbae.core.decisions.request_once", return_value=(payload(), {"response_cost": 0.002})
    ):
        result = decision_providers.external_step(plan, "final", participant)
    assert result["completed"]
    chunk = native_evaluation.directory(plan, "final") / "chunks" / "0.jsonl"
    assert chunk.exists()
    chunk.write_text(chunk.read_text().replace("Evidence", "Changed"))
    with (
        patch("overbae.core.decisions.request_once", side_effect=AssertionError("do not replay")),
        pytest.raises(ValueError, match="chunk"),
    ):
        decision_providers.external_step(plan, "final", participant)
