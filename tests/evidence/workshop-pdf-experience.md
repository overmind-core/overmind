# Workshop PDF and experience verification

For subsequent fixes and a combined replay, see
[the full performance review](workshop-full-performance-review.md). This document
preserves the earlier audit and its then-unresolved findings.

## Scope and failure modes

This is a status audit and expanded local regression, not an implementation of
the proposed experience instrumentation. The acceptance standard is usefulness
toward the user's stated aim, not successful tool invocation alone. The platform
owns factual state and evidence; the native coding agent owns explanation.

Failure modes to exercise before making product changes:

- Long native PDFs lose pages, mix page identities, or silently truncate text.
- Scanned/mixed PDFs miss OCR evidence or confuse native extraction with semantic
  verification. Larger byte size and larger page count stress different limits.
- Multi-file batches lose sources, merge duplicate documents, or publish partial
  results when one file fails. A rejected attachment must preserve the prior cell.
- Password-protected, blank, corrupt, over-page-limit and over-byte-limit PDFs
  fail without a readable reason or prevent a later valid attachment.
- More than ten sources cannot be fully discovered through MCP inspection.
- Durable progress exists internally but the native client cannot see it;
  stalled-looking extraction encourages unnecessary retries.
- Successful extraction is mistaken for completion of a requested transformation
  or for verified semantic quality.

Fixtures are synthetic. Tests use the already-running local API, PostgreSQL,
Redis and batch worker, with no paid provider work. Public file transfer and MCP
calls exercise workflows; database access is restricted to disposable fixture
setup/cleanup and independent preservation checks.

## Implementation status

| Requirement                                                 | Status observed on this branch                                                                                                                                                                                                  |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Native agent owns interpretation, authoring and explanation | Implemented architectural boundary. Server initialization and the dataset skill describe it; no platform Workshop agent is restored.                                                                                            |
| Serve the user's stated aim, not merely finish a tool call  | Recorded assessment requirement. The dataset skill already instructs the native agent to complete requested examples rather than stop at cleaned passages. There is no measured general task-success or delight implementation. |
| Factual, useful progress available through MCP              | Partial. Job identity, state, final counts and next actions exist. File-level landing progress is stored but omitted from `get_job`; no page-level extraction progress exists.                                                  |
| Useful narration at meaningful milestones                   | Proposed, not consistently encoded in the current initialization/prompt/skill guidance and not verified in a human-facing native-client transcript.                                                                             |
| Transformation receipts and honest quality limits           | Implemented. Live runs preserved source cells/evidence and reported semantic quality as unmeasured.                                                                                                                             |
| Utility and delight analytics                               | Assessment definitions only. Existing request analytics and its local integration tests do not establish workflow outcomes, user-visible narration or satisfaction. No live analytics/dashboard changes were made.              |

## Observed results

Twenty distinct functional scenarios passed across the initial run and focused
reruns. This is not one 20/20 invocation or a claim that the experience gaps below
passed. The initial run recorded 10/19; five failures came from reading the wrong
test field (`error` instead of `job_error`), and four exposed genuinely missing
MCP extraction metadata. The latter cases now verify full metadata via REST and
explicitly record the MCP gap. All nine affected cases passed that functional
rerun. The additional combined batch passed separately. Both original failures
and final observations are preserved in `workshop-pdf-results.json`.

| Workload                                                                | Verified result                                                                                                                                                                                                         |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Native PDFs: 1, 10, 100, 500 and 2,000 pages                            | Every page's expected text and evidence identity survived extraction, projection and rereading the original cell. The 2,000-page fixture produced 4,000 rows.                                                           |
| Scanned PDFs: 1 and 25 pages                                            | Expected sentence and page evidence present on every page; OCR provenance and limitations retained in complete metadata.                                                                                                |
| Mixed native/scanned PDF: 30 pages                                      | All pages represented, with OCR content on the expected 20 pages; 120 rows.                                                                                                                                             |
| Large scanned PDF: 38,881,650 bytes, 3 pages                            | Chunked upload, OCR, byte-identical original download, transformation and original-cell preservation passed.                                                                                                            |
| Batches: 10, 25 and 100 files                                           | Exact repeated-content counts and original downloads verified. The 100-file fixture deliberately contains 25 unique documents repeated four times; observations were not deduplicated.                                  |
| Combined batch: 10 files, 2,671 total pages, 41,584,591 bytes           | Native and scanned documents of different sizes landed together; all document/page identities and transformed evidence preserved across 5,460 rows. Landing took 13.877 seconds; the complete case took 19.282 seconds. |
| Invalid PDFs: 2,001 pages; 116,644,339 bytes; encrypted; blank; corrupt | Rejected without publishing a readable cell, with an exposed error. Each dataset subsequently recovered through a valid source attachment.                                                                              |
| Corrupt file in the middle of an attachment batch                       | No partial cell publication; prior version remained readable. A later valid two-file attachment succeeded.                                                                                                              |
| 101-file batch                                                          | Rejected before dataset creation with the 100-file bound.                                                                                                                                                               |

Nine successful single-document workflows verified exact original download
checksums, transformed text/evidence and prior-cell preservation. The separate
batch cases verified another 135 source downloads. Every replay removed only its
own disposable projects/user; all 126 pre-existing datasets remained. No billable
usage records were created.

The focused document, MCP prompt, analytics-delivery and Workshop acceptance suite
passed: **61 tests in 7.30 seconds**. Its 64 warnings report the short local test
JWT signing key, not failed assertions. No UI, production service, schema or
generated client was changed in this audit; the full backend suite was not rerun.

