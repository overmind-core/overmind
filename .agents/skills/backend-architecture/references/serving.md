# Serving and inference

## Deployment shape

- `deployment.serves_as_adapter` decides the shape. A dense or explicitly supported MoE Modal-trained LoRA is served as an adapter on a shared BF16 base, and every deployment on that base shares one container pool. Everything else (full finetunes, unsupported MoE, other providers) is merged into a private checkpoint, quantized to FP8 unless the catalog entry sets `fp8_supported: false`. Native decision deployments copy their verified artifact without a merge.
- The shared base stays BF16: an FP8 base measurably degrades adapter quality. MoE models need verified `inference.lora_supported` in `models.json`. `inference.min_vram_gb` is a measured floor; a deployment below it is raised before the next boot.
- Base weights live only in `.base_models/{org--model}` on the weights Volume. `fetch_base_model` is the only writer and a global mutex; it seals `.base-manifest.json` and validates it on reuse. Celery waits on it before spawning GPU training, so a cold base downloads on CPU. Training fails on an incomplete snapshot; there is no Hub fallback.
- Nothing on the `overmind-sft` Volume is read at serve time. `tasks/cleanup_modal.py` sweeps it daily. A Modal deploy restores from the S3 archive only when retention already pruned the checkpoint (`register_model._dispatch_download`); the first Modal deploy never round-trips through S3.
- Torch compile files stay on the container disk. A Modal volume's 500k inode cap cannot hold them.

## Dedicated deployments

A ready `DeployedModel` has at most one live `DedicatedDeployment` on the private `overmind-dedicated` app. The menu is every GPU at or above the memory floor, including H100 whenever A100-80GB fits. Serverless `select_gpu` still skips H100. Chat shapes are `lora_bf16` and `merged_fp8`. Decision models use `DecisionEndpoint` with a per-deployment `pool` and report latency only. Speed comes from `overbae/modal/serving_benchmarks.json` (one bench per base, shape, and GPU); a missing cell stays unmeasured. The hourly price is the live Modal GPU rate plus that worker's CPU and memory (100 credits = $1); a missing rate refuses start. `min_containers` is the replica floor, `scaledown_window` is the idle timeout, and scale-up is demand up to `max_containers` (at most 8). The 15-second deployment controller meters whole minutes of running replicas at the snapshotted rate and stops the deployment when billing is on and the balance cannot cover the next minute. Zero replicas cost nothing. Stopping drains the runtime with a zero replica floor while retaining the saved GPU and scaling configuration for restart; the Console joins Configure GPU and Start/Stop, and MCP uses the same start/stop services. Gateway ids `dedicated/<trained model id>` skip `inference-ft-model` charges. The capability alias stays on the shared active model. The Console lists each model's current dedicated deployment and opens it on that model's inference page. MCP tools are `list_dedicated_gpus`, `start_dedicated_deployment`, `update_dedicated_deployment`, and `stop_dedicated_deployment`; `run_inference` accepts `dedicated/<trained model id>`. `seed_demo` does not create a live dedicated row.

## Workers

- Every GPU/image pair has a `{gpu}_{image}_lora` class. It snapshots the engine after level-2 sleep, then restores an adapter-free runtime-layout artifact through pinned buffers. Artifact identity includes the base revision and serving profile, never tenant adapters; adapters attach after restore.
- Only the concrete full-checkpoint classes own the normal post-snapshot startup hook. Putting it on the shared base class makes Modal run it beside the LoRA restore hook.
- `pre_warm` verifies a deployment before READY. For LoRA, the first boot of a `_lora` class that it triggers also prepares the artifact and snapshot.
- The Modal gateway and Django SSE send idle pings immediately and every 15 s; Django non-stream completions start newline keepalives after 15 s. The edge drops connections idle for 60 s (`api/streaming.py`), so these pings are load-bearing.

## Deployment controller

`services/deployment.py` persists stage, remote call handle, generation, retry budget, deadline and evaluation waiters. One 15-second controller covers training outputs and baselines. Worker death reconnects to the saved call instead of restarting GPU work. Each deployment generation has one four-hour deadline shared by at most three confirmed-failure attempts (30/60-second backoff); an archive-stage failure moves on to `weights` without spending an attempt. A submission without a saved acknowledgement fails closed. Training stays successful when only deployment fails; the dependent evaluation fails. Successful training deploys every model automatically; `deploy_finetune` backfills historical successes without changing routing.

## Activation

`services/model_activation.py` makes a ready deployment live. REST `active_model` and MCP `set_active_model` persist a per-capability `ModelActivation`; the deployment reconciler wakes and verifies through `pre_warm`, then switches the alias atomically. The previous target serves until verification succeeds and stays saved for switch-back. Only successful application API-key calls to the alias advance the connection timestamps; probes, pinned calls and optimiser traffic never do.

## Inference requests

MCP `run_inference` and REST `POST /api/deployed-models/{id}/inference/` (chat or decisions) use `services/inference_requests.py`: save the request key, input and routing fingerprint before dispatch; the 15-second controller then submits or observes the existing Modal call. Unknown submissions are never reissued; an expired observation deadline is unresolved, not cancelled. The ordinary completion API keeps its streaming and synchronous contract.

- `services/serving_context.py` sizes serving context from the pinned evaluation inputs, references and output headroom, independently of training `context_length`. Overflow warns; it never excludes a candidate.
- `modal_shared/context_budget.py` reserves a positive output budget (default 8,192 tokens) and rejects prompt truncation. Requests never shrink silently to the remaining context; MCP returns `context_length_exceeded`.
- Token-limited generations keep their finish reason and stop before tool execution or parsing.
- `services/inference_metrics.py` and `inference_live.py` serve metrics and worker state without invoking inference. Zero measured runners means asleep; unavailable counts stay unknown.

## Native decision serving

`DeployedModel.inference_contract` separates chat from typed probability serving. Native deployments pin the serving release, recorded training GPU and context, and the verified artifact identity. READY requires a real probability prediction. There is no chat-gateway registration or implicit activation. Output keeps the original option order.

## Operational receipts

`services/operational_progress.py` stores `OperationalRun` attempts and append-only `OperationalEvent`s for preparation, training, deployment, activation and inference. Background controllers copy provider events (`provider_progress.py`); status reads never call a worker. MCP `inspect_operation` and `overmind://operations/{id}` page them. Shared-pool events never prove tenant-adapter readiness. Missing telemetry, cursor backlog and dropped publications stay explicit. New worker instrumentation needs a new pinned release and a Modal deployment before its events exist.

Dedicated chat startup telemetry is collected by `services/dedicated_progress.py` after the controller transaction. It discovers container IDs from the deployment-specific served model name, filters app logs and journal receipts to those containers, and stores bounded, allowlisted startup messages in an `OperationalRun` keyed by probe attempt. REST and MCP expose `startup`; Console fills whole blocks by observed startup milestones in the endpoint header; detailed receipts and stage-local counters remain available through REST and MCP. Reads never wake a GPU; missing telemetry remains unavailable.

Inference metrics include `dedicated_usage` for the selected period, independent of the request traffic-source filter. It sums project/model-scoped dedicated ledger entries in credits, derives billed GPU-hours only where every entry records replica-minutes, and groups spend by billing timestamp and GPU. It is not per-request estimated serving cost or a CPU/memory resource split.
