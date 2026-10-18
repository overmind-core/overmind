---
name: overmind-observability
description: Investigate Overmind traces, sessions, live scores, quality regressions, errors, latency and instrumentation gaps. Use for diagnosing observed agent behaviour rather than creating training or evaluation runs. Tracing is set up for Python codebases only.
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

This guide is about **customer agent traces** (OTLP to your Overmind project via
`overmind.init()` or fan-out), not the SDK's anonymous PostHog usage analytics
(`OVERMIND_ANALYTICS_ENABLED` / `DO_NOT_TRACK`). MCP reads project data; the SDK and
the local coding workflow instrument the repository.

- `inspect_capability_health(capability, days)` returns live scoring health
  (coverage, average score, pass rate, trajectory score, per-metric rows with
  deltas), trace count, error rate and latency.
- `query_failures(capability, since_days, limit)` returns failing traces with each
  failed metric's score and reason, and the tools called.
- `query_traces` supports capability, session, trace/span, status, model, duration,
  time, cost, search, ordering and bounded pagination filters. `all_spans=true`
  includes child spans.
- `get_job` reads normalized status for an asynchronous project job.

For detail, read the returned resource links rather than inventing a second read
tool: `overmind://capabilities/{capability}`, `overmind://traces/{trace_id}`,
`overmind://sessions/{session}` and `overmind://jobs/{kind}/{id}`. Trace resources
are bounded and may report `truncated=true`; query results use the server's
summary/page fields, so do not sum a partial page as a project total. Span and
trajectory scores come from live scoring and eval-run scores from a frozen dataset
version: they are different observations, so a 1.0 eval run is not "no live
failures".

Keep comparisons on equivalent windows and cohorts. Report sample counts and
pagination or truncation limits. An empty result, an unscored span, a metric
error and a passing score are different observations. Separate
technical errors from quality failures and correlation from a demonstrated cause.

When the evidence supports a regression, identify the affected metric and
representative trace IDs, compare the relevant baseline, and explain the likely
cause with its uncertainty. A diagnostic request does not itself authorize
creating datasets, changing eval matrix rows or starting provider runs.

## Signals

`list_signals` reports defaults, effective enabled state and explicit overrides.
Deterministic checks run automatically every five minutes over spans received in the
last day. `set_signal_subscription` disables, enables or configures a check; a
capability setting overrides its project setting. `plan_drift` stays off until
explicitly enabled because it makes additional model calls. Read saved findings from
`overmind://signal-events`: events include conversation identity and their source
trace inventory, and `query_traces(signal_event=EVENT_ID)` inspects the retained
evidence in the same project. The Console shows Sessions, Signals and Traces inside
Observability. A `repeated_failure` names the expectation that keeps failing; inspect
its evidence before deciding whether the agent or the expectation should change.

## Instrumentation

