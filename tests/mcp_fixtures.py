import uuid
from collections.abc import Sequence
from typing import Any

from conftest import EVAL_ROWS, TRAIN_ROWS, frozen_dataset
from factories import make_matrix_row, make_project, make_user

from overbae.models import APIToken, Capability, EvalMatrix, EvalSet
from overbae.services.mcp.context import MCPContext

EXPECTED_TOOL_NAMES = {
    "list_projects",
    "inspect_capability_health",
    "query_failures",
    "query_traces",
    "get_job",
    "list_datasets",
    "inspect_dataset",
    "query_dataset",
    "create_dataset_from_traces",
    "create_dataset_from_llm_calls",
    "message_dataset_agent",
    "run_dataset",
    "check_evaluation_readiness",
    "create_eval_matrix_row",
    "delete_eval_matrix_row",
    "create_eval_set",
    "run_evaluation",
    "compare_evaluations",
    "check_finetune_readiness",
    "prepare_training_data",
    "estimate_finetune",
    "start_finetune",
    "retry_deployment",
    "set_active_model",
    "set_benchmark_model",
    "run_inference",
    "get_model_swap_prompt",
    "check_optimizer_readiness",
    "start_optimizer",
    "inspect_optimizer_result",
    "inspect_connectors",
    "configure_connector",
    "sync_connector",
    "get_instrumentation_plan",
    "verify_instrumentation",
    "get_model_catalog",
    "list_signals",
    "set_signal_subscription",
}


def mcp_context(permission: str | Sequence[str] = "read", **fields: Any) -> MCPContext:
    user = make_user()
    project = make_project(member=user)
    token = APIToken(
        scope={
            "scope": "project",
            "resourceIds": [str(project.id)],
            "permission": [permission] if isinstance(permission, str) else list(permission),
        }
    )
    return MCPContext(user=user, token=token, project=project, **fields)


def training_setup(context: MCPContext):
    capability = Capability.objects.create(
        project=context.project,
        name="Support",
        slug=f"support-{uuid.uuid4().hex[:6]}",
        model="openai/gpt-5.6-sol",
    )
    train = frozen_dataset(
        context.project, TRAIN_ROWS, name="Train", contract="train", capability=capability
    )
    evaluation = frozen_dataset(
        context.project,
        [{**row, "input": "held-out-" + row["input"]} for row in EVAL_ROWS],
        name="Eval",
        contract="eval",
        capability=capability,
    )
    eval_set = EvalSet.objects.create(
        project=context.project,
        capability=capability,
        name="Default evals",
    )
    eval_set.rows.add(
        make_matrix_row(
            context.project,
            "Exact match",
            type=EvalMatrix.Type.DETERMINISTIC,
            source="def evaluate(span):\n    return span.output == span.expected\n",
        )
    )
    return capability, train, evaluation, eval_set
