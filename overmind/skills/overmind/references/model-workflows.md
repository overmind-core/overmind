# Develop models from data

## Evidence-linked training plans

Use `save_training_plan` from source discovery for generative or decision tasks.
Retain prediction unit, target meaning, allowed inputs and availability evidence,
connected grouping fields, slices and success criterion. `primary_metric` is a
JSON Pointer into development-check metrics, such as `/hard_label_accuracy`,
`/expected_score_mae` or `/generation/accuracy`. Unknown semantics stay explicit.

Save a superseding snapshot after retained Workshop preparation and partitioning,
with the exact `prepared_cell` and `partition`. Audit contradictions, post-outcome
features, connected-case leakage and coverage. Retain audit, baseline and fitting
sanity evidence with methods, measurements and limitations. Baseline and sanity
reports reference train/development cells only. These reports are agent-attributed,
not independently verified semantic certification.

Create a full experiment directly with `training_plan=ID`, `stage="full"`, the
plan's train/development cells and the requested recipe. Full training requires
attributed audit, baseline and sanity evidence, but no pilot job or measured
pilot selection. Do not create or reuse a pilot merely to authorize a full run.

Only when the user requests a bounded quality experiment, use `stage="pilot"`,
explicit `max_steps` from 1–2048 and `monitoring.selection="development_loss"`.
Measure the declared task outcome as well as loss; generative tasks need the
corresponding generation probe. Hardware profiles are separate evidence.

Pilot-derived selection is optional. To retain one, save a snapshot with
`selection.check`, rationale and permitted `budget_fields` (`max_steps`,
`n_epochs`, `runtime_limit_seconds`). It requires a completed, fully scored
matching pilot development check and verified checkpoint. When supplied, full
experiments preserve that selected recipe except for declared budget changes.
An unrelated pilot is not evidence for the current task/data/partition.
Earlier snapshots remain reproducible, and launch receipts retain the exact
plan binding.

Use the frozen final role for native probability comparison or generative
evaluation, with calibration separate. Never use final results for recipe
selection. Forecast preparation remains separate from quality evidence. Discover
plans with `list_model_workflows` and `get_job(kind="training_plan")`. Direct
exploratory training remains available; no universal semantic-approval gate is added.

Read `overmind://interface/current` for the connected contract, then use the native `develop-model-from-data` prompt. A project can start in Console onboarding with **From data**, then upload through Data Workshop or `overmind dataset upload`. No repository scan or capability is required. Keep the original written task with the dataset.

The native coding agent explores the whole source and task families before authoring immutable transformation scripts. Unknown target meaning stays unknown. Preserve full probabilities and mean-only ordinal targets; numeric shape is not evidence of annotator votes or reference truth. Record semantic changes and supporting evidence in the retained recipe and review metadata. Workshop executes scripts and retains facts; it has no planning or generation agent.

`explore_dataset` saves a bounded background profile of an explicit `source_cell`. Optional sampling settings report feasibility, minimum rows and paginated stratum allocations through its resource. Profiles cover the whole source; examples remain bounded. `derive_dataset` creates a separate chain from a readable historical cell, preserves every row and its lineage, and does not move the parent's active version. Repeating an identical failed exploration request retries local work; changed requests need new keys.

The native coding agent owns interpretation. Save target meaning as a hypothesis, supported interpretation or conflict, with scoped source references, observed rows and unresolved evidence. Numeric shape or a string labelled evidence is not proof. No mandatory row-by-row LLM review is added.

Use `create_data_partition` with `source_cell`, `name`, `request_key`, and a recipe containing `seed`, `fractions` keyed by train/development/calibration/final, `group_by`, optional `stratify_by`, and optional `holdouts` (`field`, `values`, `role`). Inspect `get_job(kind="data_partition")` for counts, grouping, assignment hashes and member cells. Prepare each member for its consumer. Retry a failed saved partition with `retry_data_partition`.

`list_decision_models` returns eligible foundations, ready native artifacts and supported external participants. Eligibility is not proof of hardware fit or quality. `create_native_evaluation` freezes `final_cell`, optional `calibration_cell`, `participants` and the `baseline` key. `create_native_evaluation` saves a draft without inference. `prepare_native_evaluation` verifies immutable suites in background; `launch_native_evaluation` authorizes the saved participants and suites once. A participant has a key, name, kind and model ID, or a trained job UUID. Input references stay separate; one global temperature is fitted on the calibration suite before final scoring. Mean-only suites omit probability temperature calibration. Inspect `get_job(kind="native_evaluation")` and the returned resource for every benchmark, denominator, failure, raw/calibrated metric and paired interval. Calibration scores are in-sample; overlap and pretraining independence limitations remain explicit.

