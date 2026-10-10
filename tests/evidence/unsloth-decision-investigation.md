# Unsloth decision training investigation history

This preserves intermediate runs and diagnoses, including superseded results.
The current completion record is [unsloth-decision-acceptance.md](unsloth-decision-acceptance.md).

Requested: replace the native training engine with FastDecisionModel/DecisionTrainer,
preserve product features, qualify real Hugging Face data through local MCP, and
compare frozen benchmarks before and after training. Investigate each failure and
rerun affected checks. This record is updated with observed results during execution.

## Failure cases to exercise before cutover

- Option sorting or boolean reversal changes labels or probability targets.
- Preparation silently truncates, drops invalid examples or retokenizes different bytes.
- Nonuniform weights are normalized per microbatch instead of optimizer batch.
- Mean-only targets are converted to invented distributions or categorical labels.
- The head is omitted from checkpoints, rounded during exact export, or retained in a baseline.
- A numerical replay tolerance conceals changed adapter/head tensors or unsaved trainable weights.
- Trainer recovery restores weights but loses optimizer, scheduler, step or RNG state.
- Two schedules perform duplicate checks or disagree on checkpoint selection.
- Later loss-only checks hide the latest decision-quality result.
- Incomparable task families are pooled in majority-label observations.
- Multi-question/source-row/token counts are conflated.
- Validation or checkpoint work triggers a false stall; heartbeat falsely claims progress.
- MCP reads start provider work; request-key retries start duplicate training.
- Cancellation, collection restart or analytics failure loses completed evidence.
- New dependencies alter ordinary chat training.
- Baseline/final benchmark inputs leak into optimization or development selection.

## Repeatable verification

1. Run the native runtime journey with real Unsloth, a locally created tiny backbone,
   boolean/choice/score records, nonuniform weights and ordinal means. Verify
   preparation, optimization, checkpoint reload, interruption recovery and predictions.
1. Run existing backend preparation, monitoring, artifact, MCP, evaluation and lifecycle
   coverage and frontend type/lint checks. Retain logs; fix and rerun failing checks.
1. Download `stanfordnlp/sst2` at a recorded Hugging Face revision. Keep the original
   retained source and transformation package. Train and development come from train;
   the labeled validation split supplies the held-out final benchmark. Test labels are
   unavailable and must not be treated as gold.
1. Use local MCP setup, readiness, preparation, estimates and launch. Freeze stable
   request keys, cells, recipe, runtime and monitoring. Train two small catalog backbones
   with bounded steps; evaluate their initialized decision models and trained artifacts
   on the identical held-out decisions, plus the same-subset majority baseline.
1. Inspect progress/examples/operations through MCP and compare the same persisted
   records returned by REST. Exercise cancellation/recovery separately from the final
   benchmark runs. Record usage and failures; missing evidence stays unmeasured.

Environment: local Overmind API `http://localhost:8000`, project
`1e3f3e92-b50d-4590-85ed-97921d132d3c`; Modal GPU runtime with pinned package versions.
CLI and MCP connection preflight both reported ready on 10 October 2026.

## Observed results

The new engine uses Unsloth 2026.10.3 FastDecisionModel, its Clef encoder and collator,
and DecisionTrainer. Overmind supplies product target semantics, immutable data and
runtime identities, monitoring, recovery receipts and local benchmark scoring.
The previous token-codebook engine and its custom optimizer loop were removed.

The runtime journey passed on a real NVIDIA L4 with Torch 2.10.0+cu128. It exercised
12 boolean/choice/score decisions, soft distributions, nonuniform weights and three
ordinal means. A SIGKILL after checkpoint 2, an incomplete checkpoint 3, and a
completed checkpoint replay recovered to step 4 without an extra optimizer step.
The final retest recovered adapter/head tensors exactly; fresh-process probability
error was zero. A deliberately changed head tensor was rejected before prediction. All 12 input-only
predictions matched the saved reference. The independent semantics journey also
passed CPU/GPU encoder parity, weighted accumulation, option reordering, distinct
distributions with the same mean, and explicit context-overflow rejection.

