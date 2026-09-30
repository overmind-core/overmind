---
name: overmind-datasets
description: Build, inspect, prepare, generate and export Overmind datasets in Data Workshop. Use for trace-to-data workflows, dataset cells, semantic repair proposals and train/eval preparation; model-specific tokenization belongs to Training.
---

# Overmind Datasets

Start with `list_projects` and choose the intended accessible project. For an
account connection, pass its `project_id` on every project tool and resource
URI query; follow returned links. Project API keys retain their narrower access.

Prepare a usable dataset version with evidence for its intended consumer. Use
the chosen MCP project from `overmind://project/current?project_id=ID`. Resolve existing
datasets with `list_datasets`; names are not unique, so continue with returned UUIDs.

## Land and inspect

Use `create_dataset_from_traces` for a requested trace selection, with either
explicit IDs or the supported filters/search. For local files, use the native
`upload-dataset-file` prompt or read `overmind://dataset-upload` and follow its
CLI handoff. MCP does not transport file bytes or require credentials in chat.

Poll the returned dataset work with `get_job(kind=dataset_run, id=...)`, then
`inspect_dataset`. Inspect the source and active task-family profiles, downstream
requirements, cell chain, measured contracts, quality coverage and recent chat.
Use `query_dataset` for bounded read-only checks on table `t`; one sample does
not establish the meaning of every task family.

A dataset has one intent and a chain of cells. A ran cell is a readable version;
consumers pin the selected cell. Keep train and held-out evaluation identities
separate, including duplicate groups and synthetic seed lineage.

## Prepare through the workshop

Use `message_dataset_agent` for requested name, intent, capability or cell
changes. Specify the target task, supplied evidence, required output shape and
consumer. Preparation means supported transformations, audit, repairs and a
recheck of the changed version; an audit-only request does not authorize edits.

Inspect `task_alignment`, `input_evidence`, `answer_support` and `output_schema`
findings and their measured coverage. Missing evidence remains unknown. Do not
insert reference answers into inputs or relabel worker outputs as end-to-end
capability outputs. Keep the workshop model-independent.

Semantic replacements require a concrete reviewed proposal. Explain its actual
row examples, counts and coverage effects; call `run_dataset(proposal_cell=...)`
only for the approved proposal. Poll through the resumed agent turn and inspect
again. `awaiting_approval` is a decision checkpoint, not a failed generation.

For requested synthetic data, explicitly ask the dataset agent to generate new
examples and record their lineage. Do not duplicate rows to hit a target. If a
run stops early, report saved rows and remaining work; generated labels are not
independently verified ground truth.

## Hand off the version

Verify the chosen cell with `query_dataset`. Report dataset/cell IDs, intent,
row count, coverage and residual findings. Quality findings are advisory; only
unreadable or technically incompatible data blocks the consumer. Do not invent
a quality-approval gate or silently change a selected version.

For download, prefer `export-dataset` or read `overmind://dataset-export` for
the local CLI action. Open `datasets/{id}` under the project's `console_url`
base, preserving `projectId`, when the user wants the notebook.
