# Training monitoring implementation evidence

Status: live qualification in progress; not a full-provider completion claim.

## Authorisation and exact input

- Fresh $100 allowance explicitly authorised for implementation and relaunch.
- Local MCP: `http://localhost:8000/api/mcp/`.
- Project: `e18b29b5-915d-45a7-80cd-77ffe6559205`.
- Original cancelled job: `6555c389-fd8a-456a-a173-a753fae0c75b`; unchanged.
- Train cell: `167fe6e8-df72-42f9-85a5-c7d95a47f189`, dataset
  `554344b4-211d-5cfa-86fc-8252eb41ed3b`, 224,000 rows, fingerprint
  `e8036c69e69ab89bc00a5f75999a2d1902fd810d36fd0284acb1ccc1c2ed5ee8`.
- Source labels verified by MCP SQL: enhanced 93,834; high_risk 75,092;
  standard 36,712; prohibited 18,362. These are existing labels, not platform
  interpretations. This fixture does not establish broad KYC task suitability.
- Original final-test cell remains excluded from development monitoring.

## Reproduction and observed checks

CPU qualification (offline temporary uv environment, no model downloads):

```sh
HF_HUB_OFFLINE=1 UV_CACHE_DIR=/tmp/overmind-uv-cache PYTHONPATH=. MAX_LENGTH=32 MODEL_ID=qualification/tiny uv run --offline --no-project --with torch --with 'transformers>=5.2,<6' --with peft --with accelerate --with jsonschema --with numpy --with celery tests/evidence/qualify_training_monitor.py --output /tmp/overmind-monitoring-counter-fixed
```

Observed: six real tiny-model LoRA updates with and without monitoring produce
bit-identical weights (maximum difference 0). Checks at steps 0/2/4/6 and full
final validation complete. Four native tiny-Llama LoRA checkpoints pass actual
probability-replay reload verification. Native GPU admission/base-identity checks
are mocked in this CPU fixture; this does not qualify CUDA/Unsloth.

Failures reproduced and repaired before GPU submission:

- Nested `Trainer.evaluate` consumed the outer control flags and lost training
  log points. Preserve and restore the outer control object.
- Training-reference evaluation used a non-evaluation prefix, which could drain
  training accuracy counters. Use evaluation-mode counters for both fixed probes.
- Modal image build added pip packages after local attachments. Install pinned
  JSON Schema support before attachment.
- Native readiness resolved policy before inferring the objective, conflicting
  with explicit baseline settings. Resolve after objective inference.
- Evaluator readiness ran before eval-dataset validation and project ownership
  checks. Reorder checks without exposing foreign-project evaluator details.

Backend regression logs: `/tmp/overmind-monitoring-regression.log` initially
959 passed, 27 failed, 4 skipped, 2 socket-restriction errors. After repairs,
`/tmp/overmind-monitoring-regression-retest.log`: 202 passed, 1 stale expectation
failed. That expectation was corrected; `/tmp/overmind-monitoring-acceptance.log`:
34 passed. Scoped local-socket rerun `/tmp/overmind-monitoring-analytics.log`:
2 passed. These overlapping runs are not an additive unique-test total.

Frontend: `/tmp/overmind-monitoring-ui-current.log`: 99 passed. Expanded evidence
view red test: 2 failed/2 passed; repaired run
`/tmp/overmind-monitoring-ui-evidence.log`: 4 passed. Typecheck separately verified.

## Live qualification

Worker: `overmind-sft-554fe3c4952253704e47be26`, environment `overmind-dev`.
Local and deployed release identities match, recorded in
`/tmp/overmind-monitoring-counter-release.log`.

Job `d80f8491-f5f2-4bf4-85f1-e27a3053e51d` was launched through MCP with stable
request key `training-monitoring-20261010-chat-qualification-1`:
Qwen/Qwen3.5-4B, LoRA rank32/alpha64/dropout0/all-linear, LR0.0002, batch16,
warmup0.05, context4096, one epoch bounded to eight updates and 1,200 seconds.
Development split: 20%, existing content/group-aware splitter. Monitoring every
two steps, 128 loss rows, 32 reference rows, 12 generated-label rows, 64 output
tokens, strip_thinking normalization, all four existing labels, failure policy
stop, checkpoint selection last. Whole groups may exceed requested sample size.

Read live evidence through a freshly discovered catalogue (credentials are read
internally from the address-bound Codex connection and never printed):

```sh
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --offline python tests/evidence/inspect_training_monitoring.py d80f8491-f5f2-4bf4-85f1-e27a3053e51d --project e18b29b5-915d-45a7-80cd-77ffe6559205 --output /tmp/overmind-monitoring-live.json
```

The estimate is $1.21 for the full recipe, not an exact eight-step quote, and
excludes monitoring/storage/preparation components. At the observed H100 rate of
$3.95/hour, the 20-minute call bounds GPU time to approximately $1.32; this is not
an all-in invoice cap. Reserve substantial headroom within the fresh $100.

External before/after benchmarks are explicitly off for qualification: the
original evaluator definitions have missing compiled checklists, and the exact
Qwen3.5-4B OpenRouter route is not listed. No substituted model, new hosted-base
fallback, activation or changes to the original job were authorised by this test.

## Remaining qualification and capability gaps

- Native CUDA/Unsloth, structured generation and corrected Qwen3.5-4B training
  execution are verified below. The full replacement completed GPU training;
  its separate deployment readiness is being checked (latest observations below).
- Baseten GPU execution is not qualified by the Modal or CPU fixtures.
- Metered judges, independent challenge suites, declared-strata/slice sampling,
  general tool-contract scoring and metric-selectable checkpoint policies
  beyond development loss are not implemented by the monitoring contract.
  Agent-declared exact JSON field comparisons are implemented below; fuzzy entity
  matching, inferred fields and tool execution remain unsupported.
