# Source fidelity and document ingestion

Contract review and live qualification for Linear workstream 03. The replay scripts (`workshop_mcp_acceptance.py`, `workshop_pdf_fixtures.py`) and the machine-readable receipt (`source-fidelity-qualification.json`) are no longer in the tree; recover them from commit `5a66697` (`git show 5a66697:tests/evidence/<file>`). The reproduction commands below refer to those paths.

## Package A — contract review

Revision: `codex/source-fidelity` @ `63caac8`.
Date: 2026-10-12.
Surfaces in this review: domain, REST transfer, MCP `overmind://dataset-upload` / inspect, CLI `--json-rows-field`.
Console extraction UI is dispositioned: MCP is the control surface.

This records current implementation against the three lossless-source contracts. It does not claim Package B qualification. Existing pytest, journeys and the 2026-10-08 PDF replay are labelled separately and are not this ticket's reproduce pass.

Line numbers were re-read on this revision after a hostile pass. Policies 1–3 were called 2026-10-12: refuse unmarked JSON objects, no mixed-trace capability, pin MCP document/page fields.

## Failure modes (captured before product changes)

These are the ways the three contracts can fail. Reproduce them before any product edit.

1. Without `--json-rows-field`, `_iter_json` silently guesses wrapper keys `data` / `rows` / `items` / `examples`, or treats the whole object as one row. Neither choice is recorded (`land.read_file` writes `extraction = {}`).
1. Wrong `--json-rows-field` still publishes a dataset.
1. Original bytes on disk differ from the uploaded SHA-256, or `document_id` is not the artifact hash.
1. A PDF page the primary parser missed is omitted, OCR-replaced, or reported as semantically complete.
1. Font-encoding artifacts are silently corrected or dropped from `limitations`.
1. Mixed scalars, nulls, or integers outside signed 64-bit are coerced (float promotion, stringification, dropped nulls). Integer fidelity only; JSON float literals are not in this contract.
1. `source_row` / file provenance / document identity are rewritten to display position.
1. Trace landing uses the dataset destination capability when a source filter is set.
1. A trace with two outer invocations of the selected capability publishes mixed rows.
1. A mixed-capability trace is attributed to the root span's capability even when other capabilities are present (`trace_row` prefers `root.capability` over uniqueness).
1. OCR / empty extraction publishes a partial cell.
1. MCP `overmind://dataset-upload` numeric document/page fields exist but are unpinned in tests; image/OCR caps are prose-only in `documents`.

## Contracts

