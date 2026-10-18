---
name: overmind-agent
description: Inspect an Overmind project's agent map, capabilities, repository provenance and evaluation coverage. Use for understanding what the agent does or choosing which capability to improve.
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
means the scanned revision is unknown; `last_synced_at` is not the scan time.
Live traces can come from another revision.

Resolve a supplied capability through `overmind://capabilities/{capability}`.
For project-wide orientation, `get_instrumentation_plan` without a capability
returns registered placements and any required local action. Use returned
identities; there is no `list_capabilities` tool. Placements and observed
executions are evidence about the map, not proof of complete coverage.

Inspect the capability's status, task context, dataset, eval set, benchmark and
live model. A leftover capability is an absent scan result, not a deletion
request. MCP cannot create capability cards by scanning a repository.

## Connect contracts to evidence

When prompts are supported, use `investigate-capability` with the resolved
capability. Otherwise combine `inspect_capability_health` and `query_failures`
as needed. Follow relevant trace resource links.

The capability resource lists the `eval_matrix` rows that score it and the
Default set's `eval_preload` counts. Spans without a capability id indicate a
mapping gap; low scoring coverage means spans arrive without evidence the
matrix can judge. Missing scores do not demonstrate success.

Choose the next action from the evidence: refresh the repository map, instrument
an uncovered entry point (Python codebases only; report any other language as not
applicable), prepare data, repair evaluation coverage or investigate
a measured failure. Do not automatically start those mutations during a map review.

## Read capability state

A capability is the project-scoped product AI surface; use the word capability in
MCP calls, with its eval matrix rows and scored traces underneath it.
`overmind://capabilities/{capability}` returns safe metadata: name, slug, stored
status, model, active dataset, active eval set, active model, selected benchmark and
ready trained benchmark candidates. A reference may be a name, slug or id where the
tool schema accepts it. `get_instrumentation_plan` with no capability returns
placements for every current capability and `inspect_capability_health` reads one.
The resource may report `current`, `leftover` or `deleted`; workflow tools resolve
active current capabilities, and MCP does not reactivate or remove a capability.

## Changes and handoffs

Repository discovery runs locally through the installed Overmind setup workflow
([local repository setup](../overmind/references/setup.md)) and
`overmind sync`; MCP cannot inspect an arbitrary Git provider, create capability
cards or edit files. If the project has no instrumentation plan or capability data,
run local `/overmind setup`, preserve existing project and capability ids and
configuration, run `overmind sync`, then re-read the capability resource and request
`get_instrumentation_plan`. Never invent a capability id. If local tooling is
unavailable, report that handoff and retain the current map.

`benchmark_model` selects the incumbent for future comparisons; `active_model`
controls live serving. Preserve both during a map review. Use their respective
mutation tools only for the user's requested selection.

- `set_active_model` checks a ready deployment, wakes it and verifies inference
  before switching the capability alias. Poll the returned `model_activation` job
  with `get_job`; the old selection stays active until the job is `complete`. Read
  the capability resource for activation progress, previous selection and successful
  application traffic timestamps. Repeat the selection to retry a failed activation;
  omitting the deployment clears routing and cancels a pending switch. Fine-tune
  rollout uses `get_model_swap_prompt` only after a deployed fine-tune is ready, and
  applying that prompt stays a human action
  ([Inference](../overmind-inference/SKILL.md)).
- `set_benchmark_model` selects a ready trained deployment for future incumbent
  evaluations; read `benchmark_candidates` on the capability resource for the
  choices, and omit `deployment` to use the codebase incumbent. It is independent of
  `set_active_model` and does not change serving. The Console exposes the same
  selection on the capability's Models tab, not in training setup. New training jobs
  pin the selection; changing it does not retarget existing jobs.

Return the capability identities, observed coverage, provenance limitations and
the next useful action. For a visual view, open the project's `console_url`;
for one capability use `capabilities/{id}`, preserving its base and `projectId`.
