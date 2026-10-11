---
name: mcp
description: End-to-end workflow for adding or changing Overmind MCP tools, resources, prompts, authentication, or result contracts — server layers, catalog registration, MCP-impact classification, and required tests. Use when adding, changing, or removing an MCP tool, resource, prompt, auth rule, or CallToolResult contract.
---

# Adding or changing MCP

Platform-agent procedure for changing the server. The skill shipped by
`overmind init` is `overmind/skills/overmind/` — do not copy this file there.

The MCP server is the project-scoped agent API. It shares
domain services with the Console and REST API; it does not proxy either of
them.

The optional Claude Code, Codex and Cursor plugins under
`overmind/.{claude,codex,cursor}-plugin/` package the existing MCP connection and
shared workflow skills; their versions match `SKILLS_VERSION` in
`overmind/overmind/skills_db.py`. The focused skills in
`overmind/skills/overmind-*/` cover Agent, Observability, Datasets, Evaluations,
Optimiser, Training, Inference and Integrations; the main `overmind` skill keeps
local setup and workflows across surfaces. CLI initialization installs all of them.
Essential client-independent guidance
belongs in server initialization, tool descriptions and resources; longer
workflows use native prompts and skill fallbacks. The current-project resource
includes `console_url` from `FRONTEND_URL` for ordinary browser navigation.
Do not make a plugin or a custom UI a prerequisite for platform operations.

## MCP-impact classification

For every new or modified Overmind capability, function, API workflow, or
Console workflow, make an explicit MCP-impact decision in the same change. A
change is not complete merely because the frontend works. Classify it as one
of the following:

- **MCP-ready** — an agent can discover, inspect, or progress the workflow.
  Add or update the smallest appropriate MCP tool, resource, or prompt in the
  same change.
- **CLI-guided** — the workflow needs local files, repository edits, a
  binary download/upload, or third-party connector credentials. MCP supplies
  the state, exact identifiers, and a structured human/coding-agent action;
  the existing CLI or SDK performs the local transfer or edit.
- **Frontend-only** — presentation, navigation, visual exploration, billing,
  or another workflow that has no useful safe agent action. The underlying
  project state remains MCP-ready when it is useful to agents.
- **Out of scope** — destructive operations remain absent from the public MCP
  surface until explicitly designed and authorized.

Record a concrete reason when a change is not MCP-ready. Do not silently let
the Console become the only way to complete an agent-relevant workflow.

Textual state is the required baseline. Console-only visualizations may stay
visual, but their underlying inspectable data and agent actions should be
available through the appropriate MCP surface when they pass the classification
above.

## Shape of the server

```text
MCP client
  -> /api/mcp/ Streamable HTTP
  -> MCPAuthMiddleware + MCPTransportMiddleware
  -> request-scoped MCPContext
  -> low-level MCP Server callbacks
  -> curated ToolCatalog
  -> feature tool adapter + strict input/output contracts
  -> existing domain service / model / task
  -> compatible CallToolResult + resource links
  -> client reads overmind:// resources or polls a job receipt
```

The entrypoint is `overbae/api/mcp.py`; ASGI mounts it through
`overbae/asgi.py` as the outer Starlette app with Django at `/`. `/api/mcp/`
never runs Django's `request_started`/`request_finished`, so
`MCPAuthMiddleware` gives each request its own thread-sensitive executor and
closes that executor's DB connections on exit. A slow tool must not serialize
unrelated authentication, job reads or prompt discovery behind it.
`overbae/services/mcp/server.py` owns the official MCP SDK server, stateless
Streamable HTTP transport, protocol checks, resource and prompt callbacks, and
middleware ordering. Do not create a second MCP app or mount a feature-specific
server. With a PostHog token (`POSTHOG_PROJECT_TOKEN`, or the committed default on
hosted Clerk deployments without DEBUG), it also instruments the server with
PostHog MCP analytics (`$mcp_*` events, a session-token wrapper on the MCP route,
a flush at lifespan shutdown). Events identify the caller by Clerk user id, the
Console's distinct id, and carry `project_id`. They are metadata only: `before_send`
drops tool arguments, results and error text (failures keep `error_code`), and
`$exception` capture is off. Argument injection stays off: catalog input models
forbid extra fields. `tests/test_mcp_analytics.py` holds these invariants.

