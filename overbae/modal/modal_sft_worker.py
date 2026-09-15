"""Modal SFT training worker for ``FINETUNING_BACKEND=modal``. ``ModalRunner`` spawns one of the
Functions generated from ``TRAIN_IMAGES`` / ``TRAIN_FUNCTION_NAMES``. GPU is allocated only for
the lifetime of that call — no keep-warm, no idle billing.

Modal cannot branch installs at job runtime, so each training Function pins its image at deploy
time. All share ``_run_training``.

Volume ``overmind-sft`` at /data holds, per run:
  /data/runs/{run_id}/data.jsonl     training rows (uploaded by ModalRunner.submit)
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
import threading
import time
from pathlib import Path

import modal

APP_NAME = "overmind-sft"
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

app = modal.App(APP_NAME)
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


def _run_training(run_id: str, env: dict[str, str]) -> dict:
    """Shared body of every training Function — only the container image differs between them."""
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    final_dir = run_dir / "final"

    sft_vol.reload()
    _write_meta(run_dir, run_id=run_id, status="starting", started_at=time.time())
    sft_vol.commit()

    # transformers/trl write to CWD-relative "data.jsonl"/"val.jsonl", so stage a local
    # working dir rather than train against the mounted Volume path.
    work_dir = Path(f"/tmp/train_{run_id}")
    work_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(run_dir / "data.jsonl", work_dir / "data.jsonl")
    if (run_dir / "val.jsonl").exists():
        shutil.copy2(run_dir / "val.jsonl", work_dir / "val.jsonl")

    os.chdir(work_dir)

    # Probe/job env must NOT permanently mutate the warm container: a prior
    # UNSLOTH_TILED_MLP=0 would stick and silently disable tiled MLP for the
    # next job. Build a child env instead and only set the few worker-owned keys.
    child_env = os.environ.copy()
    child_env.update(env)
    child_env["BT_CHECKPOINT_DIR"] = str(final_dir)
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
    stop_commit = threading.Event()

    def _commit_loop() -> None:
        while not stop_commit.wait(_VOLUME_COMMIT_INTERVAL_S):
            try:
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
        with (
            open(log_path, "wb") as log_f,
            subprocess.Popen(  # noqa: S603
                [sys.executable, f"{_ASSETS_REMOTE_DIR}/train.py"],
                cwd=work_dir,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            ) as proc,
        ):
            assert proc.stdout is not None
            for line in proc.stdout:
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
                log_f.write(line)
            proc.wait()
        sft_vol.commit()
        if proc.returncode != 0:
            raise RuntimeError(f"train.py exited with code {proc.returncode}")
        status = "succeeded"
    except Exception as exc:
        _write_meta(run_dir, status="failed", error=f"{type(exc).__name__}: {exc}")
        sft_vol.commit()
        raise
    finally:
        stop_commit.set()
        committer.join(timeout=10)

    _write_meta(run_dir, status=status, finished_at=time.time())
    sft_vol.commit()

    return {
        "run_id": run_id,
        "status": status,
        "final_dir": str(final_dir),
        "metrics_path": str(run_dir / "metrics.jsonl"),
        "progress_path": str(run_dir / "progress.json"),
    }


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
        return _run_training(run_id, env)

    _sft.__name__ = fn_name
    _sft.__qualname__ = fn_name
    globals()[fn_name] = app.function(image=image, name=fn_name, **_TRAIN_FN_KWARGS)(_sft)


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
    metrics_path = run_dir / "metrics.jsonl"
    if metrics_path.exists():
        lines = [line for line in metrics_path.read_text().splitlines() if line.strip()]
        out["metrics"] = [json.loads(line) for line in lines]
    else:
        out["metrics"] = []
    final_dir = run_dir / "final"
    out["has_final_checkpoint"] = final_dir.is_dir() and any(final_dir.iterdir())
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
def upload_dataset(run_id: str, data_jsonl: str, val_jsonl: str | None = None) -> dict:
    """Content arrives as a string rather than a batch_upload, so ModalRunner.submit needs no
    local Modal Volume mount access — just a normal Function call."""
    sft_vol.reload()
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "data.jsonl").write_text(data_jsonl)
    if val_jsonl:
        (run_dir / "val.jsonl").write_text(val_jsonl)
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
        for name in ("data.jsonl", "val.jsonl"):
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
