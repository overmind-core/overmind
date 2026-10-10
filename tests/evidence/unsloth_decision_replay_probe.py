import hashlib
import inspect as python_inspect
import json
import os
import sys
from pathlib import Path

from unsloth import FastDecisionModel, FastModel  # isort: skip

import torch

sys.path.insert(0, "/root/sft_assets")
run = Path("/data/runs") / os.environ.get(
    "DIAGNOSTIC_RUN", "ft-114a1e42-ce5b-4ada-be9b-a01170b4e6c1-edc2ffb3"
)
signature = json.loads((run / "decision-training.json").read_text())
os.environ.update(
    MAX_LENGTH="4096",
    MODEL_ID=signature["model_config"]["MODEL_ID"],
    PER_DEVICE_BATCH=str(signature["microbatch"]),
    PADDED_TOKEN_BUDGET=str(signature["padded_token_budget"]),
)

from decision_engine import (  # noqa: E402
    DecisionCollator,
    IndexedRows,
    SupervisedDecisionTrainer,
    evaluate,
    verify_weights,
)
from decision_readout import collate, decision_logits, loss_terms  # noqa: E402
from unsloth.models.decision import compiled_encoder  # noqa: E402


def digest_parameters(model):
    return {
        name: hashlib.sha256(
            parameter.detach().cpu().contiguous().reshape(-1).view(torch.uint8).numpy()
        ).hexdigest()
        for name, parameter in model.named_parameters()
    }


model, processor = FastDecisionModel.from_pretrained(
    str(run / "candidate"),
    max_seq_length=4096,
    random_state=0,
    load_in_4bit=False,
    local_files_only=True,
    use_gradient_checkpointing=False
    if os.environ.get("DIAGNOSTIC_CHECKPOINTING") == "false"
    else "unsloth",
    **(
        {"attn_implementation": os.environ["DIAGNOSTIC_ATTENTION"]}
        if os.environ.get("DIAGNOSTIC_ATTENTION")
        else {}
    ),
)
tokenizer = getattr(processor, "tokenizer", processor)
data = IndexedRows(run / "decision-reload-inputs.jsonl")
expected = {
    row["key"]: row
    for row in map(json.loads, (run / "decision-reload-reference.jsonl").read_text().splitlines())
}
checks = []
initial_modes = {}
initial_buffers = {}
hidden_checks = []
hidden_first = []
evaluation_index = 0
batch_index = 0


def capture_hidden(module, args):
    global batch_index
    hidden = args[0].detach().cpu()
    if evaluation_index == 0:
        hidden_first.append(hidden)
    else:
        hidden_checks.append(
            {
                "evaluation": evaluation_index,
                "batch": batch_index,
                "max_error": (hidden.float() - hidden_first[batch_index].float())
                .abs()
                .max()
                .item(),
                "dtype": str(hidden.dtype),
            }
        )
    batch_index += 1


model.head.register_forward_pre_hook(capture_hidden)
head_first = {}
head_changes = []
normalization_checks = []


def capture_head(name):
    def capture(module, args, output):
        if evaluation_index not in (0, 1) or batch_index != 1:
            return
        tensors = {
            **{
                f"input_{i}": t.detach().cpu()
                for i, t in enumerate(args)
                if isinstance(t, torch.Tensor)
            },
            **{
                f"output_{i}": t.detach().cpu()
                for i, t in enumerate(output if isinstance(output, tuple) else (output,))
                if isinstance(t, torch.Tensor)
            },
        }
        if name == "evidence_layers.0.memory_norm":
            value = args[0]
            alternatives = {}
            for dtype in (torch.float32,):
                with torch.autocast(value.device.type, enabled=False):
                    native = torch.ops.aten.native_layer_norm(
                        value.to(dtype),
                        module.normalized_shape,
                        module.weight,
                        module.bias,
                        module.eps,
                    )[0].to(output.dtype)
                alternatives[str(dtype)] = (native.float() - output.float()).abs().max().item()
            normalization_checks.append(
                {
                    "evaluation": evaluation_index,
                    "dtype": str(output.dtype),
                    "native_errors": alternatives,
                    "function": str(torch.nn.functional.layer_norm),
                    "forward": python_inspect.getsource(module.forward),
                }
            )
        if evaluation_index == 0:
            head_first[name] = tensors
        else:
            head_changes.append(
                {
                    "module": name,
                    "changes": {
                        key: {
                            "max_error": (value.float() - head_first[name][key].float())
                            .abs()
                            .max()
                            .item(),
                            "dtype": str(value.dtype),
                        }
                        for key, value in tensors.items()
                    },
                }
            )

    return capture


for name, module in model.head.named_modules():
    module.register_forward_hook(capture_head(name))


def buffer_state():
    state = dict(model.named_buffers())
    for name, module in model.named_modules():
        for key, value in vars(module).items():
            if isinstance(value, torch.Tensor):
                state[f"{name}.{key}"] = value
            elif isinstance(value, (list, tuple)):
                state.update(
                    {
                        f"{name}.{key}.{i}": item
                        for i, item in enumerate(value)
                        if isinstance(item, torch.Tensor)
                    }
                )
    return {
        name: {
            "dtype": str(value.dtype),
            "shape": list(value.shape),
            "sha256": hashlib.sha256(
                value.detach().cpu().contiguous().reshape(-1).view(torch.uint8).numpy()
            ).hexdigest(),
        }
        for name, value in state.items()
    }


