---
name: pr-etiquette
description: How to open a complete pull request on overmind-core/overmind — the CI gates, the cross-cutting surfaces a change must carry with it (MCP, blast radius, the docs repo, agent instructions), gh pr edit being broken here, and body conventions. Use when opening a PR, editing a PR title or body, or pushing branch work for review.
---

# Pull requests on overmind-core/overmind

## Before you push — what CI will run

`.github/workflows/ci.yml` runs five jobs on every non-draft PR; pushes to `main` run all but `migrations`. Run the equivalents locally rather than discovering them in CI:

| CI job       | What it runs                                                                                                                                  | Local equivalent                                           |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `lint`       | `uv run ruff check .` and `uv run ruff format --check .`                                                                                      | the same two commands (`make lint-backend` fixes in place) |
| `frontend`   | `bun run check`, `bun run typecheck`, `bun run check:all`, `bun run test` — in parallel, all must pass                                        | same four, from `frontend/`                                |
| `test`       | `uv run pytest tests/ -n auto --dist worksteal -q`                                                                                            | `make test`                                                |
| `journeys`   | `make test-journeys` with Postgres, Redis and Tesseract                                                                                       | `make test-journeys`                                       |
| `migrations` | `makemigrations --check` on the merge commit plus `scripts/check_migrations.py`; only when `overbae/models/` or `overbae/migrations/` changed | `make check-migrations`                                    |

`pre-commit run --files <changed files>` before committing substantial work. Note that `mdformat` reflows markdown, so commit its output rather than fighting it.

### SDK changes: bump the bundled release

Any change under `overmind/`, including bundled skills or documentation, triggers
the SDK version gate. Bump `overmind/pyproject.toml` above the version on the PR's
base branch, match `overmind/overmind/__init__.py`, and regenerate `overmind/uv.lock`
with `uv lock --project overmind`. Verify the versions agree before pushing.
The root `uv.lock` pins the platform's installed SDK dependency; do not change it
to an unpublished local release. The merge publishes the bumped version to PyPI
(`sdk-publish.yml` runs on every push to `main` that touches `overmind/`).

### Migrations: rebase before you push

`make test` runs `--nomigrations`, so a stale branch passes locally and forks the migration
graph on main. Main moves fast; a branch that touched a model is usually behind by the time
it is ready. Before pushing a model change:

1. `git fetch origin && git rebase origin/main`.
1. If main gained migrations, delete yours and regenerate it: `makemigrations` picks the next
   number and depends on main's newest leaf.
1. `make check-migrations` — the exact CI check, against `origin/main`.

Never resolve a fork with `makemigrations --merge`. A `_merge_` file or a reused number fails
the `migrations` job; renumber instead.

## Completeness — the surfaces a PR must carry with it

CI cannot catch these. Every one of them has shipped half-done before, so walk the
list before opening the PR and say in the body which ones applied.

### 1. MCP

MCP is the agent API, with the same completeness bar as the
Console. Walk the mcp skill: classify the change (MCP-ready / CLI-guided /
frontend-only / out of scope), then tools, resources, prompts, contracts, and
tests in
`overbae/services/mcp/` and `tests/test_mcp_*.py`. Update the user-facing
Overmind skill (`overmind/skills/overmind/`) when the public catalog, prompts,
or resources change. A Console-only vertical change that an agent should be
able to progress is not complete. The mcp skill itself lives in
`.agents/skills/mcp/` and must not be copied into `overmind/skills/`.

### 2. Cross-vertical blast radius

Ask what else reads what you changed, and check each one that does:

- Celery routing in `CELERY_TASK_ROUTES`, `make worker`, docker-compose and `docker/worker-topology.json` — `tests/test_celery_topology.py` checks compose and `make worker`; keep `worker-topology.json` in step by hand
- `manage.py seed_demo` — new tables and states must seed, and must stay terminal (seed-demo-data skill)
- the generated OpenAPI client (api-endpoints skill)
- the agent instructions (section 4)

### 3. Docs

User-visible behaviour changes ship with a docs change in the sibling repo
`overmind-core/docs` (Mintlify, checked out at `../docs`): the relevant `.mdx`
page, plus `docs.json` if the navigation moves. Open that PR alongside this one
and link the two. A platform PR that changes what a user sees is not complete
while the docs still describe the old behaviour.

### 4. Agent instructions

Agent docs drift when a PR changes behaviour and leaves the old text in place. Before
you open the PR, search them for every name, limit, route, state and command you
changed, and update each match in the same PR:

```bash
rg -n '<changed name>' AGENTS.md CONTRIBUTING.md DESIGN.md PRODUCT.md .agents/ overmind/skills/
```

- `AGENTS.md` — the rules and traps every task needs.
- `.agents/skills/*/SKILL.md` and their `references/*.md` — subsystem maps and procedures.
- `.agents/hooks/` — a guard whose rule or message names what you changed. Hook wiring in `.claude/settings.json` and `.codex/hooks.json` changes together.
- `overmind/skills/` — the user-facing skills that `overmind init` installs; a change there needs the SDK version bump above.
- `CONTRIBUTING.md`, `DESIGN.md` and `PRODUCT.md` when they describe the change.

Update or delete the stale line; don't append a description of the new behaviour
beside it. There is one copy of each rule: put a fact in the skill that owns the
subsystem and point to it from elsewhere. `AGENTS.md` keeps only what every task
needs, and the `agent-docs-size` hook enforces its size.

## Creating

`gh pr create --body-file <file>` works normally. Write the body to a temp file in the scratchpad, never inline via `--body` (quoting breaks on long bodies). Fill in `.github/PULL_REQUEST_TEMPLATE.md` — the Verification section takes the commands you actually ran, not the ones you would run.

## Editing title/body — use REST, not gh pr edit

`gh pr edit <n> --title/--body-file` aborts on this repo with a Projects-classic GraphQL deprecation error (`repository.pullRequest.projectCards`) and exits looking like it worked, leaving the PR unchanged. The `bash_guard` hook denies it.

Use REST instead:

```bash
python3 -c "import json;print(json.dumps({'title':'<title>','body':open('pr.md').read()}))" > pr.json
gh api -X PATCH repos/overmind-core/overmind/pulls/<n> --input pr.json --jq .title
```

Always re-read the PR afterwards (`gh pr view <n>`) to confirm the edit landed.

## Conventions

- Never push directly to `main`; land work via a PR from a feature branch. A hook blocks a push that names `main` or `master`; a bare `git push` while on `main` is not caught.
- Verify the actual current branch with `git status -sb` before pushing — the session-start banner can be stale.
- Commit messages: short subject + at most one body line. No co-author trailers.
- For fix-sweep PRs, a before/after table in the body is the house style.
