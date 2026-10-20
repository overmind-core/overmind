---
name: backend-architecture
description: Deeper backend map of overbae — module layout, celery queue topology, the span-only tracing model and its API surface, capabilities and toml sync, matrix-keyed live scoring, evaluation runs and signals, auth and guests, model serving and base weights. Use when navigating unfamiliar backend subsystems or wiring cross-subsystem behavior.
---

# overbae backend map

Single Django app `overbae`, project-scoped tenancy.

## Layout

- `overbae/api/` — DRF views/serializers, one module per surface (`eval_views.py`, `datasets.py`, `optimizer.py`, `otlp.py`, `billing.py`, `mcp.py`, …). `views.py` and `serializers.py` are the legacy monoliths — new surfaces get their own module. Global exception handler returns `{detail, code, error_id}`.
- `overbae/models/` — split by domain (`iam.py`, `capabilities.py`, `traces.py`, `innermind.py`, `finetuning.py`, `datasets.py`, `optimizer.py`, `billing.py`, …).
- `overbae/innermind/` — evals: the eval matrix evaluators, live span and trajectory scoring, deterministic sandbox and seeded checks, evaluation runs (generation, replay, comparison, context), the Default set, health and signals. See § Scoring and § Evaluation.
- `overbae/services/` — business logic; largest subtrees: `datasets/` (Parquet store, landing, cell runner, contract, alignment, diff, use), `mcp/` (Streamable HTTP MCP server — procedure in the mcp skill), `codebase/`, `scan/` (AI-surface extraction, footprint matcher, scan application), `capabilities/` (identity, lifecycle, graph).
- `overbae/tasks/` — Celery tasks, roughly one module per feature; `utils/task_lock.py` for locking.
- `overbae/modal/` — Modal.com GPU workers (vLLM serving, SFT, native decision evaluation).
- `overbae/management/commands/` — backfills and syncs.

## Celery topology

Six workers, six queues. Workers are resource profiles; queues are fairness classes.

| Worker        | Pool    | Conc | Queues        | Holds                                                              |
| ------------- | ------- | ---- | ------------- | ------------------------------------------------------------------ |
| `control`     | threads | 8    | `control`     | orchestration, chord callbacks, FSM advances, beat                 |
| `io`          | threads | 24   | `io`          | preparation, eval launch, serving, connector polling               |
| `io_traces`   | prefork | 4    | `io_traces`   | live span/trajectory scoring and signals                           |
| `batch`       | prefork | 6    | `batch`       | pipeline bindings, evaluation generation/scoring, connector chunks |
| `landing`     | prefork | 1    | `landing`     | source imports                                                     |
| `interactive` | prefork | 4    | `interactive` | retained-package pipelines and runtime health                      |

Three constraints set the worker split. Only prefork enforces `time_limit` and `revoke(terminate=True)`, so every time-limited task routes to `batch`, `landing`, `interactive` or `io_traces`. A loaded prefork child costs hundreds of MB, so wide fan-out cannot be prefork. Orchestration holds its own lane because a chord callback stuck behind work never finalises its run. `run_eval_run` routes to `io` because Modal warm-up blocks for minutes; the import and workshop reconcilers and queue metrics run on `control` with short expiries and are dropped when it is full.

`io` and `io_traces` have exclusive worker pools. Round-robin delivery cannot protect IO once long-running trace tasks occupy every thread. Both pools disable prefetch. LiveScoringWork retains waiting span, trajectory and signal work in PostgreSQL; a row-locked dispatcher admits at most 16 items globally and two per project, ordered by least recent project admission. Each prefork attempt has a five-minute hard limit and a six-minute fenced running lease. Duplicate broker deliveries cannot claim a running or superseded token. Existing span claim grace and three-attempt rules govern recovery, never fabricated scores. Queue metrics include durable live-scoring backlog and oldest waiting age. Signal scans retain at most 20,000 lightweight span identities, then load complete selected conversation/trace groups in batches of at most 8 MiB of encoded payloads. SQL rechecks each batch before materialization. Oversized groups are explicitly unprocessed, other groups still publish, and the work receipt records a limit error; no group is truncated to fit. Deploy and verify the trace-only worker before removing trace consumption from IO. Drain old mixed consumers before stopping them; do not discard reserved tasks or revoke acknowledged provider work.

Every prefork lane (`batch`, `landing`, `interactive`, `io_traces`) sets `--prefetch-multiplier=1 --disable-prefetch`, so a busy process never reserves waiting work. A reserved late-ack message stays unacknowledged, and Redis redelivers it after the broker `visibility_timeout` (2 h, above every late-ack time limit; `tests/test_celery_topology.py` holds both). Landing workers consume only `landing`. Durable DatasetLandingWork admission keeps waiting imports in PostgreSQL, admits one per project in least-recent-project order, and follows observed worker capacity up to eight. Waiting age remains observable; only an explicit nonzero DATASET_IMPORT_MAX_QUEUE_SECONDS expires a waiting import. Started worker loss fences publication and requires explicit retry. Landing scale-out demand counts useful competing projects, and ScalingQueuedAgeSeconds is zero when more workers cannot unblock another project.

Evaluation generation (`innermind/generation.py`) admits `EvalRunRow`s under a global `EvalGenerationScheduler` row lock. `EVAL_MAX_IN_FLIGHT` bounds admitted provider work (default 12), with `EVAL_MAX_IN_FLIGHT_PER_PROJECT` (default 4 across all runs), `EVAL_MAX_IN_FLIGHT_PER_RUN` (default 2) and least-recently-admitted project/run fairness. Waiting rows remain in Postgres; completion and a 30-second control tick replenish available slots. Completion wakeups coalesce through a cache key, so at most one dispatcher waits on the lock. Failed/cancelled parents cannot claim or continue generation. Lost started attempts become explicit unknown outcomes and are not automatically repeated.

Chat HTTP clients enforce one cancellable elapsed deadline across request bodies, SSE heartbeats and retry waits. They close their connections on expiration; a socket read timeout alone is not a total time bound.

Hosted commands come from `docker/worker-topology.json`. Deployment runs migrations, verifies a healthy landing-only consumer on the new image, then updates workers, beat and API. Landing and interactive workers run with their queue as the Celery hostname, and `overbae.worker_health <queue>` checks that the consumer reads only its queue and registers that queue's task; a rollout of either waits for that health check. `scripts/plan_landing_capacity.py` generates the reviewed ECS, IAM, bounded scale-out and CloudWatch queue-age/newly-blocked-import/monitor-health plan. `NewlyBlockedImports` counts imports blocked in the last 5 minutes by a system cause (`queue_timeout`, `worker_timeout`, `dispatch_failed`, `attempts_exhausted`); a user-data failure does not raise it. Scale-in requires an explicit idle drain; automatic scale-in is disabled.

Routing lives in `CELERY_TASK_ROUTES` and must stay in sync with `make worker` (one process standing in for the whole fleet, so its `-Q` lists every queue) , docker-compose and the deployment command manifest. `tests/test_celery_topology.py` enforces it, and asserts no time-limited task lands on a threads lane. Workers hot-restart via watchmedo on `.py` changes.

The training reconciler renews a 30-second Redis lease while alive. Worker death
expires that lease instead of suppressing observations for five minutes. Domain
finalization records terminal operational facts on the refreshed job; reconciliation
repairs missed terminal events from durable local receipts without polling or
resubmitting a provider. Completion does not advance the worker heartbeat.

Modal training billing and forecast evidence use deduplicated worker usage, not
the interval before the collector observes completion. Billing retains hardware,
usage IDs and fetched GPU rates; missing/conflicting usage is unmeasured, never
replaced with local elapsed time. Ledger creation and the saved charge receipt are
atomic. Historical charges without a retained calculation remain unattributed;
they are not silently rewritten. This is measured GPU coverage, not an all-in
provider invoice.

## Tracing

Span-only: there is no Trace table. A trace is the set of spans sharing a `trace_id`; the root span has `parent_span_id IS NULL`. `span_id` is the primary key; `trace_id` is an indexed 32-char hex `CharField`.

`Span` carries OTel-native fields plus platform linkage:

- Identity: `span_id`, `trace_id`, `parent_span_id`
- Classification: `span_type` (`llm_call`/`tool_call`/`retrieval`/`workflow`), `operation`
- Timing: `start_time_ns`, `end_time_ns`, `duration_ns` — nanosecond ints, no datetime column
- Status: `status_code`, `status_message`
- Resource/scope: `service_name`, `resource_attrs`, `scope_name`, `scope_version`
- Payload: `attributes`, `events`, `links`
- Linkage, filled by `process_span`: `capability` (resolved through `IdentityAlias`, never created), `job`, `iteration`
- Ingest time: `received_at`
- Scoring: `eval_attributes` (latest span score, metrics and, on a root, the trajectory) and `eval_claimed_at` (the scoring claim)

