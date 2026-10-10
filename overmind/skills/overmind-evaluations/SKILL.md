---
name: overmind-evaluations
description: Prepare and run Overmind evaluations, author evaluators and eval sets, compare runs, and inspect sample-level failures. Use for judging a change against a baseline; training and repository optimisation have separate workflows.
---

# Overmind Evaluations

Training development checks are a separate measurement surface. Inspect them with
`inspect_training_progress`; they do not replace a held-out final evaluation and
must not use calibration/final partitions for checkpoint selection. Generated
label/schema metrics, coverage and paired descriptive intervals retain their own
sample identity. An invalid evaluator snapshot or missing exact base-model route
must remain a visible failure, never an indefinitely pending benchmark. Public
base benchmarks use exact OpenRouter identity; trained/hosted models use their
recorded artifact route, not an interchangeable serving alias.

Start with `list_projects` and choose the intended accessible project. For an
account connection, pass its `project_id` on every project tool and resource
URI query; follow returned links. Project API keys retain their narrower access.

Determine whether a change improves the intended task and how trustworthy that
comparison is. Use the chosen MCP project and its current tool schemas.

## Inspect or prepare

For an existing run, read `overmind://eval-runs/{eval_run}` and its linked
resources. An inspection request does not require creating a new run.

For new work, resolve the dataset using `list_datasets`, inspect its selected
cell and verify relevant examples through `query_dataset`. Use an eval-intent
version. Report residual semantic findings as warnings, including missing
evidence or overlap; do not fabricate expected outputs to make the data fit.

Prefer the native `prepare-evaluation` prompt with dataset and eval set when
available. Use `check_evaluation_readiness` with the selected cell and proposed
variants. Resolve reported evaluator applicability, bindings, dataset and credit
requirements. Read `overmind://eval-sets/{eval_set}` for set details.

When evaluator changes are requested, collect the actual rubric and use
`upsert_evaluator`; group selected evaluator IDs with `create_eval_set` when
needed. Rubric judges default to generative. Preserve existing evaluation
semantics unless the user asks to change them.

## Run and compare

Before a paid run, make the dataset/cell, candidate variants, baseline and
judge/cost choices concrete. Context estimates are advisory; present estimated
overflows and available choices without automatically changing or excluding a model.

Use `judge_model` on readiness and `run_evaluation` for a run-only judge choice.
Omission preserves saved evaluators. The run freezes its judges; do not edit
saved evaluators or an existing run to implement this override.

For an authorized change evaluation, prefer `evaluate-change` with dataset and
baseline. Otherwise call `run_evaluation` after checking readiness, poll its
`eval_run` job and read the completed run. Use `compare_evaluations` for the
specified baseline. Keep pinned dataset, variants and judge identities visible
when assessing whether the runs are comparable.

Report overall and evaluator-level deltas, sample coverage and trust flags.
Separate failed generation, output exhaustion and judge errors from quality
scores; degraded or skipped samples must not disappear into a passing average.
Use `annotate_evaluation_sample` only to record an explicit human label.

Conclude improved, regressed, unchanged or insufficient evidence, with supporting
run/sample IDs. For a visual comparison open `evaluations/runs/{id}` under the
current project's Console base and preserve `projectId`.

## Data-first model workflows

For saved partitions, standalone decision comparisons, explicit training candidates or reproducible performance workloads, use `develop-model-from-data` and [the model workflow reference](../overmind/references/model-workflows.md). The Workshop interprets targets from evidence before consumers enforce their declared meaning. These workflows do not require a repository or activate a model.

Read `overmind://interface/current` for connected lifecycle version and [model workflows](../overmind/references/model-workflows.md) for explicit source derivation, draft/prepare/launch, bounded profiles, prediction reuse and recovery. Creation is not paid launch; saved scope and receipts control continuation.
