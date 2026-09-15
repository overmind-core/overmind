# Fine-tuning and serving

SFT one or more catalog base models on a chosen **train** cell that fits, keep
a held-out **eval** cell for judges, then deploy
and prove the winner beats the agent's production incumbent.

Training dataset: intent **train** (`messages` with an assistant turn on
every row — LLM-in → LLM-out, not agent-level rows). Eval dataset: intent
**eval**; land one from other traces so the two never share a `trace_id`
(the job refuses overlap). The job uses both chosen cells, which freezes them.
See
[SKILL.md](../SKILL.md#dataset-contracts--read-first-they-gate-every-workflow)
and [datasets.md](datasets.md).

Use the native `finetune-capability` prompt to prepare and start training.
Fine-tuning is a project-scoped write flow with GPU cost; ask before starting
spend.

## Prepare and start

1. Call `get_model_catalog` first to browse dataset-independent candidates and
   their backend, tier, context, training-method, tool-calling, and batch
   metadata.
1. Call `list_datasets`, inspect each candidate by UUID, and use
   `query_dataset` to verify a **train** cell and a separate **eval** cell.
1. Call `check_finetune_readiness` only after both dataset/cell pairs and the
   capability are selected. It may narrow or recommend candidates;
   use its `catalog` and `recommendations` for `base_model` values and fix
   every reported missing prerequisite.
1. Call `estimate_finetune` for each approved base model when cost or duration
   matters.
1. After human approval, call `start_finetune`. It validates the dataset,
   evaluation set, validation split, model, credits, quota, and dispatches the
   job. A multi-model sweep uses one call per base model and can share the
   returned `group_id`.

The job resource is `overmind://finetunes/{job_id}`. Poll with
`get_job(kind="finetune_job", id=...)`. Fine-tune statuses are `queued`,
`preparing`, `running`, `deploying`, `succeeded`, `failed`, and `cancelled`.

## Deploy, verify, activate

Fine-tuning queues deployment automatically after training succeeds. Follow
the linked deployment with `get_job(kind="deployment", id=...)` and read
`overmind://deployments/{deployment}` while it moves through `queued`,
`quantizing`, `deploying`, `warming`, `ready`, or `failed`. Use
`retry_deployment` only for a failed or deleted deployment with a linked
successful or deploying fine-tuning job; it accepts the deployment UUID or
serving model id and resets the deployment before reusing the locked
registration task.

Call `run_inference` only against a `ready` deployment and treat `is_cold` as
normal first-request information. Then use `set_active_model` to activate a
ready deployment or clear the active model. Verify the final state from the
deployment and capability resources.

## Download an archived checkpoint

Use the native `download-checkpoint` prompt or read
`overmind://checkpoint-download` for the local CLI boundary. First resolve and
read `overmind://deployments/{deployment}` through MCP, then run
`overmind model download-checkpoint DEPLOYMENT --json` on the coding agent's
filesystem with the deployment id supplied by MCP. The CLI authenticates with
`X-Api-Key` from `--api-key`, the saved local project credential, or
`OVERMIND_API_KEY`, and uses
the base URL from `OVERMIND_API_URL`, `--api-url`, or `overmind.toml`.

Only archived checkpoints for `baseten` and `modal` fine-tune providers are
downloadable. Deployments without a fine-tuning job, unsupported providers,
and archives that are not ready are unavailable. The presigned S3 URL stays
inside the server/CLI flow and must not enter model context. Parse the JSON
result and report its local `path` and `bytes_written`; the CLI refuses to
overwrite an existing file.

## Repository rollout

`get_model_swap_prompt` accepts a successful fine-tune and optional `pin`. It
returns a copy-paste `prompt` plus `capability_id`, `old_model`, and
`new_model`. Apply that prompt in the repository yourself. Omit `pin` to point
the code at the capability alias, so later swaps need no further code change.
MCP does not edit the repository.

There are no public cancel or undeploy tools. Do not suggest them.