Ingest: `POST /api/v1/traces` (with a `/v1/traces` compat alias) takes an OTLP protobuf export → parse `ResourceSpans → ScopeSpans → Span` → flatten → bulk upsert on `span_id` → `process_span` per span for linkage → enqueue `tasks.innermind.score_spans`. Connector imports enqueue the same task.

Span cost has one owner: `services/span_pricing.stamp_span_cost` runs on every OTLP and connector span before it is stored. A reported cost (any alias, coalesced to `genai.cost`) is kept; otherwise `genai-prices` sets `genai.cost` from model, tokens and cache reads at the span's start time. The span, trace totals and `Capability.usage_stats` all read that value, and the SDK never prices. Gateway billing is separate on purpose: `model_catalog.estimate_cost` charges what OpenRouter charged us.

Read surface, backed by `SpanViewSet` rather than a trace viewset:

- `GET /api/traces/` — one row per trace, its head span (`Span.trace_heads`): the root once it arrives, else the earliest span, so traces stream in as spans land. Each row carries `trace_status` (`completed` = root present, `live` = rootless and recent, `interrupted` = rootless and quiet past `TRACE_SETTLE_SECONDS`)
- `GET /api/traces/{trace_id}/` — every span sharing that `trace_id`, plus root summary and `trace_status`
- `GET /api/traces/services/` — distinct `service_name`; `GET /api/traces/models/` — distinct models
- No `/api/spans/` route exists.

Query params are span-native (`received_at__gte`, `trace_id`, `span_type`, `operation`, `service_name`, `has_error`). In the trace-head view, `capability` matches any span within the same project and trace; `all_spans=true` filters each span directly. REST, MCP and trace-source selection share this filter.

## Evaluation

