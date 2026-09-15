# Onboard a repository

Repo root only. The Console paste supplies `OVERMIND_API_URL` and a temporary account-scoped `OVERMIND_API_KEY`. Export them in the current shell only; do not echo the key in chat or write it into project files.

The paste names exactly one install source — PyPI, platform git @ main, or a local editable checkout — matched to the Console environment. Use that source only; do not search the web or substitute another ref.

## Before this file is in the workspace

1. **Install overmind** using the single package source and toolchain commands from the paste:
   - Detect `uv.lock`, `poetry.lock`, `pyproject.toml`, or `requirements.txt` before choosing a command — do not default to pip.
   - Copy the `uv add`, `poetry add`, or requirements.txt line from the paste verbatim for the named source.
   - Do not use venv-only installs that leave the manifest unchanged.
1. **Export** `OVERMIND_API_URL` and `OVERMIND_API_KEY` from the paste (same shell as the commands below).
1. Run **`overmind init`** exactly as in the paste — it writes `overmind.toml`, prepares the selected IDE's MCP config, and installs this skill. It does not persist the bootstrap key.
1. Reopen **`references/onboard.md`** from the installed skill path and continue with phase 1 below.

## Phase 1 — sync (gate)

1. **`overmind sync`** — creates the Console project from `project-name` in `overmind.toml` (seeded at init from the repo directory). Empty capabilities are fine. It writes `project-id` into the toml, stores the final project-scoped key in `.overmind/credentials.toml`, and updates every configured IDE's MCP entry. The bootstrap key stays in the shell only.
1. Tell the user to open the Overmind Console and reload the coding agent once. Do not print or re-export the project key, and do not run init again. Continue with phase 2 after the reload.

**Do not run `/overmind setup` or read [setup.md](setup.md) until step 1 succeeds.** If `overmind.toml` already exists, merge — do not overwrite `base-url`, `project-id`, or capability ids without reading first.

To recreate a project: delete it in the Console, clear `project-id` in the toml, keep `project-name` as the repo directory name — never `repo_summary` — then repeat step 1.

## Phase 2 — capability scan

Follow [setup.md](setup.md) end-to-end (discovery → capability cards → provenance → eval matrix → `overmind_capabilities.json` → convert to `overmind.toml` → delete the JSON).

## Phase 3 — push capabilities

Run `overmind sync` again when the toml is ready.