| Contract                                                                   | Status                                                          | Evidence                                                                                                                                                                                                                                      | Prior tests (not this reproduce pass)                                                                                                                             | Gap                                                                                                       |
| -------------------------------------------------------------------------- | --------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Original bytes retained; SHA-256 identity                                  | implemented                                                     | `documents.py:30` `identity = store.file_sha256(path)`; `land.py:163-165` `publish` copies `staged_path` to `paths.source_path`; `EvidenceRows.__iter__` `land.py:278-284` stamps `provenance.file.{sha256,filename,row}`                     | `tests/test_workshop_documents.py` (`document_id == artifact["sha256"]`, original download); `tests/test_dataset_transfers.py` completion hash                    | —                                                                                                         |
| Explicit JSON row selection; original wrapper bytes kept                   | implemented                                                     | Explicit: `files._iter_json` with `rows_field` set. A JSON object without the field raises `FileError` naming `--json-rows-field`. Top-level arrays and JSON Lines still land. `land.py` records `selection: explicit` when the field is set. | `test_a_json_object_without_rows_field_is_refused`; `test_wrapped_json_without_rows_field_is_refused_before_dataset_publication`; explicit-field tests            | —                                                                                                         |
| Parser / OCR versions, language                                            | implemented                                                     | `documents.py:119` `version("docling-slim")`; `ocr.py:132-137` `{engine, version, languages: ["eng"]}`                                                                                                                                        | `tests/test_workshop_documents.py` scanned/image landing asserts engine/version/languages                                                                         | Docling version string itself is not asserted.                                                            |
| Coordinates, page regions, EXIF orientation                                | implemented                                                     | Docling `item.prov` bboxes in `documents.py`; Tesseract TSV in `exif-oriented-pixels` (`ocr.py:132-145`); PDF `display_box` `pdf_text.py:85-106` (rotation 0/90/180/270)                                                                      | Image EXIF ROTATE_90 in `test_image_upload_extracts_text_with_upright_evidence_and_original_download`                                                             | PDF page rotation has no fixture. Package B.                                                              |
| Encoding warnings                                                          | implemented                                                     | PDF control-char census `pdf_text.py:41-43` → limitation `documents.py:141-144`; `.txt` non-UTF-8 raises in `documents.py`; CSV/JSON/JSONL non-UTF-8 is a hard `FileError` `files.py:226-227`                                                 | `test_type3_native_text_survives_landing_without_ocr_replacement`                                                                                                 | CSV/JSONL non-UTF-8 path untested.                                                                        |
| PDFium recovery before OCR; limitations; no partial land                   | implemented                                                     | `documents.py:120-151` recovery then OCR; empty extract / OCR failure raise before rows; `ocr.py` "No partial source was landed"                                                                                                              | `test_type3_native_text_survives_landing_without_ocr_replacement`; `test_blank_pdf_reports_no_text_after_ocr`; `test_ocr_failure_never_lands_partial_native_rows` | Partial blank pages inside a mostly-text PDF untested. Package B.                                         |
| Heterogeneous scalars, nulls, integers > int64, no inexact float promotion | implemented                                                     | `store.ManifestBuilder` `store.py:134-173`; int outside `±2**63` → json kind; mixed kinds collapse to json; readers decode without re-inference                                                                                               | `test_landing_and_pipeline_preserve_scalar_values_without_lossy_coercion` (`2**63`, `10**40`); `tests/test_dataset_store.py`                                      | CSV landing of >int64 not separately pinned. JSON float literals are IEEE-754, not this integer contract. |
| Declared source identities (`source_row`, document SHA)                    | implemented                                                     | `land._stamp_source_rows` `land.py:82-87`; `EvidenceRows` file identity; document rows carry `_overmind_document_id`                                                                                                                          | document tests on `source_row` uniqueness and `document_id`                                                                                                       | —                                                                                                         |
| Source capability filter distinct from destination association             | implemented                                                     | `selection.py:66-73` `preferred_capability_id` or `filters.capability`; persisted on the source spec; `land.py:515-531` `_preferred_spans` scopes spans; `prepare` infers dataset capability separately                                       | journey `test_a_source_capability_filter_is_preserved_when_the_destination_differs`                                                                               | Field name is not asserted in pytest; behaviour is.                                                       |
| Repeated matching invocations fail before publication                      | implemented                                                     | `land._preferred_spans` `land.py:526-530` raises `LandError` when `len(roots) != 1` during `iter_trace_rows`, before `publish`                                                                                                                | journey `test_repeated_capability_invocations_refuse_to_publish_a_mixed_example` (`cells == []`)                                                                  | `len(roots) == 0` wording untested.                                                                       |
| Mixed traces get no invented single-capability attribution                 | implemented                                                     | `land.trace_row`: unique capability among spans, else `None`. Root capability does not override a mixed set. Unmatched source filter keeps whole-trace spans.                                                                                 | `test_a_mixed_capability_trace_is_not_attributed_to_the_root`                                                                                                     | —                                                                                                         |
| Missed text is not semantic success                                        | implemented                                                     | Limitations for missed pages, unverified recovery, English-only OCR; empty extract fails                                                                                                                                                      | document tests keep rows as evidence; no `semantic_quality` field asserted in pytest                                                                              | Evidence replay asserts `semantic_quality: unmeasured`; not this pass.                                    |
| Format limits discoverable through MCP                                     | implemented (numeric document/page fields; image caps as prose) | `overmind://dataset-upload` returns `document_max_bytes`, `pdf_max_pages`, `json_array_max_bytes`. Same payload `documents` states 100 MiB, 2000 pages, 64 MP, animated rejection.                                                            | `test_dataset_upload_resource_describes_cli_flow_and_server_limits` pins `document_max_bytes`, `pdf_max_pages`, and the `documents` prose                         | Image caps remain prose-only.                                                                             |

## Do not rebuild

Passing mechanisms. Do not rewrite them to produce a diff.

