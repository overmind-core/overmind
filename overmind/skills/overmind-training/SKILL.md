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

Read `benchmark_model` on the capability when comparing with an incumbent.
`set_benchmark_model` changes future benchmark selection, not live serving or
existing jobs. Use it only for a requested benchmark change. New jobs pin their
benchmark selection and readable dataset versions.

## Size and price the run

For Modal training, call `prepare_training_data` for the chosen model, context
length and cell, then poll the returned `training_preparation` job. Inspect
exact token counts, supervised content and incompatible rows. Send concrete
source-row repairs to `message_dataset_agent`; keep model-specific preprocessing
in Training. Reprepare a changed cell rather than reusing a stale report.

Use `estimate_finetune` and evaluation readiness to present training plus
before/after evaluation spend. Each selected evaluation uses the full pinned
eval dataset. Context warnings do not automatically block launch or change
model selection. An approved preview's `judge_model` becomes
`eval_judge_model` on `start_finetune`; omission preserves the set's judges.

## Launch and verify

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

For saved partitions, standalone decision comparisons, explicit training candidates or reproducible performance workloads, use `develop-model-from-data` and [the model workflow reference](../overmind/references/model-workflows.md). The Workshop interprets targets from evidence before consumers enforce their declared meaning. These workflows do not require a repository or activate a model.

Read `overmind://interface/current` for connected lifecycle version and [model workflows](../overmind/references/model-workflows.md) for explicit source derivation, draft/prepare/launch, bounded profiles, prediction reuse and recovery. Creation is not paid launch; saved scope and receipts control continuation.
