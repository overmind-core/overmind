# Local Overmind routing — 2026-10-09

## Verified outcome

All 15 identified local workspaces resolve their effective Codex Overmind server
to `http://localhost:8000/api/mcp/`. Fresh MCP SDK sessions authenticated with each
workspace's effective credentials, discovered a local project, and passed the CLI
MCP/transfer check for that same project. API URL and key environment overrides
were removed for these checks. Retained results: `local-routing-2026-10-09.json`.

This verifies saved configuration and new connections, not a live refresh of the
already-open `Create KYC eval benchmark` chat. Reload/restart Codex before
continuing that chat and rediscover the local project; its previously selected
hosted project UUID must not be reused. No message was sent to the other chat.

## What changed

- Removed hosted Codex overrides from `Documents/ChatGPT/empty` and
  `Documents/GitHub/financial-services`; both inherit the global local account
  connection. Their `overmind.toml` files now select localhost, without a hosted
  project binding. Capability metadata and all other configuration are preserved.
- Archived hosted credential files in those workspaces and
  `Documents/GitHub/platform`. No hosted keys were revoked or tested.
- Changed the existing Overmind Cursor entries in financial-services and platform
  to the paired local endpoint/account key; unrelated server entries are preserved.
- Disabled the duplicate `overmind@personal` hosted plugin. The direct local MCP
  and nine locally installed Overmind skills remain available.
- Added non-secret local API defaults to `.zshenv`, and recorded the local-only
  preference in global coding-agent instructions. A fresh zsh process confirmed
  both `OVERMIND_API_URL` and `OVERMIND_BASE_URL` select localhost. New setup should
  use `overmind init --env local`. No network or approval policy was relaxed.
- Fixed the SDK transfer resolver: repository credentials cannot follow a changed
  API URL, including when the same repository key is supplied explicitly. A
  conflict fails before network access. The installed CLI is editable, so this
  fix is already used locally.

The ten migrated configuration files were backed up with mode 0600 under:

`/Users/tyleredwards/.config/overmind/backups/local-connection-0kdvxnzf`

Three hosted credential files were removed from their active locations only
after their backup bytes were verified. They are recoverable from that directory.
No datasets, source files, hosted evaluators, hosted eval sets or paid jobs were
modified. No browser or new worktree was used.

## Failure reproduced and regression results

The missing case was an existing workspace whose MCP, project metadata and
credential store selected hosted Overmind, despite a global local connection.
Changing only `--api-url` or `OVERMIND_API_URL` retained the hosted key.

Before the fix, nine CLI boundary cases failed by attempting a network request:
connection check/upload/export, each with a URL argument, URL environment
override, or repeated repository key plus changed URL. The tests were written
and run before implementation. After the fix, those cases reject the mismatch
without a request or secret disclosure. Tests use synthetic keys and intercept
network access; no real credential is deliberately sent to a wrong host.

```sh
cd /Users/tyleredwards/Documents/GitHub/overmind/overmind
env -u OVERMIND_API_KEY -u OVERMIND_API_URL -u OVERMIND_BASE_URL .venv/bin/pytest tests/test_transfer_connection.py tests/test_resumable_transfer.py tests/test_dataset_cmd.py tests/test_init.py tests/test_config_toml.py tests/test_sync_ensure_project.py -q
```

Observed: **96 passed**. Logs:

- `/private/tmp/overmind-endpoint-guard-before.log`: nine reproduced failures.
- `/private/tmp/overmind-endpoint-guard-after.log`: 53 transfer cases passed.
- `/private/tmp/overmind-local-routing-regression.log`: 96 broader cases passed.

SDK lint/format and scoped pre-commit passed. No backend API schema, MCP catalogue,
database, worker or frontend behavior changed, so no API client generation,
migration, container restart or frontend suite was required.

## Repeat live verification

Requires this machine's existing saved account/project connections, running local
API, Codex CLI, repository Python dependencies and scoped localhost access.
The verification script is read-only and prints no credential values:

```sh
cd /Users/tyleredwards/Documents/GitHub/overmind
.venv/bin/python tests/evidence/local_connection_verification.py
```

Workspace inventory: 13 saved Codex project directories plus two existing trusted
roots discovered in the user configuration. This is not a recursive audit of
every directory on the machine, nor protection against a future explicit hosted
configuration change. Existing valid project-scoped local connections retain
their project scope.

From the formerly failing workspace:

```sh
cd /Users/tyleredwards/Documents/ChatGPT/empty
overmind connection check --project-id e18b29b5-915d-45a7-80cd-77ffe6559205 --json
```

Observed: contract 5.7, account credential, upload/export allowed, localhost.
An actual export from this workspace, with API URL/key environment variables
removed, downloaded the existing 20-row final partition successfully:

```sh
task_export_dir=$(mktemp -d /private/tmp/overmind-local-workspace-export.XXXXXX)
env -u OVERMIND_API_KEY -u OVERMIND_API_URL -u OVERMIND_BASE_URL overmind dataset export 5c6f37d2-990e-5b59-8a8b-599a3b3405be --cell ad37a595-6312-4887-b599-3e5d8e69e831 --output "$task_export_dir/final.jsonl" --json
wc -l "$task_export_dir/final.jsonl"
```

Observed: 138,901 bytes, 20 rows, fingerprint
`abfc7b58d27d12ef8fdf45380783ccbb86635aefa70bfb3a5a7e0fb6d06a0853`.
Output retained at `/private/tmp/overmind-local-workspace-export.CnHiLB/final.jsonl`.

Project configuration precedence and installed plugin enable/disable settings
follow [official Codex configuration guidance](https://developers.openai.com/plugins/build/plugins#enable-or-disable-a-plugin-for-a-repo).
The hosted plugin cache was not rewritten.
