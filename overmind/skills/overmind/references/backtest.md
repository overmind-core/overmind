# Model comparison and backtest

Use the native `compare-models` prompt. This is optimizer mode
`model_comparison`, not prompt/code optimization and not fine-tuning.

## Hard rules

1. **Provider rewrite is code, not a prompt.** Run
   `overmind.backtest.rewrite_repo(".")` when needed. Patch only the leftover
   sites the report lists. Never hardcode a model slug — runtime model is
   `OPENROUTER_MODEL`.
1. **1–5 models.** Backend rejects empty or >5 `model_ids`.
1. **Eval intent only.** The dataset's active version must fit the
   **eval** contracts (see
   [SKILL.md](../SKILL.md#dataset-contracts--read-first-they-gate-every-workflow)
   and [datasets.md](datasets.md)). Train and pending datasets are refused.
1. Pull the experiment's used version once to
   `.overmind/datasets/<cell_id>.jsonl` — never re-fetch per model.

## Main MCP path

1. Confirm a capability reference and an `eval` dataset.
1. Call `check_optimizer_readiness` with `mode="model_comparison"` and the
   selected `model_ids` (at most five).
1. After readiness and human approval of the model list, call
   `start_optimizer` with the same capability, dataset, mode, and models.
1. Inspect progress with `inspect_optimizer_result` and, when needed,
   `get_job(kind="optimizer_experiment", id=...)`.
1. Read the experiment at `overmind://optimizer-runs/{experiment}`.

The server validates model access, credits, plan quota, eval-set readiness,
and executioner connectivity. Use only model ids returned or accepted by the
readiness result; do not invent catalog ids.

## Local execution boundary

The repository run and per-datapoint execution ledger belong to the connected
local SDK/CLI executioner, not the main MCP server. When the returned
`next_action.command` asks for it, the local command is:

```bash
overmind optimise start -e <experiment-id> && overmind optimise next
```

The executioner owns local state, provider configuration, trace emission, and
result reporting. MCP owns project-scoped scheduling and result inspection. Do
not call removed per-iteration MCP tools or pretend the server runs local code.

If the repository needs model-provider translation, the local coding workflow may
run `overmind.backtest.rewrite_repo(".")`, review its report, and patch only
reported leftovers. Keep the model in `OPENROUTER_MODEL` or the repository's
existing configuration; never hardcode a model slug in application code.

Pinning a winner is a local repository/configuration change. A human reviews
and applies any returned repository change.

## Model layer

`overmind backtest` scores stored `llm_call` spans. It does not run the
application or its tools. The recorded completion is the baseline.

```bash
overmind backtest --models openai/gpt-5-mini,anthropic/claude-sonnet-4.5 --since 7d
```

`--capability` is required when `overmind.toml` lists more than one. `--limit`
caps the calls (default 200). The command waits and exits 1 when any candidate
regresses against the recorded outputs, 2 on timeout. `overmind optimise` remains
the path that reruns the repository.
