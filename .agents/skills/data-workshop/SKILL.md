---
name: data-workshop
description: Data Workshop internals — source landing, immutable dataset pipelines and runs, lineage-bound external imports, version inspection and consumer pinning. Use when changing these services, Console screens or MCP contracts.
---

# Data Workshop

The native coding agent owns intelligence. The platform stores evidence and runs
explicit operations. Source landing does not diagnose, start a chat turn, generate
examples or resume a platform agent. Chat, mutable-cell and replay endpoints are removed, together with their Console callers. Historical Cell scripts, reviews and frames remain readable. Old conversations, generation records and provider receipts are archived in DatasetHistory before their runtime tables are dropped.

## Active path

- `services/datasets/dispatch.py`: create, split and attach sources.
- `tasks/datasets.py`: landing and deterministic pipeline execution on the batch
  prefork worker; the reaper fails expired runs without replaying them.
- `services/datasets/workbench.py`: shared REST/MCP pipeline, import and publication
  lifecycle. `api/workbench.py` and `mcp/tools_workbench.py` adapt it.
- `models/dataset_pipeline.py`: immutable DatasetPipeline recipes and durable
  DatasetPipelineRun receipts, source/output/artifact FKs and fingerprints.
- `pipeline_packages.py`, `pipeline_runner.py`, `pipeline_bindings.py`: retained
  script packages, dedicated container execution and explicitly enabled source bindings.
- `services/datasets/versions.py`: version/UUID resolution without importing an agent.
- The Console landing page is a project dataset table. Rows open the existing
  cells on a grid-snapped flow canvas; the folder button reveals compact,
  project-scoped navigation. Chat and funding controls are removed. The Process control
  beside the dataset name exposes current execution facts. Corrections replace the
  displayed process, with no repair-history or restore view. Exact consumer-pinned
  inputs remain immutable internally.
  Connections follow recorded parents. Moving cells never changes dependencies.

New revisions require a retained Python package with native-agent-authored stages.
Package-free historical revisions stay readable, but validation, new execution and
bindings reject them with pipeline_package_required. REST and MCP reject inline
operation definitions; the in-process declarative executor is removed. Project-owned families have immutable numbered
revisions; `derived_from` creates a separate attributed family. Reuse the exact
revision across compatible sources in the same project. Packages execute only in
the dedicated Docker controller: approved immutable image IDs, no network, host
mounts, credentials or socket inside the job, bounded resources and retained logs.
Registration and static validation never execute code. Docker records process exit
status; script-authored status files do not establish completion. Dependencies are
baked into the approved runtime. Setup and package protocol: `docs/workshop-pipelines.md`.
External semantic/provider work remains native-agent-owned and imported with lineage.

A run request key is unique per dataset; revision keys are project-scoped. Identical retries recover the same record;
changed content conflicts. Pipeline creation does not execute. Runs bind immutable
source cell IDs and checksums; all output is measured before atomic publication.
Workbench inspection pages recipes and receipts independently with pipeline_offset,
run_offset, binding_offset and limit (20 by default, at most 100), returning total and next_cursor.
Each successful run publishes all step cells atomically. Preview retains samples
and checks but publishes no cells. Absolute min_rows/max_rows checks are deferred in previews and enforced
on publication; preservation, schemas and lineage are checked in both modes.
Step check_results retain passed/failed/deferred checks with expected/actual counts
on failure. Static validation warns about undeclared output lineage without claiming
runtime failure. Run receipts expose queue age, a polling interval and terminal time.
Runner availability heartbeats continue during validation and publication; they
do not advance measured progress or certify a stage has completed.
Bindings start paused, pin a revision and
parameters, and rebuild full source snapshots, including late trace changes and
removed matches. Checkpoint and cells commit together. Failed attempts pause on
the next inspection; re-enable or revise explicitly to authorize another attempt.
Scheduled/ingestion bindings are checked by the dedicated controller, not an agent;
trace snapshots are capped at 10,000 selected traces and reject overflow.