An `EvalRun` scores one target (`mode` `recorded`, `generate` or `provided`) over one pinned dataset cell with a frozen `matrix` (`runs.freeze_matrix`: the eval set's rows, else every enabled row for the capability). Creation with several `targets` makes one run per target under a shared `group`, at most one `is_baseline`. Startup commits the running state and complete row inventory atomically before admission can generate or score; model warmup stays outside that transaction. `innermind/runs.py` owns the lifecycle (create, launch, generate, score, aggregate, finish, cancel, relaunch, reap stalled); `generation.py` and `replay.py` produce outputs; `dataset.DatasetRowProcessor` scores rows through the same span evaluators as live scoring, appending `EvalScore(source_type=dataset_row, run=…)`. A row stands for the capability's answer, so it takes every frozen row whose `span_type` is blank or `llm_call` regardless of `span_name`; `tool_call`, `retrieval` and `workflow` rows stay live-only. An optimiser experiment reuses its first run's `matrix` for later candidates, as a training job does for its after-eval. `comparison.compare` pairs the latest score per row both runs scored (`EPSILON` 0.005; improved/regressed/unchanged/added/removed; wins/losses/ties; trust). Finetuning before/after evals, optimiser iterations (`provided` runs fed by posted outputs) and backtests all create grouped runs and read `comparison`. A `provided` run cannot relaunch.

`innermind/context.py` supplies model-aware advisory estimates for generate targets and the explanation judge through REST `POST /api/eval-runs/context-check/`, run detail and MCP readiness/resources. Readiness accepts the proposed targets; context warnings never change `ready`, gate launch or exclude training candidates. Unknown limits remain unverified. Estimates include reserved output. `suggest_models` adds preflight-only alternatives from the configured registry, filtered by published context/output and tool/schema support; registry order is retained, never auto-selected. `cost_basis` scopes USD estimates to all checked inputs plus full reserved output for one pass per model/judge; unknown prices are null. Trained benchmark hosting costs must not borrow base-model token rates.

Training setup puts the judge selector below the eval set. `FinetuningJob.eval_judge_model` picks the explanation model for that job's runs; blank uses the `EVAL_EXPLANATION` registry default. It never changes Jev. Every evaluation of a job grades with the first run's frozen matrix, and sibling baseline reuse includes judge-selection identity. REST context preview and MCP readiness accept `judge_model` and return `judge_models`: all registry judge options, annotated with estimated fit and aggregate USD budget per evaluated model/dataset pass. Unknown or undersized options stay selectable. MCP `start_finetune` accepts the same immutable `eval_judge_model`. Console tints judge/benchmark selectors only for estimated limit overflows, mutes undersized options without disabling them, and uses a warning-coloured Start button to open a short risk summary with Start anyway. Unverified, unavailable and pending checks stay neutral and do not open the warning summary. Context issues never require a model change to launch.

Bounded decisions use `core/decisions.py`: registered Jev on OpenRouter's `/systemone` endpoint, structured choice questions, validated complete probability distributions, project/contract-scoped caching, a conservative UTF-8 context guard, and shared Redis request/token pacing. Eval matrix llm rows (`innermind/evals.JevEvaluator`, workload `innermind_expectation`) call it directly with no generative fallback: transient transport failures raise so the span is retried, other `DecisionError`s become metric errors, and state over `JEV_STATE_LIMIT` is clipped head-and-tail. The trajectory `path_judge` and the `plan_drift` signal use the same evaluator. Fixed classification and workshop semantic services keep Jev with generative fallback through `services/judging/decisions.py`; confidence is not calibrated correctness. Jev is excluded from inference and chat-model pickers. Eval comparisons can select `typesafe/jev-1.13` or `typesafe/jev-1.14`; judges stay on `typesafe/jev-1.13`. `POST /api/v1/systemone` proxies that body in the request: a catalog model goes to OpenRouter, and a fine-tuned decision deployment calls its Modal worker. Provider calls in tests are blocked unless explicitly stubbed.

Generated rows snapshot the runner's initial messages and tool schemas; the REST row detail and MCP run resource expose separate input, generated output and reference, with each metric's score, verdict and reason.

Standalone REST creation and MCP `run_evaluation` accept the same optional `judge_model` used by context preview. It is frozen in `EvalRun.judge_model`; blank uses the registry default. Existing runs reject judge changes. The Console only lists runs (read-only) and creates and deletes matrix rows (REST and MCP never edit a row); the judge selector and advisory Start warning live in training setup; run detail and the MCP run resource show the frozen judge.

## Capabilities and sync

The agent is the project itself — one graph per project, no table. `Capability` rows are its nodes (UI: "Capability"; the sidebar's "Agent" is the product).

- A scan never deletes: absence sets `status=leftover` and the row reactivates in place when the code returns; `observed` rows (telemetry- or hand-made) are exempt from leftover.
- `DELETE /api/capabilities/{id}/` is a soft delete (`status=deleted`): the row and its data stay, but it is invisible to lists, scans, and identity lookup, so a later scan of the same code mints a fresh row. There is no merge, split, retire, or history.
- Ingest never creates a capability: `services/capabilities/identity.lookup` resolves ids/names/slugs through `IdentityAlias` (renames included), an unknown identity leaves the span unbound, and `tasks/capability_rebind` re-binds the backlog after scans and hand-made rows.
- Wire identity is id-only: ingest binds spans by `overmind.capability.id` alone; `overmind.agent.*` attributes are not read; `overmind.capability.name` is a display label that never resolves. The `overmind/<uuid>` model alias never changes.
- Discovery is local: `/overmind setup` runs `overmind chassis` (AST inventory + call graph), writes `overmind_capabilities.json`, `convert_json_to_toml` fills prompt spans, drops fabricated anchors and unverifiable provenance, then stamps `trajectory_map[].verified`, then `overmind sync`.
- Tool branches and execution steps may carry explicit `transitions: [{target, condition}]`. `target` resolves to an exact step anchor on the owning path, or `terminal` for that path's result. Normalization, REST flow serializers and the capability MCP resource preserve these continuations; missing or ambiguous destinations stay unresolved. SDK declaration ownership retains module-qualified entrypoint anchors while span names retain their runtime qualnames, so repeated class names cannot select another module's entrypoint.
- Toml sync is `POST`/`GET /api/v1/sync` (API-key auth). It maps onto `Capability` (`current`/`leftover`), stores `system_prompt` / `eval_metrics` / `capability_card` / `eval_matrix` in `improvement_metadata`, keeps `repo_summary` / `trace_provider` / toml `version` in `Project.settings`, writes leftovers back to toml with `archived = true`, and calls `innermind.default_set.upsert` per current capability: the project's seeded checks (`innermind/checks`, created only when missing), one deterministic `EvalMatrix` row per toml `eval_matrix` entry with `type="deterministic"` (`checks.render` sets the catalog script's constants from `params`; sync validates it and answers 400 for an unknown `check` or constant), one llm row per legacy toml expectation (managed metrics a seeded check covers map to that check), one llm row per synced code expectation (`expectations` on the wire: `overmind sync` reads `expectations=` from the checkout's entry-point, workflow and function decorators via `overmind.chassis.declared_expectations`, scoped by `span_name`; the sentence is the row name, and `eval_preload.declared` lets the next sync delete rows the code no longer declares), and the capability's `Default` `EvalSet`, activated when none is active. The result lands in `improvement_metadata.eval_preload`.
- Repository provenance: `overmind chassis` saves a local `.overmind/scan.json` receipt before inspection. Conversion verifies branch, commit and a SHA-256 fingerprint of tracked and unignored working-tree files (excluding generated scan files), then persists `repository_snapshot` in toml. A changed checkout requires a rescan. Sync never recaptures Git state: it stores provenance in `Project.settings` and stamps `last_synced_at` separately; a push without provenance clears the prior label. The agent graph and `overmind://project/current` expose both. Missing Git or a missing scan receipt leaves provenance unknown.

## Scoring

Matrix-keyed, in `overbae/innermind/`. `EvalMatrix` rows are `llm` (one-sentence expectation, `evals.JevEvaluator`) or `deterministic` (`def evaluate(span)` run by `deterministic.py` in an audited `python3 -I` child: no imports, `json`/`re` preloaded, frozen span plus parent/children/siblings, 5 s CPU / 15 s wall / 512 MB). Rows are project-wide or per capability, narrowed by `span_type`/`span_name`; span-declared `overmind.expectations` and `expect("constraint", …)` events add llm rows for that span.

REST and MCP expose a read-only `definition_source` for each matrix row. `innermind.definitions` matches saved content against the stock catalogue, synced code expectations or repository configuration; otherwise a recorded creator yields `custom`, and missing evidence yields `unknown`. This describes the current definition, not historical authorship. The Console separates Stock and Agent-specific rows, defaults to enabled rules, and labels the definition location.

- `span_processor.score_spans` skips spans that hold a score, claims each span (`eval_claimed_at`) only when it starts, applies the matching enabled rows per span type (`SpanProcessorFactory`; unscoped llm rows only on the root or a capability's topmost span, `metrics.answers_for_capability`, never tool/retrieval spans) and stores each span as it finishes (`persist`: one guarded update plus its `EvalScore`, so a score is written once) as `Span.eval_attributes` `{version, intent, fingerprint, score, metrics}`. Rows with the same expectation are judged once per span, and a computed row's metric is kept in `partial` so a retry judges only the rows that failed. A judging pass is counted in `attempts` before its judges run, so a killed or hung pass counts too: a row failing on the third (`MAX_ATTEMPTS`) pass is recorded as an error metric, and a span whose passes are spent is scored with error metrics without calling a judge. A span never waits for an ancestor or the root: it is judged with what has arrived, intent falling back from the span's own and the nearest arrived ancestor's `overmind.intent`, to the root's input if it has landed, to the capability description, and the 120 s beat sweep re-drives spans unscored past 90 s (newest first). Batches keep a trace together and load only the trace's roots and each span's arrived ancestors, plus its parent's children and its own when the project has a deterministic row. Re-ingest keeps a score whose `fingerprint` (name, type, parent, status, attributes) matches the incoming span and resets `eval_attributes` only when the evidence changed. Jev and explanation calls share one process-wide thread cap (`evals.throttled`); an event-loop semaphore cannot bound them, since every task has its own loop.
- `trajectory.score_trace` runs once, when every arrived span of a single-root trace is final; spans arriving later do not score it again: `outcome` (mean over answering spans, the trajectory score), `weakest_step`, `efficiency`, `path_judge`, `reference`, stored under the root's `eval_attributes.trajectory`. Its passes count in the root's `trajectory_attempts` under the same `MAX_ATTEMPTS`; a `path_judge` still failing then is an error metric.
- Every span, trajectory and run-row score appends an `EvalScore` (`source_type` `span`/`trajectory`/`dataset_row`); `GET /api/eval-scores/` reads it. The session score is the mean root trajectory score.
- `signals.SignalRunner` (beat every 5 min) checks each project with spans received in the last day, grouped by conversation or trace. Deterministic checks run by default without materializing subscriptions. Stored `SignalSubscription` settings override defaults; capability settings override project settings, including mutes. A per-project cache lock prevents overlap. `plan_drift` stays opt-in because it calls the judge, recording verdicts in `eval_attributes.drift` with bounded attempts. Events retain their source trace IDs in `details.trace_ids`; REST `/api/traces/?signal_event=UUID` and MCP `query_traces(signal_event=UUID)` share the project-scoped evidence filter. Historical events without the inventory retain their original trace link. Console Observability has three views: Sessions, Signals, Traces; no separate Signals page. REST `/api/signal-subscriptions/` configures overrides; MCP `list_signals` reports defaults and effective state, `set_signal_subscription` configures overrides, and `overmind://signal-events` reads saved findings.
- `health.py` backs MCP `inspect_capability_health` and `query_failures` from `eval_attributes`.

API: `api/eval_views.py`, `api/signal_views.py`.

## Data Workshop

Source → cells → derived versions. Native coding agents author reusable project revisions or attributed variants. Shared services validate lineage, execute retained packages through interactive workers in the isolated sandbox, and atomically publish step cells. Preview never publishes; explicitly enabled bindings rebuild changing source snapshots without agent authoring. Platform chat/agent tasks are retired. The dataset table opens existing cells with run inspection; historical records remain readable. Contracts and lifecycle: data-workshop skill.

## Auth and tenancy

Clerk when `CLERK_API_SECRET_KEY` is set; blank secret is self-hosted local JWT (`POST /api/auth/local/` — email + password, create on first use, no verification). ChatGPT login, linking, funding and credential storage are removed. Existing users remain; accounts without a usable password need administrator password setup. Project-scoped tenancy (`User` ↔ `Project` via `ProjectMembership`), no org layer. Everything queryable is filtered by project.

- A guest (`User.is_guest`, minted by `POST /api/auth/guest/`) holds one project and can read and claim. `GuestJWTAuthentication` (`api/authentication.py`) refuses every other write with `guest_upgrade_required`; a view opts in with `guest_allowed = True` — never a permission class or middleware, since a view's own `permission_classes` replaces the defaults and a guest identity exists only through that token. Guests get no free credits; `/demo` points at local setup. A claim moves the memberships to the Clerk account and deactivates the guest; `tasks/guest_cleanup.py` deletes inactive guests and unclaimed ones after 7 days. Guest claim requires Clerk.
- Console: `VITE_SELF_HOSTED=true` and a blank `VITE_CLERK_PUBLISHABLE_KEY` skip `ClerkProvider` and show the local login form; otherwise Clerk.
- Project invites: with Clerk, an invitation email is sent; without Clerk the `ProjectInvite` row is stored and claimed on first local (or Clerk) sign-in for that email.
- Commercial billing (remaining-credit 402s, the Free seat limit, Stripe Checkout) injects when `STRIPE_SECRET_KEY` is set (`overbae/services/billing_provider.py`). Ledger charges always run. Empty key → uncapped OSS: spend is recorded and shown, gates and grants no-op.
- API keys are either `scope=account` (every project the user belongs to) or `scope=project` with one `resourceIds` entry. Creating a key with `project` always mints project scope.

## Training preparation

Chat-training cost estimates use dataset text statistics and assumed H100 throughput, not an exact tokenizer report. Native forecasts use compatible completed execution measurements as described below. Modal GPU compute uses current workspace rates; Baseten uses its per-minute rate. Modal CPU, memory, storage and rate multipliers are additional. Exact preprocessing is a separate operation and does not validate the duration estimate.

Workshop findings are advisory before model-specific work: incomplete task/evidence/answer/schema reviews, capability mismatches and train/eval overlap produce warnings, not launch blockers. Exact preprocessing checks technical compatibility and does not repair datasets. Fine-tuning validation is read-only, and successful creation freezes the selected train, validation and `eval_cell` versions atomically. The same pinned eval cell is reused for before/after runs; dataset versions and capability cannot be swapped after creation. Internal loss-validation splitting preserves duplicates and cannot silently clean selected data.

Workshop is model-independent. Native agents inspect before transforming; historical source-bound plans and measured reviews stay attached to their exact versions. New pipeline/import runs record source bindings, impact and external attribution; no automatic semantic audit is scheduled. `services/training_preparation.py` caches exact Modal preprocessing by training/validation cell fingerprints, model, training type, context length, training stack and the SFT asset code fingerprint. REST `POST /api/training-preparations/` and MCP `prepare_training_data` share the service; MCP returns a `training_preparation` job receipt readable through `get_job` and the jobs resource. Preparation stages checksummed gzip JSONL through Modal Volume uploads and uses CPU-only `prepare_<train_function>` in the matching training image; it does not start GPU training. Tokenization writes artifacts incrementally. All Modal launches upload ordered binary SHA-256 row selections instead of the source rows a second time. The worker verifies each selection checksum and count plus the caller-pinned token artifact, then materializes training/validation through a bounded disk index, preserving order and duplicate visits. Initial source decompression and selection construction stream without retaining the corpus in memory.

Explicit preparation atomically creates a `FinetuningJob` linked by `preparation`, before the preprocessing task is queued. `preprocessing` and `awaiting_launch` are dashboard-visible setup states excluded from the training dispatcher and launch admission. The preprocessing observer updates setup progress and failures, without changing launched or cancelled jobs. A matching REST/MCP launch promotes the same job under the project lock, applies normal budget admission and freezes the complete recipe; repeating its launch key remains idempotent. Retrying a setup failure never launches GPU training. MCP preparation returns both preparation and training job receipts.

Console setup does not submit or await preprocessing and has no per-model preparation cards or workshop review recommendations. `run_finetuning` requests or reuses the exact artifact after launch, records its ID, state and report in job progress, and starts GPU training only when ready. Provider observations and finalization preserve that receipt and submission recovery history. Explicit REST/MCP preparation remains available.

Explicit preparation rejects unsupported context lengths rather than silently rounding them to a supported bucket. Model-specific recommendation and internal context selection happen before requesting that exact preparation.

Preparation reports source-export and tokenization row counts against the pinned total, then names upload, file validation and provider submission separately. The training monitor reads the current `TrainingPreparation` receipt on its fast metrics poll; the MCP training-job resource uses the same current progress, so neither has to wait for the job controller's next copy of the receipt. Counts describe their own stage, not overall training completion.

Training handoff separates selection construction, acknowledged selection-file uploads,
provider artifact verification, indexing, row selection and publication. Only successful
Modal batch completion increments acknowledged bytes/files; live network-byte progress
is unavailable. The pinned worker publishes bounded transfer measurements to the run's
`transfer-progress.json`; the existing 15-second reconciler collects them even while the
submission task is active. Console and MCP read persisted, attempt-fenced diagnostics
and operational events, never invoking a worker to read status. Stage start, source
observation and last forward progress remain separate; absent measurements show no
percentage. Worker instrumentation requires deployment of a new immutable release and
cannot retrofit a running pinned call.

GPU startup records file staging, runtime imports, weight loading, adapter setup,
tokenizer verification, dataset loading/building and trainer/optimiser startup
separately. Dataset construction reports processed rows; opaque library weight
loading has no invented percentage. Console and MCP expose stage start, source
observation, worker heartbeat and last forward progress. A committer heartbeat
does not advance progress; each new attempt resets its stage clock.

Recommendations compare the stored longest row, plus training headroom, with each model's LoRA and full fine-tuning context. A model is dropped when neither kind covers that length. A candidate keeps only the kinds that do, so a dataset that fits LoRA and not full fine-tuning is offered as LoRA only, and the reverse. Job preparation repeats sizing across the pinned training and enabled validation cells, preserving a larger requested context. A requested context equal to that estimate is still the first pass, including when a launch or setup job stored it: if exact tokenization exceeds it but fits the model's training-type limit, preparation requests the measured bucket and rechecks every row before the job fails. A different requested context stays fixed and overflow is rejected. Exact lengths need no estimate headroom; over-limit rows and other incompatibilities still block. No dataset is rewritten, truncated or filtered. A training-job retry can recover an undersized context through this same path.

`TrainingPreparation` persists queued/starting/running/ready/incompatible/failed, the remote call ID and a 24-hour deadline. A 15-second controller queues short `io` observers. Restarts poll the saved call; transport failures retain it. An unacknowledged submission fails closed. Explicit retry of a confirmed failure cancels its saved remote operation before requeueing; uncertain submissions cannot be retried automatically. REST `/retry/` and MCP `retry_failed` use the same guard. Retrying a failed/cancelled training job also retries its cached confirmed-failed preparation before queuing training, unless GPU training already has a remote ID. Ready or in-flight preparations are reused; unsafe failures and incompatible data refuse the retry without resetting job or eval state.

`sft_assets/preprocess.py` uses the trainer's `pretok` path, reporting exact token counts, shifted supervised targets, decoded masked previews and incompatible source-row IDs. It rejects over-context rows and zero-supervision examples without truncation or silent deletion. A ready artifact stores token IDs/labels, checksum and tokenizer; `upload_dataset` only materializes rows present in that artifact. The GPU engine validates context/vocabulary and consumes those tokens without preprocessing them again. Changes to the model/context or data require a matching artifact. Other providers retain their existing provider-side preprocessing; do not label those configurations exact-preflight-ready.

Changes to the SFT assets or preparation functions require `modal deploy overbae/modal/modal_sft_worker.py`; recreating local Docker services does not deploy Modal code. A worker/code fingerprint mismatch blocks preparation until the matching worker is deployed. Preprocessing infrastructure failures return a safe, actionable error and remain `failed`, distinct from incompatible rows. Each preparation retains subprocess stdout/stderr at `preparations/<id>/preprocess_stdout.log` on the SFT volume, including failed attempts; raw diagnostics do not enter user-facing error messages.

Unsloth token accuracy is measured from the final decoder output through the actual
output head, in bounded chunks over supervised next-token targets. Fused CE stays
enabled; accuracy does not depend on returned logits or a batch/context threshold.
Counts are token-weighted across microbatches, with separate training and validation
windows; checkpoint recomputation does not count twice. A missing decoder capture
fails explicitly rather than silently dropping the metric. A rolling Modal deploy
leaves active calls on their existing code; it cannot add metrics to an already-running
trainer or reconstruct historical accuracy.

## Development monitoring

`modal_shared/training_monitoring.py` owns strict policy resolution, whole-key/group
sampling, the timing scheduler and deterministic generated-output metrics.
`training_monitoring_runtime.py` persists atomic worker check/checkpoint receipts.
The chat callback preserves RNG, module modes and outer TrainerControl flags;
all teacher-forced checks use evaluation counters, including the fixed training
reference. Native checks preserve probability/ordinal targets and existing resume
artifacts. Initial/final checks are separate from periodic overhead scheduling.

Modal transfer freezes a manifest after exact token materialisation and before GPU
dispatch. `services/training_monitoring.py` stores the manifest, validates immutable
receipts, collects generated evidence in background and exposes passive paginated
reads. Preparation alone has no chosen development split. Check operational events
link to `TrainingValidationRun`; `TrainingCheckpoint` records checksums, reload
verification and explicit resume limitations. A locally interrupted check may gain
late terminal evidence without resurrecting a cancelled training job. Retained
checkpoints prevent volume cleanup; intermediate download/pruning is not yet exposed.

Status observation ingests metrics and checkpoints without downloading examples.
Missing evidence is `pending` until the per-job-locked IO collector reads each sealed
Modal Volume artifact once, with a 30-second read deadline and checksum verification.
Each verified check is persisted independently; duplicate collection skips saved
files and cannot overwrite newer monitoring facts. This keeps large development
sets and unavailable evidence downloads out of the shared training status loop.

The shared policy is accepted in readiness, preparation, estimates and launch;
Console setup uses the same hyperparameter payload. Defaults are adaptive with
2,048 loss rows, 256 reference rows, a 300-second interval target, 10% overhead
target and at most 12 interim checks. Whole groups can exceed target sizes.
Generation is opt-in and supports explicit classification labels, exact matching
and local JSON Schema or declared `json_fields` checks on Modal. The field scorer
accepts 1–64 explicit JSON Pointers, preserves boolean/number/null distinctions,
decimal precision and array order, and rejects ambiguous duplicate JSON keys.
Missing or malformed references remain unscorable; model-invalid JSON fails.
Whole-example and per-field pass rates retain their own coverage. Undeclared
fields are not judged; this does not infer tool contracts or execute tools.
Inputs exclude the final supervised answer;
generation never executes tools. Paired changes use group-bootstrap descriptive
intervals, not final-test guarantees. Metered judges, independent challenge suites
and declared-strata sampling are rejected as unsupported, not silently ignored.

MCP `inspect_training_progress(job, check|probe|field, offset, limit)` and REST expose
the same retained data without provider calls. Native per-question metrics and
assessments appear as `collections` descriptors in MCP overviews. Pass their
`field` JSON Pointer to page exact object entries or array items; append escaped
keys or indices for nested fields. REST monitoring-evidence shares this read path.
MCP evidence over 128 KiB fails explicitly; clients reduce page size or select a
deeper field. `cancel_finetune` and REST share intent-first cancellation
and delayed evidence collection. Optional check failures remain durable; explicit
best-loss selection/early stopping require successful checks. Monitoring cost is
not yet included in the forecasting model and is labelled unmeasured.

The chat monitoring context restores RNG, module modes, gradient checkpointing,
cache settings and the provider's training mode after generation. A training-
reference evaluation uses the same loss path but never emits a development-curve
point. Native prediction receipts preserve probability vectors, original target
semantics and weights. Completion keeps the frozen manifest and final monitoring
observation instead of replacing them with a provider-only progress projection.
Precision with no predicted examples stays null, while missed represented labels
retain zero recall/F1. Paired classification changes exclude references outside
the frozen label contract. Evaluation announces zero completed batches before
the first forward pass, so completed sample construction is not mistaken for
ongoing validation progress.
Completed loss, reference and generation measurements are published while the
overall check is still running; later checkpoint failures retain those facts.
Adaptive schedule formula 2 retains the preceding interval across restarts, holds
changes within a 20% deadband and bounds larger changes to half/double per check.
A bounded cadence that misses the overhead target reports the conflict. Normal
run/check-count boundaries and explicit step/epoch modes do not imply a budget
failure. A measured overhead interval that prevents an otherwise due interim
check is reported separately. Active jobs retain their pinned scheduler release.
Launch credit admission runs inside the project-locked creation path only
after request-key recovery. Identical REST/MCP retries remain readable after
credits are exhausted; changed recipes conflict and new jobs remain gated.
Chat success records the deduplicated training GPU charge before deployment;
deployment readiness and its own usage cannot delay or replace the training receipt.

`training_experience.py` adds `facts.delivery.first_result_at` and `finished_at`
from the collector's receipt clock, independently of optional analytics. An available
result requires finite loss and complete nonempty loss coverage; it does not
claim generated-output success. Repeated receipts and late evidence attachment
preserve those times. Cancellation records an interruption under the same job lock.
Configured PostHog receives `training_result_available`, `training_check_finished`
and `training_checkpoint_available` after transaction commit with stable event IDs.
Events contain project/job/check IDs, counts, measured timing and explicit resume
availability, not labels, text, error messages or artifact paths. Delivery is
best-effort, bounded and nonblocking; database receipts remain authoritative.
Blank `POSTHOG_PROJECT_TOKEN` disables delivery, including local self-host defaults.
Analytics elapsed time is measured after commit; receipt times are recorded before
commit and are not a guarantee that another client could already read them.
The earliest result event per job measures server availability, not user comprehension;
unobserved-time attribution and explicit human feedback remain separate work.

`training_quality.py` derives versioned classification assessments during receipt
collection, never on reads. It validates frozen labels, policy identity, confusion
counts, scored supports and accuracy before comparing with the same scored subset's
majority-label baseline or noting represented labels without predictions. Original
worker metrics, findings and receipt fingerprints remain unchanged. Missing or
inconsistent counts are inconclusive. Late duplicate collection can add this
separately attributed assessment without replaying work; stable IDs deduplicate
findings. No automatic training action follows. Compact MCP summaries retain the
latest generation check independently from loss-only checks, ordered by attempt
then step; detailed checks retain complete denominators and provenance.

## Serving and weights

`services/operational_progress.py` stores project-scoped `OperationalRun` attempts
and append-only events. Domain controllers, not status reads, collect provider
events. `provider_progress.py` copies the serving journal into the database with
durable cursors. `modal_shared/operational_events.py` publishes bounded-time
measurements; unavailable delivery does not fail or replay GPU work. Shared-pool
startup observations are not tenant-adapter attestation. Preparation and training
retain their existing row/stage/heartbeat sources in the shared observation model.
MCP `inspect_operation` pages history; inference result resources support content
offset/limit paging. Captured history is durable; absent provider instrumentation
and dropped publications remain explicit rather than reconstructed.
Collection backlogs survive terminal jobs and drain in background for up to one
hour after the last active observation. Engine health waits publish process
liveness without advancing the work counter. Failed publications and dropped
events are separate facts. Exported row counts never measure upload completion.

MCP inference uses `services/inference_requests.py`: save project request key,
input/routing fingerprint and payload before dispatch, then the 15-second controller
claims, submits or observes the existing Modal call. The worker publishes its
request-to-call mapping before inference, enabling acknowledgement recovery. A
client reconnect never owns execution. Unknown submissions are never reissued;
an expired observation deadline is unresolved, not proof of provider cancellation.
Unresolved requests remain eligible for five-minute reconciliation without
resubmission; terminal receipts recover missing usage records without another
provider call.
The ordinary application completion API retains its streaming/synchronous contract.

`services/model_activation.py` owns making a ready deployment live. REST `active_model` writes and MCP `set_active_model` persist a per-capability `ModelActivation`; the 15s deployment reconciler advances checking, waking/verifying via the existing `pre_warm` handle, and atomic routing switch. Until verification succeeds the previous target serves. Claims expire after 45s, transport errors retain the remote handle, and activation has a 50-minute deadline. Failed activation is retried through the same selection operation; duplicate in-progress selections are idempotent, competing selections are rejected, and clearing routing invalidates the operation generation. The capability retains its previous selection for verified switch-back. First/last application request timestamps only advance on successful API-key calls to the capability alias, excluding optimiser traffic and requests begun before the current switch. Internal probes and pinned calls never confirm alias integration.

`services/inference_metrics.py` supplies REST and MCP deployment metrics and activity. Both surfaces share `period` (1h, 24h, 7d, 30d, all) and `source` (application, all) filters; API defaults remain all time/all traffic, while the Console defaults to application traffic in the last 24 hours. The same selection applies to totals, failure counts, cost coverage, latest request/failure and chart buckets. Empty time buckets have zero requests and null latency; successful end-to-end timings include cold requests. Percentiles use the most recent 5,000 measured successful calls (per bucket for activity). Failed calls count as requests but do not contaminate successful latency distributions. Historical end-to-end timings and traffic sources remain unknown. `services/inference_live.py` reads Modal stats from the parameter-bound worker method without invoking inference. Pool identity includes base identity, GPU/image, context and LoRA rank; LoRA deployments use the shared base name. Counts are cached for 5 seconds and remote reads have a 10-second deadline. Recent successful inference (including deployment/activation verification) and gateway warming stamps remain fresh on every read. Zero measured runners means asleep; unavailable measurements remain unknown. Class-wide counts cannot establish one deployment's warmth.

`services/serving_context.py` estimates the pinned evaluation workload (including the capability prompt and reference-output headroom) independently of training `context_length`. Model recommendations with an eval dataset and REST/MCP launch share the serving plan. Estimated context overflow warns without excluding a candidate or rejecting launch. The plan respects native context without assuming RoPE scaling and still validates single-GPU capacity. Deployment and base-model evaluation use the plan; all before/after calls reserve the same output budget. A shared baseline grows only from a settled deployment state. Existing ready fine-tunes are not silently reconfigured.

`modal_shared/context_budget.py` supplies a positive default output reservation and rejects prompt truncation. Both streaming and non-streaming serving enforce it; vLLM validates the exact templated input plus reserved output before generation. MCP `run_inference` uses the same default reservation (8,192 tokens) when `max_tokens` is omitted or null, passes explicit positive integers unchanged, and returns non-retryable `context_length_exceeded` errors for requests that do not fit. Core LLM calls preserve finish reasons and reject token-limited responses before parsers or tool executors consume them. Eval runners retain the partial answer and usage and mark the row degraded; degraded rows are excluded from trusted scores rather than scored as zeros. Finalization synchronizes training-eval status explicitly; degraded runs cannot become successful training-quality comparisons. MCP inference returns finish reason, model truncation and response-content clipping separately.

The MCP deployment resource exposes current `worker` state and measurements through `inference_live.worker_status`, independently of deployment readiness, alias selection and historical metrics filters. It uses the Console's cached pool measurements and recent traffic/warming stamps without invoking inference. Unavailable counts stay null; state remains unknown unless recent successful traffic or a warming stamp supplies evidence.

Training jobs and eval sets may have no capability. Training still requires an eval dataset
and an eval set with at least one enabled matrix row; a job without a capability can also
select a ready trained benchmark. Unassigned eval sets are project-scoped and cannot be activated.
REST `POST /api/eval-sets/` and MCP `create_eval_set` share the serializer that validates `rows`.

Training has four persisted evaluation choices: `eval_incumbent_before/after` and
`eval_model_before/after`. The training model's before target is its untouched base;
after is its ready trained deployment. The training setup's **Benchmark model** selector
chooses the codebase incumbent or a ready, project-scoped trained model for this run.
REST and MCP `start_finetune` accept its serving model ID as `baseline_model`; the choice
is immutable once the job is created and never changes `active_model` or serving.
Omitting it uses the capability's separate `benchmark_model` default (set through MCP
`set_benchmark_model`), or the codebase `model` when that default is null. The Console
defaults to the codebase incumbent and sends its selection explicitly. New incumbent
evaluations reject an unavailable selection. The capability Models tab only selects live routing.
Every foundation entry in `models.json` carries an explicit nullable `openrouter_id`.
The shared resolver searches the cached OpenRouter catalogue by that mapping, then
exact model/checkpoint identities (including declared benchmark aliases), never fuzzy
names or another model size. Chat models retain their existing core registry slugs.
MCP and REST training catalogues expose `openrouter_id` and `openrouter_status`:
`available`, `not_listed`, or `catalog_unavailable`. A retained ID alone does not prove
availability; null mappings can resolve when an exact checkpoint becomes listed.
When configured and matched, before evals use OpenRouter ahead of an unused ready
hosted base. Unavailable models/catalogues retain the provider/Modal fallback.
A started evaluation or unresolved attached deployment pins its route; refreshes
do not switch live evaluations or duplicate in-flight deployment work.
Defaults are untouched-base-before + trained-after; incumbent comparisons are
separate opt-ins. The matched base is the preferred delta reference. All four can be off, but the eval
dataset and eval set remain required. Baseline evals launch as independent jobs alongside
GPU training; their completion and failure do not gate training. Retries retain EvalRuns
but detach failed baseline-eval links. After evals start once training finishes.
The same pinned eval cell and frozen matrix are reused across the job's evaluations, all in one run group.
Generate targets snapshot the capability prompt in params, injecting it only when
the row has no system turn. They evaluate the raw model with recorded context, not
the live application. Every selected training evaluation uses all rows of the pinned
version, with no row cap or sampling; shared group baselines must also cover all rows.
Automatic intermediate-checkpoint judging is replaced by this explicit schedule;
historical checkpoint results remain readable. MCP `start_finetune` accepts all four
choices and the fine-tune resource returns `evaluation_plan`.
The Console projects selected evaluations into waiting rows immediately and replaces
each by kind when its run arrives. Loss-curve polling continues after deployment while
evaluations remain pending. `services.deployment` persists each deployment's stage,
remote call handle, generation, retry budget, deadline, and evaluation waiters.
The single 15-second deployment controller covers training outputs and baselines;
short IO tasks claim a row for 45 seconds, spawn or observe one remote call, then exit.
Worker death reconnects to the saved call rather than restarting GPU work. Each
attempt has a four-hour deadline and at most three confirmed-failure attempts with
30/60-second backoff. Transport errors retain the handle. Submission without a saved
acknowledgement fails closed; never launch another call until remote state is resolved.
Failed baseline deployments notify waiting evaluations even without a remote handle
or while cancellation is pending. Baseline stage progress is mirrored to the training
job while it runs; eval score updates merge into freshly locked progress.
Base preparation always uses `fetch_base_model` before publishing/merging and booting.
Cancelled jobs and superseded generations cannot become ready; terminal notifications
and remote cancellation are durable work too. Explicit retries reset the generation
and budget without losing checkpoint metadata. Training remains successful if only
deployment fails; the dependent evaluation is marked failed. REST and MCP share the
retry service and expose stage, attempt, retry time, deadline, and last error.
The deployed-model list includes only deployments linked to training jobs, before
pagination and filtering. Baseline eval infrastructure remains addressable by ID
but is excluded from the Console's model lists and counts.

Training recommendations classify the selected capability's recorded codebase context
(`codebase.task_type`), cached by context fingerprint. Task type drives benchmark skill
weights; dataset stats still drive context/tool compatibility, hyperparameters and cost.
With no selected capability, use the dataset's stored semantic task or profile heuristic,
even if the dataset has a capability mapping. An unclassifiable selected capability stays
unknown and ungraded; it does not fall back to the dataset. Console and MCP readiness use
the same recommendation service; an uncached capability classification may call an LLM.

Training mode is separate from task type. Native decision recommendations use the
selected capability or an existing semantic dataset classification; chat-output
heuristics cannot establish their task. Missing classification stays unknown. Native
model eligibility follows the validated decision format, independently of the task
label; chat benchmark grades remain unavailable for native probability training.

- Serving splits by finetune shape in `deployment.serves_as_adapter`. A dense or explicitly supported MoE Modal-trained LoRA is served as an adapter on a shared BF16 base — `publish_adapter` copies the adapter, and every deployment on that base shares one container pool, so a second adapter deploys in seconds. Everything else (full finetunes, unsupported MoE, non-Modal providers) is merged and quantized into a private checkpoint. The shared base must stay BF16: an FP8 base measurably degrades adapter quality. MoE models (`"moe": true`) require verified `inference.lora_supported` in `models.json`. Qwen3-Coder uses native mixed-format MoE LoRA with the fused expert format declared at adapter load; weights remain separate from the BF16 base. Shared workers resolve the family from the sealed base manifest. `inference.min_vram_gb` enforces measured GPU capacity floors; Gemma 4 E2B and E4B require at least L40S. A deployment whose stored GPU is below that floor is raised before the next boot.
- Base weights live in exactly one place: `.base_models/{org--model}` on the weights Volume. `fetch_base_model` is the only writer and a global mutex (`max_containers=1`), so concurrent callers cannot corrupt a shared dir or download twice. Celery waits on that Function (`.remote()`) before spawning a GPU train job, so a cold base downloads on CPU. Training then loads `BASE_MODEL_PATH` and fails if the snapshot is incomplete — no Hub fallback, no AWS for bases, no catalog prewarm.
- Nothing on the `overmind-sft` Volume is read at serve time — GPU workers mount weights and vllm-cache, plus inference-artifacts for shared-base LoRA. `tasks/cleanup_modal.py` sweeps it daily, ages run dirs with no `FinetuningJob` row off the Volume's own mtimes, and drops `runs/{run_id}/final/` once the deploy has landed (a READY deployment means the serving copy is on the weights Volume and a confirmed S3 archive covers a rebuild). The same beat drops spent `/weights/.staging/{id}/` trees. `stage_modal_checkpoint` and `publish_adapter` fall back to `download_checkpoint_from_s3` when the checkpoint is absent — the one place an S3 round trip is allowed, never the first deploy. Both delete the job's staging dir as soon as the serving copy is written.
- Shared-base LoRA workers use a separate `{gpu}_{image}_lora` class for every existing GPU/image pair; the selector is unchanged. `fetch_base_model` hashes and seals `.base-manifest.json` once, then validates its file identities on reuse. The base digest participates in the container parameters. Snapshot capture initializes vLLM and CUDA graphs, exports adapter-free runtime-layout weights to immutable generations on `overmind-inference-artifacts`, caches parsed headers, and uses level-2 sleep to omit large weight backups. Artifact identity includes base, effective serving arguments, GPU, image, loader source and runtime versions; it is not portable across arbitrary engine profiles. A new LoRA on a compatible base/profile reuses these assets.
- Restore refreshes the Volumes, validates identities, wakes weight allocations, streams 2 GiB shards in manifest order into native pinned buffers, copies into the original CUDA graph addresses, resets empty LoRA banks, then wakes KV memory and checks health. LoRA images pin Run:ai 0.16.1; one reader uses 16 native threads, eight Torch threads and an 8 GB buffer for bases up to 80 GB, otherwise 16 GB. The pin is required by the upstream allocation site. Snapshots contain no tenant adapters; adapters are attached after restore under the existing lock. Lifecycle failures are recorded and surfaced on the input, not raised from `enter` into an endless retry. Internal sleep/reload/adapter-control endpoints cannot be forwarded through public inference methods.
- Full-checkpoint workers retain their non-snapshot startup and compile-cache persistence. Only those concrete classes own the normal post-snapshot startup hook: putting it on the common base causes Modal to run it alongside the LoRA restore hook. `pre_warm` still verifies a deployment before READY; for LoRA it also completes first-time artifact/snapshot preparation. `CUDAGRAPH_CAPTURE_SIZES` and GPU selection are unchanged. The Modal gateway and Django SSE write idle pings immediately and every 15s; Django non-stream completions wait up to 15s for a result before starting newline keepalives. Playground SSE retains its keepalive. The edge ALB drops idle connections after 60s. Early non-stream errors return 400/502; after a keepalive commits headers, Django aborts an incomplete JSON response so clients raise a transport error. SSE failures use an error frame. Requests carry X-Request-ID, and failures and end-to-end timing are recorded alongside engine timing. The Django client rejects upstream JSON errors and consumes SSE lines without byte-buffer accumulation. GPU scheduling remains outside the loader's control; no cold-start SLA is implied.

### Native decision serving

Training completion always enters the shared deployment controller, including
native decision jobs. `DeployedModel.inference_contract` separates chat from typed
probability serving. Native deployments pin `serving_runtime`, recorded training
GPU/context and the verified artifact identity. The release-pinned
`overmind-decision-serve-*` app copies the complete immutable adapter/head artifact
to the weights Volume and loads it through the same decision encoder/readout.
Workers never read the training Volume during inference. Publication checks the
artifact and base identities; READY requires a successful probability prediction.
There is no chat-gateway registration or implicit capability activation.

REST `POST /api/deployed-models/{id}/inference/`, request polling and MCP
`run_inference` share the durable inference-request service. Input-only decisions
exclude targets and generation parameters; output retains original option order
and artifact identity. Existing request keys recover receipts without another
provider submission. `deploy_finetune` can backfill successful historical jobs;
existing deployments are reused and failed/deleted deployments require explicit
retry. Native comparisons remain independent batch measurements.

### Native decision training

`DecisionJob` supplies a separate public UUID over a one-to-one `FinetuningJob` run. Its status, preparation, monitoring, cancellation, billing, provider recovery and verified deployment come from that run. REST and MCP return both IDs, and explicit preparation links a decision setup job before launch. The decision catalogue names Clef, D3 and D1 families. Clef uses the qualified shared runtime; D3 and D1 are discoverable but cannot launch until their family-specific preparation, checkpoint and serving contracts reach the same standard. Historical jobs from the retired independent lifecycle remain in `DatasetHistory` rather than being treated as new shared runs.

`training_labels.prediction_labels` supplies `record.prediction_labels` to REST detail
and the MCP finetune resource. It projects distinct questions, ordered options and
declared score axes from the checksum-verified pinned cell, checking launch selection
identity when recorded. The cache binds cell, fingerprint and file stat; full-source
queries return at most eight complete label sets within 12 KB, with total coverage
explicit. Missing or unreadable data stays unavailable; chat runs return null. This
read path never invokes a model or derives labels from target argmax.

A train cell may carry a `decision` object instead of assistant-bearing messages. `modal_shared/decisions.py` validates state, question, kind, runtime options, full target distributions and positive weights. The measured contract freezes the objective. The separately pinned `u2026_10_3_decision` image supplies Unsloth `FastDecisionModel`, `DecisionTrainer` and the Clef joint schema head, with `UNSLOTH_COMPILE_DISABLE` set because Unsloth's default block compile does not finish a Qwen3.5 decision step. Qwen and Llama chat training use `u2026_10_7` (Unsloth 2026.10.7 / Zoo 2026.10.7, transformers 5.17, TRL v1.10.0); other conversational families keep their existing stacks. `modal_shared/decision_encoding.py` maps the product request to upstream encoding, retains exact token/question/option spans and maps sorted choice and true/false outputs back to the requested option order. Targets never enter the encoder. Oversized inputs fail without truncation. Preparation fingerprints retain exact supervision and processor identity.

`SupervisedDecisionTrainer` delegates model setup, optimization, scheduling, precision and recovery to Unsloth. Its loss extension preserves complete distributions, nonuniform weights and mean-only ordinal targets. Accumulated losses share the actual optimizer batch's total weight, including partial batches. Mean targets use normalized-axis expectation error without invented distributions. Predictions retain full probabilities and stable log probabilities.

Decision artifacts contain the adapter, full-precision joint head, both upstream head/config files, tokenizer and `decision.json`. Every trainable tensor must be covered by the saved adapter or head. Training and prediction pin SDPA explicitly, including nested text backbones, so upstream trainer cleanup cannot switch the attention implementation. The head uses native PyTorch LayerNorm with FP32 normalization and output dtype restoration, avoiding compiled first-call numerical changes and changing checkpoint recomputation graphs. The Unsloth head architecture, encoder kernels and activation checkpointing are retained. Retained checkpoints publish atomically after exact tensor reload and nonempty probability verification at 0.0001 absolute tolerance. Final publication repeats verification in a fresh process. Worker receipts classify probability and weight reload failures without exposing raw worker logs. Trainer recovery includes optimizer, scheduler, RNG, progress and a base/data/runtime/recipe signature, with a checksum receipt written after a complete save; incomplete later saves cannot hide the last complete checkpoint. Catalog eligibility, runtime qualification and held-out quality remain separate evidence. Native artifacts do not enter chat evaluation or deployment. The supported method remains one-GPU Modal LoRA.

Preparation and training reuse the shared exact token artifact and monitoring lifecycle. `decision_artifact.py` seals verified adapters and heads with relocation-independent checksums. Provider retries return an already verified published artifact without optimizing again. Native validation exposes distribution CE/Brier, applicable hard-label accuracy and mean error with separate denominators. Categorical assessments compare explicitly declared gold-label families against their same-subset majority baseline; soft targets and mean-only targets never become categorical labels. MCP retains the latest decision-quality check independently of later loss-only checks.

Native `hyperparameters.pre_training_baseline` is a strict boolean, default true. False skips only the starting-model development evaluation; development checkpoint selection, post-training validation, reload verification and separately scheduled benchmarks remain independent. The Console checkbox, MCP recipe and saved variants share the choice. Launch/effective receipts, resume signatures and forecast recipe matching retain it; worker telemetry reports `not_requested` rather than a score when disabled.

Training progress keeps cumulative optimizer steps and token counts, but throughput and ETA use only work since the current attempt began. Native recovery supplies the restored token count to `ProgressCallback`; attempt starting counters are emitted with its memory event.

The release-pinned `overmind-decision-eval-*` Modal app prepares input-only benchmark records on CPU, then runs the Unsloth encoder/head with either the complete saved artifact or a freshly initialized head on the unchanged foundation. Reference distributions never enter that worker. It writes full probabilities, stable log-probabilities, input hashes and model identities. Preparation records incompatible inputs explicitly. `decision_metrics.py` scores full distributions; mean-only ordinal references receive expected-score errors rather than invented NLL/Brier. MCP, REST and Console native comparisons use this worker through the same durable services. Native comparisons are separate from chat `EvalRun` and live inference deployment.

## Reproducible training experiments

`training_record` freezes dataset selections, objective, user-accepted findings and requested configuration. `request_key` is unique within the project; creation locks the project and dataset selections in one transaction. Reusing a key and recipe returns its job without dispatching again. A different recipe under the same key conflicts. Effective provider settings are recorded separately; explicit context, batch, packing, dropout and seed must be honored or rejected, never silently clamped.

`training_release` requires an explicit Modal environment. `modal_shared.training_release` hashes the worker, shared modules, data format and training assets into an immutable app name. Preparations and jobs pin that identity; retries and observers use it even after API configuration changes. Deploying an identically named release with different code is unsupported. Existing unpinned jobs require a verified runtime reconciliation before adopting this release; never deploy this API over an active unpinned run.

CPU preparation writes closed token shards, source offsets and checksums. A restart verifies source, tokenizer and committed shards before reuse; uncommitted work is regenerated. Initial JSONL transfer is gzip compressed; the training handoff sends ordered 32-byte row keys and validates the original token artifact. Repeated observations and external validation selections survive materialization unchanged. This is shared by conversational and decision training.

`training_submission` journals intent and run ID before provider submission. Lost acknowledgements enter `submission_unknown`; the reconciler uses the saved run's provider call metadata and pinned release. It never dispatches a second trainer while ownership is unresolved. If worker inventory confirms a Modal staging task is absent and no GPU dispatch intent exists, it requeues the same job and retains the interrupted intent in its progress history. Attempt identities fence late staging owners from dispatching after recovery. Provider telemetry records stages, heartbeat, counters, attempts, checkpoint and verified restore separately. The no-progress observer compares native stage, completed-work, attempt and checkpoint/restore counters as well as optimizer metrics. Advancing validation or reload stages reset its stall window; a background commit heartbeat alone does not. MCP returns the most recent bounded metrics with truncation metadata. An observer outage is separate from job failure. Volume retention uses the current explicit release and protects run IDs from unresolved submission receipts. Native forecast matching uses the recorded effective GPU/count, never a retrospective hardware guess from the current planner.

`NativeEvaluationPlan` supports standalone comparisons or a paired training-job comparison and freezes native-only calibration and final cells, exact overlap receipts, runtime, bootstrap seed and calibration recipe. REST `native-evaluation`, MCP `schedule_native_evaluation`, resources and Console share `services.native_evaluation`. Beat dispatches bounded `batch` tasks; the plan journals every paid call, retains uncertain intents and resumes saved call IDs. Verified artifact/reload is checked by the native worker before prediction. Calibration preparation/base/candidate/fit precede final preparation/base/candidate/scoring. New plans fit global then per-kind Clef temperature scaling with local L-BFGS-B, retaining distribution targets and minimum-sample rules. Historical frozen grid recipes remain readable. References remain local. All expected decisions remain in denominators, including incompatible inputs; missing/invalid predictions prevent calibration fitting. Exact overlap checks do not prove absence of paraphrase or pretraining contamination. Quality warnings do not rewrite labels or veto accepted experiment risk.

Matched native forecasts use completed execution duration for the same project, model, training/processor/data identities, hardware, recipe and approximate character-length profile. Native omitted packing matches the unpacked launch default; explicit step limits must match. A supplied native recipe epoch count takes precedence over the generic estimate default. Evidence and rejection reasons are returned. Workload scaling uses the same source-character estimate on both sides and is limited to 0.5–2x. One or two observations receive low confidence and an explicit 0.5–1.5 planning margin; three or more use 0.8–1.3. These are planning ranges, not statistical intervals. An unmeasured recipe reports unknown duration and total GPU cost, separately from the current GPU-hour rate. Forecasts expose price/duration status and estimate blockers, and multiply duration by the same fetched rate-card snapshot; historical charges are never reused as current rates. Native timing exposes elapsed time and a remaining-time range after five recent step intervals spanning 30 seconds. The range covers the elapsed-time average with a 0.8–1.3 planning margin and widens for observed step-time quantiles; it is not a confidence interval. It resets on attempt restart and is absent during final validation/reload. MCP and the monitor do not turn it into a single countdown or finish timestamp; the monitor rounds range endpoints outward to minutes. `compute_costs` prices worker usage receipts with workspace rates fetched from `modal.Workspace.billing.rates` (five-minute cache, eight-second timeout, no static-price fallback). Training, preparation, native evaluation and native performance report CPU/memory/GPU estimates separately from recorded ledger/API charges; cumulative worker observations are deduplicated and recorded training GPU is never added twice. CPU and peak-memory observations are proxies, not provider meters. Modal workers use process resource counters because cgroup mounts are unavailable. Reaped child CPU is included; live child usage is incomplete until exit, and summed process memory peaks can overestimate simultaneous use. Missing receipts/rates remain unknown, and image boot, later idle, storage, network, local orchestration and provider adjustments remain excluded. No all-in invoice or spending cap is claimed.

Native comparison database results retain aggregate and per-benchmark metrics. Per-slice diagnostics remain in the complete downloadable report, whose checksum and byte count are stored in the scoring receipt. A paused comparison can explicitly resume an interrupted local `verify_inputs`, `fit_calibration` or `score` stage; the previous lease is fenced and saved inputs are reused without provider resubmission.

`native_evaluation_monitor.py` maintains a local SQLite sample projection beside
each sealed suite. The evaluation worker publishes immutable, checksum-bound
chunks of flushed predictions and stage/count timestamps every ten seconds,
under its exact provider call ID. The existing collector verifies call, runtime,
input and artifact identities before ingestion. Repeated observations are
idempotent; publication failure does not fail or replay inference. Preparation
incompatibility, invalid outputs and missing results remain distinct. Scoring
uses the existing decision metric contract with each metric's own denominator.
Calibration is applied only after the calibration receipt is frozen.
REST `native-evaluations/{id}/monitor` and MCP `inspect_native_evaluation` read
bounded sample pages and recorded summaries, without provider reads. Missing
historical indexes are built by `native_evaluation.refresh_monitor` on `batch`
from retained predictions and their checksums, never a new inference run.

## Data-first model workflows

### Evidence-linked training plans

`training_plans.py` saves immutable `TrainingPlan` snapshots via shared REST and
MCP. Strict `training_plan_contract.py` defines task, attributable evidence and
pilot selection. Saved source/prepared/partition identities are frozen. A quality
pilot binds an experiment to those identities, with explicit bounded steps and
development-loss selection when a pilot is requested. Full experiments require
audit/baseline/sanity reports but no pilot job or pilot selection. Optional pilot
selection requires a measured task metric from a matching settled pilot's fully
scored development check with a verified checkpoint; when supplied, only declared
exposure/time fields can change from that recipe. Final/calibration data never
selects a recipe. Launch
rechecks bindings and records the snapshot in requested configuration. Changed
upstream identities need matching evidence; historical snapshots remain readable.
This is not a universal semantic-approval gate for exploratory jobs. Hardware
profiles and forecasts remain separate from task-quality evidence.

Onboarding can create a project directly for uploads; `Dataset.brief` retains the original task. Workshop exploration proposes per-family meaning with `target_evidence`; structural shape never supplies reference truth. `decision_supervised` combines distribution cross entropy and normalized-axis mean-squared error for mean-only ordinal targets. Their denominators stay separate in evaluation.

`datasets/partition_plans.py` saves seeded role fractions, grouping, stratification and explicit holdout rules. It writes durable assignments and derived source cells, retaining every observation and inherited identity. Typed training `decision` and evaluation `input.decision` share the same input-content key. Group/content checks report exact overlap only. Scientific overlap findings remain advisory.

`native_evaluation.py` serves standalone multi-participant comparisons; `decision_providers.py` owns the supported System One adapter and durable request/response ledger. `training_experiments.py` validates explicit candidates with the existing training serializer and dispatches them under their saved group. `decision_checkpoint_policy.py` constrains retention and last/development-loss selection. Retained artifacts publish atomically after nonempty reload verification. `decision_performance.py` uses a separate native measurement worker or the qualified external snapshot, with saved call IDs, bounded input workloads, measured client latency and partial cost amortization. Catalog eligibility, exact preparation, observed hardware execution, reload and quality are separate evidence.

New tasks route partitions to batch and evaluation/performance advance to batch; their reconcilers use control. Native evaluation reconciliation also recovers undispatched experiment jobs. Unknown acknowledgements remain explicit and successful work is reused. No production activation occurs. Historical native plan migration preserves calls, predictions and calibration, including the original unfloored scoring contract.

The public MCP catalog adds partition creation/retry, native comparison creation/resume, experiment save/launch, performance measurement/resume and model/workflow discovery. `get_job` and project-scoped job resources expose complete protocols and reports. `develop-model-from-data` guides the same lifecycle. The bounded manifest budget is 50 KiB for 50 tools (previously 40 KiB for 40); secrets and output schemas remain absent from discovery.

### Durable data-first mechanics

`datasets/exploration.py`, `training_experiments.py`, `native_evaluation.py` and `decision_performance.py` own shared REST/MCP lifecycle behavior. Expensive source verification and experiment planning run on batch workers with short database claims. Native comparisons separate draft, preparation and authorized launch; each ready arm has its own lease and receipt, and calibration remains a barrier. The periodic reconciler repairs missed continuation. `evaluation_inputs.py` binds durable chunk offsets and digests to the sealed manifest. Explicit compatible prediction reuse retains source lineage and zero incremental prediction calls. Performance can qualify independently before final quality results; snapshot changes remain failures. Training profiles are bounded attempts with representative native length coverage and no default monetary cutoff. Planning constraints bind quote and launch configuration and cannot be described as an invoice cap.

Training experiment validation names the variant and field requiring correction. Prepared reports expose `launch_readiness` against the saved training constraints; unmeasured or over-budget quotes do not recommend launch. Launch rechecks the same authorization. Every routed or scheduled task must be registered by a fresh worker importing `overbae.tasks`; the cold-start topology regression prevents test imports from masking missing task registration.

A paused native evaluation collector can resume an explicit stage with its exact recorded `call_id`. Missing or different IDs are rejected; recovery fences the interrupted lease and observes the same provider call without a new submission.

Native evaluation transient stage failures automatically enter `retrying` while the
plan remains `running`. Durable `retry_at` and recovery history drive 15/30-second
backoff with three consecutive attempts; independent eligible stages continue.
Native collection reuses its saved call and local stages reuse frozen inputs.
External read-only prediction timeouts, capacity outages and 429/5xx responses
retry only missing rows, at most three attempts per input, preserving unknown
costs in the attempt ledger. Saved responses are consumed first. Bare submission
intents, unacknowledged native launches, invalid data/identity and permanent errors
do not auto-replay. Paused plans stay paused; explicit resume honors request budgets.

Exhausted/permanent collection errors settle the affected work and allow scoring
of available verified predictions. External failed rows do not block later chunks;
native partials use checksum- and identity-verified retained monitor predictions.
Failed preparation skips dependent provider submission. Incomplete calibration
is unavailable per arm; final raw scores continue, with calibrated comparisons
only for fitted pairs. Results and reports expose `completion.status`, per-suite
coverage and issues; partial results are `completed_with_issues`, never silent
success or zero-filled scores. Input verification and report failures still stop.
Orphaned external intents are retained as `submission_unknown`, excluded from
scoring and never replayed; the collector continues subsequent inputs. Resuming
a paused external stage fences its interrupted lease without requiring a native
call ID. Native unknown submissions still require their original call ID.
