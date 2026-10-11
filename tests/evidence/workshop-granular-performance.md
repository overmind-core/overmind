# Workshop granular platform verification

The platform must preserve uploaded evidence, execute declared work, expose facts
and fail safely. Agent-authored transformation meaning is not repaired by this
exercise. All live state changes use the local MCP catalogue and installed transfer
CLI. No model training, evaluation, inference, browser fallback or original user
dataset mutation is part of the matrix.

Failure modes defined before implementation:

- JSON booleans, integers, strings and nulls silently coerce in mixed columns.
- Large integers overflow storage or lose precision when sharing a float column.
- Nested/Unicode values differ across original bytes, export and SQL inspection.
- Quoted CSV/TSV, compressed JSONL or Parquet lands differently from its contract.
- PDFs lose pages, OCR evidence, source identities or extraction limitations.
- Invalid/over-limit inputs publish partial cells or prevent valid recovery.
- Wide values flood MCP responses despite the advertised row limit.
- More than 200 result columns silently lose column metadata while retaining values.
- Multi-file guidance cannot be completed through the installed transfer surface.
- Large uploads or extraction monopolize ordinary inspection and progress reads.
- Reading preserved scalars through the shared pandas adapter reintroduces float
  coercion, including nullable integer columns, before downstream consumers see them.

The baseline command is `workshop_granular_probe.py`; its report records every case,
exact dataset/transfer/cell identities, source hashes, changing progress observations,
call latency and structured/transport response sizes. Expectations compare complete
values through exported frames and independently specified fixture content, not
only platform success flags. Failures are retained and classified before fixes.

PDF fixtures use `workshop_pdf_fixtures.py`: 40 files spanning native, scanned and
mixed content; 1–2,001 pages; up to 116,644,339 bytes; invalid files and quantity
fixtures. Native and scanned pages were rendered and inspected before replay.
Fixture rendering required the bundled Poppler; local font-cache warnings were
test-environment noise, not Overmind output.

## Results

The initial live matrix passed 31/35 cases. The four failures were mixed-scalar
coercion, integer overflow, mixed numeric precision loss and an oversized query
response. Their receipts remain in `workshop-granular-baseline.json`.

After repairs, the first repeat passed 35/35. The final expanded matrix passed
37/37; a subsequently added wide-schema case passed separately, making 38 distinct
ingestion/query cases. Invalid inputs count as passing only when rejected; those
intentional error datasets are not failed regressions.

| Verification                 | Observed outcome                                                                                                                                                                               | Evidence                             |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| Structured files and images  | JSON scalars/nesting/Unicode, late type changes, nullable and large integers, quoted CSV/TSV, Parquet, gzip JSONL, text/Markdown, PNG/JPEG/WebP passed                                         | `workshop-granular-final.json`       |
| PDF extraction and limits    | Native 1/10/100/500/2,000 pages; scanned 1/25; mixed 30; 38.9 MB and 103.7 MB scanned files passed. 2,001 pages, 116.6 MB, encrypted, blank and corrupt documents rejected                     | `workshop-granular-final.json`       |
| Wide schema                  | 201 columns retained in export; full SQL projection refused explicitly, selected first/last columns returned correctly                                                                         | `workshop-wide-schema-final.json`    |
| Recovery and source quantity | Failed attachment preserved the original cell; valid recovery and identical-command retry worked. 27 sources/cells retained 72 distinct row identities and all source hashes across pagination | `workshop-recovery-load-final.json`  |
| Concurrent ingestion         | 28 PDFs, concurrency four, including scanned, mixed and near-limit files, completed; ordinary queries remained responsive                                                                      | `workshop-recovery-load-final.json`  |
| Existing real data           | Nine runs: preview and two publications each for document, tabular and conversation recipes. Complete output-value comparisons matched; original datasets, source hashes and recipes unchanged | `workshop-granular-real-replay.json` |
| Receipt drill-down           | All nine compact receipts preserved measured counts, checks, fingerprints and timings; explicit resource reads retained their evidence                                                         | `workshop-receipt-sizes.json`        |

