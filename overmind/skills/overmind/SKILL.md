---
name: overmind
description: Use Overmind's curated MCP server for project-scoped observability, datasets, evaluations, fine-tuning, optimization, connectors, and instrumentation; use the skill only as fallback orchestration and for local repository work.
---

# Overmind MCP

Overmind models production work as **Capability > behaviour > task
execution**. A capability is the product AI surface, a behaviour is a
scanned contract, and a task execution is a carved, scored unit of a trace.

MCP prompts are the native guided workflows. Invoke one of the eleven prompts
when the client supports prompts; use the references in this directory only
when prompt support is unavailable or when local repository work is required.

## Native prompts

Route guided work to these exact prompt names:

- `investigate-capability` — health, failures, traces, task executions, and instrumentation gaps.
- `instrument-repository` — translate an instrumentation plan into a human-applied code change and verify supplied spans.
- `upload-dataset-file` — upload local data through the CLI, then land the dataset through MCP or REST.
- `export-dataset` — download a dataset version through the local CLI; MCP carries guidance, not file bytes.
- `download-checkpoint` — download an archived fine-tuned deployment checkpoint through the local CLI; MCP carries guidance, not checkpoint bytes.
- `prepare-evaluation` — check evaluation dataset, evaluators, eval set, bindings, and credits.
- `evaluate-change` — run an evaluation and compare it with a supplied baseline.
- `finetune-capability` — check, estimate, launch, and verify fine-tuning.
- `optimize-capability` — schedule and inspect prompt/code optimization.
- `compare-models` — schedule and inspect model comparison.
- `ship-model` — verify deployment, activate a model, and hand off repository rollout.

Initial Console onboarding and local capability discovery remain local
workflows: use [references/onboard.md](references/onboard.md) for a new project
and [references/setup.md](references/setup.md) for repository scanning and sync.

Do not reimplement these workflows as a single generic call. The prompt
supplies the workflow; the skill supplies only missing local actions,
human approval boundaries, and fallback sequencing.

## Connection and safety

- `overmind sync` stores the **project-scoped API key** locally and configures
  each initialized MCP client with `X-Api-Key`; that value is still an API key,
  not OAuth. Never use OAuth for this server and never ask the user to paste a
  key into chat.
- The public server supports read and write permissions for the curated
  surface. It is project-scoped and returns structured errors as values.
- There are no public product tools for deletion, cancellation, or removal.
  Deployment recovery is limited to `retry_deployment` for failed or deleted
  deployments; do not invent other lifecycle tools.
- Treat dataset landing, evaluator writes, job starts, deployment changes, and
  active-model changes as mutations. Confirm user intent where the workflow
  requires approval; the server does not add a confirmation dialog.

## Core principles

Follow these for ALL Overmind work:

1. **Local setup, then MCP.** Capability discovery is local: scan the repo,
   write `overmind.toml`, run `overmind sync` — see
   [references/setup.md](references/setup.md). After that, all platform work
   goes through the Overmind
   MCP server. Do not curl REST endpoints, do not invent base URLs, and do not
   hardcode hosts. The server is already configured (plugin, or `overmind init`)
   and scoped to one project via the saved API key in its headers. Call the named
   tools; inspect each tool's schema for arguments. If tools are missing, tell
   the user to run `overmind init` for the IDE and `overmind sync` to install
   its project credential. Do not paste a URL or ask them to paste the raw key
   into chat.
1. **Reference file per use case.** Check the relevant reference below before
   implementing. This file holds conventions that apply everywhere; the
   workflow lives in the reference.
1. **Discover, then pass the id the schema asks for.** There are no
   `list_capabilities`, `list_traces`, `get_trace`, `get_capability`,
   `list_eval_sets`, `list_evaluators`, `list_eval_runs`, `list_finetune_jobs`,
   `list_deployed_models`, or `job_status` tools. Use `list_datasets`,
   `query_*`, `inspect_*`, `get_job`, and resource reads. Never paste raw
   UUIDs to the user when a name/slug exists.
   Dataset names are not unique: `list_datasets` then pass that UUID to
   `inspect_dataset` / `query_dataset` (those two reject names). Fine-tune,
   eval, and optimizer tools also accept a unique dataset name.
   Capability tools accept name, slug, or id. Stamp the capability resource
   `id` into the SDK. Pass a READY deployed-model UUID to `set_active_model`
   (omit to clear). See [references/capabilities.md](references/capabilities.md).
1. **Behaviours have no resource.** There is no
   `overmind://behaviours/...`; read them from `query_task_executions`.
