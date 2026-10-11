# Complex handbook extraction and Workshop verification

Validated on 2026-10-09 local time (receipts extend into 2026-10-10 UTC), against
the existing local deployment and project `financial-services`
(`e18b29b5-915d-45a7-80cd-77ffe6559205`). All live platform mutations used MCP and
the installed local transfer CLI. No browser, training, evaluation or inference
was used. No original user dataset or immutable recipe was rewritten.

## Findings and repairs

The supplied file is
`/Users/tyleredwards/Downloads/Lakera_Handbook_AI_Security_for_Product_Teams-.pdf`:
39 pages, 20,604,056 bytes, SHA-256
`8afd9328403fa5aa139ae44c8da8140ced77471e3404396bf9b5e97f2280f93a`.
Page 2 is genuinely blank, confirmed by rendering. Pages 2, 3, 11, 12, 13 and 33
were visually inspected. Embedded instructions/security examples were treated
as source content, not commands to the agent or platform.

### Native text was being lost

The primary Docling parser returned success but zero text items for the entire
document. The PDF contains selectable Type3-font text with empty glyph painting
instructions and separately rendered visible lettering. PDFium could read the
encoded text; the old landing path instead sent every page through OCR.

Before changing behavior, a four-rotation Type3 fixture and extraction regression
were added. The regression failed before the native recovery implementation.
The final fixture was also rendered and inspected. Its visible control-character
placeholder is intentional: that character must be preserved and disclosed.

Overmind now recovers encoded native text only on pages where the primary parser
found no text. It retains the original parser's document/image information, masks
recovered text regions before image OCR, and preserves per-row parser attribution.
Crop/rotation-aware bounding boxes, recovered pages, characters, control-character
counts and warnings are available through source inspection. It does not rewrite
source text or claim that native text establishes correct reading order.

| Measurement                               | Baseline | Corrected |
| ----------------------------------------- | -------: | --------: |
| Extracted rows                            |    1,176 |     1,381 |
| Native-text rows                          |        0 |     1,300 |
| OCR rows                                  |    1,176 |        81 |
| Pages sent through OCR                    |       39 |        23 |
| Nonblank pages with recovered native text |        0 |        38 |

The corrected native rows retain 61,990 encoded characters, including 57
non-whitespace Unicode control characters. Every nonblank page achieved full
coverage in the diagnostic native-token comparison. This compares token counts
against PDFium text, not visual fidelity, character-level OCR accuracy, table
structure or reading order. Every exported row's document hash, page references
and region bounds passed inspection.

Evidence: `workshop-handbook-baseline.json`, `workshop-handbook-fixed.json` and
`workshop_handbook_audit.py`. The failed baseline remains available as dataset
`c0d2cdc3-01d8-4209-9806-6b572364bde0`; it was not silently repaired in place.

### Upload retries displayed historical status as current

A stable-key retry correctly reused the transfer, but its saved publication
snapshot still said `landing` after extraction had finished. The human CLI output
made that snapshot look current. This was an Overmind UX defect, not a stalled job.

The CLI now returns `state_scope=at_publication` without `--wait` and says the
transfer was published. It directs the agent to current MCP progress. With
`--wait`, it reads the terminal state and returns
`state_scope=observed_after_landing`. The receipt itself stays immutable.
The regression was added before this behavior change. Live retries verified both
scopes, with the observed state `idle` and no additional source cell.

Evidence: `workshop-handbook-upload-receipt.json` and SDK transfer tests.

## Real execution, reuse and branching

The original four-step document recipe
`25b7e82f-af5a-478e-9ad1-ce0d9be78008` was downloaded, inspected and reused without
changing its meaning. Its existing review predicate yields an empty branch for
this input. A separately attributed `derived_from` variant was authored by the
coding agent to split by measured extraction method, not invented document labels.

