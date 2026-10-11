# Training progress and development evaluation implementation plan

Status: implementation and qualification in progress. A fresh $100 implementation
and relaunch allowance was explicitly authorised on 2026-10-10. See
`tests/evidence/training-monitoring-implementation-2026-10-10.md` for measured
coverage, live identifiers and remaining gaps; the specification below is not a
claim that every acceptance scenario is qualified.

Receipt-backed classification observations and independent latest-generation
summaries are implemented on MCP contract 6.2.1. They validate recorded counts and
frozen labels, retain attribution separately from worker metrics, and never change
training strategy. The full KYC worker finished 133 updates; final development
generation remains below the same probe's majority baseline. See the evidence
report for deployment status, costs and remaining unsupported capabilities.

## Outcome and ownership

Give users and their native coding agents trustworthy evidence about learning,
task performance, regressions, execution health and retained artifacts before the
final benchmark. The platform executes a declared measurement contract; it does
not become a training-strategy agent.

- The user/coding agent supplies task meaning, target semantics, evaluators,
  grouping/slices, primary selection metric and acceptable trade-offs.
- Overmind validates and freezes that contract, executes bounded checks,
  aggregates facts, records provenance and emits deterministic findings.
- Only explicitly enabled rules may select a checkpoint or stop training.
  Warnings never silently change data, learning rate, model, objective or labels.
- Development monitoring, final benchmarking, artifact integrity and serving
  readiness remain separate outcomes. There is no composite “model health” score.
- This implementation stays on the current checkout and preserves unrelated work.
  It does not redesign Workshop, change live model routing or restart old jobs.

## Verified starting point

| Area             | Existing implementation                                                                                                                         | Required change                                                                                                          |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Chat training    | `sft_assets/engine_unsloth.py` uses epoch evaluation and final-only saving                                                                      | Bounded intermediate validation, predictions and durable checkpoints                                                     |
| Worker metrics   | `sft_assets/common.py` emits loss, LR, gradients, token accuracy, token throughput and memory                                                   | Coverage, numerical-health counters, separate stage timings and complete collection                                      |
| Native decisions | `decision_engine.py` and `modal_shared/decision_checkpoint_policy.py` support development checks, retained checkpoints, Brier/agreement metrics | Shared monitoring contract without losing distribution/ordinal semantics or resume guarantees                            |
| Evaluation       | Existing deterministic/statistical evaluators, frozen judge snapshots and class metrics                                                         | Development-only prediction scoring, paired changes, slices and visible scheduling failures                              |
| Partitions       | `finetuning_split.py` delegates to content/group-aware `datasets/partition.py`                                                                  | Retain and expose exact assignments/overlap proof for monitoring; test preservation, not replace it with naive splitting |
| Receipts         | Training records, submission intents and operational timelines                                                                                  | Durable check/checkpoint identities, full paginated history, findings and availability                                   |
| UI               | Existing monitor, loss charts, class tables and confusion matrices                                                                              | Consume the same typed monitoring facts as MCP                                                                           |
| MCP              | Readiness, preparation, estimate, launch, job/resource inspection                                                                               | One monitoring contract throughout; detailed inspection and scoped cancellation                                          |

The word `random` in a split recipe does not establish row-level randomisation:
the current splitter randomises content/group components and preserves repeated
observations. Historical runs need their own frozen split evidence. Near-duplicate
and pretraining contamination remain separate, unproven questions.

`finetuning_eval.tick_job_evals` catches and logs launch exceptions; an evaluator
snapshot can reject a missing compiled checklist before an evaluation row exists.
This can leave a projected UI row waiting without a durable failure. Close that
gap as part of this work; do not add monitoring on top of invisible failures.

## Frozen monitoring contract

Introduce a strict shared `TrainingMonitoringPolicy`, accepted by REST and MCP
readiness, preparation, estimates, training launch and experiment variants. Store
the resolved immutable policy and fingerprint in the training launch record.
Keep large manifests outside the job's progress JSON.

