# Project fairness and mixed workload verification

Measured on 2026-10-16. These are local regression measurements, not production
capacity certification or a guarantee of unchanged latency at arbitrary load.

## Current measured capacity

The mixed workload uses real HTTP, MCP, PostgreSQL, Redis, Celery, dataset landing,
retained-package sandbox execution, evaluation and trace scoring, CPU training
preparation and durable inference collection. Only external model/Modal boundaries
are faked. GPU cold start is excluded. Interactive execution runs in a separate
Linux prefork worker limited to four CPUs and eight GiB; other worker profiles use
threads. Local storage does not represent production EFS/RDS.

| Profile                                 | Quiet read p50 |    p95 |      p99 | Competing requests | Outcome               |
| --------------------------------------- | -------------: | -----: | -------: | -----------------: | --------------------- |
| 32 clients, 300 seconds, 64 MiB uploads |         137 ms | 372 ms |   620 ms |             46,286 | Passed                |
| 64 clients, 90 seconds, 64 MiB uploads  |         293 ms | 615 ms | 1,410 ms |             14,985 | Failed latency budget |

Both profiles completed their submitted jobs without recorded request/job errors.
The acceptance budget is quiet read p95 below max(500 ms, twice baseline), p99 below
one second, quiet small pipelines below ten seconds and no request/job failures.
The largest passing tested profile is 32 clients; this is not the maximum possible
throughput or a production limit. The five-minute run uploaded 482,910,470 bytes
across seven imports. Large transformations took 23.8–32.8 seconds.

A snapshot of 57 quiet imports during the 32-client run measured a maximum queue
wait of 8.243 seconds and elapsed time of 8.431 seconds. Production inspection
confirmed one landing worker with one execution slot, four CPUs and eight GiB.
Landing therefore remains a shared bottleneck requiring separate admission and
capacity verification; this result does not establish import isolation.

## Faults reproduced and fixes under verification

- Duplicate evaluation admission ticks waited on a scheduler row lock. They now
  skip a locked scheduler. A real PostgreSQL lock regression passes.
- One project occupied all four interactive slots. Reducing its default cap from
  four to two allowed a quiet pipeline to finish in 474 ms instead of 23.1 seconds
  in the original isolation experiment. The separate Linux prefork rerun passed: 771 ms with twelve noisy jobs.
- Redis connection failure during interactive admission escaped without retry.
  It now retries before claiming execution; the unavailable-port regression passes.
- A real prefork subprocess kill left its attempt fenced. Another project finished
  in 939 ms; lease expiry failed the lost attempt without output publication or
  same-key replay. The shared harness rerun passed: quiet work completed in 843 ms.

The interrupted upload test resends an acknowledged chunk offset and checks the
retained bytes by SHA-256. It exercises application-level resumption, not an actual
TCP failure. All eight targeted scoring/load journeys passed in 129 seconds, including four child mixed/isolation/recovery/transfer checks. The full backend suite passed: 3,268 passed and seven skipped in 170 seconds. The broader journey run passed 74 non-Console cases. After installing missing frontend dependencies, 16 Console cases passed and one exposed focus loss during evaluation refresh. A deterministic component regression reproduced cell remounting; stable renderers fix it. All 908 frontend tests, type checking, lint and design/contrast/control checks pass. All 17 Console journeys pass after the fix. Together with the 74 non-Console journeys, all 91 journey cases passed across the scoped runs.

## Reproduce and artifacts

See `tests/load/README.md` for exact short and extended workload commands and worker
profiles. The journey wrapper `tests/journeys/test_scoring_load.py` runs read-load
and mixed/recovery experiments in isolated subprocesses and databases. Dedicated
load tests use broker database 12, separate from the parent journey worker on 15.
Do not run competing consumers or tests that flush those databases simultaneously.

Retained raw reports in `scripts/load-results/2026-10-16/`:

- `mixed-soak-32-prefork.json`
- `mixed-soak-64-prefork.json`
- `quiet-landing-32-snapshot.json`

The previous synthetic scoring workload patched owned scoring functions and has
been replaced by real scoring journeys and mixed workloads. Its historical latency
figures are not evidence for the current implementation.

## Remaining limits

Production service health was checked after AWS authentication: API, control, IO,
trace, batch, interactive, landing and sandbox services matched desired counts,
with completed deployments. This alone does not certify production throughput.
No resource expansion or new deployment was made during this verification pass.

Per-project admission prevents one project monopolising a tested pool; finite
shared CPU, database and storage still produce contention. Very large broker
backlogs, landing reservation, production-equivalent storage load and multi-project
saturation remain unverified. AWS CPU, memory, worker count or database expansion
requires the user's approval before applying it.

## Live-test environment

Production account MCP access is connected. Following the user's instruction to
reuse existing projects, synthetic probe datasets were created in `empty`
(52d7ef2e-c092-4970-ad78-8cccfb6054d7) and `financial-services`
(bb512bfa-5c3b-4f2d-9865-601e0b1d0f04). Existing customer datasets were not changed.
Twelve unloaded quiet-project count queries measured median 276 ms and maximum
365 ms. These are baseline measurements, not concurrent capacity verification.

Both initial 65-byte uploads acknowledged every byte but failed publication with
`transfer_incomplete`. Repeating the identical request keys later succeeded
without duplication. The production transfer implementation used ordinary
`Path.stat` in two places despite the existing EFS-aware `stored_stat` helper.
Both calls now use that helper. A REST regression simulates stale filesystem
attribute metadata while leaving real open/fstat reads intact: it fails before
the fix and passes afterward, including exact two-row landing and publication
idempotency. All 18 transfer tests and the full backend suite pass (3,269 passed,
seven skipped). Production deployment and fresh-upload verification remain pending.

The retained two-row pipeline probes were rejected before execution because their
128 MiB limit was below the sandbox's 1 GiB admission minimum. This is a probe
configuration error, not a measured saturation event. Correct the retained recipe
before load testing. No significant concurrent production workload or resource
expansion has been applied. AWS deployment access expired during verification;
a refreshed production login is required to deploy the metadata fix.
