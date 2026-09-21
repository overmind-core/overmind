# Contributing

Thanks for helping build Overmind. This page covers the mechanics; `AGENTS.md` is the engineering playbook (architecture invariants, style, workflow) and applies to every contribution, human or agent-assisted.

## Setup

Requirements: Docker, [uv](https://docs.astral.sh/uv/), [Bun](https://bun.sh/).

```bash
cp .env.example .env      # fill in at minimum OPENROUTER_API_KEY
docker compose up         # API, Console, Postgres, Redis, Celery workers
make install-hooks        # pre-commit
```

The Console runs at `http://localhost:3000` and the API at `http://localhost:8000`. The compose stack hot-reloads; you do not need separate dev servers.

## Before you push

Run the same checks CI runs:

| Area          | Command                                                                                   |
| ------------- | ----------------------------------------------------------------------------------------- |
| Backend lint  | `make lint-backend`                                                                       |
| Backend tests | `make test`                                                                               |
| Migrations    | `make check-migrations` (after a model change, rebased on `origin/main`)                  |
| Frontend      | `bun run lint`, `bun run typecheck`, `bun run check:all`, `bun run test` from `frontend/` |
| SDK           | `make -C overmind lint-check`, `make -C overmind test`                                    |

`pre-commit run --files <changed files>` before committing a multi-file change.

## Pull requests

- `main` is protected. Branch from `main`, open a PR, and fill in `.github/PULL_REQUEST_TEMPLATE.md`. The Verification section takes the commands you ran, not the ones you would run.
- Keep a PR to one change. Fix the stated thing plus its real prerequisites; list adjacent findings in the PR body instead of fixing them.
- A change is finished when every surface reflecting it is updated: MCP (`overbae/services/mcp/`), Celery routing, `seed.py`, the generated OpenAPI client (`make generate_api_client`), and `AGENTS.md` or the skill that describes the behaviour. The PR template walks this list.
- Frontend code uses the generated client in `frontend/src/openapi/`; never edit it by hand.
- Commit messages: short subject plus at most one body line. No co-author trailers.
- SDK changes (`overmind/`) bump `overmind/pyproject.toml` `version` and add a `CHANGELOG.md` entry.

## Coding agents

`.claude/skills/` holds subsystem maps and procedures that Claude Code and Cursor load on demand; `AGENTS.md` indexes them. If you contribute with a coding agent, point it at the repo root and it picks these up. Hooks in `.claude/hooks/` guard generated files and dangerous commands; a denial names the fix.

## Licence

The platform (`overbae/`, `frontend/`, and the rest of this repository) is AGPL-3.0. The SDK and CLI under `overmind/` are MIT. A PR licenses your changes under the licence of the files they touch.

## Reporting bugs and security issues

Open a GitHub issue for bugs and feature requests. For vulnerabilities, follow `SECURITY.md` instead of opening a public issue.