- `ManifestBuilder` JSON-text storage and Arrow manifest (`store.py`)
- `EvidenceRows` per-row file provenance (`land.py`)
- PDFium recovery, `merge_boxes`, rotation-aware `display_box`, native-glyph OCR mask (`pdf_text.py`, `ocr.py`)
- Atomic publish of staged original bytes (`land.py` `publish`)
- Unique-invocation fail in `_preferred_spans` (`land.py`)
- Explicit JSON field validation and original-byte retain on the transfer path (`files.py` when `rows_field` is set; `tests/test_dataset_transfers.py`)
- Transfer request-key recovery and SHA-256 bind (`transfers.py`, CLI)
- MCP `overmind://dataset-upload` numeric `document_max_bytes` / `pdf_max_pages` fields

## Dispositions (will not build in this package)

- English-only Tesseract (`languages: ["eng"]`). Declared in limitations.
- No table reconstruction or reading-order certification. Declared in limitations.
- One file per CLI transfer; serial attachments become new versions. Atomic multi-file CLI handoff stays unsupported.
- Trace cells truncated at 200_000 characters with an explicit cut marker. Detectable loss, not silent.
- Console and REST `POST /api/datasets/` selecting a JSON row field. Only the CLI transfer path accepts `--json-rows-field`; a wrapped `.json` dropped into the Console is refused, and the error names a CLI flag. A REST/Console selector would change the generated client and MCP contract and needs the transfer owner (workstream 01).
- Console hiding extraction metadata / OCR page counters. MCP inspect and `overmind://dataset-upload` remain the agent surface.
- Handwriting, multilingual OCR, dense layout reconstruction, OCR cancellation mid-page, worker restart during large landing: Package B limits of proof unless a Package A contract fails first.

## Policies called (2026-10-12)

1. JSON: refuse a JSON object without `--json-rows-field`. Do not guess wrapper keys. Top-level arrays and JSON Lines still land.
1. Mixed traces: more than one capability in the landed spans → `capability_id` / `capability` are `None`. Root does not win.
1. MCP: pin `document_max_bytes` and `pdf_max_pages` on `overmind://dataset-upload`. Image caps stay prose.

## Prior evidence (do not sum as this ticket)

| Kind                                                                                                                         | What it covers                                                          | What it is not                         |
| ---------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | -------------------------------------- |
| pytest `test_workshop_documents.py`, `test_dataset_transfers.py`, `test_dataset_store.py`, `test_workshop_mcp_acceptance.py` | Small synthetic fixtures for documents, JSON field, scalars, MCP paging | Live format matrix                     |
| journeys `tests/journeys/test_data_workshop.py`                                                                              | Source vs destination capability; repeated invocations                  | No PDF/OCR                             |
| `tests/evidence/workshop-pdf-experience.md` + `workshop-pdf-results.json` (2026-10-08)                                       | 20-case live PDF replay on a prior revision                             | Not this revision's Package B artifact |

## Reproduce log

Working tree on `codex/source-fidelity` after the three policy edits.

Command (host, before Tesseract):

```
uv run pytest tests/test_workshop_documents.py tests/test_dataset_transfers.py tests/test_dataset_store.py tests/test_workshop_mcp_acceptance.py tests/test_landing_incident_recovery.py tests/test_dataset_land_traces.py -q
```

First run: **12 failed, 80 passed** (38.67s). 11 failures: host had no `tesseract`. 1 failure: `test_jsonl_and_json_wrappers` still expected the old `data` guess — test updated to refuse; **1 passed** on re-run.

Tesseract 5.5.3 (`eng` + `osd`) was installed with Homebrew (`uv` cannot install that binary). Re-run:

```
uv run pytest tests/test_workshop_documents.py tests/test_workshop_mcp_acceptance.py::test_document_source_identifiers_survive_bounded_mcp_metadata -q
```

**25 passed** (16.30s).

```
make test-journeys test_args="-k data_workshop"
```

**21 passed**, 55 deselected, 80.02s. Includes source-filter vs destination and repeated invocations.

After `docker compose restart api celery-landing-worker` (API healthy): host pytest **118 passed** (36.59s); journeys **21 passed** (54.44s).

