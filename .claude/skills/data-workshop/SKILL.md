---
name: data-workshop
description: Data Workshop internals — the Dataset and Cell model, derived versions and the single use gate, the Parquet frame store and source_row identity, the sandboxed cell runner and library tiers, the dataset's agent, its two engines and thirteen tools, landing and diagnosis, the export contract local loops read. Use when changing datasets, cells, versions, the notebook runner or agent, landing, alignment, diff, or dataset export.
---

# Data Workshop

A dataset is a source and a chain of cells. Every frame is Parquet; versions are derived, never stored; one gate freezes what consumers use; the chain is edited only through the dataset's own chat.

## Model

- `Dataset`: name, source kind/spec, `capability` and `intent` (`train` | `eval` | `pending`, both proposed at landing by `alignment.rank` / `contract.propose_intent` and frozen by the first use), `capability_rank`, `active` (FK `Cell`, null = the last cell that ran), `state` (`landing` | `diagnosing` | `idle` | `running` | `error`), `chat` (the one conversation, a JSON list of turns), `agent_id` (the Cursor engine's session), `agent_messages` (the native engine's OpenAI message list, trimmed to a bounded tail) and `agent_turn_key` (the last Celery task id to run a turn, so an acks_late redelivery is a no-op).
- `Cell`: one transformation and the frame it left — `position` (0 = the source), `title`, `script` (a Python body over `df`), `note`, `state` (`proposed` | `queued` | `running` | `ok` | `failed`), `rows`, `columns`, `fingerprint`, `input_fingerprint`, `intent_report`, `capability_report`, `stats`, `used_at`.
- Frames live only at `MEDIA_ROOT/datasets/<id>/cells/<cell>.parquet`. Every frame carries `source_row`: row identity, not data. The runner rebuilds it from the index when a script drops the column; `services/datasets/diff.py` joins on it for the row-level and value-level diff the grid and the agent show. The grid, the column count and the agent's tool results hide it; the export keeps it.
- Versions (`Dataset.versions()`): the source is 1.0, each cell after it a minor, a used cell the next major.
- `use.use(dataset, intent, cell=)` is the one gate every consumer (eval runs, finetuning jobs, optimizer experiments) passes. It checks the dataset's intent and both contracts on the version, sets `used_at`, and the FK PROTECTs the cell. A used cell and every cell before it are frozen (`Cell.frozen`), the intent and capability with them.
- Dataset names are not unique; every tool result carries the id and the resolver prefers ids. A training job whose eval version shares `trace_id`s with the train version is refused.

## Landing and measuring

