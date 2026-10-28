---
name: backend-architecture
description: Map of the overbae backend — module layout, the patterns every subsystem shares, and reference files for Celery topology, tracing and scoring, evaluation, capabilities and sync, auth and billing, training, serving and data-first workflows. Use when navigating an unfamiliar backend subsystem or wiring behavior across subsystems.
---

# overbae backend map

Single Django app `overbae`, project-scoped tenancy.

## Layout

- `api/` — DRF views and serializers, one module per surface (`eval_views.py`, `datasets.py`, `optimizer.py`, `otlp.py`, `billing.py`, `mcp.py`, …). `views.py` and `serializers.py` are legacy monoliths; a new surface gets its own module. The global exception handler (`api/exception_handler.py`) flattens list errors to `{detail}`, adds `code`, maps `IntegrityError` to 409 `conflict`, and returns `{detail, code: internal_error, error_id}` for unhandled errors.
- `models/` — split by domain (`iam.py`, `capabilities.py`, `traces.py`, `innermind.py`, `finetuning.py`, `datasets.py`, `dataset_pipeline.py`, `operations.py`, `billing.py`, …).
- `innermind/` — the eval matrix evaluators, live span and trajectory scoring, deterministic checks, evaluation runs, the Default eval set, health and signals.
- `services/` — business logic. Largest subtrees: `datasets/` (Data Workshop; data-workshop skill), `mcp/` (mcp skill), `connectors/`, `sft_assets/` (training code baked into the Modal image), `codebase/`.
- `tasks/` — Celery tasks, roughly one module per feature; `utils/task_lock.py` for locking.
- `modal/` — Modal app entrypoints (vLLM serving, SFT, native decision evaluation and serving) and `models.json`. Code shared by those workers and the API lives in the root `modal_shared/` package.
- `management/commands/` — `seed_demo` (seed-demo-data skill), `grant_credits`, `check_workshop_transition`, `sync_baseten_finetuning_costs`.

## Patterns every subsystem follows

- **One domain service, three surfaces.** REST, MCP and the Console call the same service. Never copy business logic into a view or an MCP adapter.
- **Request keys.** An operation that starts work takes a project-scoped `request_key`. An identical retry returns the original record; a changed payload under the same key conflicts.
- **Durable admission.** Waiting work lives in PostgreSQL rows, and a locked dispatcher admits it with per-project fairness. Do not park waiting work in the broker.
- **Unknown provider outcomes are reconciled, never resubmitted.** Intent is journaled before a provider call; a lost acknowledgement becomes `submission_unknown` and is resolved from the saved call ID.
- **Measured, not invented.** Missing usage, progress, scores or telemetry stay unknown or null. Never fill a percentage, a zero score, an ETA or a cost from elapsed time or a guess.
- **Status writes use `.filter().update()`.** No Django signals fire; reload the row and wire side effects at the call site.
- **Model IDs**: platform models (judges, generation, Jev) live in `core/model_registry.py`; the training and serving catalog lives in `modal/models.json`, read through `modal/model_registry.py`. Jev decisions go through `core/decisions.py`.
- **Modal code ships separately.** Changes under `overbae/modal/`, `modal_shared/` or `services/sft_assets/` reach running jobs only after `modal deploy`; training and serving pin immutable releases.

## Reference files

Read the one that matches the task:

| File                                                                   | Covers                                                                              |
| ---------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| [references/celery.md](references/celery.md)                           | Workers, queues, routing, admission, deployment order                               |
| [references/tracing-and-scoring.md](references/tracing-and-scoring.md) | Span model, OTLP ingest, span cost, live scoring, signals, Jev                      |
| [references/evaluation.md](references/evaluation.md)                   | Evaluation runs, comparison, judges, context estimates, benchmark selection         |
| [references/capabilities.md](references/capabilities.md)               | The agent graph, scans, toml sync, Default eval set, provenance                     |
| [references/auth-and-billing.md](references/auth-and-billing.md)       | Clerk and local auth, guests, API keys, billing, Overwatch internal endpoints       |
| [references/training.md](references/training.md)                       | Preparation, launch, monitoring, transfer, native decision training                 |
| [references/serving.md](references/serving.md)                         | Deployments, shared-base LoRA, activation, inference requests, operational receipts |
| [references/data-first.md](references/data-first.md)                   | Training plans, partitions, native evaluation, experiments, performance runs        |
