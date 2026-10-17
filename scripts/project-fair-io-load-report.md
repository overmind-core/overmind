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
The API upload metadata fix and interactive admission fixes were deployed in this pass; task counts and resource sizing were preserved.

Per-project admission prevents one project monopolising a tested pool; finite
shared CPU, database and storage still produce contention. Very large broker
backlogs, landing reservation, production-equivalent storage load and multi-project
saturation remain unverified. AWS CPU, memory, worker count or database expansion
requires the user's approval before applying it.

## Production verification

Production account access is confirmed as account 318651457362 with Tyler's
existing SSO role. The separate `overmind-prod-sso` profile selects this account
explicitly; the older browser-login profile had selected the wrong account.
Following the user's instruction to reuse projects, all synthetic tests use
`empty` (52d7ef2e-c092-4970-ad78-8cccfb6054d7) and `financial-services`
(bb512bfa-5c3b-4f2d-9865-601e0b1d0f04). Existing customer datasets were not changed.

### Deployed and verified

- API revision `api:221`, digest
  `sha256:8d958a824ca8ba396472c1e0cc2f6515d07d51c2319b99c813913baf03afe4b7`.
  Two desired/running tasks, each four CPUs/eight GiB. Rollout completed and
  `/health` returned healthy.
- Interactive revision `celery-interactive-worker:17`, digest
  `sha256:2850920f10bd1d88e4546b121d92566354e51fe02399c3de2d20b3256f272909`.
  Six desired/running tasks, each four CPUs/eight GiB. Rollout completed. The
  image retains the Redis admission retry and per-project concurrency of two.
- Both initial 65-byte uploads acknowledged all bytes but publication failed with
  `transfer_incomplete`; identical retries later succeeded without duplication.
  Two missed `Path.stat` calls now use the existing EFS-aware `stored_stat` helper.
  A REST regression simulates stale filesystem attribute metadata without changing
  real open/fstat reads. It fails before the fix and passes afterward, including
  exact two-row landing and publication idempotency. All 18 transfer tests pass;
  full backend: 3,269 passed, seven skipped. All 24 targeted Workshop, stream and
  scoring/load journeys pass, including isolated mixed/recovery child workloads.
- Six fresh production 65-byte transfers succeeded on their first attempt across
  both projects. Additional 8,919,989-byte and 71,423,141-byte uploads succeeded
  in 28.74 and 82.72 seconds respectively, with exact retained SHA-256 and row
  counts. The combined synthetic source held 73,736 rows afterward.
- Four noisy-project pipelines were submitted before a quiet-project pipeline.
  The noisy jobs ran in pairs; the second pair waited 8.28/8.24 seconds. The quiet
  job waited 105 ms and executed in 1.777 seconds while the first noisy pair was
  still executing. All five completed with retained output cells.
- Cancelling a running synthetic pipeline prevented publication. Repeating its
  exact request returned the same cancelled receipt and no output; this is not
  evidence of remote subprocess termination or production worker-loss recovery.

### Read latency and scope

Twelve initial unloaded MCP count queries measured median 276 ms and maximum
365 ms. A 30-second test with four noisy clients and one quiet client made 182
queries without errors. Quiet p50/p95/max were 295/828/921 ms. A subsequent
unloaded 20-query sample had a 2,846 ms outlier, so the earlier difference cannot
be attributed confidently to cross-project interference. The end-to-end native
MCP measurements include client/transport overhead, not only server execution.

During the 8.9 MB upload, 20 quiet queries had median 263 ms and maximum 1,344 ms.
During the 71.4 MB upload, 40 quiet queries had median 279 ms and p95/max 398 ms,
with no errors. API CPU samples during these probes were low; this was not a
production saturation test. These results do not certify the local 32-client
profile as a production limit or establish zero slowdown at arbitrary load.

### Remaining shared landing bottleneck