| #                                                                       | Command / setup                                                                                                                                           | Result                                              | Product change authorized?                       |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- | ------------------------------------------------ |
| 1 Wrapped JSON without `--json-rows-field` (key guess and whole-object) | `uv run pytest tests/test_dataset_files.py tests/test_dataset_transfers.py tests/test_dataset_store.py::test_jsonl_lands_and_a_json_object_is_refused -q` | Refused before publication                          | Yes — guess removed; leftover store test aligned |
| 2 Wrong `--json-rows-field`                                             | `test_missing_explicit_json_field_is_refused_before_dataset_publication` in the host pytest set                                                           | Passed (409, no dataset)                            | No                                               |
| 3 Original bytes vs SHA-256                                             | `test_workshop_documents.py` after Homebrew Tesseract                                                                                                     | Passed (original download, `document_id == sha256`) | No                                               |
| 4 Missed PDF page / OCR vs native                                       | `test_type3_native_text_survives_landing_without_ocr_replacement`; scanned/mixed PDF tests                                                                | Passed                                              | No                                               |
| 5 Font-encoding limitation                                              | `test_type3_native_text_survives_landing_without_ocr_replacement`                                                                                         | Passed (`font-encoding` in limitations)             | No                                               |
| 6 Scalars / >int64 / nulls                                              | `test_landing_and_pipeline_preserve_scalar_values_without_lossy_coercion`                                                                                 | Passed                                              | No                                               |
| 7 `source_row` identity                                                 | document tests                                                                                                                                            | Passed                                              | No                                               |
| 8 Source filter vs destination                                          | `make test-journeys test_args="-k data_workshop"`                                                                                                         | Passed                                              | No                                               |
| 9 Repeated invocations                                                  | same journey file                                                                                                                                         | Passed                                              | No                                               |
| 10 Mixed-capability trace (root vs uniqueness)                          | `test_a_mixed_capability_trace_is_not_attributed_to_the_root`                                                                                             | `capability_id` is null                             | Yes — uniqueness only                            |
| 11 Partial OCR land                                                     | `test_ocr_failure_never_lands_partial_native_rows`; `test_blank_pdf_reports_no_text_after_ocr`                                                            | Passed (no partial cell)                            | No                                               |
| 12 MCP limit fields vs resource test                                    | `test_dataset_upload_resource_describes_cli_flow_and_server_limits`                                                                                       | Fields present; test pins them                      | Test-only                                        |

## Main after PR 182 (overmind-core/overmind#182)

Main gained `8e6a396` (streaming Data Workshop frames) after this branch diverged; a trial merge conflicts in about 40 files, so this evidence is for the branch without it.

- Does not apply here: the sandbox memory budget and the 100,000-row cell guidance. Both belong to the notebook runner, which this branch removed.
- Must be re-applied on merge: `FileRows` replaces `read_file_rows` in `files.py`, and main's `_iter_json` still guesses `data`/`rows`/`items`/`examples` and has no `rows_field`. The refusal, explicit field selection and the `trace_row` uniqueness change need porting onto the streaming reader, and landing now reads each file twice, so the field check must hold on both passes.
- Already compatible: CSV/TSV numeric conversion keeps `007`, `1_000`, `nan` and 19-digit ids as text.
- New losses to record as dispositions: a non-finite number nested in a JSON value is stored as `null`, and an integer column with nulls previews as `3`, not `3.0`.
- Required before sign-off: re-run the 26-case replay and the follow-on cases on the merged tree.

## Package B — qualification

Revision: `codex/source-fidelity` @ `f62bb282674291d720deaa87210f77601acfa74a`.
Date: 2026-10-12.
Surfaces: MCP `overmind://dataset-upload`, `overmind://interface/current`, `inspect_dataset`, `get_job(dataset_run)`; CLI upload; REST transfer inspect; cell export JSONL.
Console extraction UI is dispositioned MCP-native (the Package A section); not re-checked.

Companion: `source-fidelity-qualification.json`.
Single live log: `/private/tmp/source-fidelity-pkgb-replay-full.log` (sha256 `9112fef679fa…`).
No paid providers. `billable_usage_records: 0`.

This pass is one execution of 26 cases. Earlier split logs are not this result.

## Labelling

| Evidence                                             | Kind                                                      | What it covers                                                 | What it does not                    |
| ---------------------------------------------------- | --------------------------------------------------------- | -------------------------------------------------------------- | ----------------------------------- |
| This live replay                                     | THIS REVISION, live, one run                              | 26 landing/refusal cases on hashed fixtures                    | Not pytest, not journeys            |
| Host pytest 137 passed                               | THIS REVISION, pytest / in-process MCP                    | Documents, transfers, scalars, JSON field, MCP resource fields | No HTTP MCP, no 2000-page live file |
| `make test-journeys -k data_workshop` 21 passed      | THIS REVISION, journeys (provider-faked; extraction real) | Source filter vs destination; repeated invocations             | No PDF/OCR matrix                   |
| TradingAgents Pi read of `overmind://dataset-upload` | THIS REVISION, live HTTP MCP                              | Numeric limits and limitation prose                            | Does not land files                 |
| `workshop-pdf-experience.md` (2026-10-08)            | PRIOR REVISION, live                                      | Earlier PDF replay                                             | Not this revision                   |

