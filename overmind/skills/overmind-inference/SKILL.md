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

The deployment resource reports, for the chosen period, application request counts,
failures, end-to-end response percentiles, warm generation speed, estimated cost and
matching activity. `source=application` counts only application traffic; internal
evaluations and historical calls with unknown source appear only under `all`. Its
`worker` section reports the current `state` (`warm`, `warming`, `asleep` or
`unknown`), measurement `available`, runner/input/backlog counts and
recent-activity/warming signals, from the Console's cached worker measurements
without running inference. `status` remains deployment readiness and the
capability's `active_model` remains the routing selection. Metric filters do not
filter current worker state. Missing measurements stay null rather than zero; recent
successful traffic can still establish warmth when provider measurements are
unavailable.

## Follow a deployment

Follow the deployment linked to a training job with `get_job(kind="deployment", id=...)` and read `overmind://deployments/{deployment}` while it moves through
`queued`, `quantizing`, `deploying`, `warming`, `ready` or `failed`. Progress
includes the stage, attempt, retry time, deadline and last error. Worker restarts
reconnect to the same remote operation. Confirmed failures retry at most twice,
after 30 and 60 seconds, within a four-hour deadline. A lost submission
acknowledgement stops automatic retries until the remote operation is resolved; do
not bypass that guard. Deployment failure preserves the successful training
checkpoint and marks the dependent evaluation failed; retrying a deployment does not
retrain the model. `retry_deployment` accepts a deployment UUID or serving model id
and durably queues a new deployment generation, for a failed or deleted deployment
with a linked successful or deploying fine-tuning job.

## Dedicated inference

Dedicated setup currently supports eligible chat deployments only. Decision
models, including Clef and Clef Flash, do not support dedicated setup.

Use `inspect_dedicated_inference` to list project models or inspect one deployment.
It returns eligibility, a reason when unavailable, fitting GPU types and the latest
saved dedicated state. Page with `offset` and `limit`; reads do not wake workers.
Use `list_dedicated_gpus` for current credits per GPU-hour and measured speed;
missing prices or measurements remain unknown.

For authorized setup, call `start_dedicated_deployment` with the selected deployment,
GPU, replica bounds, idle timeout and a stable project-scoped `request_key`.
Identical retries return the original deployment even after it stops; changed
settings under that key conflict. A deliberate restart uses a new key.
Poll `get_job(kind="dedicated_deployment", id=...)` or follow the returned
`overmind://dedicated-deployments/{id}` resource until online
or failed. Stopped and failed attempts remain readable. Page retained startup logs
with `log_offset` and `log_limit` (1–100), following `next_log_offset` and
`logs_total`; preserve the `truncated` flag. Startup operation IDs
can be inspected with `inspect_operation` for retained events.

Pass the returned dedicated `id` as `dedicated_deployment` to
`update_dedicated_deployment` or `stop_dedicated_deployment`; this pins the exact
attempt, so a stale retry cannot affect a newer endpoint. Omit unchanged settings;
explicit null settings are invalid. `stop_dedicated_deployment` is repeatable and
drains replicas; charges end when running replicas reach zero. Dedicated GPU
costs accrue while replicas run, including idle replicas above the minimum.

For a requested inference test, pass the returned `dedicated/<trained model id>`
to `run_inference`, with its own request key and the deployment's input contract.
Dedicated setup does not switch capability aliases or benchmark selection.

## Test or activate

Successful training automatically starts deployment using the model's inference
contract. `deploy_finetune` backfills a completed historical job and reuses its
existing deployment; it never changes application routing. Failed or deleted
deployments still require `retry_deployment`.

Use `run_inference` only for a requested test on a ready deployment, with the
user's supplied messages and output budget, or input-only `decisions` for a
decision deployment. Decisions contain state, question, kind and ordered options;
they never contain targets or chat generation settings. Their results contain
ordered probabilities and the verified artifact identity. Native models use
`POST /api/deployed-models/{id}/inference/` and poll
`GET /api/inference-requests/{id}/`; they do not support chat completions or chat
capability activation. Preserve explicit `max_tokens` for chat;
omission uses the production default. Report finish reason and truncation.
An oversized context request needs a deliberate input/budget change, not silent
clipping. A test may incur inference spend.

Inspect `finish_reason` and `truncated` before using an answer: `truncated` means the
model stopped at its token limit, and `content_clipped` separately means the MCP
response size bound applied. Neither is a complete response. When `max_tokens` is
omitted or null, `run_inference` uses the production serving output reservation
(currently 8,192 tokens); explicit positive integer budgets have no MCP-specific
ceiling. The server validates the templated input plus reserved output against the
deployment's context and never silently reduces the budget. A
`context_length_exceeded` error is not retryable without changing the request or
serving context. Returned `content` stays bounded to 32,000 characters independently
of generation length. Call `run_inference` only against a `ready` deployment and
treat `is_cold` as normal first-request information.

Supply a stable `request_key`. Contract 4.0 returns an `inference_request` job,
not a synchronous answer. Read it with `get_job`; a disconnected client can reuse
the same key/input to recover the same receipt without resubmission. A different
input under that key conflicts. Unknown provider acknowledgement is not a reason
to create another request. Result resources support `offset`/`limit` content
paging when the compact result is clipped.

Follow linked operational receipts with `inspect_operation(operation, after, limit)`. The event timeline distinguishes source/observation time, heartbeat and
forward progress, and labels shared-pool observations separately from
request-specific adapter/generation work. Missing measurements, dropped provider
publications and uncollected event backlog remain explicit. Inspection does not
wake workers; it cannot establish application integration or semantic quality.

For an authorized rollout, prefer `ship-model` when capability, deployment and
fine-tuning job are known. Otherwise inspect readiness, then use
`set_active_model` for the approved capability/deployment. Poll the returned
`model_activation` job. The previous alias remains selected until verification
succeeds; a requested switch is not a completed switch.

Use `retry_deployment` only for a failed or deleted deployment. Clearing routing
is a separate requested action; omitting deployment from `set_active_model`
clears it and cancels a pending switch. Do not change the benchmark to activate
serving.

For a repository rollout, `get_model_swap_prompt` accepts a successful fine-tune and
an optional `pin` and returns a copy-paste `prompt` plus `capability_id`,
`old_model` and `new_model`; it is a local handoff and MCP does not edit the
repository. Omit `pin` to point the code at the capability alias, so later swaps need
no further code change. Use `pin: true` for an exact deployment. A model without a
capability is always pinned: the result returns `pin: true` and a null `capability_id`.
Apply code changes only within the requested scope and
verify final capability and deployment state. After `set_active_model`, verify the
final state from the deployment and capability resources. A failed activation
preserves the current model and can be retried with the same selection. Successful application API-key calls establish connection
independently of a completed activation.

Report readiness, worker state, selected alias and application evidence
separately. Open `inference` under the project's Console base, preserving
`projectId`, when the user wants the visual serving view.
