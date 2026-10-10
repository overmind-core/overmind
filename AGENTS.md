# Overmind Platform

Monorepo for the Overmind Console (`frontend/`, React), API (`overbae/`, Django + DRF + Celery), and SDK/CLI (`overmind/`). Agent improvement platform: observability → data workshop → evals → finetuning/inference. The platform is AGPL-3.0; `overmind/` is MIT.

This file is the single playbook. Claude Code, Codex and Cursor all read it natively. It holds what every task needs; procedures and subsystem maps live in `.agents/skills/`, which every tool loads on demand.

## Branches

- `main` is protected: feature branch → PR, using `.github/PULL_REQUEST_TEMPLATE.md`.
- The repo is public (`overmind-core/overmind`) and carries the platform plus the MIT SDK under `overmind/`. Never force-push `main`.
- Root ruff excludes `overmind/`. SDK CI is `.github/workflows/sdk-*.yml`. Every merge to `main` that touches `overmind/` publishes to PyPI, so `sdk-ci` requires a version bump on those PRs.

## Commands

- Frontend (**Bun**, from `frontend/`): `bun run typecheck`, `bun run lint` (Biome — no ESLint/Prettier), `bun run test` (vitest), `bun run check:all` (design/contrast/controls scripts). Scripts run with `bun`; there is no `node` on this machine.
- Backend (**uv**): `make test` (parallel pytest on the compose Postgres), `make test-journeys` (end-to-end journeys in `tests/journeys/` on the live ASGI app, a real Celery worker and compose Redis; outside services are faked at the network), `make test-serial`, `make lint-backend` (ruff), `make check-migrations` (after a model change, rebased on `origin/main`), `uv run <cmd>`.
- SDK (**uv**, from `overmind/`): `make -C overmind test`, `make -C overmind lint-check`.
- CI (`.github/workflows/ci.yml`) runs on `main`: platform lint, frontend, test and journeys as parallel jobs; draft PRs skip it. SDK CI (`sdk-ci.yml`) runs on `overmind/` changes.
- A local deployment already runs via `docker compose` with hot reload — do not start dev servers to verify changes. Celery workers auto-restart via watchmedo; `docker compose restart <worker>` if in doubt.
- After changing backend API surface: `make generate_api_client` (api-endpoints skill).
- `pre-commit run --files <changed files>` at the end of any substantial multi-file task, before committing.

## Architecture facts you can't guess

- Local file transfer is CLI-guided but has durable DatasetTransfer receipts.
  `overmind connection check --project-id PROJECT --json` verifies MCP and transfer
  access from the invoking environment; MCP connectivity alone is not readiness.
  Upload preflights automatically and binds SHA-256, byte count and destination
  recipe to a stable request key. Repeat the same command after interruption.
  `get_job(kind=dataset_transfer)` reads byte/publication state; `dataset_run`
  reports landing. Confirmed permission denial uses the host's scoped approval,
  never automatic sandbox-policy changes or browser fallback. An explicit
  `--json-rows-field FIELD` selects a top-level JSON row array and records that
  choice; original bytes remain retained. Detail: data-workshop skill.

Each line is the invariant; the named skill section carries the mechanics.

- Single Django app `overbae`: `api/` (DRF views/serializers, one module per surface), `models/` (split by domain), `services/` (business logic), `tasks/` (Celery), `modal/` (GPU workers). Map: backend-architecture skill. MCP is the first-class agent surface at `/api/mcp/` (`services/mcp/`); account API keys and OAuth connections discover authorized projects with `list_projects` and pass `project_id` per operation. Project API keys retain their narrower scope. It shares domain services with REST/Console and is not a proxy of either. Procedure: mcp skill.

- Tracing is **span-only**: no Trace table; a trace = spans sharing `trace_id`; the root span has `parent_span_id IS NULL`. OTLP ingest at `POST /api/v1/traces`.

