# Workshop preparation verification — 2026-10-10

## Behavior

The canvas presents the selected successful process, including recorded source,
script, derivation, attachment and partition dependencies across datasets.
Corrections rerun the same recipe family from its original source and replace the
visible cells. The version history and restore controls are removed. Immutable
execution records and consumer-pinned artifacts remain internal evidence; this
change does not erase recorded training inputs or rewrite executed code.

Project-owned recipes accept explicit `batch_rows` for independent row transforms
and a `consumer` contract for nested training/evaluation validation in preview and
publication. All batches and steps publish atomically. Empty branches still
execute. Bytecode caches are omitted from CLI directory packages; invalid column
types now return an actionable error. MCP instructions require original source
landing, retained code, preserved target meaning and a clean replacement process.
MCP readiness reuses the prerequisite validation result, eliminating a duplicate
full-population scan.

## Environment and boundaries

Local Compose API `http://localhost:8000`, MCP `/api/mcp/`, Console port 5173,
PostgreSQL port 5432, and the dedicated Workshop runner. The runner was restarted while idle and later deliberately interrupted during a test-owned job to verify recovery. Its approved image is
`sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea`.
The saved CLI account connection must target the same localhost API.
No training, inference, paid provider call, production deployment or push occurred.

## Automated checks

Commands run from the repository root unless specified. Each command was captured
to a separate log in `/private/tmp`; the suite counts overlap and must not be summed.

| Command                                                                                                                                                                                                                                                                                                                                        | Observed result                                                                                                    |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `WORKSHOP_TEST_IMAGE=sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea uv run --no-sync pytest tests/test_workshop_preparation_journey.py tests/test_workshop_package_transfer_journey.py tests/test_reusable_workshop_journey.py tests/test_mcp_finetuning.py tests/test_mcp_prompts.py tests/test_mcp_resources.py -q` | 139 passed, including real Docker isolation and timeout checks                                                     |
| `uv run --no-sync pytest tests/test_workshop_preparation_journey.py tests/test_mcp_finetuning.py tests/test_mcp_research_journey.py tests/test_mcp_manifest.py tests/test_mcp_catalog.py -q`                                                                                                                                                   | 91 passed                                                                                                          |
| `uv run --no-sync pytest tests/test_workshop_preparation_journey.py tests/test_workshop_package_transfer_journey.py tests/test_mcp_prompts.py tests/test_mcp_resources.py -q`                                                                                                                                                                  | 57 passed, including corrected lineage, sibling deliverables, empty batches, final-batch failure and pinned inputs |
| `PYTHONPATH=overmind uv run --no-sync pytest overmind/tests/test_transfer_connection.py overmind/tests/test_dataset_cmd.py overmind/tests/test_resumable_transfer.py overmind/tests/test_checkpoint_transfer_journey.py -q`                                                                                                                    | 53 passed; 5 local HTTP fixtures initially blocked by sandbox; those 5 passed on a scoped-permission rerun         |
| `cd frontend && bun run test`                                                                                                                                                                                                                                                                                                                  | 863 passed across 115 files                                                                                        |
| `cd frontend && bun run typecheck && bun run check:all`                                                                                                                                                                                                                                                                                        | Passed; the new Retry button initially violated control rules and was corrected/retested                           |
| `make generate_api_client`                                                                                                                                                                                                                                                                                                                     | Generated the preparation endpoint and typed graph models                                                          |

Local PostgreSQL access was initially blocked by the sandbox and passed after the
scoped test command was approved. No service credentials were printed.

## Real Titanic source through MCP and CLI

Replay:

```bash
PYTHONPATH=overmind uv run --no-sync python tests/evidence/workshop_preparation_replay.py \
  --project a279d834-899c-4ac6-888a-157f0c7d9cf8 \
  --source-dataset 74788292-a65c-43d6-9524-a87d0ab1af03 \
  --source-cell 292a84b5-975a-4252-bb4b-f873619c8af7 \
  --titanic-package /private/tmp/titanic_decision_pipeline_clean \
  --output tests/evidence/workshop-preparation-live-results.json
```

The original retained Titanic package can also be recovered through
`overmind dataset pipeline-download 076ce7c4-d2ed-4ddc-a3b2-b180519c3bc8 --project-id a279d834-899c-4ac6-888a-157f0c7d9cf8 --output pipeline.zip --json`
and extracted to the argument directory. The harness changes its consumer and
batch declarations, tests an invalid probability target, then corrects and
publishes the recipe. It uses the existing source through MCP rather than
reuploading a derivative as an unrelated source.

