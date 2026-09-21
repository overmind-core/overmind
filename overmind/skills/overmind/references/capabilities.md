# Capabilities

A capability is the project-scoped product AI surface. Use the word
capability in MCP calls; behaviours and task executions belong underneath it.

## Read capability state

Read `overmind://capabilities/{capability}` for safe capability metadata,
including name, slug, stored status, model, active dataset, active eval set,
active model, selected benchmark and ready trained benchmark candidates. A capability reference may be a name, slug, or id where the
tool schema accepts it. `get_instrumentation_plan` with no capability returns
placements for every current capability; `inspect_capability_health` reads
one. There is no `list_capabilities` tool.

The resource may report `current`, `leftover`, or `deleted` state. Current
workflow tools resolve active current capabilities; do not reactivate or remove
a capability through MCP.

## Local discovery

MCP cannot scan a repository or create capability cards. If the project has no
instrumentation plan or capability data:

1. Run local `/overmind setup` to scan the repository and write capability
   metadata.
1. Preserve existing project/capability ids and configuration.
1. Run local `overmind sync`.
1. Re-read the capability resource and request `get_instrumentation_plan`.

Never invent a capability id or ask the MCP server to analyze a Git provider.

## Active model

`set_active_model` changes or clears a capability's active deployment. It
accepts a ready deployment reference; omitting the deployment clears the
active model. Verify the result from the capability and deployment resources.

Fine-tune rollout uses `get_model_swap_prompt` only after a successful deployed
fine-tune is ready. Applying the prompt stays a human action.

## Benchmark model

`set_benchmark_model` selects a ready trained deployment for future incumbent
evaluations. Read `benchmark_candidates` on the capability resource for available
choices. Omit `deployment` to use the codebase incumbent instead. This choice is
independent of `set_active_model`: it does not change serving. The Console exposes
the same selection on the capability's Models tab, not in training setup.
New training jobs pin the selection; changing it does not retarget existing jobs.
