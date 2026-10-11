# Operational progress implementation and verification

Scope: shared durable operational receipts for preparation, training, deployment,
activation and MCP inference; provider event drill-down; passive project-scoped
inspection. No UI redesign or platform planning agent. MCP impact: MCP-ready.

Failure cases to cover before implementation:

- Client disconnect or provider acknowledgement loss repeats paid inference.
- A stale provider event overwrites newer state or resurrects terminal work.
- Heartbeat/observation timestamps falsely establish forward progress.
- Polling wakes workers, exposes another project's operation or tenant adapter.
- Bounded summaries silently discard events or hide unavailable telemetry.
- Restarts lose event history, result receipts or model-routing identity.
- Concurrent requests reuse a key with different inputs or dispatch twice.
- Completion is confused with output validity, routing switch or application use.

Verification will distinguish local transport/provider-boundary tests from real
provider execution. Live cold-start qualification requires deployed worker code;
passing local tests alone does not establish that qualification.

## Implemented surface

- `OperationalRun` binds project, domain reference and attempt; append-only
  `OperationalEvent` rows retain source and observation timestamps.
- Preparation/training controllers copy their existing factual observations;
  deployment/activation/inference controllers additionally collect Modal call
  graphs and worker journals. No platform explanation agent or UI change.
- `inspect_operation` and `overmind://operations/{id}` are passive, paginated,
  project-scoped database reads. `get_job` links the current attempt.
- Worker facts cover volume refresh, weight validation, engine-process liveness,
  snapshot artifact preparation, measured snapshot-restore bytes/parameters,
  cache allocation, health checking, adapter loading and generation. Shared-pool
  observations never establish a particular request's readiness.
- Cursor checkpoints, predecessor journals and background backlog collection
  preserve observed history across collector/worker restarts. Missing telemetry,
  missing events, failed publications and dropped events remain explicit.
- `run_inference` now saves a durable request before dispatch. Same project key
  and inputs return the same receipt; changed inputs conflict. An uncertain
  provider acknowledgement is reconciled, never resubmitted. Expired observation
  remains unresolved and is checked every five minutes. Completed results can be
  read after reconnect and paged without another inference.
- This is MCP contract **4.0.0**, **62 tools**, catalogue fingerprint
  `f10e929da7db95e89a3a5c9e81f5ad3625c266d7b4f41ac44fef6bb6c3311c01`.
  Ordinary application completion HTTP/SSE contracts are unchanged.

## Reproducible verification

Environment: current local checkout; running Compose API, Postgres, Redis and
Celery; saved local MCP account connection; authorized project
`e18b29b5-915d-45a7-80cd-77ffe6559205`; Modal `overmind-dev` deployment. The replay
refuses non-local MCP hosts, discovers projects before selecting the explicit
project, and never prints credentials. No browser was used.

Provider deployments:

```sh
uv run --no-sync modal deploy overbae/modal/modal_vllm_worker.py --env overmind-dev
uv run --no-sync modal deploy overbae/modal/modal_sft_worker.py --env overmind-dev
uv run --no-sync modal deploy overbae/modal/modal_decision_evaluation.py --env overmind-dev
```

Deployments update code; they do not launch training/evaluation. Matching training
and decision-evaluation releases are required because their immutable identities
include the changed shared runtime package.
Final deployment commands succeeded for `overmind-inference`,
`overmind-sft-66bc1f229aae558da782ab9c` and
`overmind-decision-eval-3609bba4ab6de31ab716fd19` in `overmind-dev`.

Offline/transport regression command:

```sh
set -o pipefail
uv run --no-sync pytest tests/test_operational_progress.py tests/test_inference_receipts.py tests/test_mcp_*.py tests/test_shared_base_serving.py tests/test_model_activation.py tests/test_model_deployment_resume.py tests/test_training_telemetry.py tests/test_training_preparation.py tests/test_training_transfer.py tests/test_inference_routing.py tests/test_modal_stream_keepalive.py tests/test_celery_topology.py -q 2>&1 | tee /tmp/overmind-operational-final-suite-fixed.log
```

Observed **468 passed, 2 warnings in 20.65 seconds**. The two warnings identify
locally executed Modal upload functions with fixture volumes, not real provider
uploads. Analytics tests need permission to bind a loopback HTTP fixture. An
earlier sandboxed run had 461 passes, four stale catalogue assertions and two
loopback permission errors; the catalogue fixtures were updated and the affected
18 checks passed before the complete rerun.

After that run, a new preparation check reproduced exported rows appearing as
upload completion: **54 preparation/progress checks passed** after correction
(`/tmp/overmind-operational-upload-fixed.log`). A final disconnected-client case
reproduced a saved receipt being hidden by changed deployment readiness/context
and new credit requirements. Recovery now validates the original caller inputs
against the saved request before applying new-execution prerequisites; **94
inference/MCP/progress checks passed** after correction
(`/tmp/overmind-operational-receipt-replay-fixed.log`). These counts overlap and
must not be added together as distinct coverage.

