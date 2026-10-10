import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import modal

from modal_shared.images.train import TRAIN_IMAGES
from modal_shared.stacks import TRAIN_DECISION

app = modal.App("overmind-decision-qualification")
image = TRAIN_IMAGES[TRAIN_DECISION].add_local_file(
    Path(__file__).with_name("unsloth_decision_runtime.py"),
    "/root/unsloth_decision_runtime.py",
)


@app.function(
    image=image,
    gpu="H100",
    timeout=900,
    volumes={
        "/data": modal.Volume.from_name("overmind-sft"),
        "/weights": modal.Volume.from_name("overmind-weights"),
    },
)
def verify_retained_run(run_id):
    run = Path("/data/runs") / run_id
    checkpoint = run / "candidate" if (run / "candidate").exists() else run / "final"
    signature = json.loads((run / "decision-training.json").read_text())
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name in (
            "preparation.json",
            "decision-reload-inputs.jsonl",
            "decision-reload-reference.jsonl",
        ):
            shutil.copyfile(run / name, root / name)
        subprocess.run(
            [sys.executable, "/root/sft_assets/train.py"],
            cwd=root,
            env={
                **os.environ,
                "BT_RUN_DIR": str(root),
                "BT_CHECKPOINT_DIR": str(checkpoint),
                "DECISION_VERIFY_CHECKPOINT": "1",
                "TRAINING_OBJECTIVE": "decision_cross_entropy",
                "BASE_MODEL_PATH": signature["model_config"]["BASE_MODEL_PATH"],
                "MODEL_ID": signature["model_config"]["MODEL_ID"],
                "MAX_LENGTH": "4096",
                "PER_DEVICE_BATCH": str(signature["microbatch"]),
                "PADDED_TOKEN_BUDGET": str(signature["padded_token_budget"]),
            },
            check=True,
        )
        report = json.loads((root / "decision-reload-verification.json").read_text())
        assert report["max_absolute_error"] <= 1e-4
        assert report["weight_reload"] == "exact"
        return report


@app.function(
    image=image.add_local_file(
        Path(__file__).with_name("unsloth_decision_replay_probe.py"), "/root/replay_probe.py"
    ),
    gpu="H100",
    timeout=1200,
    volumes={
        "/data": modal.Volume.from_name("overmind-sft"),
        "/weights": modal.Volume.from_name("overmind-weights"),
    },
)
def replay_probe(
    wrapper_only=False,
    state_only=False,
    attention_only=False,
    diagnostic_run="",
    diagnostic_attention="",
    diagnostic_checkpointing="unsloth",
):
    results = []
    for disabled in ("0",) if wrapper_only or state_only or attention_only else ("0", "1"):
        process = subprocess.run(
            [sys.executable, "/root/replay_probe.py"],
            env={
                **os.environ,
                "UNSLOTH_COMPILE_DISABLE": disabled,
                "WRAPPER_PROBE_ONLY": "1" if wrapper_only else "0",
                "STATE_PROBE_ONLY": "1" if state_only else "0",
                "ATTENTION_PROBE_ONLY": "1" if attention_only else "0",
                **({"DIAGNOSTIC_RUN": diagnostic_run} if diagnostic_run else {}),
                "DIAGNOSTIC_ATTENTION": diagnostic_attention,
                "DIAGNOSTIC_CHECKPOINTING": diagnostic_checkpointing,
            },
            text=True,
            capture_output=True,
        )
        print(process.stdout)
        print(process.stderr[-4000:])
        process.check_returncode()
        results.append(json.loads(process.stdout.split("REPLAY_PROBE_RESULT ")[-1]))
    return results


