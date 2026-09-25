import uuid

from conftest import EVAL_ROWS, TRAIN_ROWS, frozen_dataset

from overbae.models import Capability, EvalSet, EvalSetMember, Evaluator
from overbae.services.mcp.context import MCPContext

EXPECTED_TOOL_NAMES = {
    "inspect_capability_health",
    "query_failures",
    "query_traces",
    "query_task_executions",
    "get_job",
    "list_datasets",
    "inspect_dataset",
    "query_dataset",
    "create_dataset_from_traces",
    "create_dataset_from_llm_calls",
    "message_dataset_agent",
    "run_dataset",
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
