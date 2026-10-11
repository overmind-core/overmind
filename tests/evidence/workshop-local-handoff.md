# Local Workshop authentication handoff

Verified 2026-10-08, against the existing localhost deployment.

## What failed

The native MCP connection authenticated correctly. The installed `overmind dataset upload` command was a separate process, with neither an API-key environment variable nor repository credentials. It failed with `Missing API key`. The agent then incorrectly opened the Console. The prior configuration's no-browser developer instruction was absent from that chat's recorded instructions.

Previous acceptance coverage explicitly passed keys to upload/export helper functions. The SDK suite also supplied a test key in its shared environment. The connection replay checked MCP discovery and authenticated reads only. These tests did not exercise an installed CLI launched from a repository-free chat without injected credentials. Calling that complete end-to-end verification was too broad.

## Changes

- Dataset upload/export now resolve an optional user-level transfer connection at `$XDG_CONFIG_HOME/overmind/connection.toml`, default `~/.config/overmind/connection.toml`. Its `api-key` and `base-url` are used together only when other credentials are absent. A different selected API address is rejected before transfer. POSIX permissions must exclude group/other access; setup writes mode `0600`. Malformed profile errors do not include source text or credentials.
- The local profile uses the already-authorized account key and localhost endpoint from Codex's MCP connection. No new key was needed. Explicit/project connection behavior remains available; no hosted fallback was added.
- Moved this user's no-browser preference from the inactive developer-instructions setting to global `AGENTS.md`. Codex subsequently injected the global guidance into this chat's instructions. This is instruction-based routing, not a browser permission lock.
- MCP initialization, upload/export resources, the upload prompt, shipped dataset skill, repository guidance and CLI docs now describe the authentication boundary and forbid browser fallback for transfer failures.
- The live test found an additional export-receipt defect: converting case-insensitive HTTP headers to a plain dictionary dropped cell/version/fingerprint metadata when the server used lowercase names. Preserving case-insensitive lookup fixed the exact-cell export receipt.

MCP impact: **CLI-guided**. Local bytes still use the existing CLI; MCP controls project selection, version inspection and transformations. No new MCP operation, REST schema, UI, worker topology or migration is required.

## Verification

Logs and retained exported fixture files: `/private/tmp/workshop-handoff.R7WCue/`.

| Check                                                            | Observed result                                                                             |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Ordinary installed CLI before the change, outside the repository | Missing API key; exit 1                                                                     |
| New credential-boundary tests before implementation              | 6 failures / 6 passes                                                                       |
| Upload/export and new credential tests after implementation      | 36 passed                                                                                   |
| First live handoff                                               | Upload and both cells succeeded; export receipt failed with missing `cell`; fixture removed |
| Same live handoff after receipt fix                              | Both projects passed                                                                        |
| Configured SDK suite, after receipt fix                          | 607 passed; standard `test_spans.py` exclusion retained                                     |
| Focused MCP prompt/resource suite                                | 47 passed                                                                                   |

Each live case uses three source rows, filters to two, appends a separate conversation projection cell, queries the original three rows, then exports the exact final cell and verifies two message-bearing rows. Projects: `financial-services` and `overmind`. The installed executable is `/Users/tyleredwards/.local/bin/overmind`, an editable installation of this checkout. No key or API address is supplied to the CLI by the replay; it removes inherited credential environment variables. Only the MCP verification client reads Codex's existing authentication.

The successful run deleted its two fixture datasets: `c05231b3-2c0f-471c-aa00-b0936572c5d1` and `0db5634d-104c-48fc-8231-efa31c993db1`. The initial failed run also removed its fixture. Original fixture CSV and downloaded outputs remain locally; the deleted datasets can be recreated with the replay. No actual KYC dataset was uploaded or modified. No browser or paid model job was used.

## Repeat

Requirements: the existing API/Celery deployment on `http://localhost:8000`, the saved account-level MCP and CLI connections to that same endpoint, installed editable Overmind CLI, root virtual environment with `httpx` and MCP SDK, and access to the two named local projects. The replay creates and deletes only its own fixture datasets.

```sh
cd /Users/tyleredwards/Documents/GitHub/overmind
handoff_dir=$(mktemp -d /private/tmp/workshop-handoff.XXXXXX)
cp tests/evidence/workshop-handoff-fixture.csv "$handoff_dir/handoff.csv"
set -o pipefail
.venv/bin/python tests/evidence/workshop_handoff_replay.py "$handoff_dir" 2>&1 | tee "$handoff_dir/result.log"
```

Focused and configured regression commands:

```sh
cd /Users/tyleredwards/Documents/GitHub/overmind/overmind
.venv/bin/python -m pytest tests/test_transfer_connection.py tests/test_dataset_cmd.py -q
.venv/bin/python -m pytest tests/ -q -n 2 --ignore=tests/test_spans.py --dist worksteal
cd /Users/tyleredwards/Documents/GitHub/overmind
.venv/bin/python -m pytest tests/test_mcp_prompts.py tests/test_mcp_resources.py -q -n 2
```

The isolated tests cover credential resolution, explicit connection precedence, wrong-address rejection (argument, environment and repository), malformed profiles, secret-safe errors and insecure permissions. These security failures are deliberately not exercised against arbitrary real endpoints.

## Boundaries

This is a real CLI/MCP handoff test, not a new autonomous agent conversation. The no-browser rule's loading was observed; future model adherence is not guaranteed by deterministic tests. Existing interrupted chats were not resumed or messaged. Full frontend/backend suites and Python 3.10 runtime execution were not repeated for this focused change. The TOML backport dependency is conditional on Python below 3.11; local verification used Python 3.13.
