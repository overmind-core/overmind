# Overmind Platform

Monorepo for the Overmind Console (`frontend/`, React), API (`overbae/`, Django + DRF + Celery), and SDK/CLI (`overmind/`). Agent improvement platform: observability → data workshop → evals → finetuning/inference.

This file is the single playbook. Cursor reads it natively; Claude Code reads it through the `@AGENTS.md` import in `CLAUDE.md`. It holds what every task needs; procedures and subsystem maps live in `.claude/skills/`, which both tools load on demand.

## Branches

- `main` is protected: feature branch → PR, using `.github/PULL_REQUEST_TEMPLATE.md`.
- `oss` is based on `main` and carries the same backend and frontend plus the in-repo SDK under `overmind/`. Rewriting its history needs a force-push to `origin/oss`; do that only when publishing the rewrite. Never force-push `main`.
- Root ruff excludes `overmind/`. SDK CI is `.github/workflows/sdk-*.yml`; PyPI publish is tag-gated (`overmind-v*`).

## Commands

- Frontend (**Bun**, from `frontend/`): `bun run typecheck`, `bun run lint` (Biome — no ESLint/Prettier), `bun run test` (vitest), `bun run check:all` (design/contrast/controls scripts). Scripts run with `bun`; there is no `node` on this machine.
- Backend (**uv**): `make test` (parallel pytest), `make test-serial`, `make lint-backend` (ruff), `make check-migrations` (after a model change, rebased on `origin/main`), `uv run <cmd>`.
- SDK (**uv**, from `overmind/`): `make -C overmind test`, `make -C overmind lint-check`.
- CI (`.github/workflows/ci.yml`) runs on `main` and `oss`: platform lint/frontend/test. SDK CI (`sdk-ci.yml`) runs on `overmind/` changes.
- A local deployment already runs via `docker compose` with hot reload — do not start dev servers to verify changes. Celery workers auto-restart via watchmedo; `docker compose restart <worker>` if in doubt.
- After changing backend API surface: `make generate_api_client` (api-endpoints skill).
- `pre-commit run --files <changed files>` at the end of any substantial multi-file task, before committing.

## Architecture facts you can't guess

Each line is the invariant; the named skill section carries the mechanics.

