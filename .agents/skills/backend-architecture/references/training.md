# Training

UI "Training" is backend `finetuning`. All training runs on Modal with Unsloth; onboarding a model or family is the finetuning-model-onboarding skill.

## Preparation

`services/training_preparation.py` caches exact Modal preprocessing by training and validation cell fingerprints, model, training type, context length, training stack and the SFT asset code fingerprint. REST `POST /api/training-preparations/` and MCP `prepare_training_data` share it.

- Explicit preparation atomically creates the dashboard `FinetuningJob` before queuing preprocessing. Its `preprocessing` and `awaiting_launch` states are excluded from GPU dispatch and launch admission. A matching launch promotes that same job; retrying a setup failure never launches GPU training.
- Preparation runs CPU-only `prepare_<train_function>` in the matching training image. `sft_assets/preprocess.py` uses the trainer's `pretok` path and rejects over-context rows and zero-supervision examples. It never truncates, filters or rewrites data.
- Context sizing compares the stored longest row with each model's LoRA and full limits. An unsupported explicit context is rejected, never rounded. When exact tokenization exceeds an estimated context but fits the model, preparation requests the measured bucket once and rechecks every row.
- `TrainingPreparation` keeps state, the remote call ID and a 24-hour deadline. An unacknowledged submission fails closed. Retry of a confirmed failure cancels the saved remote operation first; uncertain submissions are never retried automatically.
- A worker or code fingerprint mismatch blocks preparation until `modal deploy overbae/modal/modal_sft_worker.py` runs. Recreating Docker services does not deploy Modal code. Subprocess logs stay at `preparations/<id>/preprocess_stdout.log` on the SFT volume and never enter user-facing errors.
- Workshop findings before training are advisory warnings, not launch blockers. Training does model-specific preprocessing only.

## Launch

- `training_record` freezes dataset selections, objective and requested configuration. `request_key` is project-unique: the same key and recipe return the same job; a changed recipe conflicts. Explicit context, batch, packing, dropout and seed are honored or rejected, never clamped.
- Creation pins train, validation and eval cells atomically. Launch credit admission runs inside the project-locked creation path after request-key recovery.
- `training_release` hashes the `*.py` files in `modal_shared/` and `sft_assets/`, the worker and the data format into the Modal app name. Preparations and jobs pin it; retries and observers use it after configuration changes. Templates (`.jinja`) and `run.sh` count only in the processor and training fingerprints, so a template-only change keeps the app name and a redeploy replaces that app's code. Never deploy over an active unpinned run.
- `training_submission` journals intent and run ID before provider submission. A lost acknowledgement is `submission_unknown`; the reconciler resolves it from the saved call metadata and never starts a second trainer while ownership is unresolved.
- Launches upload ordered SHA-256 row selections instead of the rows again; the worker verifies them against the pinned token artifact and preserves order and duplicates. At most five writers (`VOLUME_WRITERS`, shared by launches and preparation uploads) write to the SFT volume at once. The training volume commits when a stage finishes, not on a timer.
- Modal training attempts keep a 24-hour function timeout. `runtime_limit_seconds` does not shorten that call and is not an invoice cap. The downloadable run record separates recorded GPU spend from unreported components.
- Chat-training cost estimates use dataset text statistics and assumed H100 throughput. Native forecasts use completed execution measurements with matching identities and hardware; an unmeasured recipe reports unknown duration and total cost beside the current GPU rate.

## Progress and stall detection

Startup records runtime imports, weight loading, adapter setup, tokenizer verification, dataset construction and trainer startup as separate stages. Console and MCP expose stage start, source observation, worker heartbeat and last forward progress as separate clocks. A committer heartbeat does not count as progress. Opaque library weight loading has no percentage. Transfer counters count acknowledged files, not live network bytes.

The no-progress observer compares native stage, completed work, attempt and checkpoint counters as well as optimizer metrics. Native timing shows elapsed time and a remaining-time range, never a single countdown or finish timestamp; it resets on a new attempt.

## Development monitoring

`hyperparameters.monitoring` is one strict policy, accepted by readiness, preparation, estimates and launch, and frozen across them. `modal_shared/training_monitoring.py` resolves it, samples whole groups and schedules checks; `services/training_monitoring.py` stores the manifest that Modal freezes before GPU dispatch.

- `TrainingValidationRun` and `TrainingCheckpoint` keep failures, sample and artifact fingerprints, reload verification and evidence from cancelled runs.
- Status observation ingests metrics without downloading examples. The per-job IO collector reads each sealed artifact once with checksum verification; reads (MCP `inspect_training_progress`, REST monitoring evidence) never invoke a worker. MCP evidence over 128 KiB fails explicitly; `field` JSON Pointers page exact values.
- Generation checks need agent-declared labels, exact matching, a JSON Schema or `json_fields` pointers. `json_fields` compares declared JSON Pointers with type- and precision-preserving equality and ordered arrays; per-field coverage and whole-example pass rates are separate. Missing references are unscorable; invalid or missing predictions fail. Generation never executes tools. Metered judges, challenge suites and declared-strata sampling are rejected as unsupported.
- Development-loss selection and early stopping are explicit opt-ins. Classification `facts.assessment` compares the same scored subset with its majority-label baseline during collection, never on reads, and triggers no training action. Compact MCP summaries keep the latest generation check even when newer checks have loss only.
- Optional PostHog events (`training_result_available`, `training_check_finished`, `training_checkpoint_available`) fire after commit and carry counts and timings, never examples, labels or paths. Analytics failure cannot fail training.
- `cancel_finetune` and REST record cancellation intent before provider requests.

## Native decision training

`DecisionJob` is a separate public ID over one `FinetuningJob`; it has no status machine of its own. REST and MCP return both IDs, and preparation returns the DecisionJob ID when present.

- A train cell may carry a `decision` object instead of messages. `modal_shared/decisions.py` validates state, question, kind, options, full target distributions and positive weights. Targets never enter the encoder (`modal_shared/decision_encoding.py`); oversized inputs fail without truncation.
- The separately pinned decision image supplies Unsloth `FastDecisionModel`, `DecisionTrainer` and the Clef joint head. The measured contract selects distribution cross entropy or mixed distribution and ordinal-mean supervision. It never invents a distribution from a mean or turns soft labels into argmax text.
- The catalogue lists Clef, D3 and D1. Only Clef launches; D3 and D1 stay disabled until their preparation, checkpoint and serving runtimes pass the same checks. The supported method is one-GPU Modal LoRA.
- Artifacts hold the adapter, full-precision head, upstream head and config files, tokenizer and `decision.json`. Checkpoints publish only after exact tensor reload and a nonempty probability check.
- `hyperparameters.pre_training_baseline` defaults to true; false skips only the starting-model development evaluation, and telemetry records `not_requested`.
- Decision checkpoints use typed probabilities and never enter chat evaluation or deployment.
