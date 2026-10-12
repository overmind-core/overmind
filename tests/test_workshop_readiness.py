import json
from types import SimpleNamespace

import pytest
from rest_framework.exceptions import ValidationError

from overbae.api.serializers import FinetuningJobSerializer
from overbae.models import (
    Capability,
    Dataset,
    EvalSet,
    EvalSetMember,
    Evaluator,
    FinetuningJob,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.datasets import alignment, land, review, use
from overbae.services.mcp.contracts.datasets import serialize_dataset_detail
from overbae.services.mcp.errors import mcp_cell_contract
from overbae.services.training_preparation import request_preparation

pytestmark = pytest.mark.django_db


@pytest.fixture
def capability():
    project = Project.objects.create(name="KYC", slug="kyc")
    return Capability.objects.create(
        project=project,
        name="Screener",
        slug="screener",
        improvement_metadata={
            "capability_card": {
                "system_prompt": "Screen the evidence and return the escalation packet.",
                "input_schema": {
                    "type": "object",
                    "required": ["onboarding_id", "documents"],
                    "properties": {
                        "onboarding_id": {"type": "string"},
                        "documents": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    },
                },
                "output_schema": {
                    "required_keys": ["escalate", "reason"],
                    "properties": {
                        "escalate": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                },
            }
        },
    )


def make_dataset(capability, *, intent="eval", evidence=True, case_id="case1"):
    ds = Dataset.objects.create(
        project=capability.project, capability=capability, name="Cases", intent=intent
    )
    inp = {"onboarding_id": case_id}
    if evidence:
        inp["documents"] = ["Identity document expired; policy requires escalation."]
    output = {"escalate": True, "reason": "Expired document"}
    row = (
        {"input": inp, "expected_output": output}
        if intent == "eval"
        else {
            "messages": [
                {
                    "role": "system",
                    "content": capability.improvement_metadata["capability_card"]["system_prompt"],
                },
                {"role": "user", "content": json.dumps(inp)},
                {"role": "assistant", "content": json.dumps(output)},
            ]
        }
    )
    land.land_rows(ds, [row])
    ds.refresh_from_db()
    return ds


def test_id_only_eval_and_invalid_final_packet_fail_declared_schema(capability, frame_of):
    ds = make_dataset(capability, evidence=False)
    assert ds.active_cell.fits("eval")[0]
    assert not ds.active_cell.capability_report["ok"]
    assert "documents" in ds.active_cell.capability_report["reason"]
    assert use.use(ds, "eval").used_at is not None
    frame = frame_of(
        [{"input": {"onboarding_id": "a", "documents": []}, "expected_output": {"escalate": "yes"}}]
    )
    report = alignment.capability_contract(capability, frame, "eval")
    assert not report["ok"]
    assert report["rows_ok"] == 0
    assert "input violates" in report["reason"] and "expected_output violates" in report["reason"]


def test_worker_prompt_reports_capability_mismatch(capability, frame_of):
    frame = frame_of(
        [
            {
                "messages": [
                    {"role": "system", "content": "Extract documents."},
                    {"role": "user", "content": "document"},
                    {"role": "assistant", "content": '{"escalate":true,"reason":"Expired"}'},
                ]
            }
        ]
    )
    report = alignment.capability_contract(capability, frame, "train")
    assert not report["ok"] and "system turn differs" in report["reason"]


def test_a_fitting_eval_version_leads_to_evaluation_readiness_without_warnings(capability):
    ds = make_dataset(capability)
    assert review.warnings(ds, ds.active_cell) == []
    detail = serialize_dataset_detail(ds)
    assert [action.tool for action in detail.next_actions] == [
        "check_evaluation_readiness",
        "check_optimizer_readiness",
    ]
    assert detail.versions[0].fits.ok
    contract = mcp_cell_contract(ds, ds.active_cell, "eval")
    assert contract.fits and contract.warnings == []
    assert use.use(ds, "eval").used_at is not None


def test_a_version_that_does_not_fit_suggests_a_new_version(capability):
    ds = make_dataset(capability, intent="train")
    ds.intent = "eval"
    ds.save(update_fields=["intent"])
    [action] = serialize_dataset_detail(ds).next_actions
    assert action.tool == "add_dataset_version"
    assert action.arguments == {"dataset": str(ds.id), "parent": "v0"}


def test_data_can_start_model_preprocessing_without_a_use(capability, settings):
    settings.FINETUNING_BACKEND = "modal"
    ds = make_dataset(capability, intent="train")
    prep = request_preparation(ds.active_cell, "Qwen/Qwen3-8B", 4096)
    assert prep.state == "queued" and ds.active_cell.used_at is None


def test_unbound_data_can_be_used_and_warns_about_the_capability(capability):
    ds = make_dataset(capability, intent="train")
    ds.capability = None
    ds.save(update_fields=["capability"])
    assert use.use(ds, "train").used_at is not None
    assert review.warnings(ds, ds.active_cell, capability=capability) == [
        "This dataset is not attached to Screener."
    ]


def test_training_create_freezes_the_pair_atomically(capability):
    train = make_dataset(capability, intent="train")
    evaluation = make_dataset(capability)
    user = User.objects.create_user(email="validation@example.com", password="test")
    ProjectMembership.objects.create(user=user, project=capability.project)
    evaluator = Evaluator.objects.create(
        project=capability.project,
        name="Match",
        kind="deterministic",
        config={"check": "exact_match"},
    )
    eval_set = EvalSet.objects.create(project=capability.project, name="Screening")
    EvalSetMember.objects.create(eval_set=eval_set, evaluator=evaluator, role="generative")

    payload = {
        "project": str(capability.project_id),
        "dataset": str(train.id),
        "capability": str(capability.id),
        "eval_dataset": str(evaluation.id),
        "eval_set": str(eval_set.id),
        "base_model": "Qwen/Qwen3-8B",
        "name": "Screening",
        "validation_enabled": False,
    }
    serializer = FinetuningJobSerializer(
        data=payload, context={"request": SimpleNamespace(user=user)}
    )
    assert serializer.is_valid(), serializer.errors
    assert train.active_cell.used_at is None and evaluation.active_cell.used_at is None
    assert not FinetuningJob.objects.exists()
    eval_cell = evaluation.active_cell
    fingerprint = eval_cell.fingerprint
    eval_cell.fingerprint = "changed"
    eval_cell.save(update_fields=["fingerprint"])
    with pytest.raises(ValidationError, match="changed"):
        serializer.save(triggered_by=user)
    train.refresh_from_db()
    assert train.active_cell.used_at is None
    eval_cell.fingerprint = fingerprint
    eval_cell.save(update_fields=["fingerprint"])
    serializer = FinetuningJobSerializer(
        data=payload, context={"request": SimpleNamespace(user=user)}
    )
    assert serializer.is_valid(), serializer.errors
    job = serializer.save(triggered_by=user)
    assert job.eval_cell_id == evaluation.active_cell.id
    assert job.cell.used_at and job.eval_cell.used_at
    replacement = make_dataset(capability, case_id="new-eval")
    update = FinetuningJobSerializer(job, data={"eval_dataset": str(replacement.id)}, partial=True)
    assert not update.is_valid() and "eval_dataset" in update.errors
    assert replacement.active_cell.used_at is None


def test_empty_scanned_output_schema_does_not_require_json(capability, frame_of):
    capability.improvement_metadata["capability_card"]["output_schema"] = {
        "required_keys": [],
        "properties": {},
        "provenance": [],
    }
    frame = frame_of(
        [
            {
                "input": {"onboarding_id": "case", "documents": ["evidence"]},
                "expected_output": "Escalate",
            }
        ]
    )
    assert alignment.capability_contract(capability, frame, "eval")["ok"]


def test_eval_rejects_worker_system_prompt_even_with_complete_evidence(capability, frame_of):
    frame = frame_of(
        [
            {
                "input": {
                    "onboarding_id": "case",
                    "documents": ["evidence"],
                    "system_prompt": "Extract documents",
                },
                "expected_output": {"escalate": True, "reason": "Expired"},
            }
        ]
    )
    assert not alignment.capability_contract(capability, frame, "eval")["ok"]