Observed:

- Invalid probabilities failed preview before publication.
- Repeating a stable request key returned the same job.
- 891 real rows passed five isolated batches.
- A corrected recipe omitted passenger ID/name from model evidence while retaining
  source identifiers and structured target provenance.
- The final process omitted the previous successful output.
- Train/development/calibration/final counts were 625/133/44/89.
- REST and MCP returned the same six nodes and five edges.

Output dataset: `ed19e18c-e150-41c8-b501-fda917fb4a9e`.
Selected output: `ae40f6f6-7246-43c8-8dc7-6581b91b3ba7`.
Partition: `2ba5250f-8bf3-465c-af63-63e1a08965ba`.
Receipts are retained in `workshop-preparation-live-results.json`.
Two harness mistakes (helper argument collision and the partition receipt key)
were corrected; the complete replay then passed. The interrupted earlier fixture
was not substituted for the successful final replay.

Browser verification used the existing Titanic train partition and the new output.
Both displayed six cells/five edges, correct dataset links, readable rows and
retained scripts. No history/restore controls or browser errors were present.
The Process popup showed `891 → 891 rows · 5/5 batches · decision_train validated`.

## Large existing Tasksource data

Replay:

```bash
PYTHONPATH=overmind uv run --no-sync python tests/evidence/workshop_large_preparation_replay.py \
  --project a279d834-899c-4ac6-888a-157f0c7d9cf8 \
  --source 0b0d60b5-d321-416d-8881-f5934558d337 \
  --restart-controller \
  --output tests/evidence/workshop-large-preparation-live-results.json
```

This is a retained identity transformation of the existing prepared source, not a
claim that its upstream lineage or target semantics have been repaired. No source
bytes are reuploaded. The final recipe requests 20,000-row batches, 1,024 MiB scratch and memory,
and a 300-second per-batch limit. The input has 1,034,657 rows and previously
measured expanded JSONL size 3,874,290,391 bytes before platform provenance.

The first attempt (`9f6e0cce-1be9-4e08-b642-e32f88d22c6b`) stopped progressing after 700,000 rows when its controller restarted. Docker retained no restart cause. It was cancelled through MCP, its container was removed, and no partial output was published. Recovery previously left interrupted jobs running until lease expiry; the controller now uses exclusive PostgreSQL ownership and fails interrupted jobs on startup. A deliberate live restart verified that behavior before an explicit retry. The retry receipt is `3080c5e3-8b24-48f3-996e-7b92d7afed74`; it failed with a confirmed Docker OOM event, as described below.

The separate MCP `check_finetune_readiness` replay completed in **46.067 seconds**
with `validation_enabled=false` and pre/post chat evaluations disabled. It
validated all **1,034,657** rows and returned technical readiness. See
`workshop-large-readiness.json`. This timing is one local observation, not a latency
SLA or a before/after benchmark under controlled load. Existing source target
semantics remain unspecified; technical readiness does not certify their meaning.

## Interrupted controller regression

Failure modes identified before implementing recovery: a restarted controller
leaves an owned job running until lease expiry; a second controller could
incorrectly interrupt a live owner; stale batch exit codes could appear to describe
the next batch; recovery could publish partial data or replay a script whose
outcome is unknown. Coverage uses real PostgreSQL ownership locks, saved MCP jobs,
source preservation and stable request-key replay, alongside a live Docker restart.

The controller and retained-pipeline regression command `WORKSHOP_TEST_IMAGE=sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea uv run --no-sync pytest tests/test_workshop_preparation_journey.py tests/test_reusable_workshop_journey.py -q` passed **36 tests**, including real container isolation. The recovery implementation initially used an unregistered Django database alias; the test exposed this and the corrected connection passed. One fixture assertion used the wrong active-cell property and was corrected.

Live browser verification exposed missing progress controls before the first published output. The Process control now shows the current attempt during execution and returns to the selected successful process after publication. It displayed measured batch progress during the full-source retry. Final frontend typecheck and design/contrast/control checks passed; changed SDK files also passed Ruff lint and formatting.