For each recipe, the harness performed one preview and two full publications.
This was repeated in a second pass: **12/12 executions passed**. The variant's
full runs routed 81 OCR rows and 1,300 native rows through separate branches, then
rejoined all 1,381 rows into 38 page records. Both variants preserved identical
page text and all parent-row identities. Their output content hash was
`830c09df5f3fd30434bf410d7dcf66b9d4a73afc5ca7ce254722b1956b9c7689`.

Previews did not publish cells. Reused request keys returned the same run.
Incorrect source fingerprints returned `source_conflict` without changing the
selected output. The source dataset and original pipeline remained unchanged.
These checks establish execution and lineage fidelity, not semantic approval for
training. All new handbook datasets have `explore` intent.

Evidence: `workshop-handbook-replay.json` and `workshop-handbook-verified.json`.

Two additional fresh PDF uploads completed in **11.317 s** and **10.128 s** from
CLI invocation to observed idle. Full exports exactly matched the corrected
source, including provenance. Repeating each upload reused its transfer and left
one source cell. Corrected datasets:

- `42c67cd7-fea6-46d5-a836-028080798616`
- `ff1b2b00-ed30-49ef-96d9-fa7e55920b8a`

The final branched page-evidence dataset is
`2c80b047-5194-4e18-bd64-9b8874b5b778`, selected output
`3285b10a-bece-4ed8-a362-f94d0a25f563`.

## Wider regressions and code verification

The post-fix live matrix passed **38/38** cases. It covered mixed scalars, large
and nullable integers, nested Unicode, quoted CSV/TSV, Parquet, gzip JSONL,
text/Markdown, PNG/JPEG/WebP, 100,000 rows, wide values/schema, native PDFs up to
2,000 pages, 25-page scans, mixed documents, and near-limit files around 104 MB.
Malformed, encrypted, empty and over-limit inputs passed only when safely rejected.
These are the same concrete fixture expectations as the prior granular matrix;
the sparse 2,000-page fixture is not representative of a dense 2,000-page handbook.

- Backend document, transfer, storage, MCP acceptance/catalog/resource checks:
  **122 passed**, no skips, 67 existing short-test-JWT warnings.
- MCP catalog/resources after the final guidance update: **29 passed** (overlaps
  the broader backend suite; not 29 additional distinct cases).
- SDK transfer/CLI/connection checks: **41 passed**.
- Scoped pre-commit and SDK Ruff checks passed. No frontend behavior or REST
  schema changed; frontend checks and client regeneration were not needed.

Evidence: `workshop-handbook-regression.json`; test logs under
`/private/tmp/lakera-validation.n4LRTI/{regression,catalog-verified,sdk-verified}.log`.

## Product performance and remaining gaps

These are local development measurements, not production SLOs. The 38-case
matrix reported p95 reads of 36 ms for jobs, 41 ms for inspection and 51 ms for
queries. One inspection took **6.6124 s** while other work was running. Its cause
was not established. Three later reads of that same small dataset took 24–41 ms;
three reads of the corrected handbook took 22–23 ms. The outlier is not erased by
successful retries. Rechecks are recorded in
`workshop-handbook-inspection-recheck.json`.

The final full four-step pipelines recorded 11.4–11.7 s server execution and
17.7–23.5 s client completion including polling and verification. Three-row
previews still took 14.1–15.1 s client-side. There is meaningful small-job overhead;
these measurements do not isolate container startup from script execution. The
preview receipt's aggregate `server_seconds` was null rather than a made-up value.

Unresolved limitations, kept separate from agent-authored mistakes:

- Source font encoding retains 57 control characters and some punctuation does
  not match visible glyphs. Overmind now exposes this fact; it must not silently
  invent replacement text. OCR can also misread small labels or screenshot text.
- Multi-column reading order, tables and diagram relationships are not
  reconstructed. Full token retention does not establish a faithful semantic
  representation. Downstream training needs agent review and a defined task.
