# Project fairness and production concurrency verification

Current verification record for 2026-10-17 UTC. The earlier
[load report](project-fair-io-load-report.md) retains local experiments and failed
controls. This record distinguishes deployed changes, measured passing workloads
and remaining verification; it does not certify unlimited concurrency.

## Changes and purpose

| Change                                                                                         | User-facing reason                                                                                                                                                     |
| ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dedicated live-scoring worker/queue and bounded, fair evaluation admission                     | Imports and batch work cannot occupy the same execution pool as live scoring.                                                                                          |
| Durable source admission before broker publication; one running import per project             | A large burst becomes readable queued work rather than flooding the broker or consuming every landing slot.                                                            |
| Shared per-project Workshop admission                                                          | A project cannot monopolise interactive execution; other projects retain capacity.                                                                                     |
| Atomic global/per-project SQL admission, retry guidance and execution deadlines                | Expensive exploration is bounded before DuckDB engines are created; busy clients back off and timed-out queries release their slots.                                   |
| Fenced worker-loss recovery and explicit retained-source retries                               | Interrupted work cannot silently replay scripts or publish an abandoned attempt.                                                                                       |
| Landing publication leaves the admission row unlocked                                          | Long publication no longer blocks its own heartbeat and expires its ownership lease. Dataset locks still fence recovery.                                               |
| First-source receipts for fresh empty drafts and MCP recovery of retained failed draft uploads | Uploading into a draft keeps the original brief and gains the same recoverability as creating a dataset with a source. Cancelled-source replacement remains supported. |
| Fresh shared-filesystem metadata, bounded profiling and resumable chunk transport              | Acknowledged uploads survive metadata caching and transient connection loss without duplicate publication or loss of source values.                                    |
| CLI landing failures retain the published transfer/dataset receipt                             | Agents can inspect and recover an uploaded source instead of losing its identity when `--wait` fails.                                                                  |
| Useful competing-project queue-age metrics; approved second landing task                       | Scaling measures work another worker can execute. Only the approved landing warm capacity increase was applied.                                                        |
| Stable evaluation table renderers                                                              | Refreshes preserve keyboard focus instead of remounting cells.                                                                                                         |

## Passing production measurements

All request profiles use two authorised projects: a noisy project and a protected
project. They use production HTTPS MCP, respect retry delays, and stop on a
protected read exceeding two seconds. GPU cold start is excluded from inference
latency claims.

| Profile                                     | Protected read p95 |    p99 | Accepted noisy reads | Noisy backpressure responses | Protected backpressure | Result                             |
| ------------------------------------------- | -----------------: | -----: | -------------------: | ---------------------------: | ---------------------: | ---------------------------------- |
| 64 clients, 60 seconds                      |             490 ms | 755 ms |                1,905 |                        1,828 |                      0 | Passed                             |
| 128 clients, 60 seconds, isolated generator |             669 ms | 937 ms |                1,664 |                        4,383 |                      0 | Passed                             |
| 32 clients with two expensive SQL producers |             510 ms | 854 ms |                  246 |                        1,154 |                      0 | Passed; 11 expected query timeouts |

Final-release repeats also passed:

| Profile                                                                              | Protected p95 |    p99 |  Maximum | Accepted noisy reads | Noisy backpressure | Protected backpressure |
| ------------------------------------------------------------------------------------ | ------------: | -----: | -------: | -------------------: | -----------------: | ---------------------: |
| Mixed 64 clients during measured training, imports, pipelines, scoring and inference |        453 ms | 859 ms |   886 ms |                2,056 |              1,835 |                      0 |
| 128 clients                                                                          |        692 ms | 980 ms | 1,284 ms |                1,676 |              4,429 |                      0 |
| 32 clients with two expensive SQL producers                                          |        419 ms | 556 ms | 1,415 ms |                   83 |              1,220 |                      0 |

The expensive-query repeat observed 12 expected timeouts and no errors. All 140
spans from the mixed overlap were scored, including eight model-backed checks.
All 19 inference requests from that run completed with finite normalized typed
probabilities. The protected pipeline completed in 2.076 seconds while the large
pipeline ran for 115.940 seconds; the source import remained active and recorded
training steps advanced from 93 through 120 around the protected operation. The
large pipeline preserved all 81,928 rows.

The passing expensive-query run recorded client event-loop delay p99 of 21 ms.
Earlier controls stopped on protected latency tails or a transport error and are
retained as failures, not counted as passing. In the failed expensive-query
control, server logs recorded six timeout-scale queries; other query operations
had a maximum recorded service time of 171 ms. This does not prove the cause of
the client-side tail or exclude authentication/network time.

Separate real production workflow checks completed:

- Two native decision training runs, each 100 optimiser steps, succeeded and
  reached ready deployments. They did not change the active model or benchmark.
- An 81,928-row pipeline completed in 149 seconds while another project's small
  pipeline completed in 2.208 seconds, with 0.104 seconds of queue waiting.
