---
name: overmind-inference
description: Inspect Overmind model deployments, serving metrics, worker state and live routing; test inference and activate an approved model. Use for Inference and serving operations, not training or benchmark selection.
---

# Overmind Inference

Start with `list_projects` and choose the intended accessible project. For an
account connection, pass its `project_id` on every project tool and resource
URI query; follow returned links. Project API keys retain their narrower access.

Determine whether a model is ready, warm, selected and actually used. These
are separate states. Use the chosen MCP project and returned deployment
identities; do not invent model IDs or infer a live alias from training success.

## Inspect serving

Read `overmind://deployments/{deployment}` and the relevant capability resource.
For metrics, the deployment resource accepts `period=1h|24h|7d|30d|all` and
`source=application|all`. State the selected filters; all-time/all-traffic is
the default. Use application traffic when determining whether the user's
application is connected.

Separate deployment readiness, current worker warmth, capability routing and
successful application traffic. Worker measurements can be unavailable: null
counts are unknown, not zero. Reading metrics does not wake a model. Historical
traffic does not prove the worker is currently warm.

Describe failures and latency percentiles with their period and traffic source.
Do not let platform evaluations or smoke calls stand in for application usage.

## Test or activate

Use `run_inference` only for a requested test on a ready deployment, with the
user's supplied messages and output budget. Preserve explicit `max_tokens`;
omission uses the production default. Report finish reason and truncation.
An oversized context request needs a deliberate input/budget change, not silent
clipping. A test may incur inference spend.

For an authorized rollout, prefer `ship-model` when capability, deployment and
fine-tuning job are known. Otherwise inspect readiness, then use
`set_active_model` for the approved capability/deployment. Poll the returned
`model_activation` job. The previous alias remains selected until verification
succeeds; a requested switch is not a completed switch.

Use `retry_deployment` only for a failed or deleted deployment. Clearing routing
is a separate requested action; omitting deployment from `set_active_model`
clears it and cancels a pending switch. Do not change the benchmark to activate
serving.

For a repository rollout, `get_model_swap_prompt` supplies a local handoff.
Apply code changes only within the requested scope and verify final capability
and deployment state. Successful application API-key calls establish connection
independently of a completed activation.

Report readiness, worker state, selected alias and application evidence
separately. Open `inference` under the project's Console base, preserving
`projectId`, when the user wants the visual serving view.
