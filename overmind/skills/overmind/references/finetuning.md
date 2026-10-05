# Fine-tuning and serving

SFT one or more catalog base models on a chosen **train** cell that fits, keep
a held-out **eval** cell for judges, then deploy
and compare the result with its untouched base and the capability's selected benchmark.

Training consumes an explicit **train** cell: conversational messages with assistant
responses, or native decisions with preserved probability/ordinal targets. Chat
judging uses a separate **eval** cell and eval set only when selected. Native
probability models use a separate frozen native comparison; they do not enter
chat deployment or activation. Capability and repository scanning are optional.

For uploaded-data experiments, follow `develop-model-from-data` and
[model workflows](model-workflows.md): explore, prepare and freeze partitions,
save comparison/training drafts, prepare, then launch the authorized configuration.
Poll the saved receipts rather than recreating work. Wait for Workshop idle and
use exact cell UUIDs for consumer handoffs.

For ordinary conversational fine-tuning use the `finetune-capability` prompt.
Paid preparation and launches require user authorization; existing authorization
persists across the stages it covers.

## From stored LLM calls

`overmind finetune` builds a train dataset and an eval dataset from `llm_call`
spans (one row per call, hash split) and starts one job per model. It prints
the job ids and does not wait for training. The application is not run.

```bash
overmind finetune --models Qwen/Qwen2.5-7B-Instruct,meta-llama/Llama-3.1-8B-Instruct --since 7d
```

Pass 1–4 catalog ids. The jobs share one `group_id`, the capability's active
eval set, and the same train and eval cells. A model that is not in
`GET /api/finetuning-jobs/models/` is refused before any dataset is created.

## Prepare and start

1. Call `get_model_catalog` first to browse dataset-independent candidates and
   their backend, tier, context, training-method, tool-calling, and batch
   metadata.
1. Call `list_datasets`, inspect each candidate by UUID, and use
   `query_dataset` to verify a **train** cell and a separate **eval** cell.
   Report workshop findings for task alignment, input evidence, answer support,
   output schema and mismatched capability bindings. These are advisory; users may
   continue with remaining work. Send requested repairs back to the workshop;
   training never converts worker targets into orchestrator targets.
1. Call `check_finetune_readiness` after selecting the training dataset/cell.
   Capability is optional; omit it or pass null for no association. An eval
   dataset and eval set are required only for selected chat evaluations; native
   decisions use their own frozen suite plan.
   It may narrow or recommend candidates;
   use its `catalog` and `recommendations` for `base_model` values and fix
   every reported missing prerequisite.
   Ranking uses the selected capability's codebase task; with no capability,
   it uses the dataset's task. `task_type_source` identifies capability context,
   dataset semantic analysis, dataset heuristics, or an unknown capability task.
   Uncached capability classification may call an LLM. Unknown capability tasks
   leave models ungraded rather than substituting the dataset's task.
1. Call `estimate_finetune` for each approved base model when cost or duration
   matters.
1. For Modal, after selecting the model and context, call `prepare_training_data`
   with the dataset/cell, `base_model`, `context_length`, `training_type` (`lora`
   or `full`) and any separate validation dataset. This is a CPU compute operation;
   include it in spend approval. Poll the returned receipt with
   `get_job(kind="training_preparation", id=...)`. Inspect exact token counts,
   supervised content and incompatible source-row IDs. Do not start until ready.
   Request concrete repairs in the dataset's chat, then prepare the revised version
   again. The workshop has no model selector. Training consumes the validated token
   artifact; it does not silently truncate or drop incompatible rows.
1. After human approval, call `start_finetune`. It validates the dataset,
   evaluation set, validation split, model, credits, quota, and dispatches the
   job. A multi-model sweep uses one call per base model and can share the
   returned `group_id`.

Omit `hyperparameters` to derive model-specific defaults. When supplying them,
`training_type` must be an object such as `{"type": "Lora", "lora_r": 16}`
or `{"type": "Full"}`. A string such as `"Lora"` is rejected before job creation.

