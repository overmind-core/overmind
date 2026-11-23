"""Provider-agnostic runner abstraction for fine-tuning jobs.

:class:`BaseFinetuningRunner` is the interface every backend satisfies; concrete
runners hold the provider-specific upload, submit and poll logic. Adding a provider
means implementing that interface and wiring it into :func:`get_runner`.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from django.conf import settings

from modal_shared.decisions import DECISION_OBJECTIVES
from overbae.services.finetuning_policy import (
    TrainingPlan,
    derive_training_plan,
    snap_context_length,
)
from overbae.services.training_policies import profile_options
from overbae.services.training_preparation import ready_for_job
from overbae.services.training_transfer import stage_data

logger = logging.getLogger(__name__)


def resolve_modal_job_state(
    meta_status: str,
    *,
    in_flight: bool,
    call_ok: bool,
    has_final: bool = False,
    remote_failed: bool = False,
) -> str:
    """Volume success / checkpoint wins. In-flight FunctionCall beats stale ``failed``."""
    if meta_status == "cancelled":
        return "cancelled"
    if has_final or meta_status == "succeeded":
        return "succeeded"
    if remote_failed:
        return "failed"
    if in_flight or not call_ok:
        return "running"
    if meta_status == "failed":
        return "failed"
    return "succeeded"


def _remote_call_failed(call_id: str) -> bool:
    """Only Modal's call graph separates a finished failure from an observer error."""
    import modal  # noqa: PLC0415
    from modal.call_graph import InputStatus  # noqa: PLC0415

    terminal = {
        InputStatus.FAILURE,
        InputStatus.INIT_FAILURE,
        InputStatus.TERMINATED,
        InputStatus.TIMEOUT,
    }
    try:
        graph = modal.FunctionCall.from_id(call_id).get_call_graph()
    except Exception:  # noqa: BLE001
        return False
    return any(node.function_call_id == call_id and node.status in terminal for node in graph)


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
    tokens_per_second: float | None = None
    tokens_processed: int | None = None

    # Step-based progress.
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
    """``[{step, train?, eval?}, …]``."""

    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    """Normalised checkpoint rows for the monitor tables."""

    # Extended live metrics
    learning_rate: float | None = None
    token_accuracy: float | None = None
    eval_token_accuracy: float | None = None
    current_epoch: float | None = None
    eta_s: float | None = None

    # Full step history for live charts
    # Each entry: {step, epoch, train_loss, token_accuracy, lr}
    metrics_history: list[dict] = field(default_factory=list)
    # Each eval entry: {step, epoch, eval_loss, eval_token_accuracy}
    eval_history: list[dict] = field(default_factory=list)

    activity: list[dict[str, Any]] = field(default_factory=list)
    """``[{ts, message}, …]`` (ts in epoch ms) for the live-activity feed — BT_*
    telemetry and progress-bar noise filtered out, so provisioning / warm-up /
    dataset-prep stages read as real progress."""

    diagnostics: dict[str, Any] = field(default_factory=dict)
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
    if stripped.startswith(("BT_PROGRESS ", "BT_MONITORING ")):
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
    Timestamps normalise to epoch ms; providers may emit nanosecond strings.
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
    final_pending = any(
        (e or {}).get("kind") in ("final", "incumbent_after")
        and str(e.get("status") or "").lower() in ("running", "pending")
        for e in evals
    )

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


