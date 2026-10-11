# Workshop full-suite and product performance review

Date: 2026-10-08. Scope: the current `codex/general-decision-training` checkout,
existing local Compose deployment, Workshop MCP catalogue and file-transfer CLI.
No paid training, evaluation or inference jobs. The user authorized fixes and
retesting during the run. Changes stayed in this checkout; no worktree, commit,
push or UI redesign was created.

## Outcome

The final live replay passed **49/49 scenarios**, including **all 16 Workshop
tools**, three supporting tools and **21 PDF cases**. All 100,000/1,000,000-row
workflows, source-preservation checks, authorization boundaries, retries,
cancellation/recovery and exact partition membership checks passed. The replay
created no billable usage and removed only its disposable user/projects; the
existing dataset count returned to 126.

The frontend passed **854/854** tests and the configured SDK suite passed
**595/595**. The broader backend suite is **not fully green**: its latest full run
has **4,046 passes, one import timing-budget failure and 15 optional-environment
skips**. That timing failure was investigated but not resolved. No test timeout or
assertion was relaxed. Therefore the correct conclusion is complete live
Workshop catalogue coverage for this matrix, not “100% product readiness.”

The factual MCP defects found here were fixed and retested. Structured external
execution/semantic-assessment receipts, multi-source lineage, remaining resource
bounds and workflow-level experience analytics are still product gaps.

Machine-readable evidence: [baseline](workshop-performance-baseline.json) and
[final replay, fixture hashes, earlier failures and resource observations](workshop-performance-final.json).

## Assessment standard

The platform must supply complete, accurate, inspectable facts. The native coding
agent owns articulation. A missing page count, inaccessible source, ambiguous
failure or stale capability description is a product defect even when the
underlying job succeeds. More narration is not the proposed remedy.

Separate three questions:

1. Does the operation preserve data and produce the requested technical result?
1. Can an agent discover the relevant facts and safely decide what to do next?
1. What latency, resource use and transfer cost does the workflow impose?

The replay is a functional and performance observation, not a measured study of
human delight, independent semantic correctness or production capacity.

## Method

Run the full combined acceptance replay without a case filter: all sixteen
Workshop tools and three supporting tools, 100,000/1,000,000-row workflows,
100,000-row grouped partition verification, and the expanded twenty-one-case PDF
matrix (twenty cases in the baseline). Preserve actual failures; do not replace a full-run result with a
collection of reruns without saying so.

The replay now records each MCP HTTP round trip, response bytes and per-tool
nearest-rank latency percentiles, alongside logical stage timing for the large
workflow. Expected contract/permission rejections are included in timing samples;
they are not operational errors. SDK upload/export requests, REST fallback reads,
client parsing and async worker time are outside the MCP round-trip metric.
Scenario wall time includes those operations and polling delays. Stage totals
exclude some intervening assertions and retry probes.

CPU/memory samples cover the existing API and batch-worker containers. Docker has
10 virtual CPUs and 8,217,317,376 bytes of memory (about 7.65 GiB). The replay itself
runs inside the API container, so its resource sample includes test-client work;
neither container measurement is a per-request allocation or a dedicated-host
capacity benchmark. Other desktop/background services remain running.

During the final live replay, a host snapshot reported 24 GiB physical memory,
6,214.62 MiB of swap in use, about 9.48 GiB occupied by compressed pages and
roughly 65 MiB free pages. Disk had about 290 GiB available. These are point-in-time
observations, not proof of the bottleneck; low free memory alone is not a memory
pressure diagnosis. Thermal status was unavailable. No unrelated applications
were closed and no host/VM resource settings were changed.

## Broad automated checks

| Check                             | Observed result                                                                                                                                                                |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Backend                           | 4,044 passed; 15 optional-environment skips; 188.85 seconds.                                                                                                                   |
| SDK configured suite              | 595 passed; 71.44 seconds. The Makefile's `test_spans.py` exclusion was retained.                                                                                              |
| Frontend full suite               | 839 passed, 15 failed across eight files; 142.91 seconds. Every failure was the unchanged 5-second test timeout.                                                               |
| Frontend timeout diagnostic       | The same eight files passed all 56 tests with `--maxWorkers=2` after broad checks completed; 3.41 seconds. No test assertions, application code or timeout thresholds changed. |
| Frontend static checks            | Typecheck, lint, design, contrast and controls passed.                                                                                                                         |
| Fresh official MCP SDK connection | Contract 3.0.0; 61 total tools; five new Workshop tools present, three retired tools absent; authenticated call and resource read passed.                                      |

