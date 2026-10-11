# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

AI engineers at startups shipping LLM agents to production. Small teams without
dedicated eval/training infrastructure who need their agents to get better —
and cheaper — using what production already tells them. They live in their
repo and terminal; the Console is where they inspect, decide, and launch work,
not where they live all day.

## Product purpose

Overmind is an agent improvement platform with one goal: continuously improve
agents running in production using real usage data. Success is a team shipping
a measurably better agent (accuracy, cost, reliability) as a scored change in
their own codebase — a diff or coding-agent prompt — driven by their own
production traces.

## Positioning

**The agent model is the product.** Overmind builds a model of a team's
agents (capabilities, behaviours, prompts, and tools) by scanning their repo,
pinning every component to the file and line that defines it. Telemetry binds
to that model. Teams can also start with uploaded data and a written task,
without scanning a repository. The public framing: Overmind builds
a model of the agent and uses traces to validate it.

That model is what the rest depends on:

- **Generated evals.** Each capability card records what the capability does.
  Rubrics compile from the card into weighted pass/fail checks the team reviews
  before anything is scored, scoped per capability so a triage capability is
  not graded on a planner's criteria.
- **Measured coverage.** Discovered components are reconciled against actual
  instrumentation and scored `N of M`, with every silent component named and a
  fix prompt offered.
- **Changes land as diffs.** The optimiser skill (`/overmind optimise`)
  drives a local SDK that edits the team's real agent repo; candidates are
  real git diffs scored on the same eval set as the baseline, so every gain is
  a measured delta.
- **Team-owned models.** Production traces become the training signal for
  private per-team fine-tuned models, benchmarked against the incumbent model
  running in production and served on an OpenAI-compatible endpoint.

Each stage feeds the next. Repository workflows use the graph; data-first
model workflows use a saved task interpretation, data lineage and comparison
protocol.

## Operating context

- Data-first onboarding leads to the existing cell-based Workshop. The native coding agent explores
  the source and authors explicit transformations through MCP. Saved group-preserving
  partitions, standalone decision comparisons, explicit training experiments and
  reproducible performance workloads make this path available through Console
  and MCP. Calibration and development selection stay separate from final results;
  coverage, identity uncertainty and partial cost accounting remain inspectable.
  Frozen-source derivation, bounded exploration and sampling feasibility precede
  draft/prepare/launch workflows. Saved receipts recover after reconnects; explicit
  compatible prediction reuse avoids duplicate work. Runtime profiles and user-defined
  planning constraints separate measured evidence from unknown costs.

- Teams instrument agents with the `overmind` SDK (PyPI) or
  `@overmind-lab/trace-sdk` (npm), or send existing telemetry via OTLP ingest
  (`POST /api/v1/traces`) and connector sync (Langfuse, LangSmith, Braintrust,
  Galileo) — no re-instrumentation required.
  The public promise is "no wrapper classes, no rewrites, no proprietary wire
  format": one SDK setup auto-instruments the LLM libraries already in use,
  over OpenTelemetry.

- Capability discovery is local (`overmind setup` → `overmind.toml` →
  `overmind sync`). After a fine-tune, the Console and MCP return a copy-paste
  prompt that points the capability's code at the new model.

- Optimisation runs from the agent's own repo after `overmind sync`: paste
  `/overmind optimise` in the coding agent; the skill drives the SDK, the server
  scores candidates, and results return as scores and diffs.

- The Console (console.overmindlab.ai) is where teams manage projects, inspect
  agents, browse observability data, build datasets, run evaluations and
  optimisation experiments, train and deploy models, monitor jobs, and chat
  with frontier or deployed models.

- Separate web properties: the Console, a public marketing site at
  www.overmindlab.ai, docs at docs.overmindlab.ai, and a public Discord. The
  SDK/CLI lives in the sibling `overmind` repo.

### The four public pillars

