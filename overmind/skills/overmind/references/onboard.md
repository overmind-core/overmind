# Onboard a repository

Repo root only. Read [onboarding-progress.md](onboarding-progress.md) first and show its full roadmap. It owns the stage numbers, progress messages and data disclosures through connection and scanning.

Run every CLI through the selected Python: the project's `uv run python -m overmind` or `poetry run python -m overmind`, or the isolated tooling runner below. `uv run overmind` can resolve a global executable.

## Verify the project runtime

A JavaScript/TypeScript repository has no Python environment to verify: use the isolated tooling runner below for every command and read [javascript.md](javascript.md) before the scan.

Onboarding requires **Python 3.10+**. Always install or upgrade to the latest `overmind` with the installer the project uses: `uv pip install --upgrade overmind`, `poetry run pip install --upgrade overmind`, otherwise `python -m pip install --upgrade overmind`. Do not change the project's supported Python range to make resolution succeed; report the exact conflict and use isolated tooling recovery instead.

Replace `python` with `uv run python`, `poetry run python` or the project's virtual-environment Python in **every** command, including conversion and later syncs. Before `init` or the first `sync`, and again after the coding-agent reload, run `python -m overmind --version`. It must succeed and the CLI must come from the project or isolated environment; otherwise fix the install before creating a project.

Keep the destination explicit on every sync, including after reload: `python -m overmind sync --api-url <saved-base-url>`. An inherited `OVERMIND_API_URL` must not redirect it to another environment. After an SDK upgrade, run `python -m overmind skills sync overmind --ide <client>` and reopen this file.

### Isolated tooling recovery

If the application cannot install a compatible SDK, replace `python` in every command with:

```bash
uv tool run --isolated --refresh --python 3.11 --from overmind python
```

Without uv, create a dedicated Python 3.10+ virtual environment, install the latest `overmind` there and use its Python path. Keep the repository root as the working directory and use the same runner for checks, init, chassis, conversion, skills sync and sync, including after reload. An isolated CLI is not a blocker.

Isolation does not install application tracing. Verify the tracing SDK in the application's own environment before adding imports or decorators. If it conflicts, finish capability sync and report instrumentation as pending with the exact conflict.

## Connect project

1. `python -m overmind init --ide <client>` writes `overmind.toml`, prepares the IDE's MCP config and installs this skill.
1. `python -m overmind sync` creates the Console project from `project-name` in `overmind.toml` (seeded from the repo directory), writes `project-id` into the toml, stores the project credential in `.overmind/credentials.toml` and updates every configured IDE's MCP entry. Empty capabilities are fine.
1. Report that the project is connected and a coding-agent reload is required. In Claude Code, ask the user to exit and run `claude -c`; elsewhere, reload the IDE. Stop until the user reloads. Do not print credentials or run init again.

After reload, read `overmind://project/current` to confirm the project and resume scanning without repeating sync. If authentication is still unavailable, keep this stage blocked and report it.

**Do not run `/overmind setup` or read [setup.md](setup.md) until sync succeeds.** If `overmind.toml` exists, merge: do not overwrite `base-url`, `project-id` or capability ids without reading first. To recreate a project, delete it in the Console, clear `project-id` in the toml, keep `project-name` as the repo directory name, then repeat sync.

## Scan and sync capabilities

Follow [setup.md](setup.md) end-to-end, including the decorators it applies and the sync that follows them. That workflow owns scan progress and completion; do not run another sync after it succeeds.
