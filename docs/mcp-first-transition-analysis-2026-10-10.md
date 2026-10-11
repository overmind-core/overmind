# MCP-first product transition

Assessment date: 10 October 2026.

**Recommendation:** move workflow authoring, configuration, launch and ordinary recovery into the native agent through MCP. Make the Console the place to inspect evidence, compare outcomes and understand current state. Retain a small human administration surface and explicit emergency-stop controls.

The execution primitives are substantially present. The transition is blocked mainly by incomplete discovery and inspection, evaluation configuration, several lifecycle operations, and inconsistent recoverability. Removing launch forms alone would leave agents dependent on the Console for IDs, detailed evidence and recovery.

## Evidence and scope

- Read the current frontend routes, their mounted components and mutation hooks, REST handlers, MCP contracts/handlers/resources, and relevant CLI code. This includes the existing uncommitted working tree.
- Queried the connected local MCP with `list_projects`; it reported `http://localhost:8000/api/mcp/`, 69 visible tools, and the local Console origin. The matching local project is named **overmind**.
- Read `overmind://interface/current`: contract **6.3.0**, transfer protocol **1**, catalogue SHA-256 `34475b5fe28db844d7cd7160d052efa7e1ac4803ed07e09d2d4f190d19ce6b2e`.
- Inspected the connected resource inventory: six static resources and seventeen resource templates. Resource templates provide entity reads; they do not enumerate those entities.
- This is a functional and architectural review. No browser interaction, visual-quality scoring, production usage analysis, mutation calls, paid jobs or runtime workflow tests were performed. Recommendations are not claims that every existing tool works end to end.
- Only this analysis document was added. Existing code and unrelated changes were left alone.

## Product boundary

| Responsibility                                                                                             | Recommended owner                            |
| ---------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| Interpret the task, inspect source meaning, author transformations and rubrics, choose models and settings | Native coding agent                          |
| Discover project entities, validate requests, execute platform work, record state and recover jobs         | MCP over shared domain services              |
| Transfer local files, retain packages, scan/edit repositories, handle local credentials                    | CLI/SDK, with discoverable MCP handoffs      |
| Report progress, inspect data and lineage, compare runs, explain recorded failures, show deployment state  | Console, backed by the same domain services  |
| Sign in, grant/revoke access, manage membership and keys, handle commercial billing where enabled          | Human administration UI                      |
| Stop unwanted running work                                                                                 | MCP plus a small, explicit Console exception |

An MCP-first product can still use REST to serve its dashboard. There is no benefit in routing React through MCP or deleting shared REST services just because a form disappears. The requirement is that completing an operational workflow does not depend on a Console-only action.

Preserve interactive inspection: filtering, sorting, searching, selecting comparison runs, expanding samples, navigating graphs, choosing a displayed version, copying references and downloading existing artefacts. These do not need to move into chat.

Changing the active version, selecting a live model, altering a grader or retrying a job changes product state. Those belong in the native workflow even when their controls look like ordinary selectors or navigation.

## Surface-by-surface disposition

