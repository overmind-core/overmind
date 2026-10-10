# Benchmark preparation and run investigation

2026-10-10, local MCP `http://localhost:8000/api/mcp/`, project
`1e3f3e92-b50d-4590-85ed-97921d132d3c`.

## Source failures and verification

The script-backed `PolyAI/banking77` repository has no currently accessible
`refs/convert/parquet` revision (HTTP 404). The current `mteb/banking77` Parquet
files contain 9,993 train and 3,076 test rows, fewer than the published benchmark.
The same pinned repository's JSONL files retain 10,003 and 3,080 rows. Downloaded
those files, verified all labels against PolyAI's class-index map, and compared
the complete `(text, category)` multisets against the original CSVs. Both original
CSV SHA-256 values match PolyAI's retained download checksums. No final duplicate
observations were discarded.

The first partition-verification attempt compared cell-local `source_row` numbers
across cells and failed. Partition landing intentionally renumbers local rows;
the original file row is retained in `_overmind_provenance.file.row`. Corrected
the harness to use that identity. Also separated partition-export filenames from
downloaded source filenames. The rerun verified every row, original record,
reference distribution, model input and declared group across all twelve cells.
This was a harness correction; no platform behavior was changed.

## Frozen role counts

| Benchmark | Train | Development | Calibration | Final | Excluded training observations |
| --------- | ----: | ----------: | ----------: | ----: | -----------------------------: |
| Banking77 | 8,045 |         943 |       1,008 | 3,080 |                              7 |
| SST-5     | 7,720 |       1,101 |         820 | 2,210 |                              4 |
| BoolQ     | 6,372 |         790 |         826 | 3,270 |                          1,439 |

Exclusions are retained in the audit cell. SST-5 excludes three training rows
overlapping final text and one overlapping development text. BoolQ groups shared
passages, including different questions, before allocation. All roles contain
every declared class. Backend experiment preparation independently reports zero
exact-input or declared-group overlaps between training/development and
calibration/final cells.

## Exact training preparation

Every preparation passed with no issues or truncated examples. Counts include
training plus development rows; only the train role receives optimizer updates.

| Benchmark | Model        | Prepared rows |     Tokens | Maximum sequence |
| --------- | ------------ | ------------: | ---------: | ---------------: |
| Banking77 | Qwen3 0.6B   |         8,988 | 11,969,387 |            1,408 |
| Banking77 | Qwen3.5 0.8B |         8,988 | 11,996,257 |            1,411 |
| SST-5     | Qwen3 0.6B   |         8,821 |  1,566,597 |              232 |
| SST-5     | Qwen3.5 0.8B |         8,821 |  1,592,250 |              235 |
| BoolQ     | Qwen3 0.6B   |         7,162 |  1,812,648 |            1,002 |
| BoolQ     | Qwen3.5 0.8B |         7,162 |  1,831,223 |            1,003 |

Pinned training release:
`07e8a773f7d959be0cef1d5dff328df1fea81a8377fe14c2c1e5163038868053`.
The three saved forecasts expose the current H100 rate of $3.95/hour. Their
durations and total costs are unmeasured for these recipes, not zero. The
three-hour provider runtime limit is not an all-in spending cap.

The complete reproducible MCP/CLI receipts are in
`decision-benchmarks-results.json`. Source download and original-byte verification
are in `decision_benchmarks_fetch.py`; the immutable transformation package is in
`decision_benchmarks_pipeline/`. `decision_benchmarks_live.py` prepares, verifies,
launches and observes the saved experiments without duplicate submissions.

All six training jobs and all three five-participant comparisons completed.
The measured results are in `decision-benchmarks-results.md`; failures and
recoveries encountered after preparation are recorded below.

## Live MCP monitoring failure and repair

BoolQ's hundreds of distinct questions made a single retained development check
exceed the MCP 128 KiB limit. Retrying with `limit=1` failed identically: the
per-question metrics and assessment arrays were inside one check, not separate
pageable checks. Training continued successfully; the inspection path was broken.

Reproduced with a database-backed MCP/REST journey using 800 distinct question
families and 1,600 assessment findings. The original code failed with
`training_evidence_too_large`. MCP overviews now expose these collections as
counted, checksum-bound `field` JSON Pointers. The shared receipt reader pages
exact object entries and array items and supports deeper fields, scopes every
receipt to its job, and never calls the provider. Full stored receipts and the
Console's existing metrics remain intact.

Validation: 81 monitoring/quality/MCP tests plus 37 catalog/field-check tests
passed. Regenerated the OpenAPI client; frontend typecheck and pre-commit passed.
`decision_benchmarks_monitoring.py` then paged the actual six benchmark runs and
reconstructed every inspected collection with its recorded checksum. The pinned
worker release was verified unchanged. The initial replay harness used `id`
instead of the experiment receipt's `job_id`; corrected the harness and reran.
Live receipt sizes and collection identities are in
`decision-benchmarks-monitoring.json`.

Reproduce the repair checks:

