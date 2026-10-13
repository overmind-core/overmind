# Decision benchmark results

Three full training epochs; fixed recipe and last-checkpoint selection. Accuracy and probability metrics use complete official held-out splits. The base is an unchanged foundation with its initialized decision head, not a prompted chat classifier.

| Benchmark | Participant          | Accuracy | Macro F1 | Raw cross entropy | Calibrated cross entropy | Raw Brier | Scored |
| --------- | -------------------- | -------: | -------: | ----------------: | -----------------------: | --------: | -----: |
| banking77 | Qwen3 0.6B base      |   12.27% |   0.0885 |            4.3256 |                   3.9926 |    0.9865 |  3,080 |
| banking77 | Qwen3 0.6B trained   |   92.66% |   0.9266 |            0.3676 |                   0.2730 |    0.1170 |  3,080 |
| banking77 | Qwen3.5 0.8B base    |    8.83% |   0.0526 |            4.3333 |                   4.2006 |    0.9867 |  3,080 |
| banking77 | Qwen3.5 0.8B trained |   93.57% |   0.9356 |            0.3817 |                   0.2583 |    0.1096 |  3,080 |
| banking77 | Jev 1.13             |   80.29% |   0.7952 |            1.8732 |                   0.9391 |    0.2989 |  3,080 |
| sst5      | Qwen3 0.6B base      |   20.18% |   0.1045 |            1.6086 |                   1.6091 |    0.7997 |  2,210 |
| sst5      | Qwen3 0.6B trained   |   55.75% |   0.5472 |            1.4181 |                   1.0341 |    0.6830 |  2,210 |
| sst5      | Qwen3.5 0.8B base    |   29.41% |   0.2116 |            1.6087 |                   1.6094 |    0.7997 |  2,210 |
| sst5      | Qwen3.5 0.8B trained |   54.30% |   0.5313 |            1.4195 |                   1.0484 |    0.6981 |  2,210 |
| sst5      | Jev 1.13             |   58.64% |   0.5547 |            2.0028 |                   1.1108 |    0.5958 |  2,210 |
| boolq     | Qwen3 0.6B base      |   62.17% |   0.3834 |            0.6881 |                   0.6643 |    0.4949 |  3,270 |
| boolq     | Qwen3 0.6B trained   |   82.75% |   0.8143 |            0.7254 |                   0.4129 |    0.3110 |  3,270 |
| boolq     | Qwen3.5 0.8B base    |   60.86% |   0.4608 |            0.6905 |                   0.6633 |    0.4974 |  3,270 |
| boolq     | Qwen3.5 0.8B trained |   84.16% |   0.8296 |            0.6589 |                   0.3889 |    0.2825 |  3,270 |
| boolq     | Jev 1.13             |   91.53% |   0.9108 |            0.2367 |                   0.2332 |    0.1339 |  3,270 |

The constant-label baseline selects the most frequent training label, then scores the unchanged final split: banking77 1.30%; sst5 23.08%; boolq 62.17%. Label-selection receipts and checksums are in the archived run evidence.

| Benchmark | Trained model        | Gain over own base | Difference from Jev |
| --------- | -------------------- | -----------------: | ------------------: |
| banking77 | Qwen3 0.6B trained   |          +80.39 pp |           +12.37 pp |
| banking77 | Qwen3.5 0.8B trained |          +84.74 pp |           +13.28 pp |
| sst5      | Qwen3 0.6B trained   |          +35.57 pp |            -2.90 pp |
| sst5      | Qwen3.5 0.8B trained |          +24.89 pp |            -4.34 pp |
| boolq     | Qwen3 0.6B trained   |          +20.58 pp |            -8.78 pp |
| boolq     | Qwen3.5 0.8B trained |          +23.30 pp |            -7.37 pp |

BoolQ final evaluation uses the complete public validation split. Training excludes text/passage groups overlapping reserved splits. All duplicate final observations are retained. Calibration uses its own frozen rows and never changes the training recipe. One seed does not estimate training-run variance; pretraining exposure to these public benchmarks is unknown. Recorded provider usage is not an all-in invoice. Full metrics, intervals, coverage, fits, costs and provider identities remain in the archived run evidence.

## Recorded protocol

- Train Qwen/Qwen3-0.6B and Qwen/Qwen3.5-0.8B separately on each benchmark.
- Compare the unchanged foundation decision head, trained decision model, and
  `typesafe/jev-1.13`. The foundation baseline is not a prompted chat classifier.
- Use every official test example for Banking77 (3,080) and SST-5 (2,210).
  BoolQ uses all 3,270 labeled validation examples as final evaluation because its
  public dataset has no labeled test split.
- Retain original labels, duplicate observations, exact option order, source
  revision, file checksums, original split and row identities. Banking77 uses the
  complete JSONL mirror, not the deduplicated Parquet mirror.
- Group normalized identical text for Banking77/SST-5 and normalized identical
  passages for BoolQ. Keep final examples unchanged; exclude lower-priority
  overlapping groups from training/development, retaining exclusion evidence in
  an earlier Workshop cell. SST-5's official validation split supplies development.
- Reserve approximately 10% of original training data for development and 10%
  for calibration for Banking77/BoolQ. SST-5 reserves 10% of training for
  calibration and uses its official development split. Group-preserving split
  allocation uses seed 73491. No final labels select recipes or checkpoints.
- Recipe: three complete epochs, effective batch 16, learning rate 0.0002,
  LoRA rank 16 / alpha 32 / dropout 0, training seed 0, context 4096, maximum
  provider runtime 10,800 seconds. No optimizer-step cap. Monitor each epoch and
  select the last checkpoint. Keep the initial development baseline enabled.
- Fit Clef temperature calibration only on the calibration role, separately per
  participant. Report raw accuracy, cross entropy, Brier, coverage and available
  paired uncertainty, plus calibrated probability metrics. One training seed
  does not measure training-run variance. Public benchmark pretraining exposure
  for foundations and Jev is unknown.

Before launch, verify labels/options and split identities across all exported
rows, exact tokenization readiness, frozen source boundaries, cost forecasts,
and the current pinned worker release. Verify checkpoint reload and full final
coverage after training. Preserve failed attempts and repair their causes before
retesting; do not silently replace participant models or benchmark rows.

Concrete failure checks: altered mirror row counts; wrong class-index mapping;
target fields entering model inputs; cross-role duplicate/group leakage; lost
duplicate observations; context truncation; incomplete tokenization; unstable
provider submission; checkpoint reload mismatch; missing/invalid probabilities;
evaluation rows dropped; unreported versus measured costs.

Sources: [Banking77](https://huggingface.co/datasets/PolyAI/banking77),
[Banking77 JSONL mirror](https://huggingface.co/datasets/mteb/banking77),
[SST-5](https://huggingface.co/datasets/SetFit/sst5),
[BoolQ](https://huggingface.co/datasets/google/boolq).

## Evidence and reproduction

These are historical measurements from 2026-10-10, not results rerun by the cleanup.
The original scripts and receipts remain recoverable from commit
`63caac868ac9fc1cf6b66525f029e84e1b90a0de` under `tests/evidence/`.
Local copies were retained under ignored `scratch/branch-cleanup-2026-10-12/`.
The receipts pin the source revisions, packages, cells, provider calls and model artifacts.

For new measurements, use the current local MCP preparation, experiment and
evaluation workflows with fresh request keys. `scripts/decision_benchmarks.py`
converts downloaded source rows; `scripts/score_decision_benchmarks.py` scores
saved predictions. Old local replay scripts contain project-specific identities
and are not portable benchmark tooling.
