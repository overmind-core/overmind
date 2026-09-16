"""MCP-native workflow prompts grounded in the public MCP catalog."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from mcp import types
from mcp.shared.exceptions import McpError


@dataclass(frozen=True, slots=True)
class PromptDefinition:
    name: str
    title: str
    description: str
    arguments: tuple[types.PromptArgument, ...]
    template: str

    def as_mcp_prompt(self) -> types.Prompt:
        return types.Prompt(
            name=self.name,
            title=self.title,
            description=self.description,
            arguments=list(self.arguments),
        )


def _argument(name: str, description: str) -> types.PromptArgument:
    return types.PromptArgument(name=name, description=description, required=True)


def _optional_argument(name: str, description: str) -> types.PromptArgument:
    return types.PromptArgument(name=name, description=description, required=False)


PROMPTS = (
    PromptDefinition(
        name="investigate-capability",
        title="Investigate capability",
        description="Gather bounded evidence about a capability's health, failures, traces, and instrumentation.",
        arguments=(_argument("capability", "Capability name, slug, or id."),),
        template=(
            "Investigate capability {capability} in the authenticated project. "
            "Use inspect_capability_health, query_failures, query_traces, "
            "query_task_executions, and get_instrumentation_plan only as needed. "
            "Read overmind://capabilities/{capability} and any returned "
            "overmind://traces/{{trace_id}} or overmind://sessions/{{session}} resource. "
            "Return evidence, current health, instrumentation gaps, and the checkpoint "
            "that determines whether to build data, prepare an evaluation, or stop."
        ),
    ),
    PromptDefinition(
        name="instrument-repository",
        title="Instrument repository",
        description="Turn the registered instrumentation plan into a project- or capability-scoped repository change and verify supplied spans.",
        arguments=(_optional_argument("capability", "Optional capability name, slug, or id."),),
        template=(
            "Instrument this repository project-wide when the capability filter below is blank; "
            'otherwise scope the work to it. Capability filter: "{capability}". Start with '
            "get_instrumentation_plan "
            "and translate every placement into a small human-applied code change; preserve each "
            "capability_id, behaviour key, required scope, required spans, identity, grain, and "
            "target verbatim. The MCP server does not edit the repository or ingest traces. "
            "After applying the changes, report the tickets and any local checks, then ask them to choose one explicit "
            "verification mode: Real run (recommended), which runs the approved workflow and may "
            "use normal provider, search, or application resources, or Smoke run, which is bounded "
            "and may exercise fewer branches. Do not run either mode before approval. For the "
            "approved run, generate a unique correlation value, flush spans, query_traces with the "
            "narrowest correlation and all_spans=true, and require exactly one matching trace after "
            "bounded polling. Read overmind://traces/{{trace_id}} and call verify_instrumentation with "
            "the complete server-supplied span list. A missing or multiple trace match, truncated "
            "resource, or span list over the verifier limit is not a pass. Report application outcome "
            "separately from instrumentation status; a real-run retry needs fresh approval unless "
            "the approval explicitly names a bounded retry count and exact input."
        ),
    ),
    PromptDefinition(
        name="prepare-evaluation",
        title="Prepare evaluation",
        description="Check dataset, evaluator, eval-set, binding, and credit readiness before an evaluation run.",
        arguments=(
            _argument("dataset", "Evaluation dataset UUID from list_datasets."),
            _argument("eval_set", "Evaluation set name or id."),
        ),
        template=(
            "Prepare an evaluation for dataset {dataset} using eval set {eval_set}. "
            "Resolve it with list_datasets and pass its UUID to inspect_dataset. Use "
            "message_dataset_agent for intent, capability, name, or cell changes; poll with "
            "get_job(kind=dataset_run), inspect again, and use query_dataset to verify the "
            "chosen cell. Run check_evaluation_readiness with that dataset and cell. "
            "If readiness identifies missing evaluator configuration, collect the human "
            "rubric and use upsert_evaluator. Do not start a run until dataset intent, "
            "evaluator applicability and bindings, eval set, and credits are ready. "
            "Return the exact next checkpoint; if a prerequisite has no current MCP tool, "
            "name the human action."
        ),
    ),
    PromptDefinition(
        name="evaluate-change",
        title="Evaluate change",
        description="Run an evaluation and compare its measured result with a supplied baseline.",
        arguments=(
            _argument("dataset", "Evaluation dataset UUID from list_datasets."),
            _argument("baseline", "Baseline evaluation run name or id."),
        ),
        template=(
            "Evaluate a change using dataset {dataset} against baseline {baseline}. "
            "Use list_datasets, inspect_dataset, and query_dataset to choose and verify a "
            "fitting eval cell. Run check_evaluation_readiness with that dataset and cell, "
            "then run_evaluation only when ready. Track "
            "the run with get_job using kind eval_run and read "
            "overmind://eval-runs/{{eval_run}}. When complete, use compare_evaluations with "
            "the baseline; report overall and evaluator deltas plus trust flags. Use "
            "annotate_evaluation_sample only for explicit human labels. End with an "
            "improved, regressed, or unchanged checkpoint."
        ),
    ),
    PromptDefinition(
        name="finetune-capability",
        title="Fine-tune capability",
        description="Check, estimate, and launch a validated fine-tuning job for a capability.",
        arguments=(
            _argument("capability", "Capability name, slug, or id."),
            _argument("dataset", "Training dataset UUID from list_datasets."),
        ),
        template=(
            "Fine-tune capability {capability} from dataset {dataset}. Use list_datasets, "
            "inspect_dataset, and query_dataset to choose and verify a train cell. "
            "Begin with get_model_catalog for dataset-independent model discovery. "
            "After the dataset, cell, and capability are selected, run "
            "check_finetune_readiness for dataset-specific "
            "narrowing and recommendations; use estimate_finetune for approved base models "
            "before asking a human to approve GPU spend. After approval, use start_finetune "
            "with a validated base model, then "
            "track each job with get_job using kind finetune_job and "
            "overmind://finetunes/{{job_id}}. Follow the linked deployment with "
            "get_job using kind deployment, then run_inference only when it is ready; "
            "leave model activation and "
            "repository rollout to ship-model."
        ),
    ),
    PromptDefinition(
        name="optimize-capability",
        title="Optimize capability",
        description="Check readiness, schedule, and inspect a capability optimization experiment.",
        arguments=(
            _argument("capability", "Capability name, slug, or id."),
            _argument("dataset", "Evaluation dataset UUID from list_datasets."),
        ),
        template=(
            "Optimize capability {capability} using evaluation dataset {dataset}. Use "
            "list_datasets, inspect_dataset, and query_dataset to choose and verify an "
            "eval cell. Run check_optimizer_readiness with that dataset and cell, mode "
            "optimize, and inspect missing prerequisites "
            "before start_optimizer. Start only after readiness and human approval; the MCP "
            "server schedules metadata while a connected local executioner runs the "
            "experiment. Inspect progress with inspect_optimizer_result and "
            "overmind://optimizer-runs/{{experiment}}. When a completed winner is approved for "
            "landing, present its diff and hand the change to a human to apply locally."
        ),
    ),
    PromptDefinition(
        name="compare-models",
        title="Compare models",
        description="Schedule a bounded model-comparison experiment and report its measured winner.",
        arguments=(
            _argument("capability", "Capability name, slug, or id."),
            _argument("dataset", "Evaluation dataset UUID from list_datasets."),
            _argument("model_ids", "Comma-separated model ids selected for comparison."),
        ),
        template=(
            "Compare selected models for capability {capability} on evaluation dataset "
            "{dataset}; requested models: {model_ids}. Use list_datasets, inspect_dataset, "
            "and query_dataset to choose and verify an eval cell. Run "
            "check_optimizer_readiness with that dataset and cell, mode model_comparison and "
            "those model ids, then start_optimizer with the same "
            "mode only when ready and the human has confirmed the model list. The local "
            "executioner performs the repository-side runs; MCP schedules and records them. "
            "Inspect with inspect_optimizer_result and "
            "overmind://optimizer-runs/{{experiment}}; report per-model evidence, winner, "
            "and the next action."
        ),
    ),
    PromptDefinition(
        name="ship-model",
        title="Ship model",
        description="Verify a deployment, activate the approved capability model, and hand off repository rollout.",
        arguments=(
            _argument("capability", "Capability name, slug, or id."),
            _argument("deployment", "Deployment name, model id, or deployment id."),
            _argument("finetune", "Successful fine-tuning job id for repository rollout."),
        ),
        template=(
            "Ship the approved model for capability {capability} using deployment {deployment} "
            "and fine-tune {finetune}. Read overmind://deployments/{deployment} and "
            "overmind://finetunes/{finetune}; wait for the linked deployment with get_job "
            "using kind deployment, then use run_inference when it is ready and "
            "set_active_model. Use retry_deployment only to recover a failed or deleted "
            "deployment. If a repository model reference must "
            "change, use get_model_swap_prompt for the fine-tune and apply the returned "
            "prompt in the local repository. Verify final state from the deployment "
            "and capability resources and report the decision checkpoint."
        ),
    ),
    PromptDefinition(
        name="upload-dataset-file",
        title="Upload dataset file",
        description="Upload a local dataset file through the CLI and verify it through MCP.",
        arguments=(
            _argument("path", "Local CSV, TSV, JSON, JSONL, or NDJSON path."),
            _optional_argument("intent", "Optional dataset intent: train or eval."),
            _optional_argument("project_id", "Optional project UUID."),
        ),
        template=(
            "Upload the local dataset file at {path}. MCP does not carry local file bytes: run "
            "`overmind dataset upload FILE --json` from the coding agent's filesystem. Replace "
            "FILE with the exact local path supplied by MCP as one shell argument. Optionally "
            "add `--intent train|eval` with the supplied intent, or `--split PERCENT` to land a "
            "train and an eval dataset (the JSON carries `id` and `eval_id`), and `--project-id` "
            "with the supplied project UUID. Never request an API key in chat. Parse the "
            "returned dataset UUID, poll with get_job(kind=dataset_run), and call inspect_dataset. Use "
            "message_dataset_agent for requested name, intent, capability, or cell changes, "
            "then poll and inspect again. If a proposal exists, call "
            "run_dataset(proposal_cell=...) only after user approval. Verify the chosen cell "
            "with query_dataset."
        ),
    ),
    PromptDefinition(
        name="export-dataset",
        title="Export dataset",
        description="Download a committed dataset to the coding agent's local filesystem.",
        arguments=(_argument("dataset", "Dataset id supplied by MCP."),),
        template=(
            "Download committed dataset {dataset} to the coding agent's local filesystem. "
            "MCP carries workflow instructions, not file bytes: run `overmind dataset export "
            "DATASET --json`. Replace DATASET with the exact dataset id resolved through MCP as "
            "one shell argument; do not resolve a dataset name locally or invent an id. The CLI "
            "authenticates with the configured X-Api-Key, uses the "
            "Content-Disposition filename when no output path is supplied, and refuses to "
            "overwrite an existing file. For trace data: select traces, call "
            "create_dataset_from_traces, poll get_job with kind dataset_run, call inspect_dataset, "
            "then run the dataset export locally. Add `--cell` to export the chosen "
            "cell; preserve X-Overmind-Cell, X-Overmind-Version, and "
            "X-Overmind-Fingerprint when caching it. Keep trace export on this workflow; there is no "
            "export_trace MCP tool."
        ),
    ),
    PromptDefinition(
        name="download-checkpoint",
        title="Download checkpoint",
        description="Download an archived fine-tuned model checkpoint to the coding agent's local filesystem.",
        arguments=(_argument("deployment", "Deployment id supplied by MCP."),),
        template=(
            "Download the archived checkpoint for fine-tuned deployment {deployment}. First resolve "
            "and read the deployment through the MCP resource "
            "overmind://deployments/{deployment}; if needed, read its linked "
            "overmind://finetunes/{{finetuning_job}} resource to confirm the fine-tuning provider. "
            "Use only the deployment id supplied by MCP as DEPLOYMENT; do not resolve a name locally "
            "or expose any presigned URL. On the coding agent's filesystem, run `overmind model "
            "download-checkpoint DEPLOYMENT --json`. Replace DEPLOYMENT with the exact deployment id "
            "resolved through MCP as one shell argument. Parse the result and report its local `path` "
            "and `bytes_written`. Only archived checkpoints for supported fine-tune providers "
            "(baseten or modal) are downloadable. The CLI refuses to overwrite an existing file."
        ),
    ),
)

_PROMPTS_BY_NAME = {prompt.name: prompt for prompt in PROMPTS}


def list_prompts() -> list[types.Prompt]:
    return [prompt.as_mcp_prompt() for prompt in PROMPTS]


def get_prompt(name: str, arguments: Mapping[str, str] | None = None) -> types.GetPromptResult:
    prompt = _PROMPTS_BY_NAME.get(name)
    if prompt is None:
        raise McpError(
            types.ErrorData(code=-32602, message="The requested prompt is not available.")
        )

    values = {
        argument.name: str((arguments or {}).get(argument.name, "")).strip()
        for argument in prompt.arguments
    }
    missing = [
        argument.name
        for argument in prompt.arguments
        if argument.required and not values[argument.name]
    ]
    if missing:
        raise McpError(
            types.ErrorData(
                code=-32602,
                message=f"Prompt argument {missing[0]!r} is required.",
            )
        )

    return types.GetPromptResult(
        description=prompt.description,
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=prompt.template.format(**values)),
            )
        ],
    )
