---
name: pr-etiquette
description: How to open a complete pull request on overmind-core/platform — the CI gates, the cross-cutting surfaces a change must carry with it (MCP, blast radius, the docs repo), gh pr edit being broken here, and body conventions. Use when opening a PR, editing a PR title or body, or pushing branch work for review.
---

# Pull requests on overmind-core/platform

## Before you push — what CI will run

`.github/workflows/ci.yml` runs four jobs on every non-draft PR. Run the equivalents locally rather than discovering them in CI:

| CI job       | What it runs                                                                                                                                  | Local equivalent            |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- |
| `lint`       | `uv run ruff check .` and `uv run ruff format --check .`                                                                                      | `make lint-backend`         |
| `frontend`   | `bun run check`, `bun run typecheck`, `bun run check:all`, `bun run test` — in parallel, all must pass                                        | same four, from `frontend/` |
| `test`       | `uv run pytest tests/ -n auto --dist worksteal -q`                                                                                            | `make test`                 |
| `migrations` | `makemigrations --check` on the merge commit plus `scripts/check_migrations.py`; only when `overbae/models/` or `overbae/migrations/` changed | `make check-migrations`     |

`pre-commit run --files <changed files>` before committing substantial work. Note that `mdformat` reflows markdown, so commit its output rather than fighting it.

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
`.claude/skills/mcp/` and must not be copied into `overmind/skills/`.

### 2. Cross-vertical blast radius

Ask what else reads what you changed, and check each one that does:

- Celery routing in `CELERY_TASK_ROUTES`, `make worker`, and docker-compose — `tests/test_celery_topology.py` enforces the three agreeing
- `manage.py seed_demo` — new tables and states must seed, and must stay terminal (seed-demo-data skill)
- the generated OpenAPI client (api-endpoints skill)
- `AGENTS.md` and any skill whose statements the change makes untrue

### 3. Docs

User-visible behaviour changes ship with a docs change in the sibling repo
`overmind-core/docs` (Mintlify, checked out at `../docs`): the relevant `.mdx`
page, plus `docs.json` if the navigation moves. Open that PR alongside this one
and link the two. A platform PR that changes what a user sees is not complete
while the docs still describe the old behaviour.

## Creating

`gh pr create --body-file <file>` works normally. Write the body to a temp file in the scratchpad, never inline via `--body` (quoting breaks on long bodies). Fill in `.github/PULL_REQUEST_TEMPLATE.md` — the Verification section takes the commands you actually ran, not the ones you would run.

## Editing title/body — gh pr edit is BROKEN

`gh pr edit <n> --title/--body-file` aborts on this repo with a Projects-classic GraphQL deprecation error (`repository.pullRequest.projectCards`) **and exits looking like it worked**. The PR is left unchanged.

Use REST instead:

```bash
python3 -c "import json;print(json.dumps({'title':'<title>','body':open('pr.md').read()}))" > pr.json
gh api -X PATCH repos/overmind-core/platform/pulls/<n> --input pr.json --jq .title
```

Always re-read the PR afterwards (`gh pr view <n>`) to confirm the edit landed.

## Conventions

- Never push directly to `main`; land work via a PR from a feature branch. A hook blocks the push.
- Verify the actual current branch with `git status -sb` before pushing — the session-start banner can be stale.
- Commit messages: short subject + at most one body line. No co-author trailers.
- For fix-sweep PRs, a before/after table in the body is the house style (see PR #458/#460 for the shape).
- If the change makes `AGENTS.md` or a skill wrong, fix it in the same PR. There is one copy of each rule.
