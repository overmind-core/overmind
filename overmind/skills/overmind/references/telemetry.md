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
returned placement as an edit ticket: copy its target file, qualname, import
line, required scope, required decorators, capability id, behaviour key,
version/fingerprint, grain, and allowed keys. `lineno` and
`source_line` are often null; file + qualname locates the function. A missing
registry returns an explicit human action; run local `/overmind setup` and
`overmind sync`.

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

After applying the ticketed code changes, report the changed files and any local checks, then ask:

> How would you like to verify the instrumentation?
>
> - **Real run (recommended):** runs the actual workflow and provides the most representative trace coverage and highest-confidence verification. It may take longer and use normal provider, search, or application resources.
> - **Smoke run:** bounded and faster, but may exercise fewer branches.

No run begins until the user selects a mode. A real-run retry needs another
explicit approval unless the approval names a bounded retry count and exact
input. Keep this approval in the conversation/orchestrator state; do not add
an approval database field.

## Server-side trace verification

Both modes use the same verification boundary. The smoke branch uses
synthetic or read-only input. The real branch uses only the exact command or
input, capability, environment, provider/model, expected side effects, and
correlation value presented for approval. In either branch:

1. Use the plan's exact `capability_id` in the instrumented run boundary.

1. Generate a fresh unique correlation value such as
   `instrumentation-real-<nonce>` or `instrumentation-smoke-<nonce>` and set it
   as the conversation/session id.

1. Run the approved workflow and flush spans. Normal run boundaries flush on
   exit; isolated helper processes may call `force_flush_traces()`.

1. Query with the narrowest available correlation:

   ```text
   query_traces(
     capability=<capability_id>,
     session=<unique correlation>,
     trace_id=<known trace id if available>,
     all_spans=true
   )
   ```

1. Poll only a bounded number of times for ingestion delay. Group results by
   `trace_id` and require exactly one matching trace. Zero or multiple matches
   is a correlation failure, not a verification pass.

1. Read `overmind://traces/<trace_id>` and pass its complete `spans` list to
   `verify_instrumentation`. Respect the verifier's 100-span input limit and
   never claim completeness when the resource reports `truncated=true`.

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
