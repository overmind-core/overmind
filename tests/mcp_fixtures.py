import uuid
from collections.abc import Sequence
from typing import Any

from conftest import EVAL_ROWS, TRAIN_ROWS, frozen_dataset
from factories import make_matrix_row, make_project, make_user

from overbae.models import APIToken, Capability, EvalMatrix, EvalSet
from overbae.services.mcp.context import MCPContext

MAX_MANIFEST_BYTES = 92 * 1024

EXPECTED_TOOL_NAMES = {
    "save_training_plan",
    "deploy_finetune",
    "inspect_native_evaluation",
    "resume_dataset_import",
    "inspect_training_progress",
    "cancel_finetune",
    "explore_dataset",
    "derive_dataset",
    "prepare_native_evaluation",
    "launch_native_evaluation",
    "pause_native_evaluation",
    "reuse_evaluation_predictions",
    "prepare_training_experiment",
    "create_training_profile",
    "list_decision_models",
    "list_decision_training_models",
    "create_data_partition",
    "retry_data_partition",
    "create_native_evaluation",
    "resume_native_evaluation",
    "create_training_experiment",
    "launch_training_experiment",
    "list_model_workflows",
    "measure_decision_performance",
    "resume_decision_performance",
    "list_projects",
    "create_api_key",
    "inspect_capability_health",
    "query_failures",
    "query_traces",
    "get_job",
    "inspect_operation",
    "list_datasets",
    "start_dataset",
    "inspect_dataset",
    "query_dataset",
    "create_dataset_from_traces",
    "create_dataset_from_llm_calls",
    "inspect_dataset_workbench",
    "save_dataset_pipeline",
    "validate_dataset_pipeline",
    "cancel_dataset_pipeline_run",
    "save_dataset_pipeline_binding",
    "set_dataset_pipeline_binding_state",
    "run_dataset_pipeline_binding",
    "run_dataset_pipeline",
    "import_dataset_version",
    "update_dataset",
    "check_evaluation_readiness",
    "create_eval_matrix_row",
    "delete_eval_matrix_row",
    "create_eval_set",
    "run_evaluation",
    "compare_evaluations",
    "check_finetune_readiness",
    "prepare_training_data",
    "estimate_finetune",
    "start_decision_job",
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
    "cancel_dataset",
    "schedule_native_evaluation",
    "list_signals",
    "set_signal_subscription",
}


def mcp_context(permission: str | Sequence[str] = "read", **fields: Any) -> MCPContext:
    user = make_user()
    project = make_project(member=user)
    token = APIToken(
        project=project,
        user=user,
        scope={
            "scope": "project",
            "resourceIds": [str(project.id)],
            "permission": [permission] if isinstance(permission, str) else list(permission),
        },
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