`create_training_experiment` saves a name, purpose, request key, explicit variants and optional evaluation protocol UUID. Each variant declares `base_model`, train `cell`, optional `development_cell`, and `hyperparameters` (for example `n_epochs`, `learning_rate`, `lora_r`, `seed`). A `checkpoint_policy` declares fractions ending at 1 and selection `last` or `development_loss`. Final scores never choose a checkpoint. Run `prepare_training_experiment` to verify sources and obtain a forecast bound to the frozen configuration. Inspect it before `launch_training_experiment`; pass its quote ID when user constraints require it. An optional `constraints.max_training_run_usd` and explicit `unpriced_allowance_usd` enforce the user's planning policy; there is no default spend cap or claim of provider all-in enforcement. Unknown costs stay unknown. `create_training_profile` saves one bounded candidate (1–128 steps, 60–3600 seconds per provider attempt, no automatic provider retries), then uses the same preparation and launch path. Native profiles sample representative length quantiles, retain labels and record their measurement scope; profile weights are not quality evidence. Requested/effective conditions and seed variation are recorded separately from evaluation uncertainty.

`measure_decision_performance` runs a paid saved workload after the required model identity/runtime is qualified; final quality scoring need not be complete. Workload fields are `sample_size` (1–64), `repetitions` (1–10), `concurrency` and `questions_per_request` (1–16), `seed` and optional `amortization_decisions`. It measures client time through complete responses, valid throughput and recorded costs. Native first-request startup, warm worker identity and provider-controlled caches remain distinct. Missing billing components and recovered calls with unknown original latency stay unknown.

Use `list_model_workflows` to discover saved work; `get_job` and its resource carry workflow state. Native evaluation results are compact summaries with coverage computed across every benchmark. Download `report.json_path` or `report.markdown_path` from the same server with the existing API credential for complete per-benchmark metrics, paired intervals and diagnostics; do not treat summary macros as individual benchmark scores. Calibration results remain marked in-sample. Transient evaluation interruptions recover automatically with bounded backoff; poll running plans. Saved native calls and completed responses are reused. Missing external predictions may retry after a recorded transport failure, at most three attempts per input, retaining unknown prior costs. Bare submission intents remain unresolved. Resume stopped comparisons or performance runs with their dedicated resume tools. Provider-reported versions do not independently verify weights. Recorded GPU/API usage is not an all-in invoice. None of these operations activates a model.

Independent eligible participant stages run concurrently while preserving calibration and final dependencies. Chunk continuation is immediate and periodic reconciliation repairs missed work. Source manifests bind immutable chunk offsets; complete results retain denominators, failures and paired uncertainty. Native checkpoints that fail stop only dependent work. Missing acknowledgements remain unknown.

Exhausted collection completes scoring on verified available predictions and sets
`results.completion.status=completed_with_issues`. Inspect its per-suite/per-arm
coverage and issues. Missing predictions are not zero scores; paired metrics use
shared scored inputs and partial coverage may bias results. Incomplete calibration
leaves raw final scores available without fabricated temperatures. Verification
and report failures still stop the job; paused plans require explicit resume.
Unknown external intents remain missing without replay and do not block later
inputs. Resume a paused external collector by its stage; native collectors still
require their recorded call ID.

`reuse_evaluation_predictions` explicitly links compatible completed predictions into a draft comparison. Match input/model/runtime/inference identities and stored hashes. `create_training_experiment(reuse_existing_predictions=true, evaluation=...)` carries that choice into its new comparison; default false preserves independent repetitions. This does not reuse performance measurements or erase seed variation.

Discover saved objectives, inputs, output requirements and state with `list_model_workflows` and `get_job`. Action receipts expose `next_actions`, `action_requirements`, `input_required`, polling guidance and detail links. An experiment aggregates child outcomes; a failed candidate remains visible. Inspect artifact verification and coverage, not merely process success.

Partition construction exposes stage/count progress. Explicit train/development roles have train intent even when the source is exploratory; calibration/final roles have eval intent. Native decisions are projected into input.decision and separate expected_output without changing labels, weights or row membership. Mean-only targets remain means and invalid targets remain visible. Chat and other shapes may still require Workshop preparation. Inspect exact member cells and counts before creating comparisons.

Current GPU-hour rates are separate from duration evidence. Unknown duration means unknown total, even with a current rate card; inspect estimate_blockers and rejected_measurements. Do not substitute historical charges for current rates or imply unreported components are free.