1. **Contracts gate every dataset workflow.** Intent is **`train`**,
   **`eval`**, or **`pending`** — never `ft` or `surface`. Fine-tuning needs
   `train`; eval runs and optimizer experiments need `eval`. `pending` is
   refused. There is no reingest tool and no dual-intent dataset. Set intent
   at upload (`overmind dataset upload FILE --json --intent train|eval`), at
   create (`create_dataset_from_traces` / `_failures`), or later with
   `message_dataset_agent` ("set intent to train") if no version has been used.
   A used cell freezes intent: upload a second dataset with the other
   `--intent` instead of retagging. Read the contracts section below.
1. **Errors are values; mutations run immediately.** Every tool returns
   `{"error": "..."}` instead of raising — follow `fields` when present.
   There is no confirmation gate, so verify arguments (and ask the user when
   destructive) before create/delete/cancel. There are no delete or cancel
   tools.
1. **Ticketed instrumentation.** Call `get_instrumentation_plan` with no
   capability for project-wide work, or with a capability for scoped work, and
   treat each placement as an edit ticket. Copy `target.file`, `qualname`, required
   scope, required decorator, import line, and capability id. `lineno` and
   `source_line` are often null — file + qualname is enough to locate the
   function. Keep a primary scope outermost when a specialized span targets the
   same function. Spawn one coding subagent per unique `target.file`.
1. **Explicit run approval.** After applying the ticketed code changes, report
   the changed files and any local checks, then ask the user to choose a real run or a bounded smoke run. The real run
   must state its exact input, environment, provider/model, expected side
   effects, and unique correlation value. Do not execute either mode before
   explicit approval; a real-run retry needs fresh approval unless a bounded
   retry count and exact input were approved.
1. **Server-side verification.** Flush the approved run, query
   `query_traces` with the narrowest correlation and `all_spans=true`, poll
   within a fixed bound, and require exactly one matching trace. Read
   `overmind://traces/{trace_id}` and pass its complete server-supplied
   `spans` to `verify_instrumentation` (no DB writes). Reject zero or multiple
   matches, truncated resources, and span lists over the verifier limit. Report
   application outcome separately from instrumentation status.

## Use-case references

- Local setup (`overmind chassis` → scan repo → capability cards / trajectory
  maps / eval matrix → `overmind.toml` → `overmind sync`):
  [references/setup.md](references/setup.md)
- Resolving / updating agents, prompts, and eval spec:
  [references/capabilities.md](references/capabilities.md)
- Tasks (behaviour registry, task executions, eval coverage):
  [references/behaviours.md](references/behaviours.md)
- Telemetry (add tracing, inspect traces / sessions / health, connectors):
  [references/telemetry.md](references/telemetry.md)
- Landing datasets (from traces, failures, a file or rows), handing a
  version to a consumer, and pulling a version to disk:
  [references/datasets.md](references/datasets.md)
- Authoring evaluators, grouping them into eval sets, running and comparing
  eval runs:
  [references/evals.md](references/evals.md)
- Fine-tuning a model (prerequisites, recommended-model sweep, deploy, swap PR):
  [references/finetuning.md](references/finetuning.md)
- Optimizer experiments (`/overmind optimise` — skill writes diffs/commands;
  SDK runs locally; server scores):
  [references/optimizer.md](references/optimizer.md)
- Model backtest (skill rewrites provider + model onto OpenRouter via
  `overmind.backtest.rewrite_repo`; MCP posts outputs; server scores):
  [references/backtest.md](references/backtest.md)

## Conventions (read before any workflow)

- **List first.** `list_datasets` for datasets. Everything else:
  `inspect_capability_health`, `query_traces`, `query_task_executions`,
  `query_failures`, or a resource read. Pass the UUID `list_datasets`
  returned into dataset inspect/query.
  Capability name/slug/id, eval-set name, and unique dataset names work on
  the tools whose schemas accept them. Read
  `overmind://capabilities/{capability}` for capability `id` and
  `active_model`.
- **Async jobs.** Poll returned job references with `get_job(kind, id)`.
  Dataset work uses `kind=dataset_run`; other supported kinds include
  `eval_run`, `finetune_job`, `deployment`, and `optimizer_experiment`.
- Chat-UI-only helpers (`propose_plan`, `suggest_navigation`) are not exposed
  on MCP.

## Curated MCP tools

Use only these implemented names and inspect their schemas at call time.

Observability:

`inspect_capability_health`, `query_failures`, `query_traces`,
`query_task_executions`, `get_job`.

Datasets:

`list_datasets`, `inspect_dataset`, `query_dataset`,
`create_dataset_from_traces`, `message_dataset_agent`, `run_dataset`.

Evaluations:

