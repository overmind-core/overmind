---
name: mcp
description: End-to-end workflow for adding or changing Overmind MCP tools, resources, prompts, authentication, or result contracts — server layers, catalog registration, MCP-impact classification, contract versioning and required tests. Use when adding, changing, or removing an MCP tool, resource, prompt, auth rule, or CallToolResult contract.
---

# Adding or changing MCP

The MCP server at `/api/mcp/` is the project-scoped agent API. It shares domain services with the Console and REST API; it does not proxy either. This skill is for changing the server. The skills that `overmind init` installs for users live in `overmind/skills/`; never copy this file there.

## MCP-impact classification

Every new or changed capability, API workflow or Console workflow needs an explicit MCP decision in the same change. Classify it:

- **MCP-ready** — an agent can discover, inspect or progress the workflow. Add or update the smallest tool, resource or prompt.
- **CLI-guided** — the workflow needs local files, repository edits, binary transfer or third-party credentials. MCP returns state, exact IDs and a structured action; the CLI or SDK does the local work.
- **Frontend-only** — presentation, navigation, visual exploration or billing with no useful agent action. The underlying state stays readable through MCP when agents need it.
- **Out of scope** — destructive operations stay off the public surface until designed and authorized.

Record the reason when a change is not MCP-ready. The Console must not become the only way to finish an agent-relevant workflow. Textual state is the baseline; charts may stay visual.

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
  -> CallToolResult + resource links
  -> client reads overmind:// resources or polls a job receipt
```

`overbae/api/mcp.py` is the entrypoint. `overbae/asgi.py` makes the MCP Starlette app the outer ASGI app and mounts Django inside it at `/`; its middlewares pass non-MCP paths through. `/api/mcp/` and the OAuth routes never reach Django's `ASGIHandler`, so the outermost `RequestThreadMiddleware` gives each request a `ThreadSensitiveContext` (without it every thread-sensitive `sync_to_async` call shares one thread) and closes the request's DB connections. `services/mcp/server.py` owns the SDK server, stateless transport, protocol checks and middleware order. Never create a second MCP app or a feature-specific server.

With a PostHog token, the server sends `$mcp_*` analytics. Events carry identity, `project_id` and, on failure, `error_code`: `before_send` drops arguments, results and error text, `$exception` capture is off, and input models forbid extra fields so argument injection stays off. `tests/test_mcp_analytics.py` holds these rules.

## Layer ownership

| Layer              | Location                                 | Responsibility                                                                         |
| ------------------ | ---------------------------------------- | -------------------------------------------------------------------------------------- |
| Transport          | `services/mcp/server.py`                 | Protocol, allowed hosts and origins, request-size limit, SDK callbacks                 |
| Authentication     | `services/mcp/auth.py`, `oauth.py`       | API keys and OAuth tokens, credential limits, context binding                          |
| Context            | `services/mcp/context.py`                | Immutable `{user, token, project, client_ip, inference_base_url}` for the request only |
| Catalog            | `services/mcp/catalog.py`                | Curated visible tools, contract validation, handler calls, known failures              |
| Contracts          | `services/mcp/contracts/`                | Strict Pydantic inputs and outputs, resource links, pages, job receipts                |
| Feature adapters   | `services/mcp/tools_*.py`                | Resolve project-scoped references and map an intent onto domain services               |
| Domain logic       | `services/`, `innermind/`, models, tasks | Business rules, persistence, state transitions, background work                        |
| Results and errors | `result_compat.py`, `errors.py`          | Same typed output for every client; safe, stable errors                                |
| Resources          | `resources.py`                           | Read-only, project-scoped state and CLI handoff guidance                               |
| Prompts            | `prompts.py`                             | Multi-step workflow recipes composed from the public catalog                           |

Tools call the domain layer directly. They may share serializers or entity-resolution helpers, but never call frontend code, Console registries or an internal REST endpoint. If a Console workflow has no reusable service, fix the service boundary first.

## Authentication

- Every request authenticates before the SDK callback. Account API keys and OAuth grants reach the user's active projects; project keys stay on one project. Every API key enforces its allowed IPs, and all credentials enforce `read` and `write`.
- Account credentials pass `project_id` per operation; `list_projects` returns accessible projects. The catalog checks membership before the handler runs, and handlers get the project only from `MCPContext`. Selection is per request, never session state.
- OAuth uses the SDK protocol handlers with hashed codes and tokens in `models/mcp_oauth.py`. Public clients register with auth method `none` and S256 PKCE, against the exact `MCP_SERVER_URL` resource. Access tokens last one hour; refresh tokens rotate, and reuse revokes the family. OAuth works only on MCP; API keys use `X-Api-Key` or `Authorization: Bearer` (or `Api-Key`).
- `ToolDefinition.required_scopes` records the capability a tool needs; visibility enforces read versus write. Granular enforcement, if added, belongs in the catalog or auth layer for every tool, never in a handler.
- Tool names containing `delete`, `remove`, `cancel`, `retry` or `undeploy` are rejected unless they are in `_ALLOWED_LIFECYCLE_TOOLS` (`catalog.py`); the destructive ones are also in `_DESTRUCTIVE_TOOLS`. Adding to either list needs explicit approval. Do not infer generic destructive operations from the existing entries.

## Adding or changing a tool

1. Classify the change. Reuse a tool when the agent intent is unchanged; do not mirror a REST endpoint because it exists.
1. Add strict request and response models in `services/mcp/contracts/<domain>.py`. Extend `MCPModel`, bound collections and strings, reject unknown fields.
1. Put the handler in `services/mcp/tools_<domain>.py`. Resolve entities inside `context.project`, call the domain service, and raise `MCPError` for expected failures.
1. Register one `ToolDefinition` in that module's `register_<domain>_tools` with accurate `read_only`, `idempotent`, `open_world`, `required_scopes`, `cost_class` and `async_mode`. Import a new domain module into `CATALOG` only when it is genuinely new.
1. Return the declared output model, never a raw dict. Add `resource` or `resource_links` for durable entities.
1. For background work, return a `JobReceipt` with `kind`, `id`, `status` and an `overmind://jobs/{kind}/{id}` resource, and teach `get_job` and the resource reader that kind.
1. Operations that start paid or long work take a stable `request_key`; an identical retry returns the original receipt and a changed payload conflicts.
1. Bump `CONTRACT_VERSION` in `services/mcp/__init__.py` with every contract change. `overmind://interface/current` publishes it with the catalog fingerprint.
1. Keep `tools/list` within `MAX_MANIFEST_BYTES` (`tests/mcp_fixtures.py`); output schemas and secrets stay out of discovery.