## Layer ownership

| Layer              | Location                            | Responsibility                                                                                                     |
| ------------------ | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Transport          | `services/mcp/server.py`            | MCP protocol, allowed hosts/origins, request-size limit, SDK callbacks.                                            |
| Authentication     | `services/mcp/auth.py`              | Authenticate an account/project API key or MCP OAuth token, enforce credential limits, and bind context.           |
| Context            | `services/mcp/context.py`           | Make immutable `{user, token, project, client_ip}` available only during the request.                              |
| Catalog            | `services/mcp/catalog.py`           | Publish a curated visible tool set, validate contracts, invoke handlers, and turn known failures into MCP results. |
| Contracts          | `services/mcp/contracts/`           | Strict Pydantic input/output models, resource links, page metadata, and job receipts.                              |
| Feature adapters   | `services/mcp/tools_*.py`           | Resolve project-scoped references and adapt a semantic MCP intent onto domain services.                            |
| Domain logic       | existing `services/`, models, tasks | Own business rules, persistence, authorization-sensitive state transitions, and background work.                   |
| Results and errors | `result_compat.py`, `errors.py`     | Preserve all typed output for every client and emit safe, stable error values.                                     |
| Resources          | `resources.py`                      | Read-only, project-scoped entity state and static CLI handoff guidance.                                            |
| Prompts            | `prompts.py`                        | Native multi-step workflow instructions composed from the public catalog.                                          |

Tools must call the domain layer directly. They may share serializers or
entity-resolution helpers where those express domain semantics, but must not
invoke frontend code, Console tool registries, or an internal REST
endpoint.

## Authentication and authorization

Every MCP request is authenticated before the SDK callback runs. Account API
keys and OAuth grants can access active projects belonging to their user;
project API keys remain limited to their one project and allowed IPs. All
credentials enforce public `read` and/or `write` permissions. `list_projects`
returns only accessible projects. The catalog resolves `project_id` against
membership before invoking project handlers; handlers receive the selected
project only through `MCPContext`. Resources accept the same `project_id` as a
query parameter. Account result links retain it. Selection is per request,
never shared session state; missing or inaccessible projects are rejected.

OAuth uses the installed MCP SDK protocol handlers and durable, hashed codes
and tokens in `models/mcp_oauth.py`. Console sign-in and explicit consent grant
account access, including future memberships. Public clients register with
auth method `none` and S256 PKCE. Authorization and token exchange require the
exact `MCP_SERVER_URL` resource. Access tokens expire after one hour; refresh
tokens rotate without a time-based expiry; authorization continues until revoked.
Refresh-token reuse revokes the family. Account status and project memberships
remain enforced on every request.
OAuth credentials work only on MCP. API keys remain supported through
`X-Api-Key` or `Authorization: Bearer`.

Set `MCP_SERVER_URL` to the deployed HTTPS `/api/mcp/` URL (local loopback HTTP
is supported). Only configured OAuth servers advertise a Bearer challenge and
discovery/registration routes. `OPENAI_APPS_CHALLENGE` serves the public domain
verification token at `/.well-known/openai-apps-challenge`.

`ToolDefinition.required_scopes` records the product capability a tool needs
(`overmind:read`, `overmind:data:write`, and so on). Catalog visibility
currently enforces the public `read_only`/`read` versus mutation/`write`
boundary. If more granular API-key enforcement is introduced, implement it in
the catalog/auth layer for every tool—do not add one-off handler checks.

The public surface remains permission-scoped read/write. Deliberate lifecycle operations are `cancel_dataset`, `retry_deployment`, `retry_data_partition`, native-comparison pause/resume, and performance resume. Do not infer generic destructive operations from these exceptions. A comparison pause prevents new claims; in-flight work may complete and retain receipts. Unknown provider submissions cannot be replayed without reconciliation.