- Single Django app `overbae`: `api/` (DRF views/serializers, one module per surface), `models/` (split by domain), `services/` (business logic), `tasks/` (Celery), `modal/` (GPU workers). Map: backend-architecture skill. MCP is the first-class project-scoped agent surface at `/api/mcp/` (`services/mcp/`); it shares domain services with REST/Console and is not a proxy of either. Procedure: mcp skill.
- Tracing is **span-only**: no Trace table; a trace = spans sharing `trace_id`; the root span has `parent_span_id IS NULL`. OTLP ingest at `POST /api/v1/traces`.
- The agent is the project itself: one graph per project, no table. `Capability` rows are its nodes (UI: "Capability"; the sidebar's "Agent" is the product). Ingest never creates a capability, and wire identity is `overmind.capability.id` alone. A scan never deletes; an absent capability becomes `status=leftover`, and `DELETE` is a soft delete. Capability discovery runs locally: `/overmind setup` builds the `overmind chassis` digest and runs the Cursor scan, writes `overmind.toml`, and `overmind sync` uploads it. Convert fills prompt spans, drops fabricated anchors and unverifiable provenance, then stamps `trajectory_map[].verified` against the AST chassis. Sync enqueues the Default-set preload: the Tier-0 card compiler, Tier-1 generative LLM judges, and the behaviour task/step judges for trace_scoring. Detail: backend-architecture § Capabilities and sync.
- Scoring is behaviour-keyed: the scan mints `Behaviour`/`BehaviourVersion` contracts, trace scoring carves units, binds each as a `TaskExecution` and writes `Verdict` rows. Detail: backend-architecture § Scoring.
- Every model id lives in `overbae/core/model_registry.py`: the catalog, the role chains, the providers and the workshop engine ladder. The Data Workshop agent runs on the first configured engine in `WORKSHOP_ENGINES` — Cursor, then OpenRouter, then an OpenAI, Anthropic or Gemini key — and both engines emit the same events; no key is required at startup. Detail: data-workshop skill § The agent.
- Datasets are a source and a chain of cells; versions are derived, never stored; `use.use(dataset, intent, cell=)` freezes readable versions. Workshop preparation receives whole-frame task-family profiles and downstream consumer requirements, applies supported transformations, audits, repairs actionable findings and rechecks; an audit is not a substitute for preparation. Residual findings for task alignment, input evidence, answer support, output schema, capability mismatch and overlap are advisory: alert the user and allow progression without quality approval. Only unreadable or technically incompatible data blocks use. The Console changes the chain, name, intent and capability through the dataset's own chat; REST PATCH and cell endpoints share the same lifecycle functions. Workshop preparation is model-independent; evidence-preserving restructuring and declared-rule derivations can run directly. Semantic judgement calls and replacements of existing task instructions require a concrete reviewed proposal even during initial preparation; requested synthetic generation applies directly. Shared normalization preserves typed messages/tools across workshop and consumers. Shared splitting keeps content, group and synthetic-seed identities together. Training atomically pins train/validation/eval versions, preserves selected rows, and performs model-specific preprocessing only. Detail: data-workshop skill; backend-architecture § Training preparation.
- Tenancy is project-scoped, no org layer. Auth is Clerk when `CLERK_API_SECRET_KEY` is set; blank secret plus Console `VITE_SELF_HOSTED=true` uses local email/password JWT (`POST /api/auth/local/`, create-on-first-use, no verification). A guest holds one project and can only read and claim; a view that accepts guest writes opts in with `guest_allowed = True` — never a permission class or middleware. API keys are `scope=account` or `scope=project`. Detail: backend-architecture § Auth and tenancy.
- Billing meters spend on the ledger always. Remaining-credit 402s, Free/Pro quotas and Stripe inject only when `STRIPE_SECRET_KEY` is set. Detail: backend-architecture § Auth and tenancy.
- Celery is four workers over five queues: `control` (default, orchestration and beat), `io` + `io_traces` (one threads worker, round-robin), `batch` and `interactive` (prefork). Workers are resource profiles, queues are fairness classes; only prefork enforces `time_limit`, so every time-limited task routes to `batch` or `interactive`. `tests/test_celery_topology.py` enforces both invariants and must stay in sync with `make worker` and docker-compose. Detail: backend-architecture § Celery topology.
- Most status transitions use `.filter().update()` — **no Django signals fire** and the in-memory object goes stale. Any side effect a transition needs is wired explicitly at the call site, on a freshly reloaded row. Preserve this pattern.
- Serving: a dense Modal-trained LoRA is served as an adapter on a shared BF16 base (never FP8); everything else is merged and quantized into a private checkpoint. Base weights live in one place and `fetch_base_model` is the only writer; a job waits on that cache before GPU training starts. GPU memory snapshots are off on purpose. Completions and playground SSE emit an idle ping every 15s because the edge ALB idle timeout is 60s and a cold boot is 2–7 min. Detail: backend-architecture § Serving and weights.
- Benchmark selection is separate from serving: capability `benchmark_model` selects a ready trained model, or null for the codebase incumbent. New training jobs snapshot it in `baseline_model`; `active_model` only controls serving. Training setup selects the benchmark explicitly for each job; MCP `set_benchmark_model` sets the capability default.
- Frontend must use the generated OpenAPI client in `frontend/src/openapi/` — never hand-edit it.
- Naming quirks: UI "Optimiser" = backend `optimizer`; UI "Training" = backend `finetuning`.
- `DESIGN.md` and `PRODUCT.md` carry a fixed YAML frontmatter schema that tooling reads — extend the values, don't restructure the documents.

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

- Semantic tokens only. Status tints flip per theme, so **never write `dark:` variants** — `border-success/40 bg-success/10 text-success`, not a hardcoded pair.
- **No box-shadows.** Every `--shadow-*` is `none` and `shadow-*` utilities are dead no-ops. Depth is surface layering plus 1px borders.
- Icons come from the central registry only: `import { Icon } from "@/components/ui/icons"`. The glyphs are a vendored path table in `ui/icons/glyphs.ts` that `bun run icons:vendor` regenerates; never import that module or `lucide-react` in app code — `check:design` fails on both.
- New shadcn components: `bunx shadcn@latest add <component>`, then adapt to the token system.
- Tokens, primitives, the border-contrast floor, and the duplicated table implementations: frontend-design skill.

### UI copy

Instrument voice — state the fact and stop ("9 rows", never "9 rows — small enough to read"). No design rationale, no reassurance, no coaching phrases. Applies to backend-generated copy too.

### Naming

Never put plan-phase labels (P0/P1, "Phase N") in code, comments, or test names — name by behavior. Backend tests live flat: `tests/test_<feature>.py`.

## Workflow

- Stay in the asked scope. Fix the stated thing plus genuine prerequisites; report adjacent findings as a short "found but did not change" list. If the task is much bigger than framed, say so before editing.
- Simple, self-evident fixes: typecheck + lint is the bar — skip the test suite and say so plainly. When a suite run is warranted: run once, tee to a log, grep the log (run-tests skill).
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

`.claude/hooks/` holds deterministic guards, wired into Claude Code through `.claude/settings.json` and into Cursor through `.cursor/hooks.json`. A denial names the fix; follow it rather than retrying. Cursor cannot veto a file write, so one rule stays as text there: never hand-edit `frontend/src/openapi/` — `make generate_api_client` rewrites it wholesale.

## Gotchas

- After `uv add`/`uv remove` the venv lacks the dev/test groups (pytest vanishes): `uv sync --group dev --group test`. Under Claude Code the `uv_resync` hook runs it.
- A new Python dependency reaches a compose worker only when its container is recreated (`docker compose up -d --force-recreate --no-deps <worker>`); a worker started before the image changed keeps the old venv and crashes on import at its next watchmedo restart.
- `overbae/services/sft_assets/` ships via `add_local_dir`, which bakes it into the image at deploy time — editing `pretok.py`/`train.py` does nothing until `modal deploy overbae/modal/modal_sft_worker.py`. The job runs the old code and fails identically, so it reads as "the fix didn't work".
- Chat templates disagree on OpenAI wire shape: `content: null` and JSON-string `tool_calls[].function.arguments` either raise or silently render an argument-less call. `pretok.normalize_openai_wire` is the one place that reshapes them — training tool data on a new family means checking it there, not per-family.
- Vite on WSL serves stale module transforms after bulk out-of-editor file changes; hard reload won't fix it — restart vite. Tell: runtime "X is not defined" for code tsc accepts.
- Two table implementations exist (`components/ui/data-table.tsx` and the paged rows grid in `components/datasets/notebook/rows-grid.tsx`) — a table fix must be checked in both.
- `--border` has almost no contrast headroom; the ramp is bare/`70`/`60`. Never lower a border opacity without `bun run check:contrast -- --all`.
- `manage.py seed_demo` must keep every job/run terminal and every span scored, or beat/reconcilers re-drive them against real providers (seed-demo-data skill).
