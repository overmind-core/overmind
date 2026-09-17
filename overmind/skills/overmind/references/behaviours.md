# Behaviours and task executions

Overmind's terminology is **Capability > behaviour > task execution**. A
behaviour is a scanned contract; a task execution is a carved run or turn that
can be bound and scored. The curated MCP surface exposes executions and
instrumentation tickets, not a behaviour list tool. Read behaviours from
`query_task_executions`; there is no `overmind://behaviours/...` resource.

## Inspect executions

Use `query_task_executions` with the `capability`, optional `behaviour`,
`trace_id`, `binding_source`, status, time, model, and cost filters. Results
include `behaviour_key`, `unit_span_id`, `trace_id`, `binding_source`, scores,
route flags, terminal kind, and a resource link.

Use `binding_source` to distinguish:

- `anchor_join` — joined to the scanned contract by its code anchor.
- `declared` — explicitly stamped by the SDK.
- `unbound` — no contract joined; this is an instrumentation gap, not a
  scoring request.

Task execution statuses are returned by the server; common terminal values are
`completed`, `error`, and `interrupted`.

## Use the instrumentation plan

Call `get_instrumentation_plan` with a capability and, when needed, a
behaviour. A behaviour reference requires a capability. Copy each returned
ticket exactly: key, behaviour/version ids, analyzed SHA, fingerprint, grain,
target file/line/source, required scope/decorators, allowed keys, and required
capability identity.

The server cannot edit the target files. Apply tickets locally, then send
caller-supplied spans to `verify_instrumentation`. That check is read-only and
does not ingest spans or write scores. If the result has `human_action` or no
placements, report the instruction and stop. When the registry is unavailable,
run local `/overmind setup` followed by `overmind sync`, then request the plan
again.

Do not guess behaviour keys, anchors, grains, or decorator targets.
