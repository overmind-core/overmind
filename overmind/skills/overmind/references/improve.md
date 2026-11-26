# Improve a capability

Use this to change a capability's prompt or code and show whether the change
helps. You author the change; live scoring measures it with the capability's
active eval set. A human applies the result.

**Prerequisite:** a synced capability with an active eval set (see
[Evaluations](../../overmind-evaluations/SKILL.md#configure-the-active-eval-set)),
an **eval** dataset for it and Overmind tracing in the application.

## Find what to fix

1. Call `inspect_capability_health` and `query_failures` for the capability. Read
   the failing metrics and the judge reasons on a few failed traces
   (`query_traces`, then the trace resource).
1. State the failure you will address and the metric that measures it. Do not
   change the eval set to make a change pass.

## Make and measure the change

The platform cannot run the user's application. A prompt lives in the
application, so a prompt change is measured the same way as a code change: run
the application before and after the change, and let live scoring score both.

1. Make the change on a new branch. Do not touch the main branch.
1. Read the eval inputs with `query_dataset`.
1. Before you run anything, show the user the command, the number of rows you will
   run and what the application may call (tools, paid APIs, production systems).
   Run only after the user approves.
1. Run the application on the same inputs twice, once before and once after the
   change, with Overmind tracing on. Give each run its own conversation id, so
   each set of traces is one session.
1. Wait for live scoring, then read `eval_score` for each set with `query_traces`
   filtered by `session`. Compare the two sets on the same inputs. A trace without
   a score is missing data.

This comparison is not a paired eval run: the platform did not freeze the rows or
pair the outputs. Say so in the report.

## Report

Show the diff, the metric it targets and the measured change. When the platform
did not score a result, call it unmeasured and give no estimate. Do not merge or
deploy the change; the user applies it.
