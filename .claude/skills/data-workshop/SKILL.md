---
name: data-workshop
description: Data Workshop internals — Dataset and Cell, derived versions and the use gate, Parquet identity, the sandboxed runner, agent engines and tools, reviewed preparation, synthetic examples, shared splitting and export. Use when changing datasets, cells, versions, the notebook runner or agent, landing, alignment, diff, or dataset export.
---

# Data Workshop

A dataset is a source and a chain of cells. Every frame is Parquet; versions are derived, never stored; one gate freezes what consumers use; every edit goes through `lifecycle.py`, from the chat, REST or MCP.

## Model

- `Dataset`: name, source kind/spec, `capability` and `intent` (`train` | `eval` | `pending`, both proposed at landing by `alignment.rank` / `contract.propose_intent` and frozen by the first use), `capability_rank`, `active` (FK `Cell`, null = the last cell that ran), `state` (`landing` | `diagnosing` | `idle` | `running` | `error`), `chat` (the one conversation, a JSON list of turns), `agent_id` (the Cursor engine's session), `agent_messages` (the native engine's OpenAI message list, trimmed to a bounded tail) and `agent_turn_key` (the last Celery task id to run a turn, so an acks_late redelivery is a no-op).
- `Cell`: one transformation and the frame it left — `position` (0 = the source), `title`, `script` (a Python body over `df`), `note`, `state` (`proposed` | `queued` | `running` | `ok` | `failed`), `rows`, `columns`, `fingerprint`, `input_fingerprint`, `intent_report`, `capability_report`, `review`, `quality_report`, `stats`, `used_at`.
- Frames live only at `MEDIA_ROOT/datasets/<id>/cells/<cell>.parquet`. Every frame carries `source_row`: row identity, not data. The runner rebuilds it from the index when a script drops the column; `services/datasets/diff.py` joins on it for the row-level and value-level diff the grid and the agent show. The grid, the column count and the agent's tool results hide it; the export keeps it.
- The Console also hides `_overmind_provenance` from table columns, filter choices and column counts. It remains internal row lineage for contamination checks; generation never removes that lineage to change the table presentation.
- Versions (`Dataset.versions()`): the source is 1.0, each cell after it a minor, a used cell the next major.
- `use.check(dataset, intent, cell=)` checks the dataset's intent, readable frame and technical format without changing it; validators, readiness tools and estimates call it. `use.use` / `use.freeze` set `used_at` only in the transaction creating the consumer's row, so a refused launch never freezes a version. The FK PROTECTs the cell. A training job pins `cell`, `validation_cell` and `eval_cell`; later consumers read those pinned cells. Used cells and every earlier cell are frozen, together with intent and capability.
- `use.check` performs the same validation without freezing. Quality findings never block use: missing, failed, unknown, sampled or stale reviews appear as **Review recommended** in the workshop, and users may continue without approval. Reviews cover `task_alignment`, `input_evidence`, `answer_support` and `output_schema`. `record_quality_review` executes a read-only audit script producing `source_row` and one boolean-or-null column per named check, exactly one result per original row. The server derives results, checked/failed/unknown counts and failing source-row examples; caller-supplied counts and verdicts are not trusted. The script and results are persisted against the frame, intent and capability context. Unexecuted reports cannot yield Quality passed. These are agent-authored audits, not independent proofs of correctness; unknown semantic claims must remain null, and renderer fidelity requires exact comparison with the actual renderer. Declared input/output schemas are checked deterministically with JSON Schema and reported as advisory capability findings; no external schema references are fetched. Dataset summaries expose active-version readiness; MCP cell contracts expose warnings separately from technical fit. Training setup does not repeat workshop review recommendations.
- Dataset names are not unique; every tool result carries the id and the resolver prefers ids. Training warns about train/eval or train/validation overlap by normalized input content, trace/conversation identity, configured groups or synthetic seed lineage. It never silently removes those rows.

## Landing and measuring

Console creation requires an explicit purpose: **Evaluation**, **Training**, or **Train + eval**. Capability offers **Decide from the rows**, **None**, or a project capability. REST and MCP creation distinguish an omitted `capability` (infer at landing) from explicit `null` (leave unbound); dispatch carries that choice to landing for both ordinary creation and both sides of a split. API/CLI callers may still omit intent to request inference.

