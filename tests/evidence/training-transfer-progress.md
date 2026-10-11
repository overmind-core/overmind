# Training transfer progress

Failure cases to verify before implementation:

- Row-selection construction, upload acknowledgement and provider materialization
  must not collapse into a static label or share misleading counters.
- Reading/preparing bytes must never be reported as uploaded bytes. Failed uploads
  must retain the last acknowledged count, not claim completion.
- Provider verification/indexing/selection must expose bounded, measured progress
  without changing row order, duplicate visits or output integrity checks.
- Repeated observations must not refresh forward-progress time or duplicate the
  activity log. Stale observers, replaced attempts and cancellation must not
  overwrite newer work.
- Missing telemetry must be explicit, with no invented percentage or heartbeat.
  Existing pinned worker releases must remain callable without resubmission.
- Console and MCP must read the same persisted progress. Status reads must not
  invoke workers. No training, evaluation or inference is launched by these tests.

## Environment and fixtures

- Python project `.venv`, Bun frontend dependencies, SQLite test settings. No real
  dataset, training run or credential is mutated by the automated tests.
- `test_training_transfer_journey.py` creates 3,000 distinct synthetic chat rows,
  selects 3,002 training visits (including duplicates) and two validation rows.
  The real selection writer/materializer, database receipts, active-task
  reconciler, REST loss-curves endpoint and authenticated MCP HTTP transport run.
  Modal storage/remote calls are replaced with a temporary-directory adapter.
- Local Console verification: the existing Vite server on port 5173 and
  `/tests/training-transfer.html`, which explicitly labels its measurements as
  synthetic and has no provider/job actions.

## Commands and results — 2026-10-09

```sh
.venv/bin/pytest -q tests/test_training_transfer_journey.py tests/test_training_telemetry.py tests/test_training_artifact_streaming.py tests/test_training_submission.py tests/test_finetuning_reconciler.py tests/test_operational_progress.py tests/test_training_preparation.py tests/test_finetuning_monitor.py
cd frontend
bun run test src/components/finetuning/training-monitor.test.tsx
bun run typecheck
```

- Backend: **101 passed**, 10 existing JWT test-key length warnings. Output:
  `/tmp/training-transfer-final.log`.
- Frontend: **16 passed**; typecheck passed. Output:
  `/tmp/training-transfer-frontend-final.log`.
- Scoped Ruff/Biome checks passed. All applicable pre-commit hooks passed with
  `UV_CACHE_DIR=/tmp/overmind-transfer-uv-cache UV_NO_SYNC=1`; the first hook attempt
  was blocked by sandbox access to the normal uv cache.
- First backend run found a missing operational-fact allowlist entry. Fixed and
  retested; unsupported arbitrary fact keys remain rejected.
- Browser verified 56,000/224,000 rows at 25%, 32/64 KiB acknowledged at 50% with
  1/2 files, and the no-telemetry state without an invented progress bar.
- The existing KYC run was already training during inspection. It was not
  restarted, cancelled or migrated to a different worker release.
- Deployed immutable worker `overmind-sft-ec6741f6c4ffa07f06769bf9` to the configured
  `overmind-dev` environment. Initial deployment could not connect from the
  sandbox; the scoped network-enabled deployment succeeded. Deployment output:
  `/tmp/training-transfer-deploy-retry.log`.

## Scope and remaining limits

Modal's public volume upload API provides file acknowledgement, not continuous
network-byte progress. Counters explicitly describe acknowledged files/bytes.
Provider materialization snapshots are collected by the existing 15-second
controller; frontend elapsed time updates independently. Progress publication is
throttled and failed telemetry delivery does not fail materialization. Newly
pinned worker releases expose these measurements; running old releases cannot
retroactively produce them. This verification did not launch paid training,
evaluation or inference, or claim a new live-provider training round trip.