| Current surface                    | Strip out or move to MCP/native tooling                                                                                           | Keep in the Console                                                                                                                                   | Coverage and qualification                                                                                                                                                                                                                                       |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Home / Agent                       | Capability renaming/deletion; action nudges that open creation workflows                                                          | Agent map, capability status, repository snapshot, scan freshness, contracts, coverage and links to evidence                                          | Health and instrumentation tools exist. Complete graph/contract inspection and capability inventory are incomplete. Current `/` is an Agent page, not a cross-product dashboard.                                                                                 |
| Capability details                 | Inline name editor, live-model selector, eval-set activation/member editing, consumer launch shortcuts                            | Prompt and tool inspection, trajectory graph, examples, historical scores, model provenance, active and benchmark identities                          | `set_active_model` and `set_benchmark_model` already exist. Eval-set maintenance and capability metadata lifecycle need work.                                                                                                                                    |
| Observability                      | Dataset-creation forms opened from trace selection                                                                                | Traces, sessions, filters, span trees, latency/cost/error views, task verdicts, captured inputs/outputs                                               | `query_traces`, `query_task_executions`, `query_failures` and dataset-from-traces/LLM-calls tools exist. Preserve the exact selected trace IDs or filter in an agent handoff.                                                                                    |
| Dataset landing                    | New-dataset dialog, source upload/attachment forms and train/eval split configuration                                             | Dataset table, purpose, row counts, source state, freshness, active version and capability                                                            | `start_dataset`, trace/LLM-call creation, derivation and partition tools plus CLI upload cover the main path. Deletion lacks an MCP counterpart.                                                                                                                 |
| Dataset notebook                   | Intent/capability editing, Set active, Restore, Train a model / use-in-training / optimiser launch paths                          | Full cells, row inspection, retained scripts, lineage, extraction evidence, version preview, diffs, export and original-source downloads              | `update_dataset` covers metadata and active version; pipeline lifecycle and cancellation already exist. The current consumer handoff mutates `active` before navigating: it must become an exact-cell handoff.                                                   |
| Evaluation runs                    | New evaluation dialog, rerun/cancel/delete menus except the deliberate stop exception                                             | Run history, model comparison, frozen judge identities, sample input/output/reference/reasoning, technical failures and coverage                      | `run_evaluation`, readiness and comparison exist. Chat-evaluation cancellation, durable reruns and full sample reads are missing.                                                                                                                                |
| Eval sets / library                | Evaluator authoring/editing forms, prompt generation, membership edits, activation and deletion                                   | Read-only rubric/checklist/spec, role and binding coverage, set membership, history and score evidence                                                | `upsert_evaluator` and `create_eval_set` exist. Creation is not maintenance; active-set and membership changes are absent. Native agents can author rubric text without a replacement platform generation agent.                                                 |
| Decision comparisons / performance | Create, prepare, launch, pause/resume and workload forms                                                                          | Frozen protocol, participants, calibration/final distinction, metrics, slices, costs, progress and report downloads                                   | Native evaluation and performance tools already cover these transitions. Do not mistake the separate chat-evaluation cancellation gap for a missing native-comparison pause tool.                                                                                |
| Optimiser                          | Capability/dataset/model configuration dialog; ordinary run controls                                                              | Baseline/candidate scores, iteration history, failure state, winner evidence, diffs and patch download                                                | Already substantially native: its dialog constructs a copyable agent prompt. Keep a lightweight context handoff, remove the duplicate configuration form. MCP cancellation and complete large-result retrieval need work. Local execution remains CLI/SDK-owned. |
| Training                           | Full training wizard: data/model/recipe/monitoring/benchmark selection, launch; native-evaluation scheduling form; retry controls | Job history, exact recipe and inputs, loss/development checks, samples, checkpoints, forecasts versus measurements, terminal evidence and run records | Readiness, estimate, preparation, start, progress and cancel tools exist. Multiple chat benchmarks and failed-training retry do not have equivalent public MCP inputs/actions.                                                                                   |
| Inference                          | Make live, retry deployment, decommission/delete; any future playground submission belongs in the agent                           | Deployment inventory, alias target, previous target, readiness, warmth, connection evidence, metrics, activity, API snippets and checkpoint download  | Activation, deployment retry and inference request tools exist; deployment resources already expose metrics/activity. Inventory and decommission lifecycle need work. No dedicated chat/playground route is present in the inspected route set.                  |
| Integrations                       | Mapping, scheduling, backfill, sync-now forms and operational configuration wizard                                                | Provider/config identity, mapping coverage, sync freshness, counts, errors and receipt timeline                                                       | `inspect_connectors`, `configure_connector`, `sync_connector` cover operational work. New secrets use CLI setup. Existing credential rotation/edit/disconnect still needs an explicit supported owner.                                                           |
| Projects / Settings / Auth         | Move routine data-first project creation into MCP; remove workflow launch prompts from general navigation                         | Project switcher, sign-in, OAuth consent, memberships/invites, key management, connection help, administrative deletion and billing where enabled     | These are intentional human exceptions, not reasons to expose every account write to agents. Repo sync can create a project, but MCP currently only lists projects.                                                                                              |
| Shell / command palette            | Action entries and URL parameters that resurrect removed forms; creation-oriented empty states                                    | Search/navigation, recent items, job alerts, project scope and ordinary deep links                                                                    | Audit nested capability tabs, dataset footers and `?create`, `?train`, `?optimize` entry points, not just page-header buttons.                                                                                                                                   |

