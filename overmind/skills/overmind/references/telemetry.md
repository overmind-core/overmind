# Telemetry and instrumentation

This guide is about **customer agent traces** (OTLP to your Overmind project via
`overmind.init()` / fan-out). It is not the SDK's anonymous PostHog usage
analytics (`OVERMIND_ANALYTICS_ENABLED` / `DO_NOT_TRACK`).

Use the native `investigate-capability` prompt for evidence and
`instrument-repository` when code changes are needed. MCP reads project data;
the SDK and the local coding workflow instrument the repository.

## Inspect production evidence

- `inspect_capability_health(capability, days)` returns offline score health,
  live trace-score health, trace count, error rate, and latency.
- `query_failures(capability, since_days, limit)` returns failing traces,
  evaluator reasons, tool names, and violated fields.
- `query_task_executions` is the behaviour-keyed unit view; inspect it before
  walking raw spans.
- `query_traces` supports capability, session, trace/span, status, model,
  duration, time, cost, search, ordering, and bounded pagination filters.
  `all_spans=true` includes child spans.
- `get_job` reads normalized status for an asynchronous project job.

For detail, read the returned resource links rather than inventing a second
read tool:

- `overmind://capabilities/{capability}`
- `overmind://traces/{trace_id}`
- `overmind://sessions/{session}`
- `overmind://jobs/{kind}/{id}`

Trace resources are bounded and may report `truncated=true`. Query results use
the server's summary/page fields; do not sum a partial page as a project total.

## Get and apply a plan

Call `get_instrumentation_plan` with no capability for project-wide work, or
with a capability and optional behaviour for a scoped change. Treat each
returned placement as an edit ticket and preserve its exact fields:
`key`, `behaviour_id`, `version_id`, `version_analyzed_sha`,
`contract_fingerprint`, `capability`, `capability_id`, `placement_mode`,
`allowed_keys`, `grain`, `target`, `required_scope`, `required_spans`, and
`required_identity`. `lineno` and
`source_line` are often null; file + qualname locates the function. A missing
registry returns an explicit human action; report it and stop this attempt.
Do not continue when `placements` is empty. If recovery is needed, run local
`/overmind setup` followed by `overmind sync`, then request the plan again in a
new attempt.

When subagents are available and repository policy permits coding delegation,
derive each ticket's touched files from its primary `target.file` and every
`required_spans[].target.file`. Group overlapping tickets under one owner so
two workers never edit the same file. The parent integrates the groups and owns
all verification.

The MCP server does not edit files. Apply the tickets locally and preserve the
required identity. Current SDK decorators include:

```python
@overmind.run(...)
@overmind.task(...)
@overmind.entry_point(...)
@overmind.workflow(...)
@overmind.tool(...)
@overmind.retrieval(...)
@overmind.observe(...)
```

Use the decorator named by the ticket. A run boundary should cover one
execution; use a turn-grain task for an independently scored phase and do not
wrap a run-grain task in a turn unit. Keep specialized spans nested under the
run. Do not guess keys, anchors, grains, or identity.

## Local SDK configuration

For a greenfield Python app, install the existing SDK extra locally:

```bash
uv add "overmind[tracing]"
```

Call `overmind.init` before provider clients are constructed and pass the
capability id from the capability resource. Configure the local process with
the project API key and, when needed, `OVERMIND_API_URL`,
`OVERMIND_CAPABILITY_ID`, and `OVERMIND_CAPABILITY_NAME`. Never put secrets in
decorated arguments; use the SDK redaction controls.

If the app already owns an OpenTelemetry `TracerProvider`, add the Overmind
OTLP exporter to that provider and enable the SDK's instrumentation rather than
replacing the existing provider. Preserve the app's existing exporter.

Useful local helpers are `overmind.set_conversation_id`, `overmind.set_user`,
`overmind.capture_exception`, `overmind.start_span`, and
`overmind.force_flush_traces`.

## Explicit run approval

After applying the ticketed code changes, report the changed files and local
checks. Generate a unique verification correlation value, then ask the user to
approve one fully specified choice. Fill every field; when a value is unknown,
write `needs user input` and do not run:

> How would you like to verify the instrumentation?
>
> - **Real run (recommended):** `<exact command or input>` against `<capability>`
>   in `<environment>` using `<provider/model>`. Expected side effects:
>   `<effects>`. Correlation: `<value>`. Approved attempts: `<count>`.
> - **Smoke run:** `<exact synthetic or read-only command or input>` against
>   `<capability>` in `<environment>` using `<provider/model or none>`. Expected
>   side effects: `<effects>`. Correlation: `<value>`. Approved attempts:
>   `<count>`.

No run begins until the user selects a mode. A real-run retry needs another
explicit approval unless the approval names a bounded retry count and exact
input. Keep this approval in the conversation/orchestrator state; do not add
an approval database field.

## Server-side trace verification

Both modes use the same verification boundary. The smoke branch uses synthetic
or read-only input. The real branch uses only the execution envelope presented
for approval. In either branch:

1. Use each ticket's exact `capability_id` in the instrumented run boundary.
   For scoped work, it may also be used as a query filter. For project-wide
   work, rely on the unique session correlation instead of inventing one
   capability filter.

1. Stamp the approved correlation as `conversation.id` with the application's
   existing mechanism or `overmind.set_conversation_id(...)` before the run.

1. Run the approved workflow and flush spans. Normal run boundaries flush on
   exit; isolated helper processes may call `force_flush_traces()`.

1. Query the exact session for root rows only:

   ```text
   query_traces(
     session=<unique correlation>,
     all_spans=false,
     limit=2
   )
   ```

1. Poll only a bounded number of times for ingestion delay and require
   `page.total == 1`. Zero or multiple root rows is a correlation failure, not
   a verification pass. Use the single row's `trace_id`.

1. Read `overmind://traces/<trace_id>`. Require `truncated == false` and
   `span_count == len(spans)`, then pass its `spans` list unchanged to
   `verify_instrumentation`. The trace resource and verifier share a 100-span
   limit; a larger trace is a reported blocker, not a partial pass.

1. Report instrumentation status separately from application outcome. A
   successful application run does not prove instrumentation quality, and a
   failed application run does not necessarily mean instrumentation failed.

If identity, nesting, payload, binding, correlation, truncation, or coverage
is wrong, report the repair required and return to the change/smoke path before
requesting another real run. Verification is read-only: it does not ingest
spans or write scores.

Credentials, tokens, passwords, and other secrets must be redacted before
decorated functions capture arguments or results. Multi-turn apps should set a
conversation id so traces group into the session resource.