```sh
set -o pipefail
.venv/bin/modal run --env overmind-dev tests/evidence/unsloth_decision_modal.py 2>&1 | tee /tmp/unsloth-decision-attention-runtime.log
.venv/bin/modal run --env overmind-dev tests/evidence/unsloth_decision_modal.py --semantics-only 2>&1 | tee /tmp/unsloth-decision-semantics.log
.venv/bin/modal run --env overmind-dev tests/evidence/unsloth_decision_modal.py --cpu-only 2>&1 | tee /tmp/unsloth-decision-cpu-retest.log
```

The real-data experiment pins Hugging Face `stanfordnlp/sst2` revision
`8d51e7e4887a4caaa95b3fbebbf53c0490b58bbb`. The retained package is
`tests/evidence/unsloth_sst2_pipeline`. Its sample and grouping preserve duplicate
observations; exact final-suite overlaps are excluded before train sampling.
Frozen roles contain 2,059 training, 257 development, 257 calibration and 872 final
decisions. No exact-input or declared-group overlap was found between roles;
paraphrase and pretraining contamination remain unknown. The original labeled
validation split supplies final scoring. Unlabeled test rows are not used.

Both recipes use seed 0, batch 16, context 4096, learning rate 0.0002, LoRA rank 16,
alpha 32, dropout 0, and at most 256 optimizer steps. Development checks run every
32 steps; selection is the last checkpoint, frozen before final scores were read.
The before model is the unchanged foundation with a newly initialized Unsloth
decision head, not a pretrained sentiment classifier or a chat-prompt baseline.

| Model        | Before accuracy | After accuracy | Raw CE before → after | Final coverage |
| ------------ | --------------: | -------------: | --------------------: | -------------: |
| Qwen3 0.6B   |        51.8349% |       91.2844% |   0.691369 → 0.266386 |        872/872 |
| Qwen3.5 0.8B |        49.0826% |       89.6789% |   0.692719 → 0.330962 |        872/872 |

Qwen3's paired accuracy improvement is 39.4495 percentage points, with a
group-bootstrap 95% interval of [36.0063, 43.3486] (1,000 samples, seed 73491).
Calibration uses only the separate 257 decisions and is frozen before final
prediction. Its final CE is 0.253438. The same-final-subset majority baseline is
50.9174%. Qwen3.5's final paired improvement is 40.5963 percentage points,
with a 95% interval of [36.5826, 44.3807]; its calibrated final CE is 0.290406.
This is one sentiment task and one seed, not universal model qualification.
The Qwen3 row above records the earlier run; its final-runtime repeat is in progress.

MCP exercised project discovery, dataset publication, partitioning, readiness,
exact preparation, live-rate estimates, stable-key launch, inspection, paired
evaluation and cancellation. Repeating the launch key returned the original job;
changing its learning rate conflicted without launching another job. MCP and REST
returned identical persisted check metrics, facts, sample identities and examples.
Cancellation job `5907d62a-d4a3-4063-a704-60f21c5bbbc4` retained its completed step-4
check and a verified checkpoint with zero reload error. The new worker reports
`native_probabilities`, original question/options, full unrounded vectors, and
`supervised_decisions` separately from tokens. Skipping the initial baseline is
recorded as `not_requested`.

The MCP sequence for this native route is `prepare_training_data`, then
`estimate_finetune`, `start_finetune` with a stable request key, and
`schedule_native_evaluation` with separate calibration/final cells. All four chat
evaluation flags are false. `get_job`, `inspect_training_progress`, and
`inspect_operation` read recorded state; retrieving examples uses a retained check
ID. The exact launch arguments and pinned runtime are retained in
`unsloth-decision-attention-retest.json`. Estimates kept duration unmeasured when
older executions did not match the corrected runtime, while exposing the fetched
H100 hourly rate. Inspection did not start duplicate provider work.

```sh
.venv/bin/python tests/evidence/unsloth_surface_parity.py
.venv/bin/python tests/evidence/unsloth_decision_report.py --job b6500866-ed71-4a16-b0ce-20bdc4b4b77f --job 5907d62a-d4a3-4063-a704-60f21c5bbbc4 --evaluation d0f31c8c-bb09-4390-8872-733da9f63b00 --evaluation be480dc1-a0f1-41f4-adf6-0154f4f4cdf9
```

Commands use the address-bound local account connection and do not print its key.
The report command only reads saved records. Replaying GPU qualification or a
fresh training/evaluation launch incurs provider compute.

