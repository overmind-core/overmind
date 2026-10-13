---
name: overmind-evaluations
description: Prepare and run Overmind evaluations, author eval matrix rows and eval sets, compare runs, and inspect row-level failures. Use for judging a change against a baseline; training and repository optimisation have separate workflows.
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
targets. Resolve reported eval set rows, dataset and credit requirements. Read
`overmind://eval-sets/{eval_set}` for set details.

When metric changes are requested, collect the actual expectation and use
`upsert_eval_matrix_row` (an llm row is one checkable sentence; a deterministic
row is an audited `evaluate(span)` check); group rows with `create_eval_set`
when needed. Preserve existing evaluation semantics unless the user asks to
change them.

## Run and compare

Before a paid run, make the dataset/cell, targets, baseline and
judge/cost choices concrete. Context estimates are advisory; present estimated
overflows and available choices without automatically changing or excluding a model.

Use `judge_model` on readiness and `run_evaluation` for a run-only judge choice.
Omission uses the default judge. The run freezes its judge and matrix; create a
new run to change either.

For an authorized change evaluation, prefer `evaluate-change` with dataset and
baseline. Otherwise call `run_evaluation` after checking readiness, poll its
`eval_run` job and read the completed run. Use `compare_evaluations` for the
specified baseline. Keep pinned dataset, targets and judge identities visible
when assessing whether the runs are comparable.

Report overall and per-metric deltas, paired wins and losses, row coverage and
trust flags. Separate failed generation, output exhaustion and metric errors
from quality scores; degraded or errored rows must not disappear into a passing
average.

Conclude improved, regressed, unchanged or insufficient evidence, with supporting
run and row IDs. For a visual comparison open `evaluations/runs/{id}` under the
current project's Console base and preserve `projectId`.
