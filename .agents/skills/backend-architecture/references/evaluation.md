# Evaluation

## Runs

An `EvalRun` scores one target (`mode` `recorded`, `generate` or `provided`) over one pinned dataset cell with a frozen `matrix` (`runs.freeze_matrix`: the eval set's rows, else every enabled row for the capability). Creating several `targets` makes one run per target under a shared `group`; REST creation allows at most one `is_baseline`. Training groups can mark several baselines, and `baseline_of` takes the newest completed one. Startup commits the running state and the complete row inventory atomically before admission; model warm-up stays outside that transaction.

- `innermind/runs.py` owns the lifecycle (create, launch, generate, score, aggregate, finish, cancel, relaunch, reap). `generation.py` and `replay.py` produce outputs. A `provided` run cannot relaunch.
- `dataset.DatasetRowProcessor` scores rows with the live span evaluators and appends `EvalScore(source_type=dataset_row)`.
- A row stands for the capability's answer: it takes every frozen row whose `span_type` is blank or `llm_call`, whatever its `span_name`. `tool_call`, `retrieval` and `workflow` rows stay live-only.
- An optimiser experiment reuses its first run's matrix for later candidates; a training job does the same for its evaluations.
- `comparison.compare` pairs the latest score per row that both runs scored into wins, losses and ties (`EPSILON` 0.005), and labels per-metric and overall means improved, regressed or unchanged; added and removed mark a metric present in only one run. It also reports trust.
- Replayed rows snapshot the runner's initial request (`replay.RunResult.request`). A token-limited generation keeps its partial answer and usage, is marked degraded, and is excluded from trusted scores rather than scored as zero.

## Judges and context estimates

- `EvalRun.judge_model` freezes the explanation judge; blank uses the `EVAL_EXPLANATION` registry default. It never changes Jev. Existing runs reject judge changes. Training's `eval_judge_model` overrides it for that job's runs, and baseline sharing requires the same judge selection.
- `innermind/context.py` supplies advisory context estimates for generate targets and the judge, including reserved output (REST `POST /api/eval-runs/context-check/`, run detail, MCP readiness). Warnings never change `ready`, gate launch or remove a candidate. Unknown limits stay unverified; unknown prices are null.
- The Console creates standalone runs (`CreateRunDialog`, with judge model and context preview), relaunches and deletes runs, and creates and deletes matrix rows. Training setup carries its own judge selector and advisory warnings: selectors are tinted only for estimated overflows, undersized options stay selectable, and Start opens a short risk summary with Start anyway. Unverified and pending checks stay neutral. Nothing blocks launch or changes a model automatically.

## Training evaluations and benchmark selection

Training has four persisted choices: `eval_model_before` and `eval_model_after` (untouched base, then the trained deployment; on by default) and `eval_incumbent_before` and `eval_incumbent_after` (opt-in). An eval dataset and an eval set with at least one enabled row are required only when at least one choice is on. MCP turns `eval_model_before` and `eval_model_after` off by default for native decision cells.

- The benchmark model is a ready trained model or null for the codebase incumbent. A job snapshots it as `baseline_model`; it never changes `active_model` or serving. The capability default is `benchmark_model` (MCP `set_benchmark_model`).
- Every evaluation of a job uses all rows of the same pinned eval cell and the same frozen matrix, in one run group. There is no row cap or sampling.
- Chat-training setup can pin extra benchmark models for the same eval set and dataset; with benchmarking on, they and the selected training bases are scored before training.
- Baseline evaluations run alongside GPU training and never gate it. After-evaluations start when training finishes.
- Foundation entries in `models.json` carry a nullable `openrouter_id`. The resolver matches that mapping, then exact model or checkpoint identities, never fuzzy names or another size. Catalogues expose `openrouter_status`: `available`, `not_listed` or `catalog_unavailable`. A started evaluation or unresolved deployment keeps its route.
- Training jobs and eval sets may have no capability. Unassigned eval sets are project-scoped and cannot be activated.

API modules: `api/eval_views.py`, `api/signal_views.py`.
