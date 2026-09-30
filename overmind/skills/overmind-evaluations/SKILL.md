---
name: overmind-evaluations
description: Prepare and run Overmind evaluations, author evaluators and eval sets, compare runs, and inspect sample-level failures. Use for judging a change against a baseline; training and repository optimisation have separate workflows.
---

# Overmind Evaluations

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