Fault injection reproduced defects before fixes: uncollected predecessor events,
lost journal acknowledgement overwriting an event, stopped reconciliation after
an observation deadline, missing terminal usage recording, abandoned terminal
collection backlogs, stale cancellation state, recovered availability retaining
an unavailable reason, failed publications being labelled lost events, and
regressing counters falsely resetting the forward-progress clock. Tests exercise
these against persisted receipts, MCP transport or provider-boundary fixtures;
they are not claims of having killed real GPU workers.

### Live request one

```sh
uv run --no-sync python tests/evidence/operational_mcp_replay.py submit
uv run --no-sync python tests/evidence/operational_mcp_replay.py read d627623b-da84-486b-a46d-68f6dbef44c5
uv run --no-sync python tests/evidence/operational_mcp_replay.py events b3ec9400-0b17-405e-a294-39a16230034b --limit 5
```

The fixed default request key prevents a repeat submission from creating another
provider call. Inputs: existing `ft-2a04ad10-qwen3-5-0-8b` deployment, user message
`Reply exactly READY.`, temperature 0, maximum 16 output tokens.

- Request `d627623b-da84-486b-a46d-68f6dbef44c5`; provider
  `fc-01M4FBZRKAQB6RQCK21QQ6GCVW`.
- Created 2026-10-09 03:40:49.200993 UTC; completed 03:45:38.732998 UTC:
  **289.532 seconds** until the local completed receipt.
- Returned `READY.`, stop finish, 16 prompt and 3 completion tokens, no clipping
  or truncation. Provider-wrapper latency **252.648 seconds**.
- Repeating submission after a new MCP session returned the same UUID. Reading
  after disconnect recovered the answer and **23 events**.
- Logs: `/tmp/overmind-operational-live-submit.log`,
  `/tmp/overmind-operational-live-reconnect.log`,
  `/tmp/overmind-operational-live-final.log`.

### Live request two

```sh
uv run --no-sync python tests/evidence/operational_mcp_replay.py submit --request-key operational-receipt-20261008-final-02
uv run --no-sync python tests/evidence/operational_mcp_replay.py read e83bb840-e03f-4e5b-9f17-782b6a26b889
uv run --no-sync python tests/evidence/operational_mcp_replay.py events 6f923254-289c-411f-abf2-f91c7e28246c --limit 5
```

Same bounded prompt/model, separate explicit test key. Receipt returned in about
0.4 seconds for the complete replay process. Provider call
`fc-01M4FCJHAC4C9TZVNMH1TAPWNB`. During the cold start, live MCP observations showed
`waiting_for_engine`, measured elapsed time and regular `process_alive` heartbeats;
request forward-progress time correctly remained unchanged.

- Completed successfully at 2026-10-09 03:55:42.968667 UTC, **276.050 seconds**
  after creation. Provider-wrapper latency **248.044 seconds**.
- Returned `READY.`, stop finish, 16 prompt and 3 completion tokens, no clipping
  or truncation. Saved **45 events**; all collected cursors had no remaining
  backlog or reported history gaps. Some events are explicitly older shared-pool
  context, not work done for this request.
- Paging five events returned sequences 1–5/cursor 5, then 6–10/cursor 10.
- Replaying the same completed key after the final backend corrections returned
  the same succeeded receipt without another provider submission.
- Raw MCP results are retained in `operational-progress-live-results.json`.
  The last publication-counter refinement was deployed after this live call;
  that failure/retry distinction is qualified by the injected-acknowledgement
  tests, not a claim that the live call experienced a publication failure.

## Product assessment and remaining limits

The improvement is recoverability and honest visibility, not a claimed cold-start
speedup. The first request still took nearly five minutes. Its completed provider
event preceded local completion by about 28 seconds. These timings are individual
observations, not an SLA or percentile estimate.

Fresh vLLM initialization still lacks byte/shard-level loading and compilation
counters. It exposes checked process liveness, not a fabricated percentage.
Snapshot restore has deeper measured byte/parameter progress. Preparation and
training reuse existing producer facts; this change does not reconstruct stages
that those producers never emitted or retroactively instrument old runs.

Provider journals are a best-effort transport into the durable database, not a
promise that every event survives permanent provider loss. Missing/capped history
is explicit. Collector retries after terminal work are bounded. Historical
call-graph nodes retain their own observation timestamp and are not a claim that
an older pending state overrides a later completed result.

Two small inference requests were authorized under the existing $100 test budget.
No new training, evaluation, activation or routing change was launched here.
Deployment metrics reported $0.065535 estimated cumulative inference spend at an
intermediate observation; that includes prior requests and excludes unreported
provider components, so it is not an all-in invoice for this turn.

No UI redesign, raw provider log access, new service-stop/budget-control tool,
semantic-quality qualification or product-delight score is claimed. The separate
capability gap report remains relevant. A complete production qualification still
needs real worker termination, prolonged provider telemetry outage and a full
preparation-to-training-to-activation journey on this exact deployed revision.

Migrations 0028–0030 applied successfully to local Postgres. Model drift check:
no changes detected. `make check-migrations` still fails its branch comparison
because pre-existing 0013–0015 migration numbers are not above origin/main's
0015\. This is not repaired by renaming historical migrations in a dirty checkout.
No commit, push, separate worktree or browser changes were made.
Pre-commit checks passed for the changed implementation files. REST/API-client
generation and frontend verification were not run: no REST contract or UI was
changed by this operational-visibility work.
