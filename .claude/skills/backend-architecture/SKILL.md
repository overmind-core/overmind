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

Bounded decisions use `core/decisions.py`: registered Jev on OpenRouter's `/systemone` endpoint, structured choice questions, validated complete probability distributions, project/contract-scoped caching, a conservative UTF-8 context guard, and shared Redis request/token pacing. Missing configuration, capacity, invalid responses, oversized context or low confidence fall back through `services/eval/decisions.py`; confidence is not calibrated correctness. Generative question resolution uses closed, required per-question properties compatible with strict provider schemas, preserves original question IDs and the selected judge, and validates coverage and choices before conversion. No evidence is truncated for Jev. `config.decision` pins backend (`jev` or `generative`), model, confidence floor and adapter-policy version; new snapshots and rubric digests include it. The actual served revision and fallback/cost provenance are stored in `_decision` sub-scores.

Configurable evaluator policies default to generative. Explicit Jev policies cover checklist verdicts, extracted-claim support, per-turn dimensions, cascade step ratings and confident successful numeric behaviour steps; qualify them against representative labelled examples before relying on their scores. Fixed grounding, capability classification and workshop semantic services retain bounded Jev routing. Independent question batches retain accepted answers and generatively resolve only uncertainty or unfinished questions after a later transport failure. Generated choices carry reasoning, not fabricated Jev confidence. Claim verification reuses extracted claims on fallback. Failed/uncertain behaviour steps retain full generative causal diagnosis. Holistic outcomes, session ledgers, claim extraction, rubric authoring, categorical judges, panels and cascade summaries remain generative. Grounding keeps insufficient evidence outside its support/contradiction denominator; lookup and persistence use one policy-aware identity. Combined dataset description, evaluator relevance and rubric authoring use one generative call. Cascade provenance is stored once per batch, unknown costs include retries and holistic stages, and decision latency measures the whole adapter call including failures. Jev is excluded from inference and chat-model pickers. Provider calls in tests are blocked unless explicitly stubbed.

Generated samples snapshot the runner's initial messages and tool schemas after system-prompt injection in `trajectory.model_request`; `metadata.output_start` separates context from newly generated messages. This is the initial runner request, not provider chat-template bytes or every replay turn. `services/eval/sample_io.py` supplies the REST detail and MCP resource with separate input, generated output, and grading reference. Historical samples fall back only to the verified pinned dataset row and label it `input_source=dataset`, never an exact captured request; absent sources are `unavailable`. The eval-run MCP resource exposes five bounded sample inspections with grader reasoning.

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

Clerk when `CLERK_API_SECRET_KEY` is set; blank secret is self-hosted local JWT (`POST /api/auth/local/` — email + password, create on first use, no verification). Project-scoped tenancy (`User` ↔ `Project` via `ProjectMembership`), no org layer. Everything queryable is filtered by project.

- A guest (`User.is_guest`, minted by `POST /api/auth/guest/`) holds one project and can read and claim. `GuestJWTAuthentication` (`api/authentication.py`) refuses every other write with `guest_upgrade_required`; a view opts in with `guest_allowed = True` — never a permission class or middleware, since a view's own `permission_classes` replaces the defaults and a guest identity exists only through that token. Guests get no free credits; `/demo` points at local setup. A claim moves the memberships to the Clerk account and deactivates the guest; `tasks/guest_cleanup.py` deletes inactive guests and unclaimed ones after 7 days. Guest claim requires Clerk.
- Console: `VITE_SELF_HOSTED=true` and a blank `VITE_CLERK_PUBLISHABLE_KEY` skip `ClerkProvider` and show the local login form; otherwise Clerk.
- Project invites: with Clerk, an invitation email is sent; without Clerk the `ProjectInvite` row is stored and claimed on first local (or Clerk) sign-in for that email.
- Commercial billing (remaining-credit 402s, Free/Pro quotas, Stripe Checkout) injects when `STRIPE_SECRET_KEY` is set (`overbae/services/billing_provider.py`). Ledger charges always run. Empty key → uncapped OSS: spend is recorded and shown, gates and grants no-op.
- API keys are either `scope=account` (every project the user belongs to) or `scope=project` with one `resourceIds` entry. Creating a key with `project` always mints project scope.

## Training preparation