## Failures investigated

- The initial image's datasets pin conflicted with the new dependency requirements.
  The unpublished decision image was corrected to datasets 4.7.0 and built successfully.
- Transformers 5.17 removed the warmup-ratio constructor argument. The engine now
  resolves it to an exact warmup step count; real GPU training passed.
- Importing Unsloth for CPU preparation required a GPU. CPU preparation now loads
  the exact installed encoder file after verifying its package version and checksum;
  CPU-only execution and GPU parity passed.
- A completed recovery previously performed an extra optimizer step. Completed
  recovery now restores state and finalizes without calling train again; the real
  interruption/completed-replay journey passed.
- Preparations requested while deployment was still finishing reported an unavailable
  app. The same failed preparations were retried after confirming deployment; both
  completed. Credentials and endpoints were not changed.
- Context 1024 was outside the catalog's supported launch range. Both real recipes
  use 4096; actual prepared inputs have maximum lengths 175 and 177.
- Qwen3.5 completed optimization but fresh-process replay differed by 0.0002325.
  Exact adapter/head checks ruled out serialization loss; a provisional 0.001
  bound still failed a second full run at 0.0107711. Further probes showed that no
  parameters or buffers changed, and backward, zero-rate optimization, TF32 and
  the precision wrapper did not cause the difference. An empty upstream
  `compiled_encoder` context alone changed Qwen3.5's nested text attention from
  `flex_attention` to the parent's `sdpa` setting and reproduced the training
  reference exactly. Restoring Flex Attention reproduced the discrepancy.
  The implementation now explicitly requests SDPA throughout training and
  prediction and restores the original 0.0001 probability bound, while keeping
  exact tensor verification. Both failed jobs remain historical failures.
  Replaying the exact second failed checkpoint through the corrected production
  entrypoint passed: maximum probability error 0.000000245636329, 32 probes,
  exact weights. This read-only diagnosis did not publish or relabel the failed job.
  Result: `unsloth-qwen35-fixed-reload.json`; Modal run
  `ap-9DXH4QtWpOtNypLsZXXG26`.
  Source: Unsloth 2026.10.3 `models/_decision_fast.py` cleanup and Transformers
  5.17.0 `PreTrainedConfig._attn_implementation` recursive setter.
- A fresh Qwen3 run failed at 0.0009163022 probability error despite exact weights.
  Repeating evaluation without constructing a trainer produced zero error. Activation
  tracing isolated the first difference to the head's first memory LayerNorm call:
  identical backbone states, pooled states, projections, inputs and weights, but a
  0.015625 BF16 output difference. Subsequent calls were stable. A provisional runtime
  performed one input-only warm-up before retaining predictions or computing gradients,
  preserving RNG/module state and disabling autocast caching during the no-gradient
  pass. The exact failed checkpoint then passed fresh-process verification with zero
  error on 32 probes at the unchanged 0.0001 tolerance. The failed job remains failed.
  The diagnostic's first native LayerNorm comparison incorrectly paired BF16 inputs
  with FP32 weights; using the actual FP32 normalization dtype fixed that probe.
  Evidence: `unsloth-qwen3-head-wrapper-probe.json`,
  `unsloth-qwen3-norm-wrapper-probe.json`, `unsloth-qwen3-warmup-fixed-reload.json`.
- Fresh-process checkpoint verification left the tokenizer stage visible. It now
  reports `checkpoint_reload` and measured decision counts; the GPU journey checks
  the final stage, unit and complete probe count.
- The warm-up recovery retest exposed a PyTorch gradient-checkpoint metadata mismatch
  on the first resumed backward. Uninterrupted training and fresh probability
  verification had passed. Disabling autocast weight caching did not resolve the mismatch. Both provisional
  changes were removed. The implementation now uses native PyTorch LayerNorm for
  the head, preserving its FP32 weights and output dtype while retaining Unsloth
  architecture and activation checkpointing. Cold training and recovery passed
  without the optional initial baseline: exact recovered parameters, zero fresh
  probability error, completed replay at step 4 and 12 input-only predictions.
  The no-baseline harness initially retained `monitoring.initial=true`; the worker
  correctly rejected that conflict. The fixture now freezes both choices together.
  Its superseded diagnostic was stopped explicitly after the CLI required `--yes`.