Contract 5.1 retains draft → optional background preparation → explicit launch. `run_inference` requires a stable request key and returns an `inference_request` job; read its result with `get_job`. Unknown acknowledgements are reconciled without another submission. `inspect_operation` pages durable operational events; reads never invoke workers. `overmind://interface/current` and `list_projects` return the request-derived MCP endpoint, Console origin and permission-scoped tool count/catalog fingerprint. Compare these with the intended environment and refresh `tools/list` on a mismatch; never silently change endpoints or credentials. The interface resource also returns lifecycle rules; it does not attest that remote GPU images are deployed. Ordinary action receipts stay compact and point to detailed resources. Nested partition, sampling, workload and inference settings are typed; training hyperparameters are an intentional model-dependent extension validated by the training serializer/catalog. Manifest budget is 72 KiB including the five reusable-pipeline lifecycle tools and their typed inputs. Package bytes remain CLI-guided; script execution and binding lifecycle are MCP-ready. Pipeline revisions return explicit `flow` nodes, input edges, conditions and output. Native agents declare script conditions with code-line provenance; measured branch outcomes remain separate run facts. Forks execute from their declared parents; compatible disjoint branches converge through inputs. Cycles and job-level conditional skipping are not supported.

## Adding or changing a tool

Contract 5.2 adds `author-dataset-transformation` and
`inspect_dataset_workbench(pipeline=REVISION_UUID)` for exact recipe lookup,
paged family history and revision-scoped runs/bindings. Stale family writes return
`revision_conflict`. The server instructions explicitly permit approved isolated
script packages; semantic/provider work remains native-agent-owned. The interface
resource declares row-routing limits and unmeasured branch-coverage guarantees.

Contract 5.3 returns project-bound upload argv for drafts, row-check scopes and
failed check details, queue age/poll interval/terminal time for Workshop runs,
and a separate unmeasured task-suitability assessment in training readiness.
Prompts and server instructions require capability inspection and a verified
platform handoff; local artifacts alone do not complete Overmind preparation.
Workbench results carry project scope and executable next_actions; dataset inspection
points to the exact active pipeline receipt instead of an earlier landing job.

Contract 5.4 adds `inputs` for distinct
earlier parents, concatenated in declared order. Declarative `merge` retains their
disjoint observations; overlapping source_row identities fail. Flow exposes terminal
and unconsumed steps; receipts retain per-input counts, fingerprints and cell IDs.
Training preparation converges intended branches to trainable output, with unresolved
review evidence preserved. This is not a relational key join or job-level skipping.

Contract 5.5 adds cell `transformation` attribution to `inspect_dataset`: exact
completed run, revision, package checksum and entrypoint, checked against publication
membership and output fingerprint. External imports and unrecorded history never
claim platform execution. Existing package resources share checksum-verified file
pagination with REST; read every retained helper/manifest without executing code.
Author meaningful logic in each entrypoint and only reusable functions in helpers.

Contract 5.6 keeps Workshop polling, idempotent run receipts and workbench history
compact: measured counts, checks, identities and timings remain inline; each run's
`evidence_resource` retains the original row examples and preview samples. Reading
that resource never executes work. Query results over 200 columns or 32 KiB fail
explicitly without clipping data or column metadata.

Contract 5.6.1 exposes PDF native-text recovery facts through existing source
inspection and upload guidance. Recovery engine, pages and measured encoding
artifacts are distinct from OCR confidence and semantic/visual correctness.

Contract 5.7 adds measured script runtime phases and preview duration, a bounded
query deadline, actionable package-parameter diagnostics and checksum-verified
original-source CLI exports. Native training readiness returns only the eligible
catalogue, not chat rankings or heuristic prices. `batch_size` is the effective
optimizer batch; unsupported accumulation/micro-batch overrides are rejected
before dispatch and in estimates with a recoverable authored diagnostic.
Training estimates preserve the requested baseline choice. New Modal charges and
forecast duration evidence use deduplicated worker receipts, not collector delay;
`recorded_basis` exposes the saved charge calculation. Terminal training timelines
are closed by finalization or passive local reconciliation, without another provider
call. Historical unverified charges and all-in invoice components remain explicit.

