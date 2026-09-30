---
name: data-workshop
description: Data Workshop internals — Dataset and Cell, derived versions and the use gate, Parquet identity, the sandboxed runner, agent engines and tools, reviewed preparation, synthetic examples, shared splitting and export. Use when changing datasets, cells, versions, the notebook runner or agent, landing, alignment, diff, or dataset export.
---

# Data Workshop

A dataset is a source and a chain of cells. Every frame is Parquet; versions are derived, never stored; one gate freezes what consumers use; every edit goes through `lifecycle.py`, from the chat, REST or MCP.

## Model

- `Dataset`: name, source kind/spec, `capability` and `intent` (`train` | `eval` | `explore` | `pending`; capability is proposed by `alignment.rank`, intent comes only from the user, and both freeze at first use), `capability_rank`, `active` (FK `Cell`, null = the last cell that ran), `state` (`landing` | `diagnosing` | `idle` | `running` | `error`), `chat` (the one conversation, a JSON list of turns), `agent_id` (the Cursor engine's session), `agent_messages` (the native engine's OpenAI message list, trimmed to a bounded tail) and `agent_turn_key` (the last Celery task id to run a turn, so an acks_late redelivery is a no-op).
- `Cell`: one transformation and the frame it left — `position` (0 = the source), `title`, `script` (a Python body over `df`), `note`, `state` (`proposed` | `queued` | `running` | `ok` | `failed`), `rows`, `columns`, `fingerprint`, `input_fingerprint`, `intent_report`, `capability_report`, `review`, `quality_report`, `stats`, `used_at`.
- Frames live only at `MEDIA_ROOT/datasets/<id>/cells/<cell>.parquet`. Every frame carries `source_row`: row identity, not data. The runner rebuilds it from the index when a script drops the column; `services/datasets/diff.py` joins on it for the row-level and value-level diff the grid and the agent show. The grid, the column count and the agent's tool results hide it; the export keeps it.
- The Console also hides `_overmind_provenance` and `_overmind_document_id` from table columns, filter choices and column counts. It remains internal row lineage for contamination checks; generation never removes that lineage to change the table presentation.
- Versions (`Dataset.versions()`): the source is 1.0, each cell after it a minor, a used cell the next major.
- `use.check(dataset, intent, cell=)` checks the dataset's intent, readable frame and technical format without changing it; validators, readiness tools and estimates call it. `use.use` / `use.freeze` set `used_at` only in the transaction creating the consumer's row, so a refused launch never freezes a version. The FK PROTECTs the cell. A training job pins `cell`, `validation_cell` and `eval_cell`; later consumers read those pinned cells. Used cells and every earlier cell are frozen, together with intent and capability.
- `use.check` performs the same validation without freezing. Quality findings never block use: missing, failed, unknown, sampled or stale reviews appear as **Review recommended** in the workshop, and users may continue without approval. Reviews cover `task_alignment`, `input_evidence`, `answer_support` and `output_schema`. `record_quality_review` executes a read-only audit script producing `source_row` and one boolean-or-null column per named check, exactly one result per original row. The server derives results, checked/failed/unknown counts and failing source-row examples; caller-supplied counts and verdicts are not trusted. The script and results are persisted against the frame, intent and capability context. Unexecuted reports cannot yield Quality passed. These are agent-authored audits, not independent proofs of correctness; unknown semantic claims must remain null, and renderer fidelity requires exact comparison with the actual renderer. Declared input/output schemas are checked deterministically with JSON Schema and reported as advisory capability findings; no external schema references are fetched. Dataset summaries expose active-version readiness; MCP cell contracts expose warnings separately from technical fit. Training setup does not repeat workshop review recommendations.
- Dataset names are not unique; every tool result carries the id and the resolver prefers ids. Training warns about train/eval or train/validation overlap by normalized input content, trace/conversation identity, configured groups or synthetic seed lineage. It never silently removes those rows.

## Landing and measuring

