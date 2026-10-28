# Overmind Platform

Monorepo for the Overmind Console (`frontend/`, React), API (`overbae/`, Django + DRF + Celery) and SDK/CLI (`overmind/`). Agent improvement platform: observability → data workshop → evals → training and inference. The platform is AGPL-3.0; `overmind/` is MIT. There is no JavaScript SDK: a JavaScript app sends OpenTelemetry to `/api/v1/traces`, and `overmind chassis` scans TypeScript as well as Python.

This file is the playbook every task needs; Claude Code, Codex and Cursor read it natively. Procedures and subsystem maps live in `.agents/skills/`, which each tool loads when a task matches. This file holds traps and rules only, so keep it under 16 KiB: change or delete a line rather than append a feature description.

## Branches

- `main` is protected: feature branch → PR, using `.github/PULL_REQUEST_TEMPLATE.md`. Never force-push `main`.
- The repo is public (`overmind-core/overmind`). Root ruff excludes `overmind/`. Every merge to `main` that touches `overmind/` publishes to PyPI, so SDK CI requires a version bump on those PRs (pr-etiquette skill).

## Commands

- Frontend (Bun, from `frontend/`): `bun run typecheck`, `bun run lint` (Biome; no ESLint or Prettier), `bun run test` (vitest), `bun run check:all` (design, contrast and controls scripts). There is no `node` on this machine; run scripts with `bun`.
- Backend (uv): `make test` (parallel pytest on the compose Postgres), `make test-journeys` (end-to-end journeys on the live ASGI app, a real Celery worker and compose Redis), `make test-serial`, `make lint-backend` (ruff), `make check-migrations` (after a model change, rebased on `origin/main`), `uv run <cmd>`.
- SDK (uv): `make -C overmind test`, `make -C overmind lint-check`.
- CI (`.github/workflows/ci.yml`) runs lint, frontend, test and journeys on `main` and ready PRs, plus migrations on ready PRs; draft PRs skip it. SDK CI runs on `overmind/` changes.
- A local deployment already runs through `docker compose` with hot reload. Do not start dev servers to verify a change. Celery workers restart through watchmedo; `docker compose restart <worker>` if in doubt.
- After changing the backend API surface: `make generate_api_client` (api-endpoints skill).
- Run `pre-commit run --files <changed files>` at the end of a multi-file task, before committing.

## Traps you can't guess from the code

- Tracing is span-only: there is no Trace table. A trace is the spans sharing `trace_id`; the root has `parent_span_id IS NULL`. OTLP ingest is `POST /api/v1/traces`.
- Most status transitions use `.filter().update()`, so no Django signals fire and the in-memory object goes stale. Wire each side effect at the call site on a freshly reloaded row.
- MCP (`/api/mcp/`, `services/mcp/`) is the first-class agent surface. REST, MCP and the Console share domain services; MCP is not a proxy of either. Every change gets an MCP-impact decision (mcp skill).
- The frontend uses only the generated OpenAPI client in `frontend/src/openapi/`; never hand-edit it.
- Platform model IDs (judges, generation, Jev) live in `overbae/core/model_registry.py`; the training and serving catalog lives in `overbae/modal/models.json`. Jev decisions use `overbae/core/decisions.py`.
- Tenancy is project-scoped with no org layer. A view that accepts guest writes opts in with `guest_allowed = True`, never with a permission class or middleware.
- OTLP ingest never creates a capability, a scan never deletes one, and `DELETE` is a soft delete. On OTLP spans, wire identity is `overmind.capability.id` alone.
- Celery routing must agree across `CELERY_TASK_ROUTES`, `make worker`, docker-compose and `docker/worker-topology.json`. Only prefork pools enforce `time_limit`. `tests/test_celery_topology.py` checks compose and `make worker` against the routes.
- Native coding agents own interpretation, code and semantic work. The platform runs deterministic, lineage-bound operations and never invokes a platform planning or generation agent.
- Work-starting operations take a project-scoped request key: an identical retry returns the same record, and a changed payload conflicts. An unknown provider acknowledgement is reconciled from its saved call ID, never resubmitted.
- Measured facts only. Missing usage, progress, scores and telemetry stay unknown; never fill them with a percentage, a zero, an ETA or elapsed time.
- Code under `overbae/modal/`, `modal_shared/` and `overbae/services/sft_assets/` reaches Modal only after `modal deploy`; training and serving pin immutable releases.
- Naming: UI "Optimiser" is backend `optimizer`; UI "Training" is backend `finetuning`; UI "Capability" is the `Capability` model, and the sidebar's "Agent" is the project graph.
- Overwatch (`/api/internal/overwatch/*`, app in `sales-dashboard/`) only reads product records and runs no user authentication. Its tokens, caches and Slack boundary are in backend-architecture `references/auth-and-billing.md`.
- `DESIGN.md` and `PRODUCT.md` carry a fixed YAML frontmatter schema that tooling reads. Extend the values; don't restructure the documents.