`services/datasets/land.py` writes cell 0 (files, pasted rows, or traces: one row per trace with the `TRACE_MANIFEST` columns — identity, runtime, `input`/`output`, wire `messages`/`tools`, `score` from the trace's last scored `TaskExecution` — two queries per chunk of 200 traces, no unit carving; oversized cells are bounded with preview markers), measures it. A trace source enters through `services/datasets/selection.TraceSource` (REST create, the MCP tool and the landing task all parse the same payload): explicit `trace_ids` or a traces-list selection, unknown filter keys refused, and `count()` run before the dataset row is created so an empty selection is a 400 / `no_traces`, never a dataset in `error` (`measure.frame`: `contract.measure` for the intent report, `alignment.capability_contract` for the row-level one, `contract.stats`, fingerprint), and proposes `capability_rank` and the intent. Landing (`tasks/datasets.land`) runs on the `ingest` queue and queues `diagnose`.

## Running cells

- `notebook/run.py` runs the queued cells in position order. A cell whose script and `input_fingerprint` are unchanged keeps its frame. The first failure stops the run and leaves the rest `queued`; the last good version stays active.
- `notebook/runner.py` is the sandbox: an rlimited `python3 -I` child with `pd`, `np`, `source` and `df` bound, pandas/numpy file IO disabled, imports audited against `notebook/libraries.py` (the stdlib, a preloaded tier baked into the worker image, and an installable wheel-only tier that `install` pulls into `MEDIA_ROOT/libraries/<project>/`).
- `lifecycle.py` holds every edit: `add_cell`, `edit_cell` (re-queues everything after), `remove_cell`, `accept_proposal`, `set_active`, `set_intent`, `set_capability`, `delete_dataset`.
- `run`, `diagnose` and `turn` run on the `workshop` queue; `reap_stuck_runs` (beat) fails runs and landings whose worker died. Live progress is Redis pubsub (`dataset:<id>`) replayed over the SSE `events/` endpoint.

## The agent

The dataset's chat is an independent workshop agent scoped to one dataset's
chain; it is separate from the platform's MCP agent surface. `notebook/agent.py`
owns everything the page sees: the `Tools` class, the one `TOOL_SPECS` table of
thirteen tools — `status`, `query`, `diff`, `try_script`, `inspect`, `add_cell`,
`edit_cell`, `remove_cell`, `set_active`, `set_intent`, `set_capability`,
`rename`, `install` — the step and event shapes, the persisted turn and billing
(`charge_llm_usage`, service `data-workshop`, engine and model in the metadata).
Every tool result is JSON-safe; every error is `{ok: false, error}`. `status`
carries each cell's script so `edit_cell` has something to edit. The system
prompt (`notebook/prompts.py`) inlines the workshop text, the intent playbook,
the capability card, the library list and a 20-row source sample; the volatile
chain arrives through `status`.

`notebook/engines/` drives the model. `engines.select()` walks
`core.model_registry.WORKSHOP_ENGINES` — Cursor, then OpenRouter, then the first
of OPENAI / ANTHROPIC / GEMINI keys — and both engines take the same tools and
emit the same events; with no key the turn lands with `engines.NOT_CONFIGURED`
and the page stays usable. `engines/cursor.py` runs a resumable Composer session
over a workspace `notebook/workspace.py` writes per turn (`AGENTS.md`,
`cells/*.py`, `frames/<version>.parquet`) with the tool table as custom tools.
`engines/native.py` is a tool-calling loop over `core.llms.stream_llm_tools`
(reasoning on; `reasoning_details` ride each assistant message within a turn and
never persist), capped at `MAX_ROUNDS` after which one tool-less round writes
the report. Context is a character budget (`fit`): the system prompt is outside
it and carries a `cache_control` breakpoint that the body builder strips for a
direct endpoint; oldest tool results compact to a stub first, then whole rounds
of earlier turns drop, never the turn in flight. A result over `MAX_RESULT_CHARS`
loses whole items from its largest list and says so (`truncated`).

`inspect` is the measuring tool: it runs a script in the same sandbox with
`produce_frame=False`, requires no `df`, lands nothing, and returns stdout. It
is how the agent computes a threshold before it cuts — `query` covers whatever
SQL can express over every row, `inspect` covers the rest.

A turn streams `chat_step` (thinking and tool steps; a thinking step's `done`
part carries the model's reasoning as `text` when the engine streams it),
`chat_thinking` (reasoning deltas), `chat_delta` and `chat_cell` events, each
published the moment it happens with a per-dataset `seq`, and lands as two
entries in `Dataset.chat`, the agent turn with its `steps`, `ms`, `engine` and
`model`. The turn owns the dataset: `chat` sets `diagnosing` before it
enqueues (from `idle` or `error`), a run inside the turn holds that state
(`run.execute(hold=)`), API cell edits refuse it, and `agent.settle` ends it as
`idle` or, when the last run failed, `error`.

- `diagnose` is one turn: set a pending intent, land the fewest cells that make both contracts hold, then one cell per quality check that has rows behind it, run at once. A fix that drops over half the rows lands `proposed`; the page shows it as a Run / Discard card in the chat, never in the notebook (`accept_proposal` runs it, `remove_cell` drops it).
- `turn` runs a follow-up.
- The page has no header: the name, the intent and the capability change only through the chat (`rename`, `set_intent`, `set_capability`). The contract chip's suggestions (**Ask Overmind to fix it**, the other intent, a better-ranked capability) each send one turn that makes the change and re-aligns the chain.

## Consumers

- A train version hands off to the training wizard (`/training?train=true&datasetId=`); an eval version to the optimiser (`/optimiser?optimize=true&datasetId=`) or to the training wizard as the eval dataset (`/training?train=true&evalDatasetId=`). There is no single-run eval flow.
- A local loop (the SDK's optimiser and backtests) never reads a dataset by id. It pulls the used version through `GET .../export/?cell=&fmt=jsonl|csv` (a raw stream, never a use), whose response carries `X-Overmind-Cell`, `X-Overmind-Version` and `X-Overmind-Fingerprint`, and caches it as `.overmind/datasets/<cell>.jsonl` with the fingerprint beside it (`optimizer_api.export_dataset`).