Broad backend/SDK/frontend checks were initially concurrent. The frontend timeout
pattern and scoped bounded-worker success support a contention-sensitive test
run, not a reproduced UI assertion defect. This is an inference, not proof of a
single root cause. The original full frontend failure remains recorded. The live baseline started
only after those checks and the scoped diagnostic completed.

Backend skips include optional GPU/provider packages, unconfigured Modal PII
endpoints and the unit-test environment's unreachable Redis broker. The live
replay uses the actual running Redis/Celery path. Warnings include the short local
test JWT key; no credentials are stored in the evidence.

## Fixes and red/green evidence

The complete baseline passed 48/48 technical scenarios, while exposing factual
contract defects. New integration tests failed on missing source paging, landing
facts and pipeline stages. Four targeted live cases also failed: inaccessible
source inventory, missing OCR facts, generic page-limit errors and an incorrect
document upload reservation limit.

Implemented corrections:

- `inspect_dataset` pages source metadata independently, with exact extraction
  metadata and checksums. Response budgets shorten pages rather than destroying
  OCR facts. A dense 25-source/2,000-OCR-pages-per-source regression reconstructs
  every source, including beyond the last page.
- Single and batch uploads persist file/stage facts. PDF landing reports total
  pages; OCR reports completed/total recognition pages. Job output labels the
  old selected row count as `active_version`, not upload progress.
- Pipeline receipts record verification, writing, lineage, impact and publication
  stages, measured source/output counts and completed-stage durations. Cancelled
  runs stop at the next stage boundary without overwriting terminal receipts.
- Documents advertise and enforce 104,857,600-byte reservations. The SDK can reject
  an oversized file before transferring its body. PDF page counting rejects
  2,001 pages explicitly against the 2,000-page limit before extraction.
- New attachments clear old attempt progress. This extra failure was reproduced
  after the initial 85-test focused pass; its correction passed all three fact
  contract tests again.
- Removed inaccurate runtime guidance that claimed derivation publishes samples.
  Derivation copies the complete source; sample selection remains agent-authored.
- Bounded frontend test workers at two. The fresh full invocation passed all
  854 tests in 113 files in 67.97 seconds; assertions and 5-second timeouts were
  unchanged. Post-generation typecheck and lint also passed.

The PDF matrix adds a 103,683,891-byte, eight-page scanned PDF just below the
100-MiB cap. Its final page was rendered and visually checked. The oversized
fixture remains 116,644,339 bytes. These are uncompressed raster fixtures, not
representative dense or multilingual reports.

The first post-fix live retest exposed an incorrect test assumption: chunk errors
use the existing HTTP 409 response, not 400. The server returned the correct byte
limit and rejected the write. The assertion was corrected; the failed invocation
remains in the evidence. It also experienced a PostgreSQL connection timeout
while the broad backend suite ran. Service inspection found API and PostgreSQL
healthy without a restart, and a later snapshot showed nine database connections
against a maximum of 100. The cause is not established. Do not treat this combined
run as a clean latency comparison or discard its failure.

The post-fix backend invocation passed 4,045 tests with 15 optional-environment
skips and two setup errors: the sandbox denied localhost socket binding for the
analytics receiver. Both analytics tests then passed with the necessary socket
permission (12.76 s). This is environment correction, not a relaxed assertion.
The fresh SDK suite passed 595 tests in 16.35 s, retaining its configured spans
test exclusion.

A full four-worker rerun with socket permission passed 4,046 tests but failed the
existing 20,000-transcript timing gate: import took 43.10 s against a 30 s budget.
The identical test passed alone with every original budget retained; its complete
landing/import/paging/diff body took 23.24 s. This supports contention sensitivity,
not a claim that the concurrent failure did not happen. The two-worker full
invocation also passed 4,046 tests and failed the same import gate, this time at
66.80 s (508.95 s for the suite). Reducing worker count did not resolve this
failure; the full backend suite is not green. A cProfile diagnostic then exceeded
the landing gate before import (36.92 s against 20 s). Instrumentation materially
affects these timings, so its result is retained as a diagnostic, not a comparable
normal-speed measurement.

A lighter diagnostic wrapped existing stage calls with elapsed-time recording and
kept every test budget unchanged. It also failed the import gate (39.63 s).
Artifact landing took 12.45 s and the workbench execution took 27.11 s. Within
execution, lineage preservation took 8.15 s, impact measurement 5.49 s and final
cell measurement 8.41 s; these nested durations must not be added to the parent
execution total. `null_rates` took only 0.05–0.09 s here, so the much larger
cProfile attribution is not sufficient evidence to target that function.
Repeated full-row serialization, verification, indexing and measurement are real
costs, but a production optimization still needs a controlled comparison that
preserves the same lineage and contract checks.