The contract contains:

- Exact development cell/partition, seed, ordered row identity manifest, input
  fingerprint, group assignments, strata and sampling weights where applicable.
- Separate frozen loss-probe and generation-probe selections; an optional
  agent-supplied challenge/regression suite is reported separately.
- Objective-specific metric definitions, direction, denominators, label/option
  order, output extraction/normalisation, scorer revision and minimum coverage.
- Frozen generation settings, system prompt/tools and output limits. Tool
  execution is off for label/schema probes; broader workflow tests require an
  explicitly authorised isolated test environment, never production actions.
- Schedule mode (`adaptive`, `steps`, `epoch`, `off`), initial check, interim and
  final development checks, bounds, check-count limits and overhead target.
- Checkpoint cadence/retention, selection (`last` or named development metric),
  tie-break, and optional early-stop patience/minimum delta/warm-up rules.
- Explicit failure policy for optional checks versus checks required for a
  promised selection/stop rule. Missing evidence cannot satisfy such a rule.
- Provider capabilities, permitted metered checks, concurrency and cost/time
  constraints; estimates distinguish training, checks, storage and unmeasured costs.

Runtime adaptation changes execution timing only, within the frozen rules. It
does not mutate samples, metrics or thresholds. A changed semantic contract is a
new explicitly authorised run, not an invisible revision of an active run.

Readiness rejects malformed, contradictory or unsupported execution settings.
Task suitability and scientific limitations remain explicit advisory findings;
they do not cause the platform to invent semantics or rewrite data.

## Sampling and statistical validity

1. Use the existing partition/consumer services to freeze training and development
   assignments before provider dispatch. Preserve duplicates, weights and lineage.
1. Never source monitoring samples from calibration or final-test roles. Detect
   exact content/group overlap; expose source identities and scope of the check.
1. Select repeatable, group-preserving development samples using declared strata.
   Start with a target of 2,048 loss-probe rows and, when generation checks are
   selected, 256 generation-probe rows. These are visible defaults, not silent
   limits; use all rows for smaller eligible datasets.
1. Whole-group selection may change realised counts. Record requested/actual
   counts and unrepresented slices; report an infeasible sample instead of splitting
   a protected group. Explicit sizes are honoured or rejected.
1. Keep the population-representative sample distinct from deliberately enriched
   rare-case/challenge slices. Never average an enriched sample as if it represented
   production prevalence; retain inclusion/weighting semantics.
1. Compute paired checkpoint deltas on the same successful comparable examples,
   exposing unmatched/error counts. Use group-level bootstrap where observations
   are correlated; pin seed/method and report effective group counts.
1. Intervals are descriptive development evidence, not repeated-testing guarantees
   or proof of final generalisation. Insufficient coverage remains inconclusive.
1. Full development validation confirms the chosen candidate at completion; the
   final benchmark remains independent and cannot silently trigger reselection.

## Adaptive scheduling

Implement one deterministic scheduler shared by the chat callback and native
loop. Use the existing trainer's callbacks/evaluation facilities, not a new
training engine. Persist scheduler state in restartable checkpoints.

Suggested defaults to qualify with measurements:

- Cheap initial development loss check before updates when validation is enabled.
- First interim check by roughly 10% of planned updates where feasible; no promise
  of an interim check for a one-update run.
- Aim for approximately five minutes of optimisation between checks, adjusted for
  remaining steps and a maximum interim-check count (initial default: 12).
- Target at most 10% monitoring overhead for periodic checks, with initial/final
  work reported separately. This is a scheduling target, not a dollar-spend cap.
- Schedule generation probes less frequently (initially every third interim loss
  check and at final development evaluation), within their own authorised budget.