Source entry points: [Agent](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/routes/_auth/index.tsx:94), [capability actions](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/routes/_auth/capabilities.$capabilityId.tsx:115), [notebook handoff](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/components/datasets/notebook/notebook-page.tsx:113), [version restore](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/components/datasets/notebook/pipeline-runs.tsx:127), [evaluation views](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/routes/_auth/evaluations.index.tsx:130), [training](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/routes/_auth/training.tsx:41), [native comparisons](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/components/model-workflows/decision-comparisons.tsx:263), [optimiser prompt builder](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/components/optimiser/run-locally-dialog.tsx:95), [palette](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/components/command-palette.tsx:152).

## Confirmed gaps to close

Proposed API names below are design suggestions, not tools in the connected catalogue.

### 1. Complete discovery and recovery after losing conversation context

**Required before retiring operational UI.** Add paginated, filterable discovery for capabilities, evaluators, eval sets, chat-evaluation runs, training jobs/groups, all relevant deployments and optimiser experiments. Include nonterminal, failed and archived records where applicable, with exact IDs and resource links.

Today `list_projects`, `list_datasets`, `inspect_dataset_workbench`, `inspect_connectors`, `list_decision_models` and `list_model_workflows` cover particular domains. `list_model_workflows` is restricted to five data-first workflow kinds. `get_job` requires both kind and ID. Project resources return metadata rather than inventories. `inspect_capability_health` returns aggregates rather than a capability directory. Incidental IDs in traces, mappings or benchmark candidates are not a complete replacement.

Prefer a small number of typed query tools grouped by entity or job family over a separate tool for every dropdown. A general read catalogue can be bounded and typed; a generic arbitrary-mutation API should not be introduced.

Acceptance: a fresh agent given only the project can find yesterday's failed training job, its exact inputs and associated evaluation, without an ID copied from the Console.

Evidence: [project and capability resource payloads](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/resources.py:568), [static resource list](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/resources.py:1541), [health aggregation](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/tools_observability.py:162), and the connected 69-tool inventory.

### 2. Full inspection, especially capability contracts and evaluation samples

**Required for agent-led diagnosis and preparation.** Expand capability inspection to expose the same relevant task definition, input/output contract, prompt/tool context, verified trajectory and repository provenance that the Console reads. Include declared behaviours with no executions; an execution query alone cannot explain all unobserved contracts.

The current capability resource contains identity, status, model, dataset size, active eval-set/model and benchmark summaries. It does not contain the Console's rich `flow` contract. Instrumentation tickets solve placement, not general capability understanding.

Add paginated evaluation sample/score reads and direct sample inspection, plus full evaluator definitions and annotation reads. `_eval_run_resource` takes only five samples and marks truncation; the dispatch path does not page them. `annotate_evaluation_sample` exists, but creating annotations is insufficient if an agent cannot browse arbitrary samples and reread their reviews. Eval-set resources also bound members at 100.

For optimiser output, both inspection and resources clip patches at 4,000 characters. Provide a complete candidate/patch retrieval or authenticated CLI export handoff. A truncation flag is honest, but cannot be the end of the inspection path.

Do not remove response bounds. Make every important bound traversable through pages, sections or artefact downloads.

Evidence: [Console flow contract](/Users/tyleredwards/Documents/GitHub/overmind/overbae/api/serializers.py:630), [MCP capability summary](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/resources.py:585), [eval-set/sample bounds](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/resources.py:799), [patch clipping](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/tools_optimizer.py:415).

### 3. Evaluation-set maintenance and live-scoring selection

**Required before removing evaluator controls.** Add explicit set updates and active-set selection. Membership changes need evaluator identity, role, enabled state and any supported membership settings, with concurrency/version checks and preserved run snapshots.

`create_eval_set` only accepts name, optional capability and evaluator IDs. Its handler returns `active=False`. `upsert_evaluator` changes an evaluator, not a set's membership or the capability's active set. The Console can add/remove members, update membership and activate a set. Passing a new set into one evaluation does not change the default used by future work or live scoring.

Suggested shape: an atomic `update_eval_set` over an expected revision, plus `set_active_eval_set`. Reuse existing role validation and activation services. Keep saved historical evaluator identities intact.

Evidence: [MCP set creation](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/tools_evaluations.py:721), [UI membership/activation hooks](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/hooks/use-evaluations.ts:158), [REST activation](/Users/tyleredwards/Documents/GitHub/overmind/overbae/api/eval_views.py:647).

### 4. Cancellation and recovery across job families

**Required for operational independence.** Add chat-evaluation cancellation and optimiser cancellation. Add a deliberate failed/cancelled-training retry or successor-attempt operation. `retry_deployment` does not retry training, and `prepare_training_data(retry_failed=true)` only retries preparation.