Do not add these counts together.

## Live cases

26 passed, 0 failed, one process.

Independent comparison for landed PDFs: original-byte SHA download, per-page markers and `document_id` on every exported JSONL row, provenance regions on every exported row. MCP `query_dataset` is not used as the provenance source (32 KiB cap).

`document_and_ocr_evidence_round_trip` includes `scan-rotated.jpg` (pixels rotated 90°, EXIF orientation 6) and asserts `coordinate_space == exif-oriented-pixels`. `rotated-native.pdf` only sets PDF `/Rotate` on native text; that is not raster/EXIF proof.

`pdf_over-byte-limit` refused with `overmind://dataset-upload` in the SDK error, `uploaded_bytes: 0`, and no PUT on the SDK session. Extra POST/GET from connection check are expected.

`semantic_quality` is not reported on landing cases. There is no pipeline receipt on this path.

## Discovered limits (live MCP)

`overmind://interface/current` contract_version `7.3.0`.

| Field                  | Observed   |
| ---------------------- | ---------- |
| `document_max_bytes`   | 104857600  |
| `pdf_max_pages`        | 2000       |
| `json_array_max_bytes` | 268435456  |
| `max_bytes`            | 2147483648 |
| `chunk_bytes`          | 8388608    |
| `max_files`            | 100        |

`documents` prose: 100 MiB per document, 2000 pages per PDF, 64 megapixels, animated images rejected, English Tesseract, reading order not reconstructed.
`json_selection`: JSON object requires `--json-rows-field`; wrapper keys are not guessed.
Missed text is not semantic success: recovery does not certify partial omissions; `mixed-blank-pages.pdf` records `Pages without extracted text: 3, 6`.

## Reproduction

Compose stack already running. Fixture generation needs ReportLab, pypdf, Pillow on the host. Checkout `f62bb28` for the harness used in this log. Tesseract 5.5.3 (`eng`, `osd`).

```sh
git checkout f62bb282674291d720deaa87210f77601acfa74a
pdf_fixture_dir=$(mktemp -d /private/tmp/workshop-pdf-fixtures.XXXXXX)
uv run --with reportlab --with pypdf --with pillow python tests/evidence/workshop_pdf_fixtures.py "$pdf_fixture_dir"
pdf_replay_dir=$(docker compose exec -T api mktemp -d /tmp/workshop-pdf-replay.XXXXXX)
docker compose cp overmind/overmind "api:$pdf_replay_dir/overmind"
docker compose cp tests/fixtures/documents "api:$pdf_replay_dir/documents"
docker compose cp "$pdf_fixture_dir" "api:$pdf_replay_dir/pdfs"
set -o pipefail
docker compose exec -T -e PYTHONPATH="$pdf_replay_dir" -e DO_NOT_TRACK=1 \
  -e WORKSHOP_ACCEPTANCE_PDFS="$pdf_replay_dir/pdfs" \
  -e WORKSHOP_ACCEPTANCE_DOCUMENTS="$pdf_replay_dir/documents" \
  -e WORKSHOP_ACCEPTANCE_CASES='catalogue_auth_and_all_tool_validation,tabular_file_upload_transform_export,document_and_ocr_evidence_round_trip,pdf_native-1,pdf_native-10,pdf_native-100,pdf_native-500,pdf_native-2000,pdf_native-2001,pdf_scanned-1,pdf_scanned-25,pdf_mixed-30,pdf_rotated-native,pdf_mixed-blank-pages,pdf_large-scanned,pdf_near-byte-limit,pdf_over-byte-limit,pdf_encrypted,pdf_blank,pdf_corrupt,pdf_batch_10,pdf_batch_25,pdf_batch_100,pdf_mixed_batch,pdf_atomic_batch_recovery,pdf_batch_limit_101' \
  api python manage.py shell < tests/evidence/workshop_mcp_acceptance.py \
  2>&1 | tee /private/tmp/source-fidelity-pkgb-replay-full.log
```