The Console upload composer requires a written prompt and ready source files, with purpose initially pending. The trace source dialog retains **Choose in workshop**, **Data exploration**, **Evaluation**, **Training**, and **Train + eval**. REST and MCP accept a written request, source data, or both. The original request lives in `Dataset.brief`, independently of inferred task context and train/eval intent. `start_dataset` exposes intent-first creation through MCP. A draft has source kind `pending` and no source cell; `POST /datasets/{id}/source/` or `overmind dataset upload FILE --dataset ID` attaches its first source. Attaching refuses a busy dataset or an unfinished chain. In an existing workshop, the same endpoint or chat with `source.uploads` appends an **Added files** cell after the current chain. The cell becomes active; earlier frames and frozen consumer versions are unchanged. Imported batches are retained at `attachments/<cell>.parquet`, fingerprinted, and replayed by the runner. Batch source-row identities are allocated above existing identities; edits that introduce a collision fail explicitly. Original bytes and file/row evidence remain available. Multiple attachments land atomically. Request identity prevents worker redelivery from merging twice. The chat composer accepts selection, drops and pasted files, retains drafts on rejected requests, and allows an attachment-only message with a merge default. MCP impact is CLI-guided through the existing `--dataset` upload, with upload guidance and bounded source metadata exposed in MCP resources. Capability offers **Decide from the rows**, **None**, or a project capability. REST and MCP source creation distinguish an omitted capability (infer) from explicit null (unbound); attaching to a draft preserves its capability choice.

PDF, DOCX, Markdown, UTF-8 text and PNG/JPEG/WebP images land as evidence rows through `documents.py` in the batch worker, capped at 100 MB per document. Upload inspection returns `rows=null` for documents; extraction never runs in the HTTP request. Docling preserves native PDF text, page numbers and bounding boxes. `ocr.py` runs local Tesseract on pages without native text and pages with embedded images; native text regions are masked to avoid duplicate recognition. PDFium renders one page at a time, capped at 16 million pixels and an 8,000-pixel edge. Direct images are capped at 64 megapixels, normalized using EXIF orientation and composited on white for transparency before OCR. Recognition is capped at the same 16 million pixels and 8,000-pixel edge; boxes map back to upright original-image pixels. Animated, corrupt and unsupported image encodings are rejected. OCR has a 60-second page timeout and a 20-minute document budget. The Docker image includes Tesseract with English and orientation data; non-Docker workers need the same system packages. OCR rows record the engine/version, English language and mean word confidence (0–100); artifact metadata lists OCR pages and image dimensions/orientation. File-level extraction progress is persisted in `source_spec.landing_progress` and published over SSE until the atomic landing finishes. Console attachments show image thumbnails and distinguish upload readiness from extraction. Complex reading order and visual tables are not reconstructed. Missing text and extraction limitations remain explicit; unreadable documents do not land partial results. Original upload bytes are copied under the dataset's `sources/<sha256>` and exposed through a project-scoped download. `source_spec.sources` records hashes, parser versions and limitations. Rows retain document identity and evidence references in `_overmind_provenance`; document identities participate in split grouping. Extraction never creates training answers.

The Console keeps one notebook page with the source at the top. Source details expand within that cell; transformation execution details expose input/output fingerprints and duration. Row expansion reveals evidence references. Selecting a cell scopes chat without changing the active version.