Existing MCP coverage should be retained: dataset and pipeline cancellation, `cancel_finetune`, native-comparison pause/resume, partition retry, performance resume and deployment retry. Use the exact supported transition for each family instead of a generic ambiguous “stop”.

Do not port the existing evaluation rerun behaviour unchanged. The REST action deletes samples and scores before reusing the same run. Prefer a new run/attempt linked to the original, retaining old evidence and the pinned recipe. Training retry similarly needs explicit attempt and provider-reconciliation semantics rather than blindly replaying a launch after an uncertain acknowledgement.

A Console emergency stop remains useful when the agent is unavailable. It should invoke the same domain transition as MCP and show cancellation requested, provider acknowledgement and any remaining in-flight work separately.

Evidence: [eval relaunch/cancel](/Users/tyleredwards/Documents/GitHub/overmind/overbae/api/eval_views.py:797), [training retry](/Users/tyleredwards/Documents/GitHub/overmind/overbae/api/views.py:971), [optimiser cancel](/Users/tyleredwards/Documents/GitHub/overmind/overbae/api/optimizer.py:327).

### 5. Multiple chat-training benchmarks

**A specific parity blocker before deleting the training wizard.** The wizard submits `benchmarkModels`; the REST serializer accepts `benchmark_models`. `StartFinetuneInput` has one `baseline_model`, and the MCP launch handler passes that single value without a benchmark list.

Carry the supported benchmark collection through MCP discovery, readiness/context checks, estimation and launch, with exact model identities in the receipt. Preserve the primary benchmark and shared eval-set/dataset/judge semantics. Running unrelated standalone evaluations is not equivalent to the existing shared training comparison protocol.

This gap concerns chat-training benchmark selection. The existing native decision-comparison and training-experiment tools address a different workflow and should remain.

Evidence: [wizard payload](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/components/finetuning/train/use-train-wizard.ts:623), [REST field](/Users/tyleredwards/Documents/GitHub/overmind/overbae/api/serializers.py:1188), [MCP input](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/contracts/finetuning.py:202), [MCP launch](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/tools_finetuning.py:699).

### 6. Recoverable mutation contracts across older workflows

**Required before treating MCP as the primary execution surface.** `run_evaluation` and `start_optimizer` expose no stable request key and their handlers create new records per invocation. By contrast, inference, Workshop and newer model workflows have durable request identities. Training supports a request key but makes it optional.

Standardise keyed creation/launch and recipe-conflict detection for operations where a retry could duplicate work or spend. Resolve and retain exact dataset cells, eval-set/evaluator versions, model identities and judge selection at submission. Returning a job ID after success does not cover a response lost after acceptance.

Use preview/readiness → explicit launch where useful, but do not multiply tools merely to recreate a UI wizard. Agent-side consent remains in the native conversation; the platform records authorised scope and the resulting durable operation.

Evidence: [evaluation contract](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/contracts/evaluations.py:336), [evaluation creation/dispatch](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/tools_evaluations.py:528), [optimiser creation](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/tools_optimizer.py:309), [training request identity](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/contracts/finetuning.py:202).

### 7. Routine project creation without a repository

**Required for a fully native data-first start; deferrable if project creation stays an explicit UI exception.** Add account-scoped project creation for an authenticated user, with a stable request key and a returned project resource. Do not require repository scanning to create a dataset project.

MCP currently has `list_projects` only. The Console's data-project form creates projects directly. CLI sync can create a repository project, so this is not a claim that all project creation is UI-only. It is a gap in the data-first MCP journey.

Keep initial sign-in and OAuth consent in the human UI. Account membership, invitation and deletion management can remain there; these do not need to be bundled into project creation.

Evidence: [data-first project form](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/components/onboarding/data-project-form.tsx:15), [repository bootstrap](/Users/tyleredwards/Documents/GitHub/overmind/overmind/overmind/sync.py:167), [MCP project tools](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/tools_projects.py).

### 8. Deliberate retirement, cleanup and connector maintenance

**Conditional blockers: either give each retained outcome a supported owner or explicitly retire it.** MCP lacks dataset deletion, capability rename/retirement, evaluator/eval-set/eval-run cleanup, deployment decommissioning and connector disconnect. These omissions are partly intentional under the current MCP policy; they require product design, not mechanical API exposure.

