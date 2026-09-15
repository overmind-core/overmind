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
to that model; everything else runs on it. The public framing: Overmind builds
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

Each stage feeds the next, and every stage reads the graph.

## Operating context

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

| Pillar             | Headline                        | Promise                                                                           |
| ------------------ | ------------------------------- | --------------------------------------------------------------------------------- |
| **Observability**  | "Understand your agents"        | Capability scanning, automatic tracing, coverage scoring, live scoring on arrival |
| **Data Workshop**  | "Turn real behaviour into data" | Production traces become audited, redacted training and eval data                 |
| **Agent Testing**  | "Measure and improve"           | Generated evals, scored candidates, winning diffs in the team's repo              |
| **Model Training** | "Own your model"                | Fine-tune on your own data, benchmark against the incumbent, serve it             |

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

- Any file, pasted rows, or trace selection lands as-is as the **source**;
  nothing is reshaped on the way in. Trace rows carry the behaviour, verdicts,
  scores and asks that trace scoring produced, one row per task execution.
- Landing proposes two things and the first use freezes them: the **intent**
  (train or eval) and the **capability** the rows belong to. Both are
  contracts every version is measured against: the intent contract is the
  shape, the capability contract is row by row — an eval input carries the
  capability's required keys, a train transcript is the capability's own
  system prompt and tools. A row that cannot meet them is a finding, never a
  default value.
- A linear chain of Python cells is the recipe: a cell is a body (the frame
  arrives as `df` and leaves as `df`), each cell that ran is a **version**
  (the source is 1.0, then 1.1, 1.2, …), and each shows what it did to the
  rows and values against the cell before — added rows, removed rows, and
  changed values with the old value in reach. The **active version** is the
  last cell that ran unless the user points elsewhere. Editing a cell re-runs
  it and every cell after it in place; the numbers do not move.
- The chat on a dataset is the dataset's own agent, an ML engineer, not the
  general assistant: it tests a script before it lands a cell, one request is
  one cell, it reads before it writes, and it never asks what to do next. The
  moment a dataset lands it runs one turn: the fewest cells that make both
  contracts hold, each run in view, then the quality checks an engineer would
  run for that intent — refusals, truncated turns, duplicates, length
  outliers, leakage, coverage — each landed and run as one cell with the row
  count in its note; only a fix that would drop more than half the rows waits
  as a proposal. After that the conversation is open.
- Eval runs, optimiser runs, training runs and exports **use** the exact
  version they read: it becomes the next major (2.0), it and every cell
  before it freeze, the frame cannot be deleted, and the run links back to it.
- The product's shape and token distribution feed the training recommender —
  model tier, epochs, learning rate, adapter settings — with cost and duration
  estimated before commitment.

### Training and serving specifics

- Up to four tiered experiments are recommended from dataset statistics and
  live traffic, **Compact to Large**; adapter training for speed or a full
  fine-tune for depth; trace-safe splits.
- Train/validation loss and token accuracy chart live during the run.
- Every fine-tuned model is benchmarked against the **incumbent** — the model
  the capability actually runs in production — with per-metric deltas.
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
preserve them: the Console ships **user-selectable light and dark themes**, and
motion **respects `prefers-reduced-motion`**.
