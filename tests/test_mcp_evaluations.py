from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from overbae.models import (
    Annotation,
    APIToken,
    Cell,
    Dataset,
    EvalRun,
    EvalSample,
    Evaluator,
    EvalVariant,
    Project,
    ProjectMembership,
    User,
)
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
    return cell


def _dataset(context: MCPContext, name: str = "Eval") -> Dataset:
    dataset = Dataset.objects.create(
        project=context.project,
        name=name,
        intent=Dataset.Intent.EVAL,
    )
    _ok_cell(dataset, intent="eval")
    return dataset


def test_catalog_has_exactly_five_evaluation_tools_and_hides_writes():
    evaluation_names = {
        "check_evaluation_readiness",
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
