# Datasets — land, shape, use, pull

A dataset is a landed source and a linear chain of Python cells. Every ran
cell is a version: 1.0 is the source, then 1.1, 1.2, and so on. A consumer
freezes the chosen cell and every cell before it. The dataset carries an
intent (`train`, `eval`, or `pending`) and an optional capability. Every ran
cell carries measured intent and capability contracts.

Dataset names are not unique. Call `list_datasets` first and pass its dataset
UUID to every later dataset tool.

## Workflow

```
list_datasets
→ inspect_dataset
→ create_dataset_from_traces | CLI upload
→ get_job(kind=dataset_run)
→ inspect_dataset
→ message_dataset_agent when changes are needed
→ get_job(kind=dataset_run)
→ inspect_dataset
→ run_dataset(proposal_cell=...) only after user approval when a proposal exists
→ query_dataset for verification
→ start evaluation, fine-tuning, or optimisation with the chosen dataset/cell
```

Creation can precede the first inspection when no suitable dataset exists.
Creation and agent messages are asynchronous: poll the returned dataset UUID
with `get_job(kind=dataset_run)`, then inspect again.
The latest chat turn includes a persisted `status` and `progress`: stage,
reason, activity timestamp and, for generation, validated rows saved against
the requested target. An unchanged count is not proof that the provider stopped.

## Landing

For REST creation and `create_dataset_from_traces`, omit `capability` to infer
it from the rows, pass its UUID to bind it, or pass `null` to leave the dataset
unbound. The choice applies to both datasets when splitting.

- `create_dataset_from_traces` lands traces, one row per trace: identity,
  runtime, `input`, `output`, the wire `messages` and `tools`, and the trace's
  score. Give `trace_ids`, or `filters` and/or `search` (never both). The
  filter keys are listed in the tool schema; an unknown key is refused rather
  than ignored, and a selection that matches no trace is refused before any
  dataset exists. The result carries `traces`, the count that will land. Use
  `query_failures` first when the selection should be a capability's recent
  failures.
- For a local CSV, TSV, JSON, JSONL, NDJSON, or Parquet file, run
  `overmind dataset upload FILE --json --intent train|eval`. Add
  `--project-id` only when needed. The command returns the dataset UUID.
  `--intent` is `train` or `eval` only (`ft` is rejected). Omit it and the
  server lands as `pending`, then proposes from row shape. `--split PERCENT`
  replaces `--intent` and lands two datasets.
- Multiple files can form one source in their selected order. Read
  `overmind://dataset-upload` for the REST upload and inspection steps: stage
  each file, inspect its row count, then create with `source.uploads` containing
  the upload UUIDs. The same source can be split into train and eval datasets.
  Binary transfer stays local; inspect the resulting dataset UUID through MCP.

One dataset has one intent. Need both a train set and an eval set from the
same traces? Pass `split` (`eval_percent`, `position` of `head`, `tail` or
`random`) to `create_dataset_from_traces`: the selection lands as `<name> train` and `<name> eval` with disjoint rows, and the result carries both under
`dataset` and `eval_dataset`. From the same file, run
`overmind dataset upload FILE --json --split PERCENT` (add `--split-position head|tail|random`, default `tail`): the JSON result carries `id` for the train
dataset and `eval_id` for the eval dataset. There is no reingest or
copy-as-intent tool.

REST and MCP splits also accept `group_by` (column names), `stratify_by` (one categorical column), and `deduplicate` (default true). Splitting reads the combined source once, removes exact duplicate rows, and keeps matching inputs, traces, conversations, selected groups and synthetic seed descendants together. Grouping can change the requested percentage; inspect `contamination_report` for actual counts and coverage. It explicitly does not claim near-duplicate similarity checking.

