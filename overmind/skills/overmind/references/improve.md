# Improve a capability

Use this to change a capability's prompt or code and show whether the change
helps. You author the change; the platform measures it with the capability's
active eval set. A human applies the result.

**Prerequisite:** a synced capability with an active eval set (see
[Evaluations](../../overmind-evaluations/SKILL.md#configure-the-active-eval-set))
and an **eval** dataset for it.

## Find what to fix

1. Call `inspect_capability_health` and `query_failures` for the capability. Read
   the failing metrics and the judge reasons on a few failed traces
   (`query_traces`, then the trace resource).
1. State the failure you will address and the metric that measures it. Do not
   change the eval set to make a change pass.

## A prompt change

The platform can measure a new system prompt without running the application.

1. Write one to three candidate system prompts.
1. Call `check_evaluation_readiness` with the eval dataset, its cell and these
   `targets`: one `recorded` target with `is_baseline: true`, and one `generate`
   target per candidate with the capability's current model as `model_name`,
   `params: {"system_prompt": "<candidate>"}` and a `label`. Show the cost and wait
   for the user to approve it.
1. Call `run_evaluation` with the same targets and a stable `request_key`, poll each
   run with `get_job(kind="eval_run")`, then call `compare_evaluations` for each
   candidate.
1. Keep the candidate that improves the target metric without a regression on
   another metric. A replay covers one model call; it does not exercise tools or
   control flow.

## A code or tool change

The platform cannot run the user's code. Measure it with live scoring instead.

1. Make the change on a new branch. Do not touch the main branch.
1. Before you run anything, show the user the command, the number of eval rows you
   will run and what the application may call (tools, paid APIs, production
   systems). Run only after the user approves.
1. Run the application on the same eval inputs twice, before and after the change,
   with Overmind tracing on. Give each run its own conversation id, so each set of
   traces is one session.
1. Wait for live scoring, then read `eval_score` for each set with `query_traces`
   filtered by `session`. Compare the two sets on the same inputs. A trace without
   a score is missing data.

This comparison is not a paired eval run: the platform did not freeze the rows or
pair the outputs. Say so in the report.

## Report

Show the diff, the metric it targets and the measured change. When the platform
did not score a result, call it unmeasured and give no estimate. Do not merge or
deploy the change; the user applies it.