The Console landing page centres “Ready to train?” in the shared Mondwest empty-state headline above a bottom-anchored composer without a footer row. The plus menu offers **Add files** and **Select from traces**. File selection, pasted files and page drops attach compact filename chips inside the input, beside the plus and send controls. Clicking a chip opens file type, size, row count and upload details; failed attachments expose retry and removal. There are no prompt shortcuts or Ask/Auto controls. Dataset purpose is inferred from the prompt and rows. A second sidebar beside the main navigation groups dataset workspaces by project; it shares a continuous rounded outer bezel with the content. Its column expands on entry and collapses on exit; reduced motion switches immediately, and closed navigation is inert with dataset polling disabled. Single-line workspace rows retain activity status, with full names, source types and row counts in hover/focus details. Outlined search, left-aligned pagination and deletion remain available; touch layouts keep deletion visible. It tucks away inside the cell workshop; mobile exposes it through **Workspaces**. Changing project clears the draft text and attachments. Start requires nonblank prompt text and at least one file, and waits for all retained files to be ready. The button, form submit and Enter share the same guard. Shift+Enter inserts a newline; IME composition does not submit. **Select from traces** opens the source dialog; pasted rows remain API-only. Creation supports up to 100 removable uploads. Each completed file is counted by `POST /api/uploads/{id}/inspect/` with its byte `size`; `source.uploads` carries their ids in selection order and `land.read_uploads` combines the rows before any split. The source dialog defaults **Train + eval** to a 30% evaluation share and previews the backend's half-up row count, with at least one row per dataset. File names, byte sizes and row counts are retained in `source_spec.files`.

