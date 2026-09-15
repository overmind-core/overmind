# Local repository setup

Use this fallback for the local work behind the native
`instrument-repository` and `investigate-capability` prompts. The MCP server
cannot scan or edit a repository.

**Prerequisite:** phase 1 of [onboard.md](onboard.md) finished (`overmind init` then `overmind sync`). Do not start this scan before the Console project exists.

## Workflow

```
- [ ] 1. Read existing overmind.toml (if any) — keep base-url / project-id / capability ids
- [ ] 2. Run `overmind chassis` at the repo root; keep the printed digest as the GROUND-TRUTH CHASSIS
- [ ] 3. Run the system prompt below end-to-end (discovery → capability cards → provenance → eval matrix)
- [ ] 4. Write overmind_capabilities.json incrementally (valid JSON after every edit)
- [ ] 5. Persist: convert_json_to_toml → overmind.toml; delete overmind_capabilities.json after a successful toml conversion (do not hand-author the toml)
- [ ] 6. Ensure project-id (see below), then overmind sync
```

Run `overmind chassis` **before** the prompt. Follow the **system prompt below in full**. When the JSON is complete and valid, convert it to `overmind.toml` (see **Persist**), delete `overmind_capabilities.json` after a successful toml conversion, then run `overmind sync`. Do not hand-author the toml. `convert_json_to_toml` fills prompt spans, drops fabricated anchors and unverifiable provenance, and stamps `trajectory_map[].verified`.

Before the prompt: if `overmind.toml` exists, read it. Preserve `base-url`, `project-id`, `project-name`, and any capability `id` already assigned by a prior sync. Those rows are bind targets (`known_capabilities`): reuse exact `name` / `slug` / `id` when the purpose matches. Capabilities with `archived = true` are leftovers — reuse their `id` only when this scan still finds that purpose (that remounts them). Do not copy unmatched archived tables into the new current set. If the file is missing, seed a skeleton or tell the user to run `overmind init`.

After the prompt: map the analysis schema onto toml (see **Persist**), then run `overmind sync`:

- **Project credential missing** — leave the toml on disk and stop. Ask the user to repeat the Console onboarding paste so sync receives a temporary account key.
- **`project-id` present** — run `overmind sync` as usual (typical after onboarding bootstrap already synced once post-init).
- **`project-id` missing + account-scoped API key** — `overmind sync` creates a project via `POST /api/projects/` (name from `project-name` in `overmind.toml`, seeded at init from the repo directory), writes `project-id` into `overmind.toml`, then pushes. Do not invent a UUID yourself. To recreate: delete the console project and clear `project-id` in the toml; keep `project-name` as the repo directory name — never `repo_summary`.
- **`project-id` missing + project-scoped API key** — stop. Project-scoped keys are pinned and cannot mint projects. Tell the user to paste their console `project-id` into `overmind.toml`, or switch to an account-scoped key.

Never invent project or capability UUIDs by hand.

______________________________________________________________________

You are mapping the AI surface of this local repository: one product — the customer's agent — made of distinct AI capabilities. Find the capabilities and, for EACH one, extract a rich, source-grounded **Capability Card** that another system will use to interpret that capability's production output data.

A *capability* is ONE PURPOSE: the smallest cluster of LLM work the customer would name, ship, or fail as a unit. It is judged from the product entry (CLI command, HTTP handler, the top-level class a user starts) and the harness that serves it — never from how many functions call a model. When you are unsure whether something is one capability or two, report ONE: the customer can split a capability later, but a phantom node misrepresents their product. Apply the Definition / Merge / Split / Exclude rules below.

Existing capabilities in overmind.toml are bind targets: reuse exact `name` / `slug` / `id` when the purpose matches. Do not re-emit unmatched archived leftovers.

Your working directory is the repository root. Work in explicit stages and print a single progress line at the START of each stage (exactly, on its own line):

