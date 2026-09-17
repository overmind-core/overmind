from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from conftest import EVAL_ROWS, frozen_dataset

from overbae.models import (
    APIToken,
    Capability,
    Cell,
    Dataset,
    EvalSet,
    OptimizerCandidate,
    OptimizerExperiment,
    OptimizerIteration,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.mcp import tools_optimizer
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext, bind_context
from overbae.services.mcp.contracts.optimizer import InspectOptimizerResultOutput
from overbae.services.mcp.resources import read_resource

pytestmark = pytest.mark.django_db(transaction=True)


def _context(*, permission: str | list[str] = "read") -> MCPContext:
    user = User.objects.create_user(
        email=f"mcp-optimizer-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="Optimizer", slug=f"optimizer-{uuid.uuid4().hex[:8]}")
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


def _ready_objects(context: MCPContext):
    capability = Capability.objects.create(
        project=context.project, name="Support", slug=f"support-{uuid.uuid4().hex[:6]}"
    )
    dataset = frozen_dataset(
        context.project, EVAL_ROWS, capability=capability, name="Eval", contract="eval"
    )
    eval_set = EvalSet.objects.create(
        project=context.project, capability=capability, name="Default eval"
    )
    capability.active_eval_set = eval_set
    capability.save(update_fields=["active_eval_set"])
    return capability, dataset, eval_set


def _experiment(context: MCPContext, *, status: str | None = None) -> OptimizerExperiment:
    capability, dataset, eval_set = _ready_objects(context)
    return OptimizerExperiment.objects.create(
        project=context.project,
        capability=capability,
        dataset=dataset,
        eval_set=eval_set,
        status=status or OptimizerExperiment.Status.SCHEDULED,
    )


def test_catalog_adds_exactly_three_public_optimizer_tools_without_ledger_tools():
    names = {definition.name for definition in CATALOG.definitions()}
    assert {
        "check_optimizer_readiness",
        "start_optimizer",
        "inspect_optimizer_result",
    } <= names
    assert "create_optimizer_pr" not in names
    assert not names & {
        "add_optimizer_iteration",
        "post_optimizer_results",
        "evaluate_optimizer_iteration",
        "complete_optimizer_experiment",
    }


def test_read_only_key_hides_and_denies_optimizer_writes():
    context = _context()
    visible = {tool.name for tool in CATALOG.tools(frozenset({"read"}))}
    assert {"check_optimizer_readiness", "inspect_optimizer_result"} <= visible
    assert "start_optimizer" not in visible

    result = _call("start_optimizer", {"capability": "x", "dataset": "x"}, context)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "permission_denied"


def test_readiness_reports_wrong_dataset_intent_and_executioner_state():
    context = _context(permission=["read", "write"])
    capability = Capability.objects.create(
        project=context.project, name="Support", slug=f"support-{uuid.uuid4().hex[:6]}"
    )
    train = Dataset.objects.create(
        project=context.project, capability=capability, name="Train", intent=Dataset.Intent.TRAIN
    )
    result = _call(
        "check_optimizer_readiness",
        {"capability": str(capability.id), "dataset": str(train.id)},
        context,
    )

    assert result.isError is False
    output = result.structuredContent
    assert output["ready"] is False
    assert any("eval dataset" in item for item in output["missing"])
    assert output["dataset"]["usable"] is False
    assert output["executioner"]["connected"] is False


def test_start_calls_shared_create_service_and_returns_cli_next_step(monkeypatch):
    context = _context(permission=["read", "write"])
    capability, dataset, eval_set = _ready_objects(context)
    called = {}

    def fake_create(**kwargs):
        called.update(kwargs)
        return OptimizerExperiment.objects.create(
            project=context.project,
            capability=capability,
            dataset=dataset,
            cell=kwargs.get("cell") or dataset.active_cell,
            eval_set=eval_set,
            mode=kwargs["mode"],
            model_ids=kwargs["model_ids"],
            status=OptimizerExperiment.Status.SCHEDULED,
        )

    monkeypatch.setattr(tools_optimizer, "create_optimizer_experiment", fake_create)
    result = _call(
        "start_optimizer",
        {"capability": capability.slug, "dataset": str(dataset.id)},
        context,
    )

    assert result.isError is False
    output = result.structuredContent
    assert called["openrouter_key_source"] == OptimizerExperiment.OpenRouterKeySource.PLATFORM
    assert called["capability"] == capability
    assert output["job"]["id"] == output["experiment_id"]
    assert output["experiment"]["cell"]["id"] == str(dataset.active_cell.id)
    assert output["experiment"]["cell"]["rows"] == dataset.active_cell.rows
    assert output["next_action"]["state"] == "run_executioner"
    assert f"start -e {output['experiment_id']}" in output["next_action"]["command"]


def test_cross_project_experiment_is_inaccessible():
    context = _context()
    other = _context()
    experiment = _experiment(other)

    result = _call("inspect_optimizer_result", {"experiment": str(experiment.id)}, context)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "optimizer_not_found"


def test_inspect_exposes_candidate_coverage_fields():
    context = _context()
    experiment = _experiment(context, status=OptimizerExperiment.Status.COMPLETED)
    iteration = OptimizerIteration.objects.create(experiment=experiment, order=0)
    OptimizerCandidate.objects.create(
        experiment=experiment,
        iteration=iteration,
        candidate_index=0,
        score=100.0,
        scores={
            "coverage_rate": 0.95,
            "graded_rows": 19,
            "total_rows": 20,
            "coverage": {"coverage_rate": 0.95, "graded_rows": 19, "total_rows": 20},
            "measurement": {"uncovered_card_claims": ["label matches gold"]},
        },
    )

    result = _call("inspect_optimizer_result", {"experiment": str(experiment.id)}, context)

    assert result.isError is False
    output = InspectOptimizerResultOutput.model_validate(result.structuredContent)
    candidate = output.iterations[0].candidates[0]
    assert candidate.coverage_rate == pytest.approx(0.95)
    assert candidate.graded_rows == 19
    assert candidate.total_rows == 20
    assert candidate.suite_incomplete is True
    assert candidate.uncovered_card_claims == ["label matches gold"]


def test_inspect_is_bounded_typed_and_exposes_winner_state():
    context = _context()
    experiment = _experiment(context, status=OptimizerExperiment.Status.COMPLETED)
    winner = None
    for order in range(2):
        iteration = OptimizerIteration.objects.create(
            experiment=experiment, order=order, scores={"best": float(order)}
        )
        for index in range(2):
            candidate = OptimizerCandidate.objects.create(
                experiment=experiment,
                iteration=iteration,
                candidate_index=index,
                score=float(order * 10 + index),
                code_path="diff --git a/a b/a\n" if index else "",
            )
            winner = candidate if order == 1 and index == 1 else winner
    experiment.state = {"winner_candidate_id": str(winner.id)}
    experiment.save(update_fields=["state"])

    result = _call(
        "inspect_optimizer_result",
        {
            "experiment": str(experiment.id),
            "max_iterations": 1,
            "max_candidates_per_iteration": 1,
        },
        context,
    )

    assert result.isError is False
    output = InspectOptimizerResultOutput.model_validate(result.structuredContent)
    assert len(output.iterations) == 1
    assert len(output.iterations[0].candidates) == 1
    assert output.iterations_truncated is True
    assert output.winner is not None
    assert output.winner.candidate_id == str(winner.id)


def test_optimizer_resource_is_bounded_and_does_not_leak_key_values():
    context = _context()
    experiment = _experiment(context)
    experiment.state = {"openrouter_api_key": "secret-value"}
    experiment.save(update_fields=["state"])

    uri = f"overmind://optimizer-runs/{experiment.id}"

    async def read():
        with bind_context(context):
            contents = list(await read_resource(uri))
        return contents[0].content

    payload = json.loads(asyncio.run(read()))
    assert "secret-value" not in json.dumps(payload)
    assert "iterations" in payload
    assert "executioner" in payload
    assert "next_action" in payload


def test_readiness_and_start_use_explicit_eval_cell(monkeypatch):
    context = _context(permission=["read", "write"])
    capability, dataset, eval_set = _ready_objects(context)
    extra = Cell.objects.create(
        dataset=dataset,
        position=1,
        title="shaped",
        state=Cell.State.OK,
        rows=11,
        fingerprint=f"fp-opt-{uuid.uuid4().hex[:8]}",
        intent_report={"eval": {"ok": True, "reason": ""}},
        capability_report={"ok": True, "reason": ""},
    )
    called = {}

    def fake_create(**kwargs):
        called.update(kwargs)
        return OptimizerExperiment.objects.create(
            project=context.project,
            capability=capability,
            dataset=dataset,
            cell=kwargs["cell"],
            eval_set=eval_set,
            status=OptimizerExperiment.Status.SCHEDULED,
        )

    monkeypatch.setattr(tools_optimizer, "create_optimizer_experiment", fake_create)
    result = _call(
        "start_optimizer",
        {
            "capability": capability.slug,
            "dataset": str(dataset.id),
            "cell": str(extra.id),
        },
        context,
    )
    assert result.isError is False, result.structuredContent
    assert called["cell"].id == extra.id
    assert result.structuredContent["experiment"]["cell"]["id"] == str(extra.id)
    assert result.structuredContent["experiment"]["cell"]["rows"] == 11
