# Workshop review: Tasksource and Titanic

Reviewed on 2026-10-10 America/Los_Angeles. This is an investigation and design
recommendation, not an implementation change. Evidence comes from both complete
Codex thread histories, read-only local MCP receipts, the current source code, and
a full serialization scan of the retained local Tasksource training file.

## Conclusion

Retained transformation code is useful even for a dataset used once: it explains
how training examples were made and supports repair, inspection and reproduction.
Reuse is an additional benefit, not the reason every preparation needs evidence.

Keep project-owned immutable recipes and dataset-bound execution receipts. Make
the Workshop canvas show the complete preparation lineage across source datasets,
transformations, partitions and consumer handoffs. Do not make a capability own
every source-specific transformation. Its responsibility is the task contract.

Both agent behavior and product limitations contributed. The Tasksource agent
bypassed retained transformations. The Titanic agent used them successfully, but
partitioning moved the consumed data into a canvas that hides its upstream script.

## Observed runs

| Evidence                       | Train a typed decision model                                | Train Titanic survival model                       |
| ------------------------------ | ----------------------------------------------------------- | -------------------------------------------------- |
| Thread                         | `01a12884-bb48-7f40-8fb6-62fcb64f8d6a`                      | `01a12868-eb50-7091-9800-3733ead8b857`             |
| Local project                  | `a279d834-899c-4ac6-888a-157f0c7d9cf8`                      | Same                                               |
| Source                         | Tasksource default train/validation/test                    | Titanic CSV, 891 passengers                        |
| Saved recipes                  | 0                                                           | 1                                                  |
| Pipeline runs                  | 0                                                           | Preview plus publication                           |
| Lineage-bound external imports | 0                                                           | 0; platform execution was used                     |
| Dataset drafts created         | 8, two sets of four roles                                   | 1 before platform partitioning                     |
| Outcome in history             | Interrupted after readiness timed out; no training launched | Training and subsequent paired benchmark completed |

The MCP endpoint was verified as `http://localhost:8000/api/mcp/`. The review did
not submit any transformation, training, evaluation, deployment or activation.

### Tasksource: preparation happened outside the Workshop

The agent downloaded and transformed Hugging Face rows directly in temporary local
scripts, then uploaded the transformed Parquet files as original sources. It did
not upload a transformation package or call the external transformation import API.
The platform therefore retains those Parquet bytes but not their production code
or a verified relationship to the original upstream source artifacts.

The original preparation failed technical validation for unsupported semantic
labels and the flat `noul` encoding. The second preparation changed `noul` to
explicit No/Yes probabilities, but wrote `target_provenance` as a JSON string
instead of an object. The third removed that field. All three versions rewrote the
full million-row training split before the repair was accepted.

- Full local conversions took 276.468, 295.582 and 258.933 seconds: 13.85 minutes.
- The two large upload commands, including landing waits, took 270.789 and
  290.813 seconds. Both completed successfully. These were not observed broken
  byte transfers.
- Eight independently rooted datasets were created instead of preserving one
  source-to-output process with the corrected transformation replacing its cells.
- The final training dataset is `0b0d60b5-d321-416d-8881-f5934558d337`, active cell
  `b80a7b3c-27de-4556-9e7b-35202214d5b6`. Live inspection returns one Source cell,
  an empty script, `transformation.execution=source`, and no pipeline runs.
- The final cell's contract records `unspecified_distribution: 1034657` and
  `objective=decision_cross_entropy`.
- The readiness request failed after 120.007 seconds. A timed-out request is not
  evidence that the uploaded dataset failed or that server work was cancelled.

The agent explicitly chose new datasets to avoid a repair cell. The intended
behavior is to replace the transformation within one clean process, without
presenting repair attempts as history. The existing
external-import path would at least have preserved source lineage, although its
current contract does not require retained producer code.

There are unresolved semantic issues despite the successful format check:

- The source card distinguishes mean-derived score encodings from annotator vote
  distributions. The final script supplies all of them as probabilities with no
  target-semantics declaration. For example, score families include `glue/stsb`,
  `sick/relatedness` and `scirepeval/search`. Their training interpretation needs
  source-specific evidence; removing the declaration does not resolve it.
- Source values are retained in metadata, so repair is possible. This review does
  not claim all score families should become means: ordinal classes, empirical
  histograms and scalar scores require separate treatment.
- The download calls use `load_dataset(DATASET, split=..., streaming=True)` with
  no pinned Hub revision. Retained final bytes are reproducible, but redownloading
  with that script is not guaranteed to recover the same upstream version.
- Evaluation tags are heuristics. Four or more options becomes
  `many_similar_options`; that does not demonstrate similar answer choices.
  Keyword tags also do not constitute independent challenge tests. At the point
  the thread was interrupted, the requested challenging evaluation suite and
  before/after evaluation had not been completed.
- Development/calibration were divided by group_id in local code; no platform
  partition receipt or completed full cross-role overlap audit was produced.
  This does not establish that leakage occurred.

Relevant temporary evidence: `/private/tmp/tasksource_jev_overmind/`, containing
the original, v2 and v3 scripts and each file manifest. These files are not a
substitute for a retained platform package.

### Titanic: valid retained execution, difficult authoring and broken continuity

The agent uploaded the raw CSV, authored a Python transformation, uploaded its
package, registered an immutable revision, validated it, previewed 20 rows and
published 891 rows. The live publication receipt records preserved identities
and successful row checks.

- Source dataset: `74788292-a65c-43d6-9524-a87d0ab1af03`.
- Package: `076ce7c4-d2ed-4ddc-a3b2-b180519c3bc8`.
- Revision: `2eda667a-da7e-4ec6-9f01-80cc814c2386`.
- Published run: `293c4a7f-870b-461e-aa6e-00709c33f7f9`.
- Output cell: `c1130b35-57b3-4d7b-a1fa-6f90696d05d5`.
- Publication receipt elapsed time: 0.943 seconds; script execution: 0.2057 seconds.

The first package upload failed because `json` is not an accepted manifest column
type; `object` is. The CLI replaces the server's specific package validation
message with generic troubleshooting text. The agent had to inspect backend code
to diagnose the type mismatch. A subsequent syntax check created `__pycache__`,
which made the directory uploader reject the package. Failed file-copy attempts
before creating the clean directory added avoidable agent friction.

The recipe also hardcodes both minimum and maximum output rows to 891. That works
for this one run, but defeats reuse on a differently sized compatible file.
`preserve_rows` expresses the reusable requirement; an exact population assertion
belongs to the particular execution unless population size is part of the task.

Partitioning produced train/development/calibration/final datasets. Inspecting the
consumed train dataset `adc58aaa-d0bd-5ec7-9cf3-226bb7734abc` returns one Source cell
with an empty script and no transformation run. Partition parent identities are
retained separately, but the canvas only connects cells present in the currently
opened dataset. Thus the training handoff loses visible continuity with the
successful transformation. This is a product presentation gap, not evidence that
the Titanic script was never retained. The conclusion is based on MCP receipts
and frontend code; this review did not operate the browser.

The later benchmark also encountered a participant-key format error and initially
returned project-root links rather than direct run links. Those are separate MCP
ergonomics issues, not failures of the transformation runtime.

## Platform limits demonstrated by this investigation

### The current runtime is not sufficient for this full prepared artifact

The Workshop runner serializes the entire step input to JSONL and transfers it to
one restricted container. Input and output are each limited to half the declared
scratch space. The maximum scratch allowance is 4096 MiB, so each side is capped
at 2 GiB; the default allowance makes the limit only 256 MiB per side.

A read-only scan of every local prepared training row measured:

| Measurement              |         Value |
| ------------------------ | ------------: |
| Rows                     |     1,034,657 |
| Compressed Parquet bytes |   789,129,431 |
| JSONL bytes              | 3,874,290,391 |
| JSONL GiB                |      3.608214 |
| Scan duration            | 16.80 seconds |