The marketing site organises the product under `/product/` into four pillars.
These are the customer-facing names and claims, and they are the vocabulary
future work should match:

| Pillar             | Headline                        | Promise                                                                                    |
| ------------------ | ------------------------------- | ------------------------------------------------------------------------------------------ |
| **Observability**  | "Understand your agents"        | Capability scanning, automatic tracing, coverage scoring, live scoring on arrival          |
| **Data Workshop**  | "Turn real behaviour into data" | Production traces become versioned training and eval data through explicit transformations |
| **Agent Testing**  | "Measure and improve"           | Generated evals, scored candidates, winning diffs in the team's repo                       |
| **Model Training** | "Own your model"                | Fine-tune on your own data, benchmark against the incumbent, serve it                      |

The pillars line up with the Console's navigation groups. Keep the two
taxonomies in sync rather than letting them drift. Site CTAs are **"Start for
free"** and **"Book a call"**: self-serve signup and a sales conversation.

## Capabilities and constraints

### Console surfaces

Navigation order: Overmind (home), Agent, Observability, Datasets, then an
**Agent Testing** group (Evaluations, Optimiser) and a **Models** group
(Training, Inference), then Settings and Projects. Docs and Discord are
external links. A command palette provides keyboard navigation.

- **Observability** — traces and sessions with a trace detail panel, span trees
  and flame charts. Previously called "Traces"; sessions are a view within it
  rather than a separate destination.
- **Agent** — the product graph: one agent per project, made of capabilities
  (Agent > Capabilities > Tasks) mapped from a local scan, with each capability's
  prompt, tools, control flow, and input/output contracts pinned to file and
  line. A capability the scan no longer finds leaves the agent and returns in
  place when the code does; a hand delete hides a capability and keeps its data.
- **Datasets / Data Workshop** — promote production traces into datasets,
  filtered by capability, eval score, or time window. Dataset intent is named
  **Train** (formerly "ft").
- **Evaluations** — runs and samples, generated rubrics, multiple evaluator
  kinds, per-metric scores with rationale, LLM-judge cost reported in credits.
- **Optimiser** — Console lists experiments; start them in a coding agent with
  `/overmind optimise` (harness) or `/overmind backtest` (model comparison).
  Note the spelling split: all prose and UI copy use "Optimiser" and the CLI
  command is `overmind optimise`, but backend and SDK identifiers still spell
  it `optimizer` (models, modules, API routes, OTel attributes).
- **Training** — fine-tuning jobs, checkpoints, and deployment. Renamed from
  "Fine-tuning" in the UI; backend code and modules still use `finetuning`.
- **Inference** — deployed models and per-model detail.
- **Jobs, Models, Projects, Settings** — background work, catalogue,
  workspaces, and integrations/API keys.

All surfaces are available to every team. Feature flags remain in the
navigation code but are no longer used to stage access.

### Data Workshop specifics

- Data Workshop opens with a project dataset table showing purpose, source,
  row count, active version, capability and update time. Selecting a dataset opens
  its unchanged, full-size cells on a grid-snapped flow canvas at scale 1:
  steps progress downward, sibling branches align side by side on the same layer,
  with elbow connections from recorded lineage. Zoom, Fit view and a toggleable
  minimap navigate the graph without changing cell dimensions; Reset and linked-cell
  focus return to scale 1. The Process control shows the current execution facts. Corrections replace the displayed process; there is no repair-history or restore view. Recorded partition links keep train, development, calibration and final outputs connected to their source and scripts.
  The left-hand box groups the minimap toggle with the folder button for project
  dataset navigation; cell search and the cell-selector strip are removed.
  Platform-agent chat and the prompt-style landing are removed.
- The native coding agent owns planning, interpretation, transformation code,
  generation and semantic judgment. The platform owns source landing, version
  identity, deterministic execution, lineage, impact and consumer pinning.
- Sources may be files, pasted rows or traces; source-free briefs are supported.
  Documents and images retain original bytes and extraction evidence, including
  local English OCR where needed. Intent is explicit and otherwise stays pending.