Preparation is cached by data and configuration. A confirmed failed operation whose
report has `retryable=true` can be retried with `retry_failed=true`; its saved remote
call is cancelled first. An unacknowledged submission fails closed, not retried blindly.
The Console's training-job Retry action also retries a confirmed-failed preparation
when GPU training has not been submitted. It reuses ready or in-flight preparation
and refuses unsafe or unresolved incompatible retries. Job preparation sizes context
from the pinned train/validation data and can recheck an exact length overflow at a
larger supported context without changing the rows. Model limits and other technical
incompatibilities still block GPU training. MCP preparation retries continue to use
`prepare_training_data(retry_failed=true)`; do not invent a training-job retry tool.
The matching Modal worker must be deployed; a processor fingerprint mismatch means
the worker is out of date. Non-Modal providers do not currently expose this exact
preflight artifact workflow.

Include the evaluation schedule in the approval: `eval_incumbent_before`,
`eval_incumbent_after`, `eval_model_before`, and `eval_model_after`. The training
model's before evaluation uses its untouched base, and after uses the trained
checkpoint. Defaults are untouched-base-before and trained-after, giving a matched
comparison. Incumbent checks are separate opt-ins and require a configured
benchmark. Pass `baseline_model` to `start_finetune` to select the codebase incumbent
or a ready trained serving model ID in this project for this run. The Console exposes
this as **Benchmark model** in training setup. The choice is saved on the job and
does not change live routing or capability defaults. If omitted, the capability's
`benchmark_model` supplies the default, falling back to its codebase incumbent;
`set_benchmark_model` changes that API default only. Existing jobs retain their selection.
All chat checks can be disabled; no chat eval dataset or eval set is then required. Baseline evaluations launch concurrently with training and do not gate
training submission. The fine-tune resource exposes the saved `evaluation_plan`.
Before/after runs share a pinned dataset version, a prompt snapshot and evaluator
snapshots. Every selected evaluation runs every row of that version, without
sampling or a row cap. Include the full dataset in evaluation cost approval.
These are model evaluations using recorded evidence, not live application runs.

Before evaluations of the untouched starting model prefer OpenRouter when its key
is configured and the live cached catalog contains an exact model or Hugging Face
checkpoint match. No baseline inference deployment is created for that route.
Missing models, credentials, or catalog availability use the existing training-provider
or Modal inference route. The chosen route stays fixed for the evaluation; trained
checkpoints still use their own deployment. Include evaluation API costs in run approval.

The job resource is `overmind://finetunes/{job_id}`. Poll with
`get_job(kind="finetune_job", id=...)`. Fine-tune statuses are `queued`,
`preparing`, `running`, `deploying`, `succeeded`, `failed`, and `cancelled`.

Serving context is sized separately from training sequence length. Readiness recommendations include an estimated serving context and reserved output budget for the evaluation workload; launch rechecks the pinned eval version. Models that cannot accommodate that budget are excluded with a reason. A token-limited evaluation is incomplete, not a low quality score: inspect its degraded samples before comparing models. Increasing serving capacity does not require retraining.

Without a capability, evaluation uses the untouched base model as its baseline.
Eval sets without a capability can be used by any training job in the project.

## Deploy, verify, activate

Conversational fine-tuning queues deployment automatically after training succeeds; native decision checkpoints remain probability artifacts. Follow
the linked deployment with `get_job(kind="deployment", id=...)` and read
`overmind://deployments/{deployment}` while it moves through `queued`,
`quantizing`, `deploying`, `warming`, `ready`, or `failed`. Use
`retry_deployment` only for a failed or deleted deployment with a linked
successful or deploying fine-tuning job; it accepts the deployment UUID or
serving model id and durably queues a new deployment generation. Progress includes
the stage, attempt, retry time, deadline, and last error. Worker restarts reconnect
to the same remote operation. Confirmed failures retry at most twice, after 30 and
60 seconds, within a four-hour deadline. A lost submission acknowledgement stops
automatic retries until the remote operation is resolved; do not bypass that guard.
Baseline preparation reports its stage on the training job while both proceed.
A failed baseline marks only its dependent evaluation failed, including when remote
cancellation still needs confirmation.
Deployment failure preserves the successful training checkpoint and marks the
dependent evaluation failed. Retrying deployment does not retrain the model.

