# Changelog

Notable changes to the `overmind` package. The span-attribute wire contract is
pinned separately in [`docs/tracing-attributes.md`](docs/tracing-attributes.md);
entries here cover the SDK surface.

## Unreleased

### Fixed

- `convert_json_to_toml` keeps the scan's `modes` and `llm_utilities` (in the
  capability card, utilities under their `called_by` parent) and its
  `decision_logic` and `policy_markdown`; `overmind sync` sends and round-trips
  them.
- The dataset skills cancel a script run with `run`, call a stopped import
  `stopped`, and poll `get_job(kind=dataset_run)`. Skills version 1.5.1.

### Changed

- Every span started under a span that carries `overmind.intent` is stamped with the same
  intent unless it declares its own, and `overmind.intent(text)` sets the attribute on the
  current span as well as emitting its event. The platform scores a span from its own
  attributes instead of waiting for its ancestors to arrive.
- The bundled skills use the MCP tools `create_eval_matrix_row` and `delete_eval_matrix_row`
  in place of `upsert_eval_matrix_row`; matrix rows are created and deleted, never edited.
- `/overmind setup` no longer asks the scan for judge metrics. After `overmind sync` it
  decorates every function, tool, retrieval step, mode and helper the card names and
  declares each span's `expectations` in code. The scan's `eval_matrix` holds only
  deterministic checks (`check` plus `params`), which sync turns into rows; a bad
  `check` or `params` makes sync return a 400 naming the entry.
- `overmind sync` reads the `expectations=` declared on the checkout's `entry_point`,
  `workflow` and `observe` decorators and sends them with each synced capability; they never
  enter `overmind.toml`. The platform adds them to the capability's Default eval set, so
  `overmind backtest`, optimiser runs and training evaluations judge the same criteria as
  live scoring. Run `overmind sync` again after decorating.
- `overmind backtest` creates one run per target in a single eval-run group
  (`targets`, a `recorded` baseline plus one `generate` run per model) and gates
  on each candidate's `/comparison/` against the baseline.
- `overmind dataset upload` inspects the upload before creating the dataset; the
  server now refuses an uninspected upload, so older CLI versions must upgrade.

### Added

- `overmind dataset attach FILE --json` uploads a table without creating a
  dataset and prints its `upload_id`, rows and sha256; a dataset script reads it
  as the frame `attachment` through `try_dataset_script` or `add_dataset_version`.
- `observe(self_attrs=[...])` (and the decorator variants) records named attributes of
  `self`/`cls` as inputs next to the call's arguments, for methods whose request lives on
  the instance. `verify_instrumentation` now reports an empty captured input as missing.
- `observe(intent=, expectations=[...], reference_trajectory=[...])` (and the
  `entry_point` / `workflow` / `tool` / `retrieval` variants) stamp
  `overmind.intent`, `overmind.expectations` and
  `overmind.reference_trajectory` for live scoring.
- `overmind.session(conversation_id)`: scoped `conversation.id` for a block.
- `overmind.record_llm_call(model, total_tokens, cost)`: usage on the current
  span for LLM clients without a provider instrumentation.
- Codex (`.agents/plugins/marketplace.json`) and Cursor (`.cursor-plugin/marketplace.json`)
  marketplaces at the repository root, so all three clients install the plugin
  from `overmind-core/overmind`.
- `overmind dataset upload FILE --split PERCENT [--split-position head|tail|random]`:
  land one file as a train dataset and an eval dataset with disjoint rows; the
  JSON result carries `id` (train) and `eval_id`.
- Single-owner span-stamping resolver: every unit-kind and behaviour-key
  decision is made in one on-start resolver, verified by an enumerated
  invariant suite (`tests/test_stamping_invariants.py`). One run boundary per
  trace; nested run declarations resolve to `turn`.
- `task(key, unit="turn")` turn units: one turn span per (trace, behaviour
  key), shared across re-entries so a phase's non-contiguous activity lands in
  one scoring unit; closes when the run-boundary span ends.
- `overmind.run(...)`: run-lifecycle bracket as context manager and decorator —
  capability identity, entry-point run span, intent, conversation id, tags,
  error status, flush on exit, and a handle that delivers the terminal payload.
- `overmind.integrations.langgraph.bind()`: declarative LangGraph node →
  behaviour-turn binding with per-node overrides, opt-outs, code-identity
  anchoring of function-backed nodes, and a `deliver=` node.
- `providers=["langchain"]`: LangChain + LangGraph span coverage through the
  OpenInference instrumentor (`overmind[tracing]` extra).
- `init(providers="auto")`: detect installed target libraries and enable every
  provider whose instrumentor is also present; resolved list logged at INFO.
- `init(debug=True)`: one-line setup summary (endpoint, identity, enabled
  instrumentors, export mode) plus DEBUG logging for the `overmind` logger.
- `py.typed`: the package ships type information; decorators preserve wrapped
  signatures.
- Docs: the pinned wire contract in [tracing-attributes](docs/tracing-attributes.md);
  integration guidance lives in the `overmind` skill's telemetry reference.

### Removed

- `observe_safe` — use `observe(capture="none")` (or `init(redact_keys=...)`).
- `function`, `conversation`, `get_tracer`, `set_agent_id`, `set_agent_name`,
  `set_project_id` are no longer exported; identity rides on
  `init(capability_id=...)` or a `capability(name, id=...)` scope.
- `start_child_span` is private; open child spans with `start_span`.
- Wire attributes renamed `overmind.agent.*` → `overmind.capability.*` with no
  aliases; `OVERMIND_AGENT_ID` / `OVERMIND_AGENT_NAME` are inert — use
  `OVERMIND_CAPABILITY_ID` / `OVERMIND_CAPABILITY_NAME`.

### Changed

- `overmind init --ide cursor` installs skills to `.agents/skills`, shared with
  Codex, and removes earlier Overmind copies from `.cursor/skills`.
- `overmind skills sync` replaces each installed skill directory, so files
  removed from a skill no longer linger.
- The Claude Code plugin declares its MCP server as `type: http`; it was
  dropped at load before. The shared plugin MCP file is now `mcp.json`.
- `overmind init --ide claude` writes the Overmind MCP server to Claude Code's
  local scope (`~/.claude.json`) instead of `.mcp.json`, so sync works in
  repositories that commit `.mcp.json`. It no longer writes
  `.claude/commands/`; `/overmind <step>` routes through the skill.
- `overmind init` no longer persists the account bootstrap key. The first
  `overmind sync` stores the final project-scoped key in the ignored
  `.overmind/credentials.toml` sidecar and refreshes every initialized IDE MCP
  entry, so authentication survives process and IDE restarts without re-init.
- Packaging: default `pip install overmind` is the CLI. OpenTelemetry,
  LangChain, and HTTP instrumentors live on `overmind[tracing]` so an
  existing OTel pin does not clash. litellm is no longer a dependency.
- `genai.cost` is no longer computed by the SDK. The server prices each LLM
  span from its model and token counts at ingest; a provider-reported
  `genai.cost` is kept as sent.
- Orphan-span suppression: a `function` span that starts a new trace outside
  any run boundary (no parent, no unit declaration) is no longer exported —
  the platform quarantines such fragments as noise. A warning is logged once;
  `init(export_orphan_spans=True)` restores the old behaviour.
