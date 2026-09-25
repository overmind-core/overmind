# Onboard a repository

Repo root only. The Console paste supplies `OVERMIND_API_URL` and a temporary account-scoped `OVERMIND_API_KEY`. Export them in the current shell only; do not echo the key in chat or write it into project files.

The paste names exactly one install source — PyPI or a local editable checkout — matched to the Console environment. Use that source only; do not search the web or substitute another ref.

Read [onboarding-progress.md](onboarding-progress.md) from the installed skill's
`references/` directory first and show its full
roadmap. It owns the stage numbers, progress messages, and data disclosures
through both connection and scanning.

Run every CLI and Python command in the project's environment: `uv run` for uv,
`poetry run` for Poetry, or the project's virtual-environment executables.
Do not invoke a globally installed `overmind` after adding a project dependency.

## Before this file is in the workspace

1. **Install overmind** using the single package source and toolchain commands from the paste:
   - Detect `uv.lock`, `poetry.lock`, `pyproject.toml`, or `requirements.txt` before choosing a command — do not default to pip.
   - Copy the `uv add`, `poetry add`, or requirements.txt line from the paste verbatim for the named source.
   - Do not use venv-only installs that leave the manifest unchanged.
1. **Export** `OVERMIND_API_URL` and `OVERMIND_API_KEY` from the paste (same shell as the commands below).
1. Run **`overmind init`** with the paste's arguments, using the project runner — it writes `overmind.toml`, prepares the selected IDE's MCP config, and installs this skill. It does not persist the bootstrap key.
1. Reopen **`references/onboard.md`** from the installed skill path and continue with project connection below.

## Connect project

1. **`overmind sync`** — creates the Console project from `project-name` in `overmind.toml` (seeded at init from the repo directory). Empty capabilities are fine. It writes `project-id` into the toml, stores the final project-scoped key in `.overmind/credentials.toml`, and updates every configured IDE's MCP entry. The bootstrap key stays in the shell only.
1. Report that the project is connected and a coding-agent reload is required to finish this stage. Stop until the user reloads. Do not print or re-export the project key, and do not run init again.

On a post-reload continuation, verify the existing project connection and
resume scanning without repeating bootstrap sync. Read `overmind://project/current`
when MCP is available to confirm the intended project; if authentication is
still unavailable, keep the connection stage blocked and report it.

**Do not run `/overmind setup` or read [setup.md](setup.md) until step 1 succeeds.** If `overmind.toml` already exists, merge — do not overwrite `base-url`, `project-id`, or capability ids without reading first.

To recreate a project: delete it in the Console, clear `project-id` in the toml, keep `project-name` as the repo directory name — never `repo_summary` — then repeat step 1.

## Scan and sync capabilities

Follow [setup.md](setup.md) end-to-end, retaining the shared stage numbering
and final `overmind sync`. That workflow owns scan progress and completion;
do not run an additional sync after it succeeds.