Contract 6.1 adds `inspect_training_progress` and `cancel_finetune`. Monitoring
policy, checks, generated evidence, sample identities and retained checkpoint
metadata are MCP-ready; charts are frontend-only and checkpoint bytes remain a
CLI handoff. Inspection reads persisted receipts without invoking workers.
Readiness/preparation/estimate/launch accept the same strict monitoring policy;
provider capabilities and unmeasured check costs remain explicit. Cancellation
records intent first and preserves durable evidence. Evidence responses have a
128 KiB lossless bound and return an error rather than clipping values. The current
tool-manifest budget is 80 KiB. Refresh the connected catalogue after deployment.
Native per-question metrics and assessments have `collections` descriptors in
check/checkpoint overviews. Pass a descriptor's `field` JSON Pointer with
`offset`/`limit` for exact retained object entries or array items. Append escaped
keys or indices to inspect a nested field that exceeds the response limit.
`field`, `check` and `probe` are mutually exclusive and job-scoped; REST
monitoring-evidence uses the same field pagination.
Check `facts.delivery` reports the first valid loss result's collector receipt time
and check-finish observation time. These are not worker timestamps or user-feedback
scores; passive inspection never emits another lifecycle analytics event.

Contract 6.2 adds `generation.kind=json_fields` with bounded explicit JSON Pointers
through the existing shared monitoring policy. Model-catalogue discovery advertises
it; no new tool is needed. Checks expose per-field and complete-example pass rates,
unscorable references and technical coverage. This is MCP-ready; the Console uses
the same receipts. Field meanings stay native-agent-owned. Worker deployment is
required for new runs; existing pinned runs retain their original contract.

Contract 6.2.1 retains `latest_generation_check` in compact job monitoring even
when a newer check contains only loss. Receipt-collected classification
`facts.assessment` compares the same scored subset with its majority-label baseline
and identifies represented labels without predictions. Source receipt, rule
version, coverage and assessment time are separate from immutable worker metrics.
Reads never derive missing assessments or change training. This is MCP-ready
through existing job/inspection resources; no new tool or worker deployment.

Contract 6.0 requires a retained Python package for every new Workshop revision.
The save schema removes inline steps; shared services reject package-free historical
revisions for validation, new runs and bindings. Historical receipts remain readable
with original attribution. Workbench authoring facts replace the operation catalogue;
pipeline.executable identifies package-backed revisions, not verified semantic quality.
MCP input errors point to pipeline-upload and overmind://dataset-upload; optional
installed guidance is not the enforcement boundary.

`get_model_catalog` also returns each foundation's nullable `openrouter_id` and
`openrouter_status`. Availability is catalogue evidence, not inference success or
credential readiness. The shared benchmark resolver uses the same exact identities;
`not_listed` and `catalog_unavailable` are distinct from an available provider route.

1. Classify the change above. Reuse a current tool when the agent intent is
   unchanged; do not mirror a REST endpoint merely because it exists.
1. Add strict Pydantic request and response models under
   `services/mcp/contracts/<domain>.py`. Extend `MCPModel`, bound collection
   sizes and strings, and reject unknown fields. Use aliases only when they
   preserve a deliberate public compatibility contract.
1. Put the handler in the matching `services/mcp/tools_<domain>.py` module.
   Resolve all entities within `context.project`, call the existing domain
   service/model/task, and translate expected failures to `MCPError`.
1. Register one `ToolDefinition` through that module's
   `register_<domain>_tools` function. Declare accurate `read_only`,
   `idempotent`, `open_world`, `required_scopes`, `cost_class`, and
   `async_mode` metadata. The central `CATALOG` imports feature registrations;
   add a new import there only when introducing a genuinely new domain module.
1. Return the declared output model, never a raw dictionary. The catalog
   validates it before publishing. Add a `resource` or `resource_links` field
   for durable entities that the agent can inspect next.
1. For background work, return a `JobReceipt`-shaped object with `kind`, `id`,
   `status`, and an `overmind://jobs/{kind}/{id}` resource. Ensure `get_job`
   and the resource reader understand that job kind before shipping.
1. Add a prompt only when the public tool sequence needs reusable guidance or
   a human approval checkpoint. A prompt coordinates tools; it never becomes a
   hidden implementation of a state change.

Keep handlers thin. If a Console workflow lacks a reusable domain service, fix
that service boundary first and have both surfaces call it. Do not copy the
Console view's business logic into `tools_*.py`.

## Result and error contract

`ToolCatalog.call` validates the input model, runs the handler, validates the
output model, then passes its JSON form to `tool_result`. `tool_result` emits
the same complete object in two forms:

- `structuredContent` for MCP clients that preserve structured fields.
- Compact JSON `TextContent` for clients such as `CallDynamicTool` that only
  expose text to the model.

