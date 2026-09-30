---
name: overmind-optimiser
description: Set up, inspect and complete Overmind Optimiser experiments for prompt/code changes or model comparisons. Use when improving a capability through measured candidates and a local repository executioner.
---

# Overmind Optimiser

Start with `list_projects` and choose the intended accessible project. For an
account connection, pass its `project_id` on every project tool and resource
URI query; follow returned links. Project API keys retain their narrower access.

Find a measured improvement while preserving the experiment's own baseline and
the user's selected task. Use the chosen MCP project and resolve all dataset
and capability references from its returned records.

## Choose the experiment

Distinguish prompt/code optimization from model comparison. Prefer the native
`optimize-capability` prompt for the first, or `compare-models` for a selected
model list. Check the current schema for `optimize`, `model_comparison` or
`hybrid` mode; do not silently substitute one for another.

Use `list_datasets`, `inspect_dataset` and `query_dataset` to select a fitting
eval cell. Call `check_optimizer_readiness` with that cell, capability, mode,
eval set and requested models. Explain missing executioner, model, credit or
quota prerequisites. The dataset stays on the server; a local file must be
landed before an experiment can use it.

## Execute within the approved scope

After the mode, candidate scope and spend are authorized, `start_optimizer`
creates the experiment. MCP schedules and reports it; a local executioner runs
repository commands and candidates. Do not claim that a scheduled experiment
has executed or that MCP can apply a repository diff.

Follow the returned `next_action` and exact command in the intended checkout.
Use local `overmind optimise ... --help` for commands unavailable in the client.
If the client cannot run local code, provide that handoff and continue read-only
status inspection. Preserve existing repository changes.

The experiment scores its own baseline iteration. Do not create a separate
baseline run and use it as the improvement gate. Candidate changes must solve
the task rather than encode held-out answers. Stop at the returned completion
or plateau checkpoint; do not extend the search budget without authorization.

## Inspect and land

Use `inspect_optimizer_result`, `overmind://optimizer-runs/{experiment}` and
`get_job(kind=optimizer_experiment, id=...)` for progress, coverage, failures,
candidate scores and the winner. A terminal run may retain the incumbent.
Describe partial coverage and incomplete evaluations before naming a winner.

Present the winning diff and its measured delta against the experiment's own
baseline. Apply it locally only when landing that change is authorized; changing
code, switching the live serving alias and selecting a benchmark are distinct
actions. Report whether a change was merely proposed or actually applied.

Open `optimiser/{experiment_id}` under the project's Console base with the same
`projectId` when a visual result is useful.