Workshop findings are advisory before model-specific work: incomplete task/evidence/answer/schema reviews, capability mismatches and train/eval overlap produce warnings, not launch blockers. Exact preprocessing checks technical compatibility and does not repair datasets. Fine-tuning validation is read-only, and successful creation freezes the selected train, validation and `eval_cell` versions atomically. The same pinned eval cell is reused for before/after runs; dataset versions and capability cannot be swapped after creation. Internal loss-validation splitting preserves duplicates and cannot silently clean selected data.

The workshop is model-independent. `services/training_preparation.py` caches exact Modal preprocessing by training/validation cell fingerprints, model, training type, context length, training stack and the SFT asset code fingerprint. REST `POST /api/training-preparations/` and MCP `prepare_training_data` share the service; MCP returns a `training_preparation` job receipt readable through `get_job` and the jobs resource. Preparation uses CPU-only `prepare_<train_function>` in the matching Modal training image; it does not start GPU training.

Console setup does not submit or await preprocessing and has no per-model preparation cards or workshop review recommendations. `run_finetuning` requests or reuses the exact artifact after launch, reports preparation as job progress, and starts GPU training only when ready. Explicit REST/MCP preparation remains available.

Modal recommendations use row-length estimates to size training context without treating estimates as compatibility verdicts. Job preparation repeats sizing across the pinned training and enabled validation cells, preserving a larger requested context. If exact tokenization exceeds that context but fits the model's training-type limit, it requests a matching larger preparation and rechecks every row before GPU submission. Exact lengths need no estimate headroom; over-limit rows and other incompatibilities still block. No dataset is rewritten, truncated or filtered. A training-job retry can recover an undersized context through this same path.

`TrainingPreparation` persists queued/starting/running/ready/incompatible/failed, the remote call ID and a 20-minute deadline. A 15-second controller queues short `io` observers. Restarts poll the saved call; transport failures retain it. An unacknowledged submission fails closed. Explicit retry of a confirmed failure cancels its saved remote operation before requeueing; uncertain submissions cannot be retried automatically. REST `/retry/` and MCP `retry_failed` use the same guard. Retrying a failed/cancelled training job also retries its cached confirmed-failed preparation before queuing training, unless GPU training already has a remote ID. Ready or in-flight preparations are reused; unsafe failures and incompatible data refuse the retry without resetting job or eval state.

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

## Serving and weights

`services/serving_context.py` estimates the pinned evaluation workload (including the capability prompt and reference-output headroom) independently of training `context_length`. Model recommendations with an eval dataset and REST/MCP launch share the serving-capacity check. The plan respects native context without assuming RoPE scaling and validates single-GPU capacity. Deployment and base-model evaluation use the plan; all before/after calls reserve the same output budget. A shared baseline grows only from a settled deployment state. Existing ready fine-tunes are not silently reconfigured.

`modal_shared/context_budget.py` supplies a positive default output reservation and rejects prompt truncation. Both streaming and non-streaming serving enforce it; vLLM validates the exact templated input plus reserved output before generation. Core LLM calls preserve finish reasons and reject token-limited responses before parsers or tool executors consume them. Eval runners retain the partial answer and usage, mark the sample degraded, and write skipped judgments rather than quality zeros. Finalization synchronizes training-eval status explicitly; degraded runs cannot become successful training-quality comparisons. MCP inference returns finish reason, model truncation and response-content clipping separately.

Training jobs and eval sets may have no capability. Training still requires an eval dataset
and an eval set with enabled generative evaluators; a job without a capability can also
select a ready trained benchmark. Unassigned eval sets are project-scoped and cannot be activated
for live trace scoring. `eval.eval_set.create_with_evaluators` atomically creates a set and
its library members in their applicable roles; REST and MCP share that creation path.

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
When OpenRouter is configured and its cached
catalog has an exact model ID or
Hugging Face checkpoint match, before evals use OpenRouter without a base deployment.
Unavailable models or catalogs keep the existing provider/Modal fallback. A started
eval or attached baseline deployment pins the route until an explicit retry; catalog
refreshes do not switch live evals or provision duplicate baseline infrastructure.
Defaults are untouched-base-before + trained-after; incumbent comparisons are
separate opt-ins. The matched base is the preferred delta reference. All four can be off, but the eval
dataset and eval set remain required. Baseline evals launch as independent jobs alongside
GPU training; their completion and failure do not gate training. Retries retain EvalRuns
but detach failed baseline-eval links. After evals start once training finishes.
The same pinned eval cell and grader snapshots are reused across the job's evaluations.
Generate variants snapshot the capability prompt in params, injecting it only when
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

