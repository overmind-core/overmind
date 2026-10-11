# Local Workshop readiness — 2026-10-09, 21:12 PDT

## Outcome

Local API, Console, workers, Workshop runtime, installed CLI and native MCP are
ready for user testing. This is a readiness smoke check, not another full
regression or a claim that every previously documented product gap is resolved.
No paid preparation, training, evaluation, inference or deployment was launched.
The browser was not opened or operated.

The native MCP handoff limitation in the earlier `local-mcp-readiness-2026-10-09.md`
report no longer applies to this chat: its connected tools successfully discovered
projects, queried source rows, validated and executed a preview, and read the
terminal receipt directly.

## Updates

- Kept the existing current checkout; no new worktree, commit or push.
- Removed the stale contract-version pin from the canonical Overmind skill. It
  now directs agents to the connected interface for the current version.
- Synced all nine canonical Overmind skills, including references, into this
  repository's `.agents/skills` and `/Users/tyleredwards/.agents/skills` for use
  outside this repository. Source and installed trees compare without differences.
- The installed CLI is already editable and imports from this checkout, version
  0.1.85. Its new original-source export option is present. No reinstall required.
- No credential replacement, endpoint change or sandbox-policy change.
- Containers already mount the current checkout and use current code. No restart
  or database migration was needed.

## Environment and observed checks

- Project: financial-services, `e18b29b5-915d-45a7-80cd-77ffe6559205`.
- API/MCP: `http://localhost:8000/api/mcp/`; Console: `http://localhost:5173`.
- Interface: contract 5.7, 67 tools; catalogue hash
  `0b44c2eea2e02c419c96817a09b97393d2a5290f188f46e523c0dcecede65b01`.
- Fresh MCP SDK transport: initialization, tool list, interface read and
  authenticated project discovery passed; ten Workshop lifecycle tools present,
  three retired agent tools absent. Nine projects discovered, with no global
  project selection required.
- Installed CLI connection check from `/private/tmp`, without injected API URL
  or key: MCP ready, transfer ready, account scope, upload and export allowed.
- The initial sandboxed CLI check returned `network_permission_denied`. The
  same check passed with scoped host access. This does not certify permission in
  another sandbox or future session; local file transfer may require a scoped
  localhost approval. MCP authentication and shell network permission are separate.
- API, frontend, PostgreSQL and Redis healthy; all four Celery workers, beat and
  Workshop runner running. Frontend mount is the current checkout's `frontend`.
- `python manage.py migrate --check`: exit 0; `python manage.py check`: no issues.
- `bun run typecheck`: exit 0. No visual UI verification in this pass.
- Skill validator and scoped pre-commit passed. Full regression was not rerun;
  prior broader results are in `workshop-user-training-journey.md`.

## Real-data smoke checks

1. Native MCP `query_dataset`: exact source cell of the matching-pairs dataset
   returned 10,000 rows for `SELECT count(*) AS rows FROM t`.
1. `validate_dataset_pipeline`: retained four-script revision valid, no warnings,
   approved runner ready. Semantic suitability remains unmeasured by this check.
1. `run_dataset_pipeline` preview receipt
   `30167332-8c7e-4464-b3e6-012ba522c43c`: all four scripts completed with exit code
   zero, 50 rows through every step, no failed checks, 3.474 measured execution
   seconds. Absolute `min_rows` remains correctly deferred to publication. No
   output cell was published. Container cleanup confirmed for every step.
1. CLI exported the existing exact 20-row final partition to a fresh temporary
   directory: 138,901 bytes and 20 JSONL lines. No existing file overwritten.

Retained measurements: `local-workshop-ready-2026-10-09.json`.

## Repeat

Requires the existing local Compose deployment, saved account connection and
installed CLI; no injected credentials. Scoped host network/Docker access may be
required in a restricted coding environment.

```sh
cd /private/tmp
env -u OVERMIND_API_KEY -u OVERMIND_API_URL /Users/tyleredwards/.local/bin/overmind connection check --project-id e18b29b5-915d-45a7-80cd-77ffe6559205 --json
```

From the repository:

```sh
.venv/bin/python tests/evidence/workshop_connection_replay.py
docker compose ps --format '{{.Service}}: {{.Status}}'
docker compose exec -T api python manage.py migrate --check
docker compose exec -T api python manage.py check
```

For the same retained preview, use MCP `get_job` with kind `dataset_pipeline`,
project ID above and receipt `30167332-8c7e-4464-b3e6-012ba522c43c`. Exact preview
submission (the stable key returns the existing receipt):

```json
{
  "dataset": "369b56c9-3f77-48a6-8c5c-5aa7f217b602",
  "project_id": "e18b29b5-915d-45a7-80cd-77ffe6559205",
  "pipeline": "4b3ad974-0c1c-4868-a962-753811d4bc81",
  "source_cell": "1e6be2d8-1c31-48e3-b30e-c7ca400710dc",
  "source_fingerprint": "46fdca74f75a2eb35c1f1b49cafe84e250249d265e2a91b502b5b6c510e266ab",
  "mode": "preview",
  "preview_rows": 50,
  "parameters": {"seed": 42, "sample_groups": 384},
  "request_key": "local-readiness-sync-20261010-0412"
}
```

Export into a fresh directory, from outside the repository:

```sh
task_export_dir=$(mktemp -d /private/tmp/overmind-local-ready.XXXXXX)
env -u OVERMIND_API_KEY -u OVERMIND_API_URL /Users/tyleredwards/.local/bin/overmind dataset export 5c6f37d2-990e-5b59-8a8b-599a3b3405be --cell ad37a595-6312-4887-b599-3e5d8e69e831 --output "$task_export_dir/final.jsonl" --json
wc -l "$task_export_dir/final.jsonl"
```