The JSONL measurement excludes provenance added during landing, so it is already
over the limit before that additional overhead. This proves the full prepared
artifact cannot pass through a single current script step unchanged. It does not
prove that every smaller projection of the original Hugging Face data would fail.

The artifact duplicates decision input into training and evaluation columns and
duplicates several metadata fields. Consumer-specific projections would reduce
this overhead. The engine still needs bounded execution over batches or shards,
durable progress, and atomic publication of the logical output. Arbitrary global
joins, grouping and deduplication must explicitly declare whole-source execution;
they cannot be silently treated as independent row batches.

### Readiness performs expensive work synchronously and redundantly

`_readiness_sync` calls `finetune_prerequisite_report`, which calls
`validate_dataset`; `_readiness_sync` then calls `validate_dataset` again. Native
validation scans all decision rows and checks separate-set contamination.

The 120-second timeout is observed. Duplicate synchronous scans are confirmed by
code, but their individual contribution to that particular request was not
profiled. The repair should cache verified measurements by immutable input and
validator identity and run missing large measurements as durable background work.
It should not weaken the validation or simply extend the client timeout.

### Publishing hides several substantial stages

Landing reports `publishing` before Parquet writing, contract validation,
statistics, profiling and final publication. The final train landing took about
282 seconds after dispatch. The thread repeatedly interpreted unchanged publishing
timestamps without counters. Code confirms multiple full-source operations under
that label; no per-stage historical timing was retained to attribute those 282
seconds precisely.

### Validation and discovery need a shorter path

Static package validation checks source-column presence. The manifest only
expresses top-level types, and previews take the first N rows. Neither establishes
coverage of heterogeneous decision kinds or nested target semantics. A small
representative preview should include each declared source family and boundary
case, followed by full output validation before publication.

The contract should expose valid nested decision examples and structured field
errors directly. The agent should not need repository access to learn the public
wire format. In current MCP responses, preparation-context prose is heavily
truncated, while more detail is spread across resources and installed guidance.

## Ownership and product recommendation

| Object                    | Responsibility                                                                                                                                |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Project                   | Owns access to retained recipe packages and immutable revisions.                                                                              |
| Recipe                    | Describes a transformation, its input assumptions, output contract and parameters. Reuse can remain private to one task until demonstrated.   |
| Preparation task/workflow | Connects the user's purpose, source snapshots, applied revisions, partitions and output roles. It is the useful scope for the canvas.         |
| Execution                 | Binds exact inputs, revision, parameters, runtime, checks and outputs. Belongs to the particular preparation, even when the recipe is shared. |
| Dataset version           | Stores an immutable data artifact with links to its producer and ancestors.                                                                   |
| Capability                | Defines required task meaning and input/output behavior. References applicable workflows; does not own every ingestion permutation.           |

Much of this already exists. `DatasetPipeline` and its package are project-owned;
`DatasetPipelineRun` binds datasets and cells. The principal missing piece is a
coherent cross-dataset preparation view and consistent capture of every derived
artifact. Start by joining existing pipeline, partition and consumer receipts;
do not add a mandatory parallel workflow database merely to redraw those facts.

Retain one-off recipes for evidence without automatically promoting them into a
library of reusable recipes. Offer explicit promotion and discover compatible
recipes by input assumptions and intended output contract. Parameter changes
should reuse code only when meaning is unchanged; semantic changes require a
reviewed revision or attributed derivation.

To reduce permutations, separate source adaptation from task representation:

`source format -> preserved task evidence -> consumer-specific examples`

CSV and JSON feeds with equivalent meaning can use different source adapters and
the same decision encoder. A new feed should not require copying a full
capability-specific pipeline. Some source/task combinations genuinely require
custom interpretation; composition cannot eliminate those differences safely.

