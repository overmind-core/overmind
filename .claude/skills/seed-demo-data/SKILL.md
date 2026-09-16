---
name: seed-demo-data
description: Run or modify seed.py (the one-project Support Copilot demo) without breaking the beat-safety invariants that keep celery workers from re-driving seeded rows. Use when seeding demo data or editing seed.py.
---

# Demo seed (`seed.py`)

Seeds one project — **Support Copilot** at Ledgerline, a fictional payments
company — with thirty days of traffic across three capabilities (ticket triage,
KB answering, dispute resolution) and every downstream surface filled: tasks,
executions and verdicts, sessions, datasets with cell chains and chats, eval
runs, optimiser runs (harness, backtest, hybrid), training jobs with judge
evals and class metrics, deployed models with inference traffic, and the
credits ledger.

## Run

```bash
docker compose exec -T api python manage.py shell < seed.py
```

~10 min, deterministic (seeded RNG, uuid5 ids for capabilities, jobs and
groups, timestamps anchored to NOW) and idempotent: the reset deletes the
project by slug (plus the retired Undermind demo slugs), the `@ledgerline.dev`
team users, and the owner's non-free-credit ledger rows before it seeds. The
project belongs to `SEED_OWNER_EMAIL` (default `frey@overmindlab.ai`); the
account is created with password `password` when it does not exist. Plaintext
API keys print at the end.

## Beat-safety invariants — never break these

Celery beat + reconcilers stay running against the seeded DB. Violations cause
workers to re-drive seeded rows against **real providers** and spend credits:

- Every `EvalRun` / `FinetuningJob` / `OptimizerExperiment` / dataset `Cell`
  is TERMINAL (reconcilers re-drive non-terminal rows within 10–60s).
- `sweep_unscored_traces` selects roots by a two-hour `received_at` lookback
  and re-scores a trace whose latest `ScoringPass.started` precedes the root's
  `received_at`. So every trace is at least three hours old, every span's
  `received_at` is backdated **in the same `_flush()` that inserts it** (the
  sweep runs every two minutes; a batch left at "now" for even one tick gets
  scored and its `TaskExecution` collides with the seed's), and every scored
  trace has a `ScoringPass` whose `started` is after its spans landed.
- A capability sync fires the evaluator preload and rebind hooks
  (`sync_card_evaluators_task`, `preload_capability_eval_set`,
  `enqueue_capability_eval_preload_on_commit`, `identity.enqueue_rebind`) and
  the `sync_evaluators_on_card_change` signal; the seed patches them to no-ops
  and disconnects the signal. Evaluator `updated_at` is backdated too, or the
  "contract changed after the pass" rule rescores everything.
- The training monitor syncs each `FinetuningJobEval` score from its own
  `EvalRun` summary, so every job eval links to a single-variant run
  (`bench_runs`); a shared two-variant run collapses baseline and final to
  the pooled score.
- Connectors keep `auto_sync_enabled=False`.

## Mechanics

- `auto_now_add`/`auto_now` columns are backdated via the raw-SQL `backdate()`
  helper (executemany), not via the ORM.
- Datasets land through `services/datasets/land` and run through
  `notebook/run.execute`, so every dataset has an active cell with a real
  frame; consumers pass `cell=dataset.active_cell` through `use.use`, which
  PROTECTs the cell — the reset deletes jobs, experiments and eval runs before
  the project.
- Eval run summaries come from the real `aggregate_run` task applied inline;
  scores per variant are `Score` rows, so per-variant numbers are read from
  `run.scores`, never recomputed from the summary.
- When building the file in parts, formatter tooling can prune imports that are
  only used by later parts — restore the import block at the end.