```sh
.venv/bin/pytest tests/test_training_monitoring_journey.py tests/test_training_quality.py tests/test_training_monitoring_receipts.py tests/test_mcp_finetuning.py tests/test_mcp_catalog.py tests/test_training_field_checks.py -q
PYTHONPATH=. .venv/bin/python tests/evidence/decision_benchmarks_monitoring.py
```

The live replay requires the same saved local MCP account connection and project
access as the benchmark harness. It only reads retained evidence.

## BoolQ score-publication failure and recovery

At 20:28:34 UTC, Postgres was killed while updating BoolQ's completed evaluation
record. The database container's `memory.events` confirmed one OOM kill. The
retained JSON report was 70,860,363 bytes: per-question diagnostic slices dominated
its size, and the implementation embedded the complete report in both results
and the scoring call receipt. The database recovered, but the abandoned local
scoring lease left the plan unfinished. All predictions and the report artifact
were already retained.

Paused this comparison through MCP while investigating. A regression with 240
distinct questions reproduced an 8,068,751-byte database payload. The repair keeps
aggregate and per-benchmark metrics in results, retains all diagnostic slices in
the downloadable artifact, and stores its byte count and SHA-256 in the scoring
receipt. Explicitly resuming a paused local stage fences the old lease and reuses
saved inputs; unresolved provider submissions still require their original call
identity. Console comparison metrics and full report downloads remain available.

Twenty-one native-evaluation, data-first and MCP workflow tests passed, including
the large-report regression and interrupted local-stage recovery without provider
submission. Resumed only `stage="score"` through the local MCP. The real BoolQ
comparison completed. Postgres now holds 31,954 bytes of calls and 149,802 bytes
of results for this plan; its OOM kill counter stayed at one. The full report
download matches SHA-256
`70dc44b6920a7219c0dcacd775ee96e16e7d1571d352dfde9f87186997b318a0`.
All five participants scored all 3,270 final rows with no missing, invalid or
incompatible predictions. No provider inference was rerun for this repair.

Reproduce the regression and recovery verification:

```sh
.venv/bin/pytest tests/test_native_evaluation_plan.py tests/test_data_first_workflow.py tests/test_mcp_data_first.py -q
```

MCP recovery used project `1e3f3e92-b50d-4590-85ed-97921d132d3c`, evaluation
`58a99159-2626-4af8-a960-457c09112b8e`: `pause_native_evaluation`, then
`resume_native_evaluation(stage="score")` after the repair. `get_job` confirms
completion and supplies the report URL. The report generator verifies its checksum
and saves its exact bytes with lossless gzip compression.

## Interrupted Banking77 collector

The local code reload sent SIGTERM to a batch worker while it was collecting
Banking77's Qwen3.5 calibration result. Worker logs recorded `WorkerLostError`
and restoration of two unacknowledged messages. The stage retained its original
provider call ID but also retained the interrupted collector lease, delaying
collection. This interruption was caused by the local repair deployment.

Added a failing regression for recovering a paused provider collector through
its exact recorded call ID. Missing or different IDs remain rejected. The shared
resume service now fences that interrupted lease and observes the same call;
it cannot launch another native prediction request through this recovery path.
The native workflow, MCP and catalogue suite passed 31 tests after the fix.

Paused evaluation `8baf98f1-6109-4bb5-a03a-792e0e69215c` through MCP, then resumed
`stage="calibration_candidate-1"` with
`call_id="fc-01M4KR5R35HHPDDSYYJPDGKD2D"`. The same recorded call completed and the
saved experiment advanced to its planned final predictions. No calibration
inference was repeated. Pre-commit passed before this recovery; subsequent
benchmark work changes only evidence files and does not reload workers.

## Final acceptance

All six jobs completed three full epochs on their frozen training cells. Each
selected checkpoint passed 32-decision reload verification with maximum absolute
probability error 0.0. All 30 retained development checks completed without error.
The live MCP evidence replay reconstructed all 108 per-question metric and
assessment collections with matching checksums; the largest complete default
training overview was 21,565 bytes. The worker release remained unchanged.

All five participants scored every final row in each benchmark: 3,080 Banking77,
2,210 SST-5 and 3,270 BoolQ observations, with zero missing, invalid or incompatible
predictions. All resolved Jev identities were `typesafe/jev-1.13-20260917` in both
calibration and final passes. The report generator verified participant/job
identity, three-epoch execution, checkpoint reload, complete coverage and report
checksums. Lossless gzip files retain the complete downloaded report bytes.

Across the changed surfaces, 140 distinct backend tests passed (81 monitoring,
quality and fine-tuning tests; 37 catalogue and field-check tests; 22 native
comparison and data-first workflow tests). The catalogue tests also passed again
after recovery-tool guidance changed. The OpenAPI client was regenerated;
frontend typecheck, pre-commit and diff whitespace checks passed.

Results: trained Qwen3/Qwen3.5 accuracy was 92.66%/93.57% on Banking77,
55.75%/54.30% on SST-5 and 82.75%/84.16% on BoolQ. Jev scored 80.29%, 58.64% and
91.53%, respectively. Calibration and probability metrics, confidence intervals,
training-selected majority baselines, costs and exact run identities remain in
the summary and full reports. These are one-seed measurements of the frozen
recipe, not an estimate of training-run variance.
