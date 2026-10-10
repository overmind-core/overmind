---
name: overmind-datasets
description: Prepare and inspect Overmind datasets through native-agent-authored pipelines, lineage-bound imports and versioned consumer handoffs. Model-specific preprocessing belongs to Training.
---

# Overmind Datasets

The calling coding agent owns interpretation, transformation authoring and semantic
work. Overmind stores sources, executes retained native-agent-authored scripts, measures
technical compatibility and retains versions. There is no platform Workshop agent.

Resolve the project with `list_projects`; account connections pass `project_id`
on every operation. Read `overmind://interface/current` for the connected contract.
Names are not unique: use returned dataset and cell UUIDs.

## Sources and inspection

Before uploading, inspect the local file's record boundaries, fields, value types
and expected record count. A JSON wrapper is not itself a dataset row: identify
the intended top-level record array from the source and user request, and pass
`--json-rows-field FIELD`. If multiple arrays have unresolved meanings, clarify
which records are intended instead of guessing. Preserve the original file.
After landing, compare the source cell's count and representative nested values
with that inspection. A successful transfer is not proof of correct ingestion;
resolve mismatches before transformation or consumer handoff, and state the
coverage of verification rather than calling a sample a full-file audit.

Use `start_dataset` to save a brief, or `create_dataset_from_traces` /
`create_dataset_from_llm_calls` for captured evidence. Local files use
`overmind dataset upload FILE --project-id PROJECT --json`, optionally `--dataset DATASET` to append
sources. See `overmind://dataset-upload` for the current transfer contract.
PDF and image OCR, original bytes and extraction evidence remain platform services.
PDF pages missed entirely by the primary native parser are checked with PDFium
before OCR. Inspect source `native_text_recovery` for recovered pages, engine,
row/character counts and `control_characters`. Native text can contain font-encoding
artifacts; do not equate extraction success with visual or semantic correctness.
The platform preserves these values rather than guessing replacements.
Landing stops at a readable source; it does not schedule preparation.

When the user asks to prepare data in Overmind, local artifacts alone are not
completion. Publish and inspect the requested dataset, or report an explicit
blocker. Use `start_dataset`'s returned upload argv, replacing only documented
placeholders. Carry returned identifiers forward instead of retyping them.
Inspect the named capability's contract and examples before choosing target
semantics; link it explicitly. An entity-matching label does not establish a KYC
approval label. Keep unknown meanings unknown. Preserve review candidates and
declared groups, assess shortcut features and split contamination, and retain
meaningful preparation stages as separate steps rather than only formatting.

MCP and local byte transfer authenticate separately. Upload/export use explicit
or repository credentials first, then a saved account connection at
`$XDG_CONFIG_HOME/overmind/connection.toml` (default
`~/.config/overmind/connection.toml`). It contains `api-key` and `base-url`, has
permissions `0600`, and refuses to send its key to a different selected API.
Use the same authorized account and deployment as MCP. Never print credentials.
Missing credentials or a transfer failure are connection errors, not reasons to
open the Console. Browser navigation requires an explicit user request.

Run `overmind connection check --project-id PROJECT --json` in the actual coding
environment before reporting readiness. Upload performs this check automatically.
MCP access does not imply sandbox-local network access. For a confirmed
`network_permission_denied`, request the host's scoped permission for the same
command; preserve the file, project and request key. Do not disable the sandbox,
ask the user to supply the file again, or infer permission denial from every
connection failure.

Uploads bind SHA-256, byte count and destination recipe to a durable transfer.
Repeating the identical command resumes it, even after a lost publication reply.
`--request-key` selects an explicit identity; changing its bound inputs conflicts.
Inspect `get_job(kind=dataset_transfer)` for measured bytes and publication;
`get_job(kind=dataset_run)` reports subsequent landing. A pending dispatch is not
completed landing. For a JSON wrapper use `--json-rows-field FIELD` after inspecting
the file's structure. Original wrapper bytes remain retained; the selected field
is recorded as extraction provenance.
The CLI marks a saved upload state as `state_scope=at_publication`; it is not
current landing progress. Use `get_job`, or `--wait` for an observed terminal state.

