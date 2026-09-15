"""Provider-agnostic runner abstraction for fine-tuning jobs.

:class:`BaseFinetuningRunner` is the interface every backend satisfies; concrete
runners hold the provider-specific upload, submit and poll logic. Adding a provider
means implementing that interface and wiring it into :func:`get_runner`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from django.conf import settings

from overbae.services.finetuning_policy import (
    BasetenTrainingPlan,
    baseten_context_length,  # noqa: F401 — re-exported; tests/consumers import it from here
    derive_baseten_training_plan,
)

logger = logging.getLogger(__name__)

# Together rejects suffixes with spaces, punctuation, etc. — only URL-safe chars.
_TOGETHER_SUFFIX_MAX_LEN = 40
_UNSAFE_SUFFIX_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def together_suffix(name: str | None, *, fallback: str) -> str:
    raw = (name or "").strip() or fallback.strip()
    slug = _UNSAFE_SUFFIX_RE.sub("-", raw).strip("-").lower()
    if not slug:
        slug = fallback.strip("-").lower() or "ft"
    return slug[:_TOGETHER_SUFFIX_MAX_LEN]


def use_unsloth() -> bool:
    """Global trainer-engine toggle, independent of FINETUNING_BACKEND.

    Both BasetenRunner and ModalRunner read it to pick the sft_assets engine (stock TRL
    vs Unsloth); every backend × engine combination is valid.
    """
    return bool(getattr(settings, "USE_UNSLOTH", False))


def resolve_modal_job_state(
    meta_status: str, *, in_flight: bool, call_ok: bool, has_final: bool = False
) -> str:
    """Volume success / checkpoint wins. In-flight FunctionCall beats stale ``failed``."""
    if meta_status == "cancelled":
        return "cancelled"
    if has_final or meta_status == "succeeded":
        return "succeeded"
    if in_flight or not call_ok:
        return "running"
    if meta_status == "failed":
        return "failed"
    return "succeeded"


def clamp_gemma4_training_gpu(model_id: str, gpu_type: str, gpu_count: int) -> tuple[str, int]:
    """Gemma4 Unsloth cannot pipeline-split: device_map=balanced crashes with
    ``indices should be either on cpu or on the same device as the indexed
    tensor``. Keep a single GPU; upgrade to H200 when the table asked for >1.
    Catalog already disables Full on 26B/31B (would OOM on 1× anyway)."""
    mid = (model_id or "").lower()
    if gpu_count <= 1 or ("gemma-4" not in mid and "gemma4" not in mid):
        return gpu_type, gpu_count
    return "H200", 1


@dataclass
class SubmissionResult:
    remote_id: str
    """Provider's opaque job identifier — stored on the job row."""

    run_url: str
    """URL to the provider's job dashboard / detail page (empty if unavailable)."""

    training_file_id: str
    """Provider-side file ID for the uploaded corpus. Persisted so retries can skip
    the re-upload."""

    num_examples: int | None = None


@dataclass
class PollSnapshot:
    state: str
    """Normalised lowercase status string.

    Terminal OK: ``"completed"`` / ``"succeeded"``
    Terminal fail: ``"error"`` | ``"failed"``
    Terminal cancel: ``"cancelled"``
    In-progress: any other value (e.g. ``"running"``, ``"queued"``)
    """

    epochs_completed: int | None = None
    tokens_processed: int | None = None

    # Step-based progress (Baseten; Together leaves these unset).
    trained_steps: int | None = None

    phase: str = ""
    """Coarse human-facing phase for the UI (e.g. ``processing_dataset``, ``training``)."""

    # Latest loss values from the most recent checkpoint / metrics point.
    train_loss: float | None = None
    eval_loss: float | None = None
    step: int | None = None
    total_steps: int | None = None
    estimated_finish: int | None = None
    """Provider ETA as unix timestamp, when available."""

    # Time-series for the training monitor (only keys the provider exposes).
    # Each series is a list of {step, value} (loss also has optional train/eval).
    loss_series: list[dict[str, Any]] = field(default_factory=list)
    """``[{step, train_loss?, eval_loss?}, …]`` sorted by step."""

    learning_rate_series: list[dict[str, Any]] = field(default_factory=list)
    """``[{step, value}, …]``."""

    grad_norm_series: list[dict[str, Any]] = field(default_factory=list)
    """``[{step, value}, …]``."""

    token_accuracy_series: list[dict[str, Any]] = field(default_factory=list)
    """``[{step, train?, eval?}, …]`` — Baseten emits it from TRL's SFTTrainer;
    Together does not expose it."""

    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    """Normalised checkpoint rows for the monitor tables."""

    # Baseten extended live metrics
    learning_rate: float | None = None
    token_accuracy: float | None = None
    eval_token_accuracy: float | None = None
    current_epoch: float | None = None
    eta_s: float | None = None

    # Full step history for live charts (Baseten only)
    # Each entry: {step, epoch, train_loss, token_accuracy, lr}
    metrics_history: list[dict] = field(default_factory=list)
    # Each eval entry: {step, epoch, eval_loss, eval_token_accuracy}
    eval_history: list[dict] = field(default_factory=list)

    activity: list[dict[str, Any]] = field(default_factory=list)
    """``[{ts, message}, …]`` (ts in epoch ms) for the live-activity feed — BT_*
    telemetry and progress-bar noise filtered out, so provisioning / warm-up /
    dataset-prep stages read as real progress."""

    stage: str = ""
    """Pre-training sub-stage from ``BT_STAGE`` (``downloading_base_model`` →
    ``loading_model`` → ``model_loaded``); empty for providers that don't emit it."""

    download: dict[str, Any] | None = None
    """Latest ``BT_DOWNLOAD`` payload ``{pct, downloaded_gb, total_gb}``, ``None`` until
    a download is reported."""

    output_model_name: str = ""
    """Final model identifier once training succeeds."""

    weights_url: str = ""

    error: str = ""

    raw: dict[str, Any] = field(default_factory=dict)


# Cap persisted series so a 59k-step run doesn't write a multi-megabyte JSON blob on
# every progress save.
MAX_PERSISTED_SERIES_POINTS = 2000

# Live-activity feed: how many recent operational lines ride on job.progress.
MAX_ACTIVITY_LINES = 20


# Raw training logs carry CSI escapes, which render as garbage in the UI.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Install and deprecation chatter: real stage lines in a terminal, noise in a feed.
_NOISE_PREFIXES = (
    "Collecting ",
    "Downloading ",
    "Installing collected packages",
    "Successfully installed",
    "Requirement already satisfied",
    "WARNING:",
    "Warning:",
    "[transformers]",
)


def _activity_message(raw: str) -> str | None:
    """One raw provider log line → a display-ready feed line, or ``None`` to drop it."""
    msg = _ANSI_RE.sub("", raw)
    if "\r" in msg:
        # A \r-updated line's visible state is its last non-blank frame.
        frames = [f for f in msg.split("\r") if f.strip()]
        msg = frames[-1] if frames else ""
    if not msg.strip():
        return None

    stripped = msg.strip()

    # The charts already carry BT_PROGRESS; evals and checkpoints ARE stage events, so
    # they survive below as one-liners.
    if stripped.startswith("BT_PROGRESS "):
        return None
    # Calibration telemetry — not customer-facing.
    if stripped.startswith("BT_MEMORY "):
        return None
    # Download percentages ride on job.progress.download as one updating line, not the
    # scrolling feed.
    if stripped.startswith("BT_DOWNLOAD "):
        return None
    if stripped.startswith("BT_STAGE "):
        try:
            d = json.loads(stripped[len("BT_STAGE ") :])
        except ValueError:
            return None
        stage = str(d.get("stage") or "")
        model = d.get("model") or d.get("note") or ""
        return {
            "downloading_base_model": (f"Downloading base model{f' ({model})' if model else ''}…"),
            "loading_model": "Loading base model onto GPU…",
            "model_loaded": "Base model loaded — starting training",
        }.get(stage) or (f"Stage: {stage}" if stage else None)
    if stripped.startswith("BT_EVAL "):
        try:
            d = json.loads(stripped[len("BT_EVAL ") :])
            parts = [f"loss {float(d['eval_loss']):.4g}"]
            if d.get("eval_token_accuracy") is not None:
                parts.append(f"token acc {float(d['eval_token_accuracy']):.1%}")
            return f"Eval @ step {d.get('step', '?')} — {', '.join(parts)}"
        except (ValueError, KeyError, TypeError):
            return None
    if stripped.startswith("BT_CHECKPOINT "):
        try:
            d = json.loads(stripped[len("BT_CHECKPOINT ") :])
            return f"Checkpoint saved: {d.get('path', '?')} (step {d.get('step', '?')})"
        except ValueError:
            return None

    # HF trainer dict dumps: keep the final train summary, drop the per-step and
    # per-eval ones that duplicate BT_PROGRESS / BT_EVAL.
    if stripped.startswith("{'"):
        if "'train_runtime'" in stripped:
            loss = re.search(r"'train_loss':\s*'([^']+)'", stripped)
            runtime = re.search(r"'train_runtime':\s*'([^']+)'", stripped)
            bits = []
            if loss:
                bits.append(f"train loss {float(loss.group(1)):.4g}")
            if runtime:
                bits.append(f"{float(runtime.group(1)):.0f}s")
            return f"Training finished — {', '.join(bits)}" if bits else "Training finished"
        return None

    # Indented lines are continuations of install/download output.
    if msg != msg.lstrip():
        return None
    if stripped.startswith(_NOISE_PREFIXES):
        return None
    # Progress-bar frames that survived \r-resolution: block chars or tqdm rates.
    if (
        "\u2588" in stripped  # █
        or "\u2501" in stripped  # ━ (pip download bars)
        or "it/s]" in stripped
        or "B/s]" in stripped
        or "examples/s]" in stripped
    ):
        return None
    return stripped


def filter_activity_logs(logs: list[dict], cap: int = MAX_ACTIVITY_LINES) -> list[dict]:
    """The last ``cap`` meaningful operational lines for the live-activity feed.

    Filtering happens server-side so the frontend only receives display-ready lines.
    Timestamps normalise to epoch ms — Baseten emits nanosecond strings.
    """
    out: list[dict] = []
    for entry in logs:
        msg = _activity_message(entry.get("message") or "")
        if not msg:
            continue
        ts_raw = entry.get("timestamp")
        try:
            ts = int(ts_raw)
            if ts > 10**14:  # nanoseconds → ms
                ts //= 1_000_000
        except (TypeError, ValueError):
            ts = None
        out.append({"ts": ts, "message": msg[:300]})
    return out[-cap:]