API-client generation completed. Its schema step reported 27 serializer-inference
errors across five non-Workshop views (completions/models, OTLP and health), plus
warnings. Those adjacent schema issues were not changed. No migration was needed
for the Workshop fixes: progress is stored in existing durable JSON receipts.

## Baseline performance

| Workflow                         | Observed end-to-end time | Verified output                                               |
| -------------------------------- | -----------------------: | ------------------------------------------------------------- |
| 100,000-row workflow             |                 30.025 s | 50,000 transformed and imported rows                          |
| 1,000,000-row workflow           |                719.765 s | 500,000 transformed and imported rows                         |
| Cancel and recover at scale      |                167.757 s | Cancelled run unpublished; replacement retains 500,000 rows   |
| 100,000-row grouped partition    |                 30.344 s | All observations and 20,000 groups verified across four roles |
| 2,000-page native PDF            |         2.783 s to ready | 4,000 evidence rows; all pages and original bytes verified    |
| 25-page scanned PDF              |        13.234 s to ready | 100 OCR rows with page evidence                               |
| 100-file PDF batch               |          1.591 s landing | 200 rows; 100 original downloads verified                     |
| Mixed 10-file / 2,671-page batch |         25.697 s landing | 5,460 rows; source/page identity preserved                    |

The million-row workflow spent 104.061 s creating/uploading/landing the source,
193.553 s executing the deterministic pipeline, 9.948 s exporting, 9.822 s deriving
the local artifact, 127.761 s uploading/landing that artifact, and 271.766 s on
lineage-bound import. This run scales worse than linearly relative to 100,000 rows;
it does not isolate an algorithmic cause. Full verification, repeated frame
passes, serialization and local resource contention are candidates to profile,
not proven individual explanations.

Sampled peaks were 1,251 MiB / 213% CPU for the API container and 2,102 MiB / 222%
CPU for the batch worker. CPU uses Docker's per-core percentage convention.
The API sample includes the replay client. The worker's cumulative block-write
counter rose by approximately 11 GB during the full suite; this includes temporary
frames, fixtures and all scenarios, not just one transformation. These are
observations on a shared local deployment, not a capacity or production SLO.

The harness made 2,924 successful job reads; 2,419 repeated the same job facts.
It deliberately polls at 250 ms (500 ms for PDFs), so this is a test-client
efficiency issue, not measured native-agent behaviour. Job-read p95 was 65.5 ms;
inspection p95 was 44.1 ms and query p95 was 53.1 ms. Expected validation and
permission rejections are included in those distributions.

The largest response was a 223,713-byte query result. Bulk PDF verification moved
65.3 MB through query responses across the suite; it deliberately reads every row
and includes duplicated text/structured MCP content. Real agent workflows should
use aggregate/projection queries and CLI exports for bulk processing. Row-count
limits alone do not provide a query byte budget or execution deadline.

## Critical product assessment

The core strength is inspectable preservation: source bytes, exact cells,
fingerprints, parent attribution, groups, retry identities and failed-publication
boundaries were exercised through real HTTP, PostgreSQL and workers. Technical
completion is still explicitly separate from semantic correctness.

The changes improve factual usefulness, not platform intelligence. They let the
native agent explain a file, stage, page count, limitation or recovery action from
recorded evidence. They do not add a product-side narrator or claim an ETA.

Remaining capability gaps are not passing implementations:

- External imports still lack a structured code/environment/seed execution
  manifest and a cell-bound write/read contract for attributable semantic checks.
- Parent binding still selects one source cell; joining independently versioned
  inputs needs an explicit multi-source lineage contract.
- Exploration measures sampling allocations, but the agent must select and import
  the sample. Derivation is not a sampling operation.
- Query byte/deadline controls and cancellation for exploration/partition work
  remain separate gaps. Stage-boundary cancellation does not terminate arbitrary
  in-flight computation or external provider work.
- Existing request analytics do not measure user-confirmed task completion,
  clarity, confidence, effort or delight. Regression timing and passing calls
  must not be labelled as those experience outcomes.

