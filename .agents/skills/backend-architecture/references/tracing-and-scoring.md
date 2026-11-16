# Tracing and scoring

## Span model

Span-only: there is no Trace table. A trace is the spans sharing a `trace_id`; the root has `parent_span_id IS NULL`. `span_id` is the primary key. Timing is nanosecond integers (`start_time_ns`, `end_time_ns`, `duration_ns`), with no datetime column. `received_at` is ingest time. `process_span` fills the linkage: `capability` (resolved through `IdentityAlias`, never created) and `conversation`. `eval_attributes` holds the latest span score and, on a root, the trajectory.

## Ingest

`POST /api/v1/traces` (alias `/v1/traces`) accepts an OTLP protobuf or JSON export → flatten `ResourceSpans → ScopeSpans → Span` → bulk upsert on `span_id` → `process_span` → `enqueue_scoring`, which adds `LiveScoringWork` rows and wakes `dispatch_live_scoring`; `execute_live_scoring` then runs `span_processor.score_spans`. Connector imports use the same path.

Span cost has one owner: `services/span_pricing.stamp_span_cost` runs on every OTLP and connector span before storage. A reported cost (any alias, coalesced to `genai.cost`) is kept; otherwise `genai-prices` prices it from model, tokens and cache reads. The SDK never prices. Gateway billing is separate on purpose: it charges what OpenRouter reports (`usage.cost`), with `model_catalog.estimate_cost` list prices as the fallback.

## Read surface

`SpanViewSet` backs it; there is no `/api/spans/` route.

- `GET /api/traces/` — one row per trace head (`Span.trace_heads`: the root once it arrives, else the earliest span) with `trace_status` (`completed`, `live`, or `interrupted` after `TRACE_SETTLE_SECONDS`).
- `GET /api/traces/{trace_id}/`, `/api/traces/services/`, `/api/traces/models/`.

Filters are span-native (`received_at__gte`, `span_type`, `operation`, `service_name`, `has_error`). In the head view, `capability` matches any span in the trace; `all_spans=true` filters each span. REST, MCP and trace-source selection share this filter.

## Live scoring (`innermind/`)

`EvalMatrix` rows are `llm` (a one-sentence expectation judged by `evals.JevEvaluator`) or `deterministic` (`def evaluate(span)` run by `deterministic.py` in an audited `python3 -I` child: no imports, `json`/`re` preloaded, 5 s CPU, 15 s wall, 512 MB). Rows are project-wide or per capability and narrow by `span_type`/`span_name`; span-declared expectations add llm rows for that span. `innermind.definitions` derives the read-only `definition_source` of each row.

`span_processor.score_spans`:

- Claims a span only when it starts and writes each score once (`persist`: one guarded update plus its `EvalScore`).
- Unscoped llm rows apply only to the root or a capability's topmost span, never to tool or retrieval spans.
- A pass counts in `attempts` before its judges run, so a killed pass counts too. At `MAX_ATTEMPTS` (3) the failing rows become error metrics and no judge is called again.
- A span never waits for its ancestors. Intent falls back from the span, to the nearest arrived ancestor, to the root input, to the capability description. The beat sweep re-drives spans unscored after 90 s.
- Re-ingest keeps a score whose `fingerprint` matches the incoming span.
- Jev and explanation calls share one process-wide thread cap (`evals.throttled`); an event-loop semaphore cannot bound them.

`trajectory.score_trace` runs once, when every arrived span of a single-root trace is final, and stores `score` (the outcome), `passed`, `span_count` and `metrics` (`weakest_step`, `efficiency`, `path_judge`, `reference`) under the root's `eval_attributes.trajectory`; a single-span trace stores the outcome only. It shares the attempt limit.

Every span, trajectory and run-row score appends an `EvalScore` (`source_type` `span`, `trajectory` or `dataset_row`). The session score is the mean root trajectory score, using the root span score where a root has no trajectory.

## Signals

`signals.SignalRunner` (beat, every 5 min) reads every span and late score since the project's `SignalCursor` in pages, with no cap, and groups what they touch by conversation or trace. Spans old enough to cross the idle-session and missing-root thresholds get only the `time_based` checks. Deterministic signals run by default; stored `SignalSubscription` settings override defaults, and capability settings override project settings. `plan_drift` stays opt-in because it calls the judge. Each `evaluate` returns a `SignalFinding`; `Signal.details` stores it on the `SignalEvent` with thresholds, redacted evidence (first 10 spans) and recommended actions, with training first on model-caused checks.

An `Issue` is one failure repeating across traces, named by a fingerprint that ignores which check saw it, so one root cause raised by several checks is one issue; `family` is the same failure on any capability. `cause` decides the action: `model` issues can be trained away, `system` and `data` are fixed outside the model. `IssueTrace` holds exactly one row per issue and trace, stamped with the trace's start, so a trace falls in one hour and hourly distinct counts sum exactly. `innermind/traffic.py` recomputes `TrafficHour`, `ModelHour` and `IssueHour` from source rows for the hours touched, and a cursor only moves past what they counted. `services/issue_overview.py` reads only those rollups and computes rate, severity and state at read time: a failure surfaces at 5 traces across 2 hours, except a leak at 1. REST lives at `/api/issues/`; MCP is `list_training_signals`, `list_issues`, `inspect_issue` and `update_issue`. The Console's `/observe` is the new page; `/observability` is kept for comparison and reads `services/signal_insights.py`. `manage.py seed_observability` builds six months of production-shaped traffic in its own project through the real checks. A training run from a signal takes passing traces only, split into training and test sets, with each passing trace's answer as its reference; failing traces stay out because they record the wrong answer, and the issue's rate after deployment is the proof. That project sets `settings.simulated`, so `services/simulated_training.py` finishes its runs with generated, labelled numbers and refuses evaluation and optimiser starts.

`health.py` backs MCP `inspect_capability_health` and `query_failures`.

## Jev decisions

`core/decisions.py` calls the registered Jev model with structured choice questions and validates complete probability distributions. A generative model (`EVAL_EXPLANATION`) only writes reasons for low scores. Eval matrix llm rows call Jev with no generative fallback: transient transport failures raise so the span retries; other `DecisionError`s become metric errors. Fixed classification and Workshop semantic services keep a generative fallback through `services/judging/decisions.py`. Jev confidence is not calibrated correctness. Jev is excluded from inference and chat-model pickers. `POST /api/v1/systemone` proxies a body synchronously to OpenRouter, or to a fine-tuned decision deployment's Modal worker; it has no request key or receipt.
