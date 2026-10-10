import uuid

from conftest import EVAL_ROWS, TRAIN_ROWS, frozen_dataset

from overbae.models import Capability, EvalSet, EvalSetMember, Evaluator
from overbae.services.mcp.context import MCPContext

MAX_MANIFEST_BYTES = 80 * 1024

EXPECTED_TOOL_NAMES = {
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
    "inspect_capability_health",
    "query_failures",
    "query_traces",
    "query_task_executions",
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
    "upsert_evaluator",
    "create_eval_set",
    "run_evaluation",
    "compare_evaluations",
    "annotate_evaluation_sample",
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
    "cancel_dataset",
    "schedule_native_evaluation",
}


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
    evaluator = Evaluator.objects.create(
        project=context.project,
        name="Exact match",
        kind=Evaluator.Kind.DETERMINISTIC,
        config={"check": "exact_match"},
    )
    EvalSetMember.objects.create(
        eval_set=eval_set,
        evaluator=evaluator,
        role=EvalSetMember.Role.GENERATIVE,
    )
    return capability, train, evaluation, eval_set