Trace landing pins `preferred_capability_id` in the source recipe. A source capability
filter takes precedence over the destination capability; otherwise the destination
is preferred when it appears in a trace. Each row uses the unique outer matching
invocation and its descendants for I/O, transcript, usage and score. Repeated outer
invocations fail before publication. Without a match, whole-trace evidence remains;
mixed traces are not attributed to their first capability. Missing wrapper I/O
stays missing instead of borrowing tool arguments. Bindings use the same extraction.
Pipeline receipts expose the current measured stage, pinned source row count and
output row count once written. A running stage is not a percentage or an ETA.
Landing claims also bind attachment-request identities, so a cancelled delivery
cannot consume a later attachment. Paired source claims are atomic; a cancelled
or replaced member releases its unclaimed partner with a retryable source error.
A failed or cancelled run leaves the selected and consumed versions untouched.
Cancellation prevents publication; it does not claim to kill an in-flight calculation.
Worker deadlines and leases bound execution. Unresolved work is never blindly replayed.

For deployment, stop old Workshop agent workers before the API cutover. A 3.0
deployment does not acknowledge cancellation of a model request submitted by an
older worker. Preserve historical receipts and reconcile unknown provider outcomes.

Initial source imports use a durable `DatasetImport` receipt on the dedicated
`landing` queue. Publication and execution have separate fenced ownership; the
control reconciler recovers lost broker acknowledgements and records expired
attempts. `resume_dataset_import` and REST `resume-import` explicitly retry a
blocked import within its attempt budget. Cancellation fences both split members.
Landing retains evidence and returns to idle; it does not start preparation.
Pipeline queue demand contributes to batch capacity through `DatasetPipelineRun`.

## Sources, identities and versions

Datasets retain a written brief and explicit train/eval/explore/pending purpose.
A missing purpose stays pending. Capability inference remains a bounded landing
hint, not a substitute for user intent. A capability is optional for data-first work.
Name, purpose, capability and default-version changes use lifecycle services.

Frames are Parquet under `MEDIA_ROOT/datasets/<dataset>/cells/<cell>.parquet`.
Cell IDs bind versions; displayed version labels are derived by Dataset.versions().
Source identity is `source_row`, not the current display position. The Console
hides internal lineage columns, while export preserves them. SQL query responses
decode declared JSON results, including aliases and JSON expressions, without
reinterpreting ordinary text that happens to look like JSON.

CLI uploads use DatasetTransfer receipts: a project request key binds SHA-256,
byte count and destination recipe. Byte writes and publication serialize on the
transfer, and publication persists the dataset IDs and landing task before
dispatch. Repeating completion recovers an unacknowledged broker handoff; the
landing task's claim prevents duplicate cells. Unscoped staging endpoints cannot
access managed transfer bytes. An explicit json_rows_field selects a top-level
JSON array and is retained in extraction metadata. MCP get_job(dataset_transfer)
reads these facts without dispatching. CLI upload states are publication snapshots
(`state_scope=at_publication`), not
current landing status. `--wait` observes terminal landing; MCP `get_job` reads
current progress. First-source attachment
preserves a draft; later attachment appends a new version. Original document/image
bytes, extraction metadata and row evidence remain retained. Local English Tesseract
OCR covers scanned PDF regions and PNG/JPEG/WebP images. Image orientation and
source coordinates survive extraction. OCR does not reconstruct visual tables.
If the primary PDF parser returns no text on a page, `pdf_text.py` recovers the
encoded layer through PDFium before OCR. This includes Type 3 text layers.
`native_text_recovery` retains affected pages, engine/version, row/character
counts and non-whitespace control-character count; rows carry their own method
and regions. Encoding artifacts remain unchanged and flagged. Recovery does not
certify visual fidelity or partial omissions on otherwise populated pages.
Documents are capped at 100 MiB; PDFs at 2,000 pages. Upload reservations return
the file-type byte limit, and chunk writes enforce it before landing. PDF page
limits are checked before extraction. `overmind://dataset-upload` exposes these
limits as numeric fields. Single and batch uploads persist file/stage progress;
PDF OCR also reports total pages and completed/total OCR pages through
`get_job(kind=dataset_run).progress.landing` and dataset inspection.
`inspect_dataset` pages source metadata independently with `source_offset` and
`source_limit` (default 10, maximum 20). Follow `source_page.next_cursor`; response
budgets may shorten a page. Complete checksums and extraction/OCR metadata survive
pagination, rather than being replaced by truncated summaries.