The Console's **New dataset** menu chooses **Upload file** or **Data from traces** before opening the dialog; pasted rows remain API-only. File selection supports up to 100 removable uploads. Each completed file is counted by `POST /api/uploads/{id}/inspect/` with its byte `size`; `source.uploads` carries their ids in selection order and `land.read_uploads` combines the rows before any split. The dialog defaults **Train + eval** to a 30% evaluation share and previews the backend's half-up row count, with at least one row per dataset. File names, byte sizes and row counts are retained in `source_spec.files`.

`services/datasets/land.py` writes cell 0 (files, pasted rows, or traces: one row per trace with the `TRACE_MANIFEST` columns — identity, runtime, `input`/`output`, wire `messages`/`tools`, `score` from the trace's last scored `TaskExecution` — two queries per chunk of 200 traces, no unit carving; oversized cells are bounded with preview markers), measures it. A trace source enters through `services/datasets/selection.TraceSource` (REST create, the MCP tool and the landing task all parse the same payload): explicit `trace_ids` or a traces-list selection, unknown filter keys refused, and `count()` run before the dataset row is created so an empty selection is a 400 / `no_traces`, never a dataset in `error` (`measure.frame`: `contract.measure` for the intent report, `alignment.capability_contract` for the row-level one, `contract.stats`, fingerprint), and proposes `capability_rank` and the intent. Landing (`tasks/datasets.land`) runs on the `batch` queue, commits both halves atomically as `diagnosing` (never an intermediate `idle`), and queues `diagnose`. Files land unaltered: `files.py` rejects rows wider than their header, repeated columns and non-UTF-8 input; CSV columns become numeric only when every value spells a number exactly. `contract.training_line` builds the same `{messages, tools?}` line for validation and training export. A `Landing` is the source read once; `dispatch.create_split` makes a `<name> train` and a `<name> eval` dataset with intents fixed and lands both from one read, cut by `Landing.split(eval_percent, position)` (`head` | `tail` | `random`, at least one row on each side); each `source_spec.split` names the role and the sibling.

## Running cells

- `notebook/run.py` runs the queued cells in position order. A cell whose script and `input_fingerprint` are unchanged keeps its frame. The first failure stops the run and leaves the rest `queued`; the last good version stays active.
- `notebook/runner.py` is the sandbox: an rlimited `python3 -I` child with `pd`, `np`, `source` and `df` bound, pandas/numpy file IO disabled, imports audited against `notebook/libraries.py` (the stdlib, a preloaded tier baked into the worker image, and an installable wheel-only tier that `install` pulls into `MEDIA_ROOT/libraries/<project>/`).
- `lifecycle.py` holds every edit: `add_cell`, `edit_cell` (re-queues everything after), `remove_cell`, `accept_proposal`, `set_active`, `set_intent`, `set_capability`, `delete_dataset`.
- `run`, `diagnose` and `turn` run on the `interactive` queue. Every busy transition goes through `lifecycle.enter_busy`, which moves `updated_at` with the state; `reap_stuck_runs` (beat) measures a busy state's age from it against that state's own hard limit. Live progress is Redis pubsub (`dataset:<id>`) replayed over the SSE `events/` endpoint.

## The agent

The dataset's chat is an independent workshop agent scoped to one dataset's
chain; it is separate from the platform's MCP agent surface. `notebook/agent.py`
owns everything the page sees: the `Tools` class, the one `TOOL_SPECS` table of
tools — `status`, `prepare_examples`, `query`, `diff`, `try_script`, `inspect`, `add_cell`,
`edit_cell`, `remove_cell`, `set_active`, `set_intent`, `set_capability`,
`rename`, `install`, `seed_examples`, `add_synthetic_rows`, `record_quality_review`, `check_semantic_quality` — the step and event shapes, the persisted turn and billing
(`charge_llm_usage`, service `data-workshop`, engine and model in the metadata).
Every tool result is JSON-safe; every error is `{ok: false, error}`. `status`
carries each cell's script so `edit_cell` has something to edit. The system
prompt (`notebook/prompts.py`) inlines the workshop text, the intent playbook,
the capability card, the library list and `context.workshop_context`: shared
downstream SFT/model-eval requirements plus whole-frame source and active-version
profiles. Profiles group instructions, task labels, input/output shapes and tool
schemas; all rows are counted, with 16 retained families and eight clipped examples.
Unlisted-family row counts are explicit and require targeted queries. This is
structural context, not a semantic audit. MCP `inspect_dataset` exposes the same
`preparation_context`; the volatile chain arrives through `status`.

`check_semantic_quality` executes named semantic questions against actual rows and declared evidence/answer columns, using Jev with generative fallback. Answer support requires separate answer and independent evidence columns. Each call checks at most 200 rows, packed against the transport's UTF-8 state/question budgets; oversized groups split without truncating evidence. Every completed batch checkpoints results and usage before progress is emitted. Repeated calls reread the saved audit and resume the same frame/context/check contract; changed audit checkpoints cannot overwrite concurrent work. Persisted batch IDs make billing reconciliation idempotent on resume. Results are boolean/null, unprocessed and unsupported rows remain unknown, and reports stay advisory. The persisted audit records row identities, decisions, confidence, fallback and usage; status and MCP expose a bounded summary, not the full resumable state. Both quality tools share `review.record_quality_results`, which checks whole-frame coverage and locks the version/context before merging results. The semantic tool never edits rows, invents labels, or grants approval to a transformation. Deterministic scripts remain the tool for exact rules; generation and semantic-edit approval remain separate.

`notebook/engines/` drives the model. `engines.select()` walks
`core.model_registry.WORKSHOP_ENGINES` — Cursor, then OpenRouter, then the first
of OPENAI / ANTHROPIC / GEMINI keys — and both engines take the same tools and
emit the same events; with no key the turn lands with `engines.NOT_CONFIGURED`
and the page stays usable. `engines/cursor.py` runs a resumable Composer session
over a workspace `notebook/workspace.py` writes per turn (`AGENTS.md`,
`cells/*.py`, `frames/<version>.parquet`) with the tool table as custom tools.
Cursor SDK 1.0.31 or newer restricts the session to the `mcp` tool group,
with shell and subagents disabled; the full workshop context is sent in each
request. Batches cannot bypass the tool callbacks through direct service imports.
The adapter forwards SDK `thinking` events, including their reported duration,
into the same thinking stream the native engine uses.
`engines/native.py` is a tool-calling loop over `core.llms.stream_llm_tools`
(reasoning on; `reasoning_details` ride each assistant message within a turn and
never persist), capped at `MAX_ROUNDS` after which one tool-less round writes
the report. Context is a character budget (`fit`): the system prompt is outside
it and carries a `cache_control` breakpoint that the body builder strips for a
direct endpoint; oldest tool results compact to a stub first, then whole rounds
of earlier turns drop, never the turn in flight. A result over `MAX_RESULT_CHARS`
loses whole items from its largest list and says so (`truncated`).

`add_cell` validates before landing and runs directly; `try_script` is optional.
One preview is cached per turn by cell, fingerprint and script, so an identical
`add_cell` consumes it without another sandbox run. Changed inputs or scripts
invalidate reuse. Identical pending scripts against the same input, intent and
capability context return the existing proposal UUID after verifying its preview.
No-op scripts create nothing. Tool receipts omit large preview examples; full
proposal previews remain stored for review. Status exposes active_id and flags
clipped scripts. The agent's remove_cell accepts only an exact pending-proposal
UUID: applied, source and generated versions cannot be erased during cleanup.
Proposal file deletion occurs only after the database transaction commits.
Approving a later proposal uses a free non-negative position and reverse shifts
to preserve both position constraints and all earlier proposals.

`prepare_examples` applies the shared model-independent transcript conversion in
one cell. Initial preparation runs it before the agent; custom cells can also use
`prepare_examples(df, intent="train" or "eval")`. Eval keeps the complete prefix
(system, user, prior assistant/tool turns and schemas) in `input`, and separates
the final assistant target into `expected_output`. Model transcripts are not
validated against the application's entry-point schema. Identifier-only eval
inputs carry an advisory evidence warning. Collapsing rich context into identifiers
requires approval even during initial preparation; it never silently discards evidence.

`examples.normalize_record` normalises JSON-encoded `messages` and `tools` at
preparation and consumer boundaries without decoding message content. Tool schema
validation runs even on rows with no tool calls; malformed schemas are not dropped.
The exact preprocessing cache includes the shared export/validation code fingerprint.
Replacing or removing existing system/developer instructions is measured by
`review.impact` and always needs a semantic proposal, including initial preparation;
agent edits and automatic reruns cannot bypass this by labelling it mechanical.

Source rows and transformations retain hidden source-content and case identity in
`_overmind_provenance`. It is not a visible data column or a training feature.
Existing human_reviewed annotations are inherited by source identity; projection
cannot turn unreviewed synthetic examples into human-reviewed data.
Contamination checks match packet/onboarding IDs, case IDs and example IDs from
input JSON as well as existing lineage, including across SFT/eval projections.

`inspect` is the measuring tool: it runs a script in the same sandbox with
`produce_frame=False`, requires no `df`, lands nothing, and returns stdout. It
is how the agent computes a threshold before it cuts — `query` covers whatever
SQL can express over every row, `inspect` covers the rest.

A turn streams `chat_step` (thinking and tool steps; a thinking step's `done`
part carries the model's reasoning as `text` when the engine streams it),
`chat_thinking` (reasoning deltas), `chat_delta`, `chat_cell` and `chat_progress`
events, each published the moment it happens with a per-dataset `seq`.
The user turn and a running agent entry are saved immediately in `Dataset.chat`.
Tool callbacks serialise per turn and persist the stage, reason, saved counts,
steps, narrative text and cell references, so a reload restores progress without SSE replay.
Steps and cell references carry UTF-16 text offsets. Both engines use `Tools.respond`
to interleave narration with activity; progress events carry a full saved snapshot.
Thinking text is snapshotted at most once per second and capped at 16,000
characters per step. Only provider-exposed text is shown; it is never fabricated.
Completion updates that entry with `status`, `ms`, `engine` and `model`.
MCP inspection and `get_job(kind=dataset_run)` expose the same saved progress.
The turn owns the dataset: `chat` sets `diagnosing` before it
enqueues (from `idle` or `error`), a run inside the turn holds that state
(`run.execute(hold=)`), API cell edits refuse it, and `agent.settle` ends it as
`idle` or, when the last run failed, `error`.

- `diagnose` completes the initial chain in one turn: resolve intent, run shaping and measured cleaning (including justified exclusions), audit, repair actionable findings and recheck the changed version. A failed check does not end preparation while supported improvements remain; unresolved findings become non-blocking warnings after those repairs. Do not repeat identical audits or add no-op cells when no supported repair remains. Evidence-preserving restructuring and deterministic derivation from supplied facts and declared rules are mechanical, even when complex. These initial cells do not wait for per-cell approval; the source, affected-row examples and coverage impact remain available. Semantic judgement calls always remain proposals, including during initial preparation. Follow-up exclusions, row additions through transformation scripts, loss of trackable identity and explicit `run=false` also stay proposals. Finish independent repairs, then propose one concrete result with its decision, evidence and tradeoff. The chat shows **Approve** / **Deny**, coverage counts and identity-matched input/output previews; acceptance consumes the exact preview frame. Changes to the input, intent or capability context invalidate approval. Automatic diagnosis cannot generate synthetic examples. Explicitly requested generation adds validated batches directly. Expensive similarity/outlier analysis follows concrete findings or a user request, not a mandatory first-pass checklist.
- `turn` runs a follow-up.

Proposal decisions share `dispatch` across REST and MCP. A turn with pending proposals
ends as `awaiting_approval`, not an incomplete-generation error. Approval runs the
fingerprinted preview, makes it active even when an earlier version was pinned,
and records the decision against its proposing chat turn. Denial removes the
proposal without applying it. Once all that turn's proposals are decided, a single
follow-up receives the original request and decisions and completes the remaining
work, including final-version quality checks. Dispatch occurs after commit;
repeated approval does not queue a second run or continuation. Failed or stale
previews never activate or resume the agent. During generation, transformation
scripts cannot add rows; new examples must use `add_synthetic_rows`, never
identifier-remapped copies of source rows.

- The page has no header: the name, the intent and the capability change only through the chat (`rename`, `set_intent`, `set_capability`). The contract chip's suggestions (**Ask Overmind to fix it**, the other intent, a better-ranked capability) each send one turn that makes the change and re-aligns the chain.

## Preparation and splitting

Preparation maps every required target field to existing evidence, a deterministic rule-derived value, a representation change, missing evidence or a user decision. Worker-specific envelopes alone are not a reason to stop: build supported deliverables and audit input evidence and answer support against the same selected target. A canonical prompt is bound only when the example fulfils that task. Do not fabricate tool execution history, outcomes or policies; approval is not a substitute for missing evidence. If the remaining decision has no executable preview, ask a focused question rather than presenting a menu of tool names.

The selected capability defines the task boundary; ask about mixed scope only when no target has been chosen. Worker-shaped sources call for investigating a supported transformation, not an audit-only refusal. Decode nested user JSON and inspect source evidence before declaring it absent; recover supplied evidence lost during shaping and map supported answers to the declared schema. Combine worker evidence only with verified same-case identity, never positional joins or matching mode counts, and never across held-out boundaries. Worker examples cannot become end-to-end examples merely by replacing their system prompts or inventing final deliverables. Worker training uses a separate worker capability or an explicitly dataset-derived task with no capability. Eval shaping retains the full input evidence/tool transcript before the target answer. Identifiers, document references and prompts are not substitutes for evidence. Do not invent missing facts or copy targets into inputs. Both initial and requested follow-up preparation apply supported repairs before recording residual warnings; questions and audit-only requests do not authorise transformations. Incomplete semantic audits remain visible as warnings, not disabled consumer actions.

The workshop has no model selector. `context.preparation_context` supplies scanned task context and the latest active behaviour contracts for cleaning, coverage analysis and example selection. Train shaping ends with an explicit projection to `messages`, optional `tools`, and only metadata needed for coverage or contamination checks. Redundant source features and labels already represented in the transcript do not remain in the prepared table; the source stays queryable and `source_row` preserves identity. Group/trace/conversation identity, independent coverage annotations and synthetic provenance remain available. `review.readiness` separates format-valid from quality-reviewed (agent-measured checks, not a human approval). Quality results are tied to the frame, intent and capability-context fingerprints; failed/unknown checks remain visible. Exact model/context compatibility belongs to training preparation, not the workshop.

Synthetic generation is a user-requested chat operation with no draft or Apply step. `seed_examples` fixes the requested final `target_rows` and instruction and samples full seed rows. It resumes a generation at the chain tail for the same target, or accepts its `cell_id` explicitly. `add_synthetic_rows` validates at most 50 generated rows per batch against shape, real seed identities and exact duplicates across the source and saved batches; capability mismatches are advisory findings. Each batch updates one generated cell, measures it and makes it active; the source remains unchanged. An exact batch retry is a no-op. Generation and `use.use` share a dataset row lock: a used version cannot receive another batch. A changed source, intent, context or target, or a later transformation, invalidates continuation. Progress counts only validated, persisted rows, and an early stop is reported as incomplete while retaining the added rows.

The chat interleaves explanations, collapsible thinking/tool sections and full-width cell-result rows in execution order. The current activity section opens while running; earlier sections collapse when narration resumes, unless toggled by the user. Thinking text renders as Markdown, with shared elbow connectors and inspectable tool inputs/results. Generation counts show rows added, with a notice after 30 seconds without activity and no separate status card. The agent explains its approach, evidence and decisions in public-facing summaries, and its final explanation is preserved alongside verified generation counts. Pending transformation proposals appear in a review section, even if a chat turn omitted their reference. Generated data appears as a normal cell result; the user requests continuation or adjustments through chat. A snapshot preserves seed dataset/cell/row, context, instruction and inherited content/group lineage in `_overmind_provenance`; the cell records the serving workshop engine/model. Generated answers are not independently verified ground truth. No extra generator model is selected by the user. Scripts preserve lineage and grouping metadata by `source_row`.

`partition.split_rows` is shared by source train/eval creation and internal training/validation splitting. It removes exact duplicate rows by default; training passes `deduplicate=False` to preserve every reviewed row. It unions normalized input matches, trace/conversation IDs, explicit `group_by` columns and synthetic seed lineage into indivisible components. Optional `stratify_by` balances categorical coverage across those components. Seeded random, head and tail ordering are supported; grouping can change the requested percentage. `source_spec.contamination_report` records actual counts, duplicate removal, group/content overlap and strata. Near-duplicate similarity is explicitly **not checked** by the split operation. Training checks the selected versions again through `rows.contamination`; dataset versions and capability cannot be changed after a job is created.

Modal jobs run exact CPU preprocessing after launch for the selected model, training type, context and dataset versions. Setup does not start, display or wait for this step. Technical incompatibilities fail preparation before GPU training; repairs belong in the workshop, and a job using revised versions is checked again. Explicit REST/MCP preparation remains available. See backend-architecture § Training preparation.

## Consumers

- A train version hands off to the training wizard (`/training?train=true&datasetId=`); an eval version to the optimiser (`/optimiser?optimize=true&datasetId=`) or to the training wizard as the eval dataset (`/training?train=true&evalDatasetId=`). There is no single-run eval flow.
- A local loop (the SDK's optimiser and backtests) never reads a dataset by id. It pulls the used version through `GET .../export/?cell=&fmt=jsonl|csv` (a raw stream, never a use), whose response carries `X-Overmind-Cell`, `X-Overmind-Version` and `X-Overmind-Fingerprint`, and caches it as `.overmind/datasets/<cell>.jsonl` with the fingerprint beside it (`optimizer_api.export_dataset`).