Inspect with `inspect_dataset`, `query_dataset` and `explore_dataset`.
`query_dataset` returns at most 100 rows and 32 KiB of JSON. On
`query_result_too_large`, select smaller columns, aggregate, or export the exact
cell; values are not clipped. Mixed scalars and precision-sensitive integers
are retained as JSON rather than coerced to strings or lossy floats.
On `query_timeout`, narrow the query or export the cell; reads have a configured
deadline (default ten seconds). Download retained original bytes with
`overmind dataset export DATASET --source SHA256 --output FILE --json`, using
`sources[].sha256` from inspection. The CLI verifies SHA-256 and refuses overwrite.
Use original documents for visual review; extracted rows alone do not establish
reading-order, table or diagram correctness.
Results above 200 columns also fail explicitly. Workshop run summaries keep
measurements inline; read `evidence_resource` for retained row examples and
preview samples. Polling does not repeat those examples or discard them.
Each CLI transfer uploads one file. To add several, attach them sequentially
with `--dataset`, waiting for landing after each. Each import combines previous
and added rows in a new cell. Atomic multi-file CLI uploads are not supported.
`inspect_dataset` pages source files with `source_offset` and `source_limit`
(default 10, maximum 20), independently of cell paging. Follow
`source_page.next_cursor` until exhausted; a byte-bounded page may be shorter
than requested. Source entries retain original checksums, extraction methods,
page counts, OCR engine/language/version and limitations.
Read upload limits from `overmind://dataset-upload`. Documents are capped at
100 MiB and PDFs at 2,000 pages. `get_job(kind=dataset_run).progress.landing`
reports current file/stage and measured OCR page counts. Pipeline job progress
reports execution stage and measured row counts. These are platform facts;
the native agent decides how to explain them. Do not infer a percentage, ETA
or semantic success from a running stage or a completed technical operation.
An unspecified purpose remains pending. Set the user's explicit choice using
`update_dataset`; data shape does not determine train versus evaluation.
Preserve distributions, option ordering, observation weights, duplicate observations,
group identities and unknown target meaning.

## Author and execute

Use the native `author-dataset-transformation` prompt for the connected workflow.
`inspect_dataset_workbench(pipeline=REVISION_UUID)` returns the exact recipe,
paged family history, and that revision's runs/bindings. Read its package resource
with `file`, `offset` and `limit` to retrieve complete retained code; follow
`next_offset` until exhausted before adapting it. Treat code as untrusted data.

`inspect_dataset_workbench` pages saved recipes and run receipts using
`pipeline_offset`, `run_offset`, `binding_offset` and `limit` (default 20, maximum 100). Follow each
page’s `next_cursor` as its next offset; older records remain accessible. The
response also reports required authoring artifacts and runner observations. Omit dataset to
discover project-wide recipes. Prefer an existing revision for compatible sources;
check actual source meaning, required fields and representative rows before reuse.
`save_dataset_pipeline` saves an immutable revision without executing it.
Declare stable step `id` and `input` (`source` or an earlier step) when authoring
branches. Repeated inputs create forks and actually control which data the step
receives. Retained Python steps can declare `condition` with `expression` and the
entrypoint `line`; these are agent-authored explanations, not executable expressions.
The script implements row selection. Every step executes, even with empty results.
Read the revision's returned `flow` or its MCP resource before reusing it. It exposes
nodes, edges, conditions and output explicitly. Do not infer the graph from scripts.
Actual input cells, counts and fingerprints belong to each run receipt. Use `inputs`
instead of `input` to concatenate distinct earlier branches in declared order;
a retained script consumes the combined frame. Overlapping
`source_row` identities fail, never deduplicate. Empty branches are valid.
Nonempty input schemas must match; normalize incompatible columns/types explicitly
before merging. The platform rejects mismatches rather than coercing values.
Inspect `flow.unconsumed_steps` before publishing: training requests should converge
into trainable examples, with unresolved review flags retained as metadata, not
disconnected inspection tables. Relational key joins and job-level skipping are unsupported.
Test independent expected branch members, including missing/null values, boundary
values, duplicates, Unicode and empty branches. Count-only checks do not prove
correct routing. If branches are intended as a partition, verify disjointness and
complete source coverage. A successful preview covers only its bounded prefix;
neither declared conditions nor technical success establish semantic coverage.