The next full retry reached 980,000 rows and Docker recorded an `oom` event and
exit 137. Recovery correctly failed it without publication. The controller held a
whole declared batch alongside decoded source rows and 10,000-row Parquet staging
buffers. The fix must bound simultaneous buffers, keep exact row order and mixed
scalar precision, preserve all nested values, and reject oversized runtime inputs
without killing the controller. A fresh-process wide-row storage replay records
peak RSS and checks every round-tripped row before the full-source retest.

Memory replay: `PYTHONPATH=. uv run --no-sync python tests/evidence/workshop_store_memory_replay.py --output tests/evidence/workshop-store-memory-results.json`. Fixture: 2,048 rows with 256 KiB text each, nested probability targets and integers above signed 64-bit range. The original row path peaked at **2,771.73 MiB**. Bounding writes exposed an additional unbounded DataFrame reader (**1,886.95 MiB**); both paths now use bounded buffering and every value is checked after storage. The final peak was **723.17 MiB**, retained in the JSON artifact. These are local fresh-process RSS measurements, not universal hardware guarantees.

## Cell preparation provenance

Failure modes specified before implementation: dataset-scoped script saves lose
association; request retries restore an older script; successful execution hides
unknown target semantics; findings disappear during transformations or fan-in;
claims on another source or fingerprint resolve the wrong uncertainty; edits
silently discard unrelated findings; UI and MCP disagree; long finding lists are
clipped; a source finding changes after a downstream resolution.
The journey test exercises the retained-package pipeline and shared REST/MCP
surfaces. The existing full-size cells and branch layout remain the presentation.

The cell checks implementation uses current, fingerprint-bound agent findings,
with complete status counts and paginated evidence. Agent claims remain attributed;
unknown target meaning, OCR limitations and unassessed coverage never become passes
because execution succeeded. Automatic dataset/script association, optimistic
updates, sibling isolation, fan-in, stale resolution and cross-project protection
are exercised through the same REST/MCP services.

Verification commands added for this implementation:

- `WORKSHOP_TEST_IMAGE=sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea uv run --no-sync pytest tests/test_workshop_preparation_checks_journey.py tests/test_workshop_preparation_journey.py tests/test_reusable_workshop_journey.py tests/test_mcp_datasets.py tests/test_mcp_catalog.py tests/test_mcp_resources.py tests/test_mcp_research_journey.py -q`: **104 passed**, including real restricted-container execution.
- `uv run --no-sync pytest tests/test_workshop_preparation_checks_journey.py tests/test_dataset_api.py tests/test_workshop_preparation_journey.py -q`: **31 passed** after correcting grid comparisons to use the actual recorded input. This overlaps the preceding suite; do not add the counts together.
- `bun run --cwd frontend test src/components/datasets/notebook`: **11 passed** across three files. Typecheck passed. `check:all` initially rejected text-labelled ghost pagination buttons; they were changed to the shared secondary style and all design checks passed.
- `make check-migrations`: no model drift; branch migrations passed the `origin/main` comparison. Local migrations 0036 and 0037 were applied. No existing dataset rows or artifacts were deleted.
- `PYTHONPATH=overmind uv run --no-sync python tests/evidence/workshop_preparation_checks_replay.py --project a279d834-899c-4ac6-888a-157f0c7d9cf8 --dataset ed19e18c-e150-41c8-b501-fda917fb4a9e --output tests/evidence/workshop-preparation-checks-live-results.json`: **passed**. Exact REST/MCP parity, inherited unassessed coverage on all four partitions and stale fingerprint rejection. The receipt contains exact cell IDs and observations.

The pre-implementation journey failed because the tools and association did not
exist. Subsequent tests exposed an authoring fingerprint mismatch, corrected by
retaining the dataset request identity in the immutable recipe. A fixture also
reused the same unique test email and was corrected. The browser exposed a real
comparison regression: a corrected cell showed the discarded attempt's values.
The reproducer observed `999` instead of the actual source value `1`; the fix and
API journey now use the receipt-bound single parent. Multi-parent transformations
do not invent a single comparison baseline.

Browser inspection retained the six full-size cells, five edges and all four
partition labels. Preparation checks expand inside cells with scope, evidence and
attribution, and the row grid shows the corrected values. Regenerating the client
briefly removed generated modules and caused Vite reload errors; a page refresh
after generation restores the loaded modules. No dev server was started.

