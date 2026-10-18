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

## Configure the active eval set

A capability's **active eval set** is the one set of criteria live scoring, eval
runs, `overmind backtest`, the optimizer and training all grade with. Its rows come
from three places:

| Source                                         | Holds                                                         | Changed by                             |
| ---------------------------------------------- | ------------------------------------------------------------- | -------------------------------------- |
| Decorator `expectations=[...]`                 | Judge criteria (`llm` rows)                                   | Editing code, then `overmind sync`     |
| Toml `eval_matrix` entries                     | Catalog checks with `check` + `params` (`deterministic` rows) | Editing the toml, then `overmind sync` |
| `create_eval_matrix_row` / Console **New row** | Any extra expectation or custom `evaluate(span)` check        | MCP or the Console                     |

`overmind sync` keeps each capability's **Default** set from the first two plus the
enabled project checks that fit its spans, and activates it when the capability has
none. An **eval run** scores one dataset cell with a frozen copy of the rows. The
first sync that writes the toml checks comes from the local setup workflow in the
[overmind skill](../overmind/references/setup.md), which then decorates
the code with `expectations` and syncs again; that second sync is what puts the
code's expectations into the Default set.

1. Read `overmind://capabilities/{capability}`: `eval_matrix` lists the rows that
   apply and `eval_preload.counts` what sync put in the Default set (`expectations`
   and `checks`). `status: empty` means nothing applied.

1. To change judge criteria, edit the decorator's `expectations` and run
   `overmind sync`. A sentence removed from the code leaves the set at the next sync.
   Each expectation is one concrete, checkable sentence about that span's output,
   with no data values or secrets. Expectations on entry points, workflows and plain
   `observe` spans also grade dataset runs; those on `tool` and `retrieval`
   decorators grade live traces only.

1. To add a checkable rule, add a toml `eval_matrix` entry and run `overmind sync`:

   ```toml
   [[capabilities.support-agent.eval_matrix]]
   name = "Reply carries ticket keys"
   type = "deterministic"
   check = "json_required_keys"
   params = { REQUIRED_KEYS = ["ticket_id", "status"] }
   span_name = "SupportAgent.run"
   ```

   Catalog checks and their params: `exact_match` (`REFERENCE`, `CASE_INSENSITIVE`),
   `contains` (`NEEDLE`, `CASE_INSENSITIVE`), `regex` (`PATTERN`, `MUST_MATCH`),
   `goal_state_match` (`GOAL_MARKERS`), `json_required_keys` (`REQUIRED_KEYS`),
   `schema_field_conformance` (`FIELDS`), `tool_args_match` (`EXPECTED_CALLS`,
   `MODE`), `llm_call_budget` (`MAX_LLM_CALLS`), `latency_budget` (`MAX_SECONDS`),
   `cost_budget` (`MAX_COST`). `output_is_valid_json` and `tool_selection` are
   already on. Sync returns a 400 naming an entry with an unknown `check` or param;
   fix it and sync again.

1. Only for a rule the catalog cannot express, call `create_eval_matrix_row` with
   `name`, `type`, `source` and optional `capability`, `span_type` and `span_name`.
   An llm `source` is one checkable sentence. A deterministic `source` defines
   `evaluate(span)` returning a bool, a score in [0, 1], `(score, reason)` or `None`
   to abstain; the span exposes `input`, `output`, `expected`, `tool`, `status`,
   `duration_s`, `usage`, `attributes`, `parent`, `children` and `siblings`, with
   `json` and `re` preloaded and no imports. Rows are created and deleted, never
   edited. Add a row made this way to the active set with
   `create_eval_set(..., activate=True)`, or it scores live traces but not the runs
   that grade with the set.

Do not recreate the Default set or copy its rows into a new set to "prepare" an
evaluation. Do not guess row or set ids.

**Where a row applies.** On live traces, a row scores spans of its capability that
match its `span_type` and `span_name`; a row with neither scores the capability's
answer span.

## Live scoring

Every ingested span is scored against the rows that match it and the `expectations`
declared on it. Once a trace's spans are scored, its trajectory is scored: outcome,
weakest step, efficiency, path quality and, when `reference_trajectory` is declared,
the match against it. Scores land on each span's `eval_attributes` and in the score
history; low scores carry a reason. A metric below 0.5 fails.
`overmind.expect("constraint", "...")` adds an expectation for the current trace
only.

## Run and compare

Before a paid run, make the dataset/cell, targets, baseline and
judge/cost choices concrete. Context estimates are advisory; present estimated
overflows and available choices without automatically changing or excluding a model.

Use `judge_model` on readiness and `run_evaluation` for a run-only judge choice.
Omission uses the default judge. The run freezes its judge and matrix; create a
new run to change either. Pass an approved judge choice to `start_finetune` as
`eval_judge_model`.

The dataset's intent must be **eval** and the chosen cell must fit: eval runs and
optimizer experiments refuse `train` and `pending` datasets (see the
[dataset contracts](../overmind-datasets/SKILL.md#intent-and-contracts)). The run uses the version it
read, which freezes it.

Call `check_evaluation_readiness` with the dataset UUID, chosen cell UUID, optional
eval set (default: the capability's active set) and the proposed `targets`. Read
`ready`, the matrix summary, credit availability and advisory `context_checks` for
candidate models and the judge. Context warnings and unknown limits never change
`ready` or require approval; report them without blocking launch. Present
`suggestions`, `estimated_cost_usd`, `cost_delta_usd` and `cost_basis` during setup:
they are full-reserved-output budgets for one dataset pass, not total-run quotes or
quality predictions. Never switch models automatically.

A target is one side of a comparison: `recorded` scores the outputs already in the
dataset; `generate` runs a model (`model_name` from the catalogue) over the recorded
context. It does not invoke the live application. Mark at most one target
`is_baseline`. Dataset sampling draws across the complete pinned version and repeats
the same selection for the same version and budget. Call `run_evaluation` only after
readiness is green, with a run name, the dataset UUID, the cell UUID, an optional
eval set and judge, 1–6 `targets`, and bounded `max_items` or sampling. It returns
the `group` and one run per target, each with an `overmind://eval-runs/{eval_run}`
resource. Run statuses are `pending`, `running`, `completed`, `failed` and
`cancelled`; the resource lists rows with their score, each metric's verdict and
reason, and the run's `summary` (`score`, per-metric means, failures and errors,
`trust`).

`compare_evaluations` takes a candidate run; the baseline defaults to its group's
baseline. Status is `improved`, `regressed`, `unchanged`, `added` or `removed`;
deltas within half a point are `unchanged`.

`overmind backtest`, the optimizer (including `model_comparison`) and fine-tuning
before/after evaluations each grade with the capability's active set unless given
another and keep the same rows for their whole run, so confirm the set before
starting one. They create their own baseline and candidate runs in one group; a
manual eval run beforehand is only for comparisons you drive yourself.

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

For saved partitions, standalone decision comparisons, explicit training candidates or reproducible performance workloads, use `develop-model-from-data` and [Develop models from data](../overmind-training/SKILL.md#develop-models-from-data). The Workshop interprets targets from evidence before consumers enforce their declared meaning. These workflows do not require a repository or activate a model.

Read `overmind://interface/current` for connected lifecycle version and [Develop models from data](../overmind-training/SKILL.md#develop-models-from-data) for explicit source derivation, draft/prepare/launch, bounded profiles, prediction reuse and recovery. Creation is not paid launch; saved scope and receipts control continuation.

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