- Local dataset upload/export authenticate separately from MCP. With no explicit or repository key, they use the address-bound account connection at `$XDG_CONFIG_HOME/overmind/connection.toml` (default `~/.config/overmind/connection.toml`, permissions `0600`). Repository keys are also endpoint-bound: changing only the API URL, even while repeating that key explicitly, fails before network access. Verify the installed CLI from both the actual workspace and a repository-free directory without injected credentials; workspace MCP and project settings can override global defaults. Missing credentials and transfer errors never authorize a browser fallback. Detail: overmind-datasets skill.

- Repository provenance belongs to the scan: `overmind chassis` records the checkout, conversion verifies it is unchanged, and sync preserves it with a separate server sync time. The Agent header and MCP project resource identify that snapshot; missing provenance stays unknown. Detail: backend-architecture § Capabilities and sync.

- The agent is the project itself: one graph per project, no table. `Capability` rows are its nodes (UI: "Capability"; the sidebar's "Agent" is the product). Ingest never creates a capability, and wire identity is `overmind.capability.id` alone. A scan never deletes; an absent capability becomes `status=leftover`, and `DELETE` is a soft delete. Capability discovery runs locally: `/overmind setup` builds the `overmind chassis` digest and runs the Cursor scan, writes `overmind.toml`, and `overmind sync` uploads it. Convert fills prompt spans, drops fabricated anchors and unverifiable provenance, then stamps `trajectory_map[].verified` against the AST chassis. Sync enqueues the Default-set preload: the Tier-0 card compiler, Tier-1 generative LLM judges, and the behaviour task/step judges for trace_scoring. Detail: backend-architecture § Capabilities and sync.

- Scoring is behaviour-keyed: the scan mints `Behaviour`/`BehaviourVersion` contracts, trace scoring carves units, binds each as a `TaskExecution` and writes `Verdict` rows. Detail: backend-architecture § Scoring.

- Every model id lives in `overbae/core/model_registry.py`. Jev uses the dedicated `core.decisions` transport, not chat-model chains. Configurable rubric judges default to generative; Jev requires workload qualification. Data Workshop does not invoke a platform planning or generation agent. Native coding agents own interpretation, code and semantic work; deterministic execution and lineage-bound imports are platform services. Training and evaluation retain their configured providers. Detail: data-workshop skill; backend-architecture § Evaluation.

- REST and MCP datasets may start from a written `brief` without a source or capability. First-source attachment preserves the draft; later uploads append versions. Intent is explicit (`train`, `eval`, `explore`) and otherwise stays pending. Landing never starts preparation. Original document bytes, extraction metadata and row evidence remain retained; PDF and PNG/JPEG/WebP landing use local English Tesseract OCR where needed, preserving page regions, orientation and recognition provenance. MCP pages source inventories without discarding extraction facts and exposes measured landing/OCR and pipeline stages; the native agent owns their explanation. Document byte and PDF page limits are discoverable before extraction. Detail: data-workshop skill. The Console landing page is a project dataset table; selecting a row opens the unchanged step-by-step cells. A folder button reveals compact, project-scoped dataset navigation. Platform chat, the prompt-style landing composer and mutable-cell replay actions are removed. Removed routes do not exist; the Console has no chat, mutable-cell replay or funding controls.

- Datasets are sources and step-by-step chains of cells; displayed versions are derived. Native agents must upload retained Python packages and save immutable project-level revisions with `save_dataset_pipeline(package=...)`; inline operations are not accepted and package-free historical revisions are read-only. They reuse them across compatible project sources, or save attributed `derived_from` variants. Packages execute only in the dedicated restricted container runtime, never API/Celery Python; external imports remain lineage-bound and attributed. Preview does not publish. Successful runs atomically append one cell per step; failures and cancellation preserve readable source and consumer versions. Explicitly enabled bindings rebuild source snapshots with a pinned revision and commit checkpoints with publication. Shared REST/MCP services own metadata, request-key conflicts, provenance and measured impact. Semantic quality stays unmeasured without attributable evidence; technical compatibility is not semantic correctness. Preserve probability targets, option order, weights, duplicates, blank states and declared groups. `use.use(dataset, intent, cell=)` freezes exact versions. Training pins train/validation/eval cells atomically and performs model-specific preprocessing only. Modal preparation transfers compressed sources once, verifies ordered row fingerprints and reuses the exact token artifact. Detail: data-workshop skill; backend-architecture § Training preparation.

- Tenancy is project-scoped, no org layer. Auth is Clerk when `CLERK_API_SECRET_KEY` is set; blank secret plus Console `VITE_SELF_HOSTED=true` uses local JWT: email/password (`POST /api/auth/local/`, create-on-first-use, no verification) only. ChatGPT login, account linking and credentials are removed. A guest holds one project and can only read and claim; a view that accepts guest writes opts in with `guest_allowed = True` — never a permission class or middleware. API keys are `scope=account` or `scope=project`. Detail: backend-architecture § Auth and tenancy.

- Compute estimates use live provider rates with fetch timestamps and durable worker usage receipts; they never create ledger charges. Training forecasts use compatible completed execution measurements, with evidence, uncertainty and missing components explicit. Current GPU hourly rates remain visible when duration and total cost are unmeasured; one quote uses one fetched rate snapshot. Native progress reports elapsed time and a remaining-time range rather than a single countdown or finish timestamp. Detail: backend-architecture § Native decision training.

- Billing meters platform model/compute spend on the ledger. Remaining-credit 402s, quotas and Stripe apply when `STRIPE_SECRET_KEY` is set. Native-agent model selection and funding belong to the external coding-agent environment. Training, evaluation and serving retain their own billing. Detail: backend-architecture § Auth and tenancy.

- Celery is five workers over six queues: `control` (default, orchestration and beat), `io` + `io_traces` (one threads worker, round-robin), `batch`, `landing` and `interactive` (prefork). Workers are resource profiles, queues are fairness classes; only prefork enforces `time_limit`, so every time-limited task routes to `batch`, `landing` or `interactive`. Imports have dedicated landing capacity; evaluation generation uses durable, bounded admission with project and run fairness. Workshop pipelines retain queue and execution timestamps in their run receipts; their waiting and occupied slots contribute to batch capacity. Source imports retain fenced attempts and explicit recovery through REST and MCP. `tests/test_celery_topology.py` enforces both invariants and must stay in sync with `make worker` and docker-compose. Detail: backend-architecture § Celery topology.

- Most status transitions use `.filter().update()` — **no Django signals fire** and the in-memory object goes stale. Any side effect a transition needs is wired explicitly at the call site, on a freshly reloaded row. Preserve this pattern.

  Training finalization also closes its operational timeline. The training
  reconciler repairs missed terminal events without provider calls and uses a
  renewable 30-second collector lease so worker restarts do not hide progress for
  five minutes. Detail: backend-architecture § Celery topology.

  Modal training charges and forecast duration evidence use deduplicated worker
  usage and recorded hardware, never completion-observation delay. Missing usage
  remains unmeasured; charge receipts retain rates and usage IDs. Historical ledger
  entries are not silently rewritten.

- Serving: a dense or explicitly supported MoE Modal-trained LoRA is served as an adapter on a shared BF16 base (never FP8); everything else is merged and quantized into a private checkpoint. `fetch_base_model` is the only base writer and seals immutable base identities. Every GPU/image pair has a LoRA worker that snapshots the engine after level-2 sleep, then reloads an adapter-free inference-layout artifact through pinned buffers; full-checkpoint workers do not snapshot. Artifact/snapshot identity includes the base revision and compatible serving profile, never tenant adapters. Completions and playground SSE retain their 15s idle pings. Detail: backend-architecture § Serving and weights.

- Making a model live runs the durable `ModelActivation` check in `services/model_activation.py`: wake and verify with the existing deployment prewarm, then atomically switch the alias and preserve the previous selection. REST and MCP share this path; the deployment reconciler also advances activation. Successful application API-key calls to the alias confirm connection, independently of readiness and worker warmth.

- Operational drill-down uses `OperationalRun` and append-only `OperationalEvent` receipts. Preparation, training, deployment, activation and MCP inference record separate observation, heartbeat and forward-progress timestamps. `get_job` links the latest attempt; `inspect_operation` and `overmind://operations/{id}` page recorded events without invoking a worker. Shared-pool events never establish tenant-adapter readiness. Provider telemetry availability, cursor backlog and dropped publications stay explicit. MCP contract 4.0 makes `run_inference` a durable `inference_request` job requiring a stable request key; unknown provider acknowledgement is reconciled, never blindly resubmitted. Serving-worker changes require a Modal deployment before their events exist.

- Training startup distinguishes runtime initialisation, weight loading, adapter
  setup, tokenizer verification and dataset/trainer construction. Console and MCP
  expose measured counters and separate stage, heartbeat and forward-progress
  clocks. Weight-loading percentages remain unavailable when the library reports
  no counter. New instrumentation requires a new pinned worker release.

- Typed decision training uses Unsloth `FastDecisionModel`/`DecisionTrainer` on its separately pinned runtime, supports Modal LoRA on one GPU and accepts a native `decision` column with runtime options and full target distributions. Clef encoding retains upstream option spans and an explicit order mapping; full-precision head weights accompany every adapter. Shared preparation, monitoring, operational receipts and MCP lifecycle remain authoritative. Native `hyperparameters.pre_training_baseline` defaults to true; false skips only the initial development baseline and retains checkpoint/final validation. Console and MCP expose the choice, receipts pin it, and worker telemetry records an omitted baseline as not requested. The native coding agent explores the source and user request before declaring evidence-backed per-family target semantics; unknown meaning stays unknown. The measured contract selects distribution cross entropy or mixed distribution/ordinal-mean supervision; it never invents a distribution from a mean or turns soft labels into argmax text. Decision checkpoints use typed probabilities and do not enter chat deployment/evaluation. Detail: backend-architecture § Native decision training.

- Training launch receipts pin the runtime release/environment, exact cells and requested recipe. Stall detection counts native validation and checkpoint-stage progress independently of optimizer steps; a committer heartbeat alone does not establish forward progress. Reusing a project request key returns the same job; a changed recipe conflicts. Provider intent is persisted before dispatch; an unknown acknowledgement is reconciled against that job's provider metadata, never resubmitted. Explicit settings that require a clamp are rejected. Native paired evaluation uses its own frozen calibration/final plan, retains incompatible rows, keeps references local and fits calibration before final predictions. Downloadable run records distinguish recorded GPU spend from unreported components. Detail: backend-architecture § Reproducible training experiments.

- Development monitoring freezes `hyperparameters.monitoring` across readiness,
  estimates and launch. Modal freezes loss/reference/generation samples before GPU
  dispatch; the trainer verifies the manifest. Adaptive/steps/epoch/off schedules
  use measured optimisation and check time, never quality-driven strategy changes.
  `TrainingValidationRun` and `TrainingCheckpoint` retain failures, sample and
  artifact fingerprints, reload verification and cancelled-run evidence. MCP
  `inspect_training_progress` and REST read stored checks/examples/sample identities
  without waking workers; `cancel_finetune` records intent before provider requests.
  MCP check overviews expose large native per-question metrics and assessments as
  `collections`; `field` JSON Pointers page the exact retained values, including
  nested check/checkpoint fields. REST monitoring-evidence shares this pagination.
  Development-loss selection and early stopping are explicit opt-ins. Generation
  checks require agent-declared labels/exact matching/JSON schema/JSON field pointers; missing metrics
  remain missing. Metered judges, challenge suites and declared-slice sampling are
  not yet supported by this monitoring contract. Full optimizer resume is not
  implied by adapter reload verification. Modal `runtime_limit_seconds` bounds the
  provider call without changing steps; it is not an all-in invoice cap. Detail:
  backend-architecture § Development monitoring.

  `json_fields` compares declared JSON Pointers against retained references, with
  type/precision-preserving equality and ordered arrays. Missing reference fields
  remain unscorable; wrong/missing predictions fail. Per-field coverage and whole
  example pass rates are separate. No field inference or tool execution occurs.

  Check facts retain server-side result-receipt and terminal-observation times.
  Optional product events expose counts, timings and verified checkpoint availability,
  never examples or labels. Receipt commits precede analytics; analytics failure
  cannot fail training. Availability is not proof of usefulness or user delight.

  Classification receipts also retain deterministic `facts.assessment` comparisons
  against the same scored subset's majority-label baseline and represented labels
  without predictions. These do not infer a cause or change training. Counts and
  frozen policy identity must agree; otherwise assessment is inconclusive. MCP
  retains the latest generated check independently of newer loss-only checks.

- Training transfer exposes stage-specific selection, upload acknowledgement and
  provider materialization counters. Uploaded bytes mean acknowledged files, not
  live network progress. The 15-second controller collects worker transfer facts
  independently of the active submission task; Console and MCP read the same saved
  facts and freshness. Missing telemetry never becomes a fabricated percentage.

- Data-first projects start from uploaded data and the original task brief; repository scans and capabilities are optional. Saved `DataPartitionPlan` recipes preserve exact content, declared groups, synthetic lineage and duplicate observations across train/development/calibration/final roles. Calibration/final members have eval intent; native inputs and references are projected losslessly for evaluation, and construction exposes stage/count progress. Standalone `NativeEvaluationPlan` comparisons accept foundations, trained artifacts and qualified external probability adapters, with optional frozen calibration before final scoring. `TrainingExperiment` extends job groups with explicit variants, a shared protocol and development-only checkpoint selection. `DecisionPerformanceRun` measures a saved client workload without activation. Console, REST and MCP share these services; completed responses and native call IDs are reused, unresolved submissions are never blindly replayed. Provider aliases, partial cost records and unmeasured hardware fit remain explicit. Detail: backend-architecture § Data-first model workflows.

- Workshop execution uses immutable `DatasetPipeline` recipes and durable `DatasetPipelineRun` receipts. They bind source/output/artifact cells and fingerprints, stable request keys, execution state, impact and producer attribution. `get_job(kind=dataset_pipeline)` and its resource read an exact run. Cancellation prevents publication without claiming remote termination; expired runs fail without automatic replay. External execution is attributed, not independently verified. The cutover archives old conversations, runs and provider receipts in DatasetHistory before dropping the agent tables and fields. Published cells retain their read-only scripts, reviews and frames. No legacy runtime or compatibility endpoints remain. MCP contract 3.0 retires `message_dataset_agent`, `run_dataset` and `manage_dataset_workflow`. Detail: data-workshop skill.

- Workshop handoffs return project-bound upload argv. Absolute min_rows/max_rows checks apply at publication and remain visibly deferred in previews; row preservation, schema and lineage checks apply in both modes. Runs expose queue age, polling interval, terminal time and structured failed checks. Format readiness does not establish task suitability. The native agent inspects the named capability and verifies published output before claiming preparation complete. Detail: data-workshop and mcp skills.

- Data-first workflow mechanics share services across MCP and REST. `DataExploration` freezes an explicit cell for cached whole-source profiles or a separate derived chain; paginated sampling allocations never silently change coverage. Workshop interpretation remains agent-owned with scoped evidence and explicit hypothesis/conflict states. Native comparisons save drafts, prepare immutable suites in background and launch explicitly. Independent stages use recoverable leases, chunk-indexed input verification and durable provider receipts; pause stops new claims without claiming remote cancellation. Training experiments prepare a configuration-bound forecast before launch, optionally enforce user-defined planning constraints, and expose aggregate child outcomes. Bounded runtime profiles use explicit step/time limits; compatible prediction reuse is explicit and never substitutes for independent repetitions or performance measurements. MCP contract version and catalog fingerprint are discoverable at `overmind://interface/current`; installed guidance is not a correctness dependency.

  Native comparison database receipts retain aggregate and per-benchmark metrics;
  complete diagnostic slices live in the checksum-bound downloadable report.
  Scoring receipts reference that artifact rather than duplicating it. Explicit
  local-stage resume on a paused comparison fences interrupted local work and
  reuses saved inputs; it never resubmits provider inference. A paused native
  collector can be recovered only with its exact recorded provider call ID.

- Benchmark selection is separate from serving: capability `benchmark_model` selects a ready trained model, or null for the codebase incumbent. New training jobs snapshot the primary choice in `baseline_model`; `active_model` only controls serving. Chat-training setup can pin additional benchmark models for the same eval set and dataset; they and the selected training bases are scored before training when benchmarking is enabled. Trained models are scored after training when evaluations are enabled. MCP `set_benchmark_model` sets the capability default.

- Foundation catalogue entries carry nullable `openrouter_id` mappings. Base benchmarks verify the mapped ID or an exact declared checkpoint against OpenRouter's catalogue before using it, ahead of unused ready hosted bases. MCP/REST model catalogues distinguish `available`, `not_listed` and `catalog_unavailable`; missing matches never substitute a different size or variant. Started evaluations and unresolved deployments remain pinned. Detail: backend-architecture § Serving and weights.

- Evaluation context estimates cover candidate models and generative judges, including reserved output. REST and MCP expose warnings; Console highlights setup selectors and Start only for estimated limit overflows. Unverified, unavailable and pending checks stay neutral. Start opens a short risk summary with Start anyway; undersized dropdown options are muted but selectable, with fit labels and scoped judge-cost comparisons. Training `eval_judge_model` overrides generative judge snapshots for that job only; blank preserves the set's per-evaluator choices. Baseline sharing requires the same judge selection. No automatic model changes, hard launch blocking or candidate removal. The judge funnel preserves the supplied prompt, retries output exhaustion once with a larger supported budget, and records technical failures separately from quality scores. Detail: backend-architecture § Evaluation.

- Serving context is independent of training sequence length. `services/serving_context.py` sizes it from pinned evaluation inputs, references and output headroom; recommendations and launch share the check. Completion requests reserve explicit output tokens, never silently shrink to remaining context. Token-limited generations retain finish reasons, stop before tool execution, and are excluded from trusted evaluation scores with visible degraded/skipped outcomes.

- Frontend must use the generated OpenAPI client in `frontend/src/openapi/` — never hand-edit it.

- Dataset storage preserves mixed scalars and precision-sensitive integers as JSON;
  shared pandas readers must not coerce them again. MCP queries cap JSON output at
  32 KiB and return `query_result_too_large` without clipping values; a configurable
  ten-second default execution deadline returns `query_timeout`. The CLI exports
  retained original sources via `dataset export --source SHA256`, verifying bytes
  without overwrite or credential-forwarding redirects. Local uploads
  accept one file per transfer; serial attachments combine rows in new versions.
  Atomic multi-file CLI handoff remains unsupported. Detail: data-workshop skill.

- Naming quirks: UI "Optimiser" = backend `optimizer`; UI "Training" = backend `finetuning`.

- `DESIGN.md` and `PRODUCT.md` carry a fixed YAML frontmatter schema that tooling reads — extend the values, don't restructure the documents.

- Standalone evaluation creation accepts `judge_model` through REST and MCP. It freezes the run's generative judge/fallback choice, including late-attached replay judges; blank preserves saved per-evaluator models. Existing runs reject judge changes. The Console's New evaluation dialog shares the judge selector and advisory context warnings with training; results and MCP resources expose frozen judge identities. Detail: backend-architecture § Evaluation.

- Workshop flow is authored by the native agent: step `id`/`input` dependencies,
  or `inputs` for disjoint branch fan-in (ordered concatenation, overlapping row
  identities rejected). Training recipes converge on trainable outputs with
  unresolved review metadata retained; `flow.unconsumed_steps` exposes dead ends.
  retained-script condition citations and a returned `flow` entity. Runtime receipts
  separate those declarations from actual input cells, row counts and fingerprints.
  The Console keeps full-size existing cells at scale 1 on a grid-snapped canvas:
  progress runs downward; sibling branches align on the same horizontal layer. The title's version
  chip previews/restores exact cells without replaying scripts. Detail: data-workshop skill.
  Each entrypoint exposes its meaningful step logic; helpers hold reusable utilities.
  Cells expose receipt-backed transformation attribution and allow inspection of all
  checksum-verified retained package files. External imports remain distinct from
  platform execution; historical scripts are never rewritten to imply new execution.

PDF landing checks pages missed entirely by the primary native parser with PDFium
before OCR. Recovery retains encoded text, page regions and engine/count facts;
font-encoding artifacts are flagged, never semantically corrected. Detail:
data-workshop skill.

Workshop decision/Jev recipes retain full distributions, ties, weights and source
groups in typed decisions. Target meaning and evidence belong to retained code
and output metadata; historical preparation plans are not a writable authoring
API. Flat-source profiling and impact checks retain declared supervision metadata
so changed weights or semantic declarations cannot disappear from the review.

## Style

- **Rebuild, don't patch.** There are no real users yet. Build the change the right way from first principles and delete the old path; no flags, shims, `_v2` names or `if legacy` branches. Migrations are the exception: production and staging hold real state. Full bar and the "never simplify away" list: engineering-taste skill.

- **Comments** carry only what the code can't: a workaround, a wire-format or contrast constraint, an ordering invariant, a trap. Delete restatements, banners, history and name-respelling docstrings. Full policy and the two local traps: code-comments skill.

### Python

Ruff enforces formatting and line length. What it can't:

- Imports at the top of the file. The only exception is a lazy import that breaks a cyclic dependency, with a one-line reason.
- Never import a `_`-prefixed name from another module. If an outside caller needs it, the owning module exposes a public name first.
- Serializers for all API input and output — never return a raw dict from a view.
- `select_related`/`prefetch_related` on any queryset whose rows will touch a relation.
- Business logic goes in model methods, managers, or `services/` — not in views.

### Frontend

- The Console is dark-only, including first paint, authentication, charts and flow canvases. There is no theme selector, stored-theme resolution or OS-theme listener. Semantic tokens only; **never write `dark:` variants** — `border-success/40 bg-success/10 text-success`, not a hardcoded pair.
- **Flat surfaces.** Standard `shadow-*` utilities are disabled. Depth is surface layering plus 1px borders; shared confirmation dialogs alone use the subtle `shadow-confirmation` token.
- Icons come from the central registry only: `import { Icon } from "@/components/ui/icons"`. The glyphs are a vendored path table in `ui/icons/glyphs.ts` that `bun run icons:vendor` regenerates; never import that module or `lucide-react` in app code — `check:design` fails on both.
- New shadcn components: `bunx shadcn@latest add <component>`, then adapt to the token system.
- Tokens, primitives, the border-contrast floor, and the duplicated table implementations: frontend-design skill.

### UI copy

Instrument voice — state the fact and stop ("9 rows", never "9 rows — small enough to read"). No design rationale, no reassurance, no coaching phrases. Applies to backend-generated copy too.

### Naming

Never put plan-phase labels (P0/P1, "Phase N") in code, comments, or test names — name by behavior. Backend tests live flat: `tests/test_<feature>.py`; journeys live in `tests/journeys/test_<promise>.py`.

## Workflow

- Never write unit tests after you write code.
- Strongly prefer E2E tests as the sole testing mechanism. Use them to verify complex features work. At the end of each E2E run, produce a verifiable, repeatable artifact containing the command, inputs or fixtures, environment requirements, and observed results.
- If a system must be tested in isolation, first write down all the ways it could fail, then write the code. Keep an isolated test only when it catches a concrete failure that existing E2E coverage misses; do not add assertions that merely mirror the implementation, pin incidental source text, or assert tautologies.
- Tests fake only what we do not own, and only at the network: `tests/fakes` (FakeLLM, FakeModal, the vendor, Stripe and Clerk APIs, `scripted` HTTP). Never patch `overbae.*` except to capture a Celery `.delay`/`.apply_async`, and never import a private `overbae` name; call the public entry point. Each contract has one owning test; a journey owns a customer promise, so a unit test does not repeat it. Do not restate declarations (field lists, constants, `__all__`, prompt prose). The `test-seams` pre-commit hook refuses new seams. Detail: run-tests skill.
- Stay in the asked scope. Fix the stated thing plus genuine prerequisites; report adjacent findings as a short "found but did not change" list. If the task is much bigger than framed, say so before editing.
- Simple, self-evident fixes: typecheck + lint is the bar — skip the test suite and say so plainly. When a suite run is warranted: run once, tee to a log, grep the log (run-tests skill).
- Which tests to run locally (compose Postgres and Redis up):
  - While editing: the test file for the code (`uv run pytest tests/test_<feature>.py`, seconds) and the journey for its area (`make test-journeys test_args="-k <name>"`, about a minute).
  - Before a push: `make test` (about 75 s).
  - When the change crosses ingest, sync, the worker, the gateway, MCP or the Console: `make test-journeys` (about 6 min).
  - CI runs the platform suites (lint, frontend, test, journeys) on every ready PR, and SDK CI only when `overmind/` or `.github/workflows/sdk-ci.yml` changes, so a local run of everything is not required.
- A change is finished when every surface reflecting it is updated, not when its own vertical compiles. CI cannot catch this, so walk the list in the pr-etiquette skill before opening a PR: **MCP** (the first-class agent surface: `services/mcp/` — impact classification, catalog, contracts, tools, prompts, resources; mcp skill), **cross-vertical blast radius** (celery routing, the `seed_demo` command, the generated client, this file and the skills), and **docs** (the sibling `overmind-core/docs` repo at `../docs` — open that PR alongside and link the two).
- Commit messages: short subject + at most one body line. No co-author trailers. Commit and push only when asked; on a sweep branch, one commit per observation.
- Changing behavior that this file or a skill describes? Update it in the same PR. There is one copy of every rule; keeping it true is part of the change.

## Communication

- Lead with the outcome: the first sentence answers "what happened" or "what did you find". Detail and reasoning follow. Keep output short by leaving things out, not by compressing the writing.
- Before reporting progress, check each claim against a tool result from this session. Report faithfully: failing tests with their output, a skipped step as skipped, a verified result plainly.
- UI work pauses for a look in the browser before screenshots or a PR; expect iterations.
- Sub-agents explore; edits and decisions happen in the main thread, with progress reports along the way. No multi-agent workflows unless asked.
- When there is enough information to act, act. Give a recommendation, not a survey of options.

## Guardrails

`.agents/hooks/` holds deterministic guards. A denial names the fix; follow it rather than retrying.

- Claude Code reads the wiring in `.claude/settings.json`; Cursor reads the same file through its third-party hooks setting (on by default).
- Codex reads `.codex/hooks.json`; trust the project hooks once with `/hooks`.
- Skills live in `.agents/skills/` (frontmatter `name` and `description` only); `.claude/skills` is a symlink for Claude Code. Personal skills go in `~/.agents/skills/`.
- Claude Code reads this file only from v2.1.277 and only when no `CLAUDE.md` or `CLAUDE.local.md` exists. With a `CLAUDE.local.md`, set `/config` → Project instructions → `claude-md-and-agents-md`.

## Gotchas

- After `uv add`/`uv remove` the venv lacks the dev/test groups (pytest vanishes): `uv sync --group dev --group test`. The `uv_resync` hook runs it.
- A new Python dependency reaches a compose worker only when its container is recreated (`docker compose up -d --force-recreate --no-deps <worker>`); a worker started before the image changed keeps the old venv and crashes on import at its next watchmedo restart.
- `overbae/services/sft_assets/` ships via `add_local_dir`, which bakes it into the image at deploy time — editing `pretok.py`/`train.py` does nothing until `modal deploy overbae/modal/modal_sft_worker.py`. The job runs the old code and fails identically, so it reads as "the fix didn't work".
- Chat templates disagree on OpenAI wire shape: `content: null` and JSON-string `tool_calls[].function.arguments` either raise or silently render an argument-less call. `pretok.normalize_openai_wire` is the one place that reshapes them — training tool data on a new family means checking it there, not per-family.
- Vite on WSL serves stale module transforms after bulk out-of-editor file changes; hard reload won't fix it — restart vite. Tell: runtime "X is not defined" for code tsc accepts.
- Two table implementations exist (`components/ui/data-table.tsx` and the paged rows grid in `components/datasets/notebook/rows-grid.tsx`) — a table fix must be checked in both.
- `--border` has almost no contrast headroom; the ramp is bare/`70`/`60`. Never lower a border opacity without `bun run check:contrast -- --all`.
- `manage.py seed_demo` must keep every job/run terminal and every span scored, or beat/reconcilers re-drive them against real providers (seed-demo-data skill).
