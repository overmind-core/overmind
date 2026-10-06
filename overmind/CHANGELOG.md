# Changelog

Notable changes to the `overmind` package. The span-attribute wire contract is
pinned separately in [`docs/tracing-attributes.md`](docs/tracing-attributes.md);
entries here cover the SDK surface.

## Unreleased

### Changed

- `overmind dataset upload` inspects the upload before creating the dataset; the
  server now refuses an uninspected upload, so older CLI versions must upgrade.

### Added

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