Production has one landing task with one execution slot. A further 8.9 MB import
into the larger noisy dataset executed from 00:14:13.682 to 00:14:26.315 UTC.
The quiet project's tiny import was published at 00:14:24.087 but did not start
until 00:14:26.465, after the noisy import finished: 2.378 seconds of queue wait
for 0.368 seconds of actual work. Its complete transfer/landing took 9.343 seconds.
The noisy source now holds 81,928 rows; the synthetic quiet source holds four.

This confirms landing is not isolated across projects. One non-preemptible slot
cannot reserve capacity for a second project. The next change needs per-project
landing admission and at least two execution slots, followed by repeated mixed
imports and a sustained production soak. Increasing AWS worker resources requires
user approval first. No resource expansion was made. Production fault injection,
large broker backlogs and saturation limits remain unverified; GPU cold start
remains excluded. The retained 128 MiB probe configuration was corrected to the
existing sandbox's 1 GiB minimum without adding AWS resources.

Raw production evidence lives beside the local reports in
`scripts/load-results/2026-10-16/production-*.json`.

## Simultaneous training and durable landing verification

Latest local verification on 2026-10-17 UTC (2026-10-16 Pacific). The preceding
production observations remain the pre-rollout baseline.

Source landing now admits durable database receipts before publishing bounded
broker work. It admits one import per project, chooses the least recently served
eligible project, and uses observed worker counts up to the existing ceiling of
eight. A burst of 1,001 jobs does not become 1,001 landing broker messages. Queue
waiting does not consume an execution attempt or expire unless explicitly
configured. Started worker loss fences publication and requires an explicit retry
from retained source bytes. Dataset-run MCP progress and REST dataset operations
expose queue, execution and terminal admission timestamps.

Scaling demand counts distinct projects that can use another worker; waiting
behind the same project's active import does not trigger wasteful scale-out. The
raw queue age remains available, alongside the new useful scaling-age signal.
The cloud change raises only the approved landing warm floor from one to two;
the ceiling remains eight and automatic scale-in remains suspended.

Profiling no longer serialises every column value after retaining its two example
values. Ordinary text avoids exception-driven JSON decoding. A 10,000-row sample
profile decreased from 482 ms to 338 ms with identical output except measurement
time. This is a microbenchmark, not a measured platform-wide speed-up.

### Passing local mixed profile

The five-minute profile uses 32 competing clients, 65,536-row uploads, two landing
slots and four interactive prefork slots. Landing and interactive execution run
in separate Linux prefork processes. Other worker profiles use threads. Only the
external GPU/model boundaries are faked; HTTP/MCP, PostgreSQL, Redis, preparation,
training dispatch/monitoring/provider completion, trace and evaluation scoring,
imports, retained-package pipelines and inference receipts are real.

- 52,036 competing requests; no recorded request or job errors.
- 11 training executions reached their provider-completion boundary; subsequent
  model-serving startup is excluded.
- 10 large imports, 689,872,100 uploaded bytes.
- 1,203 quiet reads: p50 128 ms, p95 304 ms, p99 398 ms.
- Slowest small pipeline: 2.233 seconds.
- 233 recorded instants had training, imports, pipelines, trace scoring, inference
  and quiet reads active together. Pairwise overlap alone no longer passes.
- The test passed in 349.20 seconds with the original 500 ms p95, one-second p99
  and ten-second small-pipeline budgets unchanged.

A preceding run shared the local Docker VM with unrelated regression suites and
large-file recovery. It failed the small-pipeline budget at 11.835 seconds. The
isolated repeat above passes; both results are retained rather than discarding
the failure. An earlier harness incorrectly ran CPU-heavy landing in the API's
interpreter; landing now matches production's separate prefork process.

The final backend CI profile passed: 3,297 tests, seven skipped, 193.02 seconds.
The full migration chain, including 0047_landing_admission, applied successfully to
a fresh local PostgreSQL database. Required pre-commit checks passed.

### Full-size source and recovery

A read-only local Parquet source contains 2,500,000 rows and 859,750,067 bytes.
Three of four serial noisy-project imports published before the original
30-minute test budget expired. Independent checks verified their row counts,
retained original SHA-256 hashes and first/last data values. The harness now uses
a one-hour full-size budget and retains read timings even on failure.

