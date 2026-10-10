import hashlib
import json
import os
import random
import re
import time
from contextlib import contextmanager
from pathlib import Path

import jsonschema
import numpy as np
import torch
from peft import get_peft_model_state_dict, set_peft_model_state_dict
from peft.utils.save_and_load import load_peft_weights
from safetensors.torch import load_file, load_model
from transformers import TrainerCallback

from modal_shared.training_monitoring import (
    fingerprint,
    generation_tokens,
    resolve_policy,
    score_json_fields,
)
from modal_shared.training_monitoring_runtime import Monitor, atomic_json
from modal_shared.training_telemetry import read_telemetry, record_stage


@contextmanager
def preserve_training_state(model):
    python_state, numpy_state, cpu_state = (
        random.getstate(),
        np.random.get_state(),
        torch.get_rng_state(),
    )
    cuda_state = torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
    modes = [(module, module.training) for module in model.modules()]
    recomputation = [
        (module, module.gradient_checkpointing)
        for module, _ in modes
        if hasattr(module, "gradient_checkpointing")
    ]
    cache_settings = [
        (module.config, module.config.use_cache)
        for module, _ in modes
        if hasattr(getattr(module, "config", None), "use_cache")
    ]
    runtime_flags = {
        name: os.environ.get(name)
        for name in ("UNSLOTH_RETURN_LOGITS", "UNSLOTH_RETURN_HIDDEN_STATES")
    }
    try:
        model.eval()
        yield
    finally:
        # Unsloth generation disables checkpointing and installs inference flags beyond .training.
        if modes[0][1] and callable(getattr(model, "for_training", None)):
            model.for_training()
        for module, enabled in recomputation:
            module.gradient_checkpointing = enabled
        for config, enabled in cache_settings:
            config.use_cache = enabled
        for module, training in modes:
            module.training = training
        for name, value in runtime_flags.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        random.setstate(python_state)
        np.random.set_state(numpy_state)
        torch.set_rng_state(cpu_state)
        if cuda_state is not None:
            torch.cuda.set_rng_state_all(cuda_state)


def normalize_output(text, normalization):
    if normalization == "strip_thinking":
        text = re.sub(r"^\s*<think>.*?</think>", "", text, count=1, flags=re.DOTALL)
    return text if normalization == "none" else text.strip()