# One ordered progression the whole pipeline reports into, derived from real state
# (job status + progress + eval rows), never guessed.
LIFECYCLE_SETUP = "setup"
LIFECYCLE_TRAINING = "training"
LIFECYCLE_DEPLOYMENT = "deployment"
LIFECYCLE_EVALUATION = "evaluation"
LIFECYCLE_COMPLETED = "completed"
LIFECYCLE_FAILED = "failed"
LIFECYCLE_CANCELLED = "cancelled"

# The happy-path order (terminal failed/cancelled are off-ramps, not steps).
_SETUP_STATUSES = {
    "",
    "queued",
    "preparing",
    "submitted",
    "validating_files",
    "processing_dataset",
    "pending",
}


def lifecycle_stage(status: str | None, progress: dict | None) -> str:
    """Map a job's real state onto the canonical lifecycle stage.

    ``status`` is authoritative for the coarse phase; ``progress.trained_steps`` > 0
    separates TRAINING from a job still downloading the base model; a running FINAL
    ``judge_evals`` row holds a deployed job in EVALUATION until it lands. An empty
    ``progress`` falls back to the status alone.
    """
    progress = progress or {}
    s = (status or "").strip().lower()
    if s == "cancelled":
        return LIFECYCLE_CANCELLED
    if s == "failed":
        return LIFECYCLE_FAILED

    evals = progress.get("judge_evals") or []
    final = next((e for e in evals if (e or {}).get("kind") == "final"), None)
    final_pending = bool(final and str(final.get("status") or "").lower() in ("running", "pending"))

    if s in ("succeeded", "completed"):
        # Model is deployed READY; a final eval may still be scoring.
        return LIFECYCLE_EVALUATION if final_pending else LIFECYCLE_COMPLETED
    if s == "deploying":
        return LIFECYCLE_DEPLOYMENT
    if s in _SETUP_STATUSES:
        return LIFECYCLE_SETUP
    if s == "running":
        trained = progress.get("trained_steps")
        try:
            trained_i = int(trained) if trained is not None else None
        except (TypeError, ValueError):
            trained_i = None
        return LIFECYCLE_TRAINING if (trained_i and trained_i > 0) else LIFECYCLE_SETUP
    return LIFECYCLE_SETUP


# Readable feed lines for structured MODAL_STAGE markers and the raw prints
# register_model.py emits, so the deployment feed reads as steps, not logs.
_MODAL_STAGE_LABELS = {
    "downloading_checkpoint": "Downloading checkpoint…",
    "merging_lora": "Merging LoRA adapter into base weights…",
    "quantizing_fp8": "Quantising to FP8…",
    "registering": "Registering model with the inference server…",
    "prewarming": "Booting the model to verify it serves…",
    "ready": "Model deployed and ready.",
}
_MODAL_PREFIX_LABELS: tuple[tuple[str, str], ...] = (
    ("Listing checkpoint files", "Listing checkpoint files…"),
    ("already staged", "Checkpoint already staged — reusing."),
    ("Promoting nested artifact", "Consolidating checkpoint artefacts…"),
    ("Downloading base model", "Downloading base model for the LoRA merge…"),
    ("Base model already cached", "Base model cached — merging LoRA…"),
    ("Base model saved", "Base model downloaded."),
    ("LoRA adapter staged", "LoRA adapter staged — merging + quantising…"),
    ("Full SFT checkpoint staged", "Checkpoint staged — quantising to FP8…"),
    ("FP8 worker", "Merging + quantising to FP8 on GPU…"),
    ("already FP8-ready", "Weights already prepared — reusing."),
    ("Download done", "Checkpoint download complete."),
)


def _clean_modal_line(raw: str) -> str | None:
    """One raw Modal log line → a clean feed message, or None to drop it."""
    msg = _ANSI_RE.sub("", raw or "")
    if "\r" in msg:  # keep only the last frame of a \r-overwritten progress bar
        frames = [f for f in msg.split("\r") if f.strip()]
        msg = frames[-1] if frames else ""
    stripped = msg.strip()
    if not stripped:
        return None
    if stripped.startswith("MODAL_STAGE "):
        try:
            rec = json.loads(stripped[len("MODAL_STAGE ") :])
        except ValueError:
            return None
        return _MODAL_STAGE_LABELS.get(str(rec.get("stage") or "")) or None
    for needle, label in _MODAL_PREFIX_LABELS:
        if needle in stripped:
            return label
    # Everything else (pip chatter, tqdm frames, framework noise) is dropped.
    return None


# Backend names must never reach the frontend — error strings and log lines
# can echo them from raw exceptions/provider APIs (e.g. "Baseten API error",
# "NEBIUS_API_KEY not set"). Boundaries are letter-only (not \b, which treats
# `_` as a word char) so "NEBIUS_API_KEY" still matches. ``modal`` alone gets
# an extra ``(?!_)`` so package names like ``modal_shared`` stay intact. Order
# matters: longer/more-specific phrases first so "Together AI" doesn't leave a
# dangling "AI".
_BACKEND_NAME_RE = re.compile(
    r"(?<![A-Za-z])(together\s*ai|baseten|nebius|modal(?!_))(?![A-Za-z])",
    re.IGNORECASE,
)
# GPU SKUs in activity copy ("Pre-warming GPU (L4)") — infra detail, not product.
_GPU_PAREN_RE = re.compile(
    r"\s*\((?:L4|L40S?|A10G?|A100(?:-\d+GB)?|H100|H200|B200|T4)[^)]*\)",
    re.IGNORECASE,
)
_GPU_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:L4|L40S?|A10G?|A100(?:-\d+GB)?|H100|H200|B200|T4)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# Exception / worker internals must never reach the console.
_INTERNAL_ERROR_RE = re.compile(
    r"(RuntimeError|Traceback|Exception\b|Error:|train\.py|exited with code|"
    r"""File\s+[\"']/|/root/|/usr/local/|CUDA|OutOfMemory|\bOOM\b)""",
    re.IGNORECASE,
)

USER_FACING_TRAIN_FAILURE = "Training failed"
USER_FACING_DEPLOY_FAILURE = "Deployment failed"


def scrub_backend_names(text: str) -> str:
    """Redact vendor names and GPU SKUs from a user-facing string.

    Applied at every write boundary that reaches the frontend (job/deploy
    ``error_message``, the deploy activity feed).
    """
    if not text:
        return text
    text = _BACKEND_NAME_RE.sub("the provider", text)
    text = _GPU_PAREN_RE.sub("", text)
    text = _GPU_TOKEN_RE.sub("GPU", text)
    return text


def sanitize_job_error(text: str) -> str:
    """Map internal train/deploy failures to a fixed console string.

    Keeps short, intentional messages (timeouts, cancel notes). Replaces
    exception text, exit codes, and stack frames with a generic failure line.
    """
    if not text:
        return text
    scrubbed = scrub_backend_names(text).strip()
    if not scrubbed:
        return USER_FACING_TRAIN_FAILURE
    if _INTERNAL_ERROR_RE.search(scrubbed):
        if re.search(r"deploy|pre-?warm|register|download|inference.?server", scrubbed, re.I):
            return USER_FACING_DEPLOY_FAILURE
        return USER_FACING_TRAIN_FAILURE
    return scrubbed[:300]


def sanitize_modal_log_lines(raw_lines: list[str], *, cap: int = MAX_ACTIVITY_LINES) -> list[str]:
    """Clean raw Modal deploy logs into feed-ready lines, collapsing consecutive
    duplicates so a chatty download becomes one line.
    """
    out: list[str] = []
    for raw in raw_lines:
        msg = _clean_modal_line(raw)
        if not msg or (out and out[-1] == msg):
            continue
        out.append(msg)
    return out[-cap:]


def parse_download_stage(logs: list[dict]) -> tuple[str, dict[str, Any] | None]:
    """``(stage, download)`` from the last ``BT_STAGE`` / ``BT_DOWNLOAD`` lines.
    Providers that emit neither yield ``("", None)``.
    """
    stage = ""
    download: dict[str, Any] | None = None
    for entry in logs:
        msg = (entry.get("message") or "").strip()
        if msg.startswith("BT_STAGE "):
            try:
                rec = json.loads(msg[len("BT_STAGE ") :])
            except ValueError:
                continue
            s = str(rec.get("stage") or "")
            if s:
                stage = s
        elif msg.startswith("BT_DOWNLOAD "):
            try:
                rec = json.loads(msg[len("BT_DOWNLOAD ") :])
            except ValueError:
                continue
            download = {
                "pct": rec.get("pct"),
                "downloaded_gb": rec.get("downloaded_gb"),
                "total_gb": rec.get("total_gb"),
            }
    return stage, download