- Serving splits by finetune shape in `deployment.serves_as_adapter`. A dense or explicitly supported MoE Modal-trained LoRA is served as an adapter on a shared BF16 base — `publish_adapter` copies the adapter, and every deployment on that base shares one container pool, so a second adapter deploys in seconds. Everything else (full finetunes, unsupported MoE, non-Modal providers) is merged and quantized into a private checkpoint. The shared base must stay BF16: an FP8 base measurably degrades adapter quality. MoE models (`"moe": true`) require verified `inference.lora_supported` in `models.json`. Qwen3-Coder uses native mixed-format MoE LoRA with the fused expert format declared at adapter load; weights remain separate from the BF16 base. Shared workers resolve the family from the sealed base manifest. `inference.min_vram_gb` enforces measured GPU capacity floors; Gemma E4B requires at least L40S.
- Base weights live in exactly one place: `.base_models/{org--model}` on the weights Volume. `fetch_base_model` is the only writer and a global mutex (`max_containers=1`), so concurrent callers cannot corrupt a shared dir or download twice. Celery waits on that Function (`.remote()`) before spawning a GPU train job, so a cold base downloads on CPU. Training then loads `BASE_MODEL_PATH` and fails if the snapshot is incomplete — no Hub fallback, no AWS for bases, no catalog prewarm.
- Nothing on the `overmind-sft` Volume is read at serve time — GPU workers mount weights and vllm-cache, plus inference-artifacts for shared-base LoRA. `tasks/cleanup_modal.py` sweeps it daily, ages run dirs with no `FinetuningJob` row off the Volume's own mtimes, and drops `runs/{run_id}/final/` once the deploy has landed (a READY deployment means the serving copy is on the weights Volume and a confirmed S3 archive covers a rebuild). The same beat drops spent `/weights/.staging/{id}/` trees. `stage_modal_checkpoint` and `publish_adapter` fall back to `download_checkpoint_from_s3` when the checkpoint is absent — the one place an S3 round trip is allowed, never the first deploy. Both delete the job's staging dir as soon as the serving copy is written.
- Shared-base LoRA workers use a separate `{gpu}_{image}_lora` class for every existing GPU/image pair; the selector is unchanged. `fetch_base_model` hashes and seals `.base-manifest.json` once, then validates its file identities on reuse. The base digest participates in the container parameters. Snapshot capture initializes vLLM and CUDA graphs, exports adapter-free runtime-layout weights to immutable generations on `overmind-inference-artifacts`, caches parsed headers, and uses level-2 sleep to omit large weight backups. Artifact identity includes base, effective serving arguments, GPU, image, loader source and runtime versions; it is not portable across arbitrary engine profiles. A new LoRA on a compatible base/profile reuses these assets.
- Restore refreshes the Volumes, validates identities, wakes weight allocations, streams 2 GiB shards in manifest order into native pinned buffers, copies into the original CUDA graph addresses, resets empty LoRA banks, then wakes KV memory and checks health. LoRA images pin Run:ai 0.16.1; one reader uses 16 native threads, eight Torch threads and an 8 GB buffer for bases up to 80 GB, otherwise 16 GB. The pin is required by the upstream allocation site. Snapshots contain no tenant adapters; adapters are attached after restore under the existing lock. Lifecycle failures are recorded and surfaced on the input, not raised from `enter` into an endless retry. Internal sleep/reload/adapter-control endpoints cannot be forwarded through public inference methods.
- Full-checkpoint workers retain their non-snapshot startup and compile-cache persistence. Only those concrete classes own the normal post-snapshot startup hook: putting it on the common base causes Modal to run it alongside the LoRA restore hook. `pre_warm` still verifies a deployment before READY; for LoRA it also completes first-time artifact/snapshot preparation. `CUDAGRAPH_CAPTURE_SIZES` and GPU selection are unchanged. Both the Modal gateway and Django completions (SSE and JSON) write an idle ping immediately and every 15s while waiting (SSE comment / leading JSON newline); playground SSE retains the same keepalive. The edge ALB drops idle connections after 60s. Late errors use `{"error": {...}}` under status 200 once headers are sent; the Django client rejects JSON errors and consumes SSE lines without byte-buffer accumulation. GPU scheduling remains outside the loader's control; no cold-start SLA is implied.