Subsystem detail lives in the skills: backend-architecture (Celery, tracing and scoring, evaluation, capabilities, auth and billing, training, serving, data-first), data-workshop, mcp, frontend-design.

## Style

- **Rebuild, don't patch.** There are no real users yet. Build the change the right way from first principles and delete the old path; no flags, shims, `_v2` names or `if legacy` branches. Migrations are the exception: production and staging hold real state. Full bar: engineering-taste skill.
- **Comments** carry only what the code can't: a workaround, a wire-format or contrast constraint, an ordering invariant, a trap. Delete restatements, banners, history and name-respelling docstrings. Full policy: code-comments skill.

### Python

Ruff enforces formatting and line length. What it can't:

- Imports go at the top of the file. The only exception is a lazy import that breaks a cyclic dependency, with a one-line reason.
- Never import a `_`-prefixed name from another module; the owning module exposes a public name first.
- Serializers for all API input and output; a view never returns a raw dict.
- `select_related`/`prefetch_related` on any queryset whose rows touch a relation.
- Business logic goes in model methods, managers or `services/`, not in views.

### Frontend

- The Console is dark-only, including first paint, authentication, charts and flow canvases. Use semantic tokens, with no `dark:` variants: `border-success/40 bg-success/10 text-success`.
- Flat surfaces: `shadow-*` utilities are disabled. Depth is surface layering plus 1px borders.
- Icons come only from `import { Icon } from "@/components/ui/icons"`; `check:design` fails on `lucide-react` or the vendored glyph table.
- Tokens, primitives, the border-contrast floor and the two table implementations: frontend-design skill.

### UI copy

Instrument voice: state the fact and stop ("9 rows", not "9 rows — small enough to read"). No design rationale, reassurance or coaching phrases. This applies to backend-generated copy too.

### Naming

Name by behavior. Plan-phase labels (P0/P1, "Phase N") stay out of code, comments and test names. Backend tests live flat in `tests/test_<feature>.py`; journeys in `tests/journeys/test_<promise>.py`.

## Workflow

- Don't write unit tests after you write code. Prefer E2E tests as the testing mechanism. Each E2E run ends with a repeatable artifact: the command, inputs or fixtures, environment requirements and observed results.
- If a system must be tested in isolation, first write down the ways it can fail, then write the code. Keep an isolated test only when it catches a concrete failure that E2E coverage misses. No assertions that mirror the implementation, pin incidental text or restate declarations.
- Tests fake only what we do not own, and only at the network (`tests/fakes`: FakeLLM, FakeModal, vendor, Stripe and Clerk APIs, `scripted` HTTP). Patch `overbae.*` only to capture a Celery `.delay`/`.apply_async`; call public entry points. Each contract has one owning test, and a journey owns a customer promise. The `test-seams` pre-commit hook refuses new seams. Detail: run-tests skill.
- Stay in the asked scope: fix the stated thing plus genuine prerequisites, and list adjacent findings as "found but did not change". If the task is much bigger than framed, say so before editing.
- Simple, self-evident fixes need typecheck and lint only; say plainly that tests did not run. When a suite run is warranted, run it once, tee to a log and grep the log.
- Which tests to run (compose Postgres and Redis up): while editing, the feature's test file and its journey (`make test-journeys test_args="-k <name>"`); before a push, `make test` (about 75 s); when a change crosses ingest, sync, the worker, the gateway, MCP or the Console, `make test-journeys` (about 6 min). CI runs the rest.
- A change is finished when every surface that reflects it is updated: MCP, Celery routing, `seed_demo`, the generated client, this file and the skills, and the docs repo at `../docs` (pr-etiquette skill lists them). CI cannot catch these.
- When a change makes this file, a skill, a hook message or another agent doc untrue, update or delete that line in the same PR; the pr-etiquette skill lists the places to search. There is one copy of every rule.
- Commit messages: short subject plus at most one body line, no co-author trailers. Commit and push only when asked; on a sweep branch, one commit per observation.