- Intermediate checkpoint bytes cannot yet be exported with the final-deployment
  CLI handoff; retained checkpoints are protected from cleanup rather than pruned.
- Group identities absent from prepared row artifacts cannot be reconstructed by
  the sampler; it groups by retained group/key only. Do not claim declared-case
  stratification or population-representative confidence intervals.
- Full chat optimizer resume and exhaustive packed attention-boundary
  qualification remain unproven. Scheduler hysteresis is qualified below in
  deterministic restart tests; the active GPU run retains its earlier scheduler.
- UI task-level generation contract editing remains MCP-only. Desktop/mobile
  evidence display and the installed local connection were verified.
- Product analytics now has receipt-backed result-availability, check-completion,
  coverage/timing and verified-checkpoint events, plus the evidence-open UI event.
  Unobserved-time attribution, failure-comprehension timing, duplicate-provider-work
  incident aggregation and explicit user-feedback collection remain incomplete.

## Subsequent qualification and repairs

The original live-qualification section above records the first attempt. The
Qwen retest and final native qualification use the verified worker
`overmind-sft-fc9367fab2aa71c8f5f689ca`, environment `overmind-dev`:

- Release: `fc9367fab2aa71c8f5f689ca6c2658e207ab0d6e6de8175cc8f8d3435bfb2c76`.
- Processor: `260747cffd80ed60096d0c417e356bd49a42e7ff71aebb3159c705d4285ce92e`.
- Training: `e3ba549b94e022722297243175971b430eb368b1316d5ebac2970386290b28c5`.
- Deployment/verification: `/tmp/overmind-monitoring-chart-deploy.log` and
  `/tmp/overmind-monitoring-chart-release.log`.

### Native probabilities and ordinal means

`tests/evidence/native_monitoring_fixture.py` generates 48 synthetic controls:
24 probability-distribution targets and 24 ordinal-mean targets, with weights.
These are runtime tests, not evidence of domain-model quality.

- First job: `5416068b-046e-4004-aaba-f4dd31e1fc47`, succeeded, recorded GPU $0.1343.
  Completion incorrectly dropped monitoring timings and frozen-plan identity.
- Retest: `2fa5a342-54e8-4a01-84cf-876591d6141e`, succeeded, recorded GPU $0.1417.
  Checks 0/2/4/6 and full final step 6 all retain paginated probability evidence.
  Three checkpoints reload with 36/36 probability vectors identical (max error 0).
  Completion now preserves sample identity and timings.
- Fixed-sample loss: 0.361641 → 0.414742 → 0.337658 → 0.348201.
  Full final loss 0.388541 uses 12 rows rather than the eight-row fixed sample.
- Fixed-sample Brier improved 0.005865 → 0.001803, while ordinal-mean MAE worsened
  1.098987 → 1.121590. Hard-label accuracy stays unmeasured: no one-hot targets.
- Optimisation 3.54 s; monitoring 9.37 s. Frequent qualification checks deliberately
  dominate this tiny run and do not demonstrate the adaptive overhead target.
- Observation: `/tmp/overmind-native-monitoring-retest.json`.
- Final deployed-runtime retest: `2bb039d4-9b23-48cc-b51b-8c12b2f6bd8d`, succeeded,
  recorded GPU $0.1183. All five loss values exactly match the prior native run;
  all five checks retain evidence including original weights. Three checkpoints
  again replay 36/36 probabilities with maximum error 0. Observation:
  `/tmp/overmind-native-monitoring-final.json`. Repeating the identical MCP launch
  request returns the same job without another training submission. A misleading
  fixed "queued" summary was reproduced on a running job and fixed to use its
  recorded status; the live retry then returned "Fine-tuning job: running."

### Structured generation

`tests/evidence/structured_monitoring_fixture.py` generates 48 constant-JSON controls.
Job `fb3e1717-e490-4e59-a6f2-8a68efe127e4` completed four updates and retained two
reload-verified adapter checkpoints. The job succeeded at 07:37:55 UTC; deployment
`ca98e0a1-fa58-42a0-98f5-cf8e38bf9dea` is ready, without activation. Its provider
prewarm receipt measured 306.88 seconds through verified generation. Training
completion and this separate cold-start interval are not conflated.
Fixed-sample loss 4.281938 → 3.281071 → 2.164815; full final 2.294988.
All four generated examples satisfy the declared JSON Schema at every check,
including before training. This is 100% contract adherence on a trivial control,
not evidence that training improved task quality. GPU worker duration 120.14 s;
recorded training/deployment charges are not yet final. Final observation:
`/tmp/overmind-structured-monitoring-complete.json`.

This run exposed training-reference loss being emitted into the ordinary
development curve. The callback now distinguishes evaluation splits. The real
CPU training replay reproduces the defect before the fix and verifies afterwards
that plotted development points match check receipts exactly, without changing
the optimisation sequence (maximum weight difference 0).

### Qwen3.5-4B failure and retest gate

The first job completed only its initial check: development loss 3.661728,
training-reference loss 3.589299. All 13 generated examples exhausted the 64-token
reservation; accuracy remains null, coverage 0, technical errors 13. Failed
generations must not erase the known reference-label distribution; this is fixed.

Its first training forward pass then exhausted the H100's memory. The retained
worker log records a 1.98 GiB allocation failure with 1.96 GiB free. Generation
changes Unsloth inference/checkpointing state beyond `.training`; the monitoring
context had not restored that state. The CPU fixture now reproduces disabled
checkpointing and verifies restoration of checkpointing, cache, RNG and training
modes. GPU confirmation is still required before the full relaunch.
The failed attempt's recorded GPU cost is $0.2230.

