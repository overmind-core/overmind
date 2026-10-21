---
name: overmind
description: Connect and set up Overmind, discover capabilities from a local repository, or coordinate work across product surfaces. Use the focused Overmind surface skills for individual platform workflows and native MCP prompts when available.
---

# Overmind MCP

Overmind models production work as **capabilities** scored by an **eval
matrix**. A capability is the product AI surface; each of its spans is scored
live against the matrix rows that apply, and each trace's trajectory is scored
once its spans are.

This skill owns connection, onboarding, local repository setup and routing.
Every platform workflow lives in exactly one surface skill below; open only the
one the task needs.

MCP prompts are the native guided workflows. Invoke the matching prompt when
the client lets the agent invoke prompts. Otherwise, or when local repository
work is required, follow the matching skill. In Claude Code only the user can
run a prompt, as `/mcp__overmind__<prompt-name>`.

## Arguments

When invoked with an argument, such as `/overmind setup`, open the matching
file and follow it:

- `onboard`: [references/onboard.md](references/onboard.md)
- `setup`: [references/setup.md](references/setup.md)
- `ensure-tracing`: [Observability](../overmind-observability/SKILL.md#instrumentation)
- `dataset`: [Datasets](../overmind-datasets/SKILL.md)
- `model-workflows`: [Develop models from data](../overmind-training/SKILL.md#develop-models-from-data)
- `finetune`: [Training](../overmind-training/SKILL.md)
- `optimise`: [Optimiser](../overmind-optimiser/SKILL.md)
- `backtest`: [Model comparison and backtest](../overmind-optimiser/SKILL.md#model-comparison-and-backtest)

## Product surface skills

The plugin and `overmind init` include these focused workflows. Select the one
that matches the user's task; do not load all of them for a single operation.
Each works directly with the configured MCP connection and can be used on its own.

| Skill                                                                | Use it for                                                    |
| -------------------------------------------------------------------- | ------------------------------------------------------------- |
| [Agent](../overmind-agent/SKILL.md)                                  | Capability map, eval coverage, active and benchmark models    |
| [Observability](../overmind-observability/SKILL.md)                  | Traces, failures, signals, latency and Python instrumentation |
| [Datasets](../overmind-datasets/SKILL.md)                            | Intent and contracts, transformations, lineage, export        |
| [Evaluations](../overmind-evaluations/SKILL.md)                      | Eval matrix, eval sets, live scoring, runs and comparisons    |
| [Optimiser](../overmind-optimiser/SKILL.md)                          | Prompt/code experiments, model comparison and backtest        |
| [Training](../overmind-training/SKILL.md)                            | Model selection, preparation, cost, fine-tuning, data-first   |
| [Inference](../overmind-inference/SKILL.md)                          | Deployments, serving metrics, inference and activation        |
| [Integrations](../overmind-integrations/SKILL.md)                    | Provider connectors, boundary mapping and trace import        |
| [Model training engineering](../model-training-engineering/SKILL.md) | Cross-stage data-to-training methodology                      |

## Native prompts

Route guided work to these exact prompt names:

- `investigate-capability`: health, failures, traces, and instrumentation gaps.
- `instrument-repository`: translate an instrumentation plan into a human-applied code change and verify supplied spans.
- `upload-dataset-file`: upload local data through the CLI, then land the dataset through MCP or REST.
- `author-dataset-transformation`: retain, inspect, test, reuse and adapt versioned transformations with explicit conditional flow.
- `export-dataset`: download a dataset version through the local CLI; MCP carries guidance, not file bytes.
- `download-checkpoint`: download an archived fine-tuned deployment checkpoint through the local CLI; MCP carries guidance, not checkpoint bytes.
- `connect-traces`: connect a tracing provider, review capability boundaries and verify imported traces.
- `develop-model-from-data`: establish a generative or decision task, retain a training plan and data evidence, train and evaluate the requested recipe, with bounded quality pilots only when requested.
- `prepare-evaluation`: check evaluation dataset, eval matrix rows, eval set, and credits.
- `evaluate-change`: run an evaluation and compare it with a supplied baseline.
- `finetune-capability`: check, estimate, launch, and verify fine-tuning.
- `optimize-capability`: schedule and inspect prompt/code optimization.
- `compare-models`: schedule and inspect model comparison.
- `ship-model`: verify deployment, activate a model, and hand off repository rollout.

Do not reimplement these workflows as a single generic call. The prompt
supplies the workflow; the skill supplies only missing local actions,
human approval boundaries, and fallback sequencing.

## Onboarding and local setup

Initial Console onboarding and local capability discovery remain local
workflows: use [references/onboard.md](references/onboard.md) for a new project
and [references/setup.md](references/setup.md) for repository scanning
(`overmind chassis` → capability cards / trajectory maps → `overmind.toml` →
`overmind sync` → decorators with span expectations). Both use
[references/onboarding-progress.md](references/onboarding-progress.md) for the
opening roadmap, numbered progress updates, and data disclosures. For a
JavaScript/TypeScript repository read
[references/javascript.md](references/javascript.md) before local setup and apply
it on top of the Python-worded setup; no tracing is allowed there.

- `/overmind setup` scans the local repository and writes capability metadata;
  `overmind sync` sends that snapshot to the configured project. MCP cannot
  scan or edit the repository.
- If MCP returns a repository change, show it as a human action. The human
  reviews and applies it locally.

The plugin is an optional distribution package for this skill and the existing
MCP connection. Essential guidance is supplied by MCP initialization, tool
descriptions and resources. Installing the plugin adds no separate product UI
or additional platform permissions.

## Connection and safety

Global Codex connection setup is `overmind connection configure --api-url URL --project-id PROJECT --json`, using an account key already available locally.
It writes the MCP and transfer configuration together without pinning a global
project. It does not grant sandbox network permissions. Verify from the actual
coding environment with `overmind connection check --project-id PROJECT --json`;
configuration alone is not readiness. Follow the Datasets skill for durable
upload recovery and scoped host approvals.

Use the connected catalog and `overmind://interface/current` as the authority for available operations and the current contract version. OAuth/account API keys discover projects with `list_projects` and pass `project_id` on every operation and resource; project API keys retain narrower scope. Never put credentials in chat. `create_api_key` returns a new secret once; do not write it into the repository, a prompt, or another tool argument.

Data-first projects do not require repository scanning, capabilities or instrumentation. Use `develop-model-from-data` when starting with uploaded data. Repository discovery remains a local workflow only for code-backed projects. Installed skills assist discovery; they are not a correctness prerequisite.

The catalog exposes deliberate lifecycle operations including `cancel_dataset`, `retry_data_partition`, and comparison pause/resume. Follow their schemas. Pausing a comparison stops new work; it does not acknowledge cancellation of an already-submitted provider call. Stopping observation does not stop execution.

## Core principles

1. **Discover the connected product.** Read `overmind://interface/current`, select an authorized project, and use named MCP operations. Keep platform-owned training, evaluation and serving on their named tools. The native agent authors dataset transformations as staged Python packages; use the upload/export CLI for local file transport. Draft comparisons and experiments do not submit paid work; preparation returns a background receipt, and launch records the authorized saved scope. Check actual schemas rather than assuming an installed skill has the newest catalog.
1. **One skill per workflow.** Open the surface skill for the task before
   implementing. This file holds only conventions that apply everywhere.
1. **Discover, then pass the id the schema asks for.** There are no
   `list_capabilities`, `list_traces`, `get_trace`, `get_capability`,
   `list_eval_sets`, `list_eval_runs`, `list_finetune_jobs`,
   `list_deployed_models`, or `job_status` tools. Use `list_datasets`,
   `query_*`, `inspect_*`, `get_job`, and resource reads. Never paste raw
   UUIDs to the user when a name/slug exists.
   Dataset names are not unique: `list_datasets` then pass that UUID to
   `inspect_dataset` / `query_dataset` (those two reject names). Fine-tune,
   eval, and optimizer tools also accept a unique dataset name.
   Capability tools accept name, slug, or id.
1. **Contracts gate dataset consumption.** Fine-tuning needs a `train`
   version; eval runs and optimizer experiments need `eval`; `pending` is
   refused. See [Datasets: intent and contracts](../overmind-datasets/SKILL.md#intent-and-contracts).
1. **Follow structured errors and durable receipts.** Read the error code, fields and retryability. Creation and paid launch are separate where the schema specifies a draft. Authorization already granted for an unchanged saved scope persists through routine continuation. Unresolved provider submissions require receipt recovery; never blindly repeat them.
1. **Tracing is Python-only and approval-gated.** Check the application code
   first; any other language gets no plan, no edits and no OTLP exporter. Real
   application runs happen only after explicit user approval with the exact run
   details and correlation value presented first. The placement, approval and
   verification procedure is in
   [Observability: instrumentation](../overmind-observability/SKILL.md#instrumentation).

## Conventions

- **List first.** `list_datasets` for datasets. Everything else:
  `inspect_capability_health`, `query_traces`, `query_failures`, or a
  resource read. Read `overmind://capabilities/{capability}` for capability
  `id` and `active_model`.
- **Async jobs.** Poll returned job references with `get_job(kind, id)`.
  Source landing uses `kind=dataset_run`; transformations use the returned `kind=dataset_pipeline` and run ID. Other supported kinds include
  `eval_run`, `finetune_job`, `deployment`, `model_activation`, and `optimizer_experiment`.
- `inspect_operation` reads a linked operational receipt, with `after`/`limit`
  pagination over its durable events. It never wakes a worker. Distinguish observed
  state, heartbeat and forward progress; shared-pool telemetry does not prove a
  particular adapter or application is ready.
- Chat-UI-only helpers (`propose_plan`, `suggest_navigation`) are not exposed
  on MCP. The server does not expose generic delete or API-proxy tools. Use the
  returned structured fields and resource links rather than guessing older
  endpoint-shaped names.

## Curated MCP tools

Use only these implemented names and inspect their schemas at call time. Each
group's owning skill carries its workflow.

- Observability: `inspect_capability_health`, `query_failures`, `query_traces`, `get_job`.
- Signals: `list_signals`, `set_signal_subscription`.
- Instrumentation: `get_instrumentation_plan`, `verify_instrumentation`.
- Datasets: `list_datasets`, `inspect_dataset`, `query_dataset`,
  `start_dataset`, `create_dataset_from_traces`, `create_dataset_from_llm_calls`,
  `inspect_dataset_workbench`, `save_dataset_pipeline`, `run_dataset_pipeline`,
  `import_dataset_version`, `update_dataset`, `cancel_dataset`.
- Evaluations: `check_evaluation_readiness`, `create_eval_matrix_row`, `delete_eval_matrix_row`,
  `create_eval_set`, `run_evaluation`, `compare_evaluations`.
- Training: `get_model_catalog`, `check_finetune_readiness`, `estimate_finetune`,
  `start_finetune`, `save_training_plan`, `create_training_experiment`.
- Serving: `retry_deployment`, `set_active_model`, `set_benchmark_model`,
  `run_inference`, `get_model_swap_prompt`.
- Optimization: `check_optimizer_readiness`, `start_optimizer`, `inspect_optimizer_result`.
- Connectors: `inspect_connectors`, `configure_connector`, `sync_connector`.

## How the workflows chain

Typical loop (local setup once, then MCP):

1. **See what's happening**: [Observability](../overmind-observability/SKILL.md).
   Resolve capabilities through [Agent](../overmind-agent/SKILL.md). If none
   exist, run [setup](references/setup.md). If nothing is landing and the codebase
   is Python, add tracing; any other language gets none.
1. **Turn traces into data and shape it**: [Datasets](../overmind-datasets/SKILL.md).
1. **Grade it**: [Evaluations](../overmind-evaluations/SKILL.md). The capability's
   active eval set grades live traces, eval runs, backtests, the optimizer and
   training alike. Finetune and optimizer runs create their own baselines; do not
   spend a manual eval run just to give them a comparison point.
1. **Improve**: [Training](../overmind-training/SKILL.md) (**train** dataset),
   [Optimiser](../overmind-optimiser/SKILL.md) (**eval** dataset; `/overmind optimise`
   or `/overmind backtest`).
1. **Prove it**: `compare_evaluations` new vs the automatic baseline.
1. **Ship**: [Inference](../overmind-inference/SKILL.md) for activation and
   `get_model_swap_prompt`, or land the optimizer winner's diff locally.

## Resources

Discover accessible projects with `list_projects`. The project resource is:

`overmind://project/current?project_id=ID`

Includes `repository_snapshot` (repository, directory, branch, commit, dirty state, fingerprint and scan time) and `last_synced_at`. A null snapshot means the revision is unknown; sync time is not scan time. Run local `/overmind setup` to refresh the map.

`console_url` opens the authenticated project's ordinary Console on this
deployment. It contains no credentials; the browser still requires its own
Console session.

Static local guidance resources: `overmind://dataset-upload`,
`overmind://dataset-export`, `overmind://checkpoint-download` and
`overmind://connector-setup`.

The implemented resource templates are:

- `overmind://capabilities/{capability}`
- `overmind://traces/{trace_id}`
- `overmind://sessions/{session}`
- `overmind://datasets/{dataset}`
- `overmind://eval-runs/{eval_run}`
- `overmind://eval-sets/{eval_set}`
- `overmind://signal-events`
- `overmind://finetunes/{job_id}`
- `overmind://deployments/{deployment}`
- `overmind://optimizer-runs/{experiment}`
- `overmind://connectors/{connector}`
- `overmind://jobs/{kind}/{id}`

Use a capability, dataset, run, deployment, connector, or experiment name/id
only where the tool schema accepts it. Resource reads are project-scoped and
return JSON.

## Console navigation

When the user wants to see a product view, start with `console_url` from
`overmind://project/current`. Use an existing returned Console link where one is
available. Otherwise preserve its deployment base and `projectId`, and append
the relevant route using a resource ID already resolved through MCP:

| View           | Route                   |
| -------------- | ----------------------- |
| Capability     | `capabilities/{id}`     |
| Traces         | `observability`         |
| Dataset        | `datasets/{id}`         |
| Evaluation run | `evaluations/runs/{id}` |
| Training       | `training`              |
| Optimiser      | `optimiser`             |
| Serving        | `inference`             |

Use the host's browser-opening tool (Codex `open_in_codex` when available), or
return the ordinary link. Do not add plugin-only presentation parameters or
build another UI. Verify the browser project matches the MCP project before
combining their evidence. Console navigation is optional; continue platform
work through MCP when no browser is available.
