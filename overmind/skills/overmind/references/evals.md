# Evaluations

An **eval matrix row** is one metric: an `llm` row is a one-sentence expectation
an LLM judges against an output; a `deterministic` row is a short Python check
the platform audits and runs in a sandbox. Rows scope to a capability, a span
type or a span name. An **eval set** is a named selection of rows, optionally
mapped to a capability; the capability's active set scores its traces live and
is the default for its eval runs. An **eval run** scores one dataset cell with a
frozen snapshot of those rows.

The dataset's intent **must** be eval and the chosen cell must fit. Call
`list_datasets`, pass its dataset UUID to `inspect_dataset`, and use
`query_dataset` to verify the chosen cell. Eval runs and optimizer experiments
refuse `train` and `pending` datasets — see
[SKILL.md](../SKILL.md#dataset-contracts--read-first-they-gate-every-workflow)
and [datasets.md](datasets.md) to land one. The run uses the version it
read, which freezes it.

Use `prepare-evaluation` for readiness and `evaluate-change` for a run versus
a baseline.

## Live scoring

Every ingested span is scored against the enabled rows that match it, plus
any `expectations` declared on the span with
`@overmind.observe(expectations=[...])`. Unscoped llm rows apply to root spans.
Once a trace's spans are scored, its trajectory is scored: tool success,
weakest step, plan adherence and, when `reference_trajectory` is declared, the
match against the reference. Scores land on `eval_attributes` of each span and
in the score history; failures carry the judge's reason. A metric below 0.5
fails.

## Prepare

1. Read `overmind://capabilities/{capability}` for `eval_matrix` (the rows that
   apply) and `eval_preload` (what sync put into the Default set).
1. To add a metric, collect the human expectation and call
   `create_eval_matrix_row` with `name`, `type`, `source` and optional
   `capability`, `span_type` and `span_name`. To change one, call
   `delete_eval_matrix_row` and create the replacement. Keep an llm `source` to
   one checkable sentence. A deterministic `source` defines
   `evaluate(span)` returning a bool, a score in [0, 1], `(score, reason)` or
   `None` to abstain. The span exposes `input`, `output`, `expected`, `tool`,
   `status`, `attributes`, `parent`, `children` and `siblings`. The audit
   rejects imports and unsafe names; `json` and `re` are preloaded.
1. Group rows with `create_eval_set` (`name`, optional `capability`, `rows`,
   `activate`). Only capability-mapped sets can be activated for live scoring.
1. Call `check_evaluation_readiness` with the dataset UUID, chosen cell UUID,
   optional eval set and the proposed `targets`. Read `ready`, the matrix
   summary, credit availability and advisory `context_checks` for candidate
   models and the judge. Context warnings and unknown limits never change
   `ready` or require approval; report them without blocking launch. Present
   `suggestions`, `estimated_cost_usd`, `cost_delta_usd` and `cost_basis`
   during setup. These are full-reserved-output budgets for one dataset pass,
   not total-run quotes or quality predictions. Never switch models
   automatically. Preview a run-specific `judge_model`; pass the approved
   choice to `run_evaluation`, or as `eval_judge_model` to `start_finetune`.

Do not guess row or set ids. Keep sensitive values out of expectations and
check sources.

## Run and compare

A target is one side of a comparison: `recorded` scores the outputs already in
the dataset; `generate` runs a model (`model_name` from the catalog) over the
recorded context. It does not invoke the live application. Mark at most one
target `is_baseline`. Dataset sampling draws across the complete pinned
version and repeats the same selection for the same version and budget.

Call `run_evaluation` only after readiness is green, with a run name, the
dataset UUID, the cell UUID, an optional eval set and judge, 1–6 `targets`,
and bounded `max_items` or sampling. It returns the `group` and one run per
target, each with an `overmind://eval-runs/{eval_run}` resource.

Poll each run with `get_job(kind="eval_run", id=...)` and read its resource.
Run statuses are `pending`, `running`, `completed`, `failed` and `cancelled`.
The resource lists rows with their score, each metric's verdict and reason, and
the run's `summary` (`score`, per-metric means, failures and errors, `trust`).

Call `compare_evaluations` with a candidate run; the baseline defaults to its
group's baseline. Report `overall`, the per-metric rows, the paired
wins/losses/ties and `trust`. Status is `improved`, `regressed`, `unchanged`,
`added` or `removed`; deltas within half a point are `unchanged`.

Finetune and optimizer loops create their own baseline and candidate runs in
one group — a manual eval run beforehand is only for comparisons you drive
yourself. See [finetuning.md](finetuning.md) and [optimizer.md](optimizer.md).
