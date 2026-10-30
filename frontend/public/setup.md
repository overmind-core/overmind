# Overmind setup

These are instructions for you, an AI coding agent, to set the human up on
Overmind. Read them once, then act on them. The human who handed you this
document wants you to do the setup and execute workflows, not to explain it back to them.

Run from the repository root. Every step is safe to repeat.

1. Install the CLI (Python 3.10+): `python -m pip install --upgrade "overmind>=0.1.106"`.
   If pip reports an externally managed environment, run
   `uv tool install --upgrade "overmind>=0.1.106"` (install `uv` first if it is missing) and use
   `overmind` wherever this guide says `python -m overmind`; if `overmind` is not on `PATH`, call it
   from the directory `uv tool dir --bin` prints. On any other conflict, report it and stop.
1. Run `python -m overmind init --ide <claude|codex|cursor|opencode>` for the agent you run in.
   Add `--api-url URL` if the message names an API. It edits this agent's config; if your
   sandbox blocks that, ask the user to approve running it outside the sandbox.
1. Call `list_projects`. If it fails, have the user sign in, through the client's own prompt when
   it offers one or else by following the `Next:` line `init` printed, then call it again.
   If the prompt supplies `project_id`, verify that exact project is in the connected catalogue
   and use it for setup. Preserve it as `project-id` in `overmind.toml` before repository sync;
   do not create a different project. If the repository already names another project, ask the
   user which destination to use before changing it.

# USING OVERMIND

Overmind can be used in 2 ways, onboarding from a codebase or onboarding using a dataset,
ASK the USER which one they want to continue with, if it hasn't already been configured.

> Connect the project once: call the MCP tool `create_api_key` without `project_id`, then run
> `OVERMIND_API_KEY=<key> python -m overmind sync --api-url <base-url>`. Sync creates the project and saves its key;
> later syncs need no key. Never print or store the account key. Skip this if `overmind.toml`
> has a `project-id` and `.overmind/credentials.toml` exists.

## Onboarding using codebase or repository

Overmind has skills to scan through a respository with agentic codebase written either in Python or Typescript/Javascript

> If codebase is any other Language, avoid runnning `overmind chassis` command and just use the coding agent's output file and use it to run `overmind sync`

- Use the `/overmind setup` skill to start scanning the codebase.

## Onboarding using a dataset

> If this is a data-only project (uploaded files, no repository)

You will ask the human to provide a dataset which the human wants to use
for finetuning aka training a custom model.

Then you will analyse the shape of the dataset and a few rows to check if
the dataset has everything Overmind needs to finetune a model for the human.

Then you invoke the overmind sdk to upload this dataset to overmind backend.

______________________________________________________________________

Overmind has the following features