`check_evaluation_readiness`, `upsert_evaluator`, `run_evaluation`,
`compare_evaluations`, `annotate_evaluation_sample`.

Fine-tuning and serving:

`get_model_catalog`,
`check_finetune_readiness`, `estimate_finetune`, `start_finetune`,
`retry_deployment`, `set_active_model`, `run_inference`,
`get_model_swap_prompt`.

Call `get_model_catalog` before choosing a fine-tuning model. It is
dataset-independent and reports the active backend, tier, context limits,
batch bounds, training methods, tool-calling support, and disabled rows.

Optimization:

`check_optimizer_readiness`, `start_optimizer`,
`inspect_optimizer_result`.

Connectors:

`inspect_connectors`, `configure_connector`, `sync_connector`.

Instrumentation:

`get_instrumentation_plan`, `verify_instrumentation`.

The server does not expose delete, cancel, or generic API tools. Use
`retry_deployment` only for its documented failed/deleted deployment recovery
case.
Use the returned structured fields and resource links rather than guessing
older endpoint-shaped names.

## Dataset contracts — read first, they gate every workflow

A dataset is a landed source and a linear chain of cells; every cell that
ran is a **version** (1.0 is the source, then 1.1, 1.2, …), the dataset has
an **intent** (`train` or `eval`, proposed at landing) and a **capability**,
and every version carries two measured contracts (`list_datasets` shows the
active version's):

- **`train`** ("Train") — a `messages` column whose every row is a chat
  transcript with an assistant turn (`tools` optional), and every system
  turn and tool call is the capability's own.
- **`eval`** ("Eval") — an `input` on every row plus an `expected_output`
  column with at least one reference, and every input carries the
  capability's required keys.
- **`pending`** — the intent is not decided yet; refused by every run.
- There is no `ft` intent. A leftover stored `ft` is **train**.

What each workflow accepts:

- **Eval runs** (`run_evaluation`) and **optimizer experiments**
  (`start_optimizer`) use the active version of an **eval** dataset.
- **Fine-tuning** (`start_finetune`) uses a **train** version, plus a
  separate **eval** dataset for in-training judge evals.

A use freezes the version and everything before it, and starts a new major
(2.0). A contract is measured, never declared. The dataset's own agent shapes
the chain; if a consumer rejects a dataset for its contract, `inspect_dataset`
names the reason. Use `message_dataset_agent` to request changes, then poll
`get_job(kind=dataset_run)` and inspect again. Rows are never cleaned locally:
land them raw, shape them on the server.
Local loops pull one version by cell id
([datasets.md](references/datasets.md#pulling-a-version-to-disk)).

## How the workflows chain

Typical loop (local setup once, then MCP):

1. **See what's happening** — [telemetry.md](references/telemetry.md)
   (`inspect_capability_health` → `query_failures` → `query_task_executions`
   → `query_traces` / `overmind://traces/{trace_id}`). The task-execution layer
   ([behaviours.md](references/behaviours.md)) sits between the agent and its
   spans: check it before walking traces by hand, and treat
   `binding_source: "unbound"` as an instrumentation gap, not a scoring one.
   Offline `scores.overall_pass_rate` and live `live_trace_scores` are
   different systems; do not treat a 1.0 offline rate as "no live failures."
   Resolve / retarget capabilities via [capabilities.md](references/capabilities.md)
   (`overmind://capabilities/{capability}`, `inspect_capability_health`,
   `set_active_model`). If none
   exist, run [setup.md](references/setup.md) (`overmind chassis` →
   `overmind.toml` → `overmind sync`). If nothing is landing, add tracing in the same file —
   stamp the capability's `id` and use the ticket fan-out workflow in
   [references/telemetry.md](references/telemetry.md).
1. **Turn traces into data** — [datasets.md](references/datasets.md)
   (`create_dataset_from_traces`, or CLI upload).
1. **Shape it** — use `message_dataset_agent`, poll
   `get_job(kind=dataset_run)`, inspect with `inspect_dataset`, and accept a
   proposed cell with `run_dataset` only after user approval.
1. **Grade it** — [evals.md](references/evals.md) when you want an
   eval-vs-eval comparison you drive yourself. Finetune and optimizer runs
   create their own incumbent / experiment baselines automatically — do not
   spend a manual eval run just to give them a comparison point.
1. **Improve** — [finetuning.md](references/finetuning.md) (**train** dataset;
   recommended-model sweep), [optimizer.md](references/optimizer.md)
   (**eval** dataset; `/overmind optimise`), or
   [backtest.md](references/backtest.md) (model comparison;
   `/overmind backtest`).
1. **Prove it** — `compare_evaluations` new vs the automatic baseline
   ([evals.md](references/evals.md)).
1. **Ship** — apply `get_model_swap_prompt` in the repository, land the
   optimizer winner's diff locally, or pin the winning backtest model.

## Resources

The static project resource is:

`overmind://project/current`

The static local dataset upload guidance resource is:

`overmind://dataset-upload`

The static local dataset export guidance resource is:

`overmind://dataset-export`

The static local checkpoint download guidance resource is:

`overmind://checkpoint-download`

The implemented resource templates are:

- `overmind://capabilities/{capability}`
- `overmind://traces/{trace_id}`
- `overmind://sessions/{session}`
- `overmind://datasets/{dataset}`
- `overmind://eval-runs/{eval_run}`
- `overmind://finetunes/{job_id}`
- `overmind://deployments/{deployment}`
- `overmind://optimizer-runs/{experiment}`
- `overmind://connectors/{connector}`
- `overmind://jobs/{kind}/{id}`

Use a capability, dataset, run, deployment, connector, or experiment name/id
only where the tool schema accepts it. Resource reads are project-scoped and
return JSON. Job references use the kind values accepted by `get_job`, such as
`eval_run`, `finetune_job`, `deployment`, or `optimizer_experiment`.

## Fallback routing

Read the smallest matching reference only when the native prompt is missing or
local work is needed:

- local repository setup: [references/setup.md](references/setup.md)
- capability and behaviour orientation: [references/capabilities.md](references/capabilities.md) and [references/behaviours.md](references/behaviours.md)
- telemetry and code instrumentation: [references/telemetry.md](references/telemetry.md)
- dataset landing and shaping: [references/datasets.md](references/datasets.md)
- evaluation authoring and runs: [references/evals.md](references/evals.md)
- fine-tuning and serving: [references/finetuning.md](references/finetuning.md)
- optimizer executioner: [references/optimizer.md](references/optimizer.md)
- model comparison/backtest: [references/backtest.md](references/backtest.md)

### Local boundaries

- `/overmind setup` scans the local repository and writes capability metadata;
  `overmind sync` sends that snapshot to the configured project. MCP cannot
  scan or edit the repository.
- `get_instrumentation_plan` is read-only. Apply its exact tickets locally;
  the MCP server cannot edit files or ingest a smoke trace. Use
  `verify_instrumentation` only with caller-supplied spans.
- MCP does not carry local file bytes. From a coding agent with filesystem
  access, run `overmind dataset upload FILE --json` with optional
  `--intent train|eval` and `--project-id`. The command returns the dataset
  UUID; poll it with `get_job(kind=dataset_run)`, then inspect it. Land raw
  rows; the dataset agent shapes cells on the server.
- MCP does not carry dataset export bytes. After the active version fits, run
  `overmind dataset export DATASET --json` locally, optionally adding
  `--format jsonl|csv`, `--cell`, or `--output PATH`. Use the dataset id
  supplied by MCP; the CLI does not resolve names, uses the server filename when
  no output path is given, and refuses overwrite. For traces, select traces,
  call `create_dataset_from_traces`, wait for the agent to shape the chain,
  then run the local export. There is no `export_trace` MCP tool.
- MCP does not carry checkpoint bytes or presigned URLs. Resolve and read the
  deployment through the existing MCP resource/tool flow, then run
  `overmind model download-checkpoint DEPLOYMENT --json` locally with the
  deployment id supplied by MCP. The CLI uses `X-Api-Key` from `--api-key`,
  `.overmind/credentials.toml`, or `OVERMIND_API_KEY`, and its base URL from
  `OVERMIND_API_URL`, `--api-url`, or `overmind.toml`; `--path` selects the
  config file. Only archived checkpoints for `baseten` and `modal` fine-tune
  providers are downloadable, and the CLI refuses overwrite. Report the local
  `path` and `bytes_written`; never expose the presigned S3 URL to model
  context.
- Connector credentials are never MCP arguments. If `inspect_connectors`
  reports `connector_setup_required` and `/integrations`, the human enters or
  authorizes the provider credentials in that supported integration surface;
  then use `configure_connector` and `sync_connector` for safe settings and
  import.
- Optimizer and backtest repository execution stays in the local SDK/CLI
  execution ledger. MCP schedules and reports the project experiment; it does
  not execute local commands or apply diffs.
- If MCP returns a repository change, show it as a human action. The human
  reviews and applies it locally.

Follow [references/telemetry.md](references/telemetry.md) for instrumentation
verification. Real application tasks are allowed only after explicit user
approval with the exact run details and correlation value presented first.
