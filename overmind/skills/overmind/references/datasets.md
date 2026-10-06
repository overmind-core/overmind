# Datasets — land, shape, use, pull

A dataset is a landed source and a linear chain of Python cells. Every ran
cell is a version: 1.0 is the source, then 1.1, 1.2, and so on. A consumer
freezes the chosen cell and every cell before it. The dataset carries an
intent (`train`, `eval`, `explore`, or `pending`) and an optional capability. Every ran
cell carries measured intent and capability contracts.

Dataset names are not unique. Call `list_datasets` first and pass its dataset
UUID to every later dataset tool.

## Workflow

```
list_datasets
→ inspect_dataset
→ start_dataset | create_dataset_from_traces | CLI upload
→ get_job(kind=dataset_run)
→ inspect_dataset
→ message_dataset_agent when changes are needed
→ get_job(kind=dataset_run)
→ inspect_dataset
→ inspect applied versions and their recorded changes; no per-change approval
→ query_dataset for verification
→ start evaluation, fine-tuning, or optimisation with the chosen dataset/cell
```

Creation can precede the first inspection when no suitable dataset exists.
Creation and agent messages are asynchronous: poll the returned dataset UUID
with `get_job(kind=dataset_run)`, then inspect again.
The latest chat turn includes a persisted `status` and `progress`: stage,
reason, activity timestamp and, for generation, validated rows saved against
the requested target. An unchanged count is not proof that the provider stopped.

Recent turns also identify their `funding_source`, `engine` and `model` when
available. Self-hosted users can select a personal ChatGPT account/model in the
Console under Settings → Data Workshop models. MCP requests use that caller's
saved choice for the Workshop agent and semantic checks, with zero Overmind
credit charges. Limits and disconnected accounts stop the turn. Account consent
and funding changes stay in the Console; never request tokens through MCP or
switch to server-funded models implicitly. Evaluation runs, training and serving
retain their own billing.

## Landing

Use `start_dataset(brief=...)` when the user has an idea or question before data or a capability. Inspect its response and attach the first source with `overmind dataset upload FILE --dataset ID --json`. The same `--dataset ID` command adds more files to an existing workshop as a new import cell; earlier and used versions remain unchanged. Poll the dataset run and inspect the resulting active cell. For new file uploads, `--brief` records the user's request. Use `explore` for data exploration; `pending` means the user has not chosen a purpose.

PDF, DOCX, Markdown, UTF-8 text and PNG/JPEG/WebP images are supported alongside tables. Documents are extracted in the batch worker, with original files, parser metadata and row evidence retained. PDF landing preserves native text and automatically runs local English Tesseract OCR on scanned pages and embedded images. Direct images are capped at 64 megapixels; animated images are rejected. OCR engine/version, page regions, upright image coordinates and recognition confidence are retained; visual layouts and tables are not reconstructed. Inspection returns rows=null until extraction. Document identities keep related rows in the same split.

For REST creation and `create_dataset_from_traces`, omit `capability` to infer
it from the rows, pass its UUID to bind it, or pass `null` to leave the dataset
unbound. The choice applies to both datasets when splitting.

- `create_dataset_from_llm_calls` lands one row per `llm_call` span for a
  capability and a `since` timestamp. The row is that call's request and its
  recorded completion. The application is not run. `split` lands a train
  dataset and an eval dataset by a stable hash of `span_id`. Rows do not carry
  `trace_id`. The landing settles idle and does not start a workshop turn.
- `create_dataset_from_traces` lands traces, one row per trace: identity,
  runtime, `input`, `output`, the wire `messages` and `tools`, and the trace's
  score. Give `trace_ids`, or `filters` and/or `search` (never both). The
  filter keys are listed in the tool schema; an unknown key is refused rather
  than ignored, and a selection that matches no trace is refused before any
  dataset exists. The result carries `traces`, the count that will land. Use
  `query_failures` first when the selection should be a capability's recent
  failures.
- For a local CSV, TSV, JSON, JSONL, NDJSON, or Parquet file, run
  `overmind dataset upload FILE --json --intent train|eval|explore`. Add
  `--project-id` only when needed. The command returns the dataset UUID.
  `--intent` is `train`, `eval`, or `explore` (`ft` is rejected). Omit it and the
  server lands as `pending`. The agent uses an explicit purpose in the brief or pauses to ask; it never infers intent from row shape. `--split PERCENT`
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
dataset and `eval_id` for the eval dataset. Use derive_dataset for a separate chain or create_data_partition for saved role-specific members; neither requires export and reupload.

REST and MCP splits also accept `group_by` (column names), `stratify_by` (one categorical column), and `deduplicate` (default true). Splitting reads the combined source once, removes exact duplicate rows, and keeps matching inputs, traces, conversations, selected groups and synthetic seed descendants together. Grouping can change the requested percentage; inspect `contamination_report` for actual counts and coverage. It explicitly does not claim near-duplicate similarity checking.