For measured validation duration `C`, median optimisation-step duration `T` and
overhead fraction `b`, a starting lower bound is
`ceil(C * (1 - b) / (b * T))` optimisation steps between checks. Combine this with
the target time interval, remaining work and check-count bounds. Exclude validation,
checkpointing, queue delays and previous attempts from the optimisation timing
window. Record the observations and formula version used for each decision.

Do not guess before timing exists: use a recorded provisional step schedule. At
each completed check, recalculate only the next eligible boundary, with bounded
change/hysteresis. Quality signals may increase cadence only if the user/agent
explicitly enabled a threshold rule; no hidden strategy controller.

If early coverage, maximum wait and overhead constraints conflict, surface
`monitoring_budget_conflict` with the effective schedule. Do not silently shrink
the sample, drop a metric or claim the overhead target was achieved. A strict
explicit constraint fails preparation/launch if it cannot be met; an adaptive
target carries a warning and a recorded deterministic priority order (feasible
early signal first, overhead target second, within hard authorised budgets).

At most one check per stream is active. A slow check does not generate an
unbounded backlog: record superseded schedule slots and retain the next due slot.
Report optimisation time, monitoring time and elapsed time separately.

## Measurement execution

### Every training logging interval

Capture loss and its denominator, LR, pre-clipping gradient norm, clipping counts,
non-finite values, skipped updates, actual supervised/input/padded tokens where
measurable, data position, effective batch, optimiser-step duration, throughput,
GPU memory and host memory. An unavailable counter is null with a reason, not zero.

Report a measured stall separately from collector lag and worker liveness. Retain
preparation facts for rejected rows, truncation policy and zero-supervision rows;
do not introduce silent data filtering to make a run succeed.

### Periodic loss validation

Measure held-out objective loss and available token/decision metrics. For a
generalisation-gap diagnostic, score a small fixed training reference sample in
the same evaluation mode and weighting as development; do not subtract a noisy
rolling training-mode loss and call the result a calibrated overfitting measure.

Preserve training/eval mode and RNG state around checks so monitoring does not
change dropout/shuffling or the intended optimisation sequence. Validate packed
sequence boundaries and loss masks against unpacked references. Token metrics
remain explicitly teacher-forced, distinct from generated-answer correctness.

### Periodic generated-output checks

Generate from input-only records, then score against references outside generation.
For platform-controlled chat workers, use the current model at a safe optimiser
boundary with bounded generation; restore its mode/RNG before continuing. Publish
retained checkpoint identity for task-level check points. Do not deploy or activate
every intermediate checkpoint just to inspect learning.

Reuse existing evaluator snapshots and scoring functions:

- Classification: exact-label accuracy, macro/per-class precision/recall/F1,
  confusion matrix, prediction distribution and invalid-label rate.
- Structured extraction: schema validity and declared field/entity comparisons.
- Tool tasks: syntax and declared tool/argument contract checks; end-to-end tool
  behaviour only in an authorised sandbox with its own execution receipt.
- Free-text tasks: frozen agent-authored deterministic checks or explicitly
  selected metered judges. No platform-authored success rubric.
- Native distributions: objective loss, Brier/distribution metrics and label
  agreement with the correct denominators. Mean-only ordinal targets receive
  expected-score errors, never invented reference distributions.
- Confidence/reliability: only with a qualified probability contract. Never infer
  class probabilities from a confidence string or raw generated-label token score.
- Optional regression/challenge suites: separate results, attribution and coverage.

Initial scope uses existing declarative deterministic/statistical evaluators and
the existing configured judge system. A general arbitrary-code grader runtime is
not a prerequisite and must not be introduced through `eval` or API/Celery Python.
If a needed check cannot be represented, return a capability gap for deliberate
extension; do not run unreviewed agent code inside the trainer.

Store input/output/reference evidence, finish reason, scorer version, latency,
usage and failure classification in bounded, paginated artifacts. Preserve all
expected examples in coverage: model-invalid output, technical generation errors,
judge errors and unscorable cases have different outcomes and denominators.

### Provider routing and capability boundaries

