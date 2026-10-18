---
name: overmind-training
description: Select models, prepare exact training inputs, estimate costs, launch and inspect Overmind fine-tuning jobs. Use for the Training surface, including held-out evaluation and benchmark selection; live model activation belongs to Inference. A job needs a dataset and confirmed eval metrics before any paid step.
---

# Overmind Training

For a request spanning source transformation through training launch and comparison,
start with the `model-training-engineering` skill, then use this skill for Training.

Start with `list_projects` and choose the intended accessible project. For an
account connection, pass its `project_id` on every project tool and resource
URI query; follow returned links. Project API keys retain their narrower access.

Produce or inspect a trained model with an explicit data, evaluation and cost
contract. Use the chosen MCP project. For an existing job, start with
`overmind://finetunes/{job_id}` and its `finetune_job` status; do not relaunch it.

## Prepare the training contract

For data-first development, use `save_training_plan` and the evidence-linked
procedure in [Develop models from data](#develop-models-from-data) below.

Training consumes an explicit **train** cell: conversational messages with assistant
responses, or native decisions with preserved probability/ordinal targets. Chat
judging uses a separate **eval** cell and eval set only when selected. Native
probability models use a separate frozen native comparison; they do not enter
chat deployment or activation. Capability and repository scanning are optional.
Paid preparation and launches require user authorization; existing authorization
persists across the stages it covers. For uploaded-data experiments, explore,
prepare and freeze partitions, save comparison/training drafts, prepare, then launch
the authorized configuration. Poll the saved receipts rather than recreating work.
Wait for Workshop idle and use exact cell UUIDs for consumer handoffs.

### Prerequisites

A fine-tuning job needs both of these before any paid preparation, estimate or
`start_finetune`; if either is missing, stop and resolve it first:

1. **A dataset.** A project dataset with a readable **train** cell, resolved with
   `list_datasets`. Without one, send the user to the
   [Datasets skill](../overmind-datasets/SKILL.md) or
   `overmind finetune` (stored LLM calls) to create it.
1. **Configured eval metrics.** An eval set whose rows cover what the job trains
   for, confirmed by the user ([Confirm the metrics](#confirm-the-metrics)). Native
   decision training is exempt from the metrics: it uses its own frozen native suite
   plan, but still needs its dataset.

Prefer the native `finetune-capability` prompt with the selected dataset and
optional capability. Resolve datasets with `list_datasets`; inspect and query
the exact train and held-out eval cells. Inspect whole-frame task families and
workshop findings rather than assuming a sample describes the whole corpus.

Use `get_model_catalog` for initial model discovery (it is dataset-independent and
reports the active backend, tier, context limits, batch bounds, training methods,
tool-calling support and disabled rows), then
`check_finetune_readiness` for the selected dataset. A capability is optional. A chat job always needs its confirmed eval set; held-out evaluation data is required for the before/after evaluations the user selects. Native decision training has a separate native suite plan. Use the
returned requirements to identify technical blockers. Quality findings,
overlap and incomplete semantic reviews remain visible advisory warnings. Report
Workshop findings for task alignment, input evidence, answer support, output
schema and mismatched capability bindings; users may continue with remaining work.
Send requested repairs back to the Workshop; training never converts worker targets
into orchestrator targets.

Readiness may narrow or recommend candidates: take `base_model` values from its
`catalog` and `recommendations` and fix every reported missing prerequisite.
Ranking uses the selected capability's codebase task, or the dataset's task with no
capability; `task_type_source` identifies capability context, dataset semantic
analysis, dataset heuristics, or an unknown capability task. Uncached capability
classification may call an LLM. An unknown capability task leaves models ungraded
rather than substituting the dataset's task.

## Train from stored LLM calls

`overmind finetune` builds a train dataset and an eval dataset from `llm_call`
spans (one row per call, hash split) and starts one job per model. It prints the
job ids and does not wait for training. The application is not run.

```bash
overmind finetune --models Qwen/Qwen2.5-7B-Instruct,meta-llama/Llama-3.1-8B-Instruct --since 7d
```

Pass 1–4 catalog ids. The jobs share one `group_id`, the capability's active eval
set, and the same train and eval cells. A model that is not in
`GET /api/finetuning-jobs/models/` is refused before any dataset is created.

For typed decision training, call `list_decision_training_models` to see the explicit Clef, D3 and D1 families and their current launch availability. D3 and D1 are discoverable but unavailable until exact preparation and verified probability serving are qualified. Use `start_decision_job` for an available family and a stable `request_key`; it returns a separate `decision_job` ID and the underlying training run. Both IDs refer to the same preparation, monitoring, cancellation, billing and recovery lifecycle. `prepare_training_data` also returns the DecisionJob ID for an applicable setup run. Inspect it with `get_job(kind="decision_job")` or the linked resource, and pass either ID to `inspect_training_progress` and `cancel_finetune`.

The model catalogue returns `openrouter_id` and `openrouter_status` for base
benchmarks. Only `available` establishes a catalogue match; null IDs, `not_listed`
and `catalog_unavailable` do not. Never substitute a different model size or variant.
Overmind resolves the exact route; trained checkpoints retain their own hosting.

Read `benchmark_model` on the capability when comparing with an incumbent.
`set_benchmark_model` changes future benchmark selection, not live serving or
existing jobs. Use it only for a requested benchmark change. New jobs pin their
benchmark selection and readable dataset versions.

## Confirm the metrics

Before any paid preparation or `start_finetune` for a chat (non-decision) model,
the Console must hold eval metrics that cover the job, and the user must have
confirmed them. Create the metrics first when none exist, when they do not cover
what the job trains for, or when the dataset uses eval metrics that are not defined
in the Console. In the confirmation, state that eval metrics are a prerequisite for
fine-tuning. Native decision training is exempt: it uses its own frozen native suite
plan.

Metrics are the rows of one eval set. Show them once, as a list of each row's name,
type (`llm` sentence or `deterministic` check) and what it grades, then ask for
confirmation. One confirmation covers a multi-model sweep of the same data. Do not
start until the user answers; a change request restarts this section for the
changed rows only.

- **Repository scan.** The capability's active eval set already holds the metrics.
  Read `overmind://capabilities/{capability}` for `eval_preload`, then page
  `overmind://eval-sets/{eval_set}` (`limit` up to 100, follow `next_offset` until
  null) so the list is complete. Show them as they are and ask for confirmation.
  Do not recreate the set. When it leaves a gap (see above), draft only the missing
  rows as in the uploaded-dataset steps below, confirm them, and add them with
  `create_eval_matrix_row` for the capability.
- **Uploaded dataset only.** There is no active set and the dataset carries no
  metrics. Do not launch without creating them:
  1. Inspect at least the first 20 rows with `query_dataset` on the exact train
     cell, and more when the first rows do not show the task's variety. Report
     the task, input shape, answer shape and failure modes you observed. Use
     `inspect_dataset` for whole-frame profile facts rather than assuming the
     sample describes the corpus.
  1. Draft metrics that fit what the rows show: one checkable sentence per `llm`
     row about the answer, and a `deterministic` row only for a rule the rows
     support (valid JSON, required keys, exact label set). Keep `span_type` blank
     or `llm_call`: over a dataset only those rows score the answer, and tool,
     timing and cost checks abstain. Never invent a rule the sampled rows do not
     support or put row values or secrets in a sentence.
  1. Show the drafted metrics with the evidence rows that motivated each one and
     ask for confirmation. Create nothing before the user answers.
  1. After confirmation, call `create_eval_matrix_row` per metric with no
     `capability`, then `create_eval_set` with those row ids and no capability.
     Pass the set as `eval_set` to `check_finetune_readiness`, `estimate_finetune`
     and `start_finetune`. Rows cannot be edited: `delete_eval_matrix_row`, then
     create the replacement.

Held-out eval data is still required for the chat evaluations the user selects;
confirming metrics does not replace it, and held-out data does not replace metrics.

## Size and price the run

For Modal training, call `prepare_training_data` for the chosen model, context
length and cell, then poll the returned `training_preparation` job. Inspect
exact token counts, supervised content and incompatible rows. Send concrete
source-row repairs through native-agent-authored pipelines or lineage-bound
`import_dataset_version` results; keep model-specific preprocessing
in Training. Reprepare a changed cell rather than reusing a stale report. Do not
start until preparation is ready: Training consumes the validated token artifact and
never silently truncates or drops incompatible rows. This is a CPU compute
operation; include it in spend approval. Pass `training_type` (`lora` or `full`) and
any separate validation dataset.

Preparation is cached by data and configuration. A confirmed failed operation whose
report has `retryable=true` can be retried with `retry_failed=true`; its saved remote
call is cancelled first. An unacknowledged submission fails closed, not retried
blindly. The Console's training-job Retry action also retries a confirmed-failed
preparation when GPU training has not been submitted; it reuses ready or in-flight
preparation and refuses unsafe or unresolved incompatible retries. Job preparation
sizes context from the pinned train/validation data and can recheck an exact length
overflow at a larger supported context without changing the rows. Model limits and
other technical incompatibilities still block GPU training. MCP retries continue to
use `prepare_training_data(retry_failed=true)`; do not invent a training-job retry
tool. The matching Modal worker must be deployed; a processor fingerprint mismatch
means the worker is out of date. Non-Modal providers do not currently expose this
exact preflight artifact workflow.

Omit `hyperparameters` to derive model-specific defaults. When supplying them,
`training_type` must be an object such as `{"type": "Lora", "lora_r": 16}` or
`{"type": "Full"}`; a string such as `"Lora"` is rejected before job creation.

Forecasts distinguish live GPU-hour pricing from duration evidence. With no
compatible execution measurement, duration and total cost stay unknown while the
available hourly rate and rejection reasons stay visible. Each quote uses one
fetched rate snapshot. Report elapsed time and any supported remaining-time range,
never an invented completion timestamp. Recorded charges, resource-based estimates
and unreported components remain separate.

Use `estimate_finetune` and evaluation readiness to present training plus
before/after evaluation spend. Each selected evaluation uses the full pinned
eval dataset. Context warnings do not automatically block launch or change
model selection. An approved preview's `judge_model` becomes
`eval_judge_model` on `start_finetune`; omission preserves the set's judges.

For Modal/Baseten, `batch_size` means the effective optimizer batch. The runtime
derives micro-batching and accumulation; `gradient_accumulation_steps` and
`per_device_train_batch_size` are rejected. Native decision readiness supplies
eligible models, not a chat-benchmark ranking or an unmeasured price promise.

## Launch and verify

For native decision families, discover `list_decision_training_models` and launch
with `start_decision_job`. Use `start_finetune` for chat models. Native decisions
keep the four chat evaluation flags false and preserve the declared probability
objective; both tools share the same durable training lifecycle.

Freeze `hyperparameters.monitoring` with the rest of the recipe. Modal and Baseten
support loss monitoring with adaptive/steps/epoch/off schedules; discover current
provider capabilities from the model catalogue. Modal also accepts explicit
classification labels, exact matching, JSON Schema and `json_fields` generation probes. The
agent supplies task semantics; the platform never invents labels or a success
rubric. Metered judges, challenge suites and declared-strata sampling are not
available in this monitoring contract. Native decisions use their typed metrics.

For extraction checks, declare `generation={kind:"json_fields", fields:["/risk", "/case/id"]}`.
Fields are 1–64 distinct JSON Pointers (empty string means the whole document);
escape `/` as `~1` and `~` as `~0`. Nested objects and ordered arrays compare
exactly; undeclared fields are ignored. This is separate from schema validity,
not a schema/field hybrid. Missing or invalid references are unscorable, while
missing/wrong predictions fail. Inspect per-field coverage and complete-example
pass rates separately. The scorer never infers fields or executes tools.

Default monitoring uses initial, fixed-sample periodic and full final development
checks. Samples freeze during Modal transfer before GPU dispatch, not during
token preparation. Adaptive scheduling changes timing only; the overhead target
is not a spend cap. Set `selection=development_loss` or `early_stopping` only when
the user authorises that decision rule. Required checks fail closed. For a bounded
Modal call, `runtime_limit_seconds` sets a hard provider timeout without changing
optimizer steps; CPU preparation, storage and other costs remain separate.

Use `inspect_training_progress(job=..., check=..., offset=..., limit=...)` for
retained examples or `probe=development|training_reference|generation` for sample
identities. With neither selector, it pages checks and reports checkpoints and
current schedule. Reads never invoke the provider. Missing/failed measurements,
unrepresented labels and generation truncation are not zero-quality scores.
Paired intervals describe development evidence, not final generalisation.
The compact `get_job` summary retains `latest_generation_check` separately from
newer loss-only checks. Classification `facts.assessment` records comparisons
with the majority-label baseline on the same scored examples and represented
labels with no predictions. Inspect coverage, receipt identity and observation
time: these are sample facts, not a diagnosis, population estimate or stop rule.
Invalid or incomplete counts produce an inconclusive assessment, not a quality claim.
`cancel_finetune` records cancellation before requesting provider termination;
already retained evidence remains readable. Adapter reload verification does not
establish full optimizer resume. Intermediate checkpoint byte export remains
unavailable; the existing download handoff covers archived final deployments.

Launch only the authorized model/configuration with `start_finetune`. Keep the
selected data, benchmark, judge choice and expected spend in the receipt.
Poll `get_job(kind=finetune_job, id=...)`, then inspect the linked deployment
separately. Training completion, evaluation completion and serving readiness
are distinct outcomes. The job resource is `overmind://finetunes/{job_id}`;
statuses are `queued`, `preparing`, `running`, `deploying`, `succeeded`, `failed`
and `cancelled`. `start_finetune` validates the dataset, evaluation set, validation
split, model, credits and quota, then dispatches the job. A multi-model sweep uses
one call per base model and can share the returned `group_id`.

### Evaluation schedule

Include the evaluation schedule in the approval: `eval_incumbent_before`,
`eval_incumbent_after`, `eval_model_before` and `eval_model_after`. The training
model's before evaluation uses its untouched base and after uses the trained
checkpoint. Defaults are untouched-base-before and trained-after, a matched
comparison. Incumbent checks are separate opt-ins and require a configured
benchmark. Pass `baseline_model` to `start_finetune` to select the codebase
incumbent or a ready trained serving model ID in this project for this run. The
Console exposes this as **Benchmark model** in training setup. The choice is saved
on the job and does not change live routing or capability defaults. If omitted, the
capability's `benchmark_model` supplies the default, falling back to its codebase
incumbent; `set_benchmark_model` changes that API default only. Existing jobs retain
their selection. Without a capability, evaluation uses the untouched base model as
its baseline, and eval sets without a capability can be used by any training job in
the project.

Chat before/after checks can be disabled; no held-out eval dataset is then required,
but the confirmed eval set still is.
Baseline evaluations launch concurrently with training and do not gate training
submission. The fine-tune resource exposes the saved `evaluation_plan`. Before/after
runs share a pinned dataset version, a prompt snapshot and one frozen eval matrix
snapshot, in one run group. Every selected evaluation runs every row of that
version, without sampling or a row cap, so include the full dataset in evaluation
cost approval. These are model evaluations using recorded evidence, not live
application runs.

Before evaluations of the untouched starting model prefer OpenRouter when its key is
configured and the live cached catalogue contains an exact model or Hugging Face
checkpoint match. No baseline inference deployment is created for that route.
Missing models, credentials or catalogue availability use the existing
training-provider or Modal inference route. The chosen route stays fixed for the
evaluation; trained checkpoints still use their own deployment. Include evaluation
API costs in run approval. Baseline preparation reports its stage on the training
job while both proceed. A failed baseline marks only its dependent evaluation
failed, including when remote cancellation still needs confirmation.

Serving context is sized separately from training sequence length. Readiness
recommendations include an estimated serving context and reserved output budget for
the evaluation workload; launch rechecks the pinned eval version. Models that cannot
accommodate that budget are excluded with a reason. A token-limited evaluation is
incomplete, not a low quality score: inspect its degraded samples before comparing
models. Increasing serving capacity does not require retraining.

Native `initial_validation` is presented as **Pre-training baseline evaluation**.
It measures the starting model on the development set before training begins;
it is separate from data preparation and the held-out final benchmark.
MCP progress includes `stage_label` and `stage_description` for this stage. Set `hyperparameters.pre_training_baseline=false` on a native launch or saved experiment variant to skip this pass. It defaults to true and does not disable development validation, checkpoint selection or separately scheduled final benchmarks. The Console exposes “Run pre-training baseline evaluation” beside the native training setup. The choice is frozen in the recipe, existing launch keys and resume checkpoints cannot change it, and skipped work is recorded as `not_requested`; a missing starting score is not zero.

Report measured before/after results with trust flags and any unresolved
preparation findings. A successful training job does not authorize activation
or a repository model swap; deployment, verification, activation and rollout
belong to the [Inference skill](../overmind-inference/SKILL.md). Conversational
fine-tuning queues deployment automatically after training succeeds; native
decision checkpoints remain probability artifacts. There is no public undeploy
tool; do not suggest one. `cancel_finetune` cancels a training job.

### Download an archived checkpoint

Use the native `download-checkpoint` prompt or read `overmind://checkpoint-download`
for the local CLI boundary. First resolve and read `overmind://deployments/{deployment}`
through MCP, then run `overmind model download-checkpoint DEPLOYMENT --json` on the
coding agent's filesystem with the deployment id supplied by MCP. The CLI
authenticates with `X-Api-Key` from `--api-key`, the saved local project credential
or `OVERMIND_API_KEY`, and uses the base URL from `OVERMIND_API_URL`, `--api-url` or
`overmind.toml`. Only archived checkpoints for `baseten` and `modal` fine-tune
providers are downloadable. Deployments without a fine-tuning job, unsupported
providers and archives that are not ready are unavailable. The presigned S3 URL
stays inside the server/CLI flow and must not enter model context. Parse the JSON
result and report its local `path` and `bytes_written`; the CLI refuses to overwrite
an existing file.

Open `training` under the project's Console base with its `projectId` for the
visual job dashboard. Retain the exact job and deployment IDs in the report.

## Durable experiment receipts

Pass the same explicit train/validation/evaluation cell IDs and evaluation choices to readiness, estimate and launch. Supply one stable `request_key` per intended experiment and reuse it after an uncertain client response. A changed recipe needs a new deliberate experiment; do not work around a conflict by generating another key automatically. Inspect the job's requested/effective settings and run record. Unresolved provider submission must be reconciled, never relaunched.

For a native decision checkpoint, use `schedule_native_evaluation` with separate frozen calibration and final decision cells. This authorizes paid paired unchanged-base/candidate evaluation after verified checkpoint completion. Poll `get_job(kind=native_evaluation)` and follow its links. References stay local; known incompatible inputs remain visible in coverage. Calibration is frozen before final results. Report raw and calibrated metrics, source slices, paired confidence intervals and regressions separately from training loss.

An unknown native forecast is not a zero-dollar quote. GPU ledger spend excludes unreported preparation, CPU/memory, evaluation and storage. Compare measured work and forecast ranges without describing them as a spending cap. Native checkpoints are probability artifacts and do not enter chat deployment or activation.

## Develop models from data

For saved partitions, standalone decision comparisons, explicit training candidates or reproducible performance workloads, use the `develop-model-from-data` prompt and this section. The native coding agent interprets targets from evidence before consumers enforce their declared meaning. These workflows do not require a repository or activate a model. Creation is not paid launch; saved scope and receipts control continuation.

### Evidence-linked training plans

Use `save_training_plan` from source discovery for generative or decision tasks.
Retain prediction unit, target meaning, allowed inputs and availability evidence,
connected grouping fields, slices and success criterion. `primary_metric` is a
JSON Pointer into development-check metrics, such as `/hard_label_accuracy`,
`/expected_score_mae` or `/generation/accuracy`. Unknown semantics stay explicit.

Save a superseding snapshot after retained Workshop preparation and partitioning,
with the exact `prepared_cell` and `partition`. Audit contradictions, post-outcome
features, connected-case leakage and coverage. Retain audit, baseline and fitting
sanity evidence with methods, measurements and limitations. Baseline and sanity
reports reference train/development cells only. These reports are agent-attributed,
not independently verified semantic certification.

Create a full experiment directly with `training_plan=ID`, `stage="full"`, the
plan's train/development cells and the requested recipe. Full training requires
attributed audit, baseline and sanity evidence, but no pilot job or measured
pilot selection. Do not create or reuse a pilot merely to authorize a full run.

Only when the user requests a bounded quality experiment, use `stage="pilot"`,
explicit `max_steps` from 1–2048 and `monitoring.selection="development_loss"`.
Measure the declared task outcome as well as loss; generative tasks need the
corresponding generation probe. Hardware profiles are separate evidence.

Pilot-derived selection is optional. To retain one, save a snapshot with
`selection.check`, rationale and permitted `budget_fields` (`max_steps`,
`n_epochs`, `runtime_limit_seconds`). It requires a completed, fully scored
matching pilot development check and verified checkpoint. When supplied, full
experiments preserve that selected recipe except for declared budget changes.
An unrelated pilot is not evidence for the current task/data/partition.
Earlier snapshots remain reproducible, and launch receipts retain the exact
plan binding.

Use the frozen final role for native probability comparison or generative
evaluation, with calibration separate. Never use final results for recipe
selection. Forecast preparation remains separate from quality evidence. Discover
plans with `list_model_workflows` and `get_job(kind="training_plan")`. Direct
exploratory training remains available; no universal semantic-approval gate is added.

Read `overmind://interface/current` for the connected contract, then use the native `develop-model-from-data` prompt. A project can start in Console onboarding with **From data**, then upload through Data Workshop or `overmind dataset upload`. No repository scan or capability is required. Keep the original written task with the dataset.

The native coding agent explores the whole source and task families before authoring immutable transformation scripts. Unknown target meaning stays unknown. Preserve full probabilities and mean-only ordinal targets; numeric shape is not evidence of annotator votes or reference truth. Record semantic changes and supporting evidence in the retained recipe and review metadata. Workshop executes scripts and retains facts; it has no planning or generation agent.

`explore_dataset` saves a bounded background profile of an explicit `source_cell`. Optional sampling settings report feasibility, minimum rows and paginated stratum allocations through its resource. Profiles cover the whole source; examples remain bounded. `derive_dataset` creates a separate chain from a readable historical cell, preserves every row and its lineage, and does not move the parent's active version. Repeating an identical failed exploration request retries local work; changed requests need new keys.

The native coding agent owns interpretation. Save target meaning as a hypothesis, supported interpretation or conflict, with scoped source references, observed rows and unresolved evidence. Numeric shape or a string labelled evidence is not proof. No mandatory row-by-row LLM review is added.

Use `create_data_partition` with `source_cell`, `name`, `request_key`, and a recipe containing `seed`, `fractions` keyed by train/development/calibration/final, `group_by`, optional `stratify_by`, and optional `holdouts` (`field`, `values`, `role`). Inspect `get_job(kind="data_partition")` for counts, grouping, assignment hashes and member cells. Prepare each member for its consumer. Retry a failed saved partition with `retry_data_partition`.

`list_decision_models` returns eligible foundations, ready native artifacts and supported external participants. Eligibility is not proof of hardware fit or quality. `create_native_evaluation` freezes `final_cell`, optional `calibration_cell`, `participants` and the `baseline` key. `create_native_evaluation` saves a draft without inference. `prepare_native_evaluation` verifies immutable suites in background; `launch_native_evaluation` authorizes the saved participants and suites once. A participant has a key, name, kind and model ID, or a trained job UUID. Input references stay separate; one global temperature is fitted on the calibration suite before final scoring. Mean-only suites omit probability temperature calibration. Inspect `get_job(kind="native_evaluation")` and the returned resource for every benchmark, denominator, failure, raw/calibrated metric and paired interval. Calibration scores are in-sample; overlap and pretraining independence limitations remain explicit.

`create_training_experiment` saves a name, purpose, request key, explicit variants and optional evaluation protocol UUID. Each variant declares `base_model`, train `cell`, optional `development_cell`, and `hyperparameters` (for example `n_epochs`, `learning_rate`, `lora_r`, `seed`). A `checkpoint_policy` declares fractions ending at 1 and selection `last` or `development_loss`. Final scores never choose a checkpoint. Run `prepare_training_experiment` to verify sources and obtain a forecast bound to the frozen configuration. Inspect it before `launch_training_experiment`; pass its quote ID when user constraints require it. An optional `constraints.max_training_run_usd` and explicit `unpriced_allowance_usd` enforce the user's planning policy; there is no default spend cap or claim of provider all-in enforcement. Unknown costs stay unknown. `create_training_profile` saves one bounded candidate (1–128 steps, 60–3600 seconds per provider attempt, no automatic provider retries), then uses the same preparation and launch path. Native profiles sample representative length quantiles, retain labels and record their measurement scope; profile weights are not quality evidence. Requested/effective conditions and seed variation are recorded separately from evaluation uncertainty.

`measure_decision_performance` runs a paid saved workload after the required model identity/runtime is qualified; final quality scoring need not be complete. Workload fields are `sample_size` (1–64), `repetitions` (1–10), `concurrency` and `questions_per_request` (1–16), `seed` and optional `amortization_decisions`. It measures client time through complete responses, valid throughput and recorded costs. Native first-request startup, warm worker identity and provider-controlled caches remain distinct. Missing billing components and recovered calls with unknown original latency stay unknown.

Use `list_model_workflows` to discover saved work; `get_job` and its resource carry workflow state. Native evaluation results are compact summaries with coverage computed across every benchmark. Download `report.json_path` or `report.markdown_path` from the same server with the existing API credential for complete per-benchmark metrics, paired intervals and diagnostics; do not treat summary macros as individual benchmark scores. Calibration results remain marked in-sample. Transient evaluation interruptions recover automatically with bounded backoff; poll running plans. Saved native calls and completed responses are reused. Missing external predictions may retry after a recorded transport failure, at most three attempts per input, retaining unknown prior costs. Bare submission intents remain unresolved. Resume stopped comparisons or performance runs with their dedicated resume tools. Provider-reported versions do not independently verify weights. Recorded GPU/API usage is not an all-in invoice. None of these operations activates a model.

Independent eligible participant stages run concurrently while preserving calibration and final dependencies. Chunk continuation is immediate and periodic reconciliation repairs missed work. Source manifests bind immutable chunk offsets; complete results retain denominators, failures and paired uncertainty. Native checkpoints that fail stop only dependent work. Missing acknowledgements remain unknown.

Exhausted collection completes scoring on verified available predictions and sets
`results.completion.status=completed_with_issues`. Inspect its per-suite/per-arm
coverage and issues. Missing predictions are not zero scores; paired metrics use
shared scored inputs and partial coverage may bias results. Incomplete calibration
leaves raw final scores available without fabricated temperatures. Verification
and report failures still stop the job; paused plans require explicit resume.
Unknown external intents remain missing without replay and do not block later
inputs. Resume a paused external collector by its stage; native collectors still
require their recorded call ID.

`reuse_evaluation_predictions` explicitly links compatible completed predictions into a draft comparison. Match input/model/runtime/inference identities and stored hashes. `create_training_experiment(reuse_existing_predictions=true, evaluation=...)` carries that choice into its new comparison; default false preserves independent repetitions. This does not reuse performance measurements or erase seed variation.

Discover saved objectives, inputs, output requirements and state with `list_model_workflows` and `get_job`. Action receipts expose `next_actions`, `action_requirements`, `input_required`, polling guidance and detail links. An experiment aggregates child outcomes; a failed candidate remains visible. Inspect artifact verification and coverage, not merely process success.

Partition construction exposes stage/count progress. Explicit train/development roles have train intent even when the source is exploratory; calibration/final roles have eval intent. Native decisions are projected into input.decision and separate expected_output without changing labels, weights or row membership. Mean-only targets remain means and invalid targets remain visible. Chat and other shapes may still require Workshop preparation. Inspect exact member cells and counts before creating comparisons.

Current GPU-hour rates are separate from duration evidence. Unknown duration means unknown total, even with a current rate card; inspect estimate_blockers and rejected_measurements. Do not substitute historical charges for current rates or imply unreported components are free.