Row storage preserves heterogeneous scalars and integers outside signed 64-bit
range as JSON. Mixed numeric columns also use JSON when float promotion would
lose integer precision. Shared pandas readers must preserve these values and
nullable integers without another inference pass. This is storage fidelity,
not agent-authored normalization; old immutable cells are not rewritten.
`query_dataset` bounds its JSON output to 32 KiB as well as 100 rows. Oversize
responses return `query_result_too_large` without clipping values; project or
aggregate in SQL, or export the exact cell. A separate configurable execution
deadline defaults to ten seconds and returns `query_timeout`; source cells and
later queries remain usable. The byte/column budget is enforced while reading
results, before building an unbounded Python result.
Results above 200 columns also fail rather than omitting column metadata.
MCP Workshop run summaries omit bulky row examples and preview samples from
routine polling/history. `evidence_resource` points to the unchanged full receipt;
measured progress, checks, lineage identities and timings stay inline.
Preview duration and script runtime phases are measured separately from queue
time. Runtime cleanup confirmation never implies publication success.
Original sources can be downloaded through `overmind dataset export DATASET --source SHA256 --output FILE --json`; the CLI verifies bytes, refuses redirects
and existing destinations, and removes checksum failures.
The installed upload CLI handles one file per transfer. Serial attachments
combine new rows with the preceding cell into a new version; wait for landing
between attachments. Atomic REST batches have no resumable CLI/MCP handoff.
Document-limit reservation errors expose `file_too_large`; no receipt is created.

External results use either at most 2,000 inline rows/4 MiB or a project-scoped
uploaded artifact cell and fingerprint. Every row retains source_row or supplies
all contributing identities in \_overmind_parent_rows. The provenance service
validates every parent and preserves content/group contamination identities.
Scripts must emit the actual source_row or intentional parent declarations;
landed fields are data, not instructions to adopt as transformation lineage.
The original source remains unchanged.
An artifact FK prevents disposal of a referenced uploaded output. External execution
is recorded as external_attributed. Never promote source references or supplied
human-review flags into proof of semantic correctness.

## Checks and consumers

The platform measures format and change impact on the published output. Semantic
quality remains unmeasured unless actual attributable evidence exists; technical
compatibility is not a semantic pass. Existing reviews stay attached to their exact
historical cells. Do not create a universal quality-approval gate.

Preserve full probability targets, option order, weights, repeated observations and
valid blank states. Do not derive argmax labels or invent distributions from means.
Decision/Jev preparation publishes a typed `decision`, retaining tied maxima and
declared supervision metadata. Record evidence-backed target semantics in retained
code and output metadata; historical preparation plans are not a writable API.
Flat-source profiles and impact measurements include weights, semantics and target
provenance. Profiler upgrades invalidate cached measurements for new requests;
replaying a request key still returns its original frozen operation.
Exploration measures sampling feasibility and allocations; derivation copies a
complete source. Sample selection runs externally and returns through the import
contract. Partition plans keep related content, declared groups and synthetic
seeds together. Unknown explicit group fields fail construction; projected-away
groups remain usable through preserved lineage. Partition request keys serialize
at project scope. Generated member names reserve space for the role suffix while
retaining the full plan name.

Group identity aliases are shared by lineage recording and partition validation.
A declared `content` column has a separate group identity from internal input
fingerprints, including after projection removes that column.

`use.check` verifies readability and technical fit. `use.use` / `use.freeze`
pin exact cells in the consumer transaction. Training pins train/validation/eval
cells atomically and does model-specific preprocessing outside Workshop.
Later transformations append; they do not rewrite these frames.
Sources and successful versions remain readable after failures or cancellation.

## Contracts and verification

MCP contract 5.1 adds project revisions, packages, preview, validation, exact-run
cancellation and binding save/state/run tools. The removed agent endpoints remain
absent. `get_job` supports exact dataset_pipeline run IDs; revision/package/binding
resources and paginated pipeline diagnostics expose retained facts. Existing source,
exploration, partition, export and consumer-readiness operations remain.

`docs/workshop-pipelines.md` records runtime setup and repeatable regression commands.
Frontend uses the generated OpenAPI client.
Keep account/project isolation, stable retry keys, changed-key conflicts, source
checksum checks, failed/cancelled publication and artifact lineage covered.

