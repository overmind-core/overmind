# OCR extraction

PDF and PNG/JPEG/WebP landing runs in the isolated document sandbox. OCR uses
English by default. A native coding agent can set
`update_dataset(dataset=..., ocr_languages=["eng", "fra"])` on a draft before
uploading source bytes. REST uses `PATCH /api/datasets/{id}/` with the same field.
An empty list uses English; null and unsupported codes fail. Settings affect
subsequent imports. Published cells and settings pinned by an existing import
receipt do not change.

Recognition uses the requested languages in one pass, without script guessing or
text-based language detection. Tesseract still handles page orientation. Requested
and actual languages and trained-data checksums remain in extraction provenance.
Non-English and mixed-language documents need explicit supported language choices
and quality review. Missing requested language data fails without publishing a
partial source. No provider calls or downloaded models run during extraction.

## Parallelism and bounds

`SANDBOX_OCR_WORKERS` defaults to two and is capped at four, the container CPU
quota, and the extraction memory reservation. The scheduler reserves 768 MiB for
the parser and budgets 512 MiB per page worker. Each spawned process owns its
PDFium document and raster; native handles are never shared across threads.
At most one outstanding page per worker is submitted. Results are assembled in
page order, independent of completion order. Native text is masked before OCR.

The existing sandbox watchdog enforces combined process-tree RSS and scratch use,
CPU and wall deadlines, UID isolation, and process-group teardown on disconnect.
Raster pixel limits and per-page recognition timeouts remain enforced. Memory
budgeting is conservative admission, not a promise that arbitrary malformed PDFs
will fit; exceeding the measured limit fails without publishing a partial source.

## Durable checkpoints

Completed pages stream as bounded internal checkpoint frames to the application.
They are never published in dataset events. The application validates their shape,
size and checksum before storing them in `DocumentExtractionPage`, scoped to the
project. The sandbox receives only that project's applicable checkpoints with the
source bytes; it has no database credentials or shared media access.

Identity covers the document checksum, requested language policy, Tesseract
version and trained-data hashes, parser/library versions, rendering/masking
settings and extraction implementation version. Actual languages and model identities are retained in each completed page. Renaming a source does not
invalidate OCR; evidence binds the current source filename during final assembly.
Changed source bytes, language preferences or extraction identity cannot reuse
old OCR results. This cache skips recognition, not native PDF parsing.

Each checkpoint is limited to 900 KiB. Checkpoint transfer is bounded to 128 MiB;
additional pages are recomputed. The sandbox includes checkpoint transfer expansion
in memory admission. Interrupted work can reuse completed pages; a page interrupted
before its checkpoint reaches the application is recomputed. No partial dataset is
published. Cancellation still fences publication and never implies that a remote
process has already stopped.

Checkpoints expire after 30 days. The existing import reconciler deletes up to
1,000 expired pages per pass. Deleting a project cascades to its checkpoints.
Source metadata exposes `pages_reused`, `pages_processed`, `recipe_sha256`, OCR
elapsed seconds, configured worker limit, and actual languages.

## Progress and rollout

Language policy version 4 invalidates checkpoints produced by automatic detection.
Reload the sandbox service before resuming a blocked import to use the new policy.
The retained bytes and frozen import language settings remain unchanged.

Intermediate landing counters are coalesced to at most one write every two seconds
per import. Stage/file boundaries, completed OCR counters and terminal operation
events remain immediate. Stale import attempts cannot overwrite current progress.
Dataset event publishers reuse process-local Redis clients with bounded socket
timeouts; event delivery remains advisory. Checkpoint frames require explicit
client opt-in; older landing clients keep English OCR and receive only ordinary
progress. The language column retains its database default so older API instances
can create datasets while the migration and service rollout are in progress.

Deploy the migration first, then the sandbox image with the language packs, landing
workers, remaining workers and API through the existing staged rollout. Older
sandbox images do not implement checkpoints or language selection, so a mixed
rollout is not sufficient to claim the feature is available. Production rollout
requires validation of the same runtime image. The full candidate image and every
staging service passed rollout at `35164343`. See the
[staging benchmark](ocr-staging-benchmark.md) for paired timings, output checks and
limits; these measurements do not establish production throughput.

## Verification

The staging measurements below describe PR #218 before automatic language
detection was removed; they are historical evidence, not current-policy results.

- Document service tests exercise interrupted extraction, exact page reuse, page
  order, changed-language invalidation, project isolation and real French OCR.
- Workshop journey sets languages through MCP, uploads through the CLI and reads
  actual extraction provenance and reuse through MCP.
- Existing document tests retain native text, page regions, orientations, original
  byte downloads and atomic publication on failures.
- Paired staging runs cover three 24-page PDFs and one 96-page PDF from the
  retained CSIRT source, with explicit English, automatic language selection and
  complete cached reuse. All 68 document/transfer regressions passed, including
  the retained unsupported-script screenshot; the strengthened MCP/CLI/OCR
  journey also passed.
- Cached row values and evidence match exactly. Cached imports currently expose
  `page` before `text`, so their column order and whole-file fingerprints differ
  from cold imports. The benchmark records this limitation explicitly.