| Feature           | Reference                                                             |
| ----------------- | --------------------------------------------------------------------- |
| Agent Scanning    | [Code scanning and eval authoring](#code-scanning-and-eval-authoring) |
| Observability     | [Telemetry](#telemetry)                                               |
| Dataset           | [Datasets](#datasets)                                                 |
| Evaluations       | [Evals](#evals)                                                       |
| Finetuning        | [Finetuning](#finetuning)                                             |
| Optimiser         | [Optimiser](#optimiser)                                               |
| Model Backtesting | [Backtesting](#backtesting)                                           |
| Inference         | [Inference](#inference)                                               |

## Code scanning and eval authoring

> Scans your codebase and builds a context map of how AI is used across the codebase

- Run `/overmind setup` locally. MCP cannot scan or edit the repo.
- Judge criteria go in decorator `expectations=[...]`; deterministic checks go in the toml `eval_matrix`. Run `overmind sync` after either.
- No repo, or a rule no catalog check covers: `create_eval_matrix_row`, then `create_eval_set(activate=True)`. Rows are never edited: delete and recreate.
- Never invent capability ids. Read them from `overmind://capabilities/{capability}`.

## Telemetry

> Helps you add observability to your agentic stack.

- Python: use the Overmind SDK. `get_instrumentation_plan`, apply each `required_decorator` verbatim.
- Any other language with OpenTelemetry already enabled: no SDK and no plan. Add Overmind as one more OTLP/HTTP protobuf exporter beside the existing ones, never replacing them:
  - Endpoint `https://api.overmindlab.ai/api/v1/traces`, header `x-api-key: <project key>`.
  - Resource attribute `overmind.capability.id` (the only key that binds spans to a capability).
  - Span attributes: `overmind.span.type=entry_point` and `overmind.unit_kind=run` on the root span of a run, `overmind.delivery=true` on the span carrying the result, `conversation.id` on every span of a multi-turn exchange.
- No OpenTelemetry in a non-Python codebase: report telemetry as not set up. Do not introduce OpenTelemetry for the user.
- Verify (both paths): get the user's approval for one run (real or smoke), stamp `conversation.id` with a unique value, run, `query_traces(session=...)` (expect one root), `verify_instrumentation(trace_id)`.
- Investigate: `investigate-capability` prompt, or `inspect_capability_health` and `query_failures`, then follow trace links.
- Signals: `list_signals`, `set_signal_subscription`.
- Use the skill `/overmind-observability` to run observability operations

## Datasets

> Clients upload datasets to Overmind. Workshop turns them into versioned cells for training, evaluation or exploration

- Intent is the user's choice: `train`, `eval` or `explore`. Never infer it from rows. Set it with `update_dataset`.
- Upload files with the CLI: `overmind dataset upload FILE... --project-id ID --json`. Re-run the identical command to resume. Use `--json-rows-field` for JSON wrappers.
- Landing is not proof of correct ingestion: compare counts and sample values with the local file.
- Transform with a retained Python package: `overmind dataset pipeline-upload`, `save_dataset_pipeline`, `validate_dataset_pipeline`, preview, then full run. Use the `author-dataset-transformation` prompt. Never upload derived rows as a new source.
- From production data: `create_dataset_from_traces` or `create_dataset_from_llm_calls`.
- Export: `overmind dataset export DATASET --json`.
- Use the skill `/overmind-datasets` to run dataset operations

## Evals

> Creates a set of eval metrics relevant to your codebase or dataset and triggers evaluations

- Use the `prepare-evaluation` and `evaluate-change` prompts.
- Flow: `check_evaluation_readiness`, `run_evaluation`, `compare_evaluations`.
- The dataset must be `eval` intent. The capability's active eval set grades by default; do not recreate it.
- Report per-metric deltas and failed or missing rows, not just the average.
- Use the skill `/overmind-evaluations` to run evals operations

## Finetuning

> Creates a custom finetuned language model trained on a dataset in Overmind, validated using the evaluations created for that dataset or codebase

- Needs a `train` dataset and, for chat models, user-confirmed eval metrics. Decision models are exempt.
- Flow: `get_model_catalog`, `check_finetune_readiness`, `prepare_training_data`, `estimate_finetune`, approval, then `start_finetune` or `start_decision_job` with a stable `request_key`.
- Monitor with `get_job` and `inspect_training_progress`. Cancel with `cancel_finetune`. Never relaunch a job that already exists.
- Data-only projects: use the `develop-model-from-data` prompt and `save_training_plan` first.
- Split by group before fitting, measure a baseline, and never select on final-role data.
- Use the skill `/overmind-training` to run finetuning operations

## Optimiser

> Runs an agent in your infrastructure to experiment with different prompts and models and find which works best. All results are backed by evaluations

- Needs an `eval` dataset cell.
- Flow: `check_optimizer_readiness`, `start_optimizer`, then run `overmind optimise start -e EXPERIMENT && overmind optimise next` in the repo and do each returned action.
- Stop at `COMPLETE`. Apply the winning diff only when authorised.
- Use the skill `/overmind-optimiser` to run optimiser operations

## Backtesting

> Tries different models and replays your LLM spans to see which model works best

- Use the `compare-models` prompt: `check_optimizer_readiness` and `start_optimizer` with `mode=model_comparison` and 1-5 model ids, then `inspect_optimizer_result`.
- Or replay stored LLM spans: `overmind backtest --models a,b --since 7d`.
- Use the skill `/overmind-optimiser` to run backtesting operations

## Inference

> Runs inference on the finetuned model through Overmind.

- Successful training deploys automatically. Read `overmind://deployments/{deployment}` for readiness and traffic.
- `run_inference` only on a `ready` deployment, with a stable `request_key`.
- Go live with `set_active_model` after approval and poll the `model_activation` job. Use `get_model_swap_prompt` for the code rollout; the human applies it.
- `retry_deployment` only for a failed deployment.
- Use the skill `/overmind-inference` to run inference operations

## Integrations

- Import external traces: `connect-traces` prompt, `inspect_connectors`, `configure_connector` (propose, get approval, then `confirm_mapping=true`), `sync_connector`.
- Credentials are entered by the human in their terminal, never in chat.
- Use the skill `/overmind-integrations` to run integrations operations

______________________________________________________________________

Help:

> If the user wants to see what overmind can do and how to invoke overmind, these are some of the commands that you need to print.

1. Overmind can help you fine-tune a model using your dataset and/or codebase.
1. Overmind can run inference on your fine-tuned models.
1. Overmind can help you transform your raw data into a structured, fine-tunable dataset.
1. Overmind can help you run evaluations and compare changes in your agents.
1. Overmind can help you add tracing to your LLM agents and monitor your agent behavior using signals.
1. Overmind can help you run experiments in your agentic codebase to see how to improve it.