The memory-corrected large replay initially failed at batch 50 because the declared
512 MiB scratch space allowed only 256 MiB of input. Measuring the original data
found a maximum 20,000-row batch of **267,391,698 bytes before added provenance**,
so the limit was legitimately exceeded. The final recipe declares 1,024 MiB each
for scratch and container memory. All 52 batches completed with 1,034,657 input
and output rows. Final validation and atomic publication completed successfully in
**1,075.03 seconds** of observed wall time (one local run). There were no added or
removed rows, no decision changes and no input-evidence removal. The selected
output fingerprint matches the execution receipt exactly.

Final run: `39afd427-f5c4-4928-ac98-4614511f6419`.
Dataset: `ecabb7ed-4b75-4f2b-8572-35223f1c414c`.
Output cell: `e5563a89-f9b2-4dfd-bd68-c1dc6c71babd`.
Fingerprint: `1d2d4f092e22c878b2ffd0c80ead6e768f1c198793d565bee7c43c32f9ebd612`.
The MCP verification confirms the current retained recipe, exactly the source and
output in the visible graph, three passed checks and one explicitly unassessed
semantic-suitability check. This certifies the identity replay's technical
preservation, not the correctness of the source's original target definitions.

The final readiness replay is repeatable with
`PYTHONPATH=overmind uv run --no-sync python tests/evidence/workshop_readiness_replay.py --record tests/evidence/workshop-large-readiness.json --expected-rows 1034657`.
It returned technical pass with task suitability explicitly unmeasured. An earlier
harness read the response at the wrong nesting level; a corrected retry was
interrupted by API hot reload. The final replay ran after edits settled and passed.

The full-source replay exposed stale runner availability during long postprocessing.
The controller now maintains its availability heartbeat throughout execution,
validation and publication. It does not advance the operation's progress clock.
The regression first failed, then the combined preparation journeys passed:
`uv run --no-sync pytest tests/test_workshop_preparation_journey.py tests/test_workshop_preparation_checks_journey.py -q` — **18 passed**.
This check exercises a deliberately delayed impact measurement, verifies heartbeat
freshness with unchanged forward progress, and verifies terminal publication.

After the successful full run, the idle local controller was restarted to load the
availability heartbeat fix. A 100-row preview through MCP completed on that
runtime, published no cell, and left the selected full output and fingerprint
unchanged. The receipt is included as `post_restart_preview` in the same artifact.
Verify an existing completed receipt and repeat that preview with:

```bash
PYTHONPATH=overmind uv run --no-sync python tests/evidence/workshop_large_preparation_replay.py \
  --project a279d834-899c-4ac6-888a-157f0c7d9cf8 \
  --source 0b0d60b5-d321-416d-8881-f5934558d337 \
  --output tests/evidence/workshop-large-preparation-live-results.json \
  --verify-only --preview-check
```

The final file audit covers 79 task files (including two intentional deletions):
none missing or ignored. Unrelated existing changes and untracked artifacts were
preserved. `git diff --check` and every applicable pre-commit hook passed; the YAML
hook had no matching files. No commit or push was performed.

## Source and status chips

The follow-up presentation change keeps the same cells and branches. Each check
uses a read-only split badge with its source on the left and status on the right.
Platform, Script requirement, Agent, File import and Saved review remain distinct.
Passed is green, failed is destructive, unresolved is warning and not assessed is
neutral. Evidence expands beneath each finding; stored scope, row counts and
inherited origin remain available. Built-in check descriptions are simpler in
the shared backend, so REST and MCP use the same descriptions. No validation
policy, status, API shape or stored assessment was changed.

Validation: `bun run typecheck`, `bun run lint`, `bun run check:all` from
`frontend/`, plus Ruff check/format for
`overbae/services/datasets/preparation_checks.py`, all passed. This was a
presentation/copy change; the test suites were not rerun.

Browser replay against the existing local fixture:

1. Open `http://localhost:5173/datasets/ed19e18c-e150-41c8-b501-fda917fb4a9e?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8`.
1. In Build survival decision examples, collapse Data and expand Preparation checks.
1. Select Reset view. Confirm Agent | Not assessed, Platform | Not assessed, Script requirement | Passed, and Platform | Passed.
1. Open Evidence beneath Row count preservation. It shows 891 input and output rows and the declared preservation requirement.

Observed six cells, five edges, all expected labels and working evidence
disclosure. Browser error log was empty. Screenshot:
`/private/tmp/workshop-source-status-chips.png`.