Safe worker failures now retain a fixed `gpu_memory_exhausted` classification,
without publishing raw logs or source data. Historical error text is unchanged.
Retest recipe: `/tmp/overmind-chat-monitoring-retest-recipe.json`; preparation
`71119037-8d4b-4410-bf61-914561068b6e` completed all 224,000 rows with zero
incompatible rows, 10,825,864 input tokens and 1,830,380 supervised tokens.
Measured CPU preparation window: 909.55 seconds. The same-recipe bounded retest
was submitted through MCP as `a275d51f-6b30-41e8-b343-1132a5192bb2`, with stable
request key `training-monitoring-20261010-chat-qualification-2`. Submission is not
proof that the GPU failure is repaired; optimizer updates remain the next gate.

The retest subsequently reached the step-2 development check and checkpoint save,
past the first attempt's failure before any optimizer update. Its step-0 loss
exactly matches the first attempt, 3.6617279052734375. Initial generation still
exhausted 64 tokens on all 13 examples. This confirms forward training resumed,
not completion or task quality. Mid-run observation:
`/tmp/overmind-chat-monitoring-retest-mid.json`.

### Reporting boundary repairs and full-run release

Two isolated classification regressions reproduced undefined precision being
reported as zero. Precision now remains null when there are no predictions;
recall/F1 still record missed represented labels as zero. Paired classification
changes now exclude references outside the frozen label contract, matching the
single-check denominator instead of counting an unsupported label as an improvement.

The real CPU journey reproduced completed sample construction remaining the current
stage during the first validation forward pass. Evaluation now announces zero
completed batches before that pass. The replay passes with bit-identical trained
weights (maximum difference 0), separate development/reference plots and native
checkpoint probability verification. Logs: `/tmp/overmind-monitoring-stage-red.log`,
`/tmp/overmind-monitoring-stage-fixed.log`,
`/tmp/overmind-monitoring-undefined-precision-red.log`,
`/tmp/overmind-monitoring-paired-contract-red.log`, and
`/tmp/overmind-monitoring-metric-contract-fixed.log` (37 passed).

These fixes were deployed in `overmind-sft-4146ef10ad26182d15f35d36`:

- Release: `4146ef10ad26182d15f35d3619dae380527444ff0eb6d27298eeef11c6d88407`.
- Processor remains `260747cffd80ed60096d0c417e356bd49a42e7ff71aebb3159c705d4285ce92e`.
- Training: `d76225a8c17a97eb912e8741c566a21042345f5e55fa7abce026b3df13f6aaa0`.
- Matching local/provider identity: `/tmp/overmind-monitoring-metric-release.log`.
- The active Qwen qualification remains pinned to its earlier immutable release.

The full original-data relaunch passed readiness and is preparing on this release:
`7969e86d-b22d-4a64-b8fd-4cc231a89117`. Recipe:
`/tmp/overmind-kyc-full-relaunch-recipe.json`; preflight:
`/tmp/overmind-kyc-full-final-preflight.json`. It retains the original one-epoch,
rank-32 recipe and exact 224,000-row source, with adaptive monitoring and a
two-hour provider timeout. Estimated training GPU spend is $2.3125, excluding
monitoring/preparation/storage; it is not an all-in cap. Preparation subsequently
completed with zero incompatible rows, the identical token-artifact checksum
`8e126e0d4d3da6aeff0f626fc3b7a378240ed70b5a6c70add99da5310510d42b`, and
881.60 seconds of measured CPU preparation time. The original job and serving
alias remain unchanged.

### Qwen GPU qualification outcome and request recovery

Retest `a275d51f-6b30-41e8-b343-1132a5192bb2` completed all eight optimizer updates
and all 44,800 final development rows. The provider reports success; at the saved
observation, the platform job was separately deploying its final artifact.
Four checkpoints (steps 2/4/6/8) passed strict saved-weight reload. This verifies
inference weights, not full optimizer resume. GPU duration: 922.75 seconds;
optimizer time: 326.13 seconds; monitoring time: 515.27 seconds. The aggressive
qualification cadence is not evidence that the adaptive overhead target is met.

Fixed-probe loss: 3.661728 → 1.899302 → 0.387116 → 0.198016 → 0.165428.
Full-development loss: 0.164467. Final generated probe: 6/13 completed, 0/6
correct, one invalid label, seven output-limit failures. Its seven source groups
do not include the `prohibited` label. This is weak generated performance on the
probe despite low loss, not a statement of whole-corpus zero accuracy. No
capability, label or source content was rewritten to improve these metrics.
Observation: `/tmp/overmind-chat-monitoring-retest-complete.json`.

An identical MCP launch after deploying the newer runtime returned the same job.
Additional fault-injection journeys reproduced credits/quota blocking retrieval of
an already-created job. New-launch admission now occurs after project-locked
request-key recovery in both REST and MCP. Identical requests recover without
another submission; changed recipes conflict; genuinely new jobs still require
credits/quota. Regressions: 209 passed in
`/tmp/overmind-monitoring-admission-fixed.log`. Earlier metric/worker regression:
167 passed in `/tmp/overmind-monitoring-latest-regression.log`.

Installed CLI verification from `/tmp`, without injected credentials:

```sh
overmind connection check --project-id e18b29b5-915d-45a7-80cd-77ffe6559205 --json
```

Both MCP and transfer report ready against `http://localhost:8000`, contract 6.1,
account scope, uploads and exports enabled. Saved observation:
`/tmp/overmind-monitoring-local-connection.json`.

### Full relaunch submitted