- `__STAGE__:discovery` — identify the capabilities: the purposes the product serves, judged from product entries and harnesses over the evidence above. For each capability, note its modes/tasks (stages or roles inside the same purpose) and any single-shot LLM utilities it calls internally — fold those into `modes` / `llm_utilities`; never promote a task, role, or utility to a capability.
- `__STAGE__:capability_card` — for each distinct capability, reconstruct what it actually DOES and the shape of its inputs/outputs.
- `__STAGE__:provenance` — for each claim in the card, record the real source location it came from as `relative/path.py#Lstart-Lend`.
- `__STAGE__:eval_matrix` — for each distinct capability, design a small, high-signal starter **eval matrix**: the handful of evaluators a reviewer would actually run to know whether this capability is doing its job. Use the capability's `capability_card` (task, expected_output, success_criteria, failure_modes, output contract, tool surface) as the source of truth. Obey the strict limits below (at most 5 metrics, exactly one bespoke LLM judge, all other slots filled from the platform's existing managed-metric library).
- `__STAGE__:writing` — write the JSON file INCREMENTALLY, then stop. First write the file with `repo_summary`, `llm_utilities` and an empty `capabilities` array. Then add capabilities ONE AT A TIME, each with its own separate edit of the file, keeping the JSON valid after every edit. Before each capability's edit, print `__NOTE__:Writing card <N> of <M>: <capability name>` on its own line. Never emit the whole file in a single write when there is more than one capability.

Two more progress sentinels, each on its own line, for the live scan display only:

- The moment you identify a distinct capability during discovery, print `__FOUND__:{"name": "Capability name", "file": "relative/path/to/its/main/file.py"}` — flat single-line JSON, one line per capability, when you first become confident it qualifies under the Definition rules. Do not print it for tasks, modes, or LLM utilities you fold into a parent.
- Occasionally (at most once every several tool calls), print `__NOTE__:<fact>` — one short factual observation about the codebase, at most 60 characters, plain statement of fact, no opinions or filler (example: `__NOTE__:LangGraph state machine in orchestrator.py`).

Sentinel lines (`__STAGE__`, `__FOUND__`, `__NOTE__`) are progress output only: never write them into `overmind_capabilities.json` or any other file.

The output is a single JSON file named `overmind_capabilities.json` in the repository root, built incrementally as described under `__STAGE__:writing`, with this exact schema:

