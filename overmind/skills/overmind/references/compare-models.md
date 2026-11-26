# Compare models

Use this to see how other models do on the calls a capability already made. The
platform replays each recorded model call with a candidate model and scores the
answers with the capability's active eval set. It does not run the application,
its tools or its control flow, so a result covers one model call, not the whole
agent.

**Prerequisite:** a synced capability with an active eval set (see
[Evaluations](../../overmind-evaluations/SKILL.md#configure-the-active-eval-set)).

## Workflow

1. **Choose the calls.** Use an existing **eval** dataset for the capability
   (`list_datasets`, `inspect_dataset`). Without one, land recent calls with
   `create_dataset_from_llm_calls` and intent `eval`, then wait for its
   `dataset_run` job. Use `query_dataset` to confirm that rows carry the recorded
   output.
1. **Choose the models.** Use the OpenRouter ids the user names, in
   `provider/model` form (for example `openai/gpt-5-mini`). Do not invent ids;
   the platform rejects an id that OpenRouter does not serve. Ask the user to
   confirm the list.
1. **Check readiness and cost.** Call `check_evaluation_readiness` with the
   dataset, its cell and these `targets`: one `recorded` target with
   `is_baseline: true`, and one `generate` target per candidate with its
   `model_name` and a `label`. Show `ready`, `estimated_cost_usd` and the context
   warnings. `suggestions` lists models with room for longer rows; offer them, do
   not add them yourself. Wait for the user to approve the spend.
1. **Run.** Call `run_evaluation` with the same targets and a stable
   `request_key`. Poll each returned run with `get_job(kind="eval_run")` until it
   is terminal.
1. **Compare.** Call `compare_evaluations` for each candidate run. Its baseline is
   the recorded run in the same group.

## Report

For each model, give the overall score, the change against the recorded outputs,
wins and losses, the per-metric changes and the trust flags. Name the cheapest
model that does not regress. Report errored or degraded rows as missing data, not
as low scores.

Switching the model is a change to the user's code or configuration. Show the
change and let the user apply it. For a model trained on Overmind, use
`set_active_model` only when the user asks.