# Backend names must never reach the frontend — error strings and log lines
# can echo them from raw exceptions/provider APIs (e.g. "Modal function failed",
# "NEBIUS_API_KEY not set"). Boundaries are letter-only (not \b, which treats
# `_` as a word char) so "NEBIUS_API_KEY" still matches. ``modal`` alone gets
# an extra ``(?!_)`` so package names like ``modal_shared`` stay intact.
_BACKEND_NAME_RE = re.compile(
    r"(?<![A-Za-z])(nebius|modal(?!_))(?![A-Za-z])",
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


def native_time_range(snap):
    intervals, previous = [], None
    for row in snap.raw.get("metrics", []):
        if row.get("event") != "BT_PROGRESS":
            continue
        step, elapsed = row.get("step"), row.get("elapsed_s")
        if any(
            not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in (step, elapsed)
        ):
            intervals, previous = [], None
            continue
        if previous:
            steps, seconds = step - previous[0], elapsed - previous[1]
            if steps < 0 or seconds < 0:
                intervals = []
            elif steps > 0 and seconds > 0:
                intervals.append((seconds / steps, seconds, steps))
        previous = (step, elapsed)
    intervals = intervals[-30:]
    window = {"intervals": len(intervals), "seconds": sum(item[1] for item in intervals)}
    if (
        len(intervals) < 5
        or window["seconds"] < 30
        or not snap.total_steps
        or snap.step is None
        or (snap.stage and snap.stage != "training")
    ):
        return None, window
    remaining = max(0, snap.total_steps - snap.step)
    speeds = sorted(item[0] for item in intervals)
    average = window["seconds"] / sum(item[2] for item in intervals)
    # Sparse slow batches still contribute to the elapsed-time average.
    low = min(speeds[int((len(speeds) - 1) * 0.1)], average * 0.8)
    high = max(speeds[math.ceil((len(speeds) - 1) * 0.9)], average * 1.3)
    return [math.floor(low * remaining), math.ceil(high * remaining)], window


def progress_from_snapshot(snap: PollSnapshot, *, started_at=None) -> dict[str, Any]:
    """The durable ``FinetuningJob.progress`` blob for one poll snapshot."""
    trained = snap.step if snap.step is not None else snap.epochs_completed
    total = snap.total_steps
    percent: float | None = None
    if trained is not None and total and total > 0:
        percent = min(100.0, round(100.0 * float(trained) / float(total), 2))

    eta_seconds: int | None = None
    now = int(time.time())
    if snap.estimated_finish and snap.estimated_finish > now:
        eta_seconds = int(snap.estimated_finish - now)
    elif (
        percent is not None
        and 0 < percent < 100
        and started_at is not None
        and isinstance(started_at, datetime)
    ):
        elapsed = max(1.0, (time.time() - started_at.timestamp()))
        eta_seconds = int(elapsed * (100.0 - percent) / percent)
    elapsed_seconds: int | None = None
    if started_at is not None and isinstance(started_at, datetime):
        elapsed_seconds = max(0, int(time.time() - started_at.timestamp()))
    native = isinstance(snap.raw, dict) and "run_id" in snap.raw
    eta_range, eta_window = None, None
    if native:
        eta_seconds = None
        eta_range, eta_window = native_time_range(snap)
        if snap.state in {"succeeded", "failed", "cancelled"}:
            eta_seconds, eta_range = None, None

    return {
        "epochs_completed": snap.epochs_completed,
        "tokens_per_second": snap.tokens_per_second,
        "tokens_processed": snap.tokens_processed,
        "trained_steps": trained,
        "total_steps": total,
        "percent": percent,
        "estimated_finish": None if native else snap.estimated_finish,
        "eta_seconds": eta_seconds,
        "eta_range_seconds": eta_range,
        "eta_window": eta_window,
        "eta_basis": "recent_step_window; elapsed-time average with 0.8–1.3 planning margin, widened by observed step-time quantiles; training steps only, excludes final validation/reload; not confidence bounds"
        if native
        else "provider_or_elapsed_progress",
        "compute_usage": (snap.raw.get("meta") or {}).get("compute_usage", []) if native else [],
        "elapsed_seconds": elapsed_seconds,
        "phase": snap.phase or "",
        "stage": snap.stage or "",
        "diagnostics": snap.diagnostics,
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
        "eta_s": eta_seconds if native else snap.eta_s,
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


class ModalRunner(BaseFinetuningRunner):
    """Runner backed by Modal Functions in our own ``overmind-sft`` app.

    Submit spawns a training Function with an ephemeral GPU, poll reads progress off
    the run's Modal Volume directory, and cancel stops the FunctionCall.

    A Modal Function's image is fixed at deploy time, so the family's train stack
    selects between prebuilt Functions (``sft_{stack}``) instead of branching
    inside one.

    ``remote_id`` is ``"{run_id}:{function_call_id}"``. Requires the ``modal`` SDK plus
    its tokens on the worker, and ``overmind-sft`` deployed.
    """

    def __init__(self, release=None):
        self.release = release

    @property
    def _app_name(self):
        if self.release is None:
            raise ValueError("Training observer requires a pinned worker release.")
        return self.release["app"]

    def _modal_env(self):
        if self.release is None:
            raise ValueError("Training observer requires a pinned worker environment.")
        return self.release["environment"]

    def _await_base_model(self, hf_base: str) -> dict:
        """Block until ``.base_models/`` has this repo. Concurrent jobs share the mutex."""
        import modal  # noqa: PLC0415

        return modal.Function.from_name(
            "overmind-register", "fetch_base_model", environment_name=self._modal_env()
        ).remote(base_model=hf_base)

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

    # ≤72B LoRA stays on 1×H100 with QLoRA: 2–3×H100
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

    @staticmethod
    def model_config(model_id: str) -> dict[str, Any]:
        from overbae.modal.model_registry import get_all_models_by_backend  # noqa: PLC0415

        config = get_all_models_by_backend("modal").get(model_id) or get_all_models_by_backend(
            "decision", include_disabled=False
        ).get(model_id)
        if not config:
            raise ValueError(f"Model {model_id} is unavailable for Modal training.")
        return config

    def _select_training_gpu(self, job, *, context_length: int = 0) -> tuple[str, int]:
        from modal_shared.modelfam import clamp_training_gpus  # noqa: PLC0415

        try:
            cfg = self.model_config(job.base_model)
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
                return clamp_training_gpus(job.base_model, gpu, count)
        return clamp_training_gpus(job.base_model, "H200", 4)

    @staticmethod
    def _gpu_string(gpu_type: str, gpu_count: int) -> str:
        return gpu_type if gpu_count <= 1 else f"{gpu_type}:{gpu_count}"

    @staticmethod
    def _training_env(
        plan: TrainingPlan,
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
            "BASE_MODEL_PATH": f"/weights/.base_models/{hf_base.replace('/', '--')}",
            "PADDED_TOKEN_BUDGET": str(plan.token_budget),
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
            "DECISION_PRE_TRAINING_BASELINE": "1" if plan.pre_training_baseline else "0",
            "PACK_ROWS": "1" if plan.packing else "0",
        }
        from modal_shared.modelfam import resolve  # noqa: PLC0415

        # Family compile flags are hooks.env_overrides, applied before unsloth.
        # 4-bit is also recorded here so the saved job parameters match the run.
        if plan.training_type == "Lora" and (
            plan.load_in_4bit or resolve(model_id).lora_load_in_4bit
        ):
            env["LOAD_IN_4BIT"] = "1"
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

        from overbae.modal.training_type import training_context_length  # noqa: PLC0415

        model_cfg = self.model_config(job.base_model)
        ft_cfg = model_cfg.get("finetuning") or {}
        stats = dict((job.cell.stats if job.cell_id else None) or {})
        hp = dict(job.hyperparameters or {})
        training_kind = "full" if str(hp.get("training_type", {}).get("type")) == "Full" else "lora"
        # Exact preprocessing, not the character estimate, owns the chosen context.
        max_row_tokens = 0
        stats["max_token_length"] = 0
        model_max = training_context_length(model_cfg, training_kind)
        requested_ctx = int(hp.get("context_length") or 0) or None
        early_ctx = snap_context_length(
            max_row_tokens, model_max=model_max, requested=requested_ctx
        )
        gpu_type, gpu_count = self._select_training_gpu(job, context_length=early_ctx)
        plan = derive_training_plan(
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
        if (job.hyperparameters or {}).get("runtime_profile"):
            env["RUNTIME_PROFILE"] = json.dumps(job.hyperparameters["runtime_profile"])
        if (job.hyperparameters or {}).get("monitoring") is not None:
            env["TRAINING_MONITORING"] = json.dumps(job.hyperparameters["monitoring"])
        hidden = int(model_cfg.get("hidden_size") or 0)
        if hidden > 0:
            env["HIDDEN_SIZE"] = str(hidden)

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

        effective = {
            "gpu_type": gpu_type,
            "gpu_count": gpu_count,
            "environment": self.release["environment"],
            "release": self.release,
            "parameters": {
                key: env[key]
                for key in (
                    "SEED",
                    "DECISION_PRE_TRAINING_BASELINE",
                    "WEIGHT_DECAY",
                    "LEARNING_RATE",
                    "GRAD_ACCUM",
                    "PER_DEVICE_BATCH",
                    "MAX_LENGTH",
                    "N_EPOCHS",
                    "WARMUP_RATIO",
                    "LORA_R",
                    "LORA_ALPHA",
                    "LORA_DROPOUT",
                    "LORA_TARGET_MODULES",
                    "TRAINING_TYPE",
                    "PACK_ROWS",
                    "LOAD_IN_4BIT",
                    "MAX_STEPS",
                    "PADDED_TOKEN_BUDGET",
                )
                if key in env
            },
        }
        if "seed" in hp and int(env["SEED"]) != int(hp["seed"]):
            raise ValueError("Effective seed differs from the requested seed.")
        _FTJob.objects.filter(pk=job.pk).update(effective_configuration=effective)

        env_name = self._modal_env()
        upload_fn = modal.Function.from_name(
            self._app_name, "upload_dataset", environment_name=env_name
        )
        preparation = ready_for_job(job, plan.context_length)
        env["TRAINING_OBJECTIVE"] = preparation.config["objective"]
        env["UNSLOTH_IMAGE"] = preparation.config["stack"]
        if hp.get("checkpoint_policy"):
            env["DECISION_CHECKPOINT_POLICY"] = json.dumps(hp["checkpoint_policy"])
        volume = modal.Volume.from_name("overmind-sft", environment_name=env_name)
        paths = {"data": training_file_path}
        if validation_file_path:
            paths["val"] = validation_file_path
        from overbae.services import training_submission  # noqa: PLC0415

        stage_data(job, paths, run_id, preparation, volume, upload_fn, num_examples=num_examples)

        training_submission.record_pre_dispatch_stage(
            job,
            "preparing_base_model",
            "Checking base model weights and downloading missing files",
        )
        base = self._await_base_model(env["MODEL_ID"])
        if training_kind == "lora":
            from overbae.services.deployment import start_serving_snapshot  # noqa: PLC0415

            start_serving_snapshot(
                job, base=base if isinstance(base, dict) else {}, rank=int(env.get("LORA_R") or 16)
            )
        training_submission.record_pre_dispatch_stage(
            job, "starting_training_worker", "Base model weights ready; starting training worker"
        )

        # One Function per frozen train stack — see modal_shared.stacks.TRAIN_FUNCTION_NAMES.
        from modal_shared.stacks import train_function_name  # noqa: PLC0415

        function_name = train_function_name(preparation.config["stack"])
        train_fn = modal.Function.from_name(
            self._app_name, function_name, environment_name=env_name
        )
        options = {"gpu": self._gpu_string(gpu_type, gpu_count)}
        if preparation.config["objective"] in DECISION_OBJECTIVES:
            options["retries"] = modal.Retries(max_retries=10, initial_delay=0.0)
        options.update(profile_options(hp))
        training_submission.dispatching(job, run_id)
        call = train_fn.with_options(**options).spawn(
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
            remote_failed = False
            try:
                call = modal.FunctionCall.from_id(call_id)
                call.get(timeout=0)
                call_ok = True
            except TimeoutError:
                in_flight = True
            except Exception as exc:  # noqa: BLE001 — FunctionCall raise is not death (retries).
                call_error = str(exc)
                remote_failed = _remote_call_failed(call_id)
            state = resolve_modal_job_state(
                meta_status,
                in_flight=in_flight,
                call_ok=call_ok,
                has_final=has_final,
                remote_failed=remote_failed,
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
                for key in (
                    "argmax_target_agreement",
                    "hard_label_accuracy",
                    "hard_label_decisions",
                    "brier",
                    "decisions",
                    "runtime_seconds",
                ):
                    if rec.get(key) is not None:
                        eval_point[key] = rec[key]
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
            tokens_per_second=next(
                (
                    float(rec["tokens_per_s"])
                    for rec in reversed(snap.get("metrics") or [])
                    if rec.get("tokens_per_s") is not None
                ),
                None,
            ),
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
            diagnostics={
                **(snap.get("telemetry") or {}),
                **({"failure": meta["failure"]} if meta.get("failure") else {}),
            },
            stage=(snap.get("telemetry") or {}).get("stage", ""),
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
    "modal": ModalRunner,
}

_DEFAULT_RUNNER = "modal"


def get_runner(backend: str | None = None, *, job=None) -> BaseFinetuningRunner:
    """The runner for ``backend``, else ``settings.FINETUNING_BACKEND``."""
    pinned_backend = (
        "modal" if job is not None and (job.requested_configuration or {}).get("runtime") else None
    )
    key = backend or pinned_backend or getattr(settings, "FINETUNING_BACKEND", _DEFAULT_RUNNER)
    cls = _RUNNER_REGISTRY.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown fine-tuning backend '{key}'. Available: {list(_RUNNER_REGISTRY)}"
        )
    if key == "modal":
        from overbae.services.training_release import for_job

        return cls(release=for_job(job)) if job is not None else cls()
    return cls()