## Open experience and catalogue findings

1. **Useful progress is lost at the MCP boundary.** During the combined batch,
   stored progress advanced from native extraction through scanned extraction to
   merging, with filenames and completed counts. All 27 nonterminal polls still
   returned only `cells={}` and `rows=0`; the next poll jumped to the final 5,460
   rows. Expose bounded typed file/stage progress through both job reads, then add
   page counts where actually measured. Do not invent a percentage or ETA.
1. **Metadata truncation removes the facts needed for an honest explanation.**
   Each scanned/mixed PDF's MCP `extraction` became
   `{"truncated": true, "ocr": {"truncated": true}}`, even the one-page fixture.
   Page count, method and OCR limitations remained available through REST, not
   this MCP summary. Preserve those scalar facts/warnings before bounding long
   region/page arrays, with a complete inspectable detail path.
1. **Source inventory remains incomplete above ten files.** The 25- and 100-file
   cases exposed ten sources with accurate totals but no cursor. Full inventory
   and downloads were verified through REST; this is not full MCP discovery.
1. **Page-limit errors are not actionable enough.** A readable 2,001-page PDF
   received the same generic extraction/password message as corrupt/encrypted
   files. Expose the 2,000-page limit and a split-file recovery action. The upload
   reservation also advertises a general 2 GiB bound, while documents are limited
   to 100 MiB and reject only after transfer; make document limits discoverable
   before uploading large files.
1. **Helpfulness and delight are still unverified experience outcomes.** Add
   native-agent guidance grounded in the user's task and current receipts, then
   assess real visible sessions. Keep direct feedback separate from speed,
   repeat use and successful-call proxies. None of these gaps requires restoring
   platform intelligence or redesigning the cell UI.

These are findings, not product fixes applied by this audit. The PDF skill led to
visual checks of native, scanned and large uncompressed fixtures before replay;
the Datasets skill supplied the source/cell/lineage and unmeasured-quality checks.

## Reproduction

Run from the original checkout with the existing Compose deployment and current
SDK. Fixture generation needs Python with `reportlab`, `pypdf` and Pillow. The
local bundled Python was used; the API image does not need those extra packages.
The worker already has Docling/PDFium and Tesseract English OCR.

```sh
pdf_fixture_dir=$(mktemp -d /private/tmp/workshop-pdf-fixtures.XXXXXX)
/Users/tyleredwards/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/evidence/workshop_pdf_fixtures.py "$pdf_fixture_dir"
pdf_cases='pdf_native-1,pdf_native-10,pdf_native-100,pdf_native-500,pdf_native-2000,pdf_native-2001,pdf_scanned-1,pdf_scanned-25,pdf_mixed-30,pdf_large-scanned,pdf_over-byte-limit,pdf_encrypted,pdf_blank,pdf_corrupt,pdf_batch_10,pdf_batch_25,pdf_batch_100,pdf_mixed_batch,pdf_atomic_batch_recovery,pdf_batch_limit_101'
pdf_replay_dir=$(docker compose exec -T api mktemp -d /tmp/workshop-pdf-replay.XXXXXX)
docker compose cp overmind/overmind "api:$pdf_replay_dir/overmind"
docker compose cp "$pdf_fixture_dir" "api:$pdf_replay_dir/pdfs"
set -o pipefail
docker compose exec -T -e PYTHONPATH="$pdf_replay_dir" -e DO_NOT_TRACK=1 \
  -e WORKSHOP_ACCEPTANCE_PDFS="$pdf_replay_dir/pdfs" \
  -e WORKSHOP_ACCEPTANCE_CASES="$pdf_cases" \
  api python manage.py shell < tests/evidence/workshop_mcp_acceptance.py \
  2>&1 | tee /private/tmp/workshop-pdf-replay.log
```

Use only the nine affected case names for the corrected rerun, and
`pdf_mixed_batch` for the combined-batch run. The current script supports all
twenty cases together; the observations here preserve the actual run sequence.
The earlier full replay did not use `pipefail`; its explicit summary and traceback
record failure. Subsequent runs used `pipefail` and exited successfully.

```sh
set -o pipefail
.venv/bin/pytest tests/test_workshop_documents.py tests/test_mcp_analytics.py \
  tests/test_mcp_prompts.py tests/test_workshop_mcp_acceptance.py \
  -n 4 --dist worksteal -q -ra \
  2>&1 | tee /private/tmp/workshop-pdf-focused-tests.log
```

Local logs: `/private/tmp/workshop-pdf-replay.log`,
`/private/tmp/workshop-pdf-targeted.log`,
`/private/tmp/workshop-pdf-mixed-batch.log` and
`/private/tmp/workshop-pdf-focused-tests.log`. The machine-readable evidence saves
their observed summaries, initial failures, progress samples, fixture dimensions
and checksums. Synthetic fixtures and isolated SDK copies are disposable and can
be regenerated; cleanup must target only their exact temporary directories.

## Limits of this proof

Native fixtures have two simple text lines per page; the large byte-size fixture
uses uncompressed scanned images. They are not representative benchmarks for
dense reports, complex tables, handwriting, multilingual OCR or visual reasoning.
Exact 100-MiB byte-boundary behaviour, very long scanned books, many concurrent
heavy batches, worker restarts and cancellation during PDF OCR remain untested by
this extension. Existing image orientation tests were rerun; PDF rotation and
layout reconstruction were not added here. Capacity and delight claims require
broader representative documents and human-facing workflow review.