@app.function(
    image=image,
    gpu="H100",
    timeout=1200,
    volumes={
        "/data": modal.Volume.from_name("overmind-sft"),
        "/weights": modal.Volume.from_name("overmind-weights"),
    },
)
def inspect_reload():
    script = r"""
from unsloth import FastDecisionModel
import json, os, sys, torch
from pathlib import Path
from safetensors.torch import load_file
from peft import get_peft_model_state_dict, set_peft_model_state_dict
sys.path.insert(0, "/root/sft_assets")
run = Path("/data/runs/ft-9157305b-6b8b-4778-8dbc-cf66c162a785-b130644e")
signature = json.loads((run/"decision-training.json").read_text())
os.environ.update({"MAX_LENGTH":"4096", "MODEL_ID":"unsloth/Qwen3.5-0.8B", "PER_DEVICE_BATCH":str(signature["microbatch"]), "PADDED_TOKEN_BUDGET":str(signature["padded_token_budget"])})
from decision_engine import IndexedRows, evaluate
import decision_engine
decision_engine.PER_DEVICE_BATCH = signature["microbatch"]
decision_engine.PADDED_TOKEN_BUDGET = signature["padded_token_budget"]
expected = {r["key"]: r for r in map(json.loads, (run/"decision-reload-reference.jsonl").read_text().splitlines())}
results = []
for checkpointing in ("reconstruct_base",):
    model, processor = FastDecisionModel.from_pretrained(signature["model_config"]["BASE_MODEL_PATH"], max_seq_length=4096, random_state=0, load_in_4bit=False, local_files_only=True, use_gradient_checkpointing="unsloth", head_config=signature["head_config"])
    config = json.loads((run/"candidate/adapter_config.json").read_text())
    model = FastDecisionModel.get_peft_model(model, r=config["r"], lora_alpha=config["lora_alpha"], lora_dropout=config["lora_dropout"], target_modules=config["target_modules"], random_state=0)
    set_peft_model_state_dict(model.encoder, load_file(str(run/"candidate/adapter_model.safetensors")))
    model.head.load_state_dict(load_file(str(run/"candidate/joint_head.safetensors")))
    actual = get_peft_model_state_dict(model.encoder)
    saved = load_file(str(run/"candidate/adapter_model.safetensors"))
    differences = [{"key": k, "saved_dtype": str(v.dtype), "loaded_dtype": str(actual[k].dtype), "max_error": (v.float()-actual[k].cpu().float()).abs().max().item()} for k,v in saved.items()]
    head = load_file(str(run/"candidate/joint_head.safetensors"))
    head_error = max((v-model.head.state_dict()[k].cpu()).abs().max().item() for k,v in head.items())
    destination = Path("/tmp/reload-inspection.jsonl")
    tokenizer = getattr(processor, "tokenizer", processor)
    evaluate(model, IndexedRows(run/"decision-reload-inputs.jsonl"), tokenizer, destination)
    probabilities = [json.loads(line) for line in destination.read_text().splitlines()]
    error = max(abs(a-b) for row in probabilities for a,b in zip(row["probabilities"],expected[row["key"]]["probabilities"]))
    results.append({"checkpointing":checkpointing,"adapter_differences":sorted(differences,key=lambda r:r["max_error"],reverse=True)[:12],"head_error":head_error,"probability_error":error,"forced_float32":getattr(model,"_unsloth_forced_float32",None)})
    training_args = torch.load(run/"candidate/training_args.bin",weights_only=False)
    results.append({"saved_args":{k:getattr(training_args,k,None) for k in ("tf32","torch_compile","bf16","fp16")},"initial_matmul_precision":torch.get_float32_matmul_precision(),"initial_tf32":torch.backends.cuda.matmul.allow_tf32})
    for precision in ("highest", "high"):
        torch.set_float32_matmul_precision(precision)
        evaluate(model, IndexedRows(run/"decision-reload-inputs.jsonl"), tokenizer, destination)
        probabilities = [json.loads(line) for line in destination.read_text().splitlines()]
        error = max(abs(a-b) for row in probabilities for a,b in zip(row["probabilities"],expected[row["key"]]["probabilities"]))
        results.append({"precision":precision,"probability_error":error})
    from decision_engine import SupervisedDecisionTrainer, DecisionCollator
    training_args.output_dir = "/tmp/diagnostic-trainer"
    trainer = SupervisedDecisionTrainer(model=model,tokenizer=tokenizer,train_dataset=IndexedRows(run/"data.jsonl"),data_collator=DecisionCollator(tokenizer.pad_token_id),args=training_args)
    evaluate(model, IndexedRows(run/"decision-reload-inputs.jsonl"), tokenizer, destination)
    probabilities = [json.loads(line) for line in destination.read_text().splitlines()]
    error = max(abs(a-b) for row in probabilities for a,b in zip(row["probabilities"],expected[row["key"]]["probabilities"]))
    results.append({"trainer_initialized":True,"probability_error":error,"matmul_precision":torch.get_float32_matmul_precision()})
    del model,processor
print("RELOAD_RESULT " + json.dumps(results))
"""
    process = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True)
    print(process.stdout)
    print(process.stderr[-4000:])
    process.check_returncode()
    return json.loads(process.stdout.split("RELOAD_RESULT ")[-1])


@app.function(image=image, gpu="L4", timeout=3600)
def qualify(semantics_only=False, skip_baseline=False):
    subprocess.run(
        [
            sys.executable,
            "/root/unsloth_decision_runtime.py",
            "--root",
            "/tmp/qualification",
            "--assets",
            "/root/sft_assets",
            *(["--semantics-only"] if semantics_only else []),
        ],
        env={**os.environ, "DECISION_PRE_TRAINING_BASELINE": "0" if skip_baseline else "1"},
        check=True,
    )
    return json.loads(Path("/tmp/qualification/result.json").read_text())


@app.local_entrypoint()
def main(
    cpu_only: bool = False,
    semantics_only: bool = False,
    skip_baseline: bool = False,
    reload_only: bool = False,
    replay_probe_only: bool = False,
    wrapper_probe_only: bool = False,
    state_probe_only: bool = False,
    attention_probe_only: bool = False,
    verify_retained_only: bool = False,
    diagnostic_run: str = "",
    diagnostic_attention: str = "",
    diagnostic_checkpointing: str = "unsloth",
    diagnostic_name: str = "qwen35",
):
    if verify_retained_only:
        if not diagnostic_run:
            raise ValueError("Retained verification requires --diagnostic-run")
        result = verify_retained_run.remote(diagnostic_run)
        Path(f"tests/evidence/unsloth-{diagnostic_name}-fixed-reload.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        print(json.dumps(result))
        return
    if replay_probe_only or wrapper_probe_only or state_probe_only or attention_probe_only:
        result = replay_probe.remote(
            wrapper_probe_only,
            state_probe_only,
            attention_probe_only,
            diagnostic_run,
            diagnostic_attention,
            diagnostic_checkpointing,
        )
        label = (
            "attention"
            if attention_probe_only
            else "state"
            if state_probe_only
            else "wrapper"
            if wrapper_probe_only
            else "replay"
        )
        Path(f"tests/evidence/unsloth-{diagnostic_name}-{label}-probe.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        return
    if reload_only:
        result = inspect_reload.remote()
        Path("tests/evidence/unsloth-qwen35-reload-investigation.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        return
    if cpu_only:
        print(prepare_cpu.remote())
        return
    result = qualify.remote(semantics_only, skip_baseline)
    label = "semantics" if semantics_only else "without-baseline" if skip_baseline else "runtime"
    Path(f"tests/evidence/unsloth-decision-{label}-results.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result))


@app.function(image=image, cpu=2, timeout=600)
def prepare_cpu():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, '/root/sft_assets'); from decision_tokenizer import encode_record; print(encode_record.__module__)",
        ],
        capture_output=True,
        text=True,
    )
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
