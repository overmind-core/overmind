# Native-agent Data Workshop verification

Date: 2026-10-08. Checkout: `/Users/tyleredwards/Documents/GitHub/overmind`.
Branch: `codex/general-decision-training`. No commit, push or branch-history rewrite.

## Scope and failure contract

The native coding agent authors transformations. The platform owns ingestion,
immutable recipes, bounded deterministic execution, published cells, lineage and
durable receipts. It does not run Workshop agents or semantic judges.

The original step-by-step cell layout remains. Retired chat and mutable-script
controls, their REST endpoints, MCP tools, runtime, provider wiring and executable
replay scripts are deleted, not replaced by compatibility handlers. ChatGPT login,
linking, funding and credential storage are removed.

Failure cases identified before implementation and exercised by the journeys:

- Source landing or a brief unexpectedly starts diagnosis or generation.
- Changed source/artifact fingerprints are accepted after inspection.
- Duplicate request keys create another output, or accept different payloads.
- Failed or cancelled work publishes data or changes a frozen consumer version.
- A late source-landing delivery publishes after queued cancellation.
- Historical unpublished cells block later source attachments.
- External outputs lose parent identity or inherit human-reviewed claims.
- A foreign project can inspect, execute or import another project's data.
- Recipes execute caller-supplied Python on the platform.
- Polling loses receipt identity, or metadata validation partially applies edits.
- Cutover loses published frames, historical conversations or provider receipts.
- Removed routes remain callable, or their deleted imports prevent startup.

## Repeatable automated checks

Python 3.13 with the repository's existing uv environment and dev/test groups;
Bun for the frontend. Pytest loads `tests.settings`, isolated SQLite databases,
temporary media and an in-memory Celery transport. OCR cases require local
Tesseract. Analytics fixtures bind a local test server. No paid provider run is
part of this verification.

Fixtures create synthetic support questions, pinned train/eval cells, full native
probability targets, document/image uploads and a 2,100-row imported artifact.
The native-agent journey persists a receipt, invokes its worker, and then checks
REST, MCP, job resources, lineage and consumer readability. The migration replay
uses a separate temporary SQLite database with real migrations enabled.

From the checkout:

```sh
UV_NO_SYNC=1 uv run pytest tests/ -n 4 --dist worksteal -q
```

Final result: **4,035 passed, 15 skipped**, 1,000 warnings, 29.98 seconds.
Log: `/private/tmp/workshop-final-full-tests.log`. Warnings include the test JWT
key length, streaming-response adaptation and locally invoked Modal functions.

The earlier full run had 10 failures. Retired chat expectations and source
fixtures were updated; the full command above was rerun after the final
cancellation and attachment fixes. The standalone edge-case replay also passed:

```sh
UV_NO_SYNC=1 uv run pytest tests/test_workshop_redesign.py \
  tests/test_workshop_documents.py tests/test_dataset_streaming.py \
  tests/test_dataset_dispatch.py -q
```

Result: **44 passed**.
Log: `/private/tmp/workshop-cancellation-attachment-final.log`.

From `frontend/`:

```sh
bun run test
bun run typecheck
bun run lint
bun run check:all
```

Frontend suite: **113 files, 854 tests passed**.
Log: `/private/tmp/workshop-frontend-tests.log`. Typecheck and lint passed.
Design, contrast and control checks also passed; log:
`/private/tmp/workshop-final-design-check.log`.
The OpenAPI client was regenerated with `make generate_api_client`; it was not
edited manually.

Bundled `overmind`, `overmind-datasets` and `overmind-training` skills pass
`quick_validate.py`. The repository pre-commit hooks corrected formatting on their
first run. The final `pre-commit run --files <changed files>` passed every
applicable hook. Log: `/private/tmp/workshop-final-precommit.log`.

## PostgreSQL API replay

The migrated local API container also executes a rollback-only REST journey:

```sh
docker compose exec -T api python manage.py shell \
  < tests/evidence/workshop_postgres_replay.py
```

It creates synthetic source data, saves and executes a conversation recipe,
recovers the same receipt on retry, imports an externally authored version with a
long provenance description, checks database constraints and confirms original
source bytes are unchanged. Result: **2 completed runs, 3 cells, rollback
verified**. All database rows are rolled back and temporary media is removed.
Log: `/private/tmp/workshop-postgres-replay.log`.
The replay does not send paid requests or enqueue background work.

## Migration and live local cutover

`make check-migrations` finds **no missing model migrations**, but its branch
numbering check still fails on the inherited committed migrations 0013–0015,
because `origin/main` already reaches 0015.
Log: `/private/tmp/workshop-final-migration-check.log`.
Those historical migration names were not rewritten; branch reconciliation is
required before merging.

New migrations 0024–0027 applied successfully to the running local deployment.
Workers were checked idle, the affected workers and scheduler were stopped for
the cutover, and then restarted. API and frontend serve the original checkout.
The old isolated worktree was archived.

Before migration, a private PostgreSQL custom-format backup was created:

`/private/tmp/overmind-workshop-cutover.3YcnJr/database.dump`

It is 56 MiB with mode 0600; `pg_restore --list` read 1,058 entries. Recovery
requires restoring this database backup with matching pre-cutover code; the
history migration is forward-only. The single linked ChatGPT account already
had a usable password. No remote provider revocation was attempted.

After migration, all **126 datasets** remain. `DatasetHistory` contains 113
conversations, 53 source operations, 662 records, 54 unpublished cells, 436 work
items and 44 runs. Old Workshop and ChatGPT tables are absent. Published cells
retain their frames and read-only historical metadata.
Log: `/private/tmp/workshop-live-migrations.log`.

The original dirty checkout was separately preserved before transfer at
`/private/tmp/overmind-workshop-transfer.meUcXN/original-edits.tar.gz`.
Unrelated Training work remains on the branch.

## Browser verification

Read-only verification used the existing local deployment and authenticated
session; it did not start a development server or change a dataset.

Route:
`http://localhost:5173/datasets/dcfb4cca-3168-4739-aa20-457efd0bb59b?projectId=e18b29b5-915d-45a7-80cd-77ffe6559205`

Observed "Workshop grounded completion check" with three cells: Source (3 rows),
Derived examples (4 rows), and Project SFT columns (4 rows, active). The notebook
outline, original cell frames, row inspection, filters, export and active-version
controls render. No chat composer or obsolete execution controls remain.
The browser reported no console errors.

No live external coding-agent generation or provider-funded training was run.
Technical compatibility and attributed lineage do not certify semantic truth.
