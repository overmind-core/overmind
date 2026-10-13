# Evidence: Data Workshop revamp (OVE-70)

Branch `frey/ove-70-data-workshop`, 2026-10-13. The unit suite and the journeys ran at `44375a7`; the
Console checks at `5ca6695` and the isolation suite at `c568d4c` (nothing in `frontend/` or `sandbox/` changed
after them). Plan: Linear document "Data Workshop revamp — implementation plan", section 17.

## Environment

- WSL2 (kernel 6.18.33.2), 16 CPUs, Docker 29.9.0, Python 3.13.14, Bun.
- Compose stack up (Postgres, Redis, workers, `sandbox` with only CHOWN, DAC_OVERRIDE, FOWNER, KILL, SETGID, SETUID
  and SYS_PTRACE, `no-new-privileges`, `init: true`,
  `/tmp` tmpfs 8 GiB, network `sandbox_net` internal).
- Unit tests and journeys start a host sandbox (`python -m sandbox`, `SANDBOX_ALLOW_SHARED_UID=1`):
  shared uid, host network, and no Tesseract on this host, so OCR cases skip there.
- Isolation tests run inside `celery-interactive-worker` against the compose `sandbox` (root, a
  uid per run, no route out, Tesseract installed).

## Commands and results

| Command                                                                  | Result                                                                          |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| `make test`                                                              | 3739 passed, 25 skipped                                                         |
| `make test-journeys`                                                     | 49 passed, 2 xfailed                                                            |
| `make lint-backend`                                                      | clean                                                                           |
| `make check-migrations` (with placeholder `SANDBOX_URL`/`SANDBOX_TOKEN`) | one migration after 0015: `0016_workshop_without_agent.py`; no changes detected |
| `cd frontend && bun run typecheck && bun run lint && bun run check:all`  | clean                                                                           |
| `cd frontend && bunx vitest run --maxWorkers=4`                          | 111 files, 844 tests passed                                                     |
| `make -C overmind test` / `make -C overmind lint-check`                  | 556 passed / clean                                                              |
| `pre-commit run --files <changed files>`                                 | passed                                                                          |
| Isolation (below)                                                        | 15 passed                                                                       |

`bun run test` with the default worker count timed out 16–17 unrelated tests at 5 s (evaluations,
finetuning, traces) while the compose stack loaded the machine; every one passes with
`--maxWorkers=4`. No datasets test was among them.

Isolation, from the repo root:

```sh
docker compose exec -T celery-interactive-worker mkdir -p /tmp/iso
docker compose cp tests/test_sandbox.py celery-interactive-worker:/tmp/iso/
docker compose cp tests/test_sandbox_isolation.py celery-interactive-worker:/tmp/iso/
docker compose cp tests/fixtures celery-interactive-worker:/tmp/iso/fixtures
docker compose exec -T -w /tmp/iso celery-interactive-worker sh -c \
  'SANDBOX_ISOLATION_URL=$SANDBOX_URL SANDBOX_ISOLATION_TOKEN=$SANDBOX_TOKEN \
   PYTHONPATH=/tmp/pytest-only:/tmp/iso:/code python -m pytest -p no:cacheprovider -q -o addopts="" \
   test_sandbox_isolation.py'
```

(`/tmp/pytest-only` holds pytest installed with `pip install --target`; the image has no test
dependencies.)

## Fixtures (`tests/fixtures/documents/`, sha256)

| File           | sha256                                                             |
| -------------- | ------------------------------------------------------------------ |
| `blank.pdf`    | `d9c0e2d743661e85a2c9328f7acb88e9b2d922f438e04d67d0b200f14005914f` |
| `blank.png`    | `f0707cb4f4725bee699908b1c74988e3f521a5d4c9527eb14755078b1c645616` |
| `faq.txt`      | `fe6e05278f432e8a4f7913443b1e337ac8bc5ea1ba25a039cf64663df6c853a8` |
| `latin1.txt`   | `2809ff010be0a77fd29e628fcb886955776dc10bf67ffe6f2140cb9fcc82985e` |
| `native.pdf`   | `c8821c7adb82d6388d9dfe5a05e18637ac2f2744834763362685712d3ff59f32` |
| `notes.md`     | `4e130db5e240dbd3e423724c4a51fc0c77f2ef98101c136d1f4a6df04c0ebfe4` |
| `password.pdf` | `0276b9f2ca0704558c1db95c962ca18c58f4d3556afaa7e61dbbe889d93fc084` |
| `policy.docx`  | `06ee8953a3bf72ecc75a611f77ba2a1cf0c8aacbe78ff9367e0c30058227d84d` |
| `scanned.pdf`  | `acbbf585f343b527c825ac8171ee370137ffd08dd87cd724a0472db723f78642` |
| `text.png`     | `a49120477931c850d88aa7363283c6b3b8d75461b12ff107c1891267b181d398` |

