---
name: overmind-training
description: Select models, prepare exact training inputs, estimate costs, launch and inspect Overmind fine-tuning jobs. Use for the Training surface, including held-out evaluation and benchmark selection; live model activation belongs to Inference.
---

# Overmind Training

Start with `list_projects` and choose the intended accessible project. For an
account connection, pass its `project_id` on every project tool and resource
URI query; follow returned links. Project API keys retain their narrower access.

Produce or inspect a trained model with an explicit data, evaluation and cost
contract. Use the chosen MCP project. For an existing job, start with
`overmind://finetunes/{job_id}` and its `finetune_job` status; do not relaunch it.

## Prepare the training contract

Prefer the native `finetune-capability` prompt with the selected dataset and
optional capability. Resolve datasets with `list_datasets`; inspect and query
the exact train and held-out eval cells. Inspect whole-frame task families and
workshop findings rather than assuming a sample describes the whole corpus.

Use `get_model_catalog` for initial model discovery, then
`check_finetune_readiness` for the selected dataset. A capability is optional. Ordinary held-out evaluation data and an applicable eval set are required only when those chat evaluations are selected. Native decision training has a separate native suite plan. Use the
returned requirements to identify technical blockers. Quality findings,
overlap and incomplete semantic reviews remain visible advisory warnings.

The model catalogue returns `openrouter_id` and `openrouter_status` for base
benchmarks. Only `available` establishes a catalogue match; null IDs, `not_listed`
and `catalog_unavailable` do not. Never substitute a different model size or variant.
Overmind resolves the exact route; trained checkpoints retain their own hosting.

Read `benchmark_model` on the capability when comparing with an incumbent.
`set_benchmark_model` changes future benchmark selection, not live serving or
existing jobs. Use it only for a requested benchmark change. New jobs pin their
benchmark selection and readable dataset versions.

## Size and price the run

For Modal training, call `prepare_training_data` for the chosen model, context
length and cell, then poll the returned `training_preparation` job. Inspect
exact token counts, supervised content and incompatible rows. Send concrete
source-row repairs through native-agent-authored pipelines or lineage-bound
`import_dataset_version` results; keep model-specific preprocessing
in Training. Reprepare a changed cell rather than reusing a stale report.

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
selected data, benchmark, evaluator choices and expected spend in the receipt.
Poll `get_job(kind=finetune_job, id=...)`, then inspect the linked deployment
separately. Training completion, evaluation completion and serving readiness
are distinct outcomes.

Native `initial_validation` is presented as **Pre-training baseline evaluation**.
It measures the starting model on the development set before training begins;
it is separate from data preparation and the held-out final benchmark.
MCP progress includes `stage_label` and `stage_description` for this stage. Set `hyperparameters.pre_training_baseline=false` on a native launch or saved experiment variant to skip this pass. It defaults to true and does not disable development validation, checkpoint selection or separately scheduled final benchmarks. The choice is frozen in the recipe; skipped work is recorded as `not_requested`.

Report measured before/after results with trust flags and any unresolved
preparation findings. A successful training job does not authorize activation
or a repository model swap. For requested checkpoint export, use
`download-checkpoint` or the `overmind://checkpoint-download` CLI handoff.

Open `training` under the project's Console base with its `projectId` for the
visual job dashboard. Retain the exact job and deployment IDs in the report.

## Durable experiment receipts

Pass the same explicit train/validation/evaluation cell IDs and evaluation choices to readiness, estimate and launch. Supply one stable `request_key` per intended experiment and reuse it after an uncertain client response. A changed recipe needs a new deliberate experiment; do not work around a conflict by generating another key automatically. Inspect the job's requested/effective settings and run record. Unresolved provider submission must be reconciled, never relaunched.

For a native decision checkpoint, use `schedule_native_evaluation` with separate frozen calibration and final decision cells. This authorizes paid paired unchanged-base/candidate evaluation after verified checkpoint completion. Poll `get_job(kind=native_evaluation)` and follow its links. References stay local; known incompatible inputs remain visible in coverage. Calibration is frozen before final results. Report raw and calibrated metrics, source slices, paired confidence intervals and regressions separately from training loss.

An unknown native forecast is not a zero-dollar quote. GPU ledger spend excludes unreported preparation, CPU/memory, evaluation and storage. Compare measured work and forecast ranges without describing them as a spending cap. Native checkpoints are probability artifacts and do not enter chat deployment or activation.

## Data-first model workflows

For saved partitions, standalone decision comparisons, explicit training candidates or reproducible performance workloads, use `develop-model-from-data` and [the model workflow reference](../overmind/references/model-workflows.md). The native coding agent interprets targets from evidence before consumers enforce their declared meaning. These workflows do not require a repository or activate a model.

Read `overmind://interface/current` for connected lifecycle version and [model workflows](../overmind/references/model-workflows.md) for explicit source derivation, draft/prepare/launch, bounded profiles, prediction reuse and recovery. Creation is not paid launch; saved scope and receipts control continuation.