Call `run_inference` only against a `ready` deployment and treat `is_cold` as
normal first-request information. Then use `set_active_model` to activate a
ready deployment or clear the active model. Activation verifies inference before changing routing; poll the returned `model_activation` job until complete. A failed activation preserves the current model and can be retried with the same selection. Verify the final state from the deployment and capability resources.

Inspect `finish_reason` and `truncated` before using an inference answer. `truncated` means the model stopped at its token limit; `content_clipped` separately indicates the MCP response size bound. Neither is a complete response.

`run_inference` uses the production serving output reservation when `max_tokens` is omitted or null (currently 8,192 tokens). Explicit positive integer budgets have no MCP-specific ceiling. The server validates the templated input plus reserved output against the deployment's context; it never silently reduces the budget. A `context_length_exceeded` error is not retryable without changing the request or serving context. The returned `content` remains bounded to 32,000 characters, independently of generation length; `content_clipped` reports that response limit.

## Download an archived checkpoint

Use the native `download-checkpoint` prompt or read
`overmind://checkpoint-download` for the local CLI boundary. First resolve and
read `overmind://deployments/{deployment}` through MCP, then run
`overmind model download-checkpoint DEPLOYMENT --json` on the coding agent's
filesystem with the deployment id supplied by MCP. The CLI authenticates with
`X-Api-Key` from `--api-key`, the saved local project credential, or
`OVERMIND_API_KEY`, and uses
the base URL from `OVERMIND_API_URL`, `--api-url`, or `overmind.toml`.

Only archived checkpoints for `baseten` and `modal` fine-tune providers are
downloadable. Deployments without a fine-tuning job, unsupported providers,
and archives that are not ready are unavailable. The presigned S3 URL stays
inside the server/CLI flow and must not enter model context. Parse the JSON
result and report its local `path` and `bytes_written`; the CLI refuses to
overwrite an existing file.

## Repository rollout

`get_model_swap_prompt` accepts a successful fine-tune and optional `pin`. It
returns a copy-paste `prompt` plus `capability_id`, `old_model`, and
`new_model`. Apply that prompt in the repository yourself. Omit `pin` to point
the code at the capability alias, so later swaps need no further code change.
MCP does not edit the repository.

There are no public cancel or undeploy tools. Do not suggest them.

### Live inference performance

Read `overmind://deployments/{deployment}?period=24h&source=application` for application request counts, failures, end-to-end response percentiles, warm generation speed, estimated cost and matching activity. Supported periods are `1h`, `24h`, `7d`, `30d` and `all`; source is `application` or `all`. Both default to `all`. Internal evaluations and historical calls with unknown source are included only in `all`.

The resource's `worker` section reports current `state` (`warm`, `warming`, `asleep`, or `unknown`), measurement `available`, runner/input/backlog counts, and recent-activity/warming signals. It uses the Console's cached worker measurements without running inference. `status` remains deployment readiness; capability `active_model` remains the routing selection. Metrics filters do not filter current worker state. Missing measurements remain null rather than zero; recent successful traffic can still establish warmth when provider measurements are unavailable.

Forecasts distinguish live GPU-hour pricing from duration evidence. When no compatible execution measurement exists, duration and total cost remain unknown while the available hourly rate and rejection reasons stay visible. Each quote uses one fetched rate snapshot. Report elapsed time and any supported remaining-time range, never an invented completion timestamp. Recorded charges, resource-based estimates and unreported components remain separate.

## Native pre-training baseline

`initial_validation` is the pre-training baseline evaluation on development data.
Set `hyperparameters.pre_training_baseline=false` to omit it when launching native
decision training or saving an experiment variant. This strict boolean defaults to
true. The Console exposes “Run pre-training baseline evaluation” beside the native
training setup. Disabling it preserves the selected validation data, development
checkpoint selection, final validation and separately configured benchmarks.
Skipped baselines report `not_requested`; a missing starting score is not zero.
Existing launch keys and resume checkpoints cannot change this choice.
