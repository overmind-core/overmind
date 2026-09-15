---
name: backend-architecture
description: Deeper backend map of overbae — module layout, celery queue topology, the span-only tracing model and its API surface, capabilities and toml sync, behaviour-keyed scoring, auth and guests, model serving and base weights. Use when navigating unfamiliar backend subsystems or wiring cross-subsystem behavior.
---

# overbae backend map

Single Django app `overbae`, project-scoped tenancy.

## Layout

- `overbae/api/` — DRF views/serializers, one module per surface (`eval_views.py`, `datasets.py`, `optimizer.py`, `otlp.py`, `billing.py`, `mcp.py`, …). `views.py` and `serializers.py` are the legacy monoliths — new surfaces get their own module. Global exception handler returns `{detail, code, error_id}`.
- `overbae/models/` — split by domain (`iam.py`, `capabilities.py`, `traces.py`, `evaluation.py`, `finetuning.py`, `datasets.py`, `optimizer.py`, `billing.py`, …).
- `overbae/services/` — business logic; largest subtrees: `eval/` (rubric/judging/cascade/runner), `datasets/` (Parquet store, landing, cell runner, contract, alignment, diff, use), `mcp/` (Streamable HTTP MCP server — procedure in the mcp skill), `codebase/`, `scan/` (AI-surface extraction, footprint matcher, scan application), `capabilities/` (identity, lifecycle, graph).
- `overbae/tasks/` — Celery tasks, roughly one module per feature; `utils/task_lock.py` for locking.
- `overbae/modal/` — Modal.com GPU workers (vLLM serving, SFT, PII NER).
- `overbae/management/commands/` — backfills and syncs.

## Celery topology

Four workers, five queues. Workers are resource profiles; queues are fairness classes.

| Worker        | Pool    | Conc | Queues           | Holds                                                    |
| ------------- | ------- | ---- | ---------------- | -------------------------------------------------------- |
| `control`     | threads | 8    | `control`        | orchestration, chord callbacks, FSM advances, beat       |
| `io`          | threads | 24   | `io`,`io_traces` | judges, live trace scoring, connector polling, rebinding |
| `batch`       | prefork | 6    | `batch`          | sample generation, dataset landing, connector chunks     |
| `interactive` | prefork | 4    | `interactive`    | workshop cell runs and agent turns                       |

Three constraints set the worker split. Only prefork enforces `time_limit` and `revoke(terminate=True)`, so every time-limited task routes to `batch` or `interactive`. A loaded prefork child costs hundreds of MB, so wide fan-out cannot be prefork. Orchestration holds its own lane because a chord callback stuck behind work never finalises its run.

