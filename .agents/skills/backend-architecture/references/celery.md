# Celery topology

Six workers over six queues. Workers are resource profiles; queues are fairness classes.

| Worker        | Pool    | Conc | Holds                                                                               |
| ------------- | ------- | ---- | ----------------------------------------------------------------------------------- |
| `control`     | threads | 8    | orchestration, chord callbacks, FSM advances, reconcilers                           |
| `io`          | threads | 24   | preparation, eval launch, serving, connector polling                                |
| `io_traces`   | prefork | 4    | live span and trajectory scoring, signals                                           |
| `batch`       | prefork | 6    | pipeline bindings, evaluation generation and scoring, partitions, native evaluation |
| `landing`     | prefork | 1    | source imports                                                                      |
| `interactive` | prefork | 4    | retained-package Workshop pipelines, runtime health                                 |

Each worker consumes only its own queue. Beat runs as its own `celery-beat` service; its tasks route like any other task.

## Why the split exists

- Only prefork enforces `time_limit` and `revoke(terminate=True)`. Every time-limited task routes to `batch`, `landing`, `interactive` or `io_traces`.
- A loaded prefork child costs hundreds of MB, so wide fan-out stays on thread pools.
- Orchestration has its own lane: a chord callback stuck behind work never finalises its run.
- `run_eval_run` routes to `io` because Modal warm-up blocks for minutes.
- `io_traces` is separate from `io` so trace scoring cannot occupy preparation or serving slots.

Every prefork lane runs `--prefetch-multiplier=1 --disable-prefetch`. A reserved late-ack message is redelivered after the broker `visibility_timeout` (2 h), which must stay above every late-ack time limit.

## Routing must agree in four places

`CELERY_TASK_ROUTES`, `make worker` (one local process, so its `-Q` lists every queue), docker-compose and `docker/worker-topology.json`. `tests/test_celery_topology.py` checks that compose and `make worker` drain every routed queue, rejects a time-limited task on a compose thread pool, checks the prefork prefetch flags and the `io`/`io_traces` split in `worker-topology.json`, and imports `overbae.tasks` in a fresh worker. Nothing checks that `worker-topology.json` drains every queue; keep it in step by hand. Workers hot-restart via watchmedo on `.py` changes.

## Durable admission

Waiting work is a PostgreSQL row, admitted by a row-locked dispatcher in least-recent-project order:

- `LiveScoringWork` — spans, trajectories and signals; global and per-project caps; fenced running lease longer than the five-minute hard limit.
- `DatasetLandingWork` — one import per project, global admission follows observed landing capacity up to eight. Waiting never expires unless `DATASET_IMPORT_MAX_QUEUE_SECONDS` is nonzero. A lost started import needs explicit recovery.
- `EvalGenerationScheduler` — `EVAL_MAX_IN_FLIGHT`, `EVAL_MAX_IN_FLIGHT_PER_PROJECT` and `EVAL_MAX_IN_FLIGHT_PER_RUN`; completion and a 30-second control tick refill slots.
- `DatasetPipelineRun` is the exception: a queued run takes a per-project Redis slot or retries through the broker every 2 s, and fails after 15 minutes without capacity. The default project limit stays below the minimum interactive worker size.

A lost started attempt becomes an explicit unknown outcome. It is never repeated automatically. Queue metrics report durable backlog and oldest waiting age.

## Leases and reconcilers

Frequent beat tasks (live-scoring admission, import and evaluation dispatch, Workshop bindings, queue metrics, sweeps) set short `expires`, so a busy queue drops stale ticks; most domain reconcilers set none. The training reconciler renews a 30-second Redis lease, so a dead worker stops suppressing observations quickly. Reconciliation repairs missed terminal events from local receipts; it never polls or resubmits a provider to do so.

## Deployment

Hosted commands come from `docker/worker-topology.json`. A rollout runs migrations, verifies a healthy landing-only consumer on the new image (`overbae.worker_health <queue>`), then updates workers, beat and API. Drain old consumers before stopping them; never discard reserved tasks or revoke acknowledged provider work. Scale-in needs an explicit idle drain.

## HTTP clients

Chat HTTP clients enforce one cancellable elapsed deadline across request bodies, SSE heartbeats and retry waits. A socket read timeout alone is not a total time bound.
