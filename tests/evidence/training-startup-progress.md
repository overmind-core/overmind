# Training startup progress

Failure cases, recorded before implementation:

- Runtime imports, weight loading, adapter setup and dataset construction must
  not all appear as one static loading message.
- Repeated heartbeats must not imply forward progress. Repeated observations
  must retain the actual stage start; a restarted attempt must reset it.
- Counters must not leak from a completed stage into an unmeasured stage.
- Missing or stale telemetry must remain explicit. No timer-driven percentage
  or estimated shard count is permitted.
- REST, MCP and the operational timeline must agree on recorded stage facts;
  reading them must not invoke a training worker.
- Existing pinned runs cannot acquire new worker instrumentation retroactively.

MCP classification: MCP-ready through existing job and operation reads. No new
tool or launch action is needed. Tests use provider fixtures, not paid training.

## Environment and repeatable checks

Python project `.venv`, Bun dependencies, SQLite test settings and the existing
local Vite server on port 5173. The journey test uses a temporary worker telemetry
directory, real database persistence, the authenticated MCP HTTP transport and
the Console's loss-curves endpoint. Provider invocation is explicitly forbidden
during those reads. No real dataset or job is modified.

```sh
.venv/bin/pytest -q tests/test_training_startup_journey.py tests/test_training_telemetry.py tests/test_training_transfer_journey.py tests/test_finetuning_monitor.py tests/test_operational_progress.py tests/test_sft_preparation_process.py tests/test_sft_no_truncation.py tests/test_decision_training.py
.venv/bin/pytest -q tests/test_sft_token_accuracy.py tests/test_finetuning_download_progress.py tests/test_training_telemetry.py tests/test_training_startup_journey.py
cd frontend
bun run test src/components/finetuning/training-monitor.test.tsx
bun run typecheck
bun run check:all
```

Observed 2026-10-09:

- Backend: 71 passed initially; the new journey assertion incorrectly expected
  source_at on the compact operation summary. Corrected it to compare the actual
  last_progress_at field against worker source time; the focused retest passed.
  Logs: `/tmp/training-startup-backend.log` and
  `/tmp/training-startup-backend-retest.log`. An earlier collection command named
  two nonexistent test files and ran no tests.
- Cross-provider transition check: 25 passed, two real Torch/Transformers decoder
  tests skipped because their dependencies are unavailable locally. Explicit
  BT_STAGE training emission prevents Baseten's log reader retaining a startup
  stage after on_train_begin. Log: `/tmp/training-startup-transition.log`.
- Frontend: 17 passed. Typecheck, design, contrast, controls, scoped Ruff/Biome
  and all applicable pre-commit hooks passed. The Impeccable detector returned
  no findings. Logs use `/tmp/training-startup-*`.
- Browser: `/tests/training-transfer.html` explicitly labels its data synthetic.
  Verified loading clocks, missing telemetry, adapter stages and preserved
  transfer counters at 1280px desktop and 390px mobile widths. No overflow or
  fabricated loading percentage. Restarted only the frontend to clear a stale
  Vite import after renaming the shared component; restored the viewport and
  closed the temporary verification tab.
- Deployed immutable worker `overmind-sft-0eec6ae37e2b7f7c33dd6e7b` to
  `overmind-dev`. The sandboxed attempt could not establish a connection and was
  interrupted; the scoped network-enabled deployment succeeded in 17.660s.
  Deployment log: `/tmp/training-startup-deploy-network.log`.
- Final worker after the training-start transition fix:
  `overmind-sft-3ad8272ba179499acbd0e91d`, deployed in 5.811s in the same environment.
  Log: `/tmp/training-startup-deploy-final.log`. No training jobs were launched or
  restarted during either deployment.

## Limits

Weight loading inside the model library has no measured shard/byte callback in
this implementation. It reports the actual stage, its age, forward-progress age
and independent worker-heartbeat age, not a fabricated percentage. Row counters
measure dataset construction only. The committer is not proof that model loading
or optimisation has advanced. Newly launched jobs pin the instrumented release;
existing pinned jobs retain their original worker and cannot gain absent facts.
This verification did not launch a fresh GPU training run.

No schema, migration, Celery routing, demo seed, SDK or tool-catalogue changes
were needed. Existing progress JSON carries the added facts; no generated API
client edits were necessary. User documentation was updated in
`../docs/models/training.mdx`. Benchmark routing and evaluator compilation from
the preceding investigation were not changed by this progress fix.