Fixture hashes: `source-fidelity-qualification.json`.

## Follow-on: messy report PDF

Separate from the 26-case run. One extra live case `pdf_messy-report` after adding `messy-report.pdf` (title, 4-row SKU grid, two-column notes, second page footer).

Log: `/private/tmp/source-fidelity-messy-report.log` (sha256 `faaf69f98dcc…`). Passed in 0.796 s. 2 pages, 25 exported rows, original-byte SHA `fda74fdaab75fa35eb6dc50926ae582f2950eb182464ba11349841ee06c5dba7`. Extraction `docling-native-pdf` 2.131.0. Limitation retained: `Reading order and visual table structure are not reconstructed.`

Phrases found in exported row text: `Q3 warehouse exception log`, `Returns window is thirty days from delivery`, `SKU-4419`, `SKU-8821`, `Aisle B7 is closed on bank holidays`, `Cycle count 8841`, `HOLD-ZONE-C`.

This does not reconstruct a table, score layout, or claim semantic quality.

```sh
pdf_fixture_dir=$(mktemp -d /private/tmp/workshop-pdf-fixtures.XXXXXX)
uv run --with reportlab --with pypdf --with pillow python tests/evidence/workshop_pdf_fixtures.py "$pdf_fixture_dir"
pdf_replay_dir=$(docker compose exec -T api mktemp -d /tmp/workshop-pdf-replay.XXXXXX)
docker compose cp overmind/overmind "api:$pdf_replay_dir/overmind"
docker compose cp "$pdf_fixture_dir" "api:$pdf_replay_dir/pdfs"
docker compose exec -T -e PYTHONPATH="$pdf_replay_dir" -e DO_NOT_TRACK=1 \
  -e WORKSHOP_ACCEPTANCE_PDFS="$pdf_replay_dir/pdfs" \
  -e WORKSHOP_ACCEPTANCE_CASES='pdf_messy-report' \
  api python manage.py shell < tests/evidence/workshop_mcp_acceptance.py \
  2>&1 | tee /private/tmp/source-fidelity-messy-report.log
```

## Follow-on: byte limit, cancellation and worker loss

Separate executions after the 26-case run, at harness commit `5a66697`; not added to its count. Details and hashes: `follow_on_limits_and_worker_loss` in the JSON. No paid providers.

| Case                                                                                     | Result                                                                                                                                                                                                          |
| ---------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pdf_exact-byte-limit` (104,857,600 bytes, `near-byte-limit.pdf` plus trailing newlines) | Landed; original bytes and exported rows verified                                                                                                                                                               |
| `pdf_limit-plus-one-byte` (104,857,601 bytes)                                            | Refused before transfer; no `PUT`; 0 bytes uploaded                                                                                                                                                             |
| `pdf_cancel_during_landing` (`scanned-25.pdf`)                                           | `cancel_dataset` returned the dataset to `idle` in 0.011 s with the import receipt `cancelled`; no cell published after a 30 s wait (a normal landing takes about 9 s); a new upload to the same dataset landed |
| `pdf_worker_loss_recovery` (`scanned-25.pdf`)                                            | The landing worker was killed mid-landing. The receipt went attempt 1 running, attempt 2 running, complete, with all 25 pages exported and no manual resume. Terminal after 316 s                               |

```sh
# fixtures and copy steps as above; WORKSHOP_ACCEPTANCE_CASES='pdf_exact-byte-limit,pdf_limit-plus-one-byte,pdf_cancel_during_landing'
# worker loss: add WORKSHOP_ACCEPTANCE_KILL_WORKER=1 and case pdf_worker_loss_recovery, then when the
# log prints KILL_LANDING_WORKER_NOW run: docker compose kill celery-landing-worker && docker compose up -d celery-landing-worker
```

Limits of this proof: cancellation arrived before OCR progress was reported, and the OCR loop checks no cancellation, so extraction already running finishes without publishing. The worker loss was a `SIGKILL`, not a graceful restart or a broker outage.

## Limits of this proof

Handwriting, multilingual OCR and table reconstruction were not added. A 2-page grid/two-column report was landed for phrase presence only. `near-byte-limit.pdf` landed; `over-byte-limit.pdf` was refused with no SDK PUT.
Pipeline package execution is out of this package. Image 64MP / animated rejection remain pytest and MCP prose, not this 26-case list.
The 2026-10-08 PDF replay is prior-revision evidence.
