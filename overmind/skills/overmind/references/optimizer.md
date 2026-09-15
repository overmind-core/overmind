# Optimizer

Use the native `optimize-capability` prompt for prompt/code optimization. Use
`compare-models` and [backtest.md](backtest.md) for model comparison.

## Hard rules

1. **Eval intent only.** The chosen dataset cell must fit the **eval**
   contracts (see
   [SKILL.md](../SKILL.md#dataset-contracts--read-first-they-gate-every-workflow)
   and [datasets.md](datasets.md)). Train and pending datasets are refused.
1. **The server always holds the dataset.** The experiment uses the chosen
   cell and the loop pulls that version to
   `.overmind/datasets/<cell_id>.jsonl`. A JSONL path on this machine
   is landed first (`POST /api/datasets/` with `rows` and `intent: eval`),
   then used by id.
1. **No manual baseline eval run.** The experiment scores its own baseline
   iteration (`order=0`) — that is what the improvement delta and the landing
   gate use.
1. **Not model comparison.** For swapping models follow
   [backtest.md](backtest.md) (`/overmind backtest`). Do not use this loop
   for `mode=model_comparison`.

## Main MCP path

1. Call `list_datasets`, inspect the candidate by UUID, and use
   `query_dataset` to verify the chosen `eval` cell and capability.
1. Call `check_optimizer_readiness` with the dataset UUID, cell UUID, optional
   eval set, mode, and model ids.
   Modes are exactly `optimize`, `model_comparison`, and `hybrid`.
1. Fix reported dataset, eval-set, executioner, model, credit, and quota gaps.
1. After human approval, call `start_optimizer`. It schedules one project
   experiment and returns an experiment id, job resource, executioner state,
   and possibly a local `next_action.command`.
1. Read `overmind://optimizer-runs/{experiment}` and call
   `inspect_optimizer_result` for bounded iterations, candidates, scores,
   winner, executioner state, and next action. Use
   `get_job(kind="optimizer_experiment", id=...)` for the normalized job view.

Optimizer experiment statuses include `scheduled`, `smoke_testing`, `baseline`,
`iterating`, evaluation wait states, `applying_winner`, `completed`, `failed`,
`cancelled`, and `paused`. Terminal results may select a candidate or the
incumbent.

## Local execution ledger

The connected executioner runs repository commands and records per-datapoint
results in the local SDK/CLI ledger. The main MCP server schedules metadata and
reports scores; it does not execute the repository, apply candidate diffs, or
provide per-iteration result-posting tools.

When the server returns it, a local executioner command is:

```bash
overmind optimise start -e <experiment-id> && overmind optimise next
```

The local loop may request these SDK/CLI actions: `set-template`, `run-smoke`,
`run-baseline`, `add-candidate`, `run-iteration`, `status`, and `complete`.
Those are local commands, not MCP tool names. Run them in the repository with
the required human authorization for local code or Git side effects.

## Landing a winner

```bash
overmind optimise next
```

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
`max_iterations_without_improvement` consecutive rounds fail to beat the best
score — do not write more candidates past that plateau.

Command template (`WRITE_COMMAND_TEMPLATE`): one reusable shell command that
runs the capability on a single datapoint and prints output. Keep tokens
verbatim (`__DATAPOINT_INPUT__`, `__EXPERIMENT_ID__`, `__CAPABILITY_ID__`,
`__CANDIDATE_ID__`, `__ITERATION_ID__`, `__DATAPOINT_INDEX__`,
`__PROJECT_ID__`). Prefer env/config model swap over rewriting call sites.

Candidates: unified git diffs against the current repo. Keep the entrypoint
signature. Do not hardcode datapoint answers. The SDK applies each diff in a
worktree, runs the dataset, POSTs outputs; the server runs EvalRuns.

Monitor with `get_job` and `inspect_optimizer_result`.

### 5 — Land

`inspect_optimizer_result(experiment)` returns the winner and its stored
diff once the experiment is COMPLETED and the best score beat the
experiment's own baseline. Apply that diff in this repo yourself; the server
does not edit the repository.

## SDK verbs

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

## Done when

- Experiment completed; scores reported to the user
- Best candidate beat baseline, or the plateau is clear
- Winning diff applied locally when it won