The real document recipe used the existing 320 extracted evidence rows and
produced seven page rows. Tabular and conversation recipes processed 891 and
1,000 rows respectively. This verifies execution fidelity, not the correctness
of the coding agent's chosen transformation meaning. The known agent-authored
blank-string review predicate was deliberately not changed.

## Repairs

1. Mixed scalar columns use JSON storage instead of string coercion. Integers
   outside signed 64-bit range and precision-sensitive integer/float mixtures
   also use JSON. Normal homogeneous integer/numeric columns remain typed.
1. Shared pandas readers retain those values and nullable integers without
   reintroducing float inference. Regression expectations compare exact JSON
   types through landing, published transformations, SQL and both frame readers.
1. MCP queries reject results exceeding 32 KiB of JSON or 200 columns with
   `query_result_too_large`, projection/export guidance and no silent clipping.
1. Over-limit document reservation exposes `file_too_large` and a safe CLI
   `split_or_reduce_file` action. No transfer exists at this rejection point.
1. The upload resource no longer mixes managed transfers with incompatible
   staging instructions. It explicitly describes serial cumulative attachments
   and the missing atomic multi-file CLI handoff.
1. MCP contract 5.6 separates routine Workshop measurements from bulky examples.
   `get_job`, repeated run receipts and workbench history expose
   `evidence_resource`; the full retained receipt is unchanged. Regression tests
   caught and corrected a missing typed output field during this change.

Classification: MCP-ready queries/receipts and CLI-guided local transfer. No new
tools, hidden agent, semantic normalization or compatibility execution path was
introduced. API client generation completed; backend/SDK skills and the sibling
documentation were updated. Existing immutable cells were not rewritten.

## Performance and product assessment

These are local development observations, not production SLOs. The final sequential
matrix recorded p95 job reads of 32 ms, inspection of 37 ms and queries of 47 ms.
During the concurrent 28-PDF load plus real-data replay, query p95 was 69 ms and
the slowest sampled query was 306 ms. The 28-file load took 19.9 seconds, including
CLI preflight, transfer, polling and full export verification.

| Case                         | Command start to observed landing | Complete verification |
| ---------------------------- | --------------------------------: | --------------------: |
| 100,000 structured rows      |                            5.92 s |                8.27 s |
| 2,000-page simple native PDF |                            1.84 s |                3.75 s |
| 25-page clean scanned PDF    |                            6.91 s |                8.48 s |
| 30-page mixed PDF            |                            5.97 s |                7.72 s |
| 103,683,891-byte scanned PDF |                            4.59 s |                6.32 s |
| One row with 201 columns     |                            5.27 s |                6.95 s |

The complete verification column includes an idempotent upload retry, inspection,
full export, content comparison and SQL checks; it is not worker execution time.
Polling adds measurement granularity. Simple native pages are deliberately sparse,
so their speed does not predict dense financial reports or OCR-heavy books.

The 600 KB wide-row query previously produced roughly 2.6 MB of duplicated MCP
transport content; it now returns a 650-byte actionable error and full export
remains exact. The nine compact pipeline responses measured 31–36 KB on the wire;
their separately requested evidence resources ranged from 32–169 KB.

Real four-step scripts still take roughly 10.7–12.0 seconds server-side, with
2.5–2.8 seconds recorded per execution step. Client replay timings of 15–23 seconds
also include polling and verification. Tiny previews were not materially faster.
The measured stages point to execution overhead, but do not independently isolate
container startup from script execution; optimizing either requires that split.

## Remaining gaps and confidence limits

- Atomic, resumable multi-file CLI/MCP uploads remain missing. Serial attachment
  works but creates one import version per file and can require many round trips.
- Structured landing has incomplete phase telemetry. The 201-column case spent
  about five seconds landing while `progress.landing` was null. Storage,
  profiling and publication need distinct measured stages; the cause of the
  latency has not yet been isolated.
- SQL output bounds are not query deadlines. DuckDB has a 512 MB configured
  memory limit and two threads, but the query adapter materializes results before
  the response-size check and has no explicit execution deadline.
