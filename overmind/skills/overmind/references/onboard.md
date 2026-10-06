# Onboard a repository

Repo root only. The Console paste supplies `OVERMIND_API_URL` and a temporary account-scoped `OVERMIND_API_KEY`. Shell exports may not persist between a coding agent's commands, so prefix each bootstrap command with both (`OVERMIND_API_URL=… OVERMIND_API_KEY=… uv run overmind sync`). Do not echo the key in chat or write it into project files.

The paste names exactly one install source — PyPI or a local editable checkout — matched to the Console environment. Use that source only; do not search the web or substitute another ref.

Read [onboarding-progress.md](onboarding-progress.md) from the installed skill's
`references/` directory first and show its full
roadmap. It owns the stage numbers, progress messages, and data disclosures
through both connection and decorating.

Run every CLI and Python command in the project's environment: `uv run` for uv,
`poetry run` for Poetry, or the project's virtual-environment executables.
Do not invoke a globally installed `overmind` after adding a project dependency.

## Before this file is in the workspace

1. **Install overmind** using the single package source and toolchain commands from the paste:
   - Detect `uv.lock`, `poetry.lock`, `pyproject.toml`, or `requirements.txt` before choosing a command — do not default to pip.
   - Copy the `uv add`, `poetry add`, or requirements.txt line from the paste verbatim for the named source.
   - Do not use venv-only installs that leave the package manifest unchanged.
1. Run **`overmind init`** with the paste's arguments and environment prefix, using the project runner — it writes connection-only `overmind.toml` (`base-url`, `project-name`), prepares the selected IDE's MCP config, and installs this skill. It does not persist the bootstrap key and does not declare capabilities.
1. Reopen **`references/onboard.md`** from the installed skill path and continue with project connection below.

## Connect project

1. **`overmind sync`** with the same environment prefix — creates the Console project from `project-name` in `overmind.toml` (seeded at init from the repo directory). An empty AgentManifest is fine at this step (no decorators yet). It writes `project-id` into the toml, stores the final project-scoped key in `.overmind/credentials.toml`, and updates every configured IDE's MCP entry. The bootstrap key is never saved.
1. Report that the project is connected and a coding-agent reload is required to finish this stage. In Claude Code, ask the user to exit and run `claude -c`; in other clients, reload the IDE. Stop until the user reloads. Do not print the project key, pass the bootstrap key again, or run init again.

On a post-reload continuation, verify the existing project connection and
resume decorating without repeating bootstrap sync. Read `overmind://project/current`
when MCP is available to confirm the intended project; if authentication is
still unavailable, keep the connection stage blocked and report it.

**Do not run `/overmind setup` or read [setup.md](setup.md) until step 1 succeeds.** If `overmind.toml` already exists, merge — do not overwrite `base-url` or `project-id` without reading first. Never add capability tables to the toml.

To recreate a project: delete it in the Console, clear `project-id` in the toml, keep `project-name` as the repo directory name — never `repo_summary` — then repeat step 1.

## Declare capabilities (decorators) and sync

Follow [setup.md](setup.md) end-to-end: decorate entry points with
`@capability` / `@observe` / `task()`, then run `overmind sync` so the CLI
AST-scans those call sites and the server derives the agent graph. That
workflow owns discovery progress and completion; do not run an additional
sync after it succeeds. Do not hand-author capability cards in toml.