- OpenRouter remains the preferred exact-identity route for public base-model
  generation benchmarks. No fuzzy model substitution or silent paid hosting
  fallback when the requested route is unavailable; report the missing route.
- Teacher-forced development loss is a training-worker measurement, not an
  OpenRouter benchmark. It requires access to the model and supervised token loss.
- Trained/hosted checkpoint checks run against that exact checkpoint. Do not
  substitute its base model or an active project alias.
- OpenRouter-versus-local generation comparisons disclose runtime/precision/template
  differences; they do not masquerade as same-runtime optimisation baselines.
- Implement the common contract in Modal chat/native workers and the Baseten path
  using the shared assets. For other providers, advertise real supported metrics,
  checkpoint access and schedule control; reject unsupported explicit requests.
  Do not invent telemetry or claim provider-wide feature parity without qualification.

## Durable checks, checkpoints and findings

Keep the policy in the frozen job record. Add two focused project-owned entities:

1. `TrainingValidationRun`: job, policy/sample fingerprint, stream, check ordinal,
   optimiser step/attempt, state, heartbeat/forward progress, checkpoint identity,
   metric summary, coverage, optional existing EvalRun link, artifact manifest,
   provider call receipts, usage and structured failure.
1. `TrainingCheckpoint`: job, step/attempt, exact artifact/checksum manifest,
   runtime/base/tokenizer identities, durability/reload status, optional full resume
   state, validation links, retention protection and selected status.

Use unique `(job, policy, stream, ordinal)` check identities and immutable artifact
generations. Existing `OperationalRun/Event` provides timelines and finding events;
do not introduce another generic event bus. Progress JSON becomes a bounded
projection, never the only copy of check history or predictions.

Persist planned evaluation/check rows before constructing evaluator snapshots or
submitting work. Snapshot/dispatch failures then have durable terminal receipts.
Separate transient observation failure from provider execution failure. Unknown
submission acknowledgements reconcile saved intent/call identity, never replay.

Checkpoint publication is write → checksum/seal → commit → reload verification →
eligible. Partial files never become selected. Protect last and best retained
checkpoints plus artifacts referenced by active checks; garbage-collect only after
replacement/archive verification. An inference adapter is not proof of resumable
optimizer state; expose `resumable` and its missing components explicitly.

Cancellation first records intent and fences new checks/publication, then requests
provider cancellation. Retain already durable artifacts/results. Best-effort final
checkpointing may be attempted only within a declared grace period; a hard-killed
worker cannot promise to preserve its latest unsaved update. Late events may add
historical evidence but cannot turn a cancelled job into a success or activation.

Checkpoint selection defaults to `last`; a named development-metric selection
and early stopping are opt-ins. Eligible checks require declared coverage, finite
metrics and valid comparable sample identity. Patience counts completed eligible
checks, not polling or failures. Early stop records its rule/evidence and remains
distinct from user cancellation, infrastructure failure and final benchmark success.

Deterministic findings include numerical failure, missing scheduled validation,
stale observations, sustained development deterioration, declared slice threshold
breach, insufficient coverage, output-format regression, checkpoint integrity
failure and monitoring-budget conflict. Every finding includes rule version,
measurement IDs, scope, timestamps and status. No LLM diagnosis or automatic
hyperparameter/data change is required.

## REST and MCP surface

Classification: policies, checks, findings, checkpoint metadata and cancellation
are MCP-ready. Binary downloads remain CLI-guided. Chart rendering is frontend-only;
all underlying values and comparison records are MCP-readable.