- Job: `d41d3526-d703-499b-aaee-663c2da0908d`.
- Group: `9f09f5ae-0d5f-4aa4-9004-692f77cbcd02`.
- Name: `KYC relaunch · measured development monitoring`.
- Request key: `training-monitoring-20261010-kyc-full-relaunch`.
- Frozen runtime: `4146ef10ad26182d15f35d3619dae380527444ff0eb6d27298eeef11c6d88407`.
- At the saved observation, the job was transferring/verifying prepared data,
  not yet reporting optimizer updates. Do not equate submission with completion.
- Final recipe verification: `/tmp/overmind-kyc-full-relaunch-verified.json`.
- Follow-up: current-thread heartbeat `assess-kyc-monitoring-relaunch`, every ten
  minutes, meaningful changes only, no activation or additional paid launches.

Receipt verification found the credential filter also removed the numeric
`max_new_tokens` field. The numeric-only safe-measurement allowlist now preserves
it while still removing strings, objects, booleans and credential fields. The
finetune resource also returns its recorded group ID for an exact Console link.
Live verification returns the declared 128-token limit and correct group above.
Resource/recovery/monitoring journeys: 33 passed in
`/tmp/overmind-monitoring-inspection-retest.log`; catalogue/recovery checks:
24 passed in `/tmp/overmind-monitoring-catalog-recovery-final.log`.

Local connection was rechecked after the catalogue change, with ready MCP and
transfer states: `/tmp/overmind-monitoring-final-local-connection.json`.
Catalogue fingerprint:
`fec5768adea57873e1fa55d7de3e000f8644263a7fd0d0c68c272694116c6d38`.

### Repeated local commands

```sh
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --offline python tests/evidence/launch_monitored_training.py --recipe /tmp/overmind-chat-monitoring-retest-recipe.json --output /tmp/overmind-chat-monitoring-retest-preflight.json
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --offline pytest -q tests/test_training_failure.py tests/test_training_monitoring.py tests/test_training_monitoring_native.py tests/test_training_monitoring_receipts.py tests/test_training_monitoring_journey.py tests/test_training_telemetry.py tests/test_finetuning_observe.py tests/test_finetuning_monitor.py tests/test_finetuning_eval_schedule.py tests/test_mcp_finetuning_receipts.py
```

Observed: 165 passed (`/tmp/overmind-monitoring-final-scoped.log`). The six scoped
UI tests pass, plus typecheck/Biome. UI fixes expose contract pass rate separately
from accuracy, native probabilities/targets and full prediction receipts. Desktop
browser checks confirmed persisted timings and native evidence; mobile wrapping
was corrected after the responsive review. Existing sample identities, original
job state and project-scoped passive access remain intact.

MCP compact job inspection now includes schema/exact-match pass rate as well as
classification accuracy; the live structured run returns pass_rate=1 while
accuracy remains null. The shared MCP/REST receipt journey caught the omission
and passes after the fix (10 tests). Retry receipt regressions also pass (7 tests).
These runs overlap earlier suites and are not additive unique-test counts.

Preparation currently binds the whole runtime release, so training-only worker
changes re-tokenize the 224,000-row source. This performance cost remains open;
no unsafe cross-release token reuse was introduced.

### Scheduler boundary and adaptation qualification

Failure cases before changing the scheduler: a scheduled check at the end of a
short run can report a budget conflict despite normal completion; explicit step
and epoch schedules can acquire adaptive-budget warnings; minor timing noise can
oscillate cadence; a sudden cost change can bypass a bounded cadence change; and
restarting can lose the prior interval used for that decision. Isolated scheduler
tests exercise these deterministic boundaries without spending GPU time. They do
not establish live runtime qualification of a later release.

The full relaunch acknowledged a single GPU submission at 08:18:25 UTC, remote
receipt `ft-d41d3526-d703-499b-aaee-663c2da0908d-0bd810ac:fc-01M4JE8M5KG2KY8WBM154XQ004`.
An earlier local hot-reload interrupted staging before dispatch. Its durable
recovery retained the same job/request key and did not resubmit acknowledged GPU
work. Observation: `/tmp/overmind-kyc-full-first-check.json`. The initial validation
was running with measured batch progress; there were not yet optimizer results.

A second visibility failure is checked before repair: completed development loss
and coverage remain unpublished while the reference/generation stages run. A
worker disconnect in those stages can therefore hide already measured results.
The runtime journey must publish each completed measurement while retaining a
running overall check; a later failure must preserve, not retract, those facts.

Both failures were reproduced before implementation. Regressions now pass: 80
tests in `/tmp/overmind-monitoring-partial-fixed.log` (10 existing test-JWT key
warnings). CPU tiny-model replay passes at
`/tmp/overmind-monitoring-cadence-qualified/result.json`, with identical optimizer
weights (maximum difference 0) and native probability-reload checks. Its log is
`/tmp/overmind-monitoring-cadence-qualified.log`. Red logs:
`/tmp/overmind-monitoring-schedule-red.log` and
`/tmp/overmind-monitoring-partial-red.log`.

Formula 2 uses a 20% cadence deadband and half/double bounds after its first
measured interval. It records candidates, effective intervals, normal stop reasons
and actual overhead conflicts separately. These worker changes do not alter the
already pinned full relaunch; their release qualification is separate.

Deployed release `5c733dfd2858089fb54d55a82c2ab4783bff53a72f0af83cea7d12562b5dafc5`
(`overmind-sft-5c733dfd2858089fb54d55a8`) matches the local identity, verified in
`/tmp/overmind-monitoring-cadence-release.log`. No additional training was launched
for this deploy; the full run remains pinned to `4146ef10…`. Scoped pre-commit
passed in `/tmp/overmind-monitoring-cadence-precommit.log`.