Prefer archive/retire for evidence-bearing entities. Deployment decommission must distinguish alias detachment, preventing new requests, in-flight work, worker shutdown and preservation/removal of weights. `set_active_model(deployment=null)` clears a capability selection; it is not equivalent to decommissioning the deployment.

Connector operational settings already work through MCP. Secret rotation and provider endpoint/name edits do not belong in ordinary model-visible tool arguments. The inspected CLI exposes `connector add`; it does not offer a corresponding edit/rotate command. Retain a compact connection administration UI or extend the secure CLI handoff, then expose secret-free verification/status and lifecycle actions through MCP. Disabling auto-sync is not the same as disconnecting credentials.

Keep account/project deletion and membership administration in the human UI initially. Do not add a generic `delete(resource)` tool to obtain superficial parity.

Evidence: [dataset deletion hook](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/hooks/use-datasets.ts:379), [deployment delete/undeploy](/Users/tyleredwards/Documents/GitHub/overmind/overbae/api/views.py:2004), [connector edits](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/components/connectors.tsx:153), [CLI connector add](/Users/tyleredwards/Documents/GitHub/overmind/overmind/overmind/connector_cmd.py:160), [MCP classification policy](/Users/tyleredwards/Documents/GitHub/overmind/.claude/skills/mcp/SKILL.md:26).

## What the dashboard needs to gain

Removing actions should make the evidence easier to understand. It should not leave mostly empty pages.

1. **A project overview that works without a repository.** Current `/` centres the capability graph and quickstart. Add current work, recent outcomes, failures requiring attention, data/model inventory, serving health and measured usage. A data-only project should not look unconfigured because it has no scanned Agent graph.
1. **One durable activity feed.** The current `useRunningJobs` aggregates training, eval runs and optimiser experiments. Include dataset transfers/landing/pipelines, exploration, partitions, preparation, native evaluations, training experiments, decision performance, deployments, activation and inference requests. Reuse operational receipts; do not invent progress where telemetry is absent.
1. **A consistent run detail.** Show who or which client initiated it, request/attempt identity where recorded, exact inputs/configuration, current stage, heartbeat versus forward progress, measured spend, missing cost components, output links and terminal reason. Distinguish declared intent from measured result. Backfill neither provenance nor measurements by inference.
1. **Strong cross-links.** Navigate source → cell/revision → evaluation or training → checkpoint → deployment → activation/application evidence, preserving project and version. Keep readable historical artefacts after failures and cancellation.
1. **Reliable agent handoff.** A compact “Copy context” action should carry project scope, entity/resource URI, exact cell/run/attempt and optionally selected sample IDs or filters. The agent determines the next operation. Do not replace each wizard with another long form whose final button merely copies a prompt.
1. **Freshness and completeness.** Expose last observation, stale telemetry, missing results, truncated previews and coverage denominators. Shared-pool warmth, adapter readiness and successful application traffic are separate facts. Offline evaluation scores and live trace scores stay separate.
1. **Connection visibility.** Show how to connect the agent and which environment/project it is inspecting. Expose connection/grant management in human administration. The repository has OAuth consent and a revocation endpoint, but no connected-agent grant-management screen was found in the inspected frontend.

Source: [current home](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/routes/_auth/index.tsx:94), [running-jobs aggregation](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/hooks/use-running-jobs.ts:54), [OAuth consent](/Users/tyleredwards/Documents/GitHub/overmind/frontend/src/routes/oauth.authorize.tsx), [revocation endpoint](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/oauth_routes.py:103).

Recommended navigation: **Overview**, then the existing Agent, Observability, Datasets, Evaluations, Optimiser, Training and Inference views as evidence browsers. Put Integrations and project/account administration under settings. Add Activity as a shared cross-product view. Changing the ownership of actions is enough work; merging every domain into a new information architecture is unnecessary for the first cut.

## Platform changes beyond adding tools

