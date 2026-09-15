---
name: seed-demo-data
description: Run or modify seed.py (full-platform demo workspace) without breaking the beat-safety invariants that keep celery workers from re-driving seeded rows. Use when seeding demo data or editing seed.py.
---

# Demo seed (`seed.py`)

Seeds a complete investor-demoable workspace covering every table: projects,
capabilities, ~90 days of spans, datasets with versions, eval runs, optimizer
experiments, finetuning jobs, deployed models with inference traffic + ledger.

## Run

```bash
docker compose exec -T api python manage.py shell < seed.py
```

~2 min, idempotent (deletes the demo users + project slugs first). Demo login:
superuser with access to all seeded projects — see the credentials block at the
top of `seed.py`.

## Beat-safety invariants — never break these

Celery beat + reconcilers stay running against the seeded DB. Violations cause
workers to re-drive seeded rows against **real providers**:

- Every `Job` / `EvalRun` / `FinetuningJob` / `OptimizerExperiment` must be in a
  TERMINAL state (reconcilers re-drive non-terminal rows within 10–60s). One
  deliberately `paused` optimizer experiment is the only "in-flight" row.
- `sweep_unscored_traces` keys off `ScoringPass` rows and a two-hour
  `received_at` lookback — backdating `received_at` past the lookback is what
  keeps seeded traces out of live scoring (a root `trace_scoring` feedback
  block alone does not). Seed both anyway: the block is what the UI renders.
- Backdate `received_at`; connectors keep `auto_sync_enabled=False`.

## Mechanics

- `auto_now_add`/`auto_now` columns are backdated via the raw-SQL `backdate()`
  helper (executemany), not via the ORM.
- Datasets seed through `services/datasets/land.land_rows` + `notebook/run.execute`
  so every dataset has an active version with a real frame; consumers take
  `cell=dataset.active_cell` or the gates refuse them. Consumers PROTECT
  their cell, so the reset deletes jobs, experiments and eval runs before the
  projects.
- When building the file in parts, formatter tooling can prune imports that are
  only used by later parts — restore the import block at the end.