Use scripts for deterministic transformations. Semantic labeling, generation or
external provider work should appear as attributed preparation steps with retained
producer code/configuration, inputs, outputs and provider receipts where available.
Do not describe such executions as deterministic or independently verified.
Unchanged already-prepared imports need provenance and consumer checks, not a
ceremonial identity script.

## Recommended implementation order and acceptance evidence

1. Fix capture and visibility: preserve derived-output lineage, retain producing
   code, link partition members to upstream transformations, and provide direct
   run/output links through Console and MCP. Legitimate external imports remain
   supported with truthful attribution.
1. Fix authoring: provide a generated package starter, structured manifest errors,
   an explicit file inventory that avoids bytecode artifacts, and nested consumer
   validation on representative previews. Keep safety and immutability checks.
1. Fix scale: resumable grouped source acquisition, batch/shard execution for
   compatible scripts, measured landing stages, and asynchronous cached readiness.
   Reuse the same source bytes while revising scripts. The runner remains isolated;
   source acquisition is separate from network-disabled transformation execution.
1. Clarify reuse: surface the existing project ownership, keep one-off retained
   recipes out of the shared library by default, and compose adapters/encoders
   around explicit contracts rather than capability-specific copies.

Acceptance should replay these real journeys through MCP and inspect the resulting
Console representation. Titanic must show the original CSV, retained transform,
all partition roles and training handoff from the consumed dataset. Tasksource
must validate representative kinds before a full execution, preserve evidenced
target meanings, avoid repeated source upload after a script repair, retain split
and evaluation evidence, finish readiness without an RPC timeout, and expose
honest progress. A reused recipe must work on a compatible differently sized
source; an incompatible source must fail with a precise explanation.

## Code evidence and reproduction

- Ownership: `overbae/models/dataset_pipeline.py:7`, `:41`, `:58`.
- Project recipe discovery: `overbae/services/datasets/workbench.py:918`.
- Partition parent storage and separate landing: `overbae/services/datasets/partition_plans.py:324` and `:340`.
- Dataset-only canvas edges: `frontend/src/components/datasets/notebook/flow.ts:5`.
- Canvas source scope: `frontend/src/components/datasets/notebook/notebook-page.tsx:56`.
- Runner limits and JSONL staging: `overbae/services/datasets/pipeline_packages.py:129`, `pipeline_runner.py:236`, `:308`, `:389`.
- Package errors and packing: `overmind/overmind/transfer_connection.py:58`, `dataset_cmd.py:65`.
- Duplicate readiness calls: `overbae/services/mcp/tools_finetuning.py:426`, `:467`, `overbae/services/finetuning_prereqs.py:145`.
- Native full validation: `overbae/services/finetuning_validator.py:85`.
- Publishing stages: `overbae/tasks/datasets.py:249`, `overbae/services/datasets/land.py:100`, `measure.py:16`.
- Target validation/default objective: `modal_shared/decisions.py:60`, `overbae/services/datasets/contract.py:148`.
- Final preparation code: `/private/tmp/tasksource_jev_overmind/prepare_tasksource_jev_v3.py:118` and `:202`.
- Source semantic evidence: retained `/private/tmp/overmind-tasksource-jev-6908093f/README.md:230`. It distinguishes mean-derived score encodings from vote distributions; the reviewed agent's own download was not revision-pinned.

Repeat the MCP reads with the project and dataset/run IDs above. Reproduce the
JSONL measurement from the retained file with the workspace Python environment:

```sh
.venv/bin/python - <<'PY'
import json
import pyarrow.parquet as pq

path = '/private/tmp/tasksource_jev_overmind/prepared_v3/tasksource_jev_default_train.parquet'
count = size = 0
for batch in pq.ParquetFile(path).iter_batches(batch_size=2048):
    for row in batch.to_pylist():
        count += 1
        size += len((json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n').encode())
print({'rows': count, 'jsonl_bytes': size, 'jsonl_gib': size / 1024**3})
PY
```

This was a read-only diagnostic scan, not a regression-suite run. No production
code, live datasets or provider jobs were changed. The browser was not operated.