- Agents discover reusable project transformations, then validate and preview
  against a pinned source. Revisions retain ordered scripts, declared contracts,
  parameters and an approved immutable runtime. Compatible sources reuse the exact
  revision; adaptations record their origin without changing the original.
  Each successful run publishes one cell per step atomically. Preview publishes none.
  Agents declare step IDs, input edges and code-referenced branch conditions in the
  retained recipe. MCP returns this flow explicitly; execution receipts separately
  record actual input cells and output counts. Condition labels are declarations,
  not independent verification of script semantics.
- Script packages run in isolated, resource-bounded containers without network or
  platform credentials. Native agents own authoring, semantic work and interpretation.
  Imported outputs declare all source parents and producer attribution.
  Large outputs use an uploaded, checksum-bound artifact. Source attribution
  does not independently verify execution or answer correctness.
- Retries with the same request key recover the same work; changed inputs conflict.
  Failed or cancelled transformations never replace a readable version.
  Runs have durable receipts; unknown work is not blindly replayed.
- Explicitly enabled bindings reuse an exact revision as source datasets or trace
  selections change. These are full-snapshot rebuilds, not assumed incremental
  scripts. Checkpoints advance only with publication; failed work requires explicit
  retry authorization. Existing training and evaluation inputs do not change.
- Technical fit, source preservation, coverage and semantic quality are distinct.
  Unmeasured quality stays unknown. Quality findings remain advisory; unreadable
  or technically incompatible data blocks consumer use.
- Evaluation, Optimiser and Training freeze the exact version consumed.
  Later transformations do not modify those inputs. Training owns
  model-specific preprocessing; Workshop remains model-independent.
- MCP is the first-class authoring surface. Chat, mutable-cell editing and script
  replay are unavailable in the Console. Native-agent intake inspects record
  boundaries before upload and verifies landed counts and values before handoff;
  successful transfer does not establish correct ingestion.

### Training and serving specifics

- Up to four tiered experiments are recommended from dataset statistics and
  live traffic, **Compact to Large**; adapter training for speed or a full
  fine-tune for depth; trace-safe splits.
- Train/validation loss and token accuracy chart live during the run. Development
  monitoring adds frozen samples, measured check timing, generated-label/schema/field
  evidence and verified retained checkpoints. Early stopping and best-development
  checkpoint selection require explicit settings; warnings do not alter training.
  Classification observations compare the same scored sample with its majority
  baseline and expose represented labels without predictions. These are not
  diagnoses or final-test results. Newer loss-only checks do not hide the last
  generated-output result from the coding agent.
- Selected final benchmarks compare the trained model with the pinned base or
  incumbent. They remain independent of development monitoring. Missing exact
  OpenRouter routes and invalid evaluator definitions are visible failures, not
  permission to substitute a model or silently launch hosted base inference.
- Weights are merged, quantised, and deployed to a hosted endpoint; the swap
  is offered as a copy-paste prompt for the coding agent.
- Training and serving run on **Modal** and **Baseten**. Nebius has been
  removed. The model catalogue (`/api/models/catalog/`) can disable specific
  models, and large models are currently disabled — a live constraint that
  contradicts the public "Compact to Large" tiering until re-enabled.

### Platform facts

- Inference is an OpenAI-compatible API (`/api/v1/chat/completions`,
  `/api/v1/models`, `/api/v1/models/<model_id>`) covering frontier models and
  deployed fine-tuned models.
- Billing meters usage on an append-only credits ledger. **Remaining-credit
  caps, Free/Pro plans, Stripe Checkout and top-ups** inject only when
  `STRIPE_SECRET_KEY` is set (cloud). Without it (OSS) the Console still shows
  spend and usage history, with no remaining-credit cap. The marketing site
  owns `/pricing`; the Console's legacy `/pricing` route only redirects there.
