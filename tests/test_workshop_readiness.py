import json
from types import SimpleNamespace

import pandas as pd
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
from overbae.services.datasets.context import context_fingerprint
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


def complete_review(dataset, *, failed=None, unknown=None):
    cell = dataset.active_cell
    return review.record_quality(
        dataset,
        cell,
        [
            {
                "name": name,
                "result": "fail" if name == failed else "unknown" if name == unknown else "pass",
                "rows_checked": cell.rows,
                "evidence": "Controlled fixture: evidence and target checked.",
            }
            for name in review.REQUIRED_CHECKS
        ],
        script="df = pd.DataFrame("
        + repr(
            {
                name: [False if name == failed else None if name == unknown else True] * cell.rows
                for name in review.REQUIRED_CHECKS
            }
        )
        + ")",
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


def test_id_only_eval_and_invalid_final_packet_fail_declared_schema(capability):
    ds = make_dataset(capability, evidence=False)
    assert ds.active_cell.fits("eval")[0]
    assert not ds.active_cell.capability_report["ok"]
    assert "documents" in "; ".join(review.warnings(ds, ds.active_cell))
    assert use.use(ds, "eval").used_at is not None
    frame = pd.DataFrame(
        [{"input": {"onboarding_id": "a", "documents": []}, "expected_output": {"escalate": "yes"}}]
    )
    report = alignment.capability_contract(capability, frame, "eval")
    assert not report["ok"]
    assert report["rows_ok"] == 0
    assert "input violates" in report["reason"] and "expected_output violates" in report["reason"]


def test_worker_prompt_reports_capability_mismatch(capability):
    frame = pd.DataFrame(
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


@pytest.mark.parametrize("failure", [None, "input_evidence", "answer_support"])
def test_missing_or_failed_review_warns_without_blocking_use(capability, failure):
    ds = make_dataset(capability)
    if failure:
        complete_review(ds, failed=failure)
    assert review.warnings(ds, ds.active_cell)
    assert use.use(ds, "eval").used_at is not None
    detail = serialize_dataset_detail(ds)
    assert detail.next_actions[0].tool == "message_dataset_agent"
    assert any(action.tool == "check_evaluation_readiness" for action in detail.next_actions)
    assert not detail.cells[0].readiness["quality_passed"]
    contract = mcp_cell_contract(ds, ds.active_cell, "eval")
    assert contract.fits and contract.warnings


def test_sampled_or_unknown_answer_support_cannot_pass(capability):
    ds = make_dataset(capability)
    with pytest.raises(ValueError, match="every original row"):
        review.record_quality(
            ds,
            ds.active_cell,
            [
                {
                    "name": "answer_support",
                    "result": "pass",
                    "evidence": "Sample only",
                    "rows_checked": 0,
                }
            ],
            script="df = pd.DataFrame({'answer_support': []})",
        )
    complete_review(ds, unknown="answer_support")
    assert "Answer support: unknown" in "; ".join(review.warnings(ds, ds.active_cell))
    assert use.use(ds, "eval").used_at is not None


def test_context_changes_make_review_stale_without_blocking_use(capability):
    ds = make_dataset(capability)
    complete_review(ds)
    assert review.readiness(ds, ds.active_cell)["quality_passed"]
    assert use.check(ds, "eval").used_at is None
    assert use.use(ds, "eval").used_at is not None
    before = context_fingerprint(capability)
    capability.improvement_metadata["capability_card"]["output_schema"]["required_keys"].append(
        "evidence"
    )
    capability.save(update_fields=["improvement_metadata"])
    ds.refresh_from_db()
    assert context_fingerprint(capability) != before
    assert not review.readiness(ds, ds.active_cell)["quality_passed"]
    assert review.warnings(ds, ds.active_cell)
    assert use.use(ds, "eval").used_at is not None


def test_unreviewed_data_can_start_model_preprocessing(capability, settings):
    settings.FINETUNING_BACKEND = "modal"
    ds = make_dataset(capability, intent="train")
    prep = request_preparation(ds.active_cell, "Qwen/Qwen3-8B", 4096)
    assert prep.state == "queued" and ds.active_cell.used_at is None


def test_unbound_data_still_uses_dataset_quality_contract(capability):
    ds = make_dataset(capability, intent="train")
    ds.capability = None
    ds.save(update_fields=["capability"])
    assert use.use(ds, "train").used_at is not None
    assert "not been reviewed for Screener" in "; ".join(
        review.warnings(ds, ds.active_cell, capability=capability)
    )


def test_training_create_allows_warnings_and_freezes_pair_atomically(capability):
    train = make_dataset(capability, intent="train")
    evaluation = make_dataset(capability)
    complete_review(train)
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
    assert review.warnings(evaluation, job.eval_cell)
    replacement = make_dataset(capability, case_id="new-eval")
    complete_review(replacement)
    update = FinetuningJobSerializer(job, data={"eval_dataset": str(replacement.id)}, partial=True)
    assert not update.is_valid() and "eval_dataset" in update.errors
    assert replacement.active_cell.used_at is None


def test_empty_scanned_output_schema_does_not_require_json(capability):
    capability.improvement_metadata["capability_card"]["output_schema"] = {
        "required_keys": [],
        "properties": {},
        "provenance": [],
    }
    frame = pd.DataFrame(
        [
            {
                "input": {"onboarding_id": "case", "documents": ["evidence"]},
                "expected_output": "Escalate",
            }
        ]
    )
    assert alignment.capability_contract(capability, frame, "eval")["ok"]


def test_eval_rejects_worker_system_prompt_even_with_complete_evidence(capability):
    frame = pd.DataFrame(
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