When output contains `resource` or `resource_links` matching
`ResourceLinkContract`, `result_compat.py` also emits deduplicated native MCP
`ResourceLink` content. Do not place identifiers, cost estimates, row data,
or a job receipt only in prose; they must be fields of the output contract.

Expected failures raise `MCPError`, which becomes
`{"error": {"code", "message", "retryable", "fields"}}` with
`isError=true` and the same JSON-text compatibility. Unexpected failures become
the safe `internal_error`; do not leak exceptions, provider responses, tokens,
or tracebacks. Resource reads use MCP protocol errors for malformed or missing
URIs, but must retain the same project boundary and safe message discipline.

## Resources, jobs, and local-file workflows

Resources are durable, read-only state—not a second mutation API. Add a
resource template when a tool returns an entity the agent needs to reread,
resume, or inspect. Implement its project-filtered payload in
`services/mcp/resources.py`, register its template, and produce links with
`resource_link`; do not manufacture URI strings in individual handlers.

`safe_json` is the resource serialization boundary. It bounds output and
removes sensitive fields. Keep access tokens, credentials, API keys, cookies,
private material, presigned URLs, and checkpoint URLs out of both tool and
resource output.
Known numeric token measurements (such as `tokens`, `supervised_tokens`,
`trained_tokens`, `max_tokens`, and `padded_tokens`) remain visible; strings, containers, and nonfinite values under
those keys do not bypass credential redaction.

Deployment job reads expose the shared deployment stage and its real status-change
timestamp. Activation progress exposes its deadline and next scheduled poll;
terminal activations have no next poll. These are scheduling facts, not worker
heartbeats or evidence of forward progress.

Operational receipts expose source and observation time independently, measured
counters and a paginated event timeline. Provider events are copied by background
controllers into durable project-scoped records; MCP inspection reads only those
records. Shared-pool startup is labelled separately from request-specific adapter
and generation events. Missing telemetry stays unavailable, with provider cursor
backlog and dropped-publication counts visible. Never expose raw provider logs,
prompts, credentials or another tenant's adapters as diagnostics.

Native evaluation job reads summarize every benchmark's coverage and macro
metrics before bounding the response. They omit duplicated score receipts and
per-benchmark details, and expose authenticated `report.json_path` and
`report.markdown_path` downloads on the same server. The complete report retains
all benchmarks, paired intervals and diagnostic slices; calibration results stay
marked in-sample. Never present a bounded benchmark preview as complete results.

Dataset transfer receipts use get_job(kind=dataset_transfer) and its job resource.
The CLI performs a read-only MCP handshake and project-scoped transfer preflight
with one resolved connection; it never certifies another sandbox's permissions.
Stable transfer keys survive client interruptions and publication retries. The
interface resource advertises transfer_protocol_version. JSON wrapper selection
is explicit, not semantic inference.

MCP carries JSON state, not local binary bytes. For uploads, exports,
checkpoints, repository edits, or local execution, return or link the
appropriate CLI guidance resource and give the coding agent exact IDs and
arguments. The CLI/SDK performs the filesystem action; MCP resumes at the
resulting build, dataset, deployment, or job resource.

## Prompts and tool catalog discipline

Prompts in `services/mcp/prompts.py` are named, parameterized public recipes.
They should name the public tools/resources to call, include approval and
human-action boundaries, and finish with a decision checkpoint. Do not add a
prompt to compensate for a missing primitive tool, and do not add a tool merely
to support a one-off prompt sentence.

Keep the catalog organized by user intent: discovery/read, mutation,
background work, and CLI/local handoff. Tool descriptions should say the goal,
the important constraint, and the returned next state. A broad search or
inspection tool is preferable to a cluster of near-duplicate filters; distinct
state transitions deserve distinct mutation tools.

## Required tests

Follow the testing policy in AGENTS.md. For every MCP change, verify the applicable outcomes below through existing E2E coverage first. The listed files locate existing focused coverage; they are not a requirement to add unit tests after implementation. If isolation is necessary, document failure modes before writing code.