def thin_series(points: list[dict[str, Any]], cap: int = MAX_PERSISTED_SERIES_POINTS) -> list:
    """Thin a step series to ≤``cap`` points with an even stride, always
    keeping the last point (the monitor's "latest" values read from it).
    """
    n = len(points)
    if n <= cap:
        return points
    stride = -(-n // cap)  # ceil
    thinned = points[::stride]
    if thinned[-1] is not points[-1]:
        thinned.append(points[-1])
    return thinned


def progress_from_snapshot(snap: PollSnapshot, *, started_at=None) -> dict[str, Any]:
    """The durable ``FinetuningJob.progress`` blob for one poll snapshot."""
    import time as _time
    from datetime import datetime

    trained = snap.step if snap.step is not None else snap.epochs_completed
    total = snap.total_steps
    percent: float | None = None
    if trained is not None and total and total > 0:
        percent = min(100.0, round(100.0 * float(trained) / float(total), 2))

    eta_seconds: int | None = None
    now = int(_time.time())
    if snap.estimated_finish and snap.estimated_finish > now:
        eta_seconds = int(snap.estimated_finish - now)
    elif (
        percent is not None
        and 0 < percent < 100
        and started_at is not None
        and isinstance(started_at, datetime)
    ):
        elapsed = max(1.0, (_time.time() - started_at.timestamp()))
        eta_seconds = int(elapsed * (100.0 - percent) / percent)
    elapsed_seconds: int | None = None
    if started_at is not None and isinstance(started_at, datetime):
        elapsed_seconds = max(0, int(_time.time() - started_at.timestamp()))

    return {
        "epochs_completed": snap.epochs_completed,
        "tokens_processed": snap.tokens_processed,
        "trained_steps": trained,
        "total_steps": total,
        "percent": percent,
        "estimated_finish": snap.estimated_finish,
        "eta_seconds": eta_seconds,
        "elapsed_seconds": elapsed_seconds,
        "phase": snap.phase or "",
        "stage": snap.stage or "",
        "download": snap.download,
        "provider_status": snap.state,
        "latest_train_loss": snap.train_loss,
        "latest_eval_loss": snap.eval_loss,
        # Flat live-metric keys + raw histories are the frontend's contract
        # (finetuning-progress.ts / LiveMetricsChart) — keep them verbatim.
        "train_loss": snap.train_loss,
        "eval_loss": snap.eval_loss,
        "learning_rate": snap.learning_rate,
        "token_accuracy": snap.token_accuracy,
        "eval_token_accuracy": snap.eval_token_accuracy,
        "current_epoch": snap.current_epoch,
        "eta_s": snap.eta_s,
        "metrics_history": thin_series(snap.metrics_history),
        "eval_history": thin_series(snap.eval_history),
        "activity": snap.activity,
        "metrics": {
            "loss": thin_series(snap.loss_series),
            "learning_rate": thin_series(snap.learning_rate_series),
            "grad_norm": thin_series(snap.grad_norm_series),
            "token_accuracy": thin_series(snap.token_accuracy_series),
        },
        "checkpoints": snap.checkpoints,
    }


def _obj_get(obj: Any, *keys: str, default=None):
    """Read an attribute or dict key, trying several aliases."""
    for key in keys:
        if isinstance(obj, dict):
            if key in obj and obj[key] is not None:
                return obj[key]
        else:
            val = getattr(obj, key, None)
            if val is not None:
                return val
    return default


def parse_together_metrics(raw_metrics: list[Any]) -> tuple[list[dict], list[dict], list[dict]]:
    loss_by_step: dict[int, dict[str, Any]] = {}
    lr_series: list[dict[str, Any]] = []
    grad_series: list[dict[str, Any]] = []

    for row in raw_metrics:
        data = (
            row
            if isinstance(row, dict)
            else (
                row.model_dump()
                if hasattr(row, "model_dump")
                else dict(getattr(row, "__dict__", {}) or {})
            )
        )
        # Flatten nested keys; Together returns "train/loss" style keys.
        flat: dict[str, Any] = {}
        for k, v in data.items():
            if str(k).startswith("_"):
                continue
            flat[str(k)] = v

        step_raw = flat.get("train/global_step") or flat.get("global_step") or flat.get("step")
        if step_raw is None:
            continue
        step = int(step_raw)
        point = loss_by_step.setdefault(step, {"step": step})

        train_loss = flat.get("train/loss", flat.get("train_loss"))
        eval_loss = flat.get("eval/loss", flat.get("eval_loss"))
        if train_loss is not None:
            point["train_loss"] = float(train_loss)
        if eval_loss is not None:
            point["eval_loss"] = float(eval_loss)

        lr = flat.get("train/learning_rate", flat.get("learning_rate"))
        if lr is not None:
            lr_series.append({"step": step, "value": float(lr)})

        gn = flat.get("train/grad_norm", flat.get("grad_norm"))
        if gn is not None:
            grad_series.append({"step": step, "value": float(gn)})

    loss_series = [loss_by_step[k] for k in sorted(loss_by_step) if len(loss_by_step[k]) > 1]
    lr_series.sort(key=lambda p: p["step"])
    grad_series.sort(key=lambda p: p["step"])
    return loss_series, lr_series, grad_series


def parse_together_checkpoints(checkpoints: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ckpt in checkpoints:
        step = _obj_get(ckpt, "step", "checkpoint_step", "global_step")
        if step is None:
            continue
        path = _obj_get(ckpt, "path", "filename", "name", "checkpoint_name", default="") or ""
        ckpt_type = str(
            _obj_get(ckpt, "type", "checkpoint_type", default="checkpoint") or "checkpoint"
        )
        created_at = _obj_get(ckpt, "created_at", "timestamp")
        size = _obj_get(ckpt, "size", "size_bytes")
        rows.append(
            {
                "id": str(_obj_get(ckpt, "id", default="") or path or step),
                "step": int(step),
                "type": ckpt_type,
                "path": str(path),
                "train_loss": None,
                "valid_loss": None,
                "result_files": [],
                "file_count": 0,
                "size_bytes": int(size) if size is not None else None,
                "created_at": created_at,
                "resumable": "resumable" in ckpt_type.lower() or bool(_obj_get(ckpt, "resumable")),
                "has_eval": bool(_obj_get(ckpt, "has_eval")),
                "upload_status": str(
                    _obj_get(ckpt, "upload_status", default="uploaded") or "uploaded"
                ),
            }
        )
    rows.sort(key=lambda r: r["step"])
    return rows


class BaseFinetuningRunner(ABC):
    # Subclasses override with their provider's status strings.
    _TERMINAL_OK: ClassVar[set[str]] = {"completed"}
    _TERMINAL_FAIL: ClassVar[set[str]] = {"failed", "error"}
    _TERMINAL_CANCELLED: ClassVar[set[str]] = {"cancelled", "canceled"}

    def is_terminal_ok(self, state: str) -> bool:
        return state in self._TERMINAL_OK

    def is_terminal_fail(self, state: str) -> bool:
        return state in self._TERMINAL_FAIL

    def is_terminal_cancelled(self, state: str) -> bool:
        return state in self._TERMINAL_CANCELLED

    @abstractmethod
    def submit(
        self,
        job,
        training_file_path: str,
        num_examples: int | None,
        validation_file_path: str | None = None,
    ) -> SubmissionResult:
        """Upload training data and start a fine-tuning job."""

    @abstractmethod
    def poll(self, remote_id: str) -> PollSnapshot:
        """Retrieve the current state of a previously submitted job."""

    def cancel(self, remote_id: str) -> None:
        """Cancel a remote fine-tuning job. Providers must override."""
        raise NotImplementedError(f"{type(self).__name__} does not support cancel")

    def fetch_epoch_losses(self, remote_id: str) -> list[dict]:
        """Per-epoch loss data after a job completes. Empty unless the provider exposes
        checkpoint-level metrics.
        """
        return []


class TogetherAIRunner(BaseFinetuningRunner):
    """Runner backed by the Together AI fine-tuning API.

    Requires the ``together`` SDK and ``TOGETHER_API_KEY`` in settings.
    """

    # Provider status strings, normalised to lowercase before comparison.
    _TERMINAL_OK = {"completed"}
    _TERMINAL_FAIL = {"error", "user_error", "failed"}
    _TERMINAL_CANCELLED = {"cancelled", "canceled"}

    # HP field aliases: accept human-friendly names and convert to SDK names.
    _HP_ALIASES: dict[str, str] = {
        "epochs": "n_epochs",
        "num_epochs": "n_epochs",
        "lr": "learning_rate",
        "checkpoints": "n_checkpoints",
        "evals": "n_evals",
    }

    def _client(self):
        from together import Together  # noqa: PLC0415

        return Together(api_key=settings.TOGETHER_API_KEY)

    def submit(
        self,
        job,
        training_file_path: str,
        num_examples: int | None,
        validation_file_path: str | None = None,
        existing_file_id: str | None = None,
    ) -> SubmissionResult:
        """Upload training data and create a fine-tuning job via the Together SDK.

        The SDK takes flat keyword arguments — no nested ``training_type`` dict; LoRA is
        ``lora=True`` plus the ``lora_*`` keys. ``existing_file_id`` reuses a previous
        upload and skips that step.
        """
        client = self._client()

        if existing_file_id:
            training_file_id = existing_file_id
            uploaded_examples = num_examples
        else:
            if num_examples == 0:
                raise RuntimeError("Dataset produced 0 training examples (all rows lack output).")
            uploaded = client.files.upload(file=training_file_path, purpose="fine-tune")
            training_file_id = getattr(uploaded, "id", None) or uploaded["id"]
            uploaded_examples = num_examples

        hp = dict(job.hyperparameters or {})

        # Our DB nests training_type; the SDK wants it flattened into top-level kwargs.
        training_type: dict[str, Any] = hp.pop("training_type", None) or {}
        use_lora = training_type.get("type", "Lora") == "Lora"

        create_kwargs: dict[str, Any] = {
            "training_file": training_file_id,
            "model": job.base_model,
            "suffix": together_suffix(job.name, fallback=f"ft-{str(job.id)[:8]}"),
            "lora": use_lora,
            # Only train on assistant turns — never on user inputs or system prompts.
            "train_on_inputs": False,
        }
        if use_lora:
            create_kwargs["lora_r"] = training_type.get("lora_r", 8)
            create_kwargs["lora_alpha"] = training_type.get("lora_alpha", 16)
            create_kwargs["lora_dropout"] = training_type.get("lora_dropout", 0.0)
            create_kwargs["lora_trainable_modules"] = training_type.get(
                "lora_trainable_modules", "all-linear"
            )

        _skip = {"training_file", "model", "suffix", "lora"}
        for key, value in hp.items():
            normalised = self._HP_ALIASES.get(key, key)
            if normalised in _skip:
                continue
            # Together documents a per-model minimum batch size; coerce to "max"
            # rather than failing the job.
            if normalised == "batch_size" and isinstance(value, int):
                from overbae.services.recommendation import MODEL_MIN_BATCH

                min_bs = MODEL_MIN_BATCH.get(job.base_model, 8)
                if value < min_bs:
                    logger.warning(
                        "batch_size=%d is below the minimum %d for %s; using 'max'",
                        value,
                        min_bs,
                        job.base_model,
                    )
                    value = "max"
            create_kwargs[normalised] = value

        response = client.fine_tuning.create(**create_kwargs)
        remote_id = getattr(response, "id", None) or response["id"]
        return SubmissionResult(
            remote_id=remote_id,
            run_url=f"https://api.together.ai/playground/finetune/{remote_id}",
            training_file_id=training_file_id,
            num_examples=uploaded_examples,
        )

    def poll(self, remote_id: str) -> PollSnapshot:
        """Normalised snapshot of a Together job.

        ``FinetuneResponse`` maps as ``token_count`` → tokens_processed,
        ``x_model_output_name`` → output_model_name, ``x_model_output_path`` →
        weights_url. Series come from ``list_metrics`` / ``list_checkpoints``.
        """
        client = self._client()
        ft = client.fine_tuning.retrieve(remote_id)

        state = str(getattr(ft, "status", "") or "").lower()

        # Output model fields use Pydantic aliases; access via the Python attr names.
        output_model_name = (
            getattr(ft, "x_model_output_name", None) or getattr(ft, "model_output_name", None) or ""
        )
        weights_url = (
            getattr(ft, "x_model_output_path", None) or getattr(ft, "model_output_path", None) or ""
        )

        # Fallback: parse loss/step from checkpoint_save events on the retrieve payload.
        error_msg = ""
        train_loss: float | None = None
        eval_loss: float | None = None
        latest_step: int | None = None
        events = getattr(ft, "events", None) or []
        for evt in events:
            evt_type = (getattr(evt, "type", "") or "").lower()
            if evt_type == "job_error":
                error_msg = getattr(evt, "message", "") or ""
            if evt_type == "checkpoint_save":
                msg = getattr(evt, "message", "") or ""
                step_val = getattr(evt, "step", None)
                if step_val is not None:
                    latest_step = int(step_val)
                m_train = re.search(r"train_loss=([0-9.]+)", msg)
                m_eval = re.search(r"eval_loss=([0-9.]+)", msg)
                if m_train:
                    train_loss = float(m_train.group(1))
                if m_eval:
                    eval_loss = float(m_eval.group(1))

        loss_series: list[dict[str, Any]] = []
        lr_series: list[dict[str, Any]] = []
        grad_series: list[dict[str, Any]] = []
        try:
            metrics_resp = client.fine_tuning.list_metrics(remote_id)
            raw_rows = (
                getattr(metrics_resp, "metrics", None)
                or getattr(metrics_resp, "data", None)
                or (metrics_resp if isinstance(metrics_resp, list) else [])
            )
            loss_series, lr_series, grad_series = parse_together_metrics(list(raw_rows or []))
            if loss_series:
                last = loss_series[-1]
                latest_step = last.get("step", latest_step)
                train_loss = last.get("train_loss", train_loss)
                eval_loss = last.get("eval_loss", eval_loss)
        except Exception as exc:  # noqa: BLE001 — metrics are best-effort during poll
            logger.warning("Together list_metrics failed for %s: %s", remote_id, exc)

        checkpoints: list[dict[str, Any]] = []
        try:
            ckpt_resp = client.fine_tuning.list_checkpoints(remote_id)
            raw_ckpts = (
                getattr(ckpt_resp, "data", None)
                or getattr(ckpt_resp, "checkpoints", None)
                or (ckpt_resp if isinstance(ckpt_resp, list) else [])
            )
            checkpoints = parse_together_checkpoints(list(raw_ckpts or []))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Together list_checkpoints failed for %s: %s", remote_id, exc)

        total_steps = getattr(ft, "total_steps", None) or getattr(ft, "n_evals", None)
        estimated_finish = None
        seconds_remaining = getattr(ft, "seconds_remaining", None)
        if seconds_remaining is not None:
            estimated_finish = int(time.time()) + int(seconds_remaining)

        return PollSnapshot(
            state=state,
            epochs_completed=getattr(ft, "epochs_completed", None),
            tokens_processed=getattr(ft, "token_count", None),
            phase=state,
            train_loss=train_loss,
            eval_loss=eval_loss,
            step=latest_step,
            total_steps=int(total_steps) if total_steps is not None else None,
            estimated_finish=estimated_finish,
            loss_series=loss_series,
            learning_rate_series=lr_series,
            grad_norm_series=grad_series,
            checkpoints=checkpoints,
            output_model_name=output_model_name or "",
            weights_url=weights_url or "",
            error=error_msg,
        )

    def cancel(self, remote_id: str) -> None:
        client = self._client()
        client.fine_tuning.cancel(id=remote_id)


class BasetenRunner(BaseFinetuningRunner):
    """Runner backed by Baseten training jobs.

    Submits via the ``truss_train`` SDK: script, progress callback and dataset are
    packaged into a temp directory and pushed, with config.py env vars carrying every
    hyperparameter. ``remote_id`` is ``"{project_id}:{job_id}"``.

    Requires ``BASETEN_API_KEY`` and ``truss_train`` on the Celery worker.
    """

    _API_BASE = "https://api.baseten.co"

    _TERMINAL_OK = {"succeeded"}
    _TERMINAL_FAIL = {"failed"}
    _TERMINAL_CANCELLED = {"cancelled"}

    _PHASE_MAP: dict[str, str] = {
        "TRAINING_JOB_QUEUED": "queued",
        "TRAINING_JOB_INITIALIZING": "queued",
        "TRAINING_JOB_RUNNING": "training",
        "TRAINING_JOB_COMPLETED": "finalizing",
        "TRAINING_JOB_FAILED": "failed",
        "TRAINING_JOB_STOPPED": "cancelled",
        "TRAINING_JOB_CANCELED": "cancelled",
    }

    # LoRA GPU tiers, ``(max_params_b, gpu, count)``. ≤72B stays on 1×H100 via QLoRA:
    # the multi-GPU device_map="balanced" pipeline split OOMed on the hottest stage even
    # in 4-bit, so _training_env forces LOAD_IN_4BIT instead.
    # Context above 32k upgrades 1×H100 → 1×H200.
    _LONG_CONTEXT_H200 = 32768
    _GPU_TABLE = [
        (7, "H100", 1),
        (14, "H100", 1),
        (32, "H100", 1),
        (72, "H100", 1),
        (float("inf"), "H100", 4),
    ]

    # Full FT holds weights + grads + 8-bit optimizer states (~6 bytes/param), so every
    # tier needs roughly 3× the LoRA VRAM.
    _GPU_TABLE_FULL = [
        (9, "H100", 1),
        (20, "H200", 1),  # Gemma4's multi-GPU device_map is broken
        (32, "H100", 4),
        (float("inf"), "H200", 4),
    ]

    # Shared with ModalRunner — sft_assets/train.py holds the (backend, USE_UNSLOTH)
    # dispatch both runners plug into.
    _ASSETS_DIR = Path(__file__).parent / "sft_assets"
    _ASSET_FILES = (
        "train.py",
        "common.py",
        "engine_stock.py",
        "engine_unsloth.py",
        "pretok.py",
        "catalog.py",
        "run.sh",
    )

    def _api_key(self) -> str:
        key = getattr(settings, "BASETEN_API_KEY", "") or ""
        if not key:
            raise RuntimeError("BASETEN_API_KEY is not configured in Django settings.")
        return key

    def _ensure_trussrc(self) -> None:
        """(Re)write the trussrc file ``truss_train.push`` authenticates from.

        Worker containers have HOME == TMPDIR, so the periodic tmp sweep can delete the
        trussrc the entrypoint wrote; rewriting before every push self-heals auth.
        """
        # Imported at call time so the path constant comes from truss itself, which is
        # installed on workers only.
        from truss.remote import remote_factory  # noqa: PLC0415

        path = remote_factory.USER_TRUSSRC_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "[baseten]\n"
            "remote_provider = baseten\n"
            "auth_type = api_key\n"
            f"api_key = {self._api_key()}\n"
            "remote_url = https://app.baseten.co\n"
        )
        path.chmod(0o600)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key()}"}

    def _get_job(self, project_id: str, job_id: str) -> dict:
        import requests  # noqa: PLC0415

        resp = requests.get(
            f"{self._API_BASE}/v1/training_projects/{project_id}/jobs/{job_id}",
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("training_job", {})

    def _fetch_logs(self, project_id: str, job_id: str, start_ms: int = 0) -> list[dict]:
        """Fetch ALL log entries from Baseten, ascending by timestamp.

        Pagination is mandatory: per-step telemetry plus startup noise overflows the
        API's 1000-line page, and a single ascending page silently drops the NEWEST
        entries. Entry timestamps are nanoseconds while the cursor is milliseconds, so
        each page re-fetches the last millisecond and duplicates are dropped by ns.
        """
        import requests  # noqa: PLC0415

        out: list[dict] = []
        cursor_ms = start_ms
        last_ns = -1
        for _ in range(30):
            resp = requests.get(
                f"{self._API_BASE}/v1/training_projects/{project_id}/jobs/{job_id}/logs",
                headers=self._headers(),
                params={
                    "limit": 1000,  # Baseten API max is 1000
                    "direction": "asc",
                    "start_epoch_millis": cursor_ms,
                },
                timeout=30,
            )
            resp.raise_for_status()
            page = resp.json().get("logs", [])
            fresh = (
                [e for e in page if int(e.get("timestamp") or 0) > last_ns]
                if last_ns >= 0
                else page
            )
            out.extend(fresh)
            if len(page) < 1000 or (last_ns >= 0 and not fresh):
                break
            last_ns = int(page[-1].get("timestamp") or 0)
            cursor_ms = last_ns // 1_000_000
        return out

    @staticmethod
    def _parse_remote_id(remote_id: str) -> tuple[str, str]:
        project_id, job_id = remote_id.split(":", 1)
        return project_id, job_id

    @staticmethod
    def _normalise_status(raw: str) -> str:
        mapping = {
            "TRAINING_JOB_COMPLETED": "succeeded",
            "TRAINING_JOB_FAILED": "failed",
            "TRAINING_JOB_STOPPED": "cancelled",
            "TRAINING_JOB_CANCELED": "cancelled",
        }
        if raw in mapping:
            return mapping[raw]
        return raw.lower().replace("training_job_", "")

    @staticmethod
    def _job_training_type(job) -> str:
        hp = dict(getattr(job, "hyperparameters", None) or {})
        return str((hp.get("training_type") or {}).get("type") or "Lora")

    def _select_training_gpu(self, job, *, context_length: int = 0) -> tuple[str, int]:
        """The smallest GPU tier that can train the model."""
        try:
            from overbae.modal.model_registry import get_model_config_any_backend  # noqa: PLC0415

            cfg = get_model_config_any_backend(job.base_model) or {}
            # Catalog entries carry total_params_b (billions); the raw num_parameters
            # count exists only on DeployedModel rows.
            params_b = (
                float(cfg.get("total_params_b") or 0) or (cfg.get("num_parameters") or 0) / 1e9
            )
        except Exception:  # noqa: BLE001
            logger.warning("BasetenRunner: could not look up model params for %s", job.base_model)
            params_b = 0

        table = self._GPU_TABLE if self._job_training_type(job) == "Lora" else self._GPU_TABLE_FULL
        for max_b, gpu, count in table:
            if params_b <= max_b:
                if context_length > self._LONG_CONTEXT_H200 and count == 1 and gpu == "H100":
                    gpu = "H200"
                return clamp_gemma4_training_gpu(job.base_model, gpu, count)
        return clamp_gemma4_training_gpu(job.base_model, "H200", 4)

    def _dataset_type(self, training_file_path: str) -> str:
        """``"tool"`` when any row carries a ``tools`` key, else ``"chat"``."""
        try:
            with open(training_file_path) as f:
                for line in f:
                    row = json.loads(line)
                    if row.get("tools"):
                        return "tool"
        except Exception:  # noqa: BLE001
            pass
        return "chat"

    @staticmethod
    def _training_env(
        job,
        dataset_type: str,
        plan: BasetenTrainingPlan,
        *,
        gpu_type: str = "H100",
        gpu_count: int = 1,
        params_b: float = 0,
    ) -> dict[str, str]:
        """Env vars for train.py — the plan serialised 1:1, nothing invented here."""
        from overbae.modal.model_registry import get_hf_base  # noqa: PLC0415

        env = {
            "MODEL_ID": get_hf_base(str(job.base_model)),
            "DATASET_TYPE": dataset_type,
            "TRAINING_TYPE": plan.training_type,
            "MAX_LENGTH": str(plan.context_length),
            "LORA_R": str(plan.lora_r),
            "LORA_ALPHA": str(plan.lora_alpha),
            "LORA_DROPOUT": str(plan.lora_dropout),
            "LORA_TARGET_MODULES": plan.lora_target_modules,
            "PER_DEVICE_BATCH": str(plan.per_device_batch),
            "GRAD_ACCUM": str(plan.grad_accum),
            "N_EPOCHS": str(plan.n_epochs),
            "LEARNING_RATE": str(plan.learning_rate),
            "WARMUP_RATIO": str(plan.warmup_ratio),
            "WEIGHT_DECAY": str(plan.weight_decay),
            "PACKING": "1" if plan.packing else "0",
            # Assistant-only loss masking needs TRL's chat templating, which only the
            # conversational path has; the tool path pre-renders text and trains the
            # full sequence.
            "ASSISTANT_ONLY_LOSS": "1" if dataset_type == "chat" else "0",
            "SEED": str(plan.seed),
        }
        unsloth = use_unsloth()
        env["USE_UNSLOTH"] = "true" if unsloth else "false"
        unsloth_image = ""
        if unsloth:
            from overbae.modal.model_registry import get_unsloth_image  # noqa: PLC0415

            unsloth_image = get_unsloth_image(str(job.base_model))
            env["UNSLOTH_IMAGE"] = unsloth_image
        # gpt-oss Unsloth LoRA must be QLoRA even when 20B bf16 "fits" H100 —
        # the BF16 load path is numerically broken (see families/gpt_oss.py).
        from modal_shared.modelfam import family_key  # noqa: PLC0415

        is_gpt_oss = family_key(str(job.base_model)) == "gpt_oss"
        if plan.training_type == "Lora" and (plan.load_in_4bit or is_gpt_oss):
            env["LOAD_IN_4BIT"] = "1"
        if is_gpt_oss:
            # Must be set before train.py imports unsloth (import-time constant).
            env["UNSLOTH_COMPILE_DISABLE"] = "1"
        try:
            from overbae.modal.model_registry import get_model_config_any_backend  # noqa: PLC0415

            hidden = int(
                (get_model_config_any_backend(str(job.base_model)) or {}).get("hidden_size") or 0
            )
            if hidden > 0:
                env["HIDDEN_SIZE"] = str(hidden)
        except Exception:  # noqa: BLE001
            pass
        _ = params_b  # GPU selection already applied; plan.load_in_4bit is authoritative
        return env

    def _build_config_source(
        self,
        job,
        gpu_type: str,
        gpu_count: int,
        dataset_type: str,
        plan: BasetenTrainingPlan,
        project_name: str = "",
    ) -> str:
        # truss 0.18.x TrainingProject is extra="forbid" with {name, job, team_name} and
        # no `id`; push upserts BY NAME, so BASETEN_PROJECT goes on TrainingProject.name
        # and the per-job label on TrainingJob.name.
        job_name = re.sub(r"[^a-zA-Z0-9_-]", "-", (job.name or "ft-job"))[:50]
        project = re.sub(r"[^a-zA-Z0-9_-]", "-", project_name or "overmind-finetuning")[:50]
        params_b = 0.0
        try:
            from overbae.modal.model_registry import get_model_config_any_backend  # noqa: PLC0415

            cfg = get_model_config_any_backend(job.base_model) or {}
            params_b = (
                float(cfg.get("total_params_b") or 0) or (cfg.get("num_parameters") or 0) / 1e9
            )
        except Exception:  # noqa: BLE001
            pass
        env = self._training_env(
            job, dataset_type, plan, gpu_type=gpu_type, gpu_count=gpu_count, params_b=params_b
        )
        # Baseten does not auto-inject workspace secrets into training jobs: without an
        # explicit SecretReference, gated HF downloads 401 even when the secret exists.
        env_items = ", ".join(f"{k!r}: {v!r}" for k, v in env.items())
        env_literal = (
            "{"
            f"{env_items}, "
            '"HF_TOKEN": SecretReference(name="HF_ACCESS_TOKEN"), '
            '"HUGGING_FACE_HUB_TOKEN": SecretReference(name="HF_ACCESS_TOKEN")'
            "}"
        )
        return (
            "from truss_train import CheckpointingConfig, CacheConfig, Compute, Image, Runtime, SecretReference, TrainingJob, TrainingProject\n"
            "from truss.base.truss_config import AcceleratorSpec\n\n"
            "training_project = TrainingProject(\n"
            f'    name="{project}",\n'
            "    job=TrainingJob(\n"
            f'        name="{job_name}",\n'
            '        image=Image(base_image="pytorch/pytorch:2.7.0-cuda12.8-cudnn9-runtime"),\n'
            f"        compute=Compute(accelerator=AcceleratorSpec(accelerator={gpu_type!r}, count={gpu_count})),\n"
            "        runtime=Runtime(\n"
            '            start_commands=["chmod +x ./run.sh && ./run.sh"],\n'
            f"            environment_variables={env_literal},\n"
            "            cache_config=CacheConfig(enabled=True),\n"
            "            checkpointing_config=CheckpointingConfig(enabled=True),\n"
            "        ),\n"
            "    ),\n"
            ")\n"
        )

    def submit(
        self,
        job,
        training_file_path: str,
        num_examples: int | None,
        validation_file_path: str | None = None,
    ) -> SubmissionResult:
        import shutil
        import tempfile

        try:
            from truss_train import push as truss_push  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "truss_train is not installed on the Celery worker. Run: pip install truss-train"
            ) from exc

        self._ensure_trussrc()

        if num_examples == 0:
            raise RuntimeError("Dataset produced 0 training examples.")

        # An impossible plan must raise here, before any provider spend.
        from overbae.modal.model_registry import get_model_config_any_backend  # noqa: PLC0415
        from overbae.modal.training_type import training_context_length  # noqa: PLC0415

        model_cfg = get_model_config_any_backend(job.base_model) or {}
        ft_cfg = model_cfg.get("finetuning") or {}
        stats = dict((job.cell.stats if job.cell_id else None) or {})
        try:
            max_row_tokens = int(stats.get("max_token_length") or 0)
        except (TypeError, ValueError):
            max_row_tokens = 0
        hp = dict(job.hyperparameters or {})
        training_kind = "full" if str(hp.get("training_type", {}).get("type")) == "Full" else "lora"
        model_max = training_context_length(model_cfg, training_kind)
        requested_ctx = int(hp.get("context_length") or 0) or None
        early_ctx = baseten_context_length(
            max_row_tokens, model_max=model_max, requested=requested_ctx
        )
        gpu_type, gpu_count = self._select_training_gpu(job, context_length=early_ctx)
        plan = derive_baseten_training_plan(
            hyperparameters=job.hyperparameters,
            num_train_examples=int(num_examples or 0),
            dataset_stats=stats,
            params_b=float(model_cfg.get("total_params_b") or 0),
            model_max_context=model_max,
            model_min_batch=int(ft_cfg.get("min_batch_size") or 1),
            model_max_batch=int(ft_cfg["max_batch_size"]) if ft_cfg.get("max_batch_size") else None,
            gpu_type=gpu_type,
            gpu_count=gpu_count,
            model_id=job.base_model,
            hidden_size=int(model_cfg.get("hidden_size") or 0),
        )
        for note in plan.notes:
            logger.info("BasetenRunner plan (job %s): %s", job.id, note)

        # Persist the resolved knobs so the monitor shows what was trained with,
        # not "auto".
        from overbae.models import FinetuningJob as _FTJob  # noqa: PLC0415

        _FTJob.objects.filter(pk=job.pk).update(
            hyperparameters={
                **(dict(job.hyperparameters) or {}),
                "context_length": plan.context_length,
                "n_epochs": plan.n_epochs,
                "batch_size": plan.batch_size,
                "learning_rate": plan.learning_rate,
                "warmup_ratio": plan.warmup_ratio,
                "packing": plan.packing,
            }
        )

        dataset_type = self._dataset_type(training_file_path)

        config_src = self._build_config_source(
            job=job,
            gpu_type=gpu_type,
            gpu_count=gpu_count,
            dataset_type=dataset_type,
            plan=plan,
            project_name=getattr(settings, "BASETEN_PROJECT", "") or "",
        )

        with tempfile.TemporaryDirectory(prefix="bt_ft_") as tmpdir:
            tmp = Path(tmpdir)

            for asset in self._ASSET_FILES:
                shutil.copy2(self._ASSETS_DIR / asset, tmp / asset)
            (tmp / "run.sh").chmod(0o755)
            # TrainerHooks + family registry (pure stdlib) for the training container.
            families_src = self._ASSETS_DIR / "families"
            if families_src.is_dir():
                shutil.copytree(families_src, tmp / "families")
            # modal_shared lives outside overbae on purpose — overbae/__init__.py
            # imports Celery, which the bare Baseten container can't configure.
            modelfam_src = Path(__file__).resolve().parents[2] / "modal_shared" / "modelfam"
            if modelfam_src.is_dir():
                pkg = tmp / "modal_shared"
                pkg.mkdir(exist_ok=True)
                (pkg / "__init__.py").write_text("")
                shutil.copytree(modelfam_src, pkg / "modelfam")
            # engine_stock.py's hand-patched {% generation %} chat templates.
            for template_dir in (
                "llama_templates",
                "antares_templates",
                "olmo_templates",
                "qwen_templates",
            ):
                src = self._ASSETS_DIR / template_dir
                if src.is_dir():
                    shutil.copytree(src, tmp / template_dir)

            shutil.copy2(training_file_path, tmp / "data.jsonl")
            if validation_file_path:
                shutil.copy2(validation_file_path, tmp / "val.jsonl")
            else:
                # Write a minimal val file to keep TRL happy
                (tmp / "val.jsonl").write_text("")

            (tmp / "config.py").write_text(config_src)

            logger.info(
                "BasetenRunner: pushing job %s — model=%s gpu=%s×%d ctx=%d "
                "batch=%d×%d epochs=%d packing=%s",
                job.id,
                job.base_model,
                gpu_type,
                gpu_count,
                plan.context_length,
                plan.per_device_batch,
                plan.grad_accum,
                plan.n_epochs,
                plan.packing,
            )
            resp = truss_push(config=tmp / "config.py")

        job_id = resp["id"]
        project_id = resp["training_project"]["id"]
        remote_id = f"{project_id}:{job_id}"

        logger.info(
            "BasetenRunner: submitted job_id=%s project_id=%s remote_id=%s",
            job_id,
            project_id,
            remote_id,
        )
        return SubmissionResult(
            remote_id=remote_id,
            run_url=f"https://app.baseten.co/training/{project_id}/{job_id}",
            training_file_id="",
            num_examples=num_examples,
        )

    def poll(self, remote_id: str) -> PollSnapshot:
        import json as _json  # noqa: PLC0415, F811

        project_id, job_id = self._parse_remote_id(remote_id)
        raw_job = self._get_job(project_id, job_id)
        raw_status = raw_job.get("current_status", "")
        state = self._normalise_status(raw_status)
        phase = self._PHASE_MAP.get(raw_status, "running")

        logs = self._fetch_logs(project_id, job_id, start_ms=0)

        trained_steps: int | None = None
        total_steps: int | None = None
        train_loss: float | None = None
        eval_loss: float | None = None
        learning_rate: float | None = None
        token_accuracy: float | None = None
        eval_token_accuracy: float | None = None
        current_epoch: float | None = None
        eta_s: float | None = None
        tokens_processed: int | None = None
        metrics_history: list[dict] = []
        eval_history: list[dict] = []
        checkpoint_history: list[dict] = []

        for entry in logs:
            msg = (entry.get("message") or "").strip()
            if msg.startswith("BT_PROGRESS "):
                try:
                    rec = _json.loads(msg[len("BT_PROGRESS ") :])
                    trained_steps = rec.get("step")
                    total_steps = rec.get("total_steps")
                    train_loss = rec.get("loss")
                    learning_rate = rec.get("lr")
                    token_accuracy = rec.get("token_accuracy")
                    current_epoch = rec.get("epoch")
                    eta_s = rec.get("eta_s")
                    if rec.get("num_tokens") is not None:
                        tokens_processed = int(rec["num_tokens"])
                    point: dict = {"step": trained_steps, "epoch": current_epoch}
                    if train_loss is not None:
                        point["train_loss"] = round(train_loss, 6)
                    if token_accuracy is not None:
                        point["token_accuracy"] = round(token_accuracy, 6)
                    if learning_rate is not None:
                        point["lr"] = learning_rate
                    if rec.get("grad_norm") is not None:
                        point["grad_norm"] = float(rec["grad_norm"])
                    metrics_history.append(point)
                except _json.JSONDecodeError:
                    pass
            elif msg.startswith("BT_CHECKPOINT "):
                try:
                    rec = _json.loads(msg[len("BT_CHECKPOINT ") :])
                    checkpoint_history.append(
                        {
                            "step": rec.get("step"),
                            "epoch": rec.get("epoch"),
                            "path": str(rec.get("path") or ""),
                            "timestamp": entry.get("timestamp"),
                        }
                    )
                except _json.JSONDecodeError:
                    pass
            elif msg.startswith("BT_EVAL "):
                try:
                    rec = _json.loads(msg[len("BT_EVAL ") :])
                    eval_loss = rec.get("eval_loss")
                    eval_token_accuracy = rec.get("eval_token_accuracy")
                    eval_point: dict = {
                        "step": rec.get("step"),
                        "epoch": rec.get("epoch"),
                    }
                    if eval_loss is not None:
                        eval_point["eval_loss"] = round(eval_loss, 6)
                    if eval_token_accuracy is not None:
                        eval_point["eval_token_accuracy"] = round(eval_token_accuracy, 6)
                    eval_history.append(eval_point)
                except _json.JSONDecodeError:
                    pass

        stage, download = parse_download_stage(logs)

        import time as _time  # noqa: PLC0415

        estimated_finish = int(_time.time() + eta_s) if eta_s is not None and eta_s > 0 else None

        error = raw_job.get("error_message", "") or ""

        # Map the BT_* histories onto the monitor's series contract
        # (progress_from_snapshot → job.progress.metrics.*).
        loss_by_step: dict[int, dict[str, Any]] = {}
        lr_series: list[dict[str, Any]] = []
        grad_series: list[dict[str, Any]] = []
        ta_by_step: dict[int, dict[str, Any]] = {}
        for point in metrics_history:
            p_step = point.get("step")
            if p_step is None:
                continue
            if point.get("train_loss") is not None:
                loss_by_step.setdefault(p_step, {"step": p_step})["train_loss"] = point[
                    "train_loss"
                ]
            if point.get("lr") is not None:
                lr_series.append({"step": p_step, "value": point["lr"]})
            if point.get("grad_norm") is not None:
                grad_series.append({"step": p_step, "value": point["grad_norm"]})
            if point.get("token_accuracy") is not None:
                ta_by_step.setdefault(p_step, {"step": p_step})["train"] = point["token_accuracy"]
        eval_by_step: dict[int, dict[str, Any]] = {}
        for ev in eval_history:
            e_step = ev.get("step")
            if e_step is None:
                continue
            if ev.get("eval_loss") is not None:
                loss_by_step.setdefault(e_step, {"step": e_step})["eval_loss"] = ev["eval_loss"]
                eval_by_step[int(e_step)] = ev
            if ev.get("eval_token_accuracy") is not None:
                ta_by_step.setdefault(e_step, {"step": e_step})["eval"] = ev["eval_token_accuracy"]
        loss_series = [loss_by_step[s] for s in sorted(loss_by_step)]
        token_accuracy_series = [ta_by_step[s] for s in sorted(ta_by_step)]

        # Baseten checkpoints are weight artifacts in the job's checkpoint volume, not
        # chat-callable endpoints, so is_checkpoint_inferable stays False.
        checkpoints: list[dict[str, Any]] = []
        for ckpt in checkpoint_history:
            c_step = ckpt.get("step")
            if c_step is None:
                continue
            c_step = int(c_step)
            ev = eval_by_step.get(c_step, {})
            train_point = loss_by_step.get(c_step, {})
            checkpoints.append(
                {
                    "id": ckpt["path"] or f"checkpoint-{c_step}",
                    "step": c_step,
                    "type": "checkpoint",
                    "path": ckpt["path"],
                    "train_loss": train_point.get("train_loss"),
                    "valid_loss": ev.get("eval_loss"),
                    "valid_mean_token_accuracy": ev.get("eval_token_accuracy"),
                    "result_files": [],
                    "file_count": 0,
                    "created_at": ckpt.get("timestamp"),
                    "resumable": True,
                    "has_eval": ev.get("eval_loss") is not None,
                    "upload_status": "uploaded",
                }
            )
        checkpoints.sort(key=lambda r: r["step"])

        # Baseten has no provider-side model name, so synthesise a stable id: the
        # final-eval/deployment pipeline needs a non-empty artifact ref, while the
        # weights are fetched by remote_id through the checkpoint API.
        output_model_name = f"baseten/{job_id}/final" if state in self._TERMINAL_OK else ""

        return PollSnapshot(
            state=state,
            epochs_completed=int(current_epoch) if current_epoch is not None else None,
            tokens_processed=tokens_processed,
            trained_steps=trained_steps,
            step=trained_steps,
            total_steps=total_steps,
            phase=phase,
            train_loss=train_loss,
            eval_loss=eval_loss,
            learning_rate=learning_rate,
            token_accuracy=token_accuracy,
            eval_token_accuracy=eval_token_accuracy,
            current_epoch=current_epoch,
            eta_s=eta_s,
            estimated_finish=estimated_finish,
            loss_series=loss_series,
            learning_rate_series=lr_series,
            grad_norm_series=grad_series,
            token_accuracy_series=token_accuracy_series,
            checkpoints=checkpoints,
            metrics_history=metrics_history,
            eval_history=eval_history,
            activity=filter_activity_logs(logs),
            stage=stage,
            download=download,
            output_model_name=output_model_name,
            error=error,
            raw=raw_job,
        )

    def cancel(self, remote_id: str) -> None:
        """Stop the remote training job (POST …/jobs/{id}/stop → TRAINING_JOB_STOPPED)."""
        import requests  # noqa: PLC0415

        project_id, job_id = self._parse_remote_id(remote_id)
        resp = requests.post(
            f"{self._API_BASE}/v1/training_projects/{project_id}/jobs/{job_id}/stop",
            headers=self._headers(),
            json={},
            timeout=30,
        )
        resp.raise_for_status()

    def fetch_epoch_losses(self, remote_id: str) -> list[dict]:
        import json as _json  # noqa: PLC0415, F811

        project_id, job_id = self._parse_remote_id(remote_id)
        logs = self._fetch_logs(project_id, job_id, start_ms=0)

        evals: list[dict] = []
        for entry in logs:
            msg = (entry.get("message") or "").strip()
            if msg.startswith("BT_EVAL "):
                try:
                    rec = _json.loads(msg[len("BT_EVAL ") :])
                    evals.append(rec)
                except _json.JSONDecodeError:
                    pass

        # Train loss per epoch = the last BT_PROGRESS in that epoch bucket.
        epoch_train: dict[int, float] = {}
        for entry in logs:
            msg = (entry.get("message") or "").strip()
            if msg.startswith("BT_PROGRESS "):
                try:
                    rec = _json.loads(msg[len("BT_PROGRESS ") :])
                    epoch_int = int(rec.get("epoch", 0))  # floor epoch
                    loss = rec.get("loss")
                    if loss is not None:
                        epoch_train[epoch_int] = float(loss)
                except _json.JSONDecodeError:
                    pass

        result = []
        for i, ev in enumerate(evals, start=1):
            epoch_float = ev.get("epoch", float(i))
            epoch_int = int(epoch_float)
            t_loss = epoch_train.get(epoch_int) or epoch_train.get(epoch_int - 1)
            result.append(
                {
                    "epoch": i,
                    "train_loss": t_loss,
                    "valid_loss": ev.get("eval_loss"),
                }
            )

        return result

    def is_terminal_ok(self, state: str) -> bool:
        return state in self._TERMINAL_OK

    def is_terminal_fail(self, state: str) -> bool:
        return state in self._TERMINAL_FAIL

    def is_terminal_cancelled(self, state: str) -> bool:
        return state in self._TERMINAL_CANCELLED