The corrected Qwen qualification subsequently reached platform `succeeded`,
confirmed through MCP. A local read-only cost audit of this request-key family
records $0.6173 across the failed Qwen and three native jobs; both successful chat
jobs and the active full run still have null cost records. This is a partial
ledger figure, not total spend or evidence that remaining components are free.
Audit: `/tmp/overmind-monitoring-budget-jobs.json`. Retain provider-duration
estimates and substantial headroom when enforcing the fresh $100 ceiling.

Before repair, the chat success path transitions directly to deployment and
omits the training-charge hook used by native success/failure/cancellation. The
provider usage receipt exists, so this is not missing worker telemetry. Regression
journey: completed chat training must record deduplicated worker GPU time before
deployment, repeated observations must not charge twice, and deployment failure
must not erase the training receipt. This uses a mocked provider, not a new GPU run.

The journey failed before the hook was added, then passed with the related suites:
54 tests, 10 test-JWT warnings, `/tmp/overmind-monitoring-chat-billing-fixed.log`.
Exact affected local jobs were reconciled from their retained usage: Qwen retest
$1.0125 and structured retest $0.1318. No new provider calls or payment-method
charges were submitted. The six completed qualification jobs now record $1.7616
in training GPU costs, excluding the active full run and separately estimated or
unreported components. Receipts: `/tmp/overmind-monitoring-recovered-charges.json`.
Pre-commit passed after test formatting in
`/tmp/overmind-monitoring-billing-precommit-confirmed.log`.

### Full relaunch initial result

At 08:25:30 UTC, step 0 completed: development loss 3.8397939205169678 over all
2,048 frozen loss-probe rows. Generation coverage was 30/64 (46.875%); exact-label
accuracy on those 30 completed examples was 0. Thirty-four examples could not be
scored. Retained findings identify incomplete coverage and undeclared generated
labels. The check took 360.47 seconds, reported separately from optimizer time.
This is a pre-update baseline, not evidence of learned improvement or whole-corpus
accuracy. Training then entered the first of 133 planned optimizer steps. The
next interim check is pinned at step 14. No optimizer result was yet available
at that observation.

The follow-up remains active for both the live outcome and unfinished implementation;
run completion alone does not close the remaining plan. Local MCP/transfer readiness
was reverified from `/tmp` in `/tmp/overmind-monitoring-cadence-local-ready.json`.

### Product-experience delivery events

Failure cases recorded before implementation: repeated collector polls or later
evidence attachment must not duplicate result/completion/checkpoint events;
rolled-back receipts must not escape to analytics; disabled self-hosted analytics
must make no network calls; analytics failure must not fail a training receipt;
inputs, outputs, labels, free-text errors and package paths must never enter the
product event. Time to first usable result is the earliest recorded server
availability event per job, not the provider timestamp or proof of user delight.
The end-to-end fixture uses a local HTTP ingest sink with the installed PostHog SDK.
These delivery events do not yet measure human comprehension or feedback.

Implemented `training_result_available`, `training_check_finished` and
`training_checkpoint_available`, with stable event IDs after transaction commit.
Check `facts.delivery` retains collector observation times even when analytics is
disabled. Receipt ingestion/cancellation share the job lock. Event properties are
an explicit scalar allowlist; missing resume information remains null, not false.
Transport is asynchronous and best-effort, with bounded queues/timeouts and worker
shutdown flushing. There is no durable analytics outbox; abrupt process death can
lose an event without losing the authoritative training receipt.

The local API loaded the module with analytics disabled; no local project data was
sent to a hosted analytics service. PostHog app/dashboard configuration was not
changed. Delivery was qualified against a real disposable loopback HTTP sink.
Initial and boundary failures were repaired, including commit-delay undercounting:
elapsed time to availability is calculated after commit, while collector receipt
timestamps retain their earlier clock. Rolled-back transactions emit nothing.

Reproduce using the repository test environment (local socket access required):

```sh
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --offline pytest -q tests/test_training_experience.py tests/test_training_monitoring.py tests/test_training_monitoring_receipts.py tests/test_training_monitoring_journey.py tests/test_finetuning_monitor.py tests/test_finetuning_observe.py tests/test_mcp_analytics.py
```

Observed: 81 passed, 10 existing test-JWT warnings, in
`/tmp/overmind-training-experience-commit-fixed.log`. Red evidence:
`/tmp/overmind-training-experience-boundaries-red.log`,
`/tmp/overmind-training-experience-timing-red.log`, and
`/tmp/overmind-training-experience-commit-red.log`. Earlier overlapping regression
had 80 passes; these are not additive counts. No UI or OpenAPI schema changed.

### Live checkpoint and first interim comparison

At the 08:35 UTC observation the full run had completed 16/133 optimizer updates.
The step-14 check retained the same 2,048-row development probe: loss
3.8397939205169678 → 0.1512758582830429; training-reference loss
0.14995920658111572, gap 0.001316651701927185. Teacher-forced token accuracy was
0.9273041200285782. This is encouraging supervised-objective progress; generation
was not requested at this interval, so no improved task-accuracy claim is justified.

The step-14 adapter passed strict reload verification; optimizer resume remains
unsupported. Its check cost 10.18 seconds versus 360.47 seconds for the initial
check, which included 64 generations. At observation, cumulative optimizer time
was 539.16 seconds and all monitoring time 370.64 seconds. Initial work dominates
that aggregate; it must not be confused with periodic-check overhead. The recorded
schedule next checks step 24, using measured median optimizer time 30.06 seconds
and no reported conflict. The active release still uses formula 1.

No non-finite loss or gradients were observed in 16 logged updates; 12 exceeded
the configured clipping threshold. Skipped-update counts remain unavailable, not
zero. The run's current compute estimate was $1.0882 plus $0.1083 preparation,
not a terminal charge or an all-in cost. Completed qualification GPU charges remain
$1.7616. No new provider work or activation was submitted during this heartbeat.

