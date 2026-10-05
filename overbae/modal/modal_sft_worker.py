"""Modal SFT training worker for ``FINETUNING_BACKEND=modal``. ``ModalRunner`` spawns one of the
Functions generated from ``TRAIN_IMAGES`` / ``TRAIN_FUNCTION_NAMES``. GPU is allocated only for
the lifetime of that call — no keep-warm, no idle billing.

Modal cannot branch installs at job runtime, so each training Function pins its image at deploy
time. All share ``_run_training``.

Volume ``overmind-sft`` at /data holds, per run:
  /data/runs/{run_id}/data.jsonl     validated tokens materialized from the preparation artifact
  /data/runs/{run_id}/val.jsonl      optional validation rows
  /data/runs/{run_id}/progress.json  latest snapshot (ModalRunner.poll)
  /data/runs/{run_id}/metrics.jsonl  full BT_PROGRESS/BT_EVAL/BT_CHECKPOINT history
  /data/runs/{run_id}/meta.json      run status
  /data/runs/{run_id}/final/         the ONE checkpoint a run ever saves

register_model.py reads straight out of ``final/`` — no external download step. Training itself is
overbae/services/sft_assets/train.py.

Nothing on this Volume is read at serve time. ``overbae.tasks.cleanup_modal`` sweeps it daily:
dead runs go whole, and a succeeded run loses its dataset, then ``final/`` too once the deploy
has landed and the S3 archive is confirmed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

import modal

from modal_shared.compute_usage import ComputeMeter
from modal_shared.decision_artifact import read_artifact, seal_artifact
from modal_shared.decisions import DECISION_OBJECTIVES
from modal_shared.preparation import run_preparation_process
from modal_shared.serving.artifacts import atomic_json
from modal_shared.training_data import materialize_files
from modal_shared.training_release import identity
from modal_shared.training_telemetry import read_telemetry, record_heartbeat, record_stage

RELEASE = (
    identity(Path(__file__).resolve().parents[2])
    if modal.is_local()
    else json.loads(os.environ["OVERMIND_TRAINING_RELEASE"])
)
APP_NAME = RELEASE["app"]
VOLUME_NAME = "overmind-sft"
WEIGHTS_VOLUME_NAME = "overmind-weights"
DATA_MOUNT = "/data"
WEIGHTS_MOUNT = "/weights"
RUNS_DIR = f"{DATA_MOUNT}/runs"
TRAIN_TIMEOUT_S = 24 * 60 * 60  # Modal's per-attempt ceiling
_VOLUME_COMMIT_INTERVAL_S = 5.0
_ASSETS_REMOTE_DIR = "/root/sft_assets"

# modal_shared lives outside overbae on purpose: overbae/__init__.py imports
# Celery, which configures Django settings — that must never run inside a
# bare Modal container (or in CI's `modal deploy`, which has no Django setup).
from modal_shared.images.train import TRAIN_IMAGES, light_image  # noqa: E402
from modal_shared.stacks import (  # noqa: E402
    TRAIN_FUNCTION_NAMES,
    TRAIN_U2026_8_GPOS,
)

app = modal.App(
    APP_NAME,
    secrets=[modal.Secret.from_dict({"OVERMIND_TRAINING_RELEASE": json.dumps(RELEASE)})],
)


@app.function(image=light_image)
def release_identity():
    return RELEASE


sft_vol = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
weights_vol = modal.Volume.from_name(WEIGHTS_VOLUME_NAME, create_if_missing=True)
inference_secret = modal.Secret.from_name("overmind-inference")


def _gpu_string(gpu_type: str, count: int) -> str:
    return gpu_type if count <= 1 else f"{gpu_type}:{count}"


def _run_dir(run_id: str) -> Path:
    return Path(RUNS_DIR) / run_id


def _write_meta(run_dir: Path, **fields) -> None:
    meta_path = run_dir / "meta.json"
    meta: dict = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:  # noqa: BLE001
            meta = {}
    meta.update(fields)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")


def _run_training(run_id: str, env: dict[str, str], *, gpu_type="H100", gpu_count=1) -> dict:
    meter = ComputeMeter(gpu_type=gpu_type, gpu_count=gpu_count)
    record_stage(_run_dir(run_id), "loading_model")
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    final_dir = run_dir / "final"
    native = env.get("TRAINING_OBJECTIVE") in DECISION_OBJECTIVES
    candidate_dir = run_dir / "candidate" if native else final_dir
    result = {
        "run_id": run_id,
        "status": "succeeded",
        "final_dir": str(final_dir),
        "metrics_path": str(run_dir / "metrics.jsonl"),
        "progress_path": str(run_dir / "progress.json"),
    }

    sft_vol.reload()
    usage_path = run_dir / "compute-usage.json"
    previous_usage = json.loads(usage_path.read_text()) if usage_path.exists() else []
    if native and final_dir.exists():
        result["artifact_identity"] = read_artifact(final_dir)["identity"]
        _write_meta(run_dir, status="succeeded", finished_at=time.time())
        sft_vol.commit()
        return result
    weights_vol.reload()
    _write_meta(
        run_dir,
        run_id=run_id,
        status="starting",
        started_at=time.time(),
        call_id=modal.current_function_call_id(),
    )
    sft_vol.commit()

    # transformers/trl write to CWD-relative "data.jsonl"/"val.jsonl", so stage a local
    # working dir rather than train against the mounted Volume path.
    work_dir = Path(f"/tmp/train_{run_id}")
    work_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(run_dir / "data.jsonl", work_dir / "data.jsonl")
    if (run_dir / "val.jsonl").exists():
        shutil.copy2(run_dir / "val.jsonl", work_dir / "val.jsonl")
    shutil.copy2(run_dir / "preparation.json", work_dir / "preparation.json")
    shutil.copytree(run_dir / "tokenizer", work_dir / "tokenizer", dirs_exist_ok=True)

    os.chdir(work_dir)

    # Probe/job env must NOT permanently mutate the warm container: a prior
    # UNSLOTH_TILED_MLP=0 would stick and silently disable tiled MLP for the
    # next job. Build a child env instead and only set the few worker-owned keys.
    child_env = os.environ.copy()
    child_env.update(env)
    child_env["BT_CHECKPOINT_DIR"] = str(candidate_dir)
    child_env["BT_RUN_DIR"] = str(run_dir)
    if child_env.get("UNSLOTH_IMAGE") in ("gpt_oss", TRAIN_U2026_8_GPOS):
        child_env["UNSLOTH_COMPILE_DISABLE"] = "1"
    hf_token = child_env.get("HF_TOKEN") or child_env.get("HUGGING_FACE_HUB_TOKEN")
    if hf_token:
        child_env["HF_TOKEN"] = hf_token
        child_env["HUGGING_FACE_HUB_TOKEN"] = hf_token

    # ProgressCallback writes to run_dir on every logged step but has no Modal
    # awareness, so commit the Volume on a timer instead of coupling the training
    # script to Modal internals.
    record_heartbeat(run_dir, new_attempt=True)
    stop_commit = threading.Event()

    def _commit_loop() -> None:
        while not stop_commit.wait(_VOLUME_COMMIT_INTERVAL_S):
            try:
                record_heartbeat(run_dir)
                atomic_json(usage_path, [*previous_usage, meter.snapshot()])
                sft_vol.commit()
            except Exception as exc:  # noqa: BLE001 — best-effort background commit
                print(f"[modal_sft_worker] volume commit skipped: {exc!r}")

    committer = threading.Thread(target=_commit_loop, daemon=True)
    committer.start()

    _write_meta(run_dir, status="running")
    sft_vol.commit()

    try:
        # train.py runs as its own process, never `import train; train.main()`: Modal
        # reuses warm containers, and in-process import caches (train.py's env-derived
        # constants, pretok.py's sys.path/sys.modules swap, transformers'/unsloth's
        # one-time patching) do not reset between calls — a second job inherits a
        # corrupted `trl` import. One training process per job, no shared Python state.
        # stdout is tee'd to the Volume as well as the container: the live log stream
        # keeps only a short rolling window, and intermittent failures need a durable
        # per-run transcript (`modal volume get overmind-sft runs/<run_id>/train_stdout.log`).
        log_path = run_dir / "train_stdout.log"
        for verify in [False, True] if native else [False]:
            phase_env = {**child_env, "DECISION_VERIFY_CHECKPOINT": "1" if verify else "0"}
            with (
                open(log_path, "ab") as log_f,
                subprocess.Popen(  # noqa: S603
                    [sys.executable, f"{_ASSETS_REMOTE_DIR}/train.py"],
                    cwd=work_dir,
                    env=phase_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                ) as proc,
            ):
                assert proc.stdout is not None
                for line in proc.stdout:
                    sys.stdout.buffer.write(line)
                    sys.stdout.buffer.flush()
                    log_f.write(line)
                    log_f.flush()
                proc.wait()
            sft_vol.commit()
            if proc.returncode != 0:
                raise RuntimeError(f"train.py exited with code {proc.returncode}")
        if native:
            report = json.loads((run_dir / "decision-reload-verification.json").read_text())
            result["artifact_identity"] = seal_artifact(candidate_dir, report)["identity"]
            candidate_dir.rename(final_dir)
        status = "succeeded"
    except Exception as exc:
        _write_meta(run_dir, status="failed", error=f"{type(exc).__name__}: {exc}")
        sft_vol.commit()
        raise
    finally:
        stop_commit.set()
        committer.join(timeout=10)
        atomic_json(usage_path, [*previous_usage, meter.snapshot()])
        sft_vol.commit()

    _write_meta(run_dir, status=status, finished_at=time.time())
    sft_vol.commit()

    return result


_TRAIN_FN_KWARGS = {
    "gpu": "H100",
    "timeout": TRAIN_TIMEOUT_S,
    # Training reads base weights from the same .base_models/ snapshot the deploy path merges
    # against, so a base is downloaded once per account rather than once per volume.
    "volumes": {DATA_MOUNT: sft_vol, WEIGHTS_MOUNT: weights_vol},
    "secrets": [inference_secret],
    "retries": modal.Retries(max_retries=2, initial_delay=0.0),
}


def _register_train(fn_name: str, image: modal.Image) -> None:
    def _sft(
        run_id: str,
        env: dict[str, str],
        *,
        gpu_type: str = "H100",
        gpu_count: int = 1,
    ) -> dict:
        return _run_training(run_id, env, gpu_type=gpu_type, gpu_count=gpu_count)

    _sft.__name__ = fn_name
    _sft.__qualname__ = fn_name
    globals()[fn_name] = app.function(image=image, name=fn_name, **_TRAIN_FN_KWARGS)(_sft)

    def prepare(preparation_id: str, request: dict) -> dict:
        meter = ComputeMeter(cpu=8, memory_gib=8)
        sft_vol.reload()
        destination = Path(DATA_MOUNT) / "preparations" / preparation_id
        try:
            with tempfile.TemporaryDirectory(prefix="sft-prepare-") as workspace:
                request_path = Path(workspace) / "request.json"
                request_path.write_text(json.dumps(request))
                report = run_preparation_process(
                    Path(_ASSETS_REMOTE_DIR), request_path, destination, commit=sft_vol.commit
                )
                report["compute_usage"] = meter.snapshot()
                (destination / "report.json").write_text(json.dumps(report))
                return report
        finally:
            sft_vol.commit()

    name = "prepare_" + fn_name
    prepare.__name__ = name
    prepare.__qualname__ = name
    globals()[name] = app.function(
        image=image,
        name=name,
        cpu=8,
        memory=8192,
        timeout=23 * 60 * 60,
        volumes={DATA_MOUNT: sft_vol},
        secrets=[inference_secret],
    )(prepare)


for _stack, _fn_name in TRAIN_FUNCTION_NAMES.items():
    _register_train(_fn_name, TRAIN_IMAGES[_stack])


@app.function(
    image=light_image,
    timeout=60,
    volumes={DATA_MOUNT: sft_vol},
)
def get_progress(run_id: str) -> dict:
    """Reads whatever the training Function has committed to the Volume so far."""
    sft_vol.reload()
    run_dir = _run_dir(run_id)
    out: dict = {"run_id": run_id, "found": run_dir.is_dir()}
    if not out["found"]:
        return out
    for name, key in (("meta.json", "meta"), ("progress.json", "progress")):
        p = run_dir / name
        if p.exists():
            try:
                out[key] = json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                out[key] = None
    out["telemetry"] = read_telemetry(run_dir)
    usage_path = run_dir / "compute-usage.json"
    if usage_path.exists():
        out["meta"] = {
            **(out.get("meta") or {}),
            "compute_usage": json.loads(usage_path.read_text()),
        }
    metrics_path = run_dir / "metrics.jsonl"
    if metrics_path.exists():
        with metrics_path.open() as source:
            recent, evaluations = deque(maxlen=2000), []
            for line in source:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("event") in {"BT_EVAL", "BT_CHECKPOINT"}:
                    evaluations.append(event)
                else:
                    recent.append(event)
        out["metrics"] = [*evaluations, *recent]
    else:
        out["metrics"] = []
    final_dir = run_dir / "final"
    out["has_final_checkpoint"] = final_dir.is_dir() and any(final_dir.iterdir())
    if (final_dir / "artifact.json").is_file():
        artifact = read_artifact(final_dir)
        out["native_artifact"] = {
            "artifact_identity": artifact["identity"],
            "reload_verification": artifact["verification"],
            "training": artifact["training"],
        }
    retained = run_dir / "decision-checkpoints.json"
    if retained.exists():
        out["checkpoint_selection"] = json.loads(retained.read_text())
    return out


@app.function(
    image=light_image,
    timeout=60,
    volumes={DATA_MOUNT: sft_vol},
)
def mark_cancelled(run_id: str) -> dict:
    """Written alongside ``FunctionCall.cancel()`` so poll() reports "cancelled" at once instead
    of waiting for the container to stop."""
    sft_vol.reload()
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_meta(run_dir, status="cancelled", cancelled_at=time.time())
    sft_vol.commit()
    return {"run_id": run_id, "status": "cancelled"}


@app.function(
    image=light_image,
    timeout=3600,
    volumes={DATA_MOUNT: sft_vol},
)
def upload_dataset(
    run_id: str, preparation_id: str, artifact_sha256: str, selections: dict
) -> dict:
    sft_vol.reload()
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    if not preparation_id:
        raise ValueError("A validated preprocessing artifact is required.")
    preparation = Path(DATA_MOUNT) / "preparations" / preparation_id
    report = json.loads((preparation / "report.json").read_text())
    if not report.get("ready"):
        raise ValueError("This preprocessing artifact has incompatible rows.")
    if report.get("artifact_sha256") != artifact_sha256:
        raise ValueError("The preprocessing artifact differs from the approved preparation.")
    if "data" not in selections or set(selections) - {"data", "val"}:
        raise ValueError("Training selections must contain training and optional validation rows.")
    if not selections["data"].get("rows"):
        raise ValueError("The training selection is empty.")
    files = [
        (run_dir / f"selected-{name}.keys", run_dir / f"{name}.jsonl", selection)
        for name, selection in selections.items()
    ]
    materialize_files(preparation / "tokens.jsonl", artifact_sha256, files)
    shutil.copytree(preparation / "tokenizer", run_dir / "tokenizer", dirs_exist_ok=True)
    (run_dir / "preparation.json").write_text(json.dumps(report))
    sft_vol.commit()
    return {"run_id": run_id, "run_dir": str(run_dir)}


def _dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


@app.function(
    image=light_image,
    timeout=300,
    volumes={DATA_MOUNT: sft_vol},
)
def list_run_ids() -> dict[str, float]:
    """Run id → unix mtime of its most recently written file.

    The Volume decides what still costs anything. Driving the sweep off the job table alone would
    re-send every historical run forever, since a pruned run leaves no trace there — and would
    never see a run with no job row at all, which is most of what accumulates here.

    Timestamps come from the immediate children rather than the directory itself, which does not
    move when a file inside it is rewritten.
    """
    sft_vol.reload()
    runs = Path(RUNS_DIR)
    if not runs.is_dir():
        return {}

    out: dict[str, float] = {}
    for entry in runs.iterdir():
        if not entry.is_dir():
            continue
        stamps = [child.stat().st_mtime for child in entry.iterdir()]
        out[entry.name] = max(stamps) if stamps else entry.stat().st_mtime
    return out


@app.function(
    image=light_image,
    timeout=1800,
    volumes={DATA_MOUNT: sft_vol},
)
def prune_runs(*, purge: list[str], trim: list[str], drop_final: list[str] | None = None) -> dict:
    """``purge`` drops a run directory whole; ``trim`` drops the uploaded dataset; ``drop_final``
    additionally drops the trained checkpoint, leaving the run's log behind.

    ``drop_final`` is the caller's judgement, not this Function's: it has already established that
    the deploy extracted everything ``final/`` holds onto the weights Volume and that the S3
    archive covers a rebuild.
    """
    sft_vol.reload()
    freed = 0
    purged: list[str] = []
    trimmed: list[str] = []
    finals: list[str] = []

    for run_id in purge:
        run_dir = _run_dir(run_id)
        if not run_dir.is_dir():
            continue
        freed += _dir_bytes(run_dir)
        shutil.rmtree(run_dir, ignore_errors=True)
        purged.append(run_id)

    for run_id in trim:
        run_dir = _run_dir(run_id)
        if not run_dir.is_dir():
            continue
        dropped = False
        for name in ("data.jsonl", "val.jsonl", "selected-data.keys", "selected-val.keys"):
            path = run_dir / name
            if path.is_file():
                freed += path.stat().st_size
                path.unlink()
                dropped = True
        if dropped:
            trimmed.append(run_id)

    for run_id in drop_final or []:
        final_dir = _run_dir(run_id) / "final"
        if not final_dir.is_dir():
            continue
        freed += _dir_bytes(final_dir)
        shutil.rmtree(final_dir, ignore_errors=True)
        finals.append(run_id)

    if purged or trimmed or finals:
        sft_vol.commit()
    return {
        "purged": purged,
        "trimmed": trimmed,
        "finals_dropped": finals,
        "bytes_freed": freed,
    }