- All 264 synthetic spans across both projects received deterministic scores.
- Eight durable native inference requests succeeded with finite two-option
  probability vectors summing to one.
- A protected two-row import completed with 0.106 seconds of queue waiting and
  0.307 seconds of server execution.

## Regression gates

- Clean backend run: **3,300 passed, seven skipped**, 357.94 seconds.
- SDK: **630 passed**, including lost acknowledgements, bounded retries, confirmed
  permission denial, and preserving a published receipt after landing failure.
- Focused source/MCP/cancellation recovery checks: **75 passed**.
- The heartbeat regression fails on the original code with a database lock
  timeout and passes with independent heartbeat renewal and expiry checks.
- Exact source commit `fec55182fc5ea3e42120071484fd5701820a1e9e`: all CI checks
  passed, including backend, journeys, frontend, lint, migration checks and SDK
  compatibility matrix. Required pre-commit checks pass.
- Exact deployed diagnostic source `43ac80774e4f879b90df8ec43d78a726ccf7de83`: all
  37 reported CI checks completed successfully or were intentionally skipped.
  Its targeted MCP query/capacity suite passed all 21 tests.
- Backend images were checked against all **479** source files, with no missing,
  changed or extra backend source files.

One earlier full run used a stale local sandbox image and failed runtime identity
validation. The matching image passed the affected cases. A subsequent full run
caught two cancelled-source replacement regressions; those were fixed before the
clean full run above. Neither failed run is release evidence.

## Deployment and final overlap

The heartbeat correction is deployed. Source-recovery release image:
`sha256:6b1c5ad8fa44b13701ee075ebf3a17bbbdaf6970697b33789601fd973bb8146c`.
The landing-worker/API rollout completed, preserving two desired tasks for each
service. API revision 227 additionally runs source-verified slow-query stage
diagnostics (`43ac8077`) without recording SQL text or row values. All other service sizes and database/storage settings are
unchanged. Matching native training, decision-serving and decision-evaluation
Modal runtimes were deployed for the current data-format identity. Final checks
found all eight application services stable at their desired counts, no pending
tasks, and both API load-balancer targets healthy.

The retained 859,750,067-byte, 2.5-million-row production source read all rows but
failed publication on the old heartbeat lock. Its first MCP recovery attempt then
exposed the missing draft receipt. The old legacy failure path had also deleted
that temporary upload, so the original attempt could not be resumed. Both the
heartbeat and fresh-draft recovery defects are fixed and tested. An unchanged
local copy was uploaded again with a new stable transfer request. It completed on
its first attempt: queue waiting 0.118 seconds and server landing 894.782 seconds.
The retained original SHA-256 and all 859,750,067 bytes match; the published cell
has 2,500,000 rows, with exact first/last comparisons across 14 fields. This
verifies those edge samples and retained original identity, not a full comparison
of every published value. The simultaneous workload barrier also passed.

A later final mixed run stopped after 11 protected reads: p95/p99 was 2.850
seconds. Server query logs and ALB metrics confirm a real latency tail (maximum
recorded MCP query 3.126 seconds), rather than establishing a client-only cause.
Three slow successful queries finished together on one API task; the cause is
not yet identified. Stage diagnostics were deployed to distinguish lookup,
admission, SQL, release and response work. The subsequent final-release mixed 64-client, 128-client and expensive-query
profiles all passed their unchanged thresholds. The earlier spike remains
unexplained and was not reproduced; the diagnostics are retained. Passing repeats
do not prove that the earlier latency issue was fixed or that no request can ever
be delayed.

Additional verified receipts: a 200-step native training run succeeded; all 25
durable inference requests completed with valid normalized probability vectors;
all eight narrowly scoped model-backed trace checks returned scores of 1.0. The
third large pipeline preserved 81,928 rows, and the protected pipeline completed
in 1.680 seconds overall. These earlier facts alone did not establish overlap; the subsequent recorded
v4 barrier and receipts above supply that evidence.

## Capacity scope and persistence

These tests establish measured responsiveness for the listed profiles, not a
production maximum or a guarantee for 1,000 simultaneous executions. Active
execution, accepted queued jobs and concurrent HTTP clients are separate limits.
SQL defaults are eight global executions and two per project; landing admission
uses observed worker capacity with one execution per project and a ceiling of
eight. Workshop limits a project to two concurrent pipelines.

Existing autoscaling ceilings were preserved. Only the approved second warm
landing task was added. No database, storage, API, other worker or AWS capacity
increase was applied in this work.

Feature changes are pushed to [platform PR #210](https://github.com/overmind-core/overmind/pull/210),
with [cloud PR #13](https://github.com/overmind-core/cloud-platform/pull/13) and
[docs PR #75](https://github.com/overmind-core/docs/pull/75). They remain unmerged;
persistence in future standard releases and infrastructure applies still requires
merging those reviewed changes.

Aggregate production evidence is in [load-results/2026-10-17](load-results/2026-10-17/).
No customer rows, credentials or private inference payloads are included.