`generate.py` (`1d629bd4…`) rebuilds them.

## Failure list (plan 17.1)

| #   | Promise                                                                                             | Owning test                                                                                                                                                                                           | Observed                              |
| --- | --------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| 1   | A script cannot read the server's environment                                                       | `test_sandbox_isolation::test_a_script_cannot_read_the_server_environment`                                                                                                                            | pass (compose)                        |
| 2   | A socket has no route out                                                                           | `test_sandbox_isolation::test_a_script_has_no_route_out`                                                                                                                                              | pass (compose)                        |
| 3   | A script cannot read another run's scratch dir                                                      | `test_sandbox_isolation::test_a_script_cannot_find_or_read_another_run`                                                                                                                               | pass (compose)                        |
| 4   | Memory-hungry children are killed together                                                          | `test_sandbox::test_children_that_outgrow_the_reservation_are_stopped_together`, `test_sandbox_isolation::test_no_process_of_a_run_outlives_it`                                                       | pass                                  |
| 5   | A malicious document cannot read the server's environment                                           | Exclusion: no parser exploit is reproducible. Extraction runs as a run uid with the same minimal environment as item 1; `test_a_document_is_extracted_with_no_route_out` proves it runs in that child | pass (compose)                        |
| 6   | Same parent + script at once → one version                                                          | `test_mcp_datasets::test_the_same_parent_and_script_return_one_version_even_when_sent_at_once`                                                                                                        | pass                                  |
| 7   | Script error → no version; failure reused for an hour                                               | `test_mcp_datasets::test_a_failing_script_creates_no_version_and_its_failure_is_reused_for_an_hour`                                                                                                   | pass                                  |
| 8   | Sandbox down → transient; identical call runs again                                                 | `test_mcp_datasets::test_an_unreachable_sandbox_fails_transiently_and_the_same_call_runs_again`                                                                                                       | pass                                  |
| 9   | A full project queues the next run                                                                  | journey `test_a_slow_run_answers_running_and_a_full_project_queues_the_next`                                                                                                                          | pass                                  |
| 10  | A run past 25 s answers `running`; repeat returns the result                                        | same journey                                                                                                                                                                                          | pass                                  |
| 11  | Stale `expected_active` → `active_conflict`                                                         | `test_mcp_datasets::test_an_added_version_is_measured_inactive_and_activated_only_on_request`                                                                                                         | pass                                  |
| 12  | A `running` run older than 11 min → transient failure, runs again                                   | `test_mcp_datasets::test_a_run_that_stopped_without_a_result_is_failed_and_runs_again`, `test_the_sweep_fails_stuck_runs_and_deletes_runs_after_thirty_days`                                          | pass                                  |
| 13  | Every run has an `Execution` row with timings; runs older than 30 days go                           | `test_mcp_datasets::test_every_run_has_a_row_with_timings`, the sweep test above; Activity: `notebook-page.test.tsx` "counts running scripts and lists recent runs in Activity"                       | pass                                  |
| 14  | A used version cannot be deleted; deleting the dataset takes the tree                               | `test_dataset_chain::test_a_used_version_is_protected_and_blocks_deletion`, `test_a_version_with_children_cannot_be_deleted_but_the_dataset_takes_the_tree`                                           | pass                                  |
| 15  | Landing ends `idle` with `v0` active                                                                | `test_dataset_chain::test_landing_writes_v0_makes_it_active_and_proposes_the_intent`. "Only `land` was queued" holds by construction: no other dataset task exists (`test_celery_topology`)           | pass                                  |
| 16  | Migration: chain with a proposal and failed tail → tree; consumers untouched; A2 violation stops it | `test_workshop_migration` (3 tests)                                                                                                                                                                   | pass                                  |
| 17  | `data_format_fingerprint()` unchanged after the move                                                | Exclusion: `finetuning_validator.py` changes its import line, so the digest changes once (deviation 14, rollout note)                                                                                 | not tested by design                  |
| 18  | Manifest ≤ 40 KB and lists exactly the tools                                                        | `test_mcp_manifest::test_manifest_is_compact_and_omits_optional_output_schemas`, `mcp_fixtures.EXPECTED_TOOL_NAMES`                                                                                   | pass                                  |
| 19  | Text PDF → rows with pages and regions                                                              | `test_dataset_documents::test_a_text_pdf_lands_one_row_per_element_with_pages_and_regions`                                                                                                            | pass                                  |
| 20  | Scanned PDF → OCR rows; limitations mention OCR                                                     | `test_dataset_documents::test_a_scanned_pdf_lands_ocr_lines` (skips without Tesseract); compose extraction below                                                                                      | pass (compose, CI installs Tesseract) |
| 21  | Password-protected PDF refused                                                                      | `test_dataset_documents::test_an_unreadable_document_fails_the_landing_with_no_rows[password]`                                                                                                        | pass                                  |
| 22  | 2,001-page PDF refused with the limit text                                                          | same test `[page-limit]`                                                                                                                                                                              | pass                                  |
| 23  | Non-UTF-8 `.txt` refused                                                                            | same test `[not-utf8]`                                                                                                                                                                                | pass                                  |
| 24  | Image without text refused                                                                          | same test `[no-text]` (skips without Tesseract); compose `blank.png` below                                                                                                                            | pass (compose)                        |
| 25  | Gzipped document refused at upload                                                                  | `test_dataset_documents::test_a_gzipped_document_is_refused_at_upload`                                                                                                                                | pass                                  |
| 26  | Tables and documents in one batch refused                                                           | `test_dataset_documents::test_tables_and_documents_in_one_batch_are_refused`                                                                                                                          | pass                                  |
| 27  | Extraction works with the network off                                                               | `test_sandbox_isolation::test_a_document_is_extracted_with_no_route_out`                                                                                                                              | pass (compose)                        |
| 28  | `json_rows_field` lands only that array; a wrong field is refused                                   | `test_dataset_documents::test_a_json_rows_field_lands_only_that_array`, `test_a_missing_json_rows_field_is_refused`                                                                                   | pass                                  |
| 29  | A document split keeps each document on one side                                                    | `test_dataset_documents::test_a_split_keeps_each_document_on_one_side`                                                                                                                                | pass                                  |
| 30  | After landing the upload is gone; rows carry the file's sha256                                      | `test_a_text_pdf_lands_one_row_per_element_with_pages_and_regions`                                                                                                                                    | pass                                  |