class ModalRunner(BaseFinetuningRunner):
    """Runner backed by Modal Functions in our own ``overmind-sft`` app.

    Submit spawns a training Function with an ephemeral GPU, poll reads progress off
    the run's Modal Volume directory, and cancel stops the FunctionCall.

    A Modal Function's image is fixed at deploy time, so the USE_UNSLOTH trainer choice
    selects between prebuilt Functions (``sft_{stack}``) instead of branching
    inside one the way BasetenRunner's run.sh does.

    ``remote_id`` is ``"{run_id}:{function_call_id}"``. Requires the ``modal`` SDK plus
    its tokens on the worker, and ``overmind-sft`` deployed.
    """

    @property
    def _app_name(self) -> str:
        return getattr(settings, "MODAL_SFT_APP_NAME", "overmind-sft")

    @staticmethod
    def _modal_env() -> str | None:
        """Modal environment from MODAL_ENVIRONMENT, or None for the client default.

        Passed explicitly to every ``Function.from_name`` so this runner stays scoped to
        the intended ``modal deploy --env`` target instead of relying on the SDK's
        ambient-env fallback.
        """
        return os.environ.get("MODAL_ENVIRONMENT") or None

    _TERMINAL_OK = {"succeeded"}
    _TERMINAL_FAIL = {"failed"}
    _TERMINAL_CANCELLED = {"cancelled"}

    _PHASE_MAP: dict[str, str] = {
        "starting": "queued",
        "running": "training",
        "succeeded": "finalizing",
        "failed": "failed",
        "cancelled": "cancelled",
    }

    # Same tiers as BasetenRunner. ≤72B LoRA stays on 1×H100 with QLoRA: 2–3×H100
    # device_map="balanced" splits OOMed on the last stage during Unsloth
    # gradient-checkpoint recompute.
    # Context above 32k upgrades 1×H100 → 1×H200 (141 GB vs 80 GB activation headroom).
    _LONG_CONTEXT_H200 = 32768
    _GPU_TABLE = [
        (7, "H100", 1),
        (14, "H100", 1),
        (32, "H100", 1),
        (72, "H100", 1),
        (float("inf"), "H100", 4),
    ]
    _GPU_TABLE_FULL = [
        (9, "H100", 1),
        # 10–20B full needs 1×H200: Gemma4 Unsloth full FT crashes on 2×H100 with
        # device_map="balanced", and 12B bf16+grads+optim (~72GB) overflows 1×H100.
        (20, "H200", 1),
        (32, "H100", 4),
        (float("inf"), "H200", 4),
    ]

    @staticmethod
    def _job_training_type(job) -> str:
        hp = dict(getattr(job, "hyperparameters", None) or {})
        return str((hp.get("training_type") or {}).get("type") or "Lora")

    def _select_training_gpu(self, job, *, context_length: int = 0) -> tuple[str, int]:
        try:
            from overbae.modal.model_registry import get_model_config_any_backend  # noqa: PLC0415

            cfg = get_model_config_any_backend(job.base_model) or {}
            params_b = (
                float(cfg.get("total_params_b") or 0) or (cfg.get("num_parameters") or 0) / 1e9
            )
        except Exception:  # noqa: BLE001
            logger.warning("ModalRunner: could not look up model params for %s", job.base_model)
            params_b = 0

        table = self._GPU_TABLE if self._job_training_type(job) == "Lora" else self._GPU_TABLE_FULL
        for max_b, gpu, count in table:
            if params_b <= max_b:
                if context_length > self._LONG_CONTEXT_H200 and count == 1 and gpu == "H100":
                    gpu = "H200"
                return clamp_gemma4_training_gpu(job.base_model, gpu, count)
        return clamp_gemma4_training_gpu(job.base_model, "H200", 4)

    @staticmethod
    def _gpu_string(gpu_type: str, gpu_count: int) -> str:
        return gpu_type if gpu_count <= 1 else f"{gpu_type}:{gpu_count}"

    @staticmethod
    def _training_env(
        plan: BasetenTrainingPlan,
        model_id: str,
        *,
        gpu_type: str = "H100",
        gpu_count: int = 1,
        params_b: float = 0,
        max_steps: int = 0,
    ) -> dict[str, str]:
        """Env vars for sft_assets/train.py.

        pretok owns assistant-only labels (no ASSISTANT_ONLY_LOSS). PACK_ROWS follows
        the plan — engine_unsloth packs pretokenized rows when enabled.

        LOAD_IN_4BIT is forced when bf16 LoRA weights cannot fit the selected GPUs
        (70B bf16 ≈140GB > 1×H100), which also avoids the multi-GPU OOM path.
        """
        from overbae.modal.model_registry import get_hf_base  # noqa: PLC0415

        hf_base = get_hf_base(str(model_id))
        env = {
            "MODEL_ID": hf_base,
            # The shared snapshot the deploy path already maintains. base_weights_for falls back
            # to the hub if the sweep has not staged it yet, so this is a hint, not a contract.
            "BASE_MODEL_PATH": f"/weights/.base_models/{hf_base.replace('/', '--')}",
            "TRAINING_TYPE": plan.training_type,
            "MAX_LENGTH": str(plan.context_length),
            "LORA_R": str(plan.lora_r),
            "LORA_ALPHA": str(plan.lora_alpha),
            "LORA_DROPOUT": str(plan.lora_dropout),
            "LORA_TARGET_MODULES": plan.lora_target_modules,
            "PER_DEVICE_BATCH": str(plan.per_device_batch),
            "GRAD_ACCUM": str(plan.grad_accum),
            "N_EPOCHS": str(plan.n_epochs),
            "LEARNING_RATE": str(plan.learning_rate),
            "WARMUP_RATIO": str(plan.warmup_ratio),
            "WEIGHT_DECAY": str(plan.weight_decay),
            "SEED": str(plan.seed),
            "PACK_ROWS": "1" if plan.packing else "0",
        }
        unsloth = use_unsloth()
        env["USE_UNSLOTH"] = "true" if unsloth else "false"
        unsloth_image = ""
        if unsloth:
            from overbae.modal.model_registry import get_unsloth_image  # noqa: PLC0415

            unsloth_image = get_unsloth_image(model_id)
            env["UNSLOTH_IMAGE"] = unsloth_image
        from modal_shared.modelfam import family_key  # noqa: PLC0415

        is_gpt_oss = family_key(str(model_id)) == "gpt_oss"
        if plan.training_type == "Lora" and (plan.load_in_4bit or is_gpt_oss):
            env["LOAD_IN_4BIT"] = "1"
        if is_gpt_oss:
            env["UNSLOTH_COMPILE_DISABLE"] = "1"
        if max_steps > 0:
            # Probe-run cap only: the wizard and recommender never set max_steps.
            env["MAX_STEPS"] = str(max_steps)
        return env

    @staticmethod
    def _parse_remote_id(remote_id: str) -> tuple[str, str]:
        run_id, call_id = remote_id.split(":", 1)
        return run_id, call_id

    def submit(
        self,
        job,
        training_file_path: str,
        num_examples: int | None,
        validation_file_path: str | None = None,
    ) -> SubmissionResult:
        import uuid as _uuid  # noqa: PLC0415

        import modal  # noqa: PLC0415

        if num_examples == 0:
            raise RuntimeError("Dataset produced 0 training examples.")

        from overbae.modal.model_registry import get_model_config_any_backend  # noqa: PLC0415
        from overbae.modal.training_type import training_context_length  # noqa: PLC0415

        model_cfg = get_model_config_any_backend(job.base_model) or {}
        ft_cfg = model_cfg.get("finetuning") or {}
        stats = dict((job.cell.stats if job.cell_id else None) or {})
        try:
            max_row_tokens = int(stats.get("max_token_length") or 0)
        except (TypeError, ValueError):
            max_row_tokens = 0
        hp = dict(job.hyperparameters or {})
        training_kind = "full" if str(hp.get("training_type", {}).get("type")) == "Full" else "lora"
        # Modal reuses the Baseten catalog — models.json has no "modal" rows.
        model_max = training_context_length(model_cfg, training_kind)
        requested_ctx = int(hp.get("context_length") or 0) or None
        early_ctx = baseten_context_length(
            max_row_tokens, model_max=model_max, requested=requested_ctx
        )
        gpu_type, gpu_count = self._select_training_gpu(job, context_length=early_ctx)
        plan = derive_baseten_training_plan(
            hyperparameters=job.hyperparameters,
            num_train_examples=int(num_examples or 0),
            dataset_stats=stats,
            params_b=float(model_cfg.get("total_params_b") or 0),
            model_max_context=model_max,
            model_min_batch=int(ft_cfg.get("min_batch_size") or 1),
            model_max_batch=int(ft_cfg["max_batch_size"]) if ft_cfg.get("max_batch_size") else None,
            gpu_type=gpu_type,
            gpu_count=gpu_count,
            model_id=job.base_model,
            hidden_size=int(model_cfg.get("hidden_size") or 0),
        )
        for note in plan.notes:
            logger.info("ModalRunner plan (job %s): %s", job.id, note)

        from overbae.models import FinetuningJob as _FTJob  # noqa: PLC0415

        _FTJob.objects.filter(pk=job.pk).update(
            hyperparameters={
                **(dict(job.hyperparameters) or {}),
                "context_length": plan.context_length,
                "n_epochs": plan.n_epochs,
                "batch_size": plan.batch_size,
                "learning_rate": plan.learning_rate,
                "warmup_ratio": plan.warmup_ratio,
                "packing": plan.packing,
            }
        )

        run_id = f"ft-{job.id}-{_uuid.uuid4().hex[:8]}"
        max_steps = int((job.hyperparameters or {}).get("max_steps") or 0)
        env = self._training_env(
            plan,
            job.base_model,
            gpu_type=gpu_type,
            gpu_count=gpu_count,
            params_b=float(model_cfg.get("total_params_b") or 0),
            max_steps=max_steps,
        )
        hidden = int(model_cfg.get("hidden_size") or 0)
        if hidden > 0:
            env["HIDDEN_SIZE"] = str(hidden)

        with open(training_file_path) as f:
            data_text = f.read()
        val_text = None
        if validation_file_path:
            with open(validation_file_path) as f:
                val_text = f.read()

        logger.info(
            "ModalRunner: submitting run %s — model=%s gpu=%s×%d ctx=%d batch=%d×%d epochs=%d",
            run_id,
            job.base_model,
            gpu_type,
            gpu_count,
            plan.context_length,
            plan.per_device_batch,
            plan.grad_accum,
            plan.n_epochs,
        )

        env_name = self._modal_env()
        upload_fn = modal.Function.from_name(
            self._app_name, "upload_dataset", environment_name=env_name
        )
        upload_fn.remote(run_id=run_id, data_jsonl=data_text, val_jsonl=val_text)

        # One Function per frozen train stack — see modal_shared.stacks.TRAIN_FUNCTION_NAMES.
        from modal_shared.stacks import TRAIN_STOCK, train_function_name  # noqa: PLC0415
        from overbae.modal.model_registry import get_unsloth_image  # noqa: PLC0415

        function_name = train_function_name(
            get_unsloth_image(job.base_model) if use_unsloth() else TRAIN_STOCK
        )
        train_fn = modal.Function.from_name(
            self._app_name, function_name, environment_name=env_name
        )
        call = train_fn.with_options(gpu=self._gpu_string(gpu_type, gpu_count)).spawn(
            run_id=run_id, env=env, gpu_type=gpu_type, gpu_count=gpu_count
        )

        remote_id = f"{run_id}:{call.object_id}"
        return SubmissionResult(
            remote_id=remote_id,
            run_url="",
            training_file_id=run_id,
            num_examples=num_examples,
        )

    def poll(self, remote_id: str) -> PollSnapshot:
        import modal  # noqa: PLC0415

        run_id, call_id = self._parse_remote_id(remote_id)

        get_progress_fn = modal.Function.from_name(
            self._app_name, "get_progress", environment_name=self._modal_env()
        )
        snap = get_progress_fn.remote(run_id)

        meta = snap.get("meta") or {}
        meta_status = str(meta.get("status") or "")
        call_error = ""
        has_final = bool(snap.get("has_final_checkpoint"))

        if meta_status == "cancelled":
            state = "cancelled"
        else:
            in_flight = False
            call_ok = False
            try:
                call = modal.FunctionCall.from_id(call_id)
                call.get(timeout=0)
                call_ok = True
            except TimeoutError:
                in_flight = True
            except Exception as exc:  # noqa: BLE001 — FunctionCall raise is not death (retries).
                call_error = str(exc)
            state = resolve_modal_job_state(
                meta_status, in_flight=in_flight, call_ok=call_ok, has_final=has_final
            )

        phase = self._PHASE_MAP.get(meta_status or state, "training")

        metrics_history: list[dict] = []
        eval_history: list[dict] = []
        checkpoint_history: list[dict] = []
        trained_steps: int | None = None
        total_steps: int | None = None
        train_loss: float | None = None
        eval_loss: float | None = None
        learning_rate: float | None = None
        token_accuracy: float | None = None
        eval_token_accuracy: float | None = None
        current_epoch: float | None = None
        eta_s: float | None = None
        tokens_processed: int | None = None

        for rec in snap.get("metrics") or []:
            event = rec.get("event")
            if event == "BT_PROGRESS":
                trained_steps = rec.get("step")
                total_steps = rec.get("total_steps")
                train_loss = rec.get("loss")
                learning_rate = rec.get("lr")
                token_accuracy = rec.get("token_accuracy")
                current_epoch = rec.get("epoch")
                eta_s = rec.get("eta_s")
                if rec.get("num_tokens") is not None:
                    tokens_processed = int(rec["num_tokens"])
                point: dict = {"step": trained_steps, "epoch": current_epoch}
                if train_loss is not None:
                    point["train_loss"] = round(train_loss, 6)
                if token_accuracy is not None:
                    point["token_accuracy"] = round(token_accuracy, 6)
                if learning_rate is not None:
                    point["lr"] = learning_rate
                if rec.get("grad_norm") is not None:
                    point["grad_norm"] = float(rec["grad_norm"])
                metrics_history.append(point)
            elif event == "BT_CHECKPOINT":
                checkpoint_history.append(
                    {
                        "step": rec.get("step"),
                        "epoch": rec.get("epoch"),
                        "path": str(rec.get("path") or ""),
                        "timestamp": None,
                    }
                )
            elif event == "BT_EVAL":
                eval_loss = rec.get("eval_loss")
                eval_token_accuracy = rec.get("eval_token_accuracy")
                eval_point: dict = {"step": rec.get("step"), "epoch": rec.get("epoch")}
                if eval_loss is not None:
                    eval_point["eval_loss"] = round(eval_loss, 6)
                if eval_token_accuracy is not None:
                    eval_point["eval_token_accuracy"] = round(eval_token_accuracy, 6)
                eval_history.append(eval_point)

        import time as _time  # noqa: PLC0415

        estimated_finish = int(_time.time() + eta_s) if eta_s is not None and eta_s > 0 else None

        loss_by_step: dict[int, dict[str, Any]] = {}
        lr_series: list[dict[str, Any]] = []
        grad_series: list[dict[str, Any]] = []
        ta_by_step: dict[int, dict[str, Any]] = {}
        for point in metrics_history:
            p_step = point.get("step")
            if p_step is None:
                continue
            if point.get("train_loss") is not None:
                loss_by_step.setdefault(p_step, {"step": p_step})["train_loss"] = point[
                    "train_loss"
                ]
            if point.get("lr") is not None:
                lr_series.append({"step": p_step, "value": point["lr"]})
            if point.get("grad_norm") is not None:
                grad_series.append({"step": p_step, "value": point["grad_norm"]})
            if point.get("token_accuracy") is not None:
                ta_by_step.setdefault(p_step, {"step": p_step})["train"] = point["token_accuracy"]
        eval_by_step: dict[int, dict[str, Any]] = {}
        for ev in eval_history:
            e_step = ev.get("step")
            if e_step is None:
                continue
            if ev.get("eval_loss") is not None:
                loss_by_step.setdefault(e_step, {"step": e_step})["eval_loss"] = ev["eval_loss"]
                eval_by_step[int(e_step)] = ev
            if ev.get("eval_token_accuracy") is not None:
                ta_by_step.setdefault(e_step, {"step": e_step})["eval"] = ev["eval_token_accuracy"]
        loss_series = [loss_by_step[s] for s in sorted(loss_by_step)]
        token_accuracy_series = [ta_by_step[s] for s in sorted(ta_by_step)]

        checkpoints: list[dict[str, Any]] = []
        for ckpt in checkpoint_history:
            c_step = ckpt.get("step")
            if c_step is None:
                continue
            c_step = int(c_step)
            ev = eval_by_step.get(c_step, {})
            train_point = loss_by_step.get(c_step, {})
            checkpoints.append(
                {
                    "id": ckpt["path"] or f"checkpoint-{c_step}",
                    "step": c_step,
                    "type": "checkpoint",
                    "path": ckpt["path"],
                    "train_loss": train_point.get("train_loss"),
                    "valid_loss": ev.get("eval_loss"),
                    "valid_mean_token_accuracy": ev.get("eval_token_accuracy"),
                    "result_files": [],
                    "file_count": 0,
                    "created_at": ckpt.get("timestamp"),
                    "resumable": False,
                    "has_eval": ev.get("eval_loss") is not None,
                    "upload_status": "uploaded" if snap.get("has_final_checkpoint") else "pending",
                }
            )
        checkpoints.sort(key=lambda r: r["step"])

        output_model_name = (
            f"modal/{run_id}/final" if state in self._TERMINAL_OK or has_final else ""
        )
        error = str(meta.get("error") or call_error or "")

        return PollSnapshot(
            state=state,
            epochs_completed=int(current_epoch) if current_epoch is not None else None,
            tokens_processed=tokens_processed,
            trained_steps=trained_steps,
            step=trained_steps,
            total_steps=total_steps,
            phase=phase,
            train_loss=train_loss,
            eval_loss=eval_loss,
            learning_rate=learning_rate,
            token_accuracy=token_accuracy,
            eval_token_accuracy=eval_token_accuracy,
            current_epoch=current_epoch,
            eta_s=eta_s,
            estimated_finish=estimated_finish,
            loss_series=loss_series,
            learning_rate_series=lr_series,
            grad_norm_series=grad_series,
            token_accuracy_series=token_accuracy_series,
            checkpoints=checkpoints,
            metrics_history=metrics_history,
            eval_history=eval_history,
            activity=[],
            stage="",
            download=None,
            output_model_name=output_model_name,
            error=error,
            raw=snap,
        )

    def cancel(self, remote_id: str) -> None:
        import modal  # noqa: PLC0415

        run_id, call_id = self._parse_remote_id(remote_id)
        try:
            modal.FunctionCall.from_id(call_id).cancel()
        finally:
            mark_cancelled_fn = modal.Function.from_name(
                self._app_name, "mark_cancelled", environment_name=self._modal_env()
            )
            mark_cancelled_fn.remote(run_id)

    def fetch_epoch_losses(self, remote_id: str) -> list[dict]:
        snapshot = self.poll(remote_id)
        result = []
        for i, ev in enumerate(snapshot.eval_history, start=1):
            epoch_float = ev.get("epoch", float(i))
            epoch_int = int(epoch_float)
            t_loss = next(
                (
                    p.get("train_loss")
                    for p in reversed(snapshot.metrics_history)
                    if p.get("train_loss") is not None and int(p.get("epoch") or 0) <= epoch_int
                ),
                None,
            )
            result.append(
                {
                    "epoch": i,
                    "train_loss": t_loss,
                    "valid_loss": ev.get("eval_loss"),
                }
            )
        return result


_RUNNER_REGISTRY: dict[str, type[BaseFinetuningRunner]] = {
    "together": TogetherAIRunner,
    "baseten": BasetenRunner,
    "modal": ModalRunner,
}

_DEFAULT_RUNNER = "baseten"


def get_runner(backend: str | None = None) -> BaseFinetuningRunner:
    """The runner for ``backend``, else ``settings.FINETUNING_BACKEND``."""
    key = backend or getattr(settings, "FINETUNING_BACKEND", _DEFAULT_RUNNER)
    if key == "together_ai":
        key = "together"
    cls = _RUNNER_REGISTRY.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown fine-tuning backend '{key}'. Available: {list(_RUNNER_REGISTRY)}"
        )
    return cls()


def register_runner(key: str, cls: type[BaseFinetuningRunner]) -> None:
    _RUNNER_REGISTRY[key] = cls