Native agents author explicit `id`/`input` dependencies in steps. Inputs name
`source` or an earlier step; repeated inputs create real forks. The saved entity
and MCP resource return `flow` with nodes, edges, conditions and output. Script
conditions cite `expression` and entrypoint `line`, remain `agent_declared`, and
never execute as expressions. Scripts route rows; every step executes, including
empty branches. `inputs` concatenates distinct earlier branches in declared order;
overlapping source_row identities fail before publication. The script receives a
schema-compatible union; incompatible nonempty data columns/types fail rather than
coercing values. It is a
disjoint union, not a relational key join. Flow exposes terminal and unconsumed
steps so native agents can converge training deliverables rather than leave dead ends.
Publication records actual `input_cells`, `step_id` and condition evidence per cell.
Cell `transformation` metadata binds publication membership and output fingerprint to
the completed run, exact revision, entrypoint and package. It distinguishes isolated
script execution, historical declarative operations, external imports, sources and unrecorded
history; review text alone is not proof of execution. The Script section can inspect
every retained package file, with checksum-verified character pagination shared by
REST and MCP. Keep each step's substantive logic in its visible entrypoint; shared
helpers contain genuinely reusable utilities, not an opaque step dispatcher. Change
recipes by new revisions and executions, never rewriting historical cell scripts.
The Console preserves full-size cell contents on a 20px grid-snapped flow canvas.
Steps cascade down; sibling branches align side by side on the same horizontal
layer. Cells open at scale 1; explicit zoom, Fit view and a toggleable minimap
navigate the graph without changing cell dimensions. Reset and linked-cell focus
restore scale 1; resizing preserves the chosen zoom. The Process control
beside the dataset name shows current execution facts. `preparation.py` builds the
selected successful process from verified run inputs, partition members and
derivations across datasets; unrelated attempts are not canvas nodes.
The left-hand box contains dataset navigation and the minimap toggle, without
cell search or a cell-selector strip.

MCP contract 5.2 adds the `author-dataset-transformation` prompt and exact-revision
lookup using `inspect_dataset_workbench(pipeline=REVISION_UUID)`: selected recipe,
paged family history and scoped runs/bindings. Stale family updates return
`revision_conflict`. Branch correctness requires independent member/coverage checks;
declarations, preview prefixes and successful execution do not establish it.

## Preparation execution and corrections

Upload original source bytes once and pin upstream revisions. Register retained
transformation code before processing the full data. Do not upload prepared
outputs as unrelated sources. For corrections, update the same recipe family
and run the corrected package against the original source; the resulting clean
process replaces the displayed cells without repair-history or restore controls.
Consumer-pinned artifacts remain immutable internally. Recipes belong to the
project; reuse is optional and requires compatible source and target semantics.
`inspect_dataset_workbench.preparation` exposes the same source-to-output graph
as the Console, including recorded train/development/calibration/final links.

Column contracts use `object` for a nested decision, never `json`. Declare the
final step's `consumer` as `decision_train`, `decision_eval`, `chat_train` or
`model_eval` to validate nested targets in both preview and publication. Preserve
evidenced semantics and structured provenance; never remove them to pass checks.
Preview samples a bounded prefix and does not establish coverage of every family.

For row-independent transformations, declare step `batch_rows` (1–100000).
Consecutive batches execute in separate restricted containers and concatenate in
source order. Each expanded JSONL input/output must fit half the package's
`scratch_mb`; each batch has the declared time limit. Global sorting, grouping,
splitting and deduplication must not use batching. Row checks apply to the combined
output, publication remains atomic, and progress records completed batches and
rows. A failed or cancelled batch publishes no partial cells. CLI directory
packages omit Python bytecode caches; other unsupported files remain errors.

Controller recovery uses a dedicated PostgreSQL session lock. Only its owner may execute queued recipes. On startup it fails interrupted recipe runs and removes their owned containers before accepting new work. Recovery never publishes partial outputs or automatically replays scripts. Each batch clears the previous batch's execution identity and exit code before reporting progress.

Large row stores bound staging and DataFrame chunks to 16 MiB of serialized row data (one larger row remains indivisible), with 256-row Arrow decoding batches. Declared transformation batches stream through temporary Parquet files rather than collecting all rows in memory. Runtime JSONL scratch limits remain separate and enforced.

### Script association and cell comparisons

Keep the existing cell and branch UI. `save_dataset_pipeline(dataset=..., package=...)` automatically retains the script and associates the current revision with its preparation. Registration retries return the old receipt without resetting a newer association. Submission also binds reused project recipes to the dataset; package-free execution remains prohibited. `DatasetPipeline.authoring_dataset_id` retains the request identity independently of dataset deletion.

Grid comparisons use the recorded single input, so corrections do not display discarded attempts as their baseline. Multi-input cells do not invent a single comparison parent.