buffers_before = buffer_state()


def inspect(stage):
    global evaluation_index, batch_index
    path = Path("/tmp/probe-predictions.jsonl")
    batch_index = 0
    evaluate(model, data, tokenizer, path)
    evaluation_index += 1
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    modes = {
        f"{name}.{key}": value
        for name, module in model.named_modules()
        for key, value in vars(module).items()
        if type(value) in {bool, int, float, str, type(None)}
    }
    modes.update({f"{name}.requires_grad": p.requires_grad for name, p in model.named_parameters()})
    modes.update(
        {
            f"{name}.forward": getattr(
                module.forward, "__qualname__", type(module.forward).__name__
            )
            for name, module in model.named_modules()
        }
    )
    buffers = {
        name: hashlib.sha256(
            value.detach().cpu().contiguous().reshape(-1).view(torch.uint8).numpy()
        ).hexdigest()
        for name, value in model.named_buffers()
    }
    if not initial_modes:
        initial_modes.update(modes)
        initial_buffers.update(buffers)
    checks.append(
        {
            "stage": stage,
            "max_error": max(
                abs(a - b)
                for row in rows
                for a, b in zip(
                    row["probabilities"], expected[row["key"]]["probabilities"], strict=True
                )
            ),
            "probabilities": [row["probabilities"] for row in rows],
            "matmul_precision": torch.get_float32_matmul_precision(),
            "attention": {
                name: config._attn_implementation
                for name, config in (
                    ("root", model.encoder.config),
                    ("text", model.encoder.config.get_text_config()),
                )
            },
            "changed_buffers": [key for key in buffers if initial_buffers.get(key) != buffers[key]],
            "changed_modes": {
                k: [initial_modes.get(k), v] for k, v in modes.items() if initial_modes.get(k) != v
            },
            "tensor_changes_since_load": {
                k: [buffers_before.get(k), v]
                for k, v in buffer_state().items()
                if buffers_before.get(k) != v
            },
        }
    )


verify_weights(model, run / "candidate")
before = digest_parameters(model)
inspect("loaded")
inspect("loaded_again")
inspect("loaded_third")
args = torch.load(run / "candidate/training_args.bin", weights_only=False)
args.output_dir = "/tmp/replay-probe"
args.max_steps = 1
args.learning_rate = 0.0
args.save_strategy = "no"
inspect("arguments_loaded")
trainer = SupervisedDecisionTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=IndexedRows(run / "data.jsonl"),
    data_collator=DecisionCollator(tokenizer.pad_token_id),
    head_learning_rate=0.0,
    args=args,
)
inspect("trainer_initialized")
if os.environ.get("ATTENTION_PROBE_ONLY") == "1":
    configs = [
        model.encoder.config,
        model.encoder.config.get_text_config(),
    ]
    original = [config._attn_implementation for config in configs]
    with compiled_encoder(model, forwards=0):
        pass
    inspect("after_compilation_context")
    for config, implementation in zip(configs, original, strict=True):
        config._attn_implementation = implementation
    inspect("restored_attention")
elif os.environ.get("STATE_PROBE_ONLY") == "1":
    FastModel.for_training(model.encoder)
    inspect("encoder_training_mode")
    model.eval()
    batch = collate(data.read(range(16)), tokenizer.pad_token_id, next(model.parameters()).device)
    result = decision_logits(model, batch)
    del result
    inspect("after_grad_forward")
    model.train()
    result = decision_logits(model, batch)
    numerator, denominator = loss_terms(result, batch)
    (numerator / denominator).backward()
    del result, numerator, denominator
    inspect("after_backward")
    trainer.create_optimizer()
    trainer.optimizer.step()
    inspect("after_optimizer_step")
    trainer.optimizer.zero_grad(set_to_none=True)
    inspect("after_training_forward")
    trainer.train()
    inspect("after_trainer_step")
elif os.environ.get("WRAPPER_PROBE_ONLY") == "1":
    model = trainer.accelerator.prepare_model(model, evaluation_mode=True)
    inspect("accelerator_prepared")
    model = trainer.accelerator.unwrap_model(model, keep_fp32_wrapper=False)
    inspect("wrapper_removed")
else:
    FastDecisionModel.for_training(model)
    trainer.train()
verify_weights(model, run / "candidate")
after = digest_parameters(model)
if not any(
    os.environ.get(key) == "1"
    for key in ("WRAPPER_PROBE_ONLY", "STATE_PROBE_ONLY", "ATTENTION_PROBE_ONLY")
):
    inspect("after_zero_learning_rate_step")
print(
    "REPLAY_PROBE_RESULT "
    + json.dumps(
        {
            "compile_disabled": os.environ.get("UNSLOTH_COMPILE_DISABLE"),
            "changed_parameters": [key for key in before if before[key] != after[key]],
            "checks": checks,
            "hidden_checks": hidden_checks,
            "head_changes": head_changes,
            "normalization_checks": normalization_checks,
        }
    )
)
