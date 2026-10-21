---
name: overmind-optimiser
description: Set up, inspect and complete Overmind Optimiser experiments for prompt/code changes or model comparisons. Use when improving a capability through measured candidates and a local repository executioner.
---

# Overmind Optimiser

Start with `list_projects` and choose the intended accessible project. For an
account connection, pass its `project_id` on every project tool and resource
URI query; follow returned links. Project API keys retain their narrower access.

Find a measured improvement while preserving the experiment's own baseline and
the user's selected task. Use the chosen MCP project and resolve all dataset
and capability references from its returned records.

## Choose the experiment

Distinguish prompt/code optimization from model comparison. Prefer the native
`optimize-capability` prompt for the first, or `compare-models` for a selected
model list. Check the current schema for `optimize`, `model_comparison` or
`hybrid` mode; do not silently substitute one for another.

Use `list_datasets`, `inspect_dataset` and `query_dataset` to select a fitting
eval cell. Call `check_optimizer_readiness` with that cell, capability, mode,
eval set and requested models. Explain missing executioner, model or credit
prerequisites. Every iteration is graded with the rows of the
experiment's first run, by default the capability's active eval set; confirm
it holds the code's expectations and toml checks (`eval_preload` on the
capability, refreshed by `overmind sync`) before starting. The dataset stays on the server; a local file must be
landed before an experiment can use it.

## Execute within the approved scope

After the mode, candidate scope and spend are authorized, `start_optimizer`
creates the experiment. MCP schedules and reports it; a local executioner runs
repository commands and candidates. Do not claim that a scheduled experiment
has executed or that MCP can apply a repository diff.

Follow the returned `next_action` and exact command in the intended checkout.
Use local `overmind optimise ... --help` for commands unavailable in the client.
If the client cannot run local code, provide that handoff and continue read-only
status inspection. Preserve existing repository changes.

The experiment scores its own baseline iteration. Do not create a separate
baseline run and use it as the improvement gate. Candidate changes must solve
the task rather than encode held-out answers. Stop at the returned completion
or plateau checkpoint; do not extend the search budget without authorization.

Hard rules for every experiment:

1. **Eval intent only.** The chosen dataset cell must fit the **eval** contract (see
   [dataset contracts](../overmind-datasets/SKILL.md#intent-and-contracts)).
   Train and pending datasets are refused.
1. **The server always holds the dataset.** The experiment uses the chosen cell and
   the loop pulls that version to `.overmind/datasets/<cell_id>.jsonl`. A JSONL path
   on this machine is landed first (`POST /api/datasets/` with `rows` and
   `intent: eval`), then used by id. Pull the used version once; never re-fetch it
   per model.
1. **No manual baseline eval run.** The experiment scores its own baseline iteration
   (`order=0`); that is what the improvement delta and the landing gate use.
1. **Grade with the active eval set.** Every iteration or model is scored with the
   rows of the experiment's first run, by default the capability's active set.
   Modes are exactly `optimize`, `model_comparison` and `hybrid`.

Experiment statuses include `scheduled`, `smoke_testing`, `baseline`, `iterating`,
evaluation wait states, `applying_winner`, `completed`, `failed`, `cancelled` and
`paused`. A terminal result may select a candidate or the incumbent.

## Local executioner loop

The connected executioner runs repository commands and records per-datapoint results
in the local SDK/CLI ledger. The main MCP server schedules metadata and reports
scores; it does not execute the repository, apply candidate diffs, or provide
per-iteration result-posting tools, so do not call removed per-iteration tools. When
the server returns it, the local command is:

```bash
overmind optimise start -e <experiment-id> && overmind optimise next
```

The local loop may request these SDK/CLI actions: `set-template`, `run-smoke`,
`run-baseline`, `add-candidate`, `run-iteration`, `status` and `complete`. They are
local commands, not MCP tool names; run them in the repository with the required
human authorization for local code or Git side effects. `overmind optimise next`
returns the action to take:

| Action                   | What you do                                                  |
| ------------------------ | ------------------------------------------------------------ |
| `WRITE_COMMAND_TEMPLATE` | Write the template, then `set-template`                      |
| `RUN_SMOKE`              | `overmind optimise run-smoke`                                |
| `RUN_BASELINE`           | `overmind optimise run-baseline`                             |
| `WRITE_CANDIDATES`       | Write unified diffs, `add-candidate --diff`, `run-iteration` |
| `RUN_ITERATION`          | `overmind optimise run-iteration`                            |
| `WAIT`                   | Server scoring — `next` again shortly                        |
| `COMPLETE`               | `overmind optimise complete`                                 |
| `DONE`                   | Finished — report scores                                     |

`next` returns `COMPLETE` early (with a `reason`) once
`max_iterations_without_improvement` consecutive rounds fail to beat the best score;
write no more candidates past that plateau.

Command template (`WRITE_COMMAND_TEMPLATE`): one reusable shell command that runs the
capability on a single datapoint and prints output. Keep tokens verbatim
(`__DATAPOINT_INPUT__`, `__EXPERIMENT_ID__`, `__CAPABILITY_ID__`, `__CANDIDATE_ID__`,
`__ITERATION_ID__`, `__DATAPOINT_INDEX__`, `__PROJECT_ID__`). Prefer an env/config
model swap over rewriting call sites.

Candidates are unified git diffs against the current repository. Keep the entrypoint
signature and do not hardcode datapoint answers. The SDK applies each diff in a
worktree, runs the dataset and POSTs outputs; the server runs the eval runs.

```
overmind optimise start -c <slug> -d <dataset-id>
overmind optimise start -e <experiment-id>
overmind optimise next
overmind optimise set-template <file>
overmind optimise run-smoke
overmind optimise run-baseline
overmind optimise add-candidate --diff <file>
overmind optimise run-iteration
overmind optimise status [--json]
overmind optimise complete
```

Done when the experiment is completed and scores are reported, the best candidate
beat the baseline or the plateau is clear, and a winning diff is applied locally when
it won.

## Model comparison and backtest

Use the native `compare-models` prompt. This is optimizer mode `model_comparison`,
not prompt/code optimisation and not fine-tuning; do not use the iteration loop for
it. Call `check_optimizer_readiness` with `mode="model_comparison"` and 1–5
`model_ids` (the backend rejects an empty list or more than five), then
`start_optimizer` with the same capability, dataset, mode and models after the human
approves the model list. Use only model ids the readiness result returns or accepts;
do not invent catalogue ids. The server validates model access, credits,
eval-set readiness and executioner connectivity. Inspect progress with
`inspect_optimizer_result` and `overmind://optimizer-runs/{experiment}`.

The executioner owns local state, provider configuration, trace emission and result
reporting; MCP owns project-scoped scheduling and result inspection. Provider rewrite
is code, not a prompt: when the repository needs model-provider translation, run
`overmind.backtest.rewrite_repo(".")`, review its report and patch only the leftover
sites it lists. The runtime model is `OPENROUTER_MODEL` or the repository's existing
configuration; never hardcode a model slug. Pinning a winner is a local repository or
configuration change that a human reviews and applies.

`overmind backtest` is the model layer: it scores stored `llm_call` spans, does not
run the application or its tools, and uses the recorded completion as the baseline.

```bash
overmind backtest --models openai/gpt-5-mini,anthropic/claude-sonnet-4.5 --since 7d
```

`--capability` is required when `overmind.toml` lists more than one. `--limit` caps
the calls (default 200). The command waits and exits 1 when any candidate regresses
against the recorded outputs, 2 on timeout. `overmind optimise` remains the path that
reruns the repository.

## Inspect and land

Use `inspect_optimizer_result`, `overmind://optimizer-runs/{experiment}` and
`get_job(kind=optimizer_experiment, id=...)` for progress, coverage, failures,
candidate scores and the winner. A terminal run may retain the incumbent.
Describe partial coverage and incomplete evaluations before naming a winner.

Present the winning diff and its measured delta against the experiment's own
baseline. Apply it locally only when landing that change is authorized; changing
code, switching the live serving alias and selecting a benchmark are distinct
actions. Report whether a change was merely proposed or actually applied.

Open `optimiser/{experiment_id}` under the project's Console base with the same
`projectId` when a visual result is useful.
