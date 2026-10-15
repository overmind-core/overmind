# Evaluations

A capability's **active eval set** is the one set of criteria live scoring,
eval runs, `overmind backtest`, the optimizer and training all grade with. Its
rows come from three places:

| Source                                         | Holds                                                         | Changed by                             |
| ---------------------------------------------- | ------------------------------------------------------------- | -------------------------------------- |
| Decorator `expectations=[...]`                 | Judge criteria (`llm` rows)                                   | Editing code, then `overmind sync`     |
| Toml `eval_matrix` entries                     | Catalog checks with `check` + `params` (`deterministic` rows) | Editing the toml, then `overmind sync` |
| `create_eval_matrix_row` / Console **New row** | Any extra expectation or custom `evaluate(span)` check        | MCP or the Console                     |

`overmind sync` keeps each capability's **Default** set from the first two plus
the enabled project checks that fit its spans, and activates it when the
capability has none. An **eval run** scores one dataset cell with a frozen copy
of the rows.

## Configure

1. Run `/overmind setup` ([setup.md](setup.md)). It writes the toml checks,
   syncs, decorates the code with `expectations`, then syncs again. The second
   sync is what puts the code's expectations into the Default set.

1. Read `overmind://capabilities/{capability}`: `eval_matrix` lists the rows
   that apply and `eval_preload.counts` what sync put in the Default set
   (`expectations` and `checks`). `status: empty` means nothing applied.

1. To change judge criteria, edit the decorator's `expectations` and run
   `overmind sync`. A sentence removed from the code leaves the set at the next
   sync. Each expectation is one concrete, checkable sentence about that span's
   output, with no data values or secrets. Expectations on entry points,
   workflows and plain `observe` spans also grade dataset runs; those on `tool`
   and `retrieval` decorators grade live traces only.

1. To add a checkable rule, add a toml `eval_matrix` entry and run
   `overmind sync`:

   ```toml
   [[capabilities.support-agent.eval_matrix]]
   name = "Reply carries ticket keys"
   type = "deterministic"
   check = "json_required_keys"
   params = { REQUIRED_KEYS = ["ticket_id", "status"] }
   span_name = "SupportAgent.run"
   ```

   Catalog checks and their params: `exact_match` (`REFERENCE`,
   `CASE_INSENSITIVE`), `contains` (`NEEDLE`, `CASE_INSENSITIVE`), `regex`
   (`PATTERN`, `MUST_MATCH`), `goal_state_match` (`GOAL_MARKERS`),
   `json_required_keys` (`REQUIRED_KEYS`), `schema_field_conformance`
   (`FIELDS`), `tool_args_match` (`EXPECTED_CALLS`, `MODE`), `llm_call_budget`
   (`MAX_LLM_CALLS`), `latency_budget` (`MAX_SECONDS`), `cost_budget`
   (`MAX_COST`). `output_is_valid_json` and `tool_selection` are already on.
   Sync returns a 400 naming an entry with an unknown `check` or param; fix it
   and sync again.

1. Only for a rule the catalog cannot express, call `create_eval_matrix_row`
   with `name`, `type`, `source` and optional `capability`, `span_type` and
   `span_name`. An llm `source` is one checkable sentence. A deterministic
   `source` defines `evaluate(span)` returning a bool, a score in [0, 1],
   `(score, reason)` or `None` to abstain; the span exposes `input`, `output`,
   `expected`, `tool`, `status`, `duration_s`, `usage`, `attributes`, `parent`,
   `children` and `siblings`, with `json` and `re` preloaded and no imports.
   Rows are created and deleted, never edited. Add a row made this way to the
   active set with `create_eval_set(..., activate=True)`, or it scores live
   traces but not the runs that grade with the set.

Do not recreate the Default set or copy its rows into a new set to "prepare"
an evaluation. Do not guess row or set ids.

**Where a row applies.** On live traces, a row scores spans of its capability
that match its `span_type` and `span_name`; a row with neither scores the
capability's answer span. Over a dataset, every row whose `span_type` is blank
or `llm_call` scores each row's answer, whatever its `span_name`. Checks that
read tools, timing or cost abstain on dataset rows.

## Live scoring

Every ingested span is scored against the rows that match it and the
`expectations` declared on it. Once a trace's spans are scored, its trajectory
is scored: outcome, weakest step, efficiency, path quality and, when
`reference_trajectory` is declared, the match against it. Scores land on each
span's `eval_attributes` and in the score history; low scores carry a reason.
A metric below 0.5 fails. `overmind.expect("constraint", "...")` adds an
expectation for the current trace only.

## Prepare a run

The dataset's intent **must** be eval and the chosen cell must fit. Call
`list_datasets`, pass its dataset UUID to `inspect_dataset`, and use
`query_dataset` to verify the chosen cell. Eval runs and optimizer experiments
refuse `train` and `pending` datasets — see
[SKILL.md](../SKILL.md#dataset-contracts--read-first-they-gate-every-workflow)
and [datasets.md](datasets.md) to land one. The run uses the version it read,
which freezes it.

Use `prepare-evaluation` for readiness and `evaluate-change` for a run versus a
baseline. Call `check_evaluation_readiness` with the dataset UUID, chosen cell
UUID, optional eval set (default: the capability's active set) and the proposed
`targets`. Read `ready`, the matrix summary, credit availability and advisory
`context_checks` for candidate models and the judge. Context warnings and
unknown limits never change `ready` or require approval; report them without
blocking launch. Present `suggestions`, `estimated_cost_usd`, `cost_delta_usd`
and `cost_basis` during setup: full-reserved-output budgets for one dataset
pass, not total-run quotes or quality predictions. Never switch models
automatically. Preview a run-specific `judge_model`; pass the approved choice
to `run_evaluation`, or as `eval_judge_model` to `start_finetune`.

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

## Evals in other workflows

Each grades with the capability's active set unless given another, and keeps
the same rows for its whole run, so confirm the set before starting one.

- `overmind backtest` and optimizer `model_comparison`: [backtest.md](backtest.md).
- Optimizer: [optimizer.md](optimizer.md).
- Fine-tuning before/after evaluations: [finetuning.md](finetuning.md).

These create their own baseline and candidate runs in one group; a manual eval
run beforehand is only for comparisons you drive yourself.

## Signals

`list_signals` reports defaults, effective enabled state and explicit overrides.
Deterministic checks run automatically every five minutes over spans received in
the last day. `set_signal_subscription` disables, enables or configures a check;
a capability setting overrides its project setting. `plan_drift` remains off
until explicitly enabled because it makes additional model calls.

Read saved findings from `overmind://signal-events`. Events include conversation
identity and their source trace inventory. `query_traces(signal_event=EVENT_ID)`
inspects the retained evidence in the same project. The Console exposes Sessions,
Signals and Traces inside Observability. A `repeated_failure` names the expectation
that keeps failing: inspect its evidence before deciding whether the agent or the
expectation should change.