## Communication

- Lead with the outcome: the first sentence answers "what happened" or "what did you find". Keep output short by leaving things out, not by compressing the writing.
- Report what the tool results show: failing tests with their output, a skipped step as skipped, a verified result plainly.
- UI work pauses for a look in the browser before screenshots or a PR; expect iterations.
- Sub-agents explore; edits and decisions stay in the main thread. No multi-agent workflows unless asked.
- When there is enough information to act, act. Give a recommendation, not a survey of options.

## Agent setup

One source serves all three tools:

|             | Instructions              | Skills                                         | Hooks                                                                |
| ----------- | ------------------------- | ---------------------------------------------- | -------------------------------------------------------------------- |
| Claude Code | this file (v2.1.277+)     | `.claude/skills` → symlink to `.agents/skills` | `.claude/settings.json`                                              |
| Codex       | this file (cut at 32 KiB) | `.agents/skills`                               | `.codex/hooks.json`                                                  |
| Cursor      | this file                 | `.agents/skills`                               | imports `.claude/settings.json` (Third-Party Imports, on by default) |

- `.agents/hooks/` holds deterministic guards shared by all three. A denial names the fix; follow it rather than retrying.
- Codex runs project hooks only after you approve them with `/hooks`, and asks again after every hook change.
- Claude Code reads this file only when no `CLAUDE.md` or `CLAUDE.local.md` exists. To keep a personal `CLAUDE.local.md`, set `/config` → Project instructions → `claude-md-and-agents-md`.
- Skill frontmatter is `name` and `description` only, so all three tools read it. Keep each `SKILL.md` under 500 lines and move detail into linked files beside it. Personal skills go in `~/.agents/skills/`.
- The `agent-docs-size` pre-commit hook enforces the size limits for this file and the skills.

## Gotchas

- After `uv add`/`uv remove` the venv lacks the dev and test groups (pytest vanishes): `uv sync --group dev --group test`. The `uv_resync` hook runs it.
- A new Python dependency reaches a compose worker only when its container is recreated (`docker compose up -d --force-recreate --no-deps <worker>`). An older worker crashes on import at its next watchmedo restart.
- `overbae/services/sft_assets/` is baked into the Modal image at deploy time. Editing `pretok.py` or `train.py` does nothing until `modal deploy overbae/modal/modal_sft_worker.py`; the job fails identically, so it reads as "the fix didn't work".
- Chat templates disagree on OpenAI wire shape: `content: null` and JSON-string `tool_calls[].function.arguments` either raise or render an argument-less call. `pretok.normalize_openai_wire` is the one place that reshapes them.
- Vite on WSL serves stale module transforms after bulk file changes made outside the editor; a hard reload won't fix it, so restart Vite. Tell: runtime "X is not defined" for code tsc accepts.
- Two table implementations exist (`components/ui/data-table.tsx` and `components/datasets/notebook/rows-grid.tsx`); check a table fix in both.
- `--border` has almost no contrast headroom; the ramp is bare, `/70`, `/60`. Run `bun run check:contrast -- --all` before you lower a border opacity.
- `manage.py seed_demo` must keep every job and run terminal and every span scored, or beat and the reconcilers re-drive them against real providers (seed-demo-data skill).