| Surface                                   | Change                                                                                                                                  |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `get_model_catalog`                       | Advertise objective/provider monitoring capabilities and runtime qualification                                                          |
| `check_finetune_readiness`                | Accept policy; return resolved coverage, scorer readiness, unsupported fields and findings                                              |
| `prepare_training_data`                   | Freeze/verify monitoring manifests with the exact prepared data; no hidden paid generation                                              |
| `estimate_finetune`                       | Include monitoring work/uncertainty and selected judge/checkpoint overhead                                                              |
| `start_finetune` / experiment save-launch | Freeze the same policy and fingerprint under the existing request-key contract                                                          |
| `get_job` / finetune resource             | Compact current quality/coverage, last/next check, best/last checkpoint, findings and evidence links                                    |
| Proposed `inspect_training_progress`      | One passive paginated tool for checks, metrics, slices, samples, findings and checkpoints; supports explicit same-run check comparisons |
| `inspect_operation`                       | Reuse durable event timeline; no worker calls on read                                                                                   |
| Proposed `cancel_finetune`                | Idempotent project-scoped cancellation through the shared REST service, with intent/acknowledgement distinction                         |
| Checkpoint download guidance              | Extend existing authenticated handoff to an explicit eligible retained checkpoint, not only deployed final models                       |

The new inspection tool/resources never launch evaluation, warm GPUs or recompute
missing analysis. Scoring/aggregation happen in recorded background work. Responses
retain units, numerator/denominator, sample/group counts, uncertainty method,
availability, source/observation time, checkpoint/scorer identity and truncation
metadata. Full evidence is paginated/authenticated, not embedded in polling replies.

Update strict MCP contracts, catalogue metadata, resources, prompts, interface
version/fingerprint and installed guidance together. Readiness must run the same
snapshot validation as launch so an incomplete judge is not advertised as ready.
Schema/permission and cost annotations must reflect generation/judge work accurately.

Retry/resume remains the existing explicit training lifecycle; this plan does not
invent general mid-run editing, an autonomous restart tool or arbitrary checkpoint
promotion. Full chat optimizer-resume support may be claimed only after separately
qualifying the stored state. Trained checkpoints never become live automatically.

## Console and product experience

Extend the existing training page rather than redesign it:

- Setup: automatic monitoring summary; advanced sample/cadence, task checks,
  overhead, checkpoint selection and early-stop controls. Defaults are visible.
- Live summary: optimisation progress, latest development result, coverage, next
  check, last durable checkpoint and unresolved findings.
- Charts: training/development loss with distinct sample identities, task metric
  history, confidence intervals and checkpoint markers; no absent value plotted as 0.
- Development evidence: class/slice tables, confusion matrix and paginated paired
  examples showing improvements and regressions.
- Checkpoint list: saved/verified/selected/resumable states and selection reason.
- Operational detail: actual stage counts, timings, check overhead, cost coverage,
  heartbeat versus forward progress and provider limitations.
- Final benchmark stays visually separate; cancelled/stopped jobs retain evidence.

Use generated OpenAPI types; remove touched ad-hoc unknown casts. Preserve dark-only
tokens, accessibility, keyboard navigation and existing common charts/components.
No green quality indicator without the evidence it describes.

Product analytics: time to first valid development result, unobserved training
duration, validation completion/coverage, overhead, recoverable checkpoint coverage,
time to understand a failure, evidence drill-down use and duplicate provider-work
incidents. Do not send raw training examples/prompts/outputs to product analytics.
Perceived clarity/helpfulness requires explicit user feedback; clicks or successful
tool calls alone do not establish delight.

## Implementation sequence and code ownership

Each work package includes contracts, tests and documentation before claiming it
complete. These are dependency order, not separate half-finished product releases.

1. **Acceptance fixtures and failure contracts.** Write user-level failing journeys
   and deterministic failure-injection scenarios first. Verify current split
   preservation and reproduce the invisible evaluator-start failure.
1. **Shared policy and durable records.** Add strict policy validation, monitoring
   manifests, focused models/migrations, provider capability declarations, shared
   readiness/estimate/launch handling and persisted failure states.
1. **Worker execution and checkpoint durability.** Wire the shared scheduler and
   checkpoint publication into chat/native engines; instrument stages/counters;
   preserve exact input, masking, mode/RNG and resume identities.