| Change                            | Minimum proof                                                                                                                                      |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tool contract or catalog metadata | Schema, annotations, permission visibility, input rejection, output validation in `tests/test_mcp_catalog.py` or the domain test.                  |
| Feature tool                      | Happy path, project isolation, expected errors, complete structured output, and returned resource/job identifiers in `tests/test_mcp_<domain>.py`. |
| Result format                     | JSON text exactly matches `structuredContent`; resource links are emitted and deduplicated in `tests/test_mcp_result_compat.py`.                   |
| Resource or job kind              | Project scoping, safe redaction, not-found behavior, and transport read in `tests/test_mcp_resources.py`.                                          |
| Auth or transport                 | Project-scoped key, read/write boundary, headers, protocol, and origin/host behavior in `tests/test_mcp_authorization.py`.                         |
| Prompt or CLI handoff             | Prompt arguments and rendered workflow in `tests/test_mcp_prompts.py`; CLI command behavior in `overmind/tests/` when it changes.                  |

Run the relevant MCP tests plus `pre-commit run --files` for changed files. If
the feature also changes the REST contract used by the Console, regenerate the
frontend API client as part of that API change; MCP tools themselves do not use
the generated client.

## Completeness checklist

Before shipping an agent-relevant change, verify all applicable items:

- [ ] The MCP classification and, if omitted, its concrete reason are recorded
  in the PR or implementation plan.
- [ ] The MCP tool/resource/prompt uses the shared domain service and respects
  existing project-scoped API-key authorization.
- [ ] Inputs, full structured outputs, JSON text compatibility, resource links,
  and async receipts are defined and tested.
- [ ] The entity has a read path after creation or mutation, including polling
  for background work.
- [ ] Local-file workflows have CLI guidance rather than an MCP byte-transfer
  workaround.
- [ ] The curated catalog, prompt list, resource list, user-facing Overmind
  skill (`overmind/skills/overmind/`), and MCP tests are updated together.
  Regenerate API clients when the API contract changed.

Training experiment changes expose `request_key`, explicit selection and contract receipts in readiness/estimate/start, elapsed time and remaining-time ranges with their recent metric-window evidence in `get_job`, and the generated run record in the finetune resource. `schedule_native_evaluation` is a metered GPU operation with train scope; its paired plan is a `native_evaluation` job/resource. `cancel_dataset` prevents pipeline publication or requests source-landing cancellation; it does not cancel external coding-agent or model-provider execution. Both routes share Console domain services and project scoping.

Dataset inspection uses cell_offset/cell_limit pagination, with active identity separate from the page. Large summaries expose truncation metadata; scripts, audit internals and previews are bounded before transport serialization. Reading a dataset never computes a missing whole-source profile synchronously; an unmeasured profile points to explore_dataset. These limits apply to the dataset resource and named inspection tool.

Contract 6.3.0 adds stored native decision-quality summaries, including applicable same-subset categorical assessments, to training progress. Native training uses the pinned Unsloth decision runtime through the existing preparation, launch, cancellation, records and operational-inspection tools.

Native comparison scoring stores a checksum-bound report receipt and compact metrics; full diagnostic slices remain downloadable. To recover interrupted local work on a paused comparison, pass its local `verify_inputs`, `fit_calibration` or `score` stage to `resume_native_evaluation`. This fences the prior local lease and reuses retained inputs; it does not resubmit provider calls.

A paused native evaluation collector can resume an explicit stage with its exact recorded `call_id`. Missing or different IDs are rejected; recovery fences the interrupted lease and observes the same provider call without a new submission.

Contract 6.4.0 adds `resume_dataset_import` for stopped source imports. Dataset
inspection and dataset-run jobs expose the saved source-import state and retry
availability. Recovery shares the REST and Console service and never starts an
autonomous preparation agent.

Contract 6.5.0 adds the shared `preparation` graph to Workshop inspection and
manifest guidance for explicit row batching and consumer validation. Corrections
replace the displayed process; there is no repair-history/restore UI. Source
transfer and package bytes are CLI-guided; execution, validation and lineage
inspection are MCP-ready. MCP readiness reuses its prerequisite validation result
instead of scanning the full training/validation population twice.

Contract 7.0.0 removes the preparation-findings tools and status fields. Workshop continues to expose retained scripts, the selected process, execution receipts and publication validation. Dataset-scoped pipeline saves automatically retain the current script association; retrying older receipts does not restore it.