To retag an unused dataset, `message_dataset_agent` ("set intent to
train" or `eval`). After a consumer has used a cell, intent is frozen —
upload a new dataset instead.

Land raw rows. Ask the dataset agent to transform them; do not preprocess rows
locally and upload a second dataset unless you need a second intent.

## Inspect, change, run, verify

`inspect_dataset(dataset=UUID)` returns the intent, capability, active cell,
cell chain, measured contracts, sample, recent agent chat, and next actions.
`preparation_context` includes downstream SFT/eval requirements and whole-frame
source/active profiles grouped by instructions, task labels, input/output shapes
and tool schemas. Counts scan all rows; family lists and examples are bounded and
report truncation. Query each relevant family before generalising. These profiles
are structural evidence, not a semantic quality audit. Replacing existing task
instructions requires a reviewed proposal; capability binding is not permission
to overwrite a mixed-task corpus with one prompt.

Use `message_dataset_agent(dataset=UUID, message=...)` for name, intent,
capability, and cell changes. Poll `get_job(kind=dataset_run, id=UUID)`, then
inspect again.

When inspection returns a proposed cell, explain it and obtain user approval
before calling `run_dataset(dataset=UUID, proposal_cell=CELL_UUID)`. Never
accept a proposal automatically. Approval makes the exact reviewed result active
and resumes the original agent request, including its remaining quality checks.
The Console's Deny action also resumes the agent with the decision, without
applying the proposal. Multiple proposals from one turn must all be decided before
it resumes. Poll and inspect through the continuation, not just the cell run.
`awaiting_approval` is a decision checkpoint, not a generation failure. The
continuation uses the configured workshop engine and may incur model charges.

Requested generation must produce new examples through `add_synthetic_rows`.
Do not propose script-based replication or identifier remapping to reach a target.

Mechanical repairs include complex evidence-preserving restructuring and deterministic derivation from supplied facts and declared rules. They can apply automatically. Initial preparation runs measured cleaning and justified exclusions, with source rows and coverage effects preserved. Judgement calls require a concrete proposal even during initial preparation; follow-up exclusions also require review. The Console offers Approve/Deny with identity-matched input/output examples, before/after counts and categorical coverage. Explain the decision, supporting evidence and tradeoff, not just the new row count. Approval is tied to the exact preview and its source/context; stale proposals must be regenerated.

Preparation requests mean transform, audit, repair actionable findings and recheck the changed version, not just report failures. A selected capability already defines the target. Map each target field to supplied evidence, a deterministic derivation, a representation change, missing evidence or a user decision; audit all four checks against that same target. Ask the workshop to inspect nested source payloads and recover supplied evidence before declaring it missing, and apply supported improvements even when other findings cannot be resolved. Do not join unrelated worker cases, cross held-out boundaries, fabricate missing evidence or relabel worker answers as orchestrator deliverables. Finish independent repairs before proposing a decision; approval cannot make unsupported facts true. Once supported repairs are exhausted, report remaining affected rows and let the user continue with warnings. Audit-only questions do not authorise transformations.

For synthetic generation, ask `message_dataset_agent` explicitly, for example: "Generate and add 20 examples from the existing rows and the selected capability's behaviour contracts, targeting missing coverage." No capability is required. The request authorizes adding validated rows directly, with no draft or Apply step. The agent uses the existing workshop engine, records seed lineage and generation context, checks shape and exact duplicates, and accumulates batches of at most 50 rows in one active generated version. Retries do not duplicate saved batches. If generation stops early, the added rows remain active; inspect the saved count and ask to continue to the same target (or specify the generated cell UUID). A used or changed version cannot be extended. Generated labels are not independently verified. Never treat them as ground truth without quality review or mix seed-derived rows across training and held-out evaluation.

The workshop remains model-independent. `readiness` distinguishes **format-valid** from **quality-reviewed**; the latter records agent checks with evidence, including failures and unknowns, not a human certification. Capability task context and behaviour contracts guide those checks. Exact model/context compatibility belongs to training preparation: Console jobs run it after launch, and explicit REST/MCP preparation remains available. Follow [finetuning.md](finetuning.md) for exact preprocessing and bring specific affected `source_row` IDs back to the workshop for repairs.

Use `query_dataset(dataset=UUID, cell=CELL_UUID, sql=...)` for bounded,
read-only verification. Pass the verified dataset UUID and cell UUID to
evaluation, fine-tuning, or optimisation tools.

## Intent gates

- `eval` cells are for evaluation and optimisation.
- `train` cells are for fine-tuning.
- `pending` is refused by every consumer.

Fine-tuning uses a train cell plus a separate eval cell. A cell's measured
contract must fit its intent before a consumer accepts it.

Format compatibility is not a claim of quality. Inspect the workshop's
`task_alignment`, `input_evidence`, `answer_support` and `output_schema` checks.
Failed, unknown, partial or stale reviews are advisory warnings; they do not block
use or require an approval step. Report the remaining work from `quality_report`,
`readiness.quality_reason` and MCP cell `warnings`. Send requested corrections to
`message_dataset_agent`, not to the training pipeline. Let the user continue.
These are agent-reported semantic audits, not independent guarantees of truth.
Request semantic checks through `message_dataset_agent`; the workshop's internal
`check_semantic_quality` tool evaluates actual rows with Jev and generative fallback.
Name the check and its independent evidence/answer columns. It checks at most 200
rows per call, checkpoints each completed context-sized batch, and resumes the
same version and check contract after interruption; ask it to continue
until coverage is complete. Missing evidence remains unknown, never a pass.
`inspect_dataset` returns counts and a bounded audit summary, not all row-level
decision payloads. Running a check does not approve an edit or certify a label.
The selected capability defines the target boundary and canonical prompt: worker
outputs are not end-to-end outputs. Eval inputs must preserve the evidence and
tool transcripts needed to derive their references. Never repair missing evidence
by inserting the expected answer into the input.

## Local export

Run `overmind dataset export DATASET --json`, where `DATASET` is the UUID
returned by MCP. Add `--cell CELL_UUID` to export a chosen version,
`--format csv` for CSV, or `--output PATH` for an explicit destination. Names
are never resolved locally, and existing files are not overwritten.

The response carries `X-Overmind-Cell`, `X-Overmind-Version`, and
`X-Overmind-Fingerprint`. Preserve the fingerprint when caching a version so
the local copy can be refreshed when it changes. There is no `export_trace`
MCP tool: create a dataset from traces, verify its chosen cell, then export it.