Coverage does not establish multilingual OCR, handwriting, complex reading order,
table reconstruction, every PDF encoding, long scanned books, many concurrent
heavy batches or production capacity. The page-limit fixture has two simple
native lines per page. The near-byte-limit fixture is below the exact cap, not an
exactly-at-cap valid PDF. Original user datasets remained outside fixture scope.
Worker interruption/cancellation during OCR and sustained concurrent heavy
document ingestion were not established by this matrix.

Recommended order of further work:

1. Resolve the reproducible import latency-budget failure on a controlled host,
   then test concurrent heavy uploads. Do not ship a latency claim from the one
   passing isolated run or replace integrity checks with sampling.
1. Add structured execution and agent-authored assessment receipts tied to exact
   cells. An external transformation's prose is not a reproducible execution
   record, and successful storage is not semantic validation.
1. Bound query bytes/time and provide explicit cancellation for the remaining
   background work. A row limit is not a resource limit.
1. Measure workflow outcomes and explicit user feedback separately from request
   analytics. Time-to-usable-result, recovery cost and source coverage are useful
   objective signals; delight still needs evidence from users.

## Post-fix live performance

The 100,000-row full workflow passed in 206.537 s, whereas the million-row workflow
passed in 298.395 s. The baseline was 30.025 s and 719.765 s respectively. The
opposing changes and variation within a single run mean these are not controlled
before/after speedup measurements. They establish completed work and observed
latency on this host, not a stable scaling curve or a causal performance benefit
from the factual-contract changes.

| Stage                               | 100,000 rows | 1,000,000 rows |
| ----------------------------------- | -----------: | -------------: |
| Source creation, upload and landing |    123.653 s |       63.417 s |
| Deterministic pipeline              |     67.776 s |       93.124 s |
| Export                              |      0.499 s |        3.650 s |
| Local derivation                    |      0.422 s |        3.700 s |
| Artifact upload and landing         |      5.539 s |       52.598 s |
| Lineage-bound import                |      8.498 s |       81.754 s |

The million-row pipeline receipt measures 30.391 s preserving lineage, 17.048 s
measuring impact and 33.932 s publishing. The subsequent import measures 29.055 s,
10.033 s and 35.382 s for those stages. Source/artifact hash verification is under
0.05 s per recorded stage. Publication includes writing and measuring the output
inside the dataset-lock transaction; it is not merely the final database update.
That boundary deserves concurrency/lock-wait measurement before any claim about
interactive responsiveness at scale. Integrity and atomic publication must be
retained in any optimization.

| PDF workload                  |                   Final observed latency | Result                                                    |
| ----------------------------- | ---------------------------------------: | --------------------------------------------------------- |
| Native, 2,000 pages           |                  1.635 s source-to-ready | All 4,000 evidence rows verified                          |
| Scanned, 25 pages             |                  6.764 s source-to-ready | Advancing OCR counters and 100 rows verified              |
| Near limit, 103,683,891 bytes |                  2.911 s source-to-ready | Eight OCR pages, exact original bytes retained            |
| 100 files                     |   1.065 s landing; 3.351 s complete case | Complete source inventory and original downloads verified |
| Mixed ten files / 2,671 pages | 14.437 s landing; 20.055 s complete case | All 5,460 rows and source/page identities verified        |

The 2,001-page fixture returns both the observed and supported counts. The
116,644,339-byte fixture is rejected by the real SDK after reservation without a
body-transfer request; the server also rejects a chunk beyond its advertised
limit. Encrypted, blank and corrupt PDFs reject safely and recover with a valid
attachment. The 101-file batch rejects before dataset creation. The earlier
100-file PostgreSQL connection timeout did not recur; its cause remains unknown.

All nineteen required tools had successful calls in the final replay (2,833
successful calls in total). Of 1,757 successful job reads, 1,179 repeated the same
facts (67.1%). The added stages make progress inspectable, but they do not make
aggressive polling useful. Job-read p95 was 109.9 ms, inspection p95 122.2 ms and
query p95 31.4 ms. The maximum job read took 2.096 s. Total measured MCP response
traffic was 82.0 MB, including 65.5 MB of bulk query verification. The largest
response remained 223,713 bytes. The 32-KB dataset-inspection budget applies to
structured content; duplicated MCP text/structured output can make the HTTP
response approximately twice that size. It is not a global transport-byte cap.

Final sampled peaks were 1,141 MiB / 360% CPU for the API and 1,806 MiB / 203% CPU
for the batch worker. Across the complete replay, the worker's cumulative
block-write counter increased from 54.7 GB to 63.3 GB. These samples include
temporary storage and client/diagnostic work, not attributable per-job cost.
The scenario durations sum to 785.924 s; that sum excludes harness setup and
cleanup and is not the entire shell invocation's elapsed time.

