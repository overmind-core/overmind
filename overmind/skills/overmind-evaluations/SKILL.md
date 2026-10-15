---
name: overmind-evaluations
description: Prepare and run Overmind evaluations, author eval matrix rows and eval sets, compare runs, and inspect row-level failures. Use for judging a change against a baseline; training and repository optimisation have separate workflows.
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
targets. Resolve reported eval set rows, dataset and credit requirements. Read
`overmind://eval-sets/{eval_set}` for set details. Pass `limit` (1–100, default
20\) and follow `next_offset` until null to audit every row. Pages return complete
evaluator sources; do not mistake the first page for the full suite.

A run grades with the capability's active eval set unless given another. In a
repository that set is the Default set `overmind sync` keeps: an llm row per
`expectations=[...]` sentence on entry-point, workflow and `observe`
decorators, and a deterministic row per toml `eval_matrix` entry naming a
catalog `check` with its `params`. Read `eval_preload` on
`overmind://capabilities/{capability}` to see what it holds. Use it as is; do
not recreate it or copy its rows into a new set to prepare a run.

When metric changes are requested, collect the actual expectation. In a
repository, edit the decorator's `expectations` or the toml entry and run
`overmind sync`. Without the repository, or for a rule no catalog check
expresses, use `create_eval_matrix_row` (an llm row is one checkable sentence;
a deterministic row is an audited `evaluate(span)` check) and add it to the
active set with `create_eval_set(..., activate=True)`. A row cannot be edited:
`delete_eval_matrix_row`, then create the replacement. Preserve existing
evaluation semantics unless the user asks to change them.

Over a dataset, rows whose `span_type` is blank or `llm_call` score each row's
answer whatever their `span_name`; tool, timing and cost checks abstain.
Live span and trajectory scores and eval-run scores are different
observations of the same criteria.

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

## Data-first model workflows

For saved partitions, standalone decision comparisons, explicit training candidates or reproducible performance workloads, use `develop-model-from-data` and [the model workflow reference](../overmind/references/model-workflows.md). The Workshop interprets targets from evidence before consumers enforce their declared meaning. These workflows do not require a repository or activate a model.

Read `overmind://interface/current` for connected lifecycle version and [model workflows](../overmind/references/model-workflows.md) for explicit source derivation, draft/prepare/launch, bounded profiles, prediction reuse and recovery. Creation is not paid launch; saved scope and receipts control continuation.

Use `inspect_native_evaluation` for recorded per-model execution stages, partial
metrics and paginated decision inputs, references and probability outputs. Keep
`role=calibration` separate from `role=final`; calibration scores are in-sample.
Select an exact sample `key` (including `"0"`) or page with `offset`/`limit`.
Partial scores describe only the returned scored subset, not a final ranking.
Reads may index retained local evidence but never start inference. Unavailable
worker telemetry, incompatible inputs and missing outputs remain explicit.

Transient native-evaluation stage failures recover automatically. Poll `get_job`
while the plan is running; calls retain retry times and recovery history. Completed
responses and native call IDs are reused. Missing external predictions can retry
after recorded transport failures, with a bounded budget and unknown prior costs
retained. Do not restart the entire comparison. Exhausted or permanent collection
errors finish scoring available verified predictions. Inspect `results.completion`
for `completed_with_issues`, per-suite/per-participant coverage and stage issues.
Missing predictions are not zero scores; paired comparisons use shared scored
inputs. Partial coverage can bias results. Incomplete calibration leaves raw scores
available; never claim an unfitted calibrated comparison. Verification/report
failures and unresolved native submissions still need inspection; paused runs require resume.
Unknown external intents remain missing without replay; later inputs continue.
