# Unsloth decision training acceptance

The native decision engine has been replaced with Unsloth and deployed to
`overmind-dev`. Both final SST-2 training runs and both paired benchmarks completed successfully.
The records below describe the final deployed release, with earlier attempts retained separately.

## Implementation

Unsloth 2026.10.3 `FastDecisionModel`, `DecisionTrainer`, Clef encoding/collation,
and the joint decision head own the training path. The previous token-codebook
engine and custom optimizer loop were removed. A separate pinned decision image
keeps conversational training on its existing dependency stacks.

Product contracts remain on the shared preparation, launch, monitoring,
cancellation, operational-event, artifact and MCP services. The decision loss
extension preserves complete distributions, observation weights and mean-only
ordinal supervision. It normalizes weights across the actual accumulated batch.
Encoding excludes targets, preserves option identities through an explicit order
mapping, and rejects context overflow. Predictions retain unrounded probabilities
and log probabilities. Calibration uses the Clef temperature-fitting procedure
with retained distributions and local references.

Artifacts include the adapter, FP32 joint head, tokenizer and upstream configs.
Every trainable tensor must be saved; reload checks compare exact tensor values
and probabilities at the unchanged `0.0001` absolute tolerance. Training and
inference explicitly use SDPA. Native PyTorch LayerNorm in the decision head
avoids upstream compiled first-call differences and checkpoint recomputation
failures while preserving the architecture and activation checkpointing.

The runtime is pinned in `unsloth-decision-final.json`:

- Training release: `ebc520f5648151eec275bf69da99f82d08077a024544a985529de03dd980fa2f`.
- Evaluation release: `4622f892794f5c987b6748f7f1bd5aa21f3294140dc2c33c761a93206670b8b9`.
- Decision stack: `u2026_10_3_decision`, Torch 2.10.0+cu128,
  Transformers 5.17.0, TRL 1.13.0, datasets 4.7.0.
- MCP contract: 6.3.0. No REST schema or generated-client change was required.

## Real data and frozen experiment

Hugging Face `stanfordnlp/sst2` is pinned to revision
`8d51e7e4887a4caaa95b3fbebbf53c0490b58bbb`. Original Parquet bytes, SHA-256 hashes,
upload receipts, the retained transformation package and published cell lineage
are recorded in `unsloth-decision-live-results.json`,
`unsloth-decision-data-lineage.json` and `unsloth_sst2_pipeline/`.

The frozen roles contain 2,059 training, 257 development, 257 calibration and 872
final decisions. Train/development/calibration come from the training split;
the complete labeled validation split supplies final scoring. Unlabeled test rows
are unused. Duplicate observations remain retained. Exact-input and declared-group
overlap between roles is zero; paraphrase and pretraining contamination are unknown.

Both models use seed 0, effective batch 16, context 4096, learning rate 0.0002,
LoRA rank 16/alpha 32/dropout 0, two requested epochs capped at 256 optimizer steps,
and a 3,600-second provider-call limit. Development checks run every 32 steps;
last-checkpoint selection was frozen before final scores. The same recipe and
splits were retained through numerical fixes; benchmark quality did not select
those fixes. Calibration is fitted only on its 257 examples before final prediction.

The before model is the unchanged foundation with a newly initialized decision
head. It is not a pretrained sentiment classifier or a chat-prompt baseline.

| Model        | Before accuracy | After accuracy | Raw CE before → after | Coverage |
| ------------ | --------------: | -------------: | --------------------: | -------: |
| Qwen3 0.6B   |        51.9495% |       90.8257% |   0.691353 → 0.283454 |  872/872 |
| Qwen3.5 0.8B |        49.0826% |       90.5963% |   0.692720 → 0.292192 |  872/872 |

Qwen3 0.6B's paired accuracy improvement is 38.8761 percentage points (95% group-bootstrap interval [35.2064, 42.6634]); calibrated final CE is 0.260929.

Qwen3.5 0.8B's paired accuracy improvement is 41.5138 percentage points (95% group-bootstrap interval [37.6147, 45.4128]); calibrated final CE is 0.267478.

Both comparisons use 1,000 group-bootstrap samples with seed 73491. All 872 final
inputs were scored by both models, with no missing, invalid or incompatible predictions.

The final-suite majority-label baseline is 50.9174%. This is one sentiment task and
one seed; it establishes these runs' behavior, not universal model quality.

Final run identities:

| Model        | Training job                           | Paired evaluation                      |
| ------------ | -------------------------------------- | -------------------------------------- |
| Qwen3 0.6B   | `03a02602-b320-42c1-9d0f-11379b0be5cd` | `d5c46c15-df3f-47b6-a542-fe294317699b` |
| Qwen3.5 0.8B | `bce5630c-c13d-49c1-b86f-e72189c8977e` | `22663bb7-1280-493e-9679-cd9770cb8214` |

Both final training artifacts passed exact weight reload and 32 fresh-process
probability probes with zero error. Each run retained ten completed monitoring
checks and closed its operational timeline successfully. Recorded training GPU
spend is $0.2885 and $0.4963 respectively; this is not the total experiment or
provider invoice. Preparation, evaluation, CPU/memory, storage and other components
have separate receipts or remain unreported. Post-completion MCP forecasts matched
these exact runtime/recipe measurements and explicitly retained low confidence.
See `unsloth-decision-final-observation.json`.

## MCP and observation coverage

