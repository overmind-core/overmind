# OCR extraction

PDF and PNG/JPEG/WebP landing runs in the isolated document sandbox. Automatic
language selection is the default. A native coding agent can set
`update_dataset(dataset=..., ocr_languages=["eng", "fra"])` on a draft before
uploading source bytes. REST uses `PATCH /api/datasets/{id}/` with the same field.
An empty list restores automatic selection; null and unsupported codes fail.
Settings affect subsequent imports. Published cells and the settings pinned by an
existing import receipt do not change.

The upload guidance resource lists supported Tesseract codes. Automatic selection
uses offline script detection, initial recognition and language evidence from
confident words. Short or ambiguous text remains explicitly uncertain. It is not
an assurance of OCR quality, and mixed-language pages need review. No provider
calls or downloaded models run during extraction. Language models ship in the
runtime image; their checksums and the actual selected languages are retained.

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
settings and extraction implementation version. Automatic detection evidence and
actual languages are retained in each completed page. Renaming a source does not
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
elapsed seconds, configured worker limit, and per-page language evidence.

## Progress and rollout

Intermediate landing counters are coalesced to at most one write every two seconds
per import. Stage/file boundaries, completed OCR counters and terminal operation
events remain immediate. Stale import attempts cannot overwrite current progress.
Dataset event publishers reuse process-local Redis clients with bounded socket
timeouts; event delivery remains advisory.

Deploy the migration first, then the sandbox image with the language packs, landing
workers, remaining workers and API through the existing staged rollout. Older
sandbox images do not implement checkpoints or language selection, so a mixed
rollout is not sufficient to claim the feature is available. Production rollout
requires the same tested runtime image; this change is validated on staging first.

## Verification

- Document service tests exercise interrupted extraction, exact page reuse, page
  order, changed-language invalidation, project isolation and real French OCR.
- Workshop journey sets languages through MCP, uploads through the CLI and reads
  actual extraction provenance and reuse through MCP.
- Existing document tests retain native text, page regions, orientations, original
  byte downloads and atomic publication on failures.
- Staging measurements use six screenshot pages from the retained CSIRT source.
  Results are recorded separately after the candidate image is deployed.
