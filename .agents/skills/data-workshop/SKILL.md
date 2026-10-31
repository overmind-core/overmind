---
name: data-workshop
description: Data Workshop internals — source landing and OCR, immutable dataset pipelines and runs, lineage-bound external imports, version inspection, consumer pinning and the Workshop canvas. Use when changing these services, their Console screens or their MCP contracts.
---

# Data Workshop

The native coding agent owns interpretation, code and semantic work. The platform stores evidence and runs explicit, deterministic operations. Landing never diagnoses, starts a chat turn, generates examples or starts preparation. There is no platform chat, mutable cell or replay endpoint; old conversations and provider receipts are archived in `DatasetHistory`, and historical cell scripts, reviews and frames stay readable.

## Preparation lifecycle

`DatasetPreparation` (`services/datasets/preparation_workflow.py`) groups a coding agent's preparation work under one evolving notification. MCP `start_dataset_preparation` and `update_dataset_preparation` open and close it. The Console's End preparation (`POST /api/datasets/{id}/end-preparation/`) and the dataset reaper (24 h without activity) cancel it. An open preparation labels the dataset; it never locks editing.

## Map

| Path                                                                                                               | Owns                                                                                                               |
| ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| `services/datasets/dispatch.py`                                                                                    | Create, split and attach sources                                                                                   |
| `services/datasets/land.py`, `imports.py`, `admission.py`                                                          | Landing, `DatasetImport` receipts, `DatasetLandingWork` admission                                                  |
| `services/datasets/transfers.py`                                                                                   | CLI `DatasetTransfer` receipts                                                                                     |
| `services/datasets/workbench.py`                                                                                   | Shared REST/MCP pipeline, import and publication lifecycle (`api/workbench.py`, `mcp/tools_workbench.py` adapt it) |
| `models/dataset_pipeline.py`                                                                                       | Immutable `DatasetPipeline` revisions and `DatasetPipelineRun` receipts                                            |
| `pipeline_packages.py`, `pipeline_runner.py`, `pipeline_execution.py`, `pipeline_graph.py`, `pipeline_bindings.py` | Retained packages, sandbox execution, row batching, the `flow` graph, explicitly enabled bindings                  |
| `services/datasets/versions.py`, `use.py`                                                                          | Version resolution; consumer checks and pinning                                                                    |
| `services/datasets/preparation.py`                                                                                 | The source-to-output graph shared by the Console and MCP                                                           |
| `sandbox/documents/`                                                                                               | Document parsing and OCR in the isolated `sandbox` service                                                         |
| `tasks/datasets.py`                                                                                                | Landing and pipeline execution; the reaper fails expired runs without replay                                       |

## Sources and landing

- A dataset may start from a written `brief` with no source or capability. Purpose is explicit (`train`, `eval`, `explore`) and otherwise stays pending; when no capability is given, REST and MCP landing assign the top-ranked capability if its score is above zero; CLI transfers never infer one.
- The first source attaches to the draft; later uploads append versions. Original bytes, extraction metadata and row evidence are retained.
- CLI uploads use `DatasetTransfer`: a project request key binds SHA-256, byte count and destination. Up to 100 ordered files and 2 GiB per transfer; file order is part of identity; one batch becomes one source cell or one version. Repeat the identical command after an interruption. `get_job(kind=dataset_transfer)` reads byte and publication state; `dataset_run` reports landing. An explicit `--json-rows-field` selects a top-level JSON row array and is recorded.
- Local upload, export and checkpoint download authenticate separately from MCP. Without an explicit or repository key they use the address-bound account connection at `$XDG_CONFIG_HOME/overmind/connection.toml` (mode `0600`); repository keys are endpoint-bound too. `overmind connection check --project-id PROJECT --json` verifies MCP and transfer access from the invoking environment; MCP connectivity alone is not readiness. Verify the installed CLI from the workspace and from a repository-free directory.
- Confirmed permission denial uses the host's scoped approval. Missing credentials and transfer errors never authorize a browser fallback or an automatic sandbox-policy change.
- Landing admission is one import per project, least-recent-project first, up to the observed landing capacity (backend-architecture `references/celery.md`). A lost started import needs explicit `resume_dataset_import`.
- Trace sources pin `preferred_capability_id`. A unique matching invocation scopes I/O, transcript, usage and score; repeated invocations fail before publication; unmatched traces keep whole-trace evidence. Missing wrapper I/O stays missing and never borrows tool arguments.

## Documents and OCR

- Parsing runs in the `sandbox` service without app credentials, media mounts or outbound network. Landing streams bytes and progress; missing parser configuration fails closed; there is no in-process fallback.
- PDF pages the primary parser misses are recovered through PDFium before OCR. `native_text_recovery` records engine, pages and counts; encoding artifacts are flagged, never corrected.
- OCR is local Tesseract, English by default, with explicit languages through `update_dataset(ocr_languages=[...])` on a draft before upload. Completed pages are cached per project for 30 days, keyed by document, language policy, engine and parser settings.
- Limits (100 MiB per document, 2,000 PDF pages) are discoverable at `overmind://dataset-upload` and enforced before extraction.

## Pipelines

