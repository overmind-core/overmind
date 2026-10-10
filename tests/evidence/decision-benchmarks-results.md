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

The constant-label baseline selects the most frequent training label, then scores the unchanged final split: banking77 1.30%; sst5 23.08%; boolq 62.17%. Label selection and export checksums are retained in decision-benchmarks-majority.json.

| Benchmark | Trained model        | Gain over own base | Difference from Jev |
| --------- | -------------------- | -----------------: | ------------------: |
| banking77 | Qwen3 0.6B trained   |          +80.39 pp |           +12.37 pp |
| banking77 | Qwen3.5 0.8B trained |          +84.74 pp |           +13.28 pp |
| sst5      | Qwen3 0.6B trained   |          +35.57 pp |            -2.90 pp |
| sst5      | Qwen3.5 0.8B trained |          +24.89 pp |            -4.34 pp |
| boolq     | Qwen3 0.6B trained   |          +20.58 pp |            -8.78 pp |
| boolq     | Qwen3.5 0.8B trained |          +23.30 pp |            -7.37 pp |

BoolQ final evaluation uses the complete public validation split. Training excludes text/passage groups overlapping reserved splits. All duplicate final observations are retained. Calibration uses its own frozen rows and never changes the training recipe. One seed does not estimate training-run variance; pretraining exposure to these public benchmarks is unknown. Recorded provider usage is not an all-in invoice. Full metrics, confidence intervals, coverage, calibration fits, costs and provider identities are retained in the losslessly compressed JSON reports. SHA-256 values identify the original uncompressed report bytes.

## Reproduction

Run from the repository with the local Overmind MCP, CLI account connection, Docker Workshop runtime and the pinned Modal release available. The evidence JSON contains immutable source revisions, checksums, cells, packages, experiments and provider calls.

```sh
.venv/bin/python tests/evidence/decision_benchmarks_fetch.py /tmp/decision-benchmarks-20261010
.venv/bin/python tests/evidence/decision_benchmarks_live.py data
.venv/bin/python tests/evidence/decision_benchmarks_live.py verify
.venv/bin/python tests/evidence/decision_benchmarks_live.py prepare
.venv/bin/python tests/evidence/decision_benchmarks_live.py experiments
.venv/bin/python tests/evidence/decision_benchmarks_live.py launch
.venv/bin/python tests/evidence/decision_benchmarks_live.py observe
.venv/bin/python tests/evidence/decision_benchmarks_report.py
```

The saved request keys recover these runs. Repeating launch does not create independent replications. Inspect readiness, costs and existing state before any fresh experiment.
