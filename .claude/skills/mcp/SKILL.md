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
`overbae/asgi.py`. `overbae/services/mcp/server.py` owns the official MCP
SDK server, stateless Streamable HTTP transport, protocol checks, resource and
prompt callbacks, and middleware ordering. Do not create a second MCP app or
mount a feature-specific server.

## Layer ownership

| Layer              | Location                            | Responsibility                                                                                                     |
| ------------------ | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Transport          | `services/mcp/server.py`            | MCP protocol, allowed hosts/origins, request-size limit, SDK callbacks.                                            |
| Authentication     | `services/mcp/auth.py`              | Authenticate the API key, require exactly one active project, enforce allowed IPs, and bind context.               |
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

Every MCP request is authenticated before the SDK callback runs. A usable key
must be active, belong to the user, be pinned to exactly one active project,
and carry the public `read` and/or `write` permission. The authenticated
project comes only from `MCPContext`; handlers never accept a project id or
trust a caller-supplied tenant reference.

Never send `WWW-Authenticate: Bearer` on MCP 401s. Cursor and other MCP HTTP
clients treat that challenge as an OAuth protected resource and POST
`/register`. This surface is `X-Api-Key` only. `Authorization: Bearer <api-key>`
is still accepted as an API key, not as OAuth.

`ToolDefinition.required_scopes` records the product capability a tool needs
(`overmind:read`, `overmind:data:write`, and so on). Catalog visibility
currently enforces the public `read_only`/`read` versus mutation/`write`
boundary. If more granular API-key enforcement is introduced, implement it in
the catalog/auth layer for every tool—do not add one-off handler checks.

The public surface remains read and write only. The catalog rejects destructive
tool names and destructive metadata. Do not add delete, remove, cancel, retry,
or undeploy operations without an explicit public-surface decision.

## Adding or changing a tool

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

For every MCP change, add focused tests at the appropriate layer:

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