```json
{
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
          { "name": "tool name", "purpose": "what the capability uses it for", "args": "key arguments (flat summary string)", "side_effect": "read | write | external | none — read-only vs mutates state vs external/network/paid call vs pure (the key risk/trust signal)", "returns": "short description of what the tool returns to the capability", "arguments": [ { "name": "arg name", "type": "arg type", "required": true, "description": "what this arg is" } ], "integration": "what it talks to (e.g. Postgres, GitHub API, vector store, MCP server, a model), else empty string", "cluster": "semantic capability cluster this tool belongs to — a short noun phrase grouping tools of similar function (e.g. 'graph exploration', 'dataset workshop', 'finetune lifecycle'); tools in the same cluster serve similar tasks; empty string only for a one-off tool no sibling shares a function with", "provenance": ["relative/path.py#L10-L40"] }
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
          { "id": "kebab-case-entry-id", "name": "Human-readable name", "mode": "the matching `modes` entry's `name` copied EXACTLY when this entry describes that task; empty string otherwise", "claim": "code_path | declared_task | decision_surface — the strongest claim the code supports for this entry (see Trajectory map rules)", "prompt_quote": "declared_task only — the prompt/tool-purpose text this entry derives from, quoted; empty string otherwise", "routing": "the decision that puts execution on this entry, in the code's own terms (branch condition, mode selection, gate trigger)", "sequence": [ { "step": "one intentional backbone action in plain language (e.g. 'assemble turn context', 'decide: gather evidence or answer', 'synthesize answer')", "kind": "agent_step | model_invocation", "anchors": ["anchor qualnames this step executes, copied EXACTLY from the `anchors` artifact below (optional, [] when none)"], "input": "model_invocation only — what is passed IN to the model at this point (e.g. user message + history + prior tool results + tool schemas), else empty string", "action": "model_invocation only — what the model does with it (decide whether to call tools or answer; select tool + arguments; synthesize final answer), else empty string", "output": "model_invocation only — every possible output and its destination (tool_calls -> capability dispatch, results feed back in; gated call -> confirmation branch; final answer -> output surface), else empty string", "may_use": [ { "tool": "a tool_spec tool the CODE binds at this step (a real subset the harness restricts to, e.g. gated tools at a confirm gate) — NEVER a guessed intent-to-tool mapping; on a decision_surface model_invocation leave [] (its option space is the whole tool_spec)", "when": "the code/prompt condition that triggers it, as a readable clause" } ] } ], "anchors": ["ordered anchor qualnames this entry executes, each copied EXACTLY from the `anchors` artifact below"], "tools": ["tool names every run of this entry touches (from tool_spec); conditional tools belong in a step's may_use instead"], "terminal": { "kind": "emits_record | returns_empty | escalates | error_exit", "description": "what the user concretely receives at the end (the output artifact, not just the kind)" }, "divergences": ["sibling entry ids reachable from this entry's decision points"], "provenance": ["relative/path.py#L10-L40"] }
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
          "name": "Short, human-readable metric name shown in the eval matrix UI",
          "type": "llm_judge_custom | managed",
          "managed_name": "EXACT name copied from the Available managed metrics list — REQUIRED when type=\"managed\", else empty string",
          "measures": "One sentence: the single quality dimension this metric checks for THIS capability's output",
          "rubric": "REQUIRED when type=\"llm_judge_custom\": a complete, self-contained grading rubric an LLM judge can apply to one output row, written from this capability's success_criteria / failure_modes / expected_output. Empty string for managed metrics.",
          "requires_reference": false,
          "rationale": "One sentence: why this metric matters for THIS capability (cite a success criterion or failure mode it guards against)"
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
- Every capability MUST include a `capability_card` and a non-empty `eval_matrix`. Fill `task`, `modality`, `domain`, `input_schema`, `output_fields`, and `expected_output` for every capability — these are required. `modes` and `llm_utilities` are optional (use `[]` when none apply).
- `expected_output.description` must describe what a correct/high-quality output ROW looks like for THIS capability, grounded in the code (not generic boilerplate).
- `capability_card.provenance.paths` must list at least one REAL source span you actually read, in the form `relative/path.py#Lstart-Lend`. Do not invent paths or line numbers.
- `input_schema` and `output_fields` map a field name to a short type + meaning. Prefer the field names the capability actually reads/writes (request/response keys, dataset columns, tool args).
- **Per-component detail.** For each `tool_spec` tool, set `side_effect` to one of `read|write|external|none` (the risk/trust signal: read-only vs mutates state vs external/network/paid call vs pure), describe what it `returns`, break `args` into a structured `arguments` list, name the `integration` it talks to (DB / API / vector store / MCP server / model) or `""`, and anchor it with `provenance` spans. For each `modes` task, fill `purpose` and its prompt — `prompt_span` or `prompt`, per **Prompt capture** below — plus `routing`/`model`/`output` when they apply, else `""`. For each `llm_utilities` helper, give its `io_contract`, a `cardinality` of `per_run|per_row|per_candidate|unknown`, a `prompt_excerpt`, and `structured_output` (true if it returns JSON/schema-structured output). Use the empty/`none`/`unknown` defaults rather than guessing.
- **Prompt capture.** Every capability and task must end up with readable prompt TEXT; never leave one unrepresented. Choose by what the source holds. If the prompt is a **literal** (string constant, triple-quoted block, template), set `system_prompt_span` / `modes[].prompt_span` to that literal's span and leave the text field empty — the platform reads it out of the repo, so you never retype it. If the prompt is **built by code** (builder function, f-strings, `.join`, a template filled at call time), leave the span empty and reconstruct the assembled prompt into `system_prompt` / `modes[].prompt`, using `{placeholders}` for runtime-supplied parts. Never aim a span at a `def`/`class`: source code is not a prompt, and such spans are discarded. `system_prompt_excerpt` / `prompt_excerpt` stay short companions, never a substitute.
- **Output contract as data.** `output_schema`, `constraints`, and `tool_protocol` are required only when discoverable in source — emit `{}` for `output_schema` and `[]` for the lists when the repo declares none. For `output_schema`, copy the REAL schema the code enforces (a JSON-schema/pydantic model/validator/required-keys check you actually read) — do not invent keys or types; empty object when none exists in source. For `constraints`, distill checkable invariants from prompts/policies/driver code (e.g. 'call report_stage once per phase' → `type: "tool_discipline"`; a tool budget of N → `type: "budget"`, `params: {"max_calls": N}`) and put machine-checkable values in `params`. For `tool_protocol`, capture what a valid tool sequence looks like (preconditions, orderings, evidence-before-citation rules) with the governed tool names in `tools`. Provenance is MANDATORY per entry — every `output_schema`/`constraints`/`tool_protocol` item must cite at least one real `relative/path.py#Lstart-Lend` span you actually read, same format as `capability_card.provenance.paths`.
- **Trajectory map (claim-classed).** Walk each capability's entrypoint control flow in the SOURCE — pure static analysis, assume nothing about tracing or instrumentation — and emit, per region of the capability, the STRONGEST claim the code supports. Every entry carries `claim`, exactly one of: (1) `code_path` — control flow determines the route (a pipeline, a staged workflow, a guard branch, a retry/park/resume, a confirmation gate): the entry is a CFG-backed task whose backbone is stable across executions; cite the branch's source span. (2) `declared_task` — the architecture itself NAMES the unit of work: a `modes[]` task, a router arm, a graph/crew node, or a prompt-declared duty with detectable structure (a fixed refusal string, a declared gating policy, a plan-approval flow); quote the naming text in `prompt_quote` (or cite the code span) — you are READING the builder's own task decomposition out of the artifact, never proposing one. When a `trajectory_map` entry describes the same unit as a `modes[]` entry, set its `mode` to that mode's `name` copied EXACTLY and its `id` to the kebab-case slug of that name, so the card entry and the scored task share one identity. (3) `decision_surface` — a MODEL decides the route at runtime (a tool loop, free-form dispatch, a driven coding/sub-agent choosing among MCP or registered tools): the entry records only the decision point — the deterministic prelude, the model_invocation with its full In/Action/Out contract, and the postlude to its terminals. Its option space is the whole `tool_spec` (leave that step's `may_use` empty); task identity past this point is determined at runtime by user intent and model policy — it is NOT in the code, and you MUST NOT enumerate intent tasks over it ("answer a question about X", "investigate Y"). One decision_surface entry per distinct model decision point, not per imagined intent. CLAIM PRECEDENCE: an entry whose INTERIOR contains a free tool loop takes `decision_surface` even when its trigger is architecturally named — put the naming text in `prompt_quote` and the trigger in `routing`, never downgrade the claim to `declared_task`: `declared_task` is only for entries whose whole backbone is model-decision-free. HARD RULE: any entry, task name, or `when` condition that is neither a real code branch, a real architectural name, nor a quoted declaration is fabrication — leave it out. Each entry models input → ordered BACKBONE STEPS → outcome: the `sequence` is the ordered list of intentional work units, stable across executions. Code-bound non-determinism lives WITHIN a step as `may_use` slots (`{"tool", "when"}`) ONLY when the harness genuinely binds that subset (e.g. gated tools at a confirm gate) — never as a guessed intent-to-tool table. A `step` is a deliberate work unit in plain language — never an exception type or class name (`ValueError`, `HTTPException`, …), never incidental control-flow or harness bookkeeping. Terminals (`error_exit`, `escalates`, …) stay as *outcomes* on `terminal`, not as sequence steps; the terminal `description` states what the user CONCRETELY receives. **Model invocations are explicit backbone steps**: every point where control passes to the model is a step with `kind: "model_invocation"` carrying `input` (what is passed in: message, history, prior tool results, tool schemas, prompt scope), `action` (what the model decides), `output` (EVERY possible output and its destination: tool_calls → capability dispatch, results feed back in; gated call → confirmation branch; final answer → output surface). A tool-results-feed-back loop stays ONE step — never unroll iterations. Deterministic harness work keeps `kind: "agent_step"` with an empty contract. Each entry carries a stable kebab-case `id`, the `routing` decision that selects it, the `tools` EVERY run touches, its `terminal` (`kind` EXACTLY one of `emits_record` — returns/emits a real output record; `returns_empty` — deliberately returns nothing (a declared refusal, not a failure); `escalates` — hands off to a human or another system; `error_exit` — raises/aborts), and `divergences` listing sibling entry ids reachable from its decision points. Every entry MUST cite at least one real `relative/path.py#Lstart-Lend` span. A deterministic single-task pipeline is the degenerate case: one `code_path` entry and no decision_surface. A pure tool loop is the other: harness `code_path`/`declared_task` branches (gates, parks, resumptions) plus ONE `decision_surface` — for such capabilities `tool_spec` must enumerate EVERY tool registered in the source's registry/list, not a sample; the dispatcher function is an anchor, not a tool. Emit `[]` only when the capability performs no discoverable work.
- **Tool clusters.** Assign every `tool_spec` entry a `cluster`: a short noun phrase grouping tools of similar function (e.g. 'graph exploration', 'dataset workshop', 'finetune lifecycle', 'deployment & serving'). Tools in one cluster serve similar tasks and render together; derive clusters from the tools' own purposes/naming, keep them stable and few (roughly 3-12 for a large registry), and reuse the exact same cluster string for every member.
- **Code-symbol anchors.** For each capability, emit an `anchors` artifact: the concrete functions that mark progress along its trajectories. `qualname` is the function's Python identity — the module dotted path derived from the file location (strip `.py`, `/` becomes `.`, relative to the importable package root) joined with the function's `__qualname__` including any enclosing class. Copy names from the SOURCE exactly; these are checked deterministically against the AST and fabricated names are dropped. `kind` is `entry_point` for the capability's entrypoint(s), `tool` for tool functions, `function` for other trajectory constituents. `file` cites the definition span as `relative/path.py#Lstart-Lend`. Then every `trajectory_map` entry lists its ordered `anchors` — the subset of anchor qualnames that path executes, copied verbatim from the artifact. The entrypoint anchor comes first. Prefer anchors that DISCRIMINATE between sibling paths.
- **Eval matrix (REQUIRED per capability).** Every capability MUST include an `eval_matrix` array. This is the small set of evaluators a reviewer would actually run to decide whether this capability is working. Treat it as a curated shortlist, NOT an exhaustive checklist. Derive every entry from this capability's own `capability_card` — its `task`, `expected_output`, `success_criteria`, `failure_modes`, `output_schema`, `constraints`, and `tool_spec`. Do not invent generic metrics that the code does not motivate.
- **Hard limits on `eval_matrix` (these are strict — violating them is an error):**
  1. **At most 5 metrics total.** Fewer is better. Do NOT pad the list to reach 5; include a metric ONLY if it adds real, distinct signal for THIS capability. A focused 3-metric matrix beats a padded 5-metric one.
  1. **Exactly ONE entry — no more, no fewer — has `type: "llm_judge_custom"`.** This is the single bespoke LLM-as-judge tailored to this capability. It targets the ONE most important quality dimension that none of the managed library metrics below can capture (the capability's core, domain-specific notion of a "good output row"). Its `rubric` MUST be a complete, self-contained grading rubric — lift the capability's `success_criteria` and `failure_modes` into concrete pass/fail language an LLM judge can apply to a single output row. `managed_name` MUST be empty for this entry.
  1. **Every OTHER entry has `type: "managed"`** and its `managed_name` MUST be copied **verbatim** from the "Available managed metrics" catalog below (exact spelling and casing). For managed entries, leave `rubric` as an empty string — the platform already owns the rubric. You may use **0 to 4** managed metrics; never duplicate the same `managed_name`.
  1. The "exactly one judge" rule counts ONLY bespoke `type: "llm_judge_custom"` entries. Several library metrics below (e.g. `Correctness`, `Faithfulness`) are themselves LLM-graded — that is fine and expected. Selecting an LLM-graded managed metric does NOT count as adding a second judge; only freshly-authored `llm_judge_custom` entries are capped at one.
- **Choosing managed metrics — selection rules:**
  - Pick a managed metric ONLY when its quality dimension clearly applies to this capability's output, and it does NOT overlap the dimension your bespoke judge already covers.
  - Respect `requires_reference`: only pick a metric marked "reference REQUIRED" when this capability's production data plausibly carries a ground-truth / reference answer to grade against. If outputs are open-ended with no reference, prefer reference-free metrics.
  - Only pick `scope: trajectory` metrics for capabilities that are genuinely multi-step / tool-using (they have a `tool_spec` and a meaningful action sequence). For single-shot text/JSON producers, stay with `final_output`-scope metrics.
  - Set each entry's `requires_reference` to match how it will actually be graded (true only when that metric needs a reference column).
- **Available managed metrics** (pick `managed_name` EXACTLY from this list — these are the only library metrics the platform can instantiate today):
  - `Correctness` — Is the output factually correct relative to the reference answer? (kind: llm_judge; scope: final_output; reference REQUIRED)
  - `Faithfulness` — Is every claim in the output grounded in the provided context? (kind: llm_judge; scope: final_output; reference REQUIRED)
  - `Hallucination` — Degree of hallucination (lower is better, inverted to higher=good). (kind: llm_judge; scope: final_output; reference-free)
  - `Answer Relevance` — Does the answer actually address the question asked? (kind: llm_judge; scope: final_output; reference-free)
  - `Conciseness` — Is the answer appropriately concise (no padding) without omitting needed info? (kind: llm_judge; scope: final_output; reference-free)
  - `Toxicity` — Is the output free of harmful, offensive, or disrespectful content? (kind: llm_judge; scope: final_output; reference-free)
  - `Role Adherence` — Did the assistant stay within its assigned role and persona? (kind: llm_judge; scope: trajectory; reference-free)
  - `Policy Compliance` — Did the capability follow the stated policy/guardrails? (kind: llm_judge; scope: trajectory; reference-free)
  - `Task Completion` — Did the capability accomplish the user's goal across the trajectory? (kind: trajectory; scope: trajectory; reference-free)
  - `Tool Selection Quality` — Did the capability choose appropriate tools with correct arguments? (kind: trajectory; scope: trajectory; reference-free)
  - `Trajectory Accuracy` — Reference-based match of the tool-call sequence (no LLM). (kind: trajectory; scope: trajectory; reference REQUIRED)
  - `Exact Match` — Output exactly equals the reference (case-insensitive). (kind: deterministic; scope: final_output; reference REQUIRED)
  - `JSON Validity` — Output parses as valid JSON. (kind: deterministic; scope: final_output; reference-free)
  - `Translation Quality` — Is the translation accurate, fluent, and natural in the target language? (kind: llm_judge; scope: final_output; reference REQUIRED)
- **Selection heuristics (guidance, not mandates):** RAG / context-grounded answering → `Faithfulness`, `Hallucination`, `Answer Relevance`. Classification / extraction with a known correct answer → `Correctness`, `Exact Match`. Strict JSON / schema output → `JSON Validity`. Multi-step, tool-driven capabilities → `Task Completion`, `Tool Selection Quality`, `Trajectory Accuracy`. Safety / policy-sensitive capabilities → `Toxicity`, `Policy Compliance`. Persona / chat assistants → `Role Adherence`. Translation capabilities → `Translation Quality`. Length-sensitive summarizers → `Conciseness`. Always still lead with the one bespoke `llm_judge_custom` judge for the capability's core dimension.
- If the repository has no LLM-driven capability, return `"capabilities": []` with a clear `repo_summary` (you may still populate `llm_utilities`).
- Do NOT print the full JSON to chat — only write the file `overmind_capabilities.json`.
- Verify the file contains a valid `capabilities` array (each with a `capability_card`) before finishing.
- After writing the file, reply with a short plain-text summary (2-4 sentences) of what you found.

**GROUND-TRUTH CHASSIS (deterministic AST facts — pre-extracted, verified).** You already ran `overmind chassis`. The printed digest is GROUND TRUTH: transform it, together with your own reading of the source, into the capability cards and trajectory map — do not re-derive or contradict it. Copy every `anchors` qualname VERBATIM from this inventory (same spelling, same dotted path). After you write the JSON, `convert_json_to_toml` deterministically re-checks every trajectory entry against this chassis: an entry whose anchors are not call-graph reachable from its entry anchor is stamped unverified, and anchor names absent from the inventory are dropped. Symbols elided from the digest still exist in the source — read the file to recover their exact qualnames.

## Persist

When `overmind_capabilities.json` is valid:

```bash
python -c "from overmind.utils import convert_json_to_toml; convert_json_to_toml('overmind_capabilities.json', 'overmind.toml')"
```

Delete `overmind_capabilities.json` after a successful toml conversion.

```bash
overmind sync
```

`convert_json_to_toml` preserves existing `base-url` / `project-id` / capability `id` values from any prior `overmind.toml`, fills `system_prompt` from cited spans, drops fabricated anchors and unverifiable provenance, and stamps `trajectory_map[].verified` against the AST chassis of the JSON's parent directory. `overmind sync` reads the local project credential, POSTs the snapshot to `/api/v1/sync`, and writes assigned ids back. If `project-id` was empty and the temporary key is account-scoped, sync creates the project first and persists the new id.

`eval_matrix` in toml is **intent metadata** — it round-trips through sync but is not turned into runnable graders. The platform authors the Default eval set server-side from the capability card after sync (async preload; check the Console Evaluators tab or `eval_preload` on the capability).

Never upload the repository. Never invent project or capability UUIDs.

## What stays local

Repository scanning, capability-card extraction, provenance, TOML conversion,
file edits, local Git, and pull-request commands stay on this machine. Do not
upload the repository through MCP. Use MCP only after the local snapshot exists
and the client is authenticated to the intended project.