1. **Development scoring and findings.** Reuse deterministic/statistical evaluators,
   native metric functions and selected judges; add predictions, slices, paired
   reports, coverage/uncertainty and threshold finding lifecycle.
1. **MCP/REST and Console parity.** Complete discovery, inspection/cancellation,
   bounded resources/download handoffs and typed setup/monitor views. Verify all
   meaningful agent actions without the browser.
1. **Release qualification and local handoff.** Run fixture/provider/recovery
   journeys, qualify real pinned GPU workers, update docs/skills and verify the
   installed local MCP/CLI against the deployed immutable release.

Primary existing owners: `services/training_record.py`, `training_contract.py`,
`training_preparation.py`, `training_forecast.py`, `training_experiments.py`,
`training_submission.py`, `finetuning_runner.py`, `finetuning_eval.py`,
`services/sft_assets/`, `modal_shared/training_telemetry.py`,
`modal_shared/decision_checkpoint_policy.py`, `models/finetuning.py`,
`services/operational_progress.py`, `tasks/finetuning*.py`, MCP finetuning/contracts,
REST training serializers, and existing frontend training setup/monitor components.
Introduce only focused policy/check-record/scoring modules where those owners
cannot hold a clear single responsibility. Reuse existing metrics libraries.

## Acceptance and regression matrix

Prefer end-to-end journeys; isolated deterministic scheduler/checkpoint tests are
justified for failure modes impractical or costly to reproduce on real GPUs. Write
those failure cases and tests before implementation, not afterwards.

| Scenario                                         | Required observable result                                                                     |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| Short chat run ending before an epoch            | Eligible intermediate evidence and durable checkpoint before completion where feasible         |
| One-update/tiny development run                  | Honest baseline/final-only coverage; no fabricated interim signal                              |
| Long/multi-epoch run, changing step speed        | Bounded adapted cadence, recorded inputs/reasons and no overlapping checks                     |
| Large corpus / many slices                       | Bounded memory/payloads, immutable paginated evidence, explicit slice coverage                 |
| Imbalanced multilingual classification           | Correct class counts/confusion matrix and separate rare/language slice outcomes                |
| Structured/tool output                           | Correct schema/argument scoring, no unauthorised tool side effects                             |
| Free text / selected judge                       | Frozen graders, attributed costs and visible judge readiness/runtime failures                  |
| Native soft/ordinal targets                      | Preserved probabilities, weights/options, correct metric applicability and denominators        |
| Duplicate/content/case groups                    | Preserved observations, no protected-group crossing; exact proof and near-duplicate limitation |
| Missing/contradictory task semantics             | No invented label meaning, probability or scoring rubric                                       |
| Validation deterioration / class collapse        | Rule-backed finding; no change unless an explicit stop/selection policy permits it             |
| Missing class / zero successes / errors          | Inconclusive or failed measurement, not 0/100% invented quality                                |
| Empty checklist / snapshot failure               | Durable failed evaluation receipt, actionable reason, no endless projected Pending             |
| Generation truncation / timeout                  | Retained partial output and distinct failure/coverage; no trusted full-quality result          |
| Restart during validation/checkpoint write       | Same check identity, verified recovery, no partial checkpoint eligibility                      |
| Lost provider acknowledgement / duplicate events | Reconciliation without duplicate paid work; deduplicated history/usage                         |
| Cancellation while saving/scoring                | Stop intent remains authoritative; saved evidence survives; remote state stays honest          |
| Collector down / late/out-of-order telemetry     | Freshness/backlog explicit, no fake worker progress or terminal-state resurrection             |
| Early stop / best selection                      | Only eligible metric/patience causes action; tie-break deterministic and evidence retained     |
| Unsupported provider metrics                     | Discoverable limitation and rejected unsupported configuration, no silent fallback             |
| Cross-project credentials/read-only scope        | No reads/writes/evidence leakage outside authorisation                                         |
| Repeated MCP reads and reconnects                | No GPU wake, evaluation dispatch or infrastructure commands                                    |
| Historical run / active old release              | Original metrics remain original; unsupported monitoring not backfilled or retrofitted         |