- Optional Unsloth statistics downloads produced transient HF connection errors
  during local checkpoint loading. The decision image disables that optional fetch.
- The diagnostic scalar-tensor hash required flattening before byte reinterpretation;
  the corrected probe verified all parameters. Its context probe uses the exported
  `compiled_encoder` symbol from `unsloth.models.decision`.
- The first diagnostic harness lacked `MAX_LENGTH`; its environment was corrected
  to the original batch/context configuration and the tensor diagnosis passed.
- Live MCP forecasting initially rejected a completed identical recipe because its
  omitted packing default differed from the explicit false stored at launch.
  Forecast matching now normalizes that native default and compares step limits;
  native workload estimates honor the supplied recipe epoch count. The repeat
  quote matched the successful run's 356.784246492 measured GPU seconds, with a
  [178.392123246, 535.176369738] planning range and [$0.1957, $0.5872] GPU range.
  The related 78-test suite passed (`/tmp/unsloth-decision-forecast-regression.log`). The test's simulated completion was corrected
  to record Modal as provider, which real worker dispatch supplies.
- The summary-export helper initially assumed compact-response coverage keys in
  the full benchmark report. Reading the full report's per-benchmark counts fixed
  the export; the saved summary now includes all 872 paired decisions and their interval.
- A provider log query hit its resource limit. Retrying with a ten-minute window
  and 100 entries succeeded; the training job was not resubmitted.

Existing backend coverage passed 395 tests (2 skipped), and the final focused
regression passed 102 tests after the observation fixes. Frontend typecheck and
Biome passed. The Console was not opened; frontend validation was static. Logs are retained under `/tmp/unsloth-decision-*`; final verification
and run records accompany this file.

Final verification commands after the exact-weight change:

```sh
set -o pipefail
.venv/bin/pytest -q tests/test_decision_artifact.py tests/test_decision_prediction.py tests/test_decision_checkpoint.py tests/test_training_preparation.py tests/test_training_monitoring_native.py tests/test_training_monitoring_journey.py tests/test_training_monitoring_receipts.py tests/test_decision_quality_journey.py tests/test_native_evaluation_plan.py tests/test_training_quality.py 2>&1 | tee /tmp/unsloth-decision-exact-weight-regression.log
.venv/bin/modal run --env overmind-dev tests/evidence/unsloth_decision_modal.py 2>&1 | tee /tmp/unsloth-decision-exact-weight-retest.log
```

The focused regression passed 102 tests in 3.84 seconds. The GPU retest passed,
including fresh-process corruption rejection, interruption recovery, completed-run
replay without another optimizer step, and 12 input-only predictions. Recovered
parameters and replay probabilities both had zero maximum error. Modal execution:
`ap-WKaFrMuznLajYdLP6iPFBw`.

The attention-fix regression passed 105 tests in 4.64 seconds; log: `/tmp/unsloth-decision-attention-regression.log`.

The final runtime journey also passed after the attention fix on a real NVIDIA L4:
`ap-fXX2G2DbtSTI6SEnCw1Gkj`, `/tmp/unsloth-decision-attention-runtime.log`.
All 12 probability probes replayed exactly at the restored 0.0001 bound, interrupted
and uninterrupted parameters matched exactly, and completed replay stayed at step 4.
`unsloth-decision-runtime-results.json` records this final execution.

The final backend sweep passed 390 tests with one skip; nine analytics receipt tests
could not bind their loopback fixture in the filesystem sandbox. Retrying just
`tests/test_training_experience.py` with scoped local-network access passed all nine.
No product change was required for that fixture restriction. Logs:
`/tmp/unsloth-decision-final-regression.log` and
`/tmp/unsloth-decision-final-experience-retest.log`.

Final native-normalization qualification: `ap-UL3dsWWN3egiwVFMmCuxqQ`,
`/tmp/unsloth-decision-native-norm-runtime.log`,
`unsloth-decision-without-baseline-results.json`. The independent first-call and
semantics check passed with zero cold-call error:
`ap-NHIRSO248xWEGYmYm4hf61`, `/tmp/unsloth-decision-native-norm-semantics.log`.
The final backend sweep passed 399 tests with one skip and two expected warnings
from local Modal upload fixtures: `/tmp/unsloth-decision-native-norm-regression.log`.