- **Share services, not frontend implementations.** Several missing actions currently contain business logic in REST views, particularly evaluation relaunch and training retry. Extract the intended lifecycle into a domain service before exposing it through MCP. Dashboard exceptions and MCP must call the same service.
- **Keep the native agent responsible for semantic authoring.** Remove Console prompt-generation flows where redundant. Do not replace removed dataset/evaluator forms with a new platform planning chatbot. Preserve configured evaluation judges and training/serving providers; these execute product work rather than author the workflow.
- **Make reads useful without writes.** Catalogue metadata declares fine-grained required scopes, but actual catalogue visibility currently enforces public read versus write. The dashboard and inspection tools should work with read access. If launch, activation or retirement need distinct permission classes, implement them centrally rather than assuming the metadata already enforces them.
- **Record operational provenance consistently.** Existing receipts provide a foundation, but validate actor/client identity, attempts and causes across domains before promising a global audit view. Native conversation text does not need to become a second platform chat history.
- **Improve handoff links.** MCP resource links already preserve account project scope. Add ordinary Console deep links consistently to relevant entity/job results so an agent can point the user at the exact visual evidence, not just the project homepage.
- **Update product guidance at cutover.** `PRODUCT.md` still describes the Console as a place to launch work and chat. `DESIGN.md` specifies mutable dataset consumer/restore controls. Update these, AGENTS.md, focused skills, prompts, examples and sibling docs alongside implementation. Leave shared API/generated-client maintenance intact for the dashboard and approved administrative exceptions.

Evidence: [catalogue permission enforcement](/Users/tyleredwards/Documents/GitHub/overmind/overbae/services/mcp/catalog.py:244), [product positioning](/Users/tyleredwards/Documents/GitHub/overmind/PRODUCT.md), [current UI design contract](/Users/tyleredwards/Documents/GitHub/overmind/DESIGN.md).

## Implementation order

| Order | Deliverable                                                                                                            | Exit condition                                                                                   |
| ----- | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| 1     | Adopt the ownership boundary and an inventory of every user-visible mutation, including nested controls and deep links | Each action is mapped to existing MCP, a defined gap, a human exception or deliberate retirement |
| 2     | Complete discovery, rich capability/evaluator/sample reads and job queries                                             | A fresh agent can find and inspect existing work without Console-provided IDs                    |
| 3     | Close evaluation-set maintenance, cancellation/recovery, multiple benchmarks and launch idempotency                    | Existing operational outcomes can be completed and recovered through MCP                         |
| 4     | Resolve data-first project bootstrap, cleanup and connector credential ownership                                       | No retained workflow ends at an unexplained missing-tool boundary                                |
| 5     | Remove creation/configuration forms and their route/palette/nudge entry points in coherent vertical changes            | Browsing changes only view state; approved administrative/stop exceptions are explicit           |
| 6     | Ship the project overview, complete activity feed, consistent run details and exact-context handoffs                   | The Console explains native-agent work across all supported job families                         |

Some removals can proceed sooner: Workshop mutation controls already have named MCP counterparts, and decision-comparison/performance forms have broad transition coverage. Training and evaluation configuration should wait for the specific parity gaps above. Keep read components while deleting form wrappers and unused mutation code.

## Acceptance journeys for the transition

These are proposed E2E checks, not tests executed during this analysis. Each implementation run should retain its command, fixture/input identities, environment requirements and observed receipts/results.

1. **Fresh-session discovery:** with only an authenticated project reference, find a prior failed job, inspect all relevant evidence and take the supported recovery action. No hidden IDs, raw REST workaround or Console mutation.
1. **Data-first journey:** create a project without a repository, upload through the local CLI, inspect source meaning, retain and publish a transformation, pin partitions, compare/train, then inspect everything in the dashboard.
1. **Evaluation maintenance:** discover an existing evaluator and set, inspect full specs, change membership/role, select the active set, launch an evaluation and inspect a failure beyond the first five samples. Prior run snapshots stay unchanged.
1. **Training parity:** launch the same multiple-benchmark recipe supported by the current wizard, verify exact model/judge/cell identities, inspect development evidence, then cancel or recover deliberately.
1. **Lost-response recovery:** interrupt the client after acceptance, reconnect and repeat the same request key. Recover one operation; changed inputs conflict. Unknown provider submissions are reconciled, not duplicated.
1. **Serving lifecycle:** activate a ready deployment, verify the routing receipt, distinguish readiness from real application use, then decommission through the designed lifecycle with weight retention explicit.
1. **Read-only dashboard:** visit all evidence routes with read-only access; filtering, version preview, graph navigation and downloads perform no operational mutation or worker wakeup. Stale form URLs cannot resurrect launch workflows.
1. **Project isolation and scale:** page past inventory/sample/patch preview limits, keep exact identities and scope, and reject cross-project references in reads, writes and links.

The practical definition of completion is: **the native agent can discover, perform, inspect and recover every supported operational workflow; the Console can explain the resulting state without being required to create it.**
