# Local repository setup

Use this fallback for the local work behind the native
`instrument-repository` and `investigate-capability` prompts. The MCP server
cannot scan or edit a repository.

**Prerequisite:** project connection in [onboard.md](onboard.md) finished (`python -m overmind init` then `python -m overmind sync`, followed by the coding-agent reload). Do not start this scan before the Console project exists.

## Workflow

**Language first.** Before anything else, detect whether the repository is Python or
JavaScript/TypeScript. For JavaScript/TypeScript, read [javascript.md](javascript.md)
now: it replaces the runner, the qualname and prompt rules and the whole **Instrument**
section (OpenTelemetry export, not decorators), and everything else here still applies.

Read [onboarding-progress.md](onboarding-progress.md) from the installed skill's
`references/` directory before scanning. Show its
full roadmap and data disclosure, then follow stages 3–10 with its numbered
conversation updates. Keep installation and connection marked complete only
when verified. Do not invent an MCP scan-status tool or resource.

Before scanning, repeat [the project runtime checks](onboard.md#verify-the-project-runtime),
including on a post-reload continuation. Use its isolated tooling recovery if the
application cannot install compatible tooling;
preserve any existing project and credentials instead of repeating bootstrap.
For every CLI command below, use `uv run python -m overmind` for uv,
`poetry run python -m overmind` for Poetry, or the virtual environment's
`python -m overmind`, or the verified isolated tooling runner from onboarding.
Run Python conversion with that same runner. Keep the repository root as the working
directory. An isolated CLI is explicitly allowed; do not resolve an unrelated global executable.
Verify tracing separately in the application environment before adding imports or
decorators. A tracing dependency conflict leaves instrumentation pending, but must
not prevent scanning, conversion or capability sync.

Run `python -m overmind chassis` **before** the prompt. It records the repository, branch, commit and working-tree fingerprint in `.overmind/scan.json`. Keep source files unchanged until conversion; if conversion reports a changed checkout, restart the scan. Generated `overmind.toml`, `overmind_capabilities.json` and `.overmind/` files are excluded from the fingerprint. Follow the **system prompt below in full**. When the JSON is complete and valid, convert it to `overmind.toml` (see **Persist**), delete `overmind_capabilities.json` after a successful toml conversion, run `python -m overmind sync`, then instrument the repository and sync again (see **Instrument**). Do not hand-author the toml. Judge expectations are not written to the toml: they are declared in code on the decorators. Only deterministic checks go in the toml's `eval_matrix`. `convert_json_to_toml` fills prompt spans, drops fabricated anchors and unverifiable provenance, and stamps `trajectory_map[].verified`.

Before the prompt: if `overmind.toml` exists, read it. Preserve `base-url`, `project-id`, `project-name`, and any capability `id` already assigned by a prior sync. Those rows are bind targets (`known_capabilities`): reuse exact `name` / `slug` / `id` when the purpose matches. Capabilities with `archived = true` are leftovers — reuse their `id` only when this scan still finds that purpose (that remounts them). Do not copy unmatched archived tables into the new current set. If the file is missing, seed a skeleton or tell the user to run `python -m overmind init`.

After the prompt: map the analysis schema onto toml (see **Persist**), run `python -m overmind sync`, then apply the decorators (see **Instrument**):

- **Project credential missing** — leave the toml on disk and stop. Ask the user to repeat the Console onboarding paste so sync receives a temporary account key.
- **`project-id` present** — run `python -m overmind sync` as usual (typical after onboarding bootstrap already synced once post-init).
- **`project-id` missing + account-scoped API key** — `python -m overmind sync` creates a project via `POST /api/projects/` (name from `project-name` in `overmind.toml`, seeded at init from the repo directory), writes `project-id` into `overmind.toml`, then pushes. Do not invent a UUID yourself. To recreate: delete the console project and clear `project-id` in the toml; keep `project-name` as the repo directory name — never `repo_summary`.
- **`project-id` missing + project-scoped API key** — stop. Project-scoped keys are pinned and cannot mint projects. Tell the user to paste their console `project-id` into `overmind.toml`, or switch to an account-scoped key.

Never invent project or capability UUIDs by hand.

______________________________________________________________________

You are mapping the AI surface of this local repository: one product — the customer's agent — made of distinct AI capabilities. Find the capabilities and, for EACH one, extract a rich, source-grounded **Capability Card** that another system will use to interpret that capability's production output data.

A *capability* is ONE PURPOSE: the smallest cluster of LLM work the customer would name, ship, or fail as a unit. It is judged from the product entry (CLI command, HTTP handler, the top-level class a user starts) and the harness that serves it — never from how many functions call a model. When you are unsure whether something is one capability or two, report ONE: the customer can split a capability later, but a phantom node misrepresents their product. Apply the Definition / Merge / Split / Exclude rules below.

Existing capabilities in overmind.toml are bind targets: reuse exact `name` / `slug` / `id` when the purpose matches. Do not re-emit unmatched archived leftovers.

Your working directory is the repository root. Follow the stages and conversation progress rules above:

- **Discover capabilities** — identify the capabilities: the purposes the product serves, judged from product entries and harnesses over the evidence above. For each capability, note its modes/tasks (stages or roles inside the same purpose) and any single-shot LLM utilities it calls internally — fold those into `modes` / `llm_utilities`; never promote a task, role, or utility to a capability.
- **Describe capabilities** — for each distinct capability, reconstruct what it actually DOES and the shape of its inputs/outputs.
- **Resolve model and prompt selection** — follow explicit arguments, configuration/environment overrides, constructor defaults and fallbacks through to the actual model ID and prompt-loader conditions. A provider/class name does not prove which template is selected. Trace the no-override default through every predicate; record that reasoning and source spans in `model_resolution` and `prompt_resolution`. Keep the source default separate from the unknown active runtime model. Capture alternate templates/modes, override and extension behavior separately. For a literal default template, set `prompt_resolution.default_span`, leave `system_prompt` and `system_prompt_span` empty, and supply `bindings` only when the source uses Python `str.format` (including `{}` for escape-only formatting). The converter reads and renders the template without executing repository code. Other builders remain agent-reconstructed text with an explicit `unresolved` explanation of why static literal rendering is unavailable; never invent runtime values.
- **Audit scan coverage** — work from a complete source inventory, not the first few symbols in the capped chassis digest. Enumerate the registered tools, registration/availability gates, implementations and caller result handling. For each tool, describe its supported outcomes (including a straight-through success where there is no conditional branch), the source evidence and what runs next. Record real error, retry, page/state-change and completion alternatives separately. Keep observation/context, model invocation, dispatch and result processing as distinct steps when the source separates them. Do not collapse those steps onto the entrypoint anchor. Before conversion, report registered tools versus described tools, tools with source-backed outcomes, execution steps, and unresolved source questions. Counts measure coverage, not semantic correctness; do not invent branches to meet a quota. When refreshing an existing scan, inspect lost tools, branches or anchors against the source and explain genuine removals rather than silently replacing a rich map with a summary.
- **Verify source references** — for each claim in the card, record the real source location it came from as `relative/path.py#Lstart-Lend`.
- **Review semantic coverage** — before conversion, revisit implementation and caller source for each outcome that changes the target, success state or continuation. Keep successful versus unsuccessful completion separate even if both set a done flag; keep default/root targets versus explicit container targets separate even when they share a dispatcher. Preserve format variants as additional distinctions, not replacements for those outcomes. Follow utility wrappers to their real model-calling implementations (including compaction, extraction and judging), include those definitions in the parent's `anchors`, and copy their exact qualnames into each utility's `anchors`. Cite unavailable external/dynamic implementations explicitly in `unresolved` rather than claiming a wrapper proves them. Compare against an existing snapshot only after this source review; source wins when the old snapshot is inaccurate.
- **Choose deterministic checks** — for each capability, list the checkable invariants the card states (see `eval_matrix` below). Judge criteria are not part of the snapshot: they become `expectations` on decorators in **Instrument**.
- **Write snapshot** — write the JSON file INCREMENTALLY. First write the file with `repo_summary`, `llm_utilities` and an empty `capabilities` array. Then add capabilities ONE AT A TIME, each with its own separate edit of the file, keeping the JSON valid after every edit. Report the number of cards successfully written and the next capability being written. Never emit the whole file in a single write when there is more than one capability. Continue with conversion and sync under **Persist** and decorators under **Instrument**; writing the JSON does not finish setup.

The intermediate output is a single JSON file named `overmind_capabilities.json` in the repository root, built incrementally as described under **Write snapshot**, with this exact schema:

```json
{
  "language": "python | typescript — the language this scan covers (typescript includes JavaScript); omit for a Python repository",
  "repo_summary": "One paragraph technical overview of the codebase and its AI components",
  "capabilities": [
    {
      "name": "Human-readable capability name",
      "slug_hint": "url-safe-short-name",
      "description": "What this capability does",
      "source_path": "path/to/main/module.py or entry file",
      "model": "primary model id if known, else empty string",
      "entrypoint_fn": "stable identity: function/class name, OR for multi-agent frameworks the Agent variable name or role id (e.g. researcher, clinical_summarizer) — NOT the crew/pipeline kickoff",
      "system_prompt_span": "relative/path.py#Lstart-Lend — set this ONLY when the prompt exists as a literal string or template in source, and give the span of that literal. Leave it EMPTY when the prompt is built by code; a span pointing at a function or class is wrong and is discarded.",
      "system_prompt": "This capability's full system/developer prompt. Leave empty ONLY when system_prompt_span points at a literal the platform can read verbatim. Whenever the prompt is assembled at runtime — built by a function, f-strings, concatenation, or a template filled at call time — reconstruct the assembled prompt text here, showing placeholders like {variable} for the parts supplied at runtime.",
      "system_prompt_excerpt": "A short key excerpt from the system prompt (a few lines) for compact display — NOT a replacement for the full system_prompt above",
      "tools_summary": "Brief list of tools/capabilities",
      "decision_logic": "How the capability decides what to do",
      "policy_markdown": "Markdown summary of constraints/policies for this capability",
      "capability_description": {
        "purpose": "...",
        "inputs": "...",
        "outputs": "..."
      },
      "modes": [
        { "name": "mode/task name (e.g. analysis, fix, transform; or rubric, pairwise, cascade)", "entrypoint_fn": "fn/class for this mode", "source_path": "path for this mode", "prompt_builder": "prompt builder fn if distinct (optional)", "purpose": "one line: what this task does", "routing": "when this task is selected — the input condition/decision that routes to it, else empty string", "model": "per-task model id if it differs from the capability default, else empty string", "output": "short description of this task's distinct output shape (e.g. analyze->findings, fix->patches, transform->dataset), else empty string", "prompt_span": "relative/path.py#Lstart-Lend — ONLY when THIS task's prompt is a literal in source; empty string when it is built by code", "prompt": "this task's COMPLETE prompt text, with {placeholders} for runtime parts; empty only when prompt_span is set", "prompt_excerpt": "one or two lines lifted from the prompt for compact display — a companion to `prompt`, never a replacement" }
      ],
      "capability_card": {
        "task": "One precise sentence: the task this capability performs on each input.",
        "model_resolution": { "selection": "Source-backed argument/configuration/default/fallback precedence; distinguish the default from the active runtime model", "default_model": "Exact source default, or empty when unknown", "unresolved": "Why no static default exists, if applicable; never a substitute for reading available source", "provenance": ["relative/path.py#L10-L40"] },
        "prompt_resolution": { "selection": "Trace the default model and settings through the actual template-selection predicates; describe overrides/extensions and alternate modes", "default_span": "relative/template.md#L1-L100 or a literal's source span; empty for dynamic/non-literal assembly", "unresolved": "Why literal rendering cannot resolve this prompt, if applicable; reconstruct known text in system_prompt and preserve runtime placeholders", "provenance": ["relative/loader.py#L10-L40"] },
        "modality": "text | tabular | image | audio | code | multimodal",
        "domain": "The subject domain it operates in (e.g. 'clinical question answering', 'data-quality analysis').",
        "input_schema": { "field_name": "type + what this input field is" },
        "output_fields": { "field_name": "type + what this output field is" },
        "expected_output": {
          "description": "What a GOOD output row looks like for this capability — the semantics a reviewer would use to judge it correct/high-quality.",
          "example": "A representative good output value or object (or null).",
          "quality_signals": ["concrete signals that distinguish a good output from a bad one"]
        },
        "tool_spec": [
          { "name": "tool name", "purpose": "what the capability uses it for", "args": "key arguments (flat summary string)", "side_effect": "read | write | external | none — read-only vs mutates state vs external/network/paid call vs pure (the key risk/trust signal)", "returns": "short description of what the tool returns to the capability", "arguments": [ { "name": "arg name", "type": "arg type", "required": true, "description": "what this arg is" } ], "branches": [ { "condition": "a source-backed selection rule or execution branch", "basis": "code | prompt", "action": "what this branch does", "outcome": "its local result or side effect", "next": "what executes next: queued action, new decision, retry, handoff or run completion; empty when unknown", "transitions": [ { "target": "exact destination step anchor, or terminal", "condition": "source-backed continuation condition" } ], "provenance": ["relative/path.py#L10-L40"] } ], "integration": "what it talks to (e.g. Postgres, GitHub API, vector store, MCP server, a model), else empty string", "cluster": "semantic capability cluster this tool belongs to — a short noun phrase grouping tools of similar function (e.g. 'graph exploration', 'dataset workshop', 'finetune lifecycle'); tools in the same cluster serve similar tasks; empty string only for a one-off tool no sibling shares a function with", "provenance": ["relative/path.py#L10-L40"] }
        ],
        "vocabulary": { "domain_term": "what it means in this product" },
        "success_criteria": ["what counts as the capability succeeding on a row"],
        "failure_modes": ["concrete ways the capability's output goes wrong"],
        "output_schema": {
          "required_keys": ["output keys the code REQUIRES to be present"],
          "properties": { "key_name": "type/shape the code enforces for this key (e.g. 'list[str], non-empty', 'enum: pass|fail', 'float in [0,1]')" },
          "provenance": ["relative/path.py#L10-L40"]
        },
        "constraints": [
          { "rule": "one checkable invariant the capability must obey (e.g. 'call report_stage exactly once per phase', 'at most 30 tool calls per run')", "type": "tool_discipline | budget | ordering | output_format", "params": { "param_name": "machine-checkable parameter value (e.g. max_calls: 30)" }, "provenance": ["relative/path.py#L10-L40"] }
        ],
        "tool_protocol": [
          { "rule": "what a valid tool sequence looks like (e.g. 'evidence must come from get_rows_by_id/sample_rows before it is cited')", "tools": ["tool names this rule governs"], "kind": "precondition | ordering | evidence", "provenance": ["relative/path.py#L10-L40"] }
        ],
        "trajectory_map": [
          { "id": "kebab-case-entry-id", "name": "Human-readable name", "mode": "the matching `modes` entry's `name` copied EXACTLY when this entry describes that task; empty string otherwise", "claim": "code_path | declared_task | decision_surface — the strongest claim the code supports for this entry (see Trajectory map rules)", "prompt_quote": "declared_task only — the prompt/tool-purpose text this entry derives from, quoted; empty string otherwise", "routing": "the decision that puts execution on this entry, in the code's own terms (branch condition, mode selection, gate trigger)", "sequence": [ { "step": "one intentional backbone action in plain language (e.g. 'assemble turn context', 'decide: gather evidence or answer', 'synthesize answer')", "kind": "agent_step | model_invocation", "anchors": ["anchor qualnames this step executes, copied EXACTLY from the `anchors` artifact below (required for decision_surface steps; otherwise [] when none)"], "input": "what enters this execution step (e.g. user message, page state, queued actions or tool results); empty if not captured", "action": "what this step does with its input (prepare context, select actions, dispatch tools, record results); empty if not captured", "output": "the output and its destination, including conditional continuations or run completion; empty if not captured", "transitions": [ { "target": "exact destination step anchor, or terminal", "condition": "source-backed continuation condition" } ], "may_use": [ { "tool": "a tool_spec tool the CODE binds at this step (a real subset the harness restricts to, e.g. gated tools at a confirm gate) — NEVER a guessed intent-to-tool mapping; on a decision_surface model_invocation leave [] (its option space is the whole tool_spec)", "when": "the code/prompt condition that triggers it, as a readable clause" } ] } ], "anchors": ["ordered anchor qualnames this entry executes, each copied EXACTLY from the `anchors` artifact below"], "tools": ["tool names every run of this entry touches (from tool_spec); conditional tools belong in a step's may_use instead"], "terminal": { "kind": "emits_record | returns_empty | escalates | error_exit", "description": "what the user concretely receives at the end (the output artifact, not just the kind)" }, "divergences": ["sibling entry ids reachable from this entry's decision points"], "provenance": ["relative/path.py#L10-L40"] }
        ],
        "anchors": [
          { "qualname": "the function's Python identity as `<module dotted path>.<__qualname__>` — module path derived from the file path relative to the repo/package root (strip .py, '/' becomes '.'), qualname including any enclosing class (e.g. 'app.agents.triage.TriageAgent.run')", "kind": "entry_point | tool | function", "file": "relative/path.py#Lstart-Lend — the span of this function's definition" }
        ],
        "provenance": {
          "paths": ["relative/path/to/file.py#L12-L48", "other/file.py#L80-L95"]
        }
      },
      "eval_matrix": [
        {
          "name": "Short, human-readable check name shown in the eval matrix UI",
          "type": "deterministic",
          "check": "EXACT catalog name from the deterministic checks list below",
          "params": { "CONSTANT_NAME": "value for that check's constant, copied from the card" },
          "span_name": "the decorated function's __qualname__ when the check fits one function (e.g. Agent.run); empty string scopes it to every span of the capability",
          "span_type": "optional: llm_call | tool_call | retrieval | workflow; empty string for any",
          "rationale": "One sentence: the card fact this check enforces"
        }
      ]
    }
  ],
  "llm_utilities": [
    {
      "name": "utility name (e.g. rubric_compiler, criteria_inference, prompt_rewriter)",
      "purpose": "the single-shot job it performs as a library call",
      "called_by": "the parent capability's `slug_hint` copied EXACTLY (fall back to the parent capability's exact `name` only if it has no slug_hint) — never a paraphrase or description",
      "source_path": "path/to/util.py",
      "entrypoint_fn": "fn/class name",
      "anchors": ["Exact implementation qualnames copied from the parent capability_card.anchors; follow wrappers to the model-calling helper"],
      "unresolved": "Empty for source-backed utilities; otherwise explain the specific unavailable external implementation",
      "model": "model id if known, else empty string",
      "io_contract": "short 'in: ... -> out: ...' description of what it consumes and produces",
      "cardinality": "per_run | per_row | per_candidate | unknown — its cost driver (once per run, once per dataset row, once per scored candidate, or unknown)",
      "prompt_excerpt": "short excerpt of its single-shot prompt, else empty string",
      "structured_output": true,
      "provenance": { "paths": ["relative/path/to/util.py#L10-L30"] }
    }
  ]
}
```

Rules:

- `capabilities` is required. Distinct-capability discovery is the primary task; the `capability_card` is the deliverable that makes each capability usable downstream.
- **Definition.** A capability is one purpose the customer would name, ship, or fail as a unit, with its own prompt identity and harness. A single function that calls an LLM is NOT automatically a capability; neither is a shared transport (`call_llm`, a provider client, `.invoke`) nor a public helper method that happens to be testable on its own.
- **Merge.** Report ONE capability with entries in `modes` whenever the evidence shows one driver or state machine, one product entry, one tool-spec membership, sequential stages that consume each other's artifacts, or a shared session/run object — even when the stages have different prompts, different models, or different output shapes. Named roles inside one orchestration (a crew, a graph of workers, a supervisor with specialists) are modes of the ONE capability that orchestration serves, not sibling capabilities, unless each role is also its own product entry.
- **Split.** Report two capabilities only when they have independent product entries AND independent terminals AND no harness path between them except a shared utility. Sharing a model client, a prompt helper, or a tool registry is never a reason to split; it is never a reason to merge independent product entries either.
- **Identity (`entrypoint_fn`).** When several capabilities live in one file, set `entrypoint_fn` to the function, class, or variable that starts THAT purpose, so each capability has a distinct `source_path`+`entrypoint_fn`. Never use a shared pipeline kickoff as the entrypoint of more than one capability. For a mode, set the mode's own `entrypoint_fn`.
- **Exclude from top-level.** Single-shot internal LLM utilities (judges, rubric/criteria compilers, recommenders, schema/role inference, prompt rewriters) that are library calls inside a capability are NOT capabilities — list them under `llm_utilities`, folded to their parent via `called_by`. Exclude deterministic / non-LLM helpers entirely (do not list them anywhere).
- Every capability MUST include a `capability_card` and an `eval_matrix` (`[]` when the card states no checkable invariant). Fill `task`, `modality`, `domain`, `input_schema`, `output_fields`, and `expected_output` for every capability — these are required. `modes` and `llm_utilities` are optional (use `[]` when none apply).
- `expected_output.description` must describe what a correct/high-quality output ROW looks like for THIS capability, grounded in the code (not generic boilerplate).
- `capability_card.provenance.paths` must list at least one REAL source span you actually read, in the form `relative/path.py#Lstart-Lend`. Do not invent paths or line numbers.
- `input_schema` and `output_fields` map a field name to a short type + meaning. Prefer the field names the capability actually reads/writes (request/response keys, dataset columns, tool args).
- **Per-component detail.** For each `tool_spec` tool, set `side_effect` to one of `read|write|external|none` (the risk/trust signal: read-only vs mutates state vs external/network/paid call vs pure), describe what it `returns`, break `args` into a structured `arguments` list, name the `integration` it talks to (DB / API / vector store / MCP server / model) or `""`, and anchor it with `provenance` spans. For each `modes` task, fill `purpose` and its prompt — `prompt_span` or `prompt`, per **Prompt capture** below — plus `routing`/`model`/`output` when they apply, else `""`. For each `llm_utilities` helper, give its `io_contract`, a `cardinality` of `per_run|per_row|per_candidate|unknown`, a `prompt_excerpt`, and `structured_output` (true if it returns JSON/schema-structured output). Use the empty/`none`/`unknown` defaults rather than guessing.
- **Prompt capture.** Capture all readable prompt text the checkout supplies; explicitly identify unavailable runtime content. For a decision surface's default, use the model/prompt resolution procedure above, including constructor mutations to settings before the prompt loader runs. For other literal prompts, set `system_prompt_span` / `modes[].prompt_span` and leave the text field empty so the platform reads the source. For builders that cannot use `prompt_resolution.default_span` with optional `bindings`, reconstruct the assembled text into `system_prompt` / `modes[].prompt`, using `{placeholders}` for runtime parts and explaining unavailable values. Never aim a span at a `def`/`class`: source code is not a prompt. `system_prompt_excerpt` / `prompt_excerpt` stay short companions, never a substitute. A source default describes a configuration, not proof of the prompt used in a live run.
- **Output contract as data.** `output_schema`, `constraints`, and `tool_protocol` are required only when discoverable in source — emit `{}` for `output_schema` and `[]` for the lists when the repo declares none. For `output_schema`, copy the REAL schema the code enforces (a JSON-schema/pydantic model/validator/required-keys check you actually read) — do not invent keys or types; empty object when none exists in source. For `constraints`, distill checkable invariants from prompts/policies/driver code (e.g. 'call report_stage once per phase' → `type: "tool_discipline"`; a tool budget of N → `type: "budget"`, `params: {"max_calls": N}`) and put machine-checkable values in `params`. For `tool_protocol`, capture what a valid tool sequence looks like (preconditions, orderings, evidence-before-citation rules) with the governed tool names in `tools`. Provenance is MANDATORY per entry — every `output_schema`/`constraints`/`tool_protocol` item must cite at least one real `relative/path.py#Lstart-Lend` span you actually read, same format as `capability_card.provenance.paths`.
- **Trajectory map (claim-classed).** Walk each capability's entrypoint control flow in the SOURCE — pure static analysis, assume nothing about tracing or instrumentation — and emit, per region of the capability, the STRONGEST claim the code supports. Every entry carries `claim`, exactly one of: (1) `code_path` — control flow determines the route (a pipeline, a staged workflow, a guard branch, a retry/park/resume, a confirmation gate): the entry is a CFG-backed task whose backbone is stable across executions; cite the branch's source span. (2) `declared_task` — the architecture itself NAMES the unit of work: a `modes[]` task, a router arm, a graph/crew node, or a prompt-declared duty with detectable structure (a fixed refusal string, a declared gating policy, a plan-approval flow); quote the naming text in `prompt_quote` (or cite the code span) — you are READING the builder's own task decomposition out of the artifact, never proposing one. When a `trajectory_map` entry describes the same unit as a `modes[]` entry, set its `mode` to that mode's `name` copied EXACTLY and its `id` to the kebab-case slug of that name, so the card entry and the scored task share one identity. (3) `decision_surface` — a MODEL decides the route at runtime (a tool loop, free-form dispatch, a driven coding/sub-agent choosing among MCP or registered tools): the entry records the execution loop — the deterministic prelude, the model_invocation with its full In/Action/Out contract, execution and result-handling steps, and the postlude to its terminals. The available action branches live in `tool_spec[].branches` and must not disappear into a generic decision box. Its option space is the whole `tool_spec` (leave that step's `may_use` empty); task identity past this point is determined at runtime by user intent and model policy — it is NOT in the code, and you MUST NOT enumerate intent tasks over it ("answer a question about X", "investigate Y"). One decision_surface entry per distinct model decision point, not per imagined intent. CLAIM PRECEDENCE: an entry whose INTERIOR contains a free tool loop takes `decision_surface` even when its trigger is architecturally named — put the naming text in `prompt_quote` and the trigger in `routing`, never downgrade the claim to `declared_task`: `declared_task` is only for entries whose whole backbone is model-decision-free. HARD RULE: any entry, task name, or `when` condition that is neither a real code branch, a real architectural name, nor a quoted declaration is fabrication — leave it out. Each entry models input → ordered BACKBONE STEPS → outcome: the `sequence` is the ordered list of intentional work units, stable across executions. Code-bound non-determinism lives WITHIN a step as `may_use` slots (`{"tool", "when"}`) ONLY when the harness genuinely binds that subset (e.g. gated tools at a confirm gate) — never as a guessed intent-to-tool table. A `step` is a deliberate work unit in plain language — never an exception type or class name (`ValueError`, `HTTPException`, …), never incidental control-flow or harness bookkeeping. Terminals (`error_exit`, `escalates`, …) stay as *outcomes* on `terminal`, not as sequence steps; the terminal `description` states what the user CONCRETELY receives. **Model invocations are explicit backbone steps**: every point where control passes to the model is a step with `kind: "model_invocation"` carrying `input` (what is passed in: message, history, prior tool results, tool schemas, prompt scope), `action` (what the model decides), `output` (EVERY possible output and its destination: tool_calls → capability dispatch, results feed back in; gated call → confirmation branch; final answer → output surface). A tool-results-feed-back loop stays ONE step — never unroll iterations. Deterministic harness work keeps `kind: "agent_step"` with an empty contract. Each entry carries a stable kebab-case `id`, the `routing` decision that selects it, the `tools` EVERY run touches, its `terminal` (`kind` EXACTLY one of `emits_record` — returns/emits a real output record; `returns_empty` — deliberately returns nothing (a declared refusal, not a failure); `escalates` — hands off to a human or another system; `error_exit` — raises/aborts), and `divergences` listing sibling entry ids reachable from its decision points. Every entry MUST cite at least one real `relative/path.py#Lstart-Lend` span. A deterministic single-task pipeline is the degenerate case: one `code_path` entry and no decision_surface. A pure tool loop is the other: harness `code_path`/`declared_task` branches (gates, parks, resumptions) plus ONE `decision_surface` — for such capabilities `tool_spec` must enumerate EVERY tool registered in the source's registry/list, not a sample; the dispatcher function is an anchor, not a tool. Emit `[]` only when the capability performs no discoverable work.
- **Deterministic checks (`eval_matrix`).** Each entry is a sandboxed script from the platform's catalog with its constants set from a fact the card already states; it scores every matching span without a model call. Entries are `type: "deterministic"` only. Never write an `llm_judge_custom` or `managed` entry: every judged expectation is declared on a decorator in **Instrument**. At most 5 entries per capability; skip a check the card does not motivate, and never invent a key, pattern or number. `output_is_valid_json` and `tool_selection` already run on every span, so do not list them. Catalog (`check`: constants):
  - `json_required_keys`: `REQUIRED_KEYS` (list of keys) from `output_schema.required_keys`.
  - `schema_field_conformance`: `FIELDS` (field name to rules `type`, `enum`, `min`, `max`, `pattern`, `nullable`) from `output_schema.properties`.
  - `goal_state_match`: `GOAL_MARKERS` (strings every good output contains) from a declared fixed marker or phrase.
  - `contains`: `NEEDLE`, `CASE_INSENSITIVE`; `regex`: `PATTERN`, `MUST_MATCH` (`false` gates a failure mode that must never appear); from an `output_format` constraint or a fixed string.
  - `tool_args_match`: `EXPECTED_CALLS` (list of `{"name", "arguments"}`) and `MODE` (`subset`, `exact`, `superset`); only when the code or prompt fixes a tool's arguments.
  - `llm_call_budget`: `MAX_LLM_CALLS` from a stated cap on model calls; `latency_budget`: `MAX_SECONDS` and `cost_budget`: `MAX_COST`, both with `span_type: "llm_call"`, only when the source states the budget.
  - `exact_match` needs a reference answer per row and belongs to evaluation runs, not here.
- **Tool branches and local outcomes.** A model-selected tool is still an executable branch. For every `tool_spec` tool, inspect its registration/availability gates, implementation branches and result handling in the caller. Record the meaningful alternatives in `branches`: a source-backed `condition`, its `basis` (`code` for enforced dispatch/parameter/guard logic, `prompt` for documented selection guidance), the `action`, local `outcome`, and `next` transition. Cite the relevant implementation and caller spans in each branch's `provenance`. A tool result, state change, confirmation request, handoff or retry is a local outcome even when the run continues. Distinguish stopping a queued action batch from ending the agent run. On tool branches and execution steps, record `transitions: [{target, condition}]` for source-backed continuations. `target` is an exact anchor on the destination trajectory step, or `terminal` for the owning path result. Include result processing, retries and loop-back edges; do not convert continuation prose into guessed destinations. A normal tool return continues execution, while a finish action may still pass through result processing before the run ends. Preserve missing next-step information as empty; never infer routing from a tool's name alone. Keep model selection contingent: available tools and prompt guidance establish possible actions, not guaranteed tasks or fixed intent-to-tool rules. Cover the source-backed branches without enumerating imagined end-user tasks or every loop iteration.
- **Tool clusters.** Assign every `tool_spec` entry a `cluster`: a short noun phrase grouping tools of similar function (e.g. 'graph exploration', 'dataset workshop', 'finetune lifecycle', 'deployment & serving'). Tools in one cluster serve similar tasks and render together; derive clusters from the tools' own purposes/naming, keep them stable and few (roughly 3-12 for a large registry), and reuse the exact same cluster string for every member.
- **Code-symbol anchors.** For each capability, emit an `anchors` artifact: the concrete functions that mark progress along its trajectories. `qualname` is the function's Python identity — the module dotted path derived from the file location (strip `.py`, `/` becomes `.`, relative to the importable package root) joined with the function's `__qualname__` including any enclosing class. Copy names from the SOURCE exactly; these are checked deterministically against the AST and fabricated names are dropped. `kind` is `entry_point` for the capability's entrypoint(s), `tool` for tool functions, `function` for other trajectory constituents. `file` cites the definition span as `relative/path.py#Lstart-Lend`. Then every `trajectory_map` entry lists its ordered `anchors` — the subset of anchor qualnames that path executes, copied verbatim from the artifact. The entrypoint anchor comes first. Prefer anchors that DISCRIMINATE between sibling paths.
- If the repository has no LLM-driven capability, return `"capabilities": []` with a clear `repo_summary` (you may still populate `llm_utilities`).
- Do NOT print the full JSON to chat — only write the file `overmind_capabilities.json`.
- Verify the file contains a valid `capabilities` array (each with a `capability_card`) before finishing.
- After writing the file, continue through **Persist** before giving the final summary.

**GROUND-TRUTH CHASSIS (deterministic AST facts — pre-extracted, verified).** You already ran `python -m overmind chassis`. The printed digest is GROUND TRUTH: transform it, together with your own reading of the source, into the capability cards and trajectory map — do not re-derive or contradict it. Copy every `anchors` qualname VERBATIM from this inventory (same spelling, same dotted path). After you write the JSON, `convert_json_to_toml` deterministically re-checks every trajectory entry against this chassis: an entry whose anchors are not call-graph reachable from its entry anchor is stamped unverified, and anchor names absent from the inventory are dropped. Symbols elided from the digest still exist in the source — read the file to recover their exact qualnames.

## Persist

Conversion validates decision-surface detail before replacing `overmind.toml`: every described tool needs source-backed outcomes, model steps need input/action/output and verified step anchors, and supplied transitions must resolve to actual destination step anchors or `terminal`. Model/prompt resolution needs cited selection evidence and either a source default or a precise unresolved explanation. A declared default prompt is read from its span, rendered with optional `bindings`, and must match any supplied text. Utilities need implementation anchors or an explicit unavailable-source explanation. If conversion reports incomplete detail, continue inspecting the named source and repair the JSON, then convert again. This is scan work to finish, not a reason to ask the user to restart onboarding. Never add placeholder branches, remove tools, invent unresolved reasons or change the claim class to evade validation. If evidence really cannot be found, report the precise unresolved source question and retain the previous snapshot when replacing it would lose known detail. `verified` checks static anchor reachability only; it does not certify correct template selection, complete branch coverage or observed runtime behavior. The native agent must complete the semantic coverage review even when conversion passes.

When `overmind_capabilities.json` is valid:

```bash
python -c "from overmind.utils import convert_json_to_toml; convert_json_to_toml('overmind_capabilities.json', 'overmind.toml')"
```

Delete `overmind_capabilities.json` after a successful toml conversion.

```bash
python -m overmind sync
```

`convert_json_to_toml` preserves existing `base-url` / `project-id` / capability `id` values from any prior `overmind.toml`, fills `system_prompt` from cited spans, drops fabricated anchors and unverifiable provenance, and stamps `trajectory_map[].verified` against the AST chassis of the JSON's parent directory. Conversion attaches the verified scan provenance to `overmind.toml`. Sync preserves that original revision and scan time, even if the checkout changes afterward; the server records sync time separately. The Console Agent header and `overmind://project/current` expose the snapshot. `python -m overmind sync` reads the local project credential, POSTs the snapshot to `/api/v1/sync`, and writes assigned ids back. If `project-id` was empty and the temporary key is account-scoped, sync creates the project first and persists the new id.

Sync upserts each capability's **Default** eval set from the toml's deterministic `eval_matrix` entries (one parameterised row each, scoped by `span_name` and `span_type`) plus the seeded checks that apply to its span types. Sync rejects the whole snapshot with a 400 that names an entry whose `check` is not in the catalog or whose `params` name a constant the check lacks; fix that entry and run `python -m overmind sync` again. The toml carries no judge expectations: they are declared in code (see **Instrument**), and the sync after instrumenting reads them from the decorators into the same Default set. Read `eval_preload` on `overmind://capabilities/{capability}`: `counts.checks` and `counts.expectations` report what the set holds, and `status: empty` means nothing applied. An older toml whose `eval_matrix` has llm or managed entries keeps working, but new scans do not write them. A rule the catalog cannot express can be added later as a custom row in the Console or with `create_eval_matrix_row` (rows are created and deleted, never edited); see [evals.md](evals.md#configure).

## Instrument

JavaScript/TypeScript repositories skip this section and follow
[javascript.md](javascript.md#instrument-with-opentelemetry-not-an-sdk).

Run this after `python -m overmind sync` succeeds: the entry-point decorator carries the capability id, and only sync assigns it. The scan described the checkout as it was when you ran `python -m overmind chassis`; the edits below come afterwards, so spans cited in the toml refer to that revision. Everything the scan extracted is declared in code: each function, tool, retrieval step and model call the card names gets a decorator, and each decorator carries the intent and expectations the card implies for it.

1. Install the tracing extra: the decorators import OpenTelemetry, which plain `overmind` does not ship. Add `overmind[tracing]>=0.1.87` through the project's package manager (`uv add "overmind[tracing]>=0.1.87"`, `poetry add "overmind[tracing]>=0.1.87"`, or the requirements.txt line; an editable install keeps its path, `uv add --editable "<path>[tracing]"`). Older versions reject the `expectations` and `self_attrs` arguments at import. Then confirm with the project runner: `python -c "import overmind; overmind.observe"`. If the resolver reports a conflict, report it and stop before editing source.
1. Call `get_instrumentation_plan` with no capability. If it returns `human_action` or no placements, report the instruction and stop; do not guess targets.
1. Apply each placement's `required_decorator` verbatim at `target.qualname` in `target.file`, with `capability_id` exact, and add the import from `target.import_line`. Give each file one owner when delegating to subagents. Change no behavior.
1. Cover the rest of the card. The plan lists only the card's `anchors`; walk the card for everything else and decorate the function that implements it, reading `provenance` spans and the source to find it. Never stack two Overmind decorators on one function, skip a function the plan already decorated, and when one function fits several rows use the most specific: entry point, then tool, retrieval, workflow, function.
   - **Each `modes[]` task** that is not the capability's own entry: `@overmind.workflow()` on its `entrypoint_fn`.
   - **Each `tool_spec` tool**: `@overmind.tool()` on the function that runs it. A dispatcher that serves many tools by an action argument takes one decoration naming each call, `@overmind.tool(name=lambda self, action, **params: action)`, and the dispatcher is never the tool. Heavy runtime arguments (sessions, clients, handles) go in `ignore=[...]`.
   - **Each tool whose `integration` is a vector store, search index or document store**: `@overmind.retrieval()` instead of `tool`.
   - **Each `llm_utilities[]` helper**: `@overmind.observe()` on its `entrypoint_fn`.
   - **Every other `anchors[]` function and trajectory step**: `@overmind.observe()`.
   - **Model calls**: `overmind.init(providers="auto")` traces the supported provider clients, so a call through one needs nothing. For a call through a client it cannot patch, such as a raw HTTP request, put it in a small function and decorate that with `@overmind.observe(type="llm", capture="messages")` when its `messages` argument is a chat list, else `@overmind.observe(type="llm")`.
   - List in the final summary every card item you could not map to a function, with the reason.
1. Without `overmind.init()` the decorators are no-ops. If the repository owns a process entry (a server, CLI or script `main`), add one `overmind.init(providers="auto")` call there, before any provider client is built. For a library with no process entry, change nothing and report that the host application must call it.
1. Capture each entry point's input. The decorator records the call's arguments but not `self`. When the entry point is a method that takes no arguments carrying the request (a researcher or agent object built with its query), add `self_attrs=["<attribute>"]` naming the instance attributes that hold it, read from `__init__`. Never name a secret or a client handle.
1. Declare what the card says on the decorators. These `expectations` are the only judge criteria: never add an llm or managed metric to the toml. Each is one concrete, checkable sentence about the span's output, under 1000 characters, with no data values or secrets; at most 5 per decorator, most important first, merging near duplicates.
   - **Entry point:** `intent="<card task>"`, plus one expectation per `success_criteria` item, per `failure_modes` item (phrased as the claim that it does not happen), and one from `expected_output.description`.
   - **Workflow (a mode):** one expectation from the mode's `output` when it states one.
   - **Tool and retrieval:** one expectation per distinct `returns` or `output_schema` claim; omit when the card states none.
   - **`llm_utilities` helper:** one expectation from the output side of `io_contract` when `structured_output` is true.
   - **`reference_trajectory=[...]`:** only on an entry point whose `code_path` trajectory entry names an ordered tool sequence; copy the tool names from that entry.
   - `output_schema`, `constraints` and `tool_protocol` are not expectations: the checkable ones already went to the toml's deterministic `eval_matrix`. Skip what the seeded checks cover (valid JSON, tool selection).
1. Run `python -m overmind sync` again. It reads the `expectations` on the entry-point, workflow and `observe` decorators and adds them to each capability's Default eval set, so eval runs, `overmind backtest`, the optimizer and training judge what live scoring judges. Read `eval_preload.counts.expectations` on `overmind://capabilities/{capability}`; zero after decorating means the entry point's `capability_id` is missing or not the synced id.
1. Do not run the application. Verification needs the approved run described in [instrumentation.md](instrumentation.md#verify).

Never upload the repository. Never invent project or capability UUIDs.

## Local execution and data transfer

Repository commands and file edits execute on this machine. Instrumentation edits source files; list the changed files in the final summary. Source read by the
coding agent enters its model context; local execution is not a data-residency
guarantee. Follow the disclosure and pre-sync handoff in
[onboarding-progress.md](onboarding-progress.md). Do not upload a repository
archive through MCP. Use MCP only with the intended project's authentication.
