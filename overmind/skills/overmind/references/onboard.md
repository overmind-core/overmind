# Onboard a repository

Repo root only. The Console paste supplies `OVERMIND_API_URL` and a temporary account-scoped `OVERMIND_API_KEY`. Shell exports may not persist between a coding agent's commands, so prefix each bootstrap command with both (`OVERMIND_API_URL=… OVERMIND_API_KEY=… uv run python -m overmind sync`). Do not echo the key in chat or write it into project files.

The paste names exactly one install source — PyPI or a local editable checkout — matched to the Console environment. Use that source only; do not search the web or substitute another ref.

Read [onboarding-progress.md](onboarding-progress.md) from the installed skill's
`references/` directory first and show its full
roadmap. It owns the stage numbers, progress messages, and data disclosures
through both connection and scanning.

Run every CLI through the selected Python: the project's `uv run python -m overmind`
or `poetry run python -m overmind`, or the isolated tooling runner below.
`uv run overmind` can still resolve a global executable if the project has no CLI.

## Verify the project runtime

Onboarding requires **Python 3.10+ and overmind>=0.1.95**. Install or upgrade
through the repository's dependency manifest using the source named in the
Console paste. For PyPI, use `uv add "overmind>=0.1.95"`,
`poetry add "overmind>=0.1.95"`, or `overmind>=0.1.95` in requirements.txt.
Keep an explicitly supplied editable source. Do not change the project's supported
Python range just to make resolution succeed; report the exact conflict and use
isolated tooling recovery below instead of blocking capability onboarding.

Before `init` or the first `sync`, run both commands below. Replace `python`
with `uv run python`, `poetry run python`, or the project's virtual-environment
Python in **every** command, including conversion and later syncs.

```bash
python -c "import sys, overmind; from importlib.metadata import version; from packaging.version import Version; from overmind.__main__ import app; from overmind.utils import convert_json_to_toml; assert sys.version_info >= (3, 10), 'Overmind onboarding requires Python 3.10+'; print('Python:', sys.executable); print('Overmind:', version('overmind')); print('Package:', overmind.__file__); assert Version(version('overmind')) >= Version('0.1.95'), 'Overmind onboarding requires overmind>=0.1.95'"
python -m overmind --help
```

Both must succeed. Verify the reported version is at least 0.1.95 and the
interpreter/package paths belong to the selected project or isolated environment, or specified editable
source. A missing CLI/converter, older version or mismatched path means installation
is incomplete. Use isolated tooling recovery and rerun these checks **before creating a
project**. Do not accidentally resolve an unrelated global CLI or hand-author the capability TOML.
This is Overmind's version-consistency requirement, not a user-imposed constraint.

Repeat both checks after the coding-agent reload, before scanning or capability
sync. Preserve an existing `project-id`, capability IDs, endpoint and saved
credentials during repair; do not rerun bootstrap or recreate the project.
Keep the destination explicit on every sync, including after reload:
`python -m overmind sync --api-url <saved-base-url>`. Use the endpoint from the
Console paste and matching repository configuration, never a different inherited
`OVERMIND_API_URL`. After bootstrap, use the saved project credential without the
bootstrap key. Sync rejects a saved credential paired with a different endpoint
before sending any request.
After an SDK upgrade, refresh the installed skill with
`python -m overmind skills sync overmind --ide <client>`, using the selected
client and the selected Python runner, then reopen this file.

### Isolated tooling recovery

If the application cannot install a compatible SDK, continue repository scanning,
conversion and capability sync with a dedicated tool environment. For PyPI, replace
`python` in **both** checks and all tooling commands with:

```bash
uv tool run --isolated --python 3.11 --from "overmind==0.1.95" python
```

For an explicitly supplied editable source, replace `--from "overmind==0.1.95"`
with `--with-editable "<supplied-sdk-path>" --from overmind`. Do not change the named
source. If uv is unavailable, create a dedicated Python 3.10+ tooling virtual
environment, install the same source there, and use its explicit Python path.

Keep the repository root as the working directory. Use the same runner for checks,
init, chassis, Python conversion, skills sync and capability sync, including after
reload. Record the runner/version in the progress handoff without credentials.
This isolated environment is explicitly permitted; a compatible tool being outside
the application environment is not a blocker. Preserve project and capability IDs,
endpoint and saved credentials. If bootstrap already succeeded, resume scanning
and capability sync without rerunning init or creating a project.

Tool isolation does not install application tracing. Before adding imports or
decorators, verify the tracing SDK in the application's own environment. If its
dependencies are incompatible, finish capability sync and report instrumentation
as pending with the exact conflict; do not claim full onboarding is complete.

## Before this file is in the workspace

1. **Install overmind** using the single package source and toolchain commands from the paste:
   - Detect `uv.lock`, `poetry.lock`, `pyproject.toml`, or `requirements.txt` before choosing a command — do not default to pip.
   - Copy the `uv add`, `poetry add`, or requirements.txt line from the paste verbatim for the named source.
   - Record application SDK installs in the manifest; isolated tooling recovery is exempt.
1. Run **`python -m overmind init`** with the paste's arguments and environment prefix, using the project runner — it writes `overmind.toml`, prepares the selected IDE's MCP config, and installs this skill. It does not persist the bootstrap key.
1. Reopen **`references/onboard.md`** from the installed skill path and continue with project connection below.

## Connect project

1. **`python -m overmind sync`** with the same environment prefix — creates the Console project from `project-name` in `overmind.toml` (seeded at init from the repo directory). Empty capabilities are fine. It writes `project-id` into the toml, stores the final project-scoped key in `.overmind/credentials.toml`, and updates every configured IDE's MCP entry. The bootstrap key is never saved.
1. Report that the project is connected and a coding-agent reload is required to finish this stage. In Claude Code, ask the user to exit and run `claude -c`; in other clients, reload the IDE. Stop until the user reloads. Do not print the project key, pass the bootstrap key again, or run init again.

On a post-reload continuation, verify the existing project connection and
resume scanning without repeating bootstrap sync. Read `overmind://project/current`
when MCP is available to confirm the intended project; if authentication is
still unavailable, keep the connection stage blocked and report it.

**Do not run `/overmind setup` or read [setup.md](setup.md) until step 1 succeeds.** If `overmind.toml` already exists, merge — do not overwrite `base-url`, `project-id`, or capability ids without reading first.

To recreate a project: delete it in the Console, clear `project-id` in the toml, keep `project-name` as the repo directory name — never `repo_summary` — then repeat step 1.

## Scan and sync capabilities

Follow [setup.md](setup.md) end-to-end, retaining the shared stage numbering,
the `python -m overmind sync`, the decorators it applies afterwards and the sync that
follows them. That workflow owns
scan progress and completion; do not run an additional sync after it succeeds.
