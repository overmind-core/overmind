# Capacity experiments

Run these serially against the local compose PostgreSQL and Redis services.
The tests use a separate database, broker database 12, real ASGI HTTP, Celery and
sandbox execution. Only external model providers and Modal SDK calls are faked.
The broker must not have another test consumer. Load sessions clear only their
dedicated local broker (12, or 13 for a separate experiment); cache 14 and parent
journey broker 15 are reserved. GPU startup is excluded.

The read-only and 1,001-request intake tests serve ASGI in two separate processes,
matching the hosted API worker count. The load generator has its own interpreter;
its client-side JSON work must not occupy the API's Python execution lock. Load
databases also have separate cache prefixes, including API and worker processes.
The provider-faked mixed test retains its in-process API so its external network
fakes remain effective. Reports identify the process topology; latency budgets
are unchanged.

```sh
TEST_REDIS_URL=redis://localhost:6379/12 \
OVERMIND_LOAD_TEST_DATABASE=test_overmind_capacity_manual \
SANDBOX_TEST_IMAGE=<image-with-the-current-sandbox-lock> \
uv run pytest tests/load/mixed.py tests/load/isolation.py tests/load/recovery.py \
  tests/load/transfers.py --ds=tests.load.settings -q -s
```

The journey wrapper runs the short regression profile in a child process so
Celery's process-wide test-worker shutdown flags cannot stop the main journey
worker. Long experiments are opt-in:

```sh
TEST_REDIS_URL=redis://localhost:6379/12 \
OVERMIND_LOAD_TEST_DATABASE=test_overmind_capacity_manual \
OVERMIND_SOAK_SECONDS=300 OVERMIND_SOAK_CLIENTS=32 OVERMIND_SOAK_ROWS=65536 \
OVERMIND_SOAK_REPORT=/tmp/overmind-capacity-32.json \
SANDBOX_TEST_IMAGE=<image-with-the-current-sandbox-lock> \
uv run pytest tests/load/mixed.py --ds=tests.load.settings -q -s
```

`mixed.py` combines eight competing reader projects, a noisy project doing
chunked CSV uploads and retained-package transformations, real evaluation
generation/scoring, trace ingestion/scoring, CPU training preparation and durable
inference collection. The quiet project reads and runs small pipelines. Reports
retain baseline and loaded latency, completed work, bytes, errors, process memory
and sampled database connections. The default landing profile has one slot, as
in the checked-in production configuration. Interactive execution uses a separate
prefork worker, limited to 4 vCPU and 8 GiB in the macOS Linux container. Landing
also runs in a separate prefork worker so CPU-heavy parsing does not share the
API interpreter. Other profiles use threads, so this does not certify their
prefork memory or hard limits.

Acceptance is quiet REST p95 below max(500 ms, twice baseline), empirical p99 below
one second, small pipelines below ten seconds, and no request/job failures. These
are local regression budgets, not published production SLOs. Inference completion
includes the real collector polling interval even though the provider is fake.

`isolation.py` submits twelve slow pipelines on separate datasets in one project.
With four interactive workers, its quiet neighbour must complete within five
seconds. `recovery.py` uses an actual prefork worker (Linux Docker on macOS), kills
a task subprocess and checks another project's completion, lost-attempt fencing,
source retention and same-key non-replay. It explicitly advances the persisted
deadline to test expiry without waiting the full production lease duration.
`transfers.py` resumes an interrupted chunk sequence, resends an acknowledged
offset and verifies complete retained bytes by SHA-256.

Do not infer production EFS/RDS capacity from local scratch storage. Resource
sizes, provider quotas, connection limits and production backlog require separate
measurements. Per-project admission prevents monopolising a pool; it does not
make CPU, storage or database resources independent at arbitrary load. AWS CPU,
memory, worker count or database expansion requires the user's prior approval.

### Retained local data

The landing isolation test can use an existing local Parquet file rather than
synthetic CSV rows. It streams upload chunks and checks the published row count
against the retained Parquet metadata. The original file is read-only; all imports
use temporary test projects and media storage.

```sh
OVERMIND_LANDING_SOURCE=/private/tmp/overmind-capacity-real-decisions.parquet \
OVERMIND_LANDING_DEADLINE=3600 \
OVERMIND_LOAD_TEST_DATABASE=test_landing_real \
TEST_REDIS_URL=redis://localhost:6379/12 \
SANDBOX_TEST_IMAGE=YOUR_COMPATIBLE_IMAGE \
uv run pytest tests/load/landing.py --ds=tests.load.settings -q
```

This submits four full imports to one project and a tiny import to another. It
requires the quiet import to start before the noisy project's second import and
requires each project's own imports to run serially. This is an isolation check,
not a production throughput or maximum-concurrent-request certification. Do not
commit retained customer rows or copy them to production as part of this test.

The mixed workload also launches training and records explicit provider-training
windows. It requires a shared instant with training, imports, pipelines, trace
scoring, inference and quiet reads all active. Reports retain individual pipeline
queue and execution times. GPU execution is the external Modal fake; preparation, dispatch, progress
collection and training completion are real platform services. Serving startup
is excluded. Its synthetic training user has an active Pro subscription so the
soak measures concurrency rather than the Free plan monthly training quota.
Training reconcilers run at the production 15-second cadence.
