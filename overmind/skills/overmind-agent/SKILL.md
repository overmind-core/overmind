---
name: overmind-agent
description: Inspect an Overmind project's agent map, capabilities, behaviour contracts, repository provenance and evaluation coverage. Use for understanding what the agent does or choosing which capability to improve.
---

# Overmind Agent

Start with `list_projects` and choose the intended accessible project. For an
account connection, pass its `project_id` on every project tool and resource
URI query; follow returned links. Project API keys retain their narrower access.

Turn the Agent surface into an evidence-based account of the project's capabilities,
their contracts and the next improvement to make. The agent is the project;
capabilities are its nodes, not separate agents.

## Establish the map

Use the configured Overmind MCP connection. Read `overmind://project/current?project_id=ID`
for project identity and repository provenance. A missing repository snapshot
means the synced revision is unknown; `last_synced_at` is not the scan time.
Live traces can come from another revision.

Resolve a supplied capability through `overmind://capabilities/{capability}`.
For project-wide orientation, `get_instrumentation_plan` without a capability
returns registered placements and any required local action. Use returned
identities; there is no `list_capabilities` tool. Placements and observed
executions are evidence about the map, not proof of complete coverage.

Inspect the capability's status, task context, dataset, eval set, benchmark and
live model. A leftover capability is absent from the latest decorator sync, not
a deletion request. MCP cannot create capability cards by scanning a repository.

## Connect contracts to evidence

When prompts are supported, use `investigate-capability` with the resolved
capability. Otherwise combine `inspect_capability_health` and
`query_task_executions` as needed. Follow relevant trace resource links.

Distinguish capability, behaviour and task execution: a behaviour is a contract
minted from `task()` declarations and the call graph at sync; an execution is
one observed, bound unit. Separate `declared`, `anchor_join` and `unbound`
executions. Unbound executions indicate a mapping gap; missing scores do not
demonstrate success. There is no behaviour resource or behaviour-list tool to
invent.

Choose the next action from the evidence: refresh the repository map (decorate

- `overmind sync`), instrument an uncovered behaviour, prepare data, repair
  evaluation coverage or investigate a measured failure. Do not automatically
  start those mutations during a map review.

## Changes and handoffs

Repository discovery is local: decorate `@capability` / `@observe` / `task()`
in code, then `overmind sync` AST-scans those call sites and pushes an
AgentManifest. MCP cannot inspect an arbitrary Git provider, edit files, or
author cards into `overmind.toml`. If local tooling is unavailable, report that
handoff and retain the current map.

`benchmark_model` selects the incumbent for future comparisons; `active_model`
controls live serving. Preserve both during a map review. Use their respective
mutation tools only for the user's requested selection.

Return the capability identities, observed coverage, provenance limitations and
the next useful action. For a visual view, open the project's `console_url`;
for one capability use `capabilities/{id}`, preserving its base and `projectId`.