`io_traces` is a second queue on the io worker, not a second worker: one worker over both round-robins (kombu's redis default), so an unbounded trace-scoring burst cannot queue ahead of user-started eval scoring.

`interactive` sets `--prefetch-multiplier=1` so a busy child never hoards the next turn.

Routing lives in `CELERY_TASK_ROUTES` and must stay in sync with `make worker` (one process standing in for the whole fleet, so its `-Q` lists every queue) and docker-compose. `tests/test_celery_topology.py` enforces it, and asserts no time-limited task lands on a threads lane. Workers hot-restart via watchmedo on `.py` changes.

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

Ingest: `POST /api/v1/traces` (with a `/v1/traces` compat alias) takes an OTLP protobuf export → parse `ResourceSpans → ScopeSpans → Span` → flatten → bulk upsert on `span_id` → `process_span` per span for linkage.

Read surface, backed by `SpanViewSet` rather than a trace viewset:

- `GET /api/traces/` — one row per trace, its head span (`Span.trace_heads`): the root once it arrives, else the earliest span, so traces stream in as spans land. Each row carries `trace_status` (`completed` = root present, `live` = rootless and recent, `interrupted` = rootless and quiet past `TRACE_SETTLE_SECONDS`)
- `GET /api/traces/{trace_id}/` — every span sharing that `trace_id`, plus root summary and `trace_status`
- `GET /api/traces/services/` — distinct `service_name`; `GET /api/traces/models/` — distinct models
- No `/api/spans/` route exists.

Query params are span-native (`received_at__gte`, `trace_id`, `span_type`, `operation`, `service_name`, `has_error`).

## Evaluation

Two systems share the evaluator engine but never share a judge. `ctx["eval_surface"]` decides which, and `base.judge_module(ctx)` is the only place that chooses — nested callers inherit the surface through `ctx` rather than importing a judge directly.

| Surface                   | Set by                           | Judge                                        | Score comes from                                            |
| ------------------------- | -------------------------------- | -------------------------------------------- | ----------------------------------------------------------- |
| `generative`              | `tasks/eval.py`                  | `evaluators/gen_judge.py`, `ChecklistResult` | weighted fraction of checklist verdicts, computed in Python |
| `trace_scoring` (default) | `services/eval/trace_scoring.py` | `evaluators/judge.py`, `JudgeResult`         | the model's own `score` field                               |

The default is `trace_scoring`. `rubric_compiler` mirrors the split: `build_checklist_prompt` for generative, `build_judge_prompt` for trace scoring.

Trace scoring carves a trace into units in `services/eval/units.py` (explicit precedence: turn spans > entry_point invocations > key-segment shim > structural root; beside the lattice, a run boundary enclosing turn slices becomes a run-grain execution surface when a run-grain behaviour binds it, and a boundary-less single-function-span trace is an unscorable orphan fragment); `services/eval/trace_scoring.py` judges the units and `services/behaviour/binder.py` binds them.

## Capabilities and sync

The agent is the project itself — one graph per project, no table. `Capability` rows are its nodes (UI: "Capability"; the sidebar's "Agent" is the product).

- A scan never deletes: absence sets `status=leftover` and the row reactivates in place when the code returns; `observed` rows (telemetry- or hand-made) are exempt from leftover.
- `DELETE /api/capabilities/{id}/` is a soft delete (`status=deleted`): the row and its data stay, but it is invisible to lists, scans, and identity lookup, so a later scan of the same code mints a fresh row. There is no merge, split, retire, or history.
- Ingest never creates a capability: `services/capabilities/identity.lookup` resolves ids/names/slugs through `IdentityAlias` (renames included), an unknown identity leaves the span unbound, and `tasks/capability_rebind` re-binds the backlog after scans and hand-made rows.
- Wire identity is id-only: ingest binds spans by `overmind.capability.id` alone; `overmind.agent.*` attributes are not read; `overmind.capability.name` is a display label that never resolves. The `overmind/<uuid>` model alias never changes.
- Discovery is local: `/overmind setup` runs `overmind chassis` (AST inventory + call graph), writes `overmind_capabilities.json`, `convert_json_to_toml` fills prompt spans, drops fabricated anchors and unverifiable provenance, then stamps `trajectory_map[].verified`, then `overmind sync`.
- Toml sync is `POST`/`GET /api/v1/sync` (API-key auth). It maps onto `Capability` (`current`/`leftover`), stores `system_prompt` / `eval_metrics` / `capability_card` / `eval_matrix` in `improvement_metadata` (`eval_matrix` is intent metadata only — not materialized into graders), mints `Behaviour` rows from `capability_card.trajectory_map`, keeps `repo_summary` / `trace_provider` / toml `version` in `Project.settings`, writes leftovers back to toml with `archived = true`, and enqueues the async Default-set preload (Tier-0 card compiler + Tier-1 LLM judges).

## Scoring

Behaviour-keyed. The codebase scan mints `Behaviour`/`BehaviourVersion` contracts per capability (`services/behaviour/registry.py`, re-anchored across rescans, retired never deleted). Trace scoring carves units (`services/eval/units.py`), binds each as a `TaskExecution` (`services/behaviour/binder.py` — unbound units still materialize), and writes `Verdict` rows plus the `feedback_score["trace_scoring"]` block the console reads. The session score is a fold over the append-only `ConversationEvent` ask ledger. API: `api/behaviours.py`.

## Data Workshop

Source → cells → derived versions, edited only through the dataset's own agent. The full model, runner sandbox, agent tools, landing and export contract: data-workshop skill.

## Auth and tenancy

Clerk-backed auth; project-scoped tenancy (`User` ↔ `Project` via `ProjectMembership`), no org layer. Everything queryable is filtered by project.

- A guest (`User.is_guest`, minted by `POST /api/auth/guest/`) holds one project and can read and claim. `GuestJWTAuthentication` (`api/authentication.py`) refuses every other write with `guest_upgrade_required`; a view opts in with `guest_allowed = True` — never a permission class or middleware, since a view's own `permission_classes` replaces the defaults and a guest identity exists only through that token. Guests get no free credits; `/demo` points at local setup. A claim moves the memberships to the Clerk account and deactivates the guest; `tasks/guest_cleanup.py` deletes inactive guests and unclaimed ones after 7 days.
- Commercial billing (remaining-credit 402s, Free/Pro quotas, Stripe Checkout) injects when `STRIPE_SECRET_KEY` is set (`overbae/services/billing_provider.py`). Ledger charges always run. Empty key → uncapped OSS: spend is recorded and shown, gates and grants no-op.
- API keys are either `scope=account` (every project the user belongs to) or `scope=project` with one `resourceIds` entry. Creating a key with `project` always mints project scope.

## Serving and weights

- Serving splits by finetune shape in `_serves_as_adapter`. A dense Modal-trained LoRA is served as an adapter on a shared BF16 base — `publish_adapter` copies the adapter, and every deployment on that base shares one container pool, so a second adapter deploys in seconds. Everything else (full finetunes, MoE, non-Modal providers) is merged and quantized into a private checkpoint. The shared base must stay BF16: an FP8 base measurably degrades adapter quality. MoE is excluded because vLLM cannot apply a LoRA that targets fused expert layers, flagged by `"moe": true` in `models.json`.
- Base weights live in exactly one place: `.base_models/{org--model}` on the weights Volume. `fetch_base_model` is the only writer and a global mutex (`max_containers=1`), so concurrent callers cannot corrupt a shared dir or download twice. Training reads the same snapshot via `BASE_MODEL_PATH`, falling back to the hub only when `base_weights_for` finds it absent or half-downloaded. A daily sweep (`tasks/base_models.py`) keeps every enabled catalog base staged.
- Nothing on the `overmind-sft` Volume is read at serve time — the GPU worker mounts only weights and vllm-cache. `tasks/cleanup_modal.py` sweeps it daily, ages run dirs with no `FinetuningJob` row off the Volume's own mtimes, and drops `runs/{run_id}/final/` once the deploy has landed (a READY deployment means the serving copy is on the weights Volume and a confirmed S3 archive covers a rebuild). The same beat drops spent `/weights/.staging/{id}/` trees. `stage_modal_checkpoint` and `publish_adapter` fall back to `download_checkpoint_from_s3` when the checkpoint is absent — the one place an S3 round trip is allowed, never the first deploy. Both delete the job's staging dir as soon as the serving copy is written.
- GPU memory snapshots are off deliberately — measured first requests of 113-675s against a ~15s target. `pre_warm` is a single boot-and-verify pass. Serving captures CUDA graphs instead (`CUDAGRAPH_CAPTURE_SIZES`); widening that list means re-checking `ACTIVATION_OVERHEAD_GB` in `gpu_selector`, which budgets the graph memory. After a healthy boot the worker `commit()`s `overmind-vllm-cache` (`/root/.cache/vllm`) so inductor artifacts survive scale-to-zero, and `reload()`s that Volume on enter. Weight load uses `--load-format runai_streamer`; the serve images install `runai-model-streamer>=0.15.7`. `@enter` does not fire warmup chats — `/health` is enough to take traffic. Completions (SSE and JSON) and playground SSE write an idle ping immediately and every 15s while waiting on vLLM (SSE comment / leading JSON newline), because the edge ALB drops a connection after 60s with no bytes and a genuine cold boot is 2–7 min; a non-stream finetuned completion is therefore a streaming JSON response that reports a late failure as `{"error": {...}}` under status 200.
