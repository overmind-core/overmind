"""Shared, import-order-safe helpers for the Unsloth SFT engine.

Deliberately imports nothing from ``trl`` — only ``torch``/``transformers``
symbols that don't participate in Unsloth's "must import before trl" ordering
requirement (see engine_unsloth.py).
"""

from __future__ import annotations

import json
import os
import resource
import sys
import threading
import time
from pathlib import Path
from typing import Any

import torch
from tqdm.std import tqdm as _std_tqdm
from transformers import TrainerCallback, TrainerControl, TrainerState, TrainingArguments

_GB = 1024**3


def _host_rss_gb() -> float:
    """Peak host RSS. Unsloth offloads gradient-checkpoint activations to host RAM."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is KB on Linux, bytes on macOS.
    return round(rss / (_GB if sys.platform == "darwin" else 1024**2), 2)


def _vram_gb(fn) -> float:
    """``fn`` (allocated/reserved, current/peak) over the hottest GPU.

    Multi-GPU here is a device_map pipeline split, so the binding constraint is
    whichever stage peaks highest — not the sum, and not device 0.
    """
    if not torch.cuda.is_available():
        return 0.0
    return round(max(fn(i) for i in range(torch.cuda.device_count())) / _GB, 3)


MODEL_ID = os.getenv("MODEL_ID", "Qwen/Qwen3-8B")
CHECKPOINT_DIR = os.getenv("BT_CHECKPOINT_DIR", "./checkpoints")
RUN_DIR = os.getenv("BT_RUN_DIR", CHECKPOINT_DIR)
USE_LORA = os.getenv("TRAINING_TYPE", "Lora") != "Full"
MAX_LENGTH = int(os.environ["MAX_LENGTH"])
LORA_R = int(os.getenv("LORA_R", "16"))
LORA_DROPOUT = float(os.getenv("LORA_DROPOUT", "0.0"))
PER_DEVICE_BATCH = int(os.getenv("PER_DEVICE_BATCH", "1"))
GRAD_ACCUM = int(os.getenv("GRAD_ACCUM", "8"))
N_EPOCHS = int(os.getenv("N_EPOCHS", "3"))
# Debug only: caps the run at N steps regardless of dataset size or N_EPOCHS.
# Unset (0) in every real product path.
MAX_STEPS = int(os.getenv("MAX_STEPS", "0"))
LEARNING_RATE = float(os.getenv("LEARNING_RATE", "1e-4"))
WARMUP_RATIO = float(os.getenv("WARMUP_RATIO", "0.05"))
WEIGHT_DECAY = float(os.getenv("WEIGHT_DECAY", "0.01"))
SEED = int(os.getenv("SEED", "42"))

# transformers reads a float <1 as a FRACTION of total steps, so this logs every
# step up to 500 steps and thins evenly to ~500 lines beyond that.
LOGGING_EVERY = 1 / 500


def needs_trust_remote(model_id: str) -> bool:
    from catalog import _ensure_modelfam

    _ensure_modelfam()
    from modal_shared.modelfam import needs_trust_remote as _needs

    return _needs(model_id)


def is_llama31_family(model_id: str) -> bool:
    """True for Llama-3.1/3.2/3.3 — the models the hand-rolled tool renderer targets."""
    from catalog import _ensure_modelfam

    _ensure_modelfam()
    from modal_shared.modelfam import is_llama31_family as _is_llama31

    return _is_llama31(model_id)


def load_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def rewrite_adapter_base_model(checkpoint_dir: str, model_id: str) -> None:
    """Point adapter_config at the catalog/HF id used for the job.

    Unsloth QLoRA rewrites the base id to its own ``unsloth/…-bnb-4bit`` repo;
    deploy merge would then download the packed 4-bit weights and
    OOM/shape-mismatch applying LoRA deltas. Persisting MODEL_ID keeps merge on
    the bf16 base.
    """
    if not USE_LORA:
        return
    cfg_path = Path(checkpoint_dir) / "adapter_config.json"
    if not cfg_path.exists():
        return
    cfg = json.loads(cfg_path.read_text())
    prev = cfg.get("base_model_name_or_path")
    if prev == model_id:
        return
    cfg["base_model_name_or_path"] = model_id
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    print(
        f"Rewrote adapter base_model_name_or_path {prev!r} → {model_id!r}",
        flush=True,
    )


def apply_shared_patches() -> None:
    """Idempotent monkeypatches needed by the Unsloth engine.

    Every patch guards on the symbol actually being broken first, so this is a
    no-op on transformers versions that don't need it.
    """
    _patch_sliding_window_cache()
    _patch_loss_kwargs()
    _patch_granite_aux_loss()


def _patch_sliding_window_cache() -> None:
    # trust_remote_code repos (Phi-4-mini, Ministral, Gemma-4, …) import
    # SlidingWindowCache, removed from transformers' public API in 4.48. It must
    # be a distinct SUBCLASS, not an alias: aliasing makes every plain
    # DynamicCache satisfy isinstance(cache, SlidingWindowCache), which pulls
    # ordinary caches into sliding-window paths and yields shapes that differ
    # between the forward pass and its gradient-checkpoint recompute.
    import transformers.cache_utils as _cache_utils

    if hasattr(_cache_utils, "SlidingWindowCache"):
        return

    class SlidingWindowCache(_cache_utils.DynamicCache):
        pass

    _cache_utils.SlidingWindowCache = SlidingWindowCache


def _patch_loss_kwargs() -> None:
    # Phi-4-mini's trust_remote_code file imports LossKwargs, removed in
    # transformers 4.54. Restore the v4.53.3 definition rather than aliasing to
    # TransformersKwargs, whose key set is wider and not a drop-in replacement.
    import transformers.utils as _tf_utils

    if hasattr(_tf_utils, "LossKwargs"):
        return

    from typing import TypedDict

    class LossKwargs(TypedDict, total=False):
        num_items_in_batch: torch.Tensor | None

    _tf_utils.LossKwargs = LossKwargs


def _patch_granite_aux_loss() -> None:
    # GraniteMoeHybrid's `load_balancing_loss_func` returns a plain int 0 when
    # router_logits is None, but its caller does `aux_loss.to(loss.device)`.
    # A zero TENSOR carries the same "no load-balancing signal" semantics.
    try:
        import transformers.models.granitemoehybrid.modeling_granitemoehybrid as gmh
    except ImportError:
        return  # granitemoehybrid not present in this transformers version — no-op

    if getattr(gmh.load_balancing_loss_func, "_sft_tensor_safe", False):
        return
    _orig = gmh.load_balancing_loss_func

    def _safe(gate_logits, *args, **kwargs):
        result = _orig(gate_logits, *args, **kwargs)
        if isinstance(result, int):
            device = (
                gate_logits[0].device if isinstance(gate_logits, tuple) and gate_logits else "cpu"
            )
            return torch.tensor(float(result), device=device)
        return result

    _safe._sft_tensor_safe = True  # type: ignore[attr-defined]
    gmh.load_balancing_loss_func = _safe


_GB = 1024**3
# Caps BT_DOWNLOAD chatter so a 28GB pull adds ~one line/3s, not thousands.
_DOWNLOAD_EMIT_EVERY_S = 3.0


def bt(prefix: str, payload: dict) -> None:
    """Emit one flushed, discrete structured line (both platforms capture stdout)."""
    sys.stdout.write(f"{prefix} {json.dumps(payload)}\n")
    sys.stdout.flush()


def emit_stage(stage: str, **extra) -> None:
    bt("BT_STAGE", {"stage": stage, **extra})


class DownloadReporter(_std_tqdm):
    """tqdm shim turning HF Hub byte bars into throttled BT_DOWNLOAD lines.

    The platform's log stream drops ``\\r`` progress frames. HF fetches shards in
    parallel (one bar per file), so bytes aggregate across every live byte-bar
    via class-level counters under a lock.
    """

    _lock = threading.Lock()
    _downloaded = 0
    _total = 0
    _last_emit = 0.0

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._downloaded = 0
            cls._total = 0
            cls._last_emit = 0.0

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if getattr(self, "unit", "") == "B" and self.total:
            with DownloadReporter._lock:
                DownloadReporter._total += int(self.total)

    def update(self, n=1):  # noqa: D102
        ret = super().update(n)
        if getattr(self, "unit", "") == "B":
            with DownloadReporter._lock:
                DownloadReporter._downloaded += int(n or 0)
                now = time.monotonic()
                if now - DownloadReporter._last_emit >= _DOWNLOAD_EMIT_EVERY_S:
                    DownloadReporter._last_emit = now
                    DownloadReporter._emit()
        return ret

    @classmethod
    def _emit(cls) -> None:
        rec: dict = {"downloaded_gb": round(cls._downloaded / _GB, 2)}
        if cls._total > 0:
            rec["total_gb"] = round(cls._total / _GB, 2)
            rec["pct"] = min(99, int(100 * cls._downloaded / cls._total))
        bt("BT_DOWNLOAD", rec)


def prefetch_base_model(model_id: str) -> None:
    """Pre-download the base model to the HF cache with visible progress.

    Best-effort: on any failure ``from_pretrained`` still fetches normally, just
    without granular progress.
    """
    emit_stage("downloading_base_model", model=model_id)
    try:
        from huggingface_hub import snapshot_download

        DownloadReporter.reset()
        snapshot_download(
            model_id,
            tqdm_class=DownloadReporter,
            ignore_patterns=["*.pth", "*.gguf", "*.onnx", "original/*", "*.bin"],
        )
        DownloadReporter._emit()
    except Exception as exc:  # noqa: BLE001 — download progress is best-effort
        print(f"snapshot_download skipped ({exc!r}); from_pretrained will fetch directly")


class ProgressCallback(TrainerCallback):
    """Structured BT_PROGRESS / BT_EVAL / BT_CHECKPOINT lines for live monitoring.

    Passing ``run_dir`` also persists them to files, which Modal jobs need — they
    have no logs API, so the runner polls files instead of stdout.
    """

    def __init__(self, run_dir: Path | str | None = None) -> None:
        self._train_start: float | None = None
        self._total_steps: int = 0
        self._vram_static_gb: float = 0.0
        self._last_log_mono: float | None = None
        # Real tokens per micro-step (sum of input_ids lengths). Falls back to
        # PER_DEVICE_BATCH * MAX_LENGTH until set_measured_tokens_per_step runs.
        self._tokens_per_step: int = PER_DEVICE_BATCH * MAX_LENGTH
        self._tokens_per_step_assumed: bool = True
        self.run_dir = Path(run_dir) if run_dir is not None else None
        self.metrics_path = None
        self.progress_path = None
        if self.run_dir is not None:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.metrics_path = self.run_dir / "metrics.jsonl"
            self.progress_path = self.run_dir / "progress.json"

    def set_measured_tokens_per_step(self, tokens: int) -> None:
        """Record the actual token count seen in one micro-batch.

        Calibration fits C = activations / (tokens × hidden). Using the claimed
        MAX_LENGTH when rows are shorter/longer silently biases the constant.
        """
        if tokens <= 0:
            return
        self._tokens_per_step = int(tokens)
        self._tokens_per_step_assumed = False

    def _emit(self, prefix: str, record: dict[str, Any]) -> None:
        sys.stdout.write(f"{prefix} {json.dumps(record)}\n")
        sys.stdout.flush()
        if self.metrics_path is not None:
            # "event" carries the BT_* prefix so a poller reading metrics.jsonl
            # dispatches exactly as BasetenRunner.poll does off the stdout prefix.
            payload = {"event": prefix, **record}
            with self.metrics_path.open("a") as f:
                f.write(json.dumps(payload) + "\n")
            self.progress_path.write_text(json.dumps(payload, indent=2) + "\n")

    def on_train_begin(
        self,
        args: TrainingArguments,
        state: TrainerState,
        control: TrainerControl,
        **kwargs,
    ) -> None:
        self._train_start = time.monotonic()
        self._last_log_mono = self._train_start
        self._total_steps = state.max_steps

        # Static footprint (weights + adapters) before the first step. Peak −
        # static later isolates activations for ACTIVATION_BYTES calibration.
        self._vram_static_gb = _vram_gb(torch.cuda.memory_allocated)
        model = kwargs.get("model")
        cfg = getattr(model, "config", None)
        self._emit(
            "BT_MEMORY",
            {
                "phase": "train_begin",
                "vram_static_gb": self._vram_static_gb,
                "host_rss_gb": _host_rss_gb(),
                "gpu_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
                "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
                "tokens_per_step": self._tokens_per_step,
                "tokens_per_step_assumed": self._tokens_per_step_assumed,
                "per_device_batch": PER_DEVICE_BATCH,
                "grad_accum": GRAD_ACCUM,
                "max_length": MAX_LENGTH,
                "hidden_size": int(getattr(cfg, "hidden_size", 0) or 0),
                "num_layers": int(getattr(cfg, "num_hidden_layers", 0) or 0),
                "vocab_size": int(getattr(cfg, "vocab_size", 0) or 0),
                "load_in_4bit": os.getenv("LOAD_IN_4BIT", "0") in ("1", "true", "yes"),
                "training_type": "Lora" if USE_LORA else "Full",
                "engine": "unsloth",
            },
        )

    def on_log(
        self,
        args: TrainingArguments,
        state: TrainerState,
        control: TrainerControl,
        logs: dict | None = None,
        **kwargs,
    ) -> None:
        logs = logs or {}
        if "loss" not in logs:
            return

        now = time.monotonic()
        elapsed = now - (self._train_start or now)
        total = self._total_steps or 1
        done = state.global_step
        eta = (elapsed / done * (total - done)) if done > 0 else 0.0
        step_s = now - (self._last_log_mono or now)
        self._last_log_mono = now

        record: dict[str, Any] = {
            "step": done,
            "total_steps": total,
            "epoch": round(state.epoch or 0, 4),
            "epochs": args.num_train_epochs,
            "loss": float(logs.get("loss", 0)),
            "lr": float(logs.get("learning_rate", 0)),
            "elapsed_s": round(elapsed, 1),
            "eta_s": round(eta, 1),
            "step_s": round(step_s, 3),
        }
        if "grad_norm" in logs:
            record["grad_norm"] = round(float(logs["grad_norm"]), 6)
        if "mean_token_accuracy" in logs:
            record["token_accuracy"] = round(float(logs["mean_token_accuracy"]), 4)
        if "num_tokens" in logs:
            record["num_tokens"] = int(float(logs["num_tokens"]))
            if elapsed > 0:
                record["tokens_per_s"] = round(record["num_tokens"] / elapsed, 1)

        record["vram_peak_gb"] = _vram_gb(torch.cuda.max_memory_allocated)
        record["vram_reserved_gb"] = _vram_gb(torch.cuda.max_memory_reserved)
        record["host_rss_gb"] = _host_rss_gb()

        self._emit("BT_PROGRESS", record)

    def on_evaluate(
        self,
        args: TrainingArguments,
        state: TrainerState,
        control: TrainerControl,
        metrics: dict | None = None,
        **kwargs,
    ) -> None:
        metrics = metrics or {}
        if "eval_loss" not in metrics:
            return
        record: dict[str, Any] = {
            "step": state.global_step,
            "epoch": round(state.epoch or 0, 4),
            "eval_loss": float(metrics["eval_loss"]),
            "eval_runtime_s": round(float(metrics.get("eval_runtime", 0)), 2),
        }
        if "eval_mean_token_accuracy" in metrics:
            record["eval_token_accuracy"] = round(float(metrics["eval_mean_token_accuracy"]), 4)
        self._emit("BT_EVAL", record)

    def on_train_end(
        self,
        args: TrainingArguments,
        state: TrainerState,
        control: TrainerControl,
        **kwargs,
    ) -> None:
        peak = _vram_gb(torch.cuda.max_memory_allocated)
        static = self._vram_static_gb
        tokens = self._tokens_per_step
        record: dict[str, Any] = {
            "phase": "train_end",
            "vram_static_gb": static,
            "vram_peak_gb": peak,
            "vram_reserved_gb": _vram_gb(torch.cuda.max_memory_reserved),
            "vram_activation_gb": round(max(0.0, peak - static), 3),
            "host_rss_gb": _host_rss_gb(),
            "tokens_per_step": tokens,
            "tokens_per_step_assumed": self._tokens_per_step_assumed,
        }
        hidden = int(getattr(getattr(kwargs.get("model"), "config", None), "hidden_size", 0) or 0)
        if hidden > 0 and tokens > 0:
            record["measured_bytes_per_token_per_hidden"] = round(
                record["vram_activation_gb"] * _GB / (tokens * hidden), 2
            )
        self._emit("BT_MEMORY", record)

    def on_save(
        self,
        args: TrainingArguments,
        state: TrainerState,
        control: TrainerControl,
        **kwargs,
    ) -> None:
        # Only fires when save_strategy!="no"; final-only engines call
        # emit_final_checkpoint() once after save_model() instead.
        self._emit(
            "BT_CHECKPOINT",
            {
                "step": state.global_step,
                "epoch": round(state.epoch or 0, 4),
                "path": f"checkpoint-{state.global_step}",
            },
        )

    def emit_final_checkpoint(self, state: TrainerState, path: str = "checkpoint-final") -> None:
        """Caller must invoke this once after trainer.save_model(), for
        final-only (save_strategy="no") engines."""
        self._emit(
            "BT_CHECKPOINT",
            {
                "step": state.global_step,
                "epoch": round(state.epoch or 0, 4),
                "path": path,
            },
        )
