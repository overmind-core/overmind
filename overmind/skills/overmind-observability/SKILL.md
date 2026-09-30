---
name: overmind-observability
description: Investigate Overmind traces, sessions, task executions, quality regressions, errors, latency and instrumentation gaps. Use for diagnosing observed agent behaviour rather than creating training or evaluation runs.
---

# Overmind Observability

Start with `list_projects` and choose the intended accessible project. For an
account connection, pass its `project_id` on every project tool and resource
URI query; follow returned links. Project API keys retain their narrower access.

Explain a measured problem using the smallest relevant set of traces and scored
executions. Use the configured MCP connection and identify the chosen project through
`overmind://project/current?project_id=ID` before combining evidence from the Console.

## Investigate

For a named capability, prefer the native `investigate-capability` prompt when
available. For a trace, session or time-window question, start directly with the
matching resource or `query_traces`; a capability is not required for every query.

Resolve the affected period, deployment/model and capability from the request
and available evidence. Use `inspect_capability_health` for aggregates,
`query_failures` for scored failures and `query_task_executions` for behaviour
bindings. Follow returned trace or session links to inspect the failing step,
input, output, tool calls and parent-child relationships.

Keep comparisons on equivalent windows and cohorts. Report sample counts and
pagination or truncation limits. An empty result, an unscored execution, an
evaluator failure and a passing score are different observations. Separate
technical errors from quality failures and correlation from a demonstrated cause.

When the evidence supports a regression, identify the affected behaviour and
representative trace IDs, compare the relevant baseline, and explain the likely
cause with its uncertainty. A diagnostic request does not itself authorize
creating datasets, changing evaluators or starting provider runs.

## Instrumentation gaps

For a requested instrumentation change, prefer `instrument-repository` and the
server's `get_instrumentation_plan`. Preserve returned ticket identities,
placement modes and required spans. If the plan returns `human_action` or no
placements, follow that handoff; do not invent anchors or behaviour keys.

Apply authorized edits locally. Before a verification run, present its exact
command/input, environment, model/provider, side effects, correlation and attempt
limit for approval. Use that correlation as `conversation.id`, query its trace,
require exactly one complete, untruncated trace, and pass the returned spans
unchanged to `verify_instrumentation`. The verifier does not ingest traces.
Report the application's outcome separately from instrumentation coverage.

Finish with the finding, supporting trace/execution links and the remaining
uncertainty. Open `observability` under the returned Console base with the same
`projectId` when the user wants the visual trace view.