`services/datasets/land.py` writes cell 0 (files, pasted rows, or traces: one row per trace with the `TRACE_MANIFEST` columns — identity, runtime, `input`/`output`, wire `messages`/`tools`, `score` from the trace's last scored `TaskExecution` — two queries per chunk of 200 traces, no unit carving; oversized cells are bounded with preview markers), measures it. A trace source enters through `services/datasets/selection.TraceSource` (REST create, the MCP tool and the landing task all parse the same payload): explicit `trace_ids` or a traces-list selection, unknown filter keys refused, and `count()` run before the dataset row is created so an empty selection is a 400 / `no_traces`, never a dataset in `error` (`measure.frame`: `contract.measure` for the intent report, `alignment.capability_contract` for the row-level one, `contract.stats`, fingerprint), and proposes `capability_rank` only. Landing (`tasks/datasets.land`) runs on the `batch` queue, commits both halves atomically as `diagnosing` (never an intermediate `idle`), and queues `diagnose`. Files land unaltered: `files.py` rejects rows wider than their header, repeated columns and non-UTF-8 input; CSV columns become numeric only when every value spells a number exactly. `contract.training_line` builds the same `{messages, tools?}` line for validation and training export. A `Landing` is the source read once; `dispatch.create_split` makes a `<name> train` and a `<name> eval` dataset with intents fixed and lands both from one read, cut by `Landing.split(eval_percent, position)` (`head` | `tail` | `random`, at least one row on each side); each `source_spec.split` names the role and the sibling.

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
(`record_workshop_usage`, service `data-workshop`, funding source, engine and model in the metadata).
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

`check_semantic_quality` executes named semantic questions against actual rows and declared evidence/answer columns, using Jev with generative fallback for server-funded turns or the selected ChatGPT model for personally funded turns. Answer support requires separate answer and independent evidence columns. Each call checks at most 200 rows, packed against the transport's UTF-8 state/question budgets; oversized groups split without truncating evidence. Every completed batch checkpoints results and usage before progress is emitted. Repeated calls reread the saved audit and resume the same frame/context/check contract; changed audit checkpoints cannot overwrite concurrent work. Persisted batch IDs make billing reconciliation idempotent on resume. Results are boolean/null, unprocessed and unsupported rows remain unknown, and reports stay advisory. The persisted audit records row identities, decisions, confidence, fallback and usage; status and MCP expose a bounded summary, not the full resumable state. Both quality tools share `review.record_quality_results`, which checks whole-frame coverage and locks the version/context before merging results. The semantic tool never edits rows, invents labels, or grants approval to a transformation. Deterministic scripts remain the tool for exact rules; generation and semantic-edit approval remain separate.

`notebook/engines/` drives the model. Without a personal ChatGPT selection, `engines.select(user)` walks
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

### ChatGPT funding

Self-hosted users connect an account on the initial **Continue with ChatGPT**
login or in Settings → Data Workshop models. First login/inline linking selects
ChatGPT funding and the first available account model when plan permission is
granted; later logins preserve explicit funding choices.
Both Workshop composers place **Use ChatGPT** beside the bottom plus button;
the toggle enables personal funding and the shared model selector chooses from
the account's catalogue. Turning it off keeps the account connected. The same
saved preference remains editable in Settings, and pending funding mutations
disable request submission. `services/chatgpt.py` owns Sign in with ChatGPT, the model
catalogue and the Responses transport. `models/chatgpt.py` stores per-user
registrations, encrypted tokens and a persistent funding preference. PKCE, state,
an HttpOnly browser cookie and verified ID tokens bind the loopback callback;
refresh rotation locks the account. Clerk or Stripe disables this public local
integration. Cloud plan usage needs OpenAI's separate access approval.

`engines/chatgpt.py` uses the native tool loop with Responses namespace tools,
`store=false` and streaming. Tools execute only after `response.completed`;
encrypted reasoning is replayed within the turn and stripped from saved history.
The selected session is pinned for each turn, including semantic checks, which
use that model instead of Jev. Audit identity includes account and model, so
switching funding does not reuse a differently funded audit. The ledger records
zero-charge usage and REST/MCP chat turns expose funding, engine and model.
Limits, disconnection and unavailable sessions stop the turn without switching
to server models. Evaluation runs, training and serving retain their own billing.

MCP classification: account consent and funding selection are frontend-only;
MCP workshop requests use the caller's saved preference and return the same
funding provenance. Tokens and OAuth URLs are never MCP resource data.

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
to preserve position constraints, then retires the other previews tied to the previous chain tail.

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
Steps and cell references carry UTF-16 text offsets. Activity updates retain their starting offset and never insert a paragraph into narration. Both engines use `Tools.respond` for text and `Tools.start_response` only at model-round boundaries; Cursor consumes ordered provider deltas rather than deriving boundaries from asynchronous tool callbacks. The Console places activity after a complete paragraph or fenced code block, never between its tokens. Progress events carry a full saved snapshot.
Thinking text is snapshotted at most once per second and capped at 16,000
characters per step. Only provider-exposed text is shown; it is never fabricated.
Completion updates that entry with `status`, `ms`, `engine` and `model`.
MCP inspection and `get_job(kind=dataset_run)` expose the same saved progress.
The turn owns the dataset: `chat` sets `diagnosing` before it
enqueues (from `idle` or `error`), a run inside the turn holds that state
(`run.execute(hold=)`), API cell edits refuse it, and `agent.settle` ends it as
`idle` or, when the last run failed, `error`.

- `diagnose` first resolves purpose from explicit user words or pauses with `awaiting_intent`. Pending tools cannot access data or mutate the chain; `set_intent` requires a quote from the user request. Missing intent produces a durable question with Training, Eval and Data exploration choices. Chat accepts `intent_choice` with `intent_turn_id`; the shared locked dispatcher resolves that exact question and continues the original request once. MCP `message_dataset_agent` shares the same fields and resources expose question IDs. Exploration persists as `explore`, follows the user request, and skips automatic train/eval shaping and audits. Once train/eval intent is explicit, `diagnose` completes the initial chain in one turn: run shaping and measured cleaning (including justified exclusions), audit, repair actionable findings and recheck the changed version. A failed check does not end preparation while supported improvements remain; unresolved findings become non-blocking warnings after those repairs. Do not repeat identical audits or add no-op cells when no supported repair remains. Evidence-preserving restructuring and deterministic derivation from supplied facts and declared rules are mechanical, even when complex. These initial cells do not wait for per-cell approval; the source, affected-row examples and coverage impact remain available. Semantic judgement calls always remain proposals, including during initial preparation. Follow-up exclusions, row additions through transformation scripts, loss of trackable identity and explicit `run=false` also stay proposals. Finish independent repairs, then propose one concrete result with its decision, evidence and tradeoff. The chat shows **Approve** / **Reject**, coverage counts and identity-matched input/output previews; acceptance consumes the exact preview frame. Changes to the input, intent or capability context retire pending previews through `proposals.retire_outdated`; the shared approval guard still verifies the exact frame. Superseded previews are recorded separately from user rejection, and any replacement must be previewed against current data. Automatic diagnosis cannot generate synthetic examples. Explicitly requested generation adds validated batches directly. Expensive similarity/outlier analysis follows concrete findings or a user request, not a mandatory first-pass checklist.
- `turn` runs a follow-up.

Proposal decisions share `dispatch` across REST and MCP. A turn with pending proposals
ends as `awaiting_approval`, not an incomplete-generation error. Approval runs the
fingerprinted preview, makes it active even when an earlier version was pinned,
and records the decision against its proposing chat turn. Denial removes the
proposal without applying it. Once all that turn's proposals are decided or superseded, a single
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

The notebook weaves requests and agent responses through one canonical cell chain above a bottom-anchored composer. Every cell appears once; later revisions link back without reordering it. Thinking/tool sections are collapsed by default, including during work. The latest final summary is expanded; earlier narration is folded into Preparation activity. A new prompt collapses previous responses, which remain reopenable. Pixel status icons show working, completion, review and failure, with motion disabled under reduced-motion preferences. Thinking text renders as Markdown, with shared elbow connectors and inspectable tool inputs/results. Generation counts show rows added, with a notice after 30 seconds without activity and no separate status card. The agent explains its approach, evidence and decisions in public-facing summaries, and its final explanation is preserved alongside verified generation counts. Pending transformation proposals appear as compact rows in one inset panel anchored above the composer, even if a chat turn omitted their reference. Rows show a short description and Approve/Reject actions; clicking the description expands the full note, impact and evidence previews. Only one row expands at a time, with bounded scrolling; only complete previews of the current successful chain tail appear, and busy datasets disable both actions. There is no out-of-date suggestion badge. Generated data appears as a normal cell result; the user requests continuation or adjustments through chat. A snapshot preserves seed dataset/cell/row, context, instruction and inherited content/group lineage in `_overmind_provenance`; the cell records the serving workshop engine/model. Generated answers are not independently verified ground truth. No extra generator model is selected by the user. Scripts preserve lineage and grouping metadata by `source_row`.

`partition.split_rows` is shared by source train/eval creation and internal training/validation splitting. It removes exact duplicate rows by default; training passes `deduplicate=False` to preserve every reviewed row. It unions normalized input matches, trace/conversation IDs, explicit `group_by` columns and synthetic seed lineage into indivisible components. Optional `stratify_by` balances categorical coverage across those components. Seeded random, head and tail ordering are supported; grouping can change the requested percentage. `source_spec.contamination_report` records actual counts, duplicate removal, group/content overlap and strata. Near-duplicate similarity is explicitly **not checked** by the split operation. Training checks the selected versions again through `rows.contamination`; dataset versions and capability cannot be changed after a job is created.

Modal jobs run exact CPU preprocessing after launch for the selected model, training type, context and dataset versions. Setup does not start, display or wait for this step. Technical incompatibilities fail preparation before GPU training; repairs belong in the workshop, and a job using revised versions is checked again. Explicit REST/MCP preparation remains available. See backend-architecture § Training preparation.

## Consumers

- A train version hands off to the training wizard (`/training?train=true&datasetId=`); an eval version to the optimiser (`/optimiser?optimize=true&datasetId=`) or to the training wizard as the eval dataset (`/training?train=true&evalDatasetId=`). There is no single-run eval flow.
- A local loop (the SDK's optimiser and backtests) never reads a dataset by id. It pulls the used version through `GET .../export/?cell=&fmt=jsonl|csv` (a raw stream, never a use), whose response carries `X-Overmind-Cell`, `X-Overmind-Version` and `X-Overmind-Fingerprint`, and caches it as `.overmind/datasets/<cell>.jsonl` with the fingerprint beside it (`optimizer_api.export_dataset`).