Snapshot: `/tmp/overmind-kyc-monitoring-heartbeat-0828.json` (filename identifies
the heartbeat trigger, not snapshot time). Installed local MCP and file transfer
both passed from the actual workspace and `/tmp`, without injected credentials:
`/tmp/overmind-monitoring-workspace-ready.json` and
`/tmp/overmind-monitoring-outside-ready.json`.

### Declared JSON field comparisons

Failure cases specified before code: schema-valid output with wrong content must
not pass an extraction check; missing reference fields must not be scored as model
failures; incomplete generation must remain technical failure; null must differ
from missing and booleans from numbers; large integers/decimals and array order
must survive comparison; duplicate JSON keys must not select an arbitrary value;
escaped/nested pointers must resolve; empty, duplicate, malformed or oversized
field contracts must fail before launch; paired comparisons must exclude
unscorable references. A later boundary test reproduced an additional supplied
schema/label grader being silently ignored; `json_fields` now rejects that mix.

MCP-ready through the existing policy, not a new tool: `generation.kind=json_fields`,
with 1–64 explicit JSON Pointers under `fields` (1,024 characters/64 segments max).
Empty string selects the complete JSON document. References remain outside model
inputs; only declared fields are compared and no tools execute. Per-field and
complete-example pass rates retain separate coverage, technical failures and
unscorable-reference counts. Scorer identity is `json_fields:1`. Raw prediction
and reference text remain available in the authenticated retained evidence.

The deterministic journey uses five controlled examples: one correct, one wrong,
one invalid output, one missing reference and one failed generation. It records
3/5 scorable examples, 1/3 complete-example passes, 1/3 risk-field passes and
2/3 case-id passes; MCP and REST read identical persisted results with provider
access prohibited. The paired comparison excludes the missing reference and
failed generation. This is mocked generation, not a GPU-quality result.

Reproduce in the local test environment:

```sh
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --offline pytest -q tests/test_training_field_checks.py tests/test_training_monitoring.py tests/test_training_monitoring_native.py tests/test_training_monitoring_receipts.py tests/test_training_monitoring_journey.py tests/test_mcp_model_catalog.py tests/test_mcp_catalog.py tests/test_mcp_finetuning_receipts.py tests/test_mcp_prompts.py tests/test_mcp_resources.py
HF_HUB_OFFLINE=1 UV_CACHE_DIR=/tmp/overmind-uv-cache PYTHONPATH=. MAX_LENGTH=32 MODEL_ID=qualification/tiny uv run --offline --no-project --with torch --with 'transformers>=5.2,<6' --with peft --with accelerate --with jsonschema --with numpy --with celery tests/evidence/qualify_training_monitor.py --output /tmp/overmind-monitoring-fields-final-qualified
```

Observed: 148 backend/MCP tests passed, eight component tests passed, typecheck,
Biome, design/contrast/control checks, mechanical detector and scoped pre-commit
passed. Logs: `/tmp/overmind-training-fields-final.log`,
`/tmp/overmind-training-fields-ui-fixed.log`,
`/tmp/overmind-training-fields-typecheck.log`,
`/tmp/overmind-training-fields-design.log`,
`/tmp/overmind-training-fields-detector.json`,
`/tmp/overmind-training-fields-precommit.log`. Red logs:
`/tmp/overmind-training-fields-red.log`,
`/tmp/overmind-training-fields-ambiguity-red.log`,
`/tmp/overmind-training-fields-ui-red.log`. Counts overlap earlier runs.

The real tiny-model CPU callback now also runs generated field checks, preserving
bit-identical weights versus unmonitored training (maximum difference 0). Its
two-token generation cap exhausted on all examples, correctly retaining null
pass rates and zero coverage. This verifies callback/state preservation and
failure evidence, not successful extraction or GPU qualification. The same
fixture verifies native probability-checkpoint replay. Artifact:
`/tmp/overmind-monitoring-fields-final-qualified/result.json`.

The interface skill kept the Console change limited to classifying field results
as contract pass rate, with no layout redesign. Browser review was rejected by
the MCP-only permission guard and was not retried or bypassed. Visual verification
of this change remains unperformed and requires explicit UI authorisation.
Existing generic metrics/evidence inspectors expose all field facts. REST remains
unchanged (existing JSON policy/metric fields), so no generated-client edits or
database migration were needed.

One immutable worker deployment, no new training/preparation job:
`overmind-sft-326116cd9b96d3ee6de4d525`, release
`326116cd9b96d3ee6de4d5254e76b34897599c89de53637ad72a212fdcf016c8`,
environment `overmind-dev`. Provider metadata matches the local release:
`/tmp/overmind-monitoring-fields-deploy.log`,
`/tmp/overmind-monitoring-fields-release.log`. The active full run remains on
`4146ef10…`; this does not retrofit its policy or claim GPU qualification for
the new scorer. Bundled training guidance was synced through `overmind skills sync overmind-training --ide codex` and verified byte-identical.
Local MCP/transfer are ready on contract 6.2, 69 tools, catalogue
`c6475173209deb38b2d2b9130ab27271a23a1eb77850c62a2eae43802251398a`:
`/tmp/overmind-monitoring-fields-local-ready.json`. Live catalogue discovery
advertises `json_fields`. Architecture, MCP guidance, product and sibling docs
were updated; broader unsupported capabilities above remain explicit.

### First post-update generated KYC results

At 08:46:44 UTC the acknowledged full run was still running at 38/133 optimizer
updates, with no job error. Check 34 completed with 2,048/2,048 loss coverage,
development loss 0.1180869862, training-reference loss 0.1087291613 and gap
0.0093578249. Teacher-forced token accuracy was 0.9384972613. Three retained
checkpoints (14/24/34) passed strict reload verification; optimizer resume remains
unsupported. No non-finite observations appeared in 38 logged updates, and
clipping-threshold exceedances remained 12. Skipped updates remain unmeasured.

