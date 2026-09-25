from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from conftest import review_fixture
from mcp.shared.exceptions import McpError

from overbae.models import (
    Annotation,
    APIToken,
    Capability,
    Cell,
    Dataset,
    EvalRun,
    EvalSample,
    EvalSet,
    EvalSetMember,
    Evaluator,
    EvalVariant,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.datasets import paths, store
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext, bind_context
from overbae.services.mcp.resources import read_resource

pytestmark = pytest.mark.django_db(transaction=True)


def _context(*, permission: str | list[str] = "read") -> MCPContext:
    user = User.objects.create_user(
        email=f"mcp-eval-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="Evaluations", slug=f"eval-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    permissions = [permission] if isinstance(permission, str) else permission
    token = APIToken(
        scope={
            "scope": "project",
            "resourceIds": [str(project.id)],
            "permission": permissions,
        }
    )
    return MCPContext(user=user, token=token, project=project)


def _call(name: str, arguments: dict, context: MCPContext):
    return asyncio.run(CATALOG.call(name, arguments, context))


def _ok_cell(dataset, *, intent="eval", rows=2, title="source", position=0, active=True, fits=True):
    cell = Cell.objects.create(
        dataset=dataset,
        position=position,
        title=title,
        state=Cell.State.OK,
        rows=rows,
        fingerprint=f"fp-{position}-{uuid.uuid4().hex[:8]}",
        intent_report={intent: {"ok": fits, "reason": "" if fits else "not a fit"}},
        capability_report={"ok": True, "reason": ""},
    )
    if active:
        dataset.active = cell
        dataset.save(update_fields=["active"])
    path = paths.cell_path(dataset.id, cell.id)
    store.write_rows(
        path,
        [{"source_row": i, "input": f"q{i}", "expected_output": f"a{i}"} for i in range(rows)],
    )
    cell.fingerprint = store.file_sha256(path)
    cell.save(update_fields=["fingerprint"])
    review_fixture(dataset, cell)
    return cell


def _dataset(context: MCPContext, name: str = "Eval") -> Dataset:
    dataset = Dataset.objects.create(
        project=context.project,
        name=name,
        intent=Dataset.Intent.EVAL,
    )
    _ok_cell(dataset, intent="eval")
    return dataset


def test_readiness_exposes_advisory_context_without_a_new_readiness_gate(monkeypatch):
    from overbae.services.mcp import tools_evaluations

    context = _context()
    dataset = _dataset(context)
    eval_set = EvalSet.objects.create(project=context.project, name="Context checks")
    evaluator = Evaluator.objects.create(
        project=context.project,
        name="Match",
        kind="deterministic",
        config={"check": "exact_match"},
        applicable_roles=["generative"],
    )
    EvalSetMember.objects.create(eval_set=eval_set, evaluator=evaluator, role="generative")
    warning = {
        "role": "generation",
        "label": "Candidate",
        "model": "gpt-4.1",
        "context_window": 1000,
        "max_output_tokens": 5000,
        "estimated_input_tokens": 2000,
        "reserved_output_tokens": 5000,
        "required_context": 7000,
        "checked_rows": 2,
        "affected_rows": 2,
        "row_indices": [0, 1],
        "estimated": True,
        "status": "warning",
        "message": "Context may be too small.",
    }
    monkeypatch.setattr(tools_evaluations, "check_context", lambda **kwargs: [warning])
    result = _call(
        "check_evaluation_readiness",
        {
            "dataset": str(dataset.pk),
            "eval_set": str(eval_set.pk),
            "mode": "generate",
            "variants": [{"mode": "generate", "label": "Candidate", "model_name": "gpt-4.1"}],
        },
        context,
    )
    assert not result.isError
    assert result.structuredContent["context_checks"] == [
        {
            **warning,
            "total_input_tokens": 0,
            "configured_model": "",
            "estimated_cost_usd": None,
            "cost_basis": "",
            "suggestions": [],
            "suggestion_note": "",
        }
    ]
    assert result.structuredContent["ready"] is True


def test_catalog_exposes_evaluation_tools_and_hides_writes():
    evaluation_names = {
        "check_evaluation_readiness",
        "create_eval_set",
        "upsert_evaluator",
        "run_evaluation",
        "compare_evaluations",
        "annotate_evaluation_sample",
    }
    assert evaluation_names <= {definition.name for definition in CATALOG.definitions()}
    assert evaluation_names - {"check_evaluation_readiness", "compare_evaluations"} == {
        tool.name for tool in CATALOG.tools(frozenset({"write"})) if tool.name in evaluation_names
    }
    assert {tool.name for tool in CATALOG.tools(frozenset({"read"}))} >= {
        "check_evaluation_readiness",
        "compare_evaluations",
    }

    result = _call("upsert_evaluator", {"name": "Hidden", "rubric_md": "x"}, _context())
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "permission_denied"


@pytest.mark.parametrize("with_capability", [True, False])
def test_create_eval_set_returns_readable_members_and_enforces_project_scope(with_capability):
    context = _context(permission=["read", "write"])
    capability = Capability.objects.create(project=context.project, name="Support", slug="support")
    evaluator = Evaluator.objects.create(
        project=context.project, name="Accuracy", kind="deterministic"
    )
    payload = {"name": "Quality", "evaluator_ids": [str(evaluator.id)]}
    if with_capability:
        payload["capability"] = str(capability.id)
    result = _call("create_eval_set", payload, context)
    assert result.isError is False, result.structuredContent
    assert json.loads(result.content[0].text) == result.structuredContent
    uri = result.structuredContent["resource_links"][0]["uri"]
    with bind_context(context):
        resource = json.loads(asyncio.run(read_resource(uri))[0].content)
    assert resource["members"][0]["name"] == "Accuracy"
    assert resource["members"][0]["capability"] is None
    assert resource["is_active"] is False
    other = _context(permission=["read", "write"])
    denied = _call("create_eval_set", payload, other)
    assert denied.isError is True
    with bind_context(other), pytest.raises(McpError):
        asyncio.run(read_resource(uri))
    assert EvalSet.objects.filter(name="Quality").count() == 1
    payload["name"] = "Rejected"
    payload["evaluator_ids"] = [
        str(Evaluator.objects.create(project=other.project, name="Other").id)
    ]
    assert _call("create_eval_set", payload, context).isError is True
    assert not EvalSet.objects.filter(name="Rejected").exists()


def test_create_eval_set_is_not_available_to_read_only_keys():
    result = _call(
        "create_eval_set", {"name": "Quality", "evaluator_ids": [str(uuid.uuid4())]}, _context()
    )
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "permission_denied"


def test_readiness_rejects_non_eval_dataset_clearly():
    context = _context(permission=["read", "write"])
    dataset = Dataset.objects.create(
        project=context.project,
        name="Train",
        intent=Dataset.Intent.TRAIN,
    )

    result = _call("check_evaluation_readiness", {"dataset": str(dataset.id)}, context)

    assert result.isError is True
    error = result.structuredContent["error"]
    assert error["code"] == "dataset_intent_mismatch"
    assert "eval dataset" in error["message"]


def test_upsert_uses_evaluator_spec_validation_and_sanitizes_text():
    context = _context(permission=["read", "write"])
    invalid = _call(
        "upsert_evaluator",
        {
            "name": "Bad regex",
            "kind": "deterministic",
            "config": {"check": "regex", "pattern": "["},
        },
        context,
    )
    assert invalid.isError is True
    assert invalid.structuredContent["error"]["code"] == "evaluator_invalid"
    assert not Evaluator.objects.filter(project=context.project, name="Bad regex").exists()

    created = _call(
        "upsert_evaluator",
        {
            "name": "Helpful",
            "kind": "llm_judge",
            "rubric_md": "Judge the answer and compare {_answer} to _SECRET_VALUE.",
        },
        context,
    )
    assert created.isError is False
    evaluator = Evaluator.objects.get(id=created.structuredContent["id"])
    assert "_SECRET_VALUE" not in evaluator.rubric_md
    assert "{_answer}" not in evaluator.rubric_md

    updated = _call(
        "upsert_evaluator",
        {"evaluator": str(evaluator.id), "description": "Updated description"},
        context,
    )
    assert updated.isError is False
    assert updated.structuredContent["status"] == "updated"
    evaluator.refresh_from_db()
    assert evaluator.description == "Updated description"


def test_run_uses_existing_serializer_and_task(monkeypatch):
    context = _context(permission=["read", "write"])
    dataset = _dataset(context)
    evaluator = Evaluator.objects.create(
        project=context.project,
        name="Exact match",
        kind=Evaluator.Kind.DETERMINISTIC,
        config={"check": "exact_match"},
    )
    calls: dict[str, object] = {}
    monkeypatch.setattr("overbae.api.credit_gate.require_credits", lambda _user: None)
    monkeypatch.setattr(
        "overbae.tasks.eval.run_eval_run.apply_async",
        lambda **kwargs: calls.update(kwargs=kwargs) or SimpleNamespace(id="celery-eval"),
    )

    result = _call(
        "run_evaluation",
        {
            "name": "First run",
            "dataset": str(dataset.id),
            "evaluator_ids": [str(evaluator.id)],
            "variants": [{"mode": "existing"}],
        },
        context,
    )

    assert result.isError is False, result.structuredContent
    run = EvalRun.objects.get(id=result.structuredContent["run_id"])
    assert run.dataset_id == dataset.id
    assert run.cell_id == dataset.active_cell.id
    assert result.structuredContent["cell"]["id"] == str(run.cell_id)
    assert result.structuredContent["cell"]["rows"] == dataset.active_cell.rows
    assert calls["kwargs"] == {"kwargs": {"eval_run_id": str(run.id)}}
    assert result.structuredContent["job"]["resource"]["uri"].startswith(
        "overmind://jobs/eval_run/"
    )


@pytest.mark.parametrize("selection", ["", "gpt-5.6-luna"])
def test_run_judge_override_is_frozen_readable_and_project_scoped(monkeypatch, selection):
    context = _context(permission=["read", "write"])
    dataset = _dataset(context)
    evaluator = Evaluator.objects.create(
        project=context.project,
        name="Quality",
        kind="llm_judge",
        judge_model="gpt-4.1",
        checklist=[{"id": "correct", "q": "Is the answer correct?"}],
    )
    monkeypatch.setattr("overbae.api.credit_gate.require_credits", lambda _user: None)
    monkeypatch.setattr(
        "overbae.tasks.eval.run_eval_run.apply_async", lambda **kwargs: SimpleNamespace(id="test")
    )
    payload = {
        "name": "Override",
        "dataset": str(dataset.pk),
        "evaluator_ids": [str(evaluator.pk)],
        "variants": [{"mode": "existing"}],
        "judge_model": selection,
    }
    result = _call("run_evaluation", payload, context)
    assert not result.isError, result.structuredContent
    assert json.loads(result.content[0].text) == result.structuredContent
    run = EvalRun.objects.get(pk=result.structuredContent["run_id"])
    assert run.judge_model == selection
    assert run.run_evaluators.get().snapshot["judge_model"] == (selection or "gpt-4.1")
    evaluator.refresh_from_db()
    assert evaluator.judge_model == "gpt-4.1"
    uri = result.structuredContent["resource"]["uri"]
    with bind_context(context):
        data = json.loads(asyncio.run(read_resource(uri))[0].content)
    assert data["judge_model"] == selection
    assert data["run_evaluators"][0]["judge_model"] == (selection or "gpt-4.1")
    other = _context(permission=["read", "write"])
    assert _call("run_evaluation", payload, other).isError
    with bind_context(other), pytest.raises(McpError):
        asyncio.run(read_resource(uri))
    assert EvalRun.objects.count() == 1
    assert _call("run_evaluation", {**payload, "judge_model": "invalid"}, context).isError
    assert EvalRun.objects.count() == 1


def test_compare_is_typed_and_run_resource_has_progress():
    context = _context(permission="read")
    dataset = _dataset(context)
    baseline = EvalRun.objects.create(
        project=context.project,
        name="Baseline",
        dataset=dataset,
        status=EvalRun.Status.COMPLETED,
        summary={
            "metrics": ["quality"],
            "variants": {"base": {"metrics": {"quality": {"mean": 0.5, "n": 1}}}},
        },
    )
    current = EvalRun.objects.create(
        project=context.project,
        name="Current",
        dataset=dataset,
        status=EvalRun.Status.COMPLETED,
        summary={
            "metrics": ["quality"],
            "variants": {"current": {"metrics": {"quality": {"mean": 0.8, "n": 1}}}},
        },
    )

    result = _call(
        "compare_evaluations",
        {"run": str(current.id), "baseline": str(baseline.id)},
        context,
    )
    assert result.isError is False, result.structuredContent
    assert result.structuredContent["overall"]["status"] == "improved"
    assert result.structuredContent["trust"]["current"]["trusted"] is True

    async def read():
        with bind_context(context):
            content = await read_resource(f"overmind://eval-runs/{current.id}")
        return json.loads(content[0].content)

    resource = asyncio.run(read())
    assert resource["progress"]["phase"] == EvalRun.Status.COMPLETED
    assert resource["sample_count"] == 0


def test_evaluation_references_are_project_scoped_and_annotation_uses_user(monkeypatch):
    context = _context(permission=["read", "write"])
    other_context = _context(permission=["read", "write"])
    other_dataset = _dataset(other_context, "Other dataset")
    other_evaluator = Evaluator.objects.create(
        project=other_context.project,
        name="Other evaluator",
        kind=Evaluator.Kind.DETERMINISTIC,
        config={"check": "exact_match"},
    )
    other_run = EvalRun.objects.create(project=other_context.project, name="Other run")
    other_variant = EvalVariant.objects.create(run=other_run, label="Existing")
    other_sample = EvalSample.objects.create(run=other_run, variant=other_variant)

    assert (
        _call(
            "check_evaluation_readiness", {"dataset": str(other_dataset.id)}, context
        ).structuredContent["error"]["code"]
        == "dataset_not_found"
    )
    assert (
        _call(
            "upsert_evaluator", {"evaluator": str(other_evaluator.id)}, context
        ).structuredContent["error"]["code"]
        == "evaluator_not_found"
    )
    assert (
        _call(
            "compare_evaluations",
            {"run": str(other_run.id), "baseline": str(other_run.id)},
            context,
        ).structuredContent["error"]["code"]
        == "eval_run_not_found"
    )

    annotation = _call(
        "annotate_evaluation_sample",
        {"sample": str(other_sample.id), "value": 1},
        context,
    )
    assert annotation.structuredContent["error"]["code"] == "eval_sample_not_found"


def test_annotation_is_attributed_to_authenticated_user(monkeypatch):
    context = _context(permission=["read", "write"])
    run = EvalRun.objects.create(project=context.project, name="Run")
    variant = EvalVariant.objects.create(run=run, label="Existing")
    sample = EvalSample.objects.create(run=run, variant=variant)

    result = _call(
        "annotate_evaluation_sample",
        {"sample": str(sample.id), "label": "pass", "note": "checked"},
        context,
    )

    assert result.isError is False
    annotation = Annotation.objects.get(id=result.structuredContent["id"])
    assert annotation.user_id == context.user.id
    assert annotation.project_id == context.project.id


def test_run_rejects_nonfitting_eval_cell_and_records_explicit_cell(monkeypatch):
    context = _context(permission=["read", "write"])
    dataset = Dataset.objects.create(
        project=context.project, name="Eval", intent=Dataset.Intent.EVAL
    )
    _ok_cell(dataset, intent="eval", rows=2, fits=False)
    result = _call(
        "run_evaluation",
        {"name": "Bad", "dataset": str(dataset.id), "evaluator_ids": []},
        context,
    )
    assert result.isError is True
    assert result.structuredContent["error"]["code"] in {
        "dataset_invalid",
        "dataset_intent_mismatch",
    }

    dataset = Dataset.objects.create(
        project=context.project, name="Eval 2", intent=Dataset.Intent.EVAL
    )
    active = _ok_cell(dataset, intent="eval", rows=2)
    extra = _ok_cell(dataset, intent="eval", rows=11, title="shaped", position=1, active=False)
    evaluator = Evaluator.objects.create(
        project=context.project,
        name="Exact match",
        kind=Evaluator.Kind.DETERMINISTIC,
        config={"check": "exact_match"},
    )
    monkeypatch.setattr("overbae.api.credit_gate.require_credits", lambda _user: None)
    monkeypatch.setattr(
        "overbae.tasks.eval.run_eval_run.apply_async",
        lambda **_kwargs: SimpleNamespace(id="celery-eval"),
    )
    result = _call(
        "run_evaluation",
        {
            "name": "Explicit",
            "dataset": str(dataset.id),
            "evaluator_ids": [str(evaluator.id)],
            "cell": str(extra.id),
            "variants": [{"mode": "existing"}],
        },
        context,
    )
    assert result.isError is False, result.structuredContent
    run = EvalRun.objects.get(id=result.structuredContent["run_id"])
    assert run.cell_id == extra.id
    assert run.cell_id != active.id
    extra.refresh_from_db()
    assert extra.used_at is not None
