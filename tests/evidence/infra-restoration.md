# Infrastructure reconciliation — 2026-10-15

Base: current `origin/main` at `ff093cc0f50ded2f4ce96030e8908254f9ecaa43`.
Integration branch: `codex/rl-continuous-optimisation`, PR #198.
This supersedes the dedicated-controller infrastructure recommendation in
`merge-main-readiness.md`; its historical test receipts remain unchanged.

## Retained from main

| Area       | Result                                                                                                                                                                                                                                           |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Hosting    | Existing Fargate services and isolated sandbox; no dedicated EC2 host, Docker socket or singleton Workshop controller. Cloud-platform PR #11 is no longer a dependency.                                                                          |
| Workers    | Five worker profiles, six queues, original concurrency and prefetch settings. Landing remains separate. Packages run on interactive workers, with four shared Redis slots per project by default.                                                |
| Scaling    | Durable pipeline waiting/running receipts contribute to interactive capacity. Evaluation remains batch demand. Task-role metrics, fresh-worker registration checks and consumer-first rollout remain.                                            |
| Isolation  | Per-run UIDs, private scratch, capability dropping, no network egress, no app credentials or shared media, process/descendant cleanup, safe output claiming and bounded logs.                                                                    |
| Resources  | Main's 5.5 input-expansion factor, 256 MiB baseline, minimum 1 GiB reservation, 60% admission budget and RSS/scratch/unlinked-file accounting. Restored libraries and offline tokenizer caches.                                                  |
| Deployment | Latest Terraform task-family revision remains authoritative for roles, secrets, storage and environment. Sandbox, landing and interactive readiness precede producers; stopped services require their recorded desired count.                    |
| Migrations | New migrations still require fresh numbers. Only the exact checksum-pinned histories already applied before integration retain colliding identities. Merging a branch alone grants no exemption. No schema or historical migration file changed. |

## Testing fixes and product contracts retained

The prior integration and Workshop/decision-training chats established the
following contracts; this infrastructure reconciliation preserves them:

- Immutable packages/revisions, source fingerprints, lineage, branching/fan-in,
  bounded independent-row batches, previews, atomic publication and stable
  request-key conflicts.
- Exact large integers, Unicode, nulls, duplicate observations and original source
  bytes; OCR/native-text recovery, extraction provenance and streamed progress.
- Explicit recovery of source imports, provider submissions and comparisons.
  Unknown outcomes are not resubmitted automatically.
- Pinned Modal runtime releases/environments, checkpoint reload verification,
  startup/transfer telemetry, renewable collector leases and recorded GPU usage.
- The tested native decision `FinetuningJob` lifecycle, with shared preparation,
  monitoring, cancellation and recovery. Its probability-serving runtime remains
  distinct from chat serving. Restoring hosting does not restore the retired
  separate `DecisionJob` product lifecycle. Its historical evidence stays archived,
  as explicitly approved in the earlier integration chat.

## Package adapter and operating limits

Interactive workers stream retained package files, parameters and source JSONL
to the existing sandbox. Only its unprivileged child executes the package.
The child receives three paths: input JSONL, output JSONL and parameters JSON.
The service reports its observed process exit code; a script-authored status file
cannot certify success. Publication still validates the complete output chain.

Packages now pin a SHA-256 identity of sandbox source and the dependency lockfile.
Old Docker-pinned revisions remain readable and are never silently rebound.
Executing their code on the restored runtime requires saving a new revision.

Main's admission estimate is applied conservatively to streamed input bytes;
representative production concurrency/load qualification remains separate from
functional and isolation testing. Explicit memory caps below the estimate fail
before execution. `batch_rows` is only appropriate for independent rows.

Queued runs expire after 15 minutes. Pipeline tasks have a 25-minute soft and
26-minute hard limit; individual package steps retain their declared limit up to
1,200 seconds. A lost worker's lease expires without replay. Cancellation fences
publication; it does not claim that a disconnected remote process has stopped.
Runtime availability heartbeats continue independently of measured run progress.
Receipts retain queue duration, runtime identity, execution time, sampled resource
usage and exit status; missing samples remain unmeasured.

The one-time schema cutover still requires drained consumers and a restorable
database/media backup. Detection uses the currently deployed release marker,
not a service's desired count. No production infrastructure, migration, model
release or routing was changed by this work.

## Verification

- Full backend run: 3,228 passed, 7 skipped, one stale expectation that package
  execution bypasses Celery. The corrected test requires exactly one Celery
  dispatch; its complete file rerun passed all 12 tests. There are no unresolved
  failures from this run.
- Real sandbox-container tests cover nested imports, restored dependencies and
  offline tokenizer caches, output-file attacks, forged success, separate UIDs,
  credential/network isolation, descendant cleanup, memory/scratch limits,
  runtime identity and 10,000/100,000-row packages. Redis tests exercise project
  admission and stale-slot ownership. These are included in the backend run.
- Migration consistency: no model changes detected; 33 new migrations checked,
  with 11 exact previously applied identities retained. No historical migration
  bytes or schema changed in this infrastructure reconciliation.
- The rebuilt application/sandbox Docker image completed successfully. Companion
  documentation passed Mintlify's broken-link check using Node 22.
- Repository-wide Ruff lint/format checks and pre-commit on the changed files
  passed. The local services were refreshed with the existing PostgreSQL/media
  volumes. Sandbox health and runtime identity matched; landing and interactive
  worker registration checks passed before resuming the scheduler.
- The live CLI/MCP pipeline smoke test was explicitly skipped at the user's
  request. No dataset was created for that test. Frontend code was unchanged;
  its earlier local results remain historical.
- Clean SDK CI exposed a missing `openai` test dependency in the existing span
  integration test. It is now declared in the development group and lockfile,
  without adding a runtime dependency. SDK lint passed and all 600 SDK tests
  passed locally. The already-bumped SDK version remains 0.1.92, above main's
  0.1.90.
- Frontend CI passed lint, typechecking and design guardrails, with 873 tests
  passing and two stale colour assertions. Both selectors now inspect the
  rendered bar inside the button; the UI is unchanged. All five tests in the
  affected file passed on rerun, and frontend typechecking passed again.

Logs are in `/tmp/infra-restore-*`. This is functional and isolation evidence,
not a representative production load qualification.