Generated quality remains weak despite low loss: 64/64 completed without output
limit errors or invalid labels, but only 24/64 correct (37.5%), macro F1 0.1964.
Predictions were enhanced 53 and high_risk 11; neither standard nor prohibited
was predicted. References were enhanced 27, high_risk 17, standard 17, prohibited
3\. A constant enhanced prediction would score 27/64 (42.1875%) on this probe.
The sample is small, grouped and not a declared-strata population estimate; do
not interpret these figures as a production-risk guarantee or final benchmark.

The paired baseline/candidate comparison has only 30 comparable completions:
11 improved, zero regressed, 19 unchanged; 34 baseline failures remain unpaired.
Its descriptive bootstrap interval is not a significance or generalisation claim.
The model has learned output format but has not demonstrated balanced task success.
The existing findings list does not flag absent predicted classes; the underlying
class/prediction distributions expose the issue. No class-collapse diagnosis or
automatic recipe change was added to the live run.

Monitoring usefulness is now concrete: objective loss alone would miss this
quality problem. Initial monitoring cost 360.47 seconds; three interim checks
cost 10.18, 10.31 and 32.22 seconds (52.71 seconds total) against 1,202.09 seconds
of recorded optimization. Interim checks comprise about 4.2% of those combined
intervals; including the initial check raises measured monitoring share to about
25.6%. Checkpoint/startup/other overhead is not included in that ratio. Next check
44 uses measured ~30-second optimizer steps; no scheduling conflict was recorded.

Active training compute estimate: $1.8554; preparation estimate: $0.1083. Current
recorded GPU charge is still null, not zero. Completed qualification GPU charges
remain $1.7616, with deployment/storage/network/unreported costs separate and
substantial reserve under the fresh $100 allowance. No new training/evaluation,
activation or serving alias changes. New-release build/metadata calls were not
individually reconciled into an all-in invoice amount. Snapshot:
`/tmp/overmind-kyc-monitoring-heartbeat-0840-later.json`.

### Receipt-backed quality findings and loss-only summary recovery

Failure cases recorded before implementation: a loss-only check must not hide the
last generated result; new attempts with reset steps must not select an older
attempt; absent predicted classes and a below-majority result must retain their
scored denominator and frozen labels; missing, malformed, inconsistent or
overflow-sized metrics must produce inconclusive analysis; generation failures
must not inflate reference supports; repeated/late collection must not rewrite
worker evidence, duplicate finding identities or invoke providers on reads.

MCP-ready on contract 6.2.1, with the same 69 tools/catalogue fingerprint.
`training_quality.py` derives `facts.assessment` during collection. The rule verifies
policy identity, label order, matrix counts, scored supports and accuracy, then
records `represented_labels_not_predicted` and `below_probe_majority_baseline`
where measured. Source receipt, source time, assessment time, sample identity and
rule version are explicit. Original worker metrics/findings/fingerprint remain
unchanged. No model strategy or semantic repair is inferred. Passive summaries now
retain `latest_generation_check`, ordered by attempt then step, independently of
later loss-only checks. Detailed REST and MCP checks share these recorded facts.

The first resumed regression attempt exposed one incomplete test fixture (missing
base-model identity made its job label invalid) and nine loopback sandbox errors,
not nine analytics failures. The fixture was repaired and the same tests ran with
scoped loopback permission: 57 passed. Additional pre-fix tests then reproduced
older-attempt selection and integer-overflow assessment failures: two failed,
ten passed. Both are fixed. Final scoped run: **118 passed**:

```sh
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --offline pytest -q tests/test_training_quality.py tests/test_training_monitoring_receipts.py tests/test_training_monitoring_journey.py tests/test_training_experience.py tests/test_training_field_checks.py tests/test_mcp_prompts.py tests/test_mcp_catalog.py tests/test_mcp_resources.py tests/test_mcp_result_compat.py
```

Requires the existing backend test environment and loopback permission for the
local analytics receiver; no GPU calls. Logs: `/tmp/overmind-training-quality-fixed.log`,
`/tmp/overmind-training-quality-verified.log`,
`/tmp/overmind-training-quality-recovery-red.log`,
`/tmp/overmind-training-quality-final.log`. Counts overlap prior runs.
No schema/migration, queue/topology, demo fixture or generated-client change was
needed: assessment uses existing JSON facts. No frontend change or visual check
was performed. Generic receipt inspection exposes the added facts; the MCP-only
browser restriction remains in force. Canonical guidance, prompts, architecture,
product and sibling docs are updated; installed skill sync requires scoped access
to the protected `.agents` directory.

Scoped pre-commit passed (including Ruff and Markdown formatting), and
`git diff --check` passed. Log: `/tmp/overmind-training-quality-precommit.log`.
The supported `overmind skills sync overmind-training --ide codex` completed with
scoped permission; installed and canonical skill files compare byte-identically.
The first sandboxed install attempt was denied and did not establish readiness.
No root dependency lockfile, SDK version or unrelated file was changed in this
heartbeat; the existing bundled SDK version bump remains in place.

### Full KYC worker completion, development quality and collection delay

At **16:30:58 UTC**, passive MCP discovery confirmed local `financial-services`,
contract 6.2.1, 69 tools and catalogue
`c6475173209deb38b2d2b9130ab27271a23a1eb77850c62a2eae43802251398a`.
The same acknowledged job reached **133/133** updates. Training completion was
reconciled after local services resumed; the job remains `deploying`, with separate
deployment `f0a8d60a-10d4-407c-b07c-563bddb7db15` `warming`/`Booting and verifying`.
No activation, alias change, replay, new qualification or worker deployment was
submitted in this heartbeat. Its pinned worker remains `4146ef10…`.