Compose extraction (`sandbox` service, network off), one run per file:

| File                                    | Result                                                                                 |
| --------------------------------------- | -------------------------------------------------------------------------------------- |
| `scanned.pdf`                           | ok, 1 row, `docling-native-pdf+tesseract-ocr`, OCR limitation listed                   |
| `invoice.png`, `invoice.jpg`            | ok, 2 rows each, `tesseract-ocr`                                                       |
| `native.pdf`, `handbook.pdf`            | ok, 2 and 12 rows, `docling-native-pdf`                                                |
| `policy.docx`, `returns.docx`, `faq.md` | ok, 3, 5 and 7 rows, `docling`                                                         |
| `faq.txt`                               | ok, 2 rows, `utf8-paragraphs`                                                          |
| `blank.png`                             | refused: "No text was found after OCR. Check that the document contains legible text." |

## End-to-end matrix

A held journey stack (live ASGI, a Celery worker thread, a host sandbox, fake LLMs) with the branch
SDK (`overmind` 0.1.86) and MCP over HTTP. Checks run against the final code:

- Tables: CSV (241 rows, emoji and accents intact), TSV, JSONL, NDJSON, JSON with a default and a
  named wrapper field, Parquet, a 200,000-row `.csv.gz`, a 300-column CSV (preview 200 columns),
  ragged CSV refused with the row number.
- Documents: PDF (12 rows), DOCX, Markdown, text; password, non-UTF-8 and page-limit refusals;
  mixed batch refused; a split of three documents keeps each document on one side; a split of one
  document is refused before any dataset exists.
- Sources: traces (4 rows, `prepare_examples` eval version fits), LLM calls, empty trace selection.
- Versions: tree with branches, identical add returns the same version, failure reuse, diff,
  `active_conflict`, probe, try, synthetic rows (new identities past every version's
  `source_row`), `update_dataset`, a 200,000-row script, 8 concurrent runs, export headers, the
  REST surface.
- Scripts: `re.compile` and `df.eval` run; `import socket` and `open()` are refused; a frame with
  `NaN` and `NaT` previews them as null; renumbered rows report `identity_preserved: false`.
- Exclusions on the host stack: OCR (no Tesseract; compose above) and "no network" (the host
  sandbox has the host network; compose isolation item 2).

## Agent sessions

Four Claude sessions used the MCP tools and CLI against the held stack: eval preparation from a
CSV, documents, a training dataset with branches and synthetic rows, and adversarial QA. Their
defects are fixed, each with a test except the `preparation_context` wording (prose, not tested): synthetic rows reused existing identities;
renumbered rows got identity by position; `re.compile` was refused; `inspect_dataset` hid the
source files and limitations; a one-document split left two datasets in error; unreadable sources
offered a retry; `SUM()` came back as a string; `get_job` had no `completed_at`; export headers
were read case-sensitively; `preparation_context` said eval reads only `input.messages`. Rechecking
those fixes on the stack found one more: a `NaN` in a try preview failed the run on the worker.