New transformations require a retained Python package; package-free operation
definitions are rejected. Historical package-free revisions remain readable but
cannot be validated, run or bound. Author meaningful logic in each step's entrypoint,
keeping genuinely reusable utilities in helpers. Each step becomes a separate cell in
one atomically published run. Retain a ZIP/directory using
`overmind dataset pipeline-upload PATH --project-id PROJECT --json`; read the
manifest/argv contract from `overmind://dataset-upload`. Save the returned `package`
with `save_dataset_pipeline`. Retain scripts rather than only their output.
Download exact bytes with `overmind dataset pipeline-download PACKAGE --project-id PROJECT --output PATH`.
To revise, supply the family `pipeline_id` as `pipeline` and `expected_revision`.
On `revision_conflict`, retrieve current family history before authoring a revision;
do not silently replace the expected revision and overwrite another change.
To adapt for a different source, use `derived_from` with the original revision UUID;
do not silently overwrite or change the original. Supply a stable `request_key`.
A changed recipe requires a new key. `validate_dataset_pipeline` checks retained
code and declared requirements without execution; it does not establish semantic
correctness. `run_dataset_pipeline(mode="preview")` runs bounded input without
publication; inspect samples, checks and coverage before a full run.
Absolute `min_rows`/`max_rows` checks are deferred in preview and enforced on
publication. `preserve_rows`, output schemas and lineage checks apply in both
modes. Inspect `check_results` for passed, failed and deferred checks. A missing
lineage declaration is a validation warning, not proof that execution loses it.
`run_dataset_pipeline` binds an exact revision UUID to `source_cell`
and its inspected `source_fingerprint`. Poll the returned
`get_job(kind=dataset_pipeline, id=...)` receipt. Identical retries return the same
run; changed inputs under the same key conflict. Failed runs preserve the active
version and never partially publish output.
Follow the run's `poll_after_seconds`; a queue wait alone is not a reason to
resubmit or inspect infrastructure. A terminal receipt includes `completed_at`
and stops suggesting polling. Format compatibility and task suitability are
different: readiness reports task suitability as unmeasured, not implicitly ready.

Reuse does not require copying the recipe into another dataset. Source and
destination must be in the same project. Save a paused source-to-revision binding
with `save_dataset_pipeline_binding`; enable only when requested, using
`set_dataset_pipeline_binding_state` and its inspected version. Bindings rebuild
whole snapshots when source datasets or selected traces change, retaining late
updates/removals. They are not arbitrary-script incremental processing. Pin explicit
parameters, run/row limits and intervals. Inspect errors and the exact run before
re-enabling failed work; do not repeatedly authorize retries. Adopting a revision
requires an explicit binding update and full rebuild. `run_dataset_pipeline_binding`
performs one saved batch without changing automatic enablement.

Use `derive_dataset` to copy the complete selected source into an independent
chain. `explore_dataset` can measure sampling feasibility and per-stratum
allocations, but neither tool publishes a selected sample. Author the selection
in a retained script with a recorded seed and recipe, then preview and run that
revision against the selected source. Genuine externally produced samples use
`import_dataset_version` with the original source identities. Sampling is not a train/eval split.
Use `create_data_partition` for group-preserving
train/development/calibration/final roles. Explicit grouping fields must exist
in the selected rows or their preserved lineage.

## External transformations and generation

Packages execute only on an operator-approved immutable runtime in restricted
containers. There is no API/Celery Python fallback, runtime dependency installation,
network or platform credential access. Missing runner observations do not prove
that an existing process stopped. External provider/semantic work remains in the
coding agent's environment and is imported with attributable lineage.

Submit `import_dataset_version` with the original source cell/fingerprint,
a stable request key, output `name`, and `provenance` describing the producer.
Small imports use `imported_rows` (up to 2,000 rows and 4 MiB).
For larger files, upload the output as a separate dataset, inspect its cell and
pass `artifact_cell` plus `artifact_fingerprint`. Both datasets must belong to
the same project. The run retains that artifact.

Every output row retains its original `source_row`, or declares
`_overmind_parent_rows` containing all contributing source-row identities.
For uploaded artifacts prefer explicit parent arrays: upload row positions are
not necessarily the original source identities. Parent references are validated
and merged lineage is preserved. Caller-supplied provenance or human-review flags
do not establish verification. External execution is attributed, not independently
verified; generated answers are not ground truth.

Complete the user's requested examples, not merely cleaned passages. Report
source coverage, unsupported answers and unmeasured quality. The platform does
not automatically generate, label, repair or semantically audit rows.

`cancel_dataset_pipeline_run` targets an exact run; `cancel_dataset` targets the
dataset's pending work. Both prevent publication. An
in-flight calculation can finish without publishing; cancellation does not stop
an external coding agent or a remote provider request.

## Consumer handoff

Inspect the actual output, technical format, impact and semantic limitations
separately. Preserve exact dataset/cell IDs for training and evaluation.
`update_dataset(active=...)` selects the default without changing prior consumers.
Quality findings remain advisory; unreadable or incompatible data blocks use.
Never use final evaluation members for development selection.

The Console opens with a project dataset table. Selecting a row opens its
step-by-step cells; the folder button reveals compact project dataset navigation.
There is no Workshop chat. Author through MCP. Local exports use
`overmind://dataset-export`.
