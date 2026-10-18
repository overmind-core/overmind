---
name: overmind-observability
description: Investigate Overmind traces, sessions, live scores, quality regressions, errors, latency and instrumentation gaps. Use for diagnosing observed agent behaviour rather than creating training or evaluation runs.
---

# Overmind Observability

Start with `list_projects` and choose the intended accessible project. For an
account connection, pass its `project_id` on every project tool and resource
URI query; follow returned links. Project API keys retain their narrower access.

Explain a measured problem using the smallest relevant set of scored traces. Use the configured MCP connection and identify the chosen project through
`overmind://project/current?project_id=ID` before combining evidence from the Console.

## Investigate

For a named capability, prefer the native `investigate-capability` prompt when
available. For a trace, session or time-window question, start directly with the
matching resource or `query_traces`; a capability is not required for every query.

Resolve the affected period, deployment/model and capability from the request
and available evidence. Use `inspect_capability_health` for aggregates,
`query_failures` for scored failures with each failed metric's reason. Follow
returned trace or session links to inspect the failing span's `eval_attributes`,
input, output, tool calls and parent-child relationships.

Keep comparisons on equivalent windows and cohorts. Report sample counts and
pagination or truncation limits. An empty result, an unscored span, a metric
error and a passing score are different observations. Separate
technical errors from quality failures and correlation from a demonstrated cause.

When the evidence supports a regression, identify the affected metric and
representative trace IDs, compare the relevant baseline, and explain the likely
cause with its uncertainty. A diagnostic request does not itself authorize
creating datasets, changing eval matrix rows or starting provider runs.

## Instrumentation gaps

For a requested instrumentation change, prefer `instrument-repository` and the
server's `get_instrumentation_plan`. Apply each placement's
`required_decorator` verbatim. A JavaScript/TypeScript application has no placements; it
exports OTLP, see the overmind skill's `references/javascript.md`. If the plan returns `human_action` or no
placements, follow that handoff; do not invent anchors.

Apply authorized edits locally. Before a verification run, present its exact
command/input, environment, model/provider, side effects, correlation and attempt
limit for approval. Use that correlation as `conversation.id`, query its trace,
require exactly one complete, untruncated trace, and pass its `trace_id` to
`verify_instrumentation`. The verifier does not ingest traces.
Report the application's outcome separately from instrumentation coverage.

Finish with the finding, supporting trace/execution links and the remaining
uncertainty. Open `observability` under the returned Console base with the same
`projectId` when the user wants the visual trace view.
