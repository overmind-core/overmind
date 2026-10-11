# Connection and transfer acceptance

MCP impact: CLI-guided byte transfer; MCP-ready durable inspection through
`get_job(kind=dataset_transfer)` and its job resource. No browser, platform agent,
remote filesystem access or model-provider work is involved.

Failure cases to verify before implementation:

- MCP authentication succeeds but sandbox-local transport cannot connect. The
  connection check must report partial/not-ready, not configure around policy.
- Invalid credentials, denied project/write access, incompatible transfer
  contracts and redirects must fail before reserving an upload; diagnostics must
  not reveal credentials.
- Process interruption after reservation or a stored chunk must resume the same
  request key with the same SHA-256, project and recipe.
- Lost publication acknowledgement must recover the same dataset(s), including
  attachment to an existing chain, without another source cell.
- Changed bytes or recipe under a reused key must conflict. Chunk retransmission
  must compare bytes, not merely accept an already occupied offset.
- Concurrent publication must serialize. A crash after file append but before DB
  commit must recover the persisted file offset safely.
- Missing staging bytes, truncated uploads, hash mismatch, excessive size,
  foreign project identifiers and writes after publication must be rejected.
- Local export must preserve all 10,000 supplied rows and nested values; the
  original input is not rewritten. A deterministic verification transformation
  must retain its source version and explicit lineage.

The connection check executes from the actual invoking environment. A successful
check in an approved host execution does not certify a restricted sandbox or a
different MCP client's cached catalogue. Authorization stays with the host; no
automatic permission-file edits or browser fallback.

The CLI uses one resolved credential/endpoint pair for a read-only MCP handshake
and a project-scoped transfer preflight. Readiness measures reachability and
authorization, not a promise that disk/broker/provider infrastructure will never
fail. Publication and landing are distinct; landing progress remains on the
dataset job. Transfer records survive byte staging cleanup.

## Observed results — 2026-10-08

Implemented in the current checkout; no worktree, browser, training, evaluation
or inference job was used. The live run used the installed editable CLI and the
official MCP client against the existing local stack. The child CLI ran from an
empty temporary directory, with neither credential flags nor credential
environment variables. It used the saved account connection.

### Supplied file and retained output

| Fact                   | Observed value                                                     |
| ---------------------- | ------------------------------------------------------------------ |
| Input                  | `/Users/tyleredwards/Downloads/sample_10000.json`                  |
| Bytes                  | 42,174,160                                                         |
| Input SHA-256          | `fe2f701f6034d3adaa586ef747d3c3855ca10fcbac1de97fa3dd7e487058c521` |
| Explicit row selector  | `pairs`                                                            |
| Rows                   | 10,000                                                             |
| Project                | `financial-services` / `e18b29b5-915d-45a7-80cd-77ffe6559205`      |
| Transfer               | `9d19993d-4985-4ebd-ba45-789817829af9`                             |
| Request key            | `sample-10000-transfer-20261008-01`                                |
| Dataset                | `2340e11f-7fa3-47b2-b958-b100fbd955e3`                             |
| Source cell            | `53a8d1e6-1665-47ca-893b-93336024642b` / version 1.0               |
| Verification cell      | `5569b1b5-a872-4a76-b946-fef4f37b7457` / version 1.1               |
| Pipeline run           | `6193a5f9-cfcb-4777-a6cf-681a37955f3a`                             |
| Both cell fingerprints | `46fdca74f75a2eb35c1f1b49cafe84e250249d265e2a91b502b5b6c510e266ab` |
| Exact-cell exports     | 32,354,952 bytes each; all 10,000 rows compared                    |

The file contains a metadata wrapper and a `pairs` array, not a top-level row
array. Added an explicit JSON array selector rather than guessing its meaning.
The original uploaded bytes, including wrapper metadata, remain retained. Both
exports preserved every nested `left`, `right` and `judgement` value in order,
with distinct source-row identities. The original local file's hash was unchanged.

The verification recipe selects those three existing fields. This tests
transformation execution, immutable lineage and exact export; it is not a
semantic transformation into a model's training format. Dataset intent remains
pending. No claim of training readiness or label quality is made.

### Interruption, recovery and issues fixed

1. A controlled client disconnection occurred after the server stored the first
   8,388,608-byte chunk. MCP independently reported those stored bytes.
1. A separate CLI invocation resumed that same transfer. Publication exposed a
   PostgreSQL failure: a row lock included a nullable outer join. The transaction
   rolled back without publishing a dataset; the uploaded bytes remained intact.
1. Restricted the publication lock to the transfer row. Retried the same receipt
   and request key, including concurrent CLI completions. Both recovered the same
   dataset. No replacement upload or duplicate source was created.
1. The MCP transformation completed, and source/output exports matched all rows.
   Repeated live checks reused the same dataset and single pipeline run.

Two harness/environment problems were also corrected: the root environment had
an older CLI, so the replay now explicitly selects the installed editable CLI;
and a helper's `name` argument collided with a tool's recipe-name field. These
were test harness failures, not additional dataset failures.

The latest recovery-and-export verification completed in 2.315 seconds against
already landed data. This is **not** a cold upload/landing benchmark. The earlier
complete interrupted journey included the failure and repair above; no reliable
single uninterrupted cold-start duration is claimed.