- Auth is Clerk, plus issued API keys for SDK/CLI access. Product analytics is
  PostHog.
- Training delivery analytics measures result availability, check completion,
  coverage, measured overhead and reload-verified checkpoints. It excludes raw
  training content and does not infer clarity or delight. Local analytics is off
  unless explicitly configured; underlying delivery receipts remain available.

### Stack

Django API (`overbae`) with Celery workers, and a React 19 Console (Vite,
TanStack Router/Query/Table/Form/Virtual, Tailwind v4, Radix, Clerk). Tooling
is `bun` for the frontend and `uv` for the backend; Biome lints the frontend.
The frontend API client is generated from the backend schema
(`make generate_api_client` → `frontend/src/openapi/`) and is never
hand-edited.

### Terminology in product voice

Console, traces/spans, coverage, Observability, Data Workshop,
Agent Testing, Optimiser, executioner, Experiments, incumbent, Training
(marketing writes it as **Model Training**), credits. The hierarchy is
Agent > Capabilities > Tasks: "Agent" is the product (one per project),
"Capability" is a node inside it, "Task" is reserved. Never "Behaviour" or
"Mode" in product copy.

## Brand commitments

- Name: **Overmind** (company: Overmind Lab; domains `overmindlab.ai`,
  packages `overmind` on PyPI, `@overmind-lab/trace-sdk` on npm).
- Logo assets exist at `frontend/src/assets/`: `overmind-eye-copper.svg`,
  `overmind-eye-mono.svg`, `overmind-union.svg` (an eye mark with copper and
  mono variants, plus a union wordmark).
- API codename `overbae` is internal, not customer-facing.
- Public voice is declarative and mechanism-first: short claims stated as
  fact ("The agent model is the product", "Stop guessing, start measuring",
  "No ML infra"), each backed by the specific thing the system does.

## Evidence on hand

- A working product: live Console, SDK on PyPI/npm, OTLP + connector ingest,
  repo scanning into a capability model, generated evals, server-driven
  optimisation producing real scored diffs, and training through to
  deployed models.
- Stage: design partners / pilots. A handful of teams use it under the radar;
  there has been no public launch and **no publicly citable customers**.
- The public marketing site carries **no customer logos, testimonials, case
  studies, or benchmark numbers** — its proof is the mechanism described in
  specifics. Match that standard.
- Future work must not fabricate testimonials, customer logos, case studies,
  usage numbers, or benchmark results. Truthful evidence is the product
  mechanism itself (the graph, the loop, the Console, real docs) until pilots
  convert to public proof.

## Product principles

1. **Every feature reads the scanned agent.** Discovery, coverage, evals,
   optimisation, and training recommendations all read the scanned capability
   model. A feature that invents its own model of the agent duplicates the
   part that is hard to copy.
1. **Every feature advances the loop.** Each page moves a team from trace to
   dataset, to eval, to optimize or train, to a shipped change. A feature that
   dead-ends outside the loop is off-strategy.
1. **Real production data over synthetic demos.** Improvements are grounded in
   what the agent actually did in production, not hand-written examples.
1. **Measured, not asserted.** Candidates and models are scored against a
   baseline or incumbent on the same eval set. Claim a gain only where there is
   a delta to point at.
1. **Meet engineers where change ships.** The repo, CLI, and coding agent are
   the destination; the Console orchestrates and explains, but the win lands in
   the team's own codebase.
1. **Only truthful proof.** At pilot stage, credibility comes from showing the
   real mechanism working — never invented social proof or numbers.

## Accessibility and inclusion

No formal conformance standard has been set for this product — that decision is
open. Two constraints hold in the current implementation and future work must
preserve them: the Console ships a **single dark theme**, independent of stored
or operating-system preferences, and motion **respects `prefers-reduced-motion`**.

Script retention is automatic; the native agent owns preparation and repairs. Workshop cells show retained data and scripts, while execution receipts record publication validation.