class TrainingMonitorCallback(TrainerCallback):
    def __init__(self, run_dir, train_rows, development_rows, build_dataset, tokenizer, progress):
        self.run_dir = Path(run_dir)
        self.train_rows, self.development_rows = train_rows, development_rows
        self.build_dataset, self.tokenizer, self.progress = build_dataset, tokenizer, progress
        value = json.loads(os.environ.get("TRAINING_MONITORING", '{"mode":"off"}'))
        self.policy = resolve_policy(
            value, has_development=bool(development_rows), provider="modal"
        )
        self.trainer = None
        self.monitor = None
        self.step_started = None
        self.datasets = {}
        self.eval_progress = None

    def on_train_begin(self, args, state, control, **kwargs):
        if self.policy["mode"] == "off":
            return
        self.monitor = Monitor(
            self.run_dir,
            self.policy,
            self.train_rows,
            self.development_rows,
            total_steps=state.max_steps,
            attempt=read_telemetry(self.run_dir).get("attempt", 1),
        )
        if self.policy["initial"] and state.global_step == 0:
            self.run_check(0)

    def on_step_begin(self, args, state, control, **kwargs):
        self.step_started = time.monotonic()

    def on_log(self, args, state, control, logs=None, **kwargs):
        if self.monitor and logs and "loss" in logs:
            self.monitor.observe_metrics(
                logs,
                clipping_threshold=args.max_grad_norm,
                loss_filtering=args.logging_nan_inf_filter,
            )

    def on_prediction_step(self, args, state, control, **kwargs):
        if self.eval_progress is not None:
            self.eval_progress["completed"] += 1
            record_stage(
                self.run_dir,
                "validation",
                completed=self.eval_progress["completed"],
                total=self.eval_progress["total"],
                unit="batches",
            )

    def on_step_end(self, args, state, control, **kwargs):
        if self.monitor is None:
            return control
        self.monitor.observe_step(time.monotonic() - self.step_started)
        if self.monitor.schedule.due(state.global_step):
            self.run_check(state.global_step)
        if self.monitor.data["stop_reason"]:
            control.should_training_stop = True
        return control

    def on_epoch_end(self, args, state, control, **kwargs):
        if (
            self.monitor
            and self.policy["mode"] == "epoch"
            and state.global_step < state.max_steps
            and self.monitor.schedule.checks < self.policy["max_checks"]
        ):
            self.run_check(state.global_step)
            if self.monitor.data["stop_reason"]:
                control.should_training_stop = True
        return control

    def run_check(self, step, *, final=False):
        started = time.monotonic()
        control = self.trainer.control
        control_state = dict(vars(control))
        try:
            with preserve_training_state(self.trainer.model):
                return self.monitor.check(
                    step,
                    evaluate=self.evaluate,
                    generate=self.generate,
                    save_checkpoint=self.save_checkpoint,
                    final=final,
                )
        finally:
            # Nested evaluate/log callbacks must not consume the outer optimizer's log/save flags.
            vars(control).update(control_state)
            self.trainer.control = control
            self.progress.exclude_monitoring_time(time.monotonic() - started)

    def evaluate(self, indices, split):
        cache_key = (split, fingerprint(indices))
        if cache_key not in self.datasets:
            rows = self.development_rows if split == "development" else self.train_rows
            self.datasets[cache_key] = self.build_dataset(
                self.tokenizer,
                [rows[index] for index in indices],
                stage="building_monitoring_sample",
            )
        self.eval_progress = {
            "completed": 0,
            "total": len(self.trainer.get_eval_dataloader(self.datasets[cache_key])),
        }
        record_stage(
            self.run_dir,
            "validation",
            completed=0,
            total=self.eval_progress["total"],
            unit="batches",
        )
        previous_split = self.progress.evaluation_split
        self.progress.evaluation_split = split
        try:
            metrics = self.trainer.evaluate(self.datasets[cache_key], metric_key_prefix="eval")
            metrics["_evaluation_batches"] = self.eval_progress["completed"]
        finally:
            self.eval_progress = None
            self.progress.evaluation_split = previous_split
        return metrics

    def generate(self, indices):
        policy = self.policy["generation"]
        model = self.trainer.model
        device = model.get_input_embeddings().weight.device
        examples = []
        eos_ids = model.generation_config.eos_token_id or self.tokenizer.eos_token_id
        eos_ids = {eos_ids} if isinstance(eos_ids, int) else set(eos_ids or [])
        for ordinal, index in enumerate(indices):
            row = self.development_rows[index]
            record = {"row": index, "key": row["key"], "status": "failed"}
            started = time.monotonic()
            try:
                prompt, answer = generation_tokens(row)
                if len(prompt) + policy["max_new_tokens"] > int(os.environ["MAX_LENGTH"]):
                    raise ValueError("Generation output reservation exceeds the pinned context")
                tensor = torch.tensor([prompt], dtype=torch.long, device=device)
                with torch.inference_mode():
                    output = model.generate(
                        input_ids=tensor,
                        attention_mask=torch.ones_like(tensor),
                        max_new_tokens=policy["max_new_tokens"],
                        do_sample=False,
                        pad_token_id=self.tokenizer.pad_token_id,
                        use_cache=True,
                    )
                tokens = output[0, len(prompt) :].tolist()
                output_text = self.tokenizer.decode(tokens, skip_special_tokens=True)
                reference = self.tokenizer.decode(answer, skip_special_tokens=True)
                prediction = normalize_output(output_text, policy["normalization"])
                reference = normalize_output(reference, policy["normalization"])
                finish_reason = "stop" if tokens and tokens[-1] in eos_ids else "length"
                record.update(
                    input=self.tokenizer.decode(prompt),
                    output=output_text,
                    reference=reference,
                    prediction=prediction,
                    input_tokens=len(prompt),
                    output_tokens=len(tokens),
                    finish_reason=finish_reason,
                    status="completed" if finish_reason == "stop" else "failed",
                )
                if finish_reason != "stop":
                    record["error"] = {
                        "code": "output_limit",
                        "message": "Generation exhausted its output reservation",
                    }
                if policy["kind"] == "exact_match":
                    record["passed"] = prediction == reference
                elif policy["kind"] == "json_schema":
                    try:
                        jsonschema.validate(json.loads(prediction), policy["schema"])
                        record["passed"] = True
                    except (json.JSONDecodeError, jsonschema.ValidationError):
                        record["passed"] = False
                elif policy["kind"] == "json_fields":
                    record.update(score_json_fields(prediction, reference, policy["fields"]))
            except Exception as exc:
                record["error"] = {"code": type(exc).__name__, "message": str(exc)}
            record["latency_seconds"] = time.monotonic() - started
            examples.append(record)
            record_stage(
                self.run_dir,
                "generation_validation",
                completed=ordinal + 1,
                total=len(indices),
                unit="examples",
            )
        return examples

    def reload_checkpoint(self, directory):
        model = self.trainer.model
        if hasattr(model, "peft_config"):
            weights = load_peft_weights(str(directory), device="cpu")
            loaded = set_peft_model_state_dict(model, weights)
            if loaded.unexpected_keys:
                raise ValueError("Checkpoint contains unexpected adapter weights")
            restored = get_peft_model_state_dict(model)
            if set(weights) != set(restored) or any(
                not torch.equal(weights[key], restored[key].detach().cpu()) for key in weights
            ):
                raise ValueError("Reloaded adapter weights differ from the saved checkpoint")
        elif (directory / "model.safetensors").exists():
            load_model(model, str(directory / "model.safetensors"), strict=True)
        else:
            index = json.loads((directory / "model.safetensors.index.json").read_text())
            restored = set()
            for filename in sorted(set(index["weight_map"].values())):
                if Path(filename).name != filename:
                    raise ValueError("Invalid checkpoint shard path")
                weights = load_file(str(directory / filename))
                result = model.load_state_dict(weights, strict=False)
                if result.unexpected_keys:
                    raise ValueError("Checkpoint contains unexpected weights")
                restored.update(weights)
            aliases = {}
            for key, value in model.state_dict().items():
                aliases.setdefault(value.data_ptr(), set()).add(key)
            if any(not names.intersection(restored) for names in aliases.values()):
                raise ValueError("Checkpoint is missing model weights")

    def save_checkpoint(self, step, metrics):
        directory = self.run_dir / f"monitoring-checkpoint-{self.monitor.attempt}-{step}"
        record_stage(self.run_dir, "checkpointing", checkpoint_step=step)
        self.trainer.save_model(str(directory))
        self.tokenizer.save_pretrained(directory)
        config_path = directory / "adapter_config.json"
        if config_path.exists():
            config = json.loads(config_path.read_text())
            config["base_model_name_or_path"] = os.environ.get(
                "ADAPTER_BASE_MODEL", os.environ["MODEL_ID"]
            )
            atomic_json(config_path, config)
        manifest = {"path": directory.name, "files": {}}
        for file in sorted(directory.rglob("*")):
            if file.is_file():
                with file.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                manifest["files"][str(file.relative_to(directory))] = {
                    "sha256": digest,
                    "bytes": file.stat().st_size,
                }
        record_stage(self.run_dir, "verifying_checkpoint", checkpoint_step=step)
        self.reload_checkpoint(directory)
        return {
            "identity": fingerprint(manifest),
            "manifest": manifest,
            "state": "available",
            "metrics": metrics,
            "verification": {
                "reload_verified": True,
                "method": "strict_saved_weight_reload",
                "resume_supported": False,
                "scope": "weights; optimizer state not retained",
            },
        }

    def finish(self, state):
        if self.monitor is None:
            return
        if state.global_step > 0:
            self.run_check(state.global_step)
        selected = next(
            (item for item in self.monitor.data["checkpoints"] if item.get("selected")), None
        )
        if self.policy["selection"] == "development_loss":
            if selected is None:
                raise ValueError("Development checkpoint selection has no verified checkpoint")
            self.reload_checkpoint(self.run_dir / selected["manifest"]["path"])
        if self.policy["final"]:
            self.run_check(selected["step"] if selected else state.global_step, final=True)
