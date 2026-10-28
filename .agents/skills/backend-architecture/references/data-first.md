# Data-first model workflows

Data-first projects start from uploaded data and the original task brief (`Dataset.brief`); repository scans and capabilities are optional. Console, REST and MCP share every service below. Reconcilers run on `control`; expensive work runs on `batch` with short database claims.

## Services

| Service                                                         | Owns                                                                                                                                   |
| --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `services/datasets/exploration.py`                              | `DataExploration`: freezes a cell for whole-source profiles or a separate derived chain; sampling never silently changes coverage      |
| `services/datasets/partition_plans.py`                          | `DataPartitionPlan`: seeded role fractions, groups, stratification and holdouts across train, development, calibration and final roles |
| `services/training_plans.py`, `training_plan_contract.py`       | Immutable `TrainingPlan` snapshots of task, evidence and optional pilot selection                                                      |
| `services/training_experiments.py`                              | `TrainingExperiment`: explicit variants under one job group, a shared protocol, development-only checkpoint selection                  |
| `services/native_evaluation.py`, `native_evaluation_monitor.py` | `NativeEvaluationPlan` comparisons of foundations, trained artifacts and qualified external adapters                                   |
| `services/decision_performance.py`, `decision_providers.py`     | `DecisionPerformanceRun` client workloads, without activation; the System One adapter and its request ledger                           |

## Rules

- Partitions keep exact content, declared groups, synthetic lineage and duplicate observations together. Calibration and final members have eval intent. Group and content checks report exact overlap only; they do not prove absence of paraphrase or pretraining contamination.
- Final and calibration data never select a recipe. Full experiments need audit, baseline and sanity reports but no pilot. Optional pilot selection needs a measured task metric from a matching settled pilot with a verified checkpoint.
- Experiments prepare a configuration-bound forecast before launch and may enforce user-defined planning constraints. Unmeasured or over-budget quotes do not recommend launch. Constraints are not an invoice cap.
- Native comparisons save a draft, prepare immutable suites in the background and launch explicitly. Calibration is a barrier: it is fitted (global, then per kind, by local L-BFGS-B) before final predictions, and stays separate from final scoring. References stay local and never enter a worker.
- Every paid call is journaled. Unacknowledged native submissions and bare external intents are never replayed; a paused native collector resumes only with its exact recorded `call_id`. Explicit resume of a local stage (`verify_inputs`, `fit_calibration`, `score`) fences the interrupted lease and reuses saved inputs without provider calls. Pause stops new claims; it does not cancel remote work.
- Transient stage failures retry with 15/30-second backoff, at most three attempts. External timeouts and 429/5xx responses retry only missing rows, keeping uncertain costs in the attempt ledger.
- Exhausted collection scores the verified predictions that exist. Results report `completion.status`, per-suite coverage and issues; partial results are `completed_with_issues`, never silent success or zero-filled scores.
- Database receipts keep aggregate and per-benchmark metrics; complete diagnostic slices live in the checksum-bound downloadable report.
- Benchmark workers publish checksum-bound prediction chunks under the exact call ID. The collector verifies runtime, input and artifact identity before indexing into a local SQLite projection beside the suite. Missing indexes rebuild from retained predictions, never from new inference.
- Per-label precision, recall and F1 (`view=labels`) need hard references with a fixed option order. Missing predictions and zero denominators stay distinct from wrong answers. Ordinal means never become probability references.
- Prediction reuse is explicit and never stands in for independent repetitions or performance measurements. Nothing here activates a model in production.