The Datasets guidance shaped the immutable-source, version and lineage checks;
the PDF guidance shaped the varied fixtures and visual verification. Neither
guidance nor passing technical tests establishes semantic correctness or human
delight.

## Catalogue coverage

| Workflow area                  | Workshop tools exercised                                                                                      |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| Discovery and inspection       | `list_datasets`, `inspect_dataset`, `query_dataset`, `inspect_dataset_workbench`                              |
| Sources                        | `start_dataset`, `create_dataset_from_traces`, `create_dataset_from_llm_calls`                                |
| Version creation and lifecycle | `save_dataset_pipeline`, `run_dataset_pipeline`, `import_dataset_version`, `update_dataset`, `cancel_dataset` |
| Exploration and partitions     | `explore_dataset`, `derive_dataset`, `create_data_partition`, `retry_data_partition`                          |

Supporting calls are `get_job`, `list_model_workflows` and `list_projects`.
Catalogue coverage does not mean every possible input combination is covered.
The replay also checks account/project/read-only authorization, cross-project
isolation, structured schemas, resources, prompts, retry keys, simultaneous
submissions, immutable source versions and cancellation/publication boundaries.

## Reproduction

Use the running local Compose deployment with migrations applied, current source
mounted into API/workers, PostgreSQL and Redis available, and English Tesseract
OCR installed in the batch worker. Do not start another application server or
edit backend files during the replay: the development worker hot-reloader can
interrupt in-flight work. No model-provider credentials or paid jobs are needed.

The API image can contain an older installed SDK, so copy this checkout's SDK to
an isolated temporary directory. Keep host regression suites separate from the
live replay when comparing performance. The synthetic fixtures occupy about
263 MB before uploaded/extracted artifacts; the million-row workflows use
additional temporary disk space.

```sh
review_dir=$(mktemp -d /private/tmp/workshop-review.XXXXXX)
/Users/tyleredwards/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  tests/evidence/workshop_pdf_fixtures.py "$review_dir/pdfs"
replay_dir=$(docker compose exec -T api mktemp -d /tmp/workshop-review.XXXXXX)
docker compose cp overmind/overmind "api:$replay_dir/overmind"
docker compose cp tests/fixtures/documents "api:$replay_dir/documents"
docker compose cp "$review_dir/pdfs" "api:$replay_dir/pdfs"
set -o pipefail
docker compose exec -T \
  -e PYTHONPATH="$replay_dir" -e DO_NOT_TRACK=1 \
  -e WORKSHOP_ACCEPTANCE_DOCUMENTS="$replay_dir/documents" \
  -e WORKSHOP_ACCEPTANCE_PDFS="$replay_dir/pdfs" \
  -e WORKSHOP_ACCEPTANCE_CASES= \
  -e WORKSHOP_ACCEPTANCE_ROWS=100000,1000000 \
  api python manage.py shell < tests/evidence/workshop_mcp_acceptance.py \
  2>&1 | tee "$review_dir/live.log"
```

The fixture generator requires ReportLab, pypdf and Pillow; the command above uses
the available bundled runtime. The replay creates a unique test user and two
projects and removes only those fixtures when work is terminal. An interrupted
replay retains active fixtures and prints their project IDs; inspect them before
cleanup. Do not remove unrelated projects or datasets.

Broad checks used `.venv/bin/pytest tests/ -n4 --dist worksteal -q -ra`,
`make -C overmind test`, and, from `frontend/`, `bun run test`,
`bun run typecheck`, `bun run lint` and `bun run check:all`. Backend analytics
tests require permission to bind a local receiver socket. The two-worker
diagnostic changes only `-n4` to `-n2`; the isolated performance diagnostic is
`.venv/bin/pytest tests/test_dataset_scale.py -n0 -q -ra --durations=5`.
Each invocation uses `pipefail` and preserves its own log.

Final scoped pre-commit checks passed: whitespace, JSON, private-key detection,
Markdown formatting, Ruff and Biome. The first attempt could not access the
existing dependency cache; the permitted rerun passed. Both repositories' diff
whitespace checks passed. The generated frontend client was not hand-edited.

Raw logs are retained under `/private/tmp/workshop-full-review.mIGyGI/`; their
verification hashes are recorded in the final evidence JSON. Generated host PDF
inputs and the isolated container SDK/fixture directory were removed after the
replay completed. Regenerate them with the commands above; the fixture manifest
and checksums remain in the saved evidence. No original datasets were removed.
