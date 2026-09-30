---
name: overmind-integrations
description: Connect external tracing providers to Overmind, inspect connector health, review capability-boundary mappings and verify imported traces. Use for the Integrations surface; repository SDK instrumentation belongs to Observability.
---

# Overmind Integrations

Start with `list_projects` and choose the intended accessible project. For an
account connection, pass its `project_id` on every project tool and resource
URI query; follow returned links. Project API keys retain their narrower access.

Bring external traces into the correct project with explicit boundary mapping.
Use the configured Overmind MCP connection and read `overmind://project/current?project_id=ID`.
The provider's source project and the Overmind destination are different identities.

## Inspect and authenticate

Start with `inspect_connectors`. For a supported provider, prefer the native
`connect-traces` prompt. Use the server's available provider list and setup
commands rather than assuming that a named service is supported.

Credentials are not MCP arguments. If inspection returns
`connector_setup_required`, show its CLI command and let the human enter the
provider credential in their terminal. Do not ask for a key in chat, export it,
or run an interactive credential flow in a non-TTY sandbox. Read
`overmind://connector-setup` for the current handoff. Continue after the human
confirms completion or supplies the non-secret connector ID.

## Review the mapping

Inspect the connector with `include_source_projects=true`. Resolve the chosen
source project and lookback. Examine `observation_shapes`, `suggested_boundaries`,
`alternatives`, `unmapped_roots` and `mapping_options`.

`mapping.names` declares capability boundaries that become Overmind trace roots.
Default to the suggested parent observation names so children remain nested.
An alternative can replace that parent when chosen; do not include both it and
its ancestor. Tool/LLM spans and other nested names are not independent roots
unless the human chooses that boundary.

Use `configure_connector` to propose the selected source, lookback and mapping
without `confirm_mapping`. Present the concrete mapping and the import-unmapped
option, then wait for its review. After approval, confirm that same mapping with
`confirm_mapping=true`. Do not silently broaden the import window or destination.

## Sync and verify

Run the authorized `sync_connector`, poll with
`get_job(kind=connector_sync, id=...)`, then query the imported traces. Report imported/skipped counts, failed work and remaining
unmapped roots from the actual result. A stored credential or confirmed mapping
is not proof that traces arrived.

For inspection-only work, read `overmind://connectors/{connector}` and status
without starting another sync. There are no MCP deletion or credential-rotation
tools to invent.

Return the source/destination identities, mapping decision, sync outcome and
`console_traces_url`. For configuration, open `observability/integrations` under
the project's Console base with its `projectId`.