To retag an unused dataset, `message_dataset_agent` ("set intent to
train" or `eval`). After a consumer has used a cell, intent is frozen —
derive a separate chain or use a saved partition instead.

Land raw rows. Ask the dataset agent to transform them; do not preprocess rows
locally and upload a second dataset unless you need a second intent.

## Inspect, change, run, verify

`inspect_dataset(dataset=UUID)` returns the intent, capability, active cell,
cell chain, measured contracts, sample, recent agent chat, and next actions.
`cell_offset` and `cell_limit` page the chain; `cell_page.next_cursor` gives the next offset. The active cell identity is always separate. Inspect truncation fields before treating a summary as complete.
`preparation_context` includes downstream SFT/eval requirements and cached whole-frame
source/active profiles grouped by instructions, task labels, input/output shapes
and tool schemas. Missing profiles remain unmeasured; request explore_dataset to compute them. Profiles cover all rows; family lists and examples are bounded and
report truncation. Query each relevant family before generalising. These profiles
are structural evidence, not a semantic quality audit. Replacing existing task
instructions requires source evidence and recorded rationale; capability binding is not permission
to overwrite a mixed-task corpus with one prompt.

When recent_chat contains `awaiting_intent`, ask the user to choose Training, Eval, or Data exploration. Answer with `message_dataset_agent(dataset=UUID, intent_choice="train"|"eval"|"explore", intent_turn_id=TURN_ID)`; omit message. This resumes the original request.

Use `message_dataset_agent(dataset=UUID, message=...)` for name, intent,
capability, and cell changes. Poll `get_job(kind=dataset_run, id=UUID)`, then
inspect again.

The Workshop applies supported changes sequentially and reports the resulting
versions and measured impact. Only missing initial intent requires a user choice.
If older unfinished suggestions are present, continue through
`message_dataset_agent`; the agent reassesses them against current data before
applying changes. Poll and inspect through completion.

Requested generation must produce new examples through `add_synthetic_rows`.
Do not propose script-based replication or identifier remapping to reach a target.

Supported mechanical and semantic repairs run directly as recorded cells. Each step uses the latest successful output, preserves earlier versions, and records evidence, affected rows and coverage changes. Inspect the choices and their impact, not just the final row count. Missing evidence remains unknown; there are no Approve/Deny controls.

Preparation requests mean transform, audit, repair actionable findings and recheck the changed version, not just report failures. A selected capability already defines the target. Map each target field to supplied evidence, a deterministic derivation, a representation change, missing evidence or an evidence-backed agent interpretation; audit all four checks against that same target. Ask the workshop to inspect nested source payloads and recover supplied evidence before declaring it missing, and apply supported improvements even when other findings cannot be resolved. Do not join unrelated worker cases, cross held-out boundaries, fabricate missing evidence or relabel worker answers as orchestrator deliverables. Complete independent supported repairs and report limitations; do not ask follow-up questions or invent unsupported facts. Once supported repairs are exhausted, report remaining affected rows and let the user continue with warnings. Audit-only questions do not authorise transformations.

For source-to-examples preparation, the selected train/eval purpose and original request already authorize grounded construction. The Workshop chooses a count from inspected evidence if none was supplied. Derived examples occupy their own version; raw sources stay in earlier versions. Exact source quotes, document/group lineage and uncovered source counts are recorded. Inspect the final consumer format and task checks; generated answers are not independently verified by attribution alone. Do not treat cleaned pages or trivial pseudo-Q&A as completion of a substantive Q&A task.

For additional synthetic variants, ask `message_dataset_agent` explicitly, for example: "Generate and add 20 examples from the existing rows and the selected capability's behaviour contracts, targeting missing coverage." No capability is required. The request authorizes a saved generation recipe, a small qualified pilot and bounded background batches. Inspect `workflow.generation` for the run ID, revision, generated/remaining rows, source binding and failure receipts. Accepted batches are immutable and are published as one version at completion or an explicit partial outcome; earlier and used versions stay unchanged. Do not repeat a generation request to poll it. Use `get_job` or `inspect_dataset`, and `manage_dataset_workflow` with the current `run_id`, `revision` and `action` (`pause`, `resume`, `publish_partial`) for supported recovery. A pause stops new claims; an owned provider call may still finish. Unknown provider outcomes require receipt recovery, never blind resubmission. `cancel_dataset` handles cancellation. Generated labels are not independently verified. Never split seed-derived rows across training and held-out evaluation.

The workshop remains model-independent. `readiness` distinguishes **format-valid** from **quality-reviewed**; the latter records agent checks with evidence, including failures and unknowns, not a human certification. Capability task context and behaviour contracts guide those checks. Exact model/context compatibility belongs to training preparation: Console jobs run it after launch, and explicit REST/MCP preparation remains available. Follow [finetuning.md](finetuning.md) for exact preprocessing and bring specific affected `source_row` IDs back to the workshop for repairs.

Use `query_dataset(dataset=UUID, cell=CELL_UUID, sql=...)` for bounded,
read-only verification. Pass the verified dataset UUID and cell UUID to
evaluation, fine-tuning, or optimisation tools.

## Intent gates

- `eval` cells are for evaluation and optimisation.
- `train` cells are for fine-tuning.
- `pending` and `explore` are refused by training/evaluation consumers until the user selects that purpose.

Fine-tuning uses a train cell plus a separate eval cell. A cell's measured
contract must fit its intent before a consumer accepts it.

Format compatibility is not a claim of quality. Inspect the workshop's
saved preparation plan and its task-specific checks, with separate technical,
preservation, coverage and semantic assessments. Preparation explores first,
then saves source-bound mappings, assumptions and steps before transforming.
Unspecified intent remains pending; table shape cannot determine intended use.
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