Reproduce the observation with address-bound local MCP credentials already installed:

```sh
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --offline python tests/evidence/inspect_training_monitoring.py d41d3526-d703-499b-aaee-663c2da0908d --project e18b29b5-915d-45a7-80cd-77ffe6559205 --output /tmp/overmind-kyc-monitoring-heartbeat-final.json
```

The helper discovers/paginates projects and verifies the reported local endpoint,
then reads the job, checks, paginated sample evidence, interface and linked
deployment. It never submits provider work. The initial observation failed with
`httpx.ReadError` while the existing local containers were starting; a later read
worked without changing credentials or endpoints. No browser fallback was used.

Measured training results:

| Check     | Generated correct / scored | Macro F1 | Prohibited predictions |
| --------- | -------------------------- | -------- | ---------------------- |
| 34        | 24 / 64                    | 0.1964   | 0                      |
| 64        | 20 / 64                    | 0.2058   | 0                      |
| 94        | 25 / 64                    | 0.2020   | 0                      |
| 124       | 24 / 64                    | 0.2336   | 0                      |
| Final 133 | 22 / 64                    | 0.2041   | 0                      |

Every post-update generation check completed all 64 examples with valid labels.
The same-sample majority-label baseline is **27/64 (42.1875%)**, above every
post-update result. Final predictions: enhanced 49, high_risk 14, standard 1,
prohibited 0; references: 27/17/17/3 respectively. Final recalls are 17/27,
4/17, 1/17 and 0/3. The final paired comparison against step 124 records two
regressions, zero improvements and 62 unchanged examples; its descriptive grouped
interval is not an independent final-benchmark claim. The small fixed probe and
disputed fixture semantics do not establish production KYC fitness or a cause.

Full final teacher-forced loss is **0.1131938770 over 44,800/44,800 rows**.
The fixed 2,048-row last periodic loss is 0.1149649918, with 256-row training
reference 0.1026186571 (gap 0.0123463348). Initial fixed-probe loss was 3.8397939.
The learning objective improved substantially but generated task correctness did
not beat a trivial sample baseline. This is direct evidence that the new
monitoring is useful; it is not evidence the model is good. External benchmarks
remain disabled for the previously recorded exact-route/evaluator blockers.

Numerical health: 133 logged updates, zero observed non-finite losses/gradients,
12 clipping-threshold exceedances. Skipped optimizer updates remain unavailable.
All **13 retained checkpoints** report strict adapter-weight reload verification;
step 133 is selected under the frozen `last` policy. Full optimizer resume is
unsupported. The final deployment's artifact/readiness verification is separate
and was still pending at this observation. Shared-pool `engine_ready` telemetry
from 08:21 does not establish this adapter's readiness at 16:30.

Measured optimization: **4,081.70 seconds**. Monitoring: **788.11 seconds**,
comprising initial 360.47, interim 221.55 and final 206.09 seconds. Interim checks
represent **5.15%** of optimization plus interim-check time; all checks together
represent **16.18%** of optimization plus monitoring time. These ratios exclude
startup/checkpoint/other overhead and are not billing fractions. The old pinned
scheduler recorded an extra endpoint periodic check and boundary-conflict notices;
the newer scheduler's boundary fixes do not retroactively change these receipts.

Product failure/recovery evidence: the remote final result was observed by the
worker at **09:40:47 UTC**, but first collected locally at **16:26:38 UTC**, a
**6h45m50.55s collection delay**. Checks after step 44 were likewise collected
late. Local containers reported a recent restart when inspection resumed; this
does not establish the reason for the whole outage. Durable evidence recovered
without repeating training, and charges use 4,944.82 measured GPU seconds, not
that observation delay. However, a stopped local control plane cannot deliver
live progress or advance deployment: remote training completion and local product
availability are separate. This is a critical operational limitation, not an
extra six hours of model training or proof that live monitoring was uninterrupted.

Recorded GPU cost: **$5.4256** for the full run, plus **$1.7616** from the six
qualification receipts already listed: **$7.1872 recorded GPU total** against the
fresh $100 allowance. This run additionally estimates $0.24036 CPU/memory and
$0.10834 preparation; adding those gives **$7.53590 known recorded-plus-estimated
subtotal** before other qualifications' non-GPU, deployment, image/storage/network
and unreconciled provider components. All-in actual cost remains unknown, not
zero. Keep substantial reserve; no new paid work is justified merely by completion.

Artifacts: `/tmp/overmind-kyc-monitoring-heartbeat-1625-recovered.json`,
`/tmp/overmind-kyc-monitoring-heartbeat-final.json` and corresponding `.log` files.
The complete implementation remains unfinished: the capability gaps above still
apply, and deployment readiness requires its own later evidence.

The **16:32:57 UTC** follow-up remains `deploying`/`warming`. The retained provider
call graph now explicitly shows `pre_warm` and its `L4_vllm_lora.*` child both
`PENDING`, with no worker IDs. Adapter-specific events/heartbeat remain unavailable;
the old shared-pool observation must not be presented as active worker progress.
No repeated prewarm/inference request was sent. Artifact:
`/tmp/overmind-kyc-monitoring-1635.json` (filename is a label, not observation time).

### Checkpoint download connection prerequisite

Before extending retained-checkpoint export, inspect the existing final-artifact
handoff. Failure cases to reproduce before implementation: repository-free CLI
cannot use the saved account connection; overriding an API address can move a
repository-bound key; metadata/artifact redirects are followed; credentials must
not reach artifact requests or errors; failed transfers must not leave output.
Use a local HTTP fixture with synthetic credentials, never real credentials at a
different endpoint. Intermediate checkpoint export itself remains unimplemented.