The local endpoint is `http://localhost:8000/api/mcp/`, project
`1e3f3e92-b50d-4590-85ed-97921d132d3c`. Both MCP discovery and CLI transfer preflight
were verified against localhost; credentials were never moved to another endpoint.

The exercised sequence was dataset upload/publication and partitioning, training
readiness, `prepare_training_data`, `estimate_finetune`, `start_finetune`, then
`schedule_native_evaluation` with distinct calibration/final cells. Native launch
sets all four chat evaluation flags false. Exact launch arguments and stable
request keys are retained in `unsloth-decision-final.json`.

`get_job`, `inspect_training_progress` and `inspect_operation` read saved evidence.
MCP and REST agreed on all 20 final-run checks' metrics, assessments, coverage,
sample/evidence fingerprints and four sampled examples. The independent latest
decision summary survives later loss-only checks. Native quality uses
`native_probabilities`; categorical observations compare explicitly declared gold
families with their own same-subset majority baseline. Soft distributions and
means do not become invented categorical correctness.

Repeating each final launch key returned its original successful job; a changed
recipe had already been verified to conflict without dispatch.
Cancellation job `5907d62a-d4a3-4063-a704-60f21c5bbbc4` retained completed checks and
a verified checkpoint. Provider-call identities, separate heartbeat/progress
clocks, measured reload counters and terminal events remained visible. Reading
these records did not dispatch replacement training. Final Qwen3.5's long first
optimizer interval was investigated through its existing call's logs; subsequent
steps and collected checks confirmed forward progress without resubmission.

## Verification and reproduction

Requirements: the repository's Python environment, local Overmind deployment and
address-bound account connection, Bun for frontend checks, and an authenticated
Modal profile authorized for `overmind-dev`. GPU qualification and fresh launches
incur provider compute. Saved-report/parity commands only read existing records.

```sh
set -o pipefail
PYTHONPATH=overbae .venv/bin/modal run --env overmind-dev tests/evidence/unsloth_decision_modal.py --skip-baseline 2>&1 | tee /tmp/unsloth-decision-native-norm-runtime.log
PYTHONPATH=overbae .venv/bin/modal run --env overmind-dev tests/evidence/unsloth_decision_modal.py --semantics-only 2>&1 | tee /tmp/unsloth-decision-native-norm-semantics.log
.venv/bin/pytest -q tests/test_training*.py tests/test_decision*.py tests/test_native*.py tests/test_finetuning*.py tests/test_sft_training_chat_template.py tests/test_mcp_finetuning*.py tests/test_mcp_resources.py tests/test_mcp_catalog.py tests/test_mcp_manifest.py tests/test_mcp_prompts.py tests/test_mcp_workflow_friction.py tests/test_workflow_measurements.py tests/test_mcp_research_journey.py tests/test_operational_progress.py 2>&1 | tee /tmp/unsloth-decision-cutover-regression.log
.venv/bin/python tests/evidence/unsloth_surface_parity.py --job 03a02602-b320-42c1-9d0f-11379b0be5cd --job bce5630c-c13d-49c1-b86f-e72189c8977e
.venv/bin/python tests/evidence/unsloth_decision_report.py --job 03a02602-b320-42c1-9d0f-11379b0be5cd --job bce5630c-c13d-49c1-b86f-e72189c8977e --evaluation d5c46c15-df3f-47b6-a542-fe294317699b --evaluation 22663bb7-1280-493e-9679-cd9770cb8214
```

The real NVIDIA L4 journey passed cold training with the initial baseline disabled,
SIGKILL after checkpoint 2, recovery past an incomplete checkpoint 3, exact recovered
parameters, completed replay remaining at step 4, deliberate head-corruption
rejection, and 12 input-only predictions. The independent semantics journey passed
first-call equality, CPU/GPU encoder parity, boolean/choice order mapping, weighted
accumulation, two different distributions with the same mean, mean-only targets,
and explicit overflow rejection. Results are in
`unsloth-decision-without-baseline-results.json` and
`unsloth-decision-semantics-results.json`; Modal executions are
`ap-UL3dsWWN3egiwVFMmCuxqQ` and `ap-NHIRSO248xWEGYmYm4hf61`.

The broader backend suite passed **952 tests**, with two skips for unavailable
optional Baseten packages (`truss_train` and `truss.remote.remote_factory`). Its
135 warnings concern local Modal upload fixtures and the test JWT secret length.
Obsolete mocked token-codebook engine tests were removed; real GPU journeys cover
the replacement. Frontend `bun run typecheck`, Biome and repository pre-commit
hooks passed. The Console was not opened, so frontend validation was static.

## Failures fixed and retested

Dependency resolution, the removed Transformers warmup argument, CPU encoder
loading, completed-resume extra steps, exact head serialization, attention changes
on trainer cleanup, compiled head normalization/recovery, reload-stage telemetry,
and mismatched forecast defaults were investigated and corrected. The final GPU
journeys and both full real-data training runs use the resulting pinned runtime.
Earlier failed jobs remain failed; successful provisional runs remain historical
and are not substituted for the final release's results.

[Investigation history](unsloth-decision-investigation.md) retains intermediate
failures, rejected fixes, diagnostic receipts and rerun commands. Raw final
training/evaluation records are in `unsloth-decision-reports/`; no credentials are
included. Supported scope remains single-GPU Modal LoRA with typed probability
consumers. These artifacts do not enter chat serving or chat evaluation.