Keep handlers thin. Never copy a Console view's business logic into `tools_*.py`.

## Results and errors

`ToolCatalog.call` validates input, runs the handler, validates output, then `tool_result` emits the same object as `structuredContent` and as compact JSON text for clients that read only text. Matching `resource`/`resource_links` also become native `ResourceLink` content. Identifiers, costs, rows and receipts are contract fields, never prose only.

`MCPError` becomes `{"error": {"code", "message", "retryable", "fields"}}` with `isError=true`. Unexpected failures become a safe `internal_error`. Messages come from authored text or authored domain errors; never leak raw exceptions, provider responses, tokens or tracebacks.

Bound tool responses losslessly: page large results, or fail with a typed error such as `query_result_too_large`, rather than clip values. Action receipts stay compact and point to a resource with the full evidence.

## Resources, jobs and local files

- Resources are durable, read-only state, not a second mutation API. Implement the project-filtered payload in `resources.py`, register its template, and build links with `resource_link`.
- `safe_json` is the resource serialization boundary. It drops keys that name a token, secret, password, cookie, credential, authorization, API key or private key (numeric token counts stay), and it truncates strings at 12,000 characters and collections at 100 entries. `safe_finetune_progress` also removes presigned and checkpoint URLs.
- Reads never invoke a worker or a provider. Operational, monitoring and evaluation evidence come from receipts that background controllers saved.
- MCP carries JSON, not local bytes. Uploads, exports, checkpoints, repository edits and local execution return a CLI guidance resource with exact IDs and argv; MCP resumes at the resulting resource.
- Essential client-independent guidance belongs in server instructions, tool descriptions and resources. Plugins and installed skills are optional; never make one a prerequisite or the enforcement boundary. The `overmind/.{claude,codex,cursor}-plugin/` versions match `SKILLS_VERSION` in `overmind/overmind/skills_db.py`.

## Prompts and catalog discipline

Prompts are named, parameterized recipes. They name the public tools to call, mark approval and human-action points, and end with a decision checkpoint. Do not add a prompt to cover a missing tool, or a tool for one prompt sentence.

Organize the catalog by intent: discovery and read, mutation, background work, CLI handoff. A description states the goal, the main constraint and the next state. Prefer one broad inspection tool to near-duplicate filters; distinct state transitions deserve distinct tools.

## Tests

Follow the testing policy in AGENTS.md: prove the change through existing E2E coverage first. These files hold focused coverage:

| Change                       | Coverage                                                                                                                              |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Contract or catalog metadata | `tests/test_mcp_catalog.py`, `tests/test_mcp_manifest.py`                                                                             |
| Feature tool                 | `tests/test_mcp_<domain>.py`: happy path, project isolation, expected errors, structured output, returned IDs                         |
| Result format                | `tests/test_mcp_result_compat.py`                                                                                                     |
| Resource or job kind         | `tests/test_mcp_resources.py`                                                                                                         |
| Auth or transport            | `tests/test_mcp_authorization.py`, `test_mcp_oauth.py`, `test_mcp_api_keys.py`, `test_mcp_account_access.py`, `test_mcp_transport.py` |
| Prompt or CLI handoff        | `tests/test_mcp_prompts.py`; `overmind/tests/` for CLI behavior                                                                       |

## Completeness

- [ ] The MCP classification, and the reason when not MCP-ready, is in the PR.
- [ ] The tool, resource or prompt uses the shared domain service and project-scoped authorization.
- [ ] Inputs, structured outputs, resource links and async receipts are defined and tested.
- [ ] The entity has a read path after creation, including polling for background work.
- [ ] Local-file workflows use CLI guidance, not an MCP byte-transfer workaround.
- [ ] `CONTRACT_VERSION`, the catalog, prompts, resources, the user-facing skills in `overmind/skills/` and the MCP tests change together. Regenerate the API client when the REST contract changed.