Record per journey: command/prompt, dataset/cell and policy IDs, fixtures/hashes,
runtime/environment, worker release, request keys, check/checkpoint IDs, results,
errors/recovery, timings, cost coverage and remaining limitations. A mocked provider
journey is labelled mocked, not proof of hardware or serving behaviour.

Live qualification should include a bounded chat classification run on the existing
KYC source, one structured-output task, and native distribution/ordinal fixtures.
KYC fixtures verify transport/scoring mechanics; their disputed target semantics
cannot be used as proof of KYC usefulness. Include clean learnable controls and
deliberately unlearnable/imbalanced cases to test the finding logic. Re-run each
repaired failing journey and repeat key recovery/clean paths with stable seeds.

The user subsequently authorised implementation and a fresh $100 allowance for
qualification and relaunch, separate from earlier project spend. Reserve headroom
for incomplete cost components and
provider cancellation latency. Estimates and sampled metering cannot promise an
exact all-in invoice cap; stop submitting before the remaining allowance is unsafe.

Run scoped backend/MCP/provider integration tests and frontend tests with logs,
then typecheck, lint, design/contrast checks, migration checks and generated-client
verification. Run `pre-commit` for implementation-owned files only. Do not sweep
the existing dirty tree into this change. UI verification uses the local browser
only for explicitly scoped UI work, not as a fallback for MCP errors.

## Deployment and documentation

- Preserve existing job/artifact/evaluation rows with data-safe migrations; do not
  relabel historical checkpoints as newly verified.
- Remove the epoch-only/final-only new-job chat path in favour of the shared policy,
  with explicit epoch/off settings still available as real requested modes. Do not
  create legacy flags or parallel monitoring systems.
- Add schemas, serialisers and typed client generation (`make generate_api_client`).
- Deploy a new immutable Modal training release after asset changes, verify its
  capabilities/fingerprint, then configure new launches. Old pinned calls continue
  on their original release. Local hot reload is not a GPU deployment.
- Verify fresh Celery task registration/routing, reconciler recovery and terminal
  seed-demo records. External checks must not run automatically from demo data.
- Update `AGENTS.md`, relevant architecture/MCP guidance, canonical bundled training
  and evaluation skills, `PRODUCT.md` without changing its schema, and sibling docs
  `../docs/models/training.mdx` and `../docs/platform/mcp.mdx`. Sync installed local
  guidance through the supported setup flow; bump the bundled SDK when required.
- Confirm local MCP discovery reports localhost:8000, new contract/capabilities and
  exact release readiness. Inspect from a repository-free CLI environment without
  credential injection; never try keys against a different endpoint.
- Do not commit, push, create PRs, activate models or deploy paid workloads merely
  because the plan exists; those follow implementation/publishing authorisation.

## Definition of complete

A coding agent can configure a run from a normal user request, discover monitoring
limits, inspect credible development evidence before training ends, trace any metric
to samples and an exact checkpoint, understand a missing/failed check, cancel within
its authority and recover retained evidence through MCP alone. The Console shows
the same facts. The system does not confuse execution health with task success,
does not touch the final test set for tuning, and cannot hide the failure that
prevented measurement.

Coverage is reported against the test matrix and qualified provider/objective
combinations, never as a claim of universal error-free training.

## Technical references

Step-based evaluation, initial evaluation and checkpoint selection use supported
[Transformers Trainer facilities](https://huggingface.co/docs/transformers/main_classes/trainer#transformers.TrainingArguments).
The distinction between masked-token training metrics and generated-answer quality
follows the documented [TRL SFT metric definitions](https://huggingface.co/docs/trl/sft_trainer#logged-metrics).