- Status still duplicates compact progress in `progress` and `details.result`.
  Deep evidence resources are explicit but not sample-paginated. Finer per-step
  evidence selection would improve large-run drill-down further.
- Landing jobs expose `idle/error`, unlike pipeline `completed/failed`, and do
  not provide a terminal timestamp. The first harness assumed the wrong terminal
  names; this was corrected in the harness, not misclassified as a server stall.
- This pass does not certify arbitrary PDF reading order/table reconstruction,
  multilingual/handwritten or poor-quality OCR, DOCX extraction, 2 GiB structured
  limits, cross-project concurrent load, provider outages or long production soak.
  Native markers and required OCR text were checked on every tested page, not
  character-level OCR accuracy across arbitrary documents.
- No training-quality claims, provider calls, browser checks, original-byte
  re-download verification or universal “zero friction” claim are made.

The evidence supports confidence in the tested local fidelity, recovery,
execution and inspection paths. The remaining product gaps above are real
follow-up work, not agent-authored transformation errors.

## Reproduce

Run from the repository with its existing Compose API, extraction workers, local
Tesseract and approved Workshop runtime already available. Use a saved localhost
account connection, installed editable CLI and authorized project
`e18b29b5-915d-45a7-80cd-77ffe6559205`. The scripts reject non-loopback endpoints.
Scoped host permission may be required for local connections; never inject or
print credentials. Use a fresh `--run` value to test changed server behaviour;
an old stable key intentionally reuses its prior publication.

```bash
/Users/tyleredwards/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/evidence/workshop_pdf_fixtures.py /private/tmp/workshop-granular.mdoZkg/pdfs

UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_granular_probe.py --directory /private/tmp/workshop-granular.mdoZkg --report tests/evidence/workshop-granular-final.json --run granular-final-20261009 --cli /Users/tyleredwards/.local/bin/overmind

UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_granular_probe.py --directory /private/tmp/workshop-granular.mdoZkg --report tests/evidence/workshop-wide-schema-final.json --run granular-wide-schema-20261009 --only wide-schema --cli /Users/tyleredwards/.local/bin/overmind

UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_recovery_load.py --directory /private/tmp/workshop-granular.mdoZkg --report tests/evidence/workshop-recovery-load-final.json --run recovery-load-final-20261009 --cli /Users/tyleredwards/.local/bin/overmind

UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_platform_replay.py --run granular-real-replay-20261009 --report tests/evidence/workshop-granular-real-replay.json --cli /Users/tyleredwards/.local/bin/overmind

UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_receipt_probe.py --fixture tests/evidence/workshop-granular-real-replay.json --report tests/evidence/workshop-receipt-sizes.json
```

The fixture generator creates files, not platform state. The live replay scripts
create disposable regression datasets in the selected project and preserve their
receipts, including intentional rejection cases. No datasets were deleted.

The current probe includes the subsequently added wide-schema case, so a fresh
complete run now executes 38 cases. The saved final report contains 37 plus the
separate one-case report. The final load and real-data replay ran concurrently.

Backend verification:

```bash
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync pytest tests/test_workshop_mcp_acceptance.py tests/test_dataset_store.py tests/test_dataset_files.py tests/test_mcp_datasets.py tests/test_dataset_transfers.py tests/test_workshop_documents.py tests/test_reusable_workshop_journey.py tests/test_mcp_catalog.py tests/test_mcp_resources.py tests/test_mcp_result_compat.py tests/test_mcp_observability.py tests/test_decision_workshop.py tests/test_workshop_readiness.py -q --tb=short
```

SDK verification, from `overmind/`:

```bash
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync pytest tests/test_resumable_transfer.py tests/test_transfer_connection.py tests/test_dataset_cmd.py -q --tb=short
```

Backend result: 193 passed, 3 skipped. SDK result: 40 passed. Three backend tests require `WORKSHOP_TEST_IMAGE` and were
skipped in the host pytest environment; the nine live runs exercised the installed
isolated runner. Existing short test-JWT warnings were observed. They were not
treated as extraction failures or silently suppressed.
