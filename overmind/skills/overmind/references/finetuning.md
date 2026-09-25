# Fine-tuning and serving

SFT one or more catalog base models on a chosen **train** cell that fits, keep
a held-out **eval** cell for judges, then deploy
and compare the result with its untouched base and the capability's selected benchmark.

Training dataset: intent **train** (`messages` with an assistant turn on
every row — LLM-in → LLM-out, not agent-level rows). Eval dataset: intent
**eval**; use an independent source or a shared source split. Report normalized
input, trace/conversation, configured-group and synthetic-seed overlap as warnings;
do not block progression or silently exclude rows. Successful job creation atomically freezes the
selected train, validation and eval versions. The eval version is pinned for the
matched before/after evaluations; a rejected launch does not freeze new versions.
See
[SKILL.md](../SKILL.md#dataset-contracts--read-first-they-gate-every-workflow)
and [datasets.md](datasets.md).

Use the native `finetune-capability` prompt to prepare and start training.
Fine-tuning is a project-scoped write flow with GPU cost; ask before starting
spend.

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
   dataset and an eval set with generative evaluators are still required.
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
All checks can be disabled, but the eval dataset and eval set are still
required. Baseline evaluations launch concurrently with training and do not gate
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

Fine-tuning queues deployment automatically after training succeeds. Follow
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
ready deployment or clear the active model. Verify the final state from the
deployment and capability resources.

Inspect `finish_reason` and `truncated` before using an inference answer. `truncated` means the model stopped at its token limit; `content_clipped` separately indicates the MCP response size bound. Neither is a complete response.

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
