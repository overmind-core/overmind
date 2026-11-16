---
name: seed-demo-data
description: Run or modify the seed_demo management command (the one-project Support Copilot demo) without breaking the beat-safety invariants that keep celery workers from re-driving seeded rows. Use when seeding demo data or editing overbae/management/commands/seed_demo.py.
---

# Demo seed (`manage.py seed_demo`)

Seeds one project — **Support Copilot** at Ledgerline, a fictional payments
company — with thirty days of traffic across three capabilities (ticket triage,
KB answering, dispute resolution) and every downstream surface filled: an eval
matrix and eval sets, live span and trajectory scores with `EvalScore` history,
signal subscriptions and events, sessions, datasets with immutable cell chains,
grouped eval runs, optimiser runs (harness, model comparison), training jobs
with judge evals and class metrics, deployed models with inference traffic, and
the credits ledger.

## Run

```bash
docker compose exec api python manage.py seed_demo [--owner EMAIL]
```

~10 min, deterministic (seeded RNG, uuid5 ids for capabilities, jobs and
groups, timestamps anchored to NOW) and idempotent: the reset deletes the
project by slug (plus the retired Undermind demo slugs), the `@ledgerline.dev`
team users, and the owner's non-free-credit ledger rows before it seeds. The
project belongs to `--owner` (default `frey@overmindlab.ai`); the
account is created with password `password` when it does not exist. Plaintext
API keys print at the end. Activity and onboarding examples are terminal receipts
labelled Demo; they never imply that a real coding agent performed the seeded work.

## Beat-safety invariants — never break these

Celery beat + reconcilers stay running against the seeded DB. Violations cause
workers to re-drive seeded rows against **real providers** and spend credits:

- Every `EvalRun` / `FinetuningJob` / `OptimizerExperiment` / dataset `Cell`
  is TERMINAL (reconcilers re-drive non-terminal rows within 10–60s).
- The span sweep re-scores any span whose `eval_attributes` lacks a `score`
  key, and the trajectory sweep any root from the last day with a `score` but
  no `trajectory`. So every span is written with a final `eval_attributes`
  (children `score: None`), every root carries a `trajectory` block, and every
  span's `received_at` is backdated **in the same `_flush()` that inserts it**.
- Signal subscriptions are deterministic signals only; `plan_drift` calls the
  judge on every run of the signal beat.
- A capability sync fires the rebind hook (`identity.enqueue_rebind`); the seed
  patches it to a no-op. The Default eval set upsert is synchronous and calls
  no model.
- The training monitor syncs each `FinetuningJobEval` score from its own
  `EvalRun` summary, so every job eval links to its own single-target run
  (`bench_runs`).
- Connectors keep `auto_sync_enabled=False`.

## Mechanics

- `auto_now_add`/`auto_now` columns are backdated via the raw-SQL `backdate()`
  helper (executemany), not via the ORM.
- Datasets land through `services/datasets/land.py`. Explicit fixture transformations
  publish measured, provenance-preserving frames directly; no platform agent or
  script runner exists. Every dataset has a real active frame. Historical demo consumption stamps `used_at` directly and pins cells,
  including each training job's `eval_cell`; it does not fabricate a semantic
  quality review. Live use reports missing reviews as warnings. The reset deletes
  jobs, experiments and eval runs before the project.
- An eval run is one target; a comparison is runs sharing a `group` with one
  `is_baseline`. Each run's rows are `EvalRunRow`s with `dataset_row`
  `EvalScore`s, and its summary comes from the real `innermind.runs.aggregate`.
- When building the file in parts, formatter tooling can prune imports that are
  only used by later parts — restore the import block at the end.

## Saved model workflows

`seed_model_workflows` uses synthetic support inputs, real local exploration/partitioning and the common offline scorer. Its comparison is labelled synthetic, and its saved performance workload explicitly has no measurements. All exploration/run/partition states are terminal; the historical training group is wrapped without dispatch. Reset deletes data explorations, performance runs, training experiments, native evaluations and partition plans before protected cells/jobs. No provider adapter is called.

The training-plan fixture is a static discovery snapshot with no baseline, pilot or selection claim. It schedules no work. Reset removes snapshots newest first, before protected partitions/cells, because a superseding snapshot protects its predecessor.