Instrumentation is allowed only for a Python codebase. Before `get_instrumentation_plan`,
any edit or any `overmind[tracing]` install, check the repository's application code:
Python manifests (`pyproject.toml`, `requirements*.txt`, `setup.py`) and `.py`
sources, with no JavaScript, TypeScript or other-language application code. If it is
anything else, including a mixed codebase, do not allow tracing: set up no telemetry
or instrumentation, add no OTLP exporter, apply no edits, and report it as not
applicable with the language you found. A plan that returns anchors for such a
repository does not override this
([JavaScript](../overmind/references/javascript.md#no-instrumentation)). Reading
existing traces stays available. For a requested change in a
Python repository, prefer `instrument-repository` and the server's
`get_instrumentation_plan`. Live scoring reads what the SDK decorators write on each
span: the capability identity, input and output, tool names, and optional `intent`,
`expectations` and `reference_trajectory`.

### Get and apply a plan

Call `get_instrumentation_plan` with no capability for project-wide work, or with a
capability for a scoped change. Each placement names a `target` (`file`, `qualname`,
`module`, `import_line`), its `kind` (`entry_point`, `workflow`, `tool`, `retrieval`
or `function`) and the exact `required_decorator`. Apply each decorator verbatim at
`target.qualname` in `target.file`, with the capability id exact and the import from
`target.import_line`. If the plan has `human_action` or no placements, report its
instruction and stop this attempt; do not guess anchors or decorator targets. When
the registry is unavailable, run local `/overmind setup` followed by `overmind sync`
([local repository setup](../overmind/references/setup.md)), then request
the plan again in a new attempt.

When subagents are available and repository policy permits coding delegation, group
placements by `target.file` under one owner so two workers never edit the same file;
the parent integrates the groups and owns all verification. The MCP server does not
edit files. Current SDK decorators are `@overmind.run`, `task`, `entry_point`,
`workflow`, `tool`, `retrieval` and `observe`. A run boundary covers one execution;
keep specialised spans nested under it. The entry point carries the capability id:

```python
@overmind.entry_point(capability_id="<capability id from the placement>")
def handle(message: str) -> str: ...
```

Declare the expectations a span's own contract implies on its decorator;
`/overmind setup` writes them from the capability card and decorates every tool,
retrieval step, mode and helper the card names, not only the plan's anchors. Judge criteria live only on
decorators, and deterministic checks live in the toml's `eval_matrix`. After changing
decorator expectations, run `overmind sync`: it copies the expectations on entry
points, workflows and plain `observe` spans into the capability's Default eval set
([Evaluations](../overmind-evaluations/SKILL.md#configure-the-active-eval-set)).

```python
@overmind.tool(expectations=["Returns the order for the given id"])
def lookup_order(order_id: str) -> dict: ...


@overmind.entry_point(
    capability_id="...",
    reference_trajectory=["lookup_order", "issue_refund"],
)
def refund(message: str) -> str: ...
```

`intent=` overrides the user request read from the root input, and descendants
inherit it. `overmind.session(conversation_id)` groups spans into a session for the
block. `overmind.record_llm_call(model, total_tokens, cost)` stamps usage for LLM
clients the provider instrumentations do not cover. Other helpers are
`overmind.set_conversation_id`, `set_user`, `capture_exception`, `start_span` and
`force_flush_traces`. Credentials, tokens and passwords must be redacted before
decorated functions capture arguments or results; use the SDK redaction controls.
Multi-turn apps should set a conversation id so traces group into the session
resource.

### Local SDK configuration

For a greenfield Python app, install the SDK extra locally:

```bash
uv add "overmind[tracing]"
```

Call `overmind.init` before provider clients are constructed and pass the capability
id from the capability resource. Configure the local process with the project API key
and, when needed, `OVERMIND_API_URL`, `OVERMIND_CAPABILITY_ID` and
`OVERMIND_CAPABILITY_NAME`. If the app already owns an OpenTelemetry
`TracerProvider`, add the Overmind OTLP exporter to that provider and enable the
SDK's instrumentation rather than replacing the provider; preserve the app's existing
exporter.

### Explicit run approval

After applying the placements, report the changed files and local checks. Generate a
unique verification correlation value, then ask the user to approve one fully
specified choice. Fill every field; when a value is unknown, write `needs user input`
and do not run:

> How would you like to verify the instrumentation?
>
> - **Real run (recommended):** `<exact command or input>` against `<capability>`
>   in `<environment>` using `<provider/model>`. Expected side effects:
>   `<effects>`. Correlation: `<value>`. Approved attempts: `<count>`.
> - **Smoke run:** `<exact synthetic or read-only command or input>` against
>   `<capability>` in `<environment>` using `<provider/model or none>`. Expected
>   side effects: `<effects>`. Correlation: `<value>`. Approved attempts:
>   `<count>`.

No run begins until the user selects a mode. A real-run retry needs another explicit
approval unless the approval names a bounded retry count and exact input. Keep the
approval in the conversation/orchestrator state; do not add an approval database
field.

### Server-side trace verification

Both modes use the same verification boundary: the smoke branch uses synthetic or
read-only input and the real branch uses only the execution envelope presented for
approval. In either branch:

1. Use each placement's exact `capability_id` in the instrumented run boundary. For
   scoped work it may also be a query filter; for project-wide work, rely on the
   unique session correlation instead of inventing one capability filter.

1. Stamp the approved correlation as `conversation.id` with the application's
   existing mechanism or `overmind.set_conversation_id(...)` before the run.

1. Run the approved workflow and flush spans. Normal run boundaries flush on exit;
   isolated helper processes may call `force_flush_traces()`.

1. Query the exact session for root rows only:

   ```text
   query_traces(session=<unique correlation>, all_spans=false, limit=2)
   ```

1. Poll a bounded number of times for ingestion delay and require `page.total == 1`.
   Zero or multiple root rows is a correlation failure, not a verification pass. Use
   the single row's `trace_id`.

1. Call `verify_instrumentation(trace_id=<trace_id>)`. The server grades the ingested
   spans, so they never pass through the conversation. It is read-only: it does not
   ingest spans or write scores. Per capability it reports `identity`, `root`,
   `input`, `output`, `tools` and `scored_rows` as `ok` or `missing`, a punch list
   with the fix for each gap, and the eval matrix rows that would score each span. A
   trace over the verifier's span limit is a reported blocker, not a partial pass.

1. Report instrumentation status separately from application outcome. A successful
   application run does not prove instrumentation quality, and a failed application
   run does not necessarily mean instrumentation failed.

If identity, nesting, payload, correlation, truncation or coverage is wrong, report
the repair required and return to the change/smoke path before requesting another
real run.

## Report

Finish with the finding, supporting trace/execution links and the remaining
uncertainty. Open `observability` under the returned Console base with the same
`projectId` when the user wants the visual trace view.