- New revisions need a retained Python package with agent-authored stages; inline operations are rejected (`pipeline_package_required`). Package-free historical revisions are read-only.
- Revisions are immutable and numbered per project family; `derived_from` starts a separate attributed family. Revision keys are project-scoped; run keys are per dataset.
- Packages run only in the isolated sandbox through interactive workers: separate UIDs, no network, credentials, media mounts or Docker socket, measured memory admission. Registration and validation never execute code. Process exit status, not a script-written file, establishes completion.
- Steps declare `id` and `input`; `inputs` concatenates disjoint earlier branches in order and rejects overlapping `source_row` identities. It is not a relational join. `flow` (`pipeline_graph.py`) returns `nodes`, `edges` (each with its `condition`), `terminal_steps` and `unconsumed_steps`. Script conditions cite `expression` and `line` and never execute as expressions.
- A successful run publishes all step cells atomically. Preview publishes nothing. `min_rows`/`max_rows` apply at publication and show as deferred in previews; row preservation, schema and lineage checks apply in both. Run receipts expose queue age, a polling interval, terminal time and structured failed checks. Runner heartbeats continue through validation and publication without advancing measured progress.
- Cells carry receipt-backed `transformation` attribution (run, revision, package checksum, entrypoint). External imports and unrecorded history never claim platform execution. Every retained package file is inspectable with checksum-verified pagination.
- Recovery is lease expiry: the `reap_stuck_runs` beat task calls `workbench.expire_runs`, which fails runs past their lease without publishing partial output or replaying scripts.
- Failure or cancellation leaves source and consumer versions unchanged. Cancellation prevents publication; it does not claim to stop a running calculation.
- `batch_rows` (1–100,000) splits a row-independent step into separate sandbox runs (`pipeline_execution.py`); global sorting, grouping, splitting and deduplication must not batch.
- Bindings start paused, pin a revision, and rebuild full source snapshots; checkpoint and cells commit together.
- Keep each step's logic in its visible entrypoint; helpers hold reusable utilities, not an opaque dispatcher. Change a recipe through a new revision and run; never rewrite historical scripts.
- `save_dataset_pipeline(dataset=..., package=...)` associates the script with the dataset's preparation; retrying an older request never resets a newer association.
- Corrections update the same family and run against the original source; the clean process replaces the displayed cells. There is no repair history or restore view.

## External imports

External results arrive inline (at most 2,000 rows or 4 MiB) or as a project-scoped artifact cell. Every row keeps `source_row` or declares all parents in `_overmind_parent_rows`; publication validates every parent (`review.py`, `workbench.py`). External runs are recorded as `external_attributed`, and their cells' `transformation.execution` is `external_import`. Never promote source references or supplied review flags into proof of semantic correctness.

## Storage and queries

- Frames are Parquet at `MEDIA_ROOT/datasets/<dataset>/cells/<cell>.parquet`. Cell IDs bind versions; version labels are derived. Source identity is `source_row`, not display position.
- A column becomes JSON when it mixes kinds, holds an integer outside signed 64-bit range, or mixes floats with an integer a float cannot represent exactly (`store.py`). Shared pandas readers must not coerce these values again.
- `query_dataset` caps output at 100 rows and 32 KiB and returns `query_result_too_large` without clipping; a configurable ten-second deadline returns `query_timeout`. Redis admission bounds MCP SQL globally and per project; busy admission returns a retryable error with the cell and retry delay.
- `overmind dataset export --source SHA256` downloads retained originals with byte verification.

## Checks and consumers

- The platform measures format and change impact. Semantic quality stays unmeasured without attributable evidence; technical compatibility is not semantic correctness. Do not add a universal quality gate.
- Preserve probability targets, option order, weights, duplicates, blank states and declared groups. Never argmax soft targets or invent distributions from means. Decision recipes publish a typed `decision` with tied maxima and supervision metadata.
- The native agent declares target meaning per family from evidence in retained code and output metadata; unknown meaning stays unknown. Historical preparation plans are not a writable API. Profiles and impact checks keep declared supervision metadata, so a changed weight or semantic declaration shows in review.
- Publication measures analytics on the full rows, bound to the exact fingerprint and rule version. They are advisory; `explore_dataset` backfills older cells, and reads never scan.
- Raw tables have no inferred feature or target mapping; column names such as `label` or `score` never select columns.
- Column contracts use `object` for a nested decision. A final step's `consumer` (`decision_train`, `decision_eval`, `chat_train`, `model_eval`) validates targets in preview and publication.
- `use.check` verifies technical fit; `use.use` and `use.freeze` pin exact cells inside the consumer transaction.
- The native agent inspects the named capability and verifies published output before it calls preparation complete. Format readiness does not establish task suitability.

## Console canvas

The landing page is a project dataset table; a row opens its cells on a grid-snapped flow canvas. Cells stay full-size at scale 1; zoom and Fit view change only the camera. Steps run downward and sibling branches share a horizontal layer. Positions persist in the browser and never change execution. The folder button opens project-scoped dataset navigation. There is no chat, funding control, cell search or cell-selector strip.

The dataset inventory exposes current work from retained pipeline and transfer receipts.
Uploads attached to a dataset report measured bytes; transformations and previews stay
indeterminate unless measured progress exists. The table retains the published version
and row count during work, and shows no row count before a version is published.

The inventory Role column and filter derive roles from completed partition memberships
and recorded training/evaluation consumers, not intent or names. Source datasets
include every role produced by their completed partition plans. Multiple roles remain
visible. Raw-only published datasets without a consumer role show Source; other
unassigned datasets show an em dash. MCP retains its existing partition and consumer
facts; this Console grouping adds no MCP operation or intent mutation.

The Console inventory groups datasets by `workspace_id`, resolved from completed
partition and exploration lineage. One source workspace row combines output roles
and active work; output datasets retain their identities and detail URLs. Counts
and filters operate on grouped workspace rows. MCP keeps individual output records
for exact consumer selection; workspace grouping is Console presentation.