Live logs are local to this machine:

- `/private/tmp/overmind-transfer-e2e.dWbIGw/replay-installed.log`: interruption
  and the original PostgreSQL failure.
- `/private/tmp/overmind-transfer-e2e.dWbIGw/replay-recovered.log`: successful
  publication followed by the harness argument collision.
- `/private/tmp/overmind-transfer-verified.dX0UvW/replay.log`: final successful
  recovery, MCP recipe and exact export. In this saved log,
  `continued_saved_receipt=true` means `interrupted_after_bytes` is the starting
  offset of the replay, not a new injected failure. The harness now names this
  field `resume_from_bytes` for subsequent recovery-only runs.

### Repeat the live check

Requirements: existing local API, PostgreSQL and Celery services; migration
0031 applied; installed editable SDK/CLI; saved account-scoped localhost
connection; MCP access to the project above; the supplied input file; permission
for the invoking process to reach localhost. No key is printed or passed on the
command line. Root Python dependencies supply the official MCP client.

Use a new empty output directory because exports intentionally do not overwrite
files. This command reuses the retained receipt and creates no new dataset:

```sh
set -o pipefail
transfer_check_dir=$(mktemp -d /tmp/overmind-transfer-check.XXXXXX)
uv --cache-dir /tmp/overmind-mcp-audit-uv-cache run --no-sync python \
  tests/evidence/dataset_transfer_replay.py \
  /Users/tyleredwards/Downloads/sample_10000.json "$transfer_check_dir" \
  --cli /Users/tyleredwards/.local/bin/overmind \
  --request-key sample-10000-transfer-20261008-01 \
  --resume-transfer 9d19993d-4985-4ebd-ba45-789817829af9 \
  2>&1 | tee "$transfer_check_dir/replay.log"
```

To repeat the actual first-chunk interruption, omit `--resume-transfer` and use
an explicitly new request key and empty directory. That variant intentionally
creates another dataset and verification pipeline; it is not a read-only check.

### Regression commands and results

Backend/Workshop/MCP: **206 passed**, 108 warnings, 8.52 seconds. Warnings concern
the existing short test JWT secret and an existing streaming-response test.

```sh
set -o pipefail
uv --cache-dir /tmp/overmind-mcp-audit-uv-cache run --no-sync pytest \
  tests/test_dataset_transfers.py tests/test_dataset_uploads.py \
  tests/test_dataset_files.py tests/test_dataset_api.py tests/test_dataset_chain.py \
  tests/test_dataset_dispatch.py tests/test_dataset_split.py \
  tests/test_workshop_documents.py tests/test_mcp_datasets.py \
  tests/test_mcp_manifest.py tests/test_mcp_resources.py tests/test_mcp_prompts.py \
  tests/test_mcp_authorization.py tests/test_workshop_redesign.py -q \
  2>&1 | tee /tmp/overmind-transfer-regression-verified.log
```

SDK/setup: **62 passed**, 1.05 seconds. Run from `overmind/` using its own test
environment, not the root environment's older SDK dependencies.

```sh
set -o pipefail
.venv/bin/python -m pytest tests/test_resumable_transfer.py \
  tests/test_transfer_connection.py tests/test_dataset_cmd.py tests/test_init.py \
  -q 2>&1 | tee /tmp/overmind-transfer-sdk-verified.log
```

`make generate_api_client` regenerated the API client. `bun run typecheck` in
`frontend/` passed. Both changed shipped skills passed the skill validator.
The final targeted pre-commit run passed, as did `git diff --check`.

The actual restricted-shell check was also exercised:

```sh
/Users/tyleredwards/.local/bin/overmind connection check \
  --project-id e18b29b5-915d-45a7-80cd-77ffe6559205 --json
```

It exited 1 with `ready=false`, `network_permission_denied`,
`next_action=request_host_network_permission`, and transfer `not_checked`.
This is the expected negative result: no transfer was reserved. The same
check inside the approved live replay reported both MCP and transfer ready.

### Boundaries and remaining limitations

- Readiness is scoped to the actual execution environment. The sandbox's network
  restriction remains enforced. Host-approved execution worked; setup never
  disables the sandbox or silently chooses a hosted API or browser.
- The live interface reports contract 4.1.0 and transfer protocol 1. This chat's
  cached native tool schema still predates the new `dataset_transfer` job kind;
  the official MCP client verified the live schema. Reload/reconnect the native
  MCP client to discover that new kind. Existing dataset inspection still works.
- Global connection configuration was tested using an isolated temporary home;
  the already aligned real global configuration was not rewritten.
- Publication and landing are separate. A transfer's stored publication result
  is a snapshot, not the current dataset state. Read the dataset job for landing.
- An unacknowledged landing dispatch remains visible and can be recovered by
  repeating completion. There is no new automatic background dispatcher.
- `make check-migrations` detected no missing model migrations, but its branch
  history guard fails on pre-existing migrations 0013–0015 relative to
  `origin/main`. Those migrations were not rewritten. Migration 0031 was applied
  successfully to the existing local database. Log:
  `/tmp/overmind-transfer-migrations-checked.log`.
- This verifies the scoped transfer/Workshop changes, not every product workflow,
  all possible file sizes or paid training/inference behavior.