- Native recovery checks completely missing pages. Partial omissions on pages
  where the primary parser already found some text are not certified by this fix.
- Prefix previews can miss a branch: all three sampled rows were native, so the
  OCR branch was empty until the full run. The full run deliberately covered it;
  a successful preview alone is insufficient evidence of branch coverage.
- Tiny-preview latency, coarse structured-file progress, atomic multi-file
  uploads, query deadlines and original-byte CLI export remain follow-ups from
  the prior granular report. This pass does not claim those capabilities exist.
- Original-byte download was checked by the synthetic extraction integration
  test. The real handbook's source hash and retained identity were verified, but
  a live original-PDF re-download was not tested through the CLI.
- No production soak, multilingual/handwritten OCR, arbitrary damaged PDFs,
  cross-tenant load, or model-training quality certification was performed.

The first interrupted handbook replay had a harness expectation of 23 C0 control
characters; the platform correctly reported 57 characters across Unicode category
Cc. The expectation was corrected and fresh runs passed. Other harness setup
errors (package/parent field assumptions) were corrected in the harness, not
reported as product defects or used to alter the original transformation. The
interrupted artifact `workshop-handbook-final.json` remains alongside verified runs.

## Local readiness and reproduce

The installed CLI and local MCP were checked together. The running server and
interface advertise **5.6.1**, 67 tools, catalog SHA-256
`afd3372b7212b5aaddd837906e47cc8ea5dc05521d3a48c6c729ba65b82a298a`.
The tool schema is unchanged; source facts and upload guidance have been updated.
Repository guidance, SDK dataset skill and sibling product documentation were
updated. No additional service was launched and no provider job was submitted.

Requirements: existing Compose API/extraction workers, Tesseract, restricted
Workshop runtime, editable installed CLI and an authorized localhost account
connection at `~/.config/overmind/connection.toml`. Commands run from the repository
unless noted. The replay refuses hosted endpoints; no keys are printed. Scoped
host permission may be needed for loopback access. Fixtures for the wider matrix
are generated as described in `workshop-granular-performance.md`.

Observed live commands:

```bash
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_handbook_audit.py --pdf '/Users/tyleredwards/Downloads/Lakera_Handbook_AI_Security_for_Product_Teams-.pdf' --rows /private/tmp/lakera-validation.n4LRTI/fixed-source.jsonl --report tests/evidence/workshop-handbook-fixed.json

UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_handbook_replay.py --dataset e459b19b-b26f-493b-828b-b6cb3cc385d7 --run handbook-verified-20261009 --report tests/evidence/workshop-handbook-verified.json --pdf '/Users/tyleredwards/Downloads/Lakera_Handbook_AI_Security_for_Product_Teams-.pdf'

UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_granular_probe.py --directory /private/tmp/workshop-granular.mdoZkg --report tests/evidence/workshop-handbook-regression.json --run handbook-regression-20261009 --cli /Users/tyleredwards/.local/bin/overmind
```

For fresh live repeats, use new run identifiers and report filenames to preserve
the evidence and avoid deliberately reusing saved publication receipts. The
handbook replay requires the recorded source dataset and immutable original recipe
in this local project; it is not a portable blank-database test. The Type3 fixture
test below covers the parser failure without that saved platform state.

```bash
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync pytest tests/test_workshop_documents.py tests/test_workshop_mcp_acceptance.py tests/test_mcp_resources.py tests/test_mcp_catalog.py tests/test_dataset_files.py tests/test_dataset_transfers.py tests/test_dataset_store.py -q --tb=short

UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync pytest tests/test_mcp_resources.py tests/test_mcp_catalog.py -q --tb=short
```

From `overmind/`:

```bash
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync pytest tests/test_resumable_transfer.py tests/test_dataset_cmd.py tests/test_transfer_connection.py -q --tb=short
```

All regression datasets and receipts were retained, including intentional rejection
cases. No cleanup, original-user-data deletion, commit or push was performed.
