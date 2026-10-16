# Project fairness load verification

Measured on 2026-10-16. This is a bounded local integration benchmark, not a
production capacity certification or a zero-interference guarantee.

## Workload and acceptance

`tests/journeys/test_scoring_load.py` runs the benchmark in an isolated subprocess
and test database, then verifies that the main journey worker still processes a
control task. `tests/load/scoring.py` runs two Celery worker instances with two
threads each, real PostgreSQL/Redis admission, and authenticated HTTP REST and MCP
reads against the journey ASGI server. A competing project holds both of its
allowed slots while its durable backlog grows from 100 to 10,000 records. A quiet
project submits 30 sequential requests in each phase, including the unloaded
baseline. This measures latency isolation, not maximum quiet-project throughput.

Two competing workloads are measured: slow-provider waits and sustained local
scratch-file writes, fsync/read verification and database reads. The latter run
wrote 519 MiB and performed 519 database reads. Provider scoring is substituted to
control its duration; this does not exercise real judge/provider throttling.

The gate is quiet scoring p95 below max(500 ms, twice baseline), scoring empirical
p99 below one second, and REST/MCP p95 below max(500 ms, twice their own baseline).
Timeouts and non-successful API/MCP responses fail the run. These are regression
budgets for this local harness, not public latency commitments. With 30 samples,
the reported empirical p99 is the maximum observed sample.

## Results

Rounded milliseconds, baseline -> 10,000 competing records:

| Competing workload           | Quiet scoring p95 | Quiet scoring p99 | REST p95 |    MCP p95 |
| ---------------------------- | ----------------: | ----------------: | -------: | ---------: |
| Slow provider                |          35 -> 28 |         112 -> 29 | 14 -> 12 | 109 -> 109 |
| Database and scratch storage |          23 -> 24 |          25 -> 29 | 14 -> 12 |  105 -> 95 |

Both workloads passed.

A separate authenticated REST read ramp uses eight competing projects and 40
quiet-project requests per phase. Quiet-project p95 was 19 ms alone, 24 ms with
eight competing sessions (257 competing requests), and 138 ms with 32 sessions
(1,307 competing requests). The maximum quiet sample at 32 sessions was 167 ms.
This passed the 500 ms absolute budget but shows about sevenfold relative
slowdown: the shared API is not independent of arbitrary concurrent read load.
It must not be presented as a zero-slowdown result or production capacity figure.

The earlier starvation test also leaves 9,998 records
waiting while the quiet project and an independent IO worker complete their work.
Admission tests cover 20 late-arriving projects, concurrent dispatchers, duplicate
delivery, lease expiry and broker failure.

A production observation during rollout found 16 admitted records across four
trace workers, with no project above two, independent IO consumers, and 8,401
completed records. It also exposed overlapping scheduler ticks occupying control
threads while waiting for the scheduler lock. Duplicate ticks now skip a locked
scheduler instead of waiting; a regression test holds a publication open and checks
that another tick immediately returns without publishing duplicate work.

## Reproduce

```sh
TEST_REDIS_URL=redis://127.0.0.1:6379/15 uv run pytest \
  tests/test_live_scoring_load.py \
  tests/test_live_scoring_admission.py \
  tests/journeys/test_scoring_load.py -q -s
```

Use the existing compose PostgreSQL and Redis services. Each phase writes a JSON
report under pytest's temporary directory and emits `LATENCY_REPORT` to the log.
The journey also retains the complete child output in its `.runs` artifacts.
Do not run alongside another journey process that flushes Redis DB 15.

## Limits

This does not establish the limit of production EFS, RDS, Redis or external provider
quotas. Local scratch-file IO is not an EFS benchmark. It does not certify landing,
Workshop execution, GPU dispatch or inference isolation. Those need separate
workload-specific tests and production-equivalent capacity measurements.

A flood from one project cannot occupy every scoring admission slot. Enough
independent projects can still fill the shared pool; new scoring work must then
wait for capacity. Per-project fairness prevents monopolisation, but cannot make
finite shared resources unlimited or guarantee unchanged latency at arbitrary
load. API/control responsiveness and admitted-job latency require separate budgets.