The fourth import's database and media were preserved before teardown. Its first
explicit recovery coincided with other local suites; Docker recorded an OOM kill
in the shared 7.65 GiB Linux VM. The standalone final recovery passed on its third attempt. All four published
imports contain 2,500,000 rows, and independent checks verified retained-file
SHA-256 and first/last data values. During the final recovery, a protected-project
import finished in 1.650 seconds; 603 protected reads measured 22.8 ms p95 and
106.3 ms maximum, with no request failures. The local VM OOM is retained as a
failed control, not used as a production capacity measurement.

### Deployment status

The admission implementation and tests are committed on the feature branch. The
new images are built and uploaded; production deployment is held pending the
remaining journey checks. The current production landing
service still has one task. No additional AWS resources have yet been applied.

Artifacts: `large-source-recovery.json`, `large-source-integrity.json`,
`mixed-training-32-simultaneous.json` and
`mixed-training-overlapping-test-suites-control.json` in the adjacent results
folder. These measurements do not certify 1,000 simultaneously executing jobs or
unchanged latency at arbitrary request rates. Durable intake, active execution
capacity and HTTP request concurrency are distinct limits.

### CI topology correction

The first full CI journey run passed 89 cases and failed two latency gates:
32-client reads at 527 ms p95 and burst-intake protected reads at 593 ms p95.
Both used an ASGI thread inside the load generator's interpreter, sharing its
Python execution lock. Read and intake benchmarks now use two separate API
processes, matching the hosted Dockerfile worker count, with the same 32 clients
and unchanged 500 ms p95 / one-second p99 budgets. Test database cache prefixes
are distinct, and child sessions clear only dedicated local brokers 12 or 13;
parent journey broker 15 and cache 14 are reserved.

The corrected read/intake cases both passed in 45.51 seconds. The parent wrappers
then passed in 178.88 seconds, including all six mixed, isolation, recovery,
transfer, landing and burst cases and a live check that the parent worker still
responded. The 32-client read rerun measured 46 ms p95. Current full CI is being
rerun; deployment remains on hold until that gate passes. Failed CI evidence is
retained in `ci-shared-interpreter-latency-control.json`; passing artifacts are
`read-load-separated-api.json` and `landing-burst-separated-api.json`.

The updated affected journey suite passed all 30 cases in 255.49 seconds before
this harness correction. No application code changed in the correction.

Production read-only SQL capacity check found API compute pressure before disk
pressure. At 32/64/128 clients, protected-read p95 was 298/572/910 ms; API CPU
peaked at 92.2%, while database CPU peaked at 29.4%. These are measured limits,
not proof of thousands of simultaneous executions. No AWS resources were added.

MCP SQL now reserves global and project capacity atomically in Redis before
creating DuckDB engines (defaults eight global, two per project). Busy and
unavailable admission return retryable errors containing the exact cell and a
minimum retry delay. Shared expiring leases recover dead workers; active leases
renew, query timeouts release capacity, and failed admission does not execute SQL.
The MCP interface declares deployed budgets. Essential guidance and bundled skills
require backoff and direct bulk analysis to retained pipelines.

The real two-process API test occupied both noisy-project slots with expensive
SQL, then completed another project's read in 80 ms. A third heavy query filled
the deliberately configured global ceiling of three; another project received
explicit backpressure. All three expensive queries timed out, subsequent reads
succeeded, and every source retained exactly one cell. The global-capacity probe initially raced its own inspection request for the
last free slot. Heavy-query clients now obey the declared retry delay on busy
admission; the test uses production's ten-second execution deadline.
The 56 affected MCP contracts also passed. Full backend and journey gates are
being rerun before deploying these additional SQL safeguards.

The unchanged application tree passed 3,298 backend tests (seven skipped) in
165.76 seconds. A preceding run overlapped an exception rename and is not release
evidence. Formatting and pre-commit checks passed.
