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

## Landing

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

One dataset has one intent. Need both a train set and an eval set from the
same traces? Pass `split` (`eval_percent`, `position` of `head`, `tail` or
`random`) to `create_dataset_from_traces`: the selection lands as `<name> train` and `<name> eval` with disjoint rows, and the result carries both under
`dataset` and `eval_dataset`. From the same file, run
`overmind dataset upload FILE --json --split PERCENT` (add `--split-position head|tail|random`, default `tail`): the JSON result carries `id` for the train
dataset and `eval_id` for the eval dataset. There is no reingest or
copy-as-intent tool.

To retag an unused dataset, `message_dataset_agent` ("set intent to
train" or `eval`). After a consumer has used a cell, intent is frozen —
upload a new dataset instead.

Land raw rows. Ask the dataset agent to transform them; do not preprocess rows
locally and upload a second dataset unless you need a second intent.

## Inspect, change, run, verify

`inspect_dataset(dataset=UUID)` returns the intent, capability, active cell,
cell chain, measured contracts, sample, recent agent chat, and next actions.

Use `message_dataset_agent(dataset=UUID, message=...)` for name, intent,
capability, and cell changes. Poll `get_job(kind=dataset_run, id=UUID)`, then
inspect again.

When inspection returns a proposed cell, explain it and obtain user approval
before calling `run_dataset(dataset=UUID, proposal_cell=CELL_UUID)`. Never
accept a proposal automatically. Poll and inspect after the run.

Use `query_dataset(dataset=UUID, cell=CELL_UUID, sql=...)` for bounded,
read-only verification. Pass the verified dataset UUID and cell UUID to
evaluation, fine-tuning, or optimisation tools.

## Intent gates

- `eval` cells are for evaluation and optimisation.
- `train` cells are for fine-tuning.
- `pending` is refused by every consumer.

Fine-tuning uses a train cell plus a separate eval cell. A cell's measured
contract must fit its intent before a consumer accepts it.

## Local export

Run `overmind dataset export DATASET --json`, where `DATASET` is the UUID
returned by MCP. Add `--cell CELL_UUID` to export a chosen version,
`--format csv` for CSV, or `--output PATH` for an explicit destination. Names
are never resolved locally, and existing files are not overwritten.

The response carries `X-Overmind-Cell`, `X-Overmind-Version`, and
`X-Overmind-Fingerprint`. Preserve the fingerprint when caching a version so
the local copy can be refreshed when it changes. There is no `export_trace`
MCP tool: create a dataset from traces, verify its chosen cell, then export it.
