"""In-training Overmind judge evals for fine-tuning jobs.

Reuses the normal :class:`~overbae.models.evaluation.EvalRun` pipeline (generate-mode
variant → ``run_eval_run``) against a :class:`ModelRef` pointing at the provider's chat
API — never a parallel scorer.

* **Baseline** — the capability's PRODUCTION incumbent (see ``_baseline_target``), never the
  base model of the family. Frontier incumbents fire immediately via OpenRouter, a
  self-hosted one routes through our Modal gateway, and only a capability with no
  resolvable model falls back to Modal-deploying the untouched base. Wizard
  multi-model groups share one baseline EvalRun per identical target.
* **Checkpoint** — only when the checkpoint is chat-callable. Together needs the path
  to look like a served model id; Baseten and Modal ship weights only, so never.
* **Final** — the fine-tune's own Modal deployment once ``output_model_name`` is set,
  so ``baseline_delta`` on that row is *finetuned − incumbent*.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction

logger = logging.getLogger(__name__)

# Same default as EvalRun.max_items; 0 is uncapped (EvalRun treats 0 as no slice).
DEFAULT_EVAL_MAX_ITEMS = 100


def _is_self_hosted(job) -> bool:
    """Baseten and Modal ship weight-only artifacts with no provider chat endpoint, so
    their evals route through OUR Modal deployment. Together serves its own fine-tunes.
    """
    from overbae.models import FinetuningJob

    return job.provider in (FinetuningJob.Provider.BASETEN, FinetuningJob.Provider.MODAL)


def job_wants_evals(job) -> bool:
    return bool(job.eval_dataset_id and job.eval_set_id)


def eval_max_items(job) -> int:
    hp = job.hyperparameters if isinstance(job.hyperparameters, dict) else {}
    raw = hp.get("eval_max_items", DEFAULT_EVAL_MAX_ITEMS)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = DEFAULT_EVAL_MAX_ITEMS
    if n <= 0:
        return 0
    return min(n, 200)


def is_checkpoint_inferable(job, ckpt: dict[str, Any]) -> bool:
    """Return True only when the checkpoint artifact can be scored via chat."""
    path = str(ckpt.get("path") or "").strip()
    if not path:
        return False

    from overbae.models import FinetuningJob

    if job.provider == FinetuningJob.Provider.TOGETHER_AI:
        lower = path.lower()
        if any(
            lower.endswith(ext)
            for ext in (".tar", ".tgz", ".gz", ".pt", ".bin", ".safetensors", ".zip")
        ):
            return False
        # Together served models are usually org/name or ft-…
        return "/" in path or path.startswith("ft-") or path.startswith("together/")

    # Baseten mid-run checkpoints are weight artifacts, not chat-callable; the final
    # eval runs against the Modal deployment instead.
    return False


def _metric_scores_for_run(eval_run_id) -> list[dict[str, Any]]:
    """Per-metric mean judge scores (0–1) for one EvalRun.

    The same means ``summary.variants[..].metrics`` reports, but computed from the
    persisted Score rows so it also works while the run is still in flight.
    """
    from django.db.models import Avg

    from overbae.models import Score

    return [
        {"name": r["name"], "score": round(float(r["avg"]), 6)}
        for r in Score.objects.filter(run_id=eval_run_id, scope="sample")
        .values("name")
        .annotate(avg=Avg("value"))
        .order_by("name")
        if r["avg"] is not None and r["name"]
    ]


def _sample_count_for_run(eval_run_id) -> int | None:
    from overbae.models import EvalSample

    n = EvalSample.objects.filter(run_id=eval_run_id).count()
    return n or None


def serialize_job_evals(job) -> list[dict[str, Any]]:
    from overbae.models import FinetuningJobEval

    rows = []
    for row in FinetuningJobEval.objects.filter(job=job).order_by("checkpoint_step", "created_at"):
        rows.append(
            {
                "id": str(row.id),
                "kind": row.kind,
                "status": row.status,
                "checkpoint_id": row.checkpoint_id or None,
                "checkpoint_step": row.checkpoint_step,
                "model_id": row.model_id,
                "aggregate_score": row.aggregate_score,
                "baseline_delta": row.baseline_delta,
                "class_metrics": row.class_metrics or None,
                "eval_run_id": str(row.eval_run_id) if row.eval_run_id else None,
                "error_message": row.error_message or None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "metric_scores": (
                    _metric_scores_for_run(row.eval_run_id) if row.eval_run_id else []
                ),
                "sample_count": (
                    _sample_count_for_run(row.eval_run_id) if row.eval_run_id else None
                ),
            }
        )
    return rows


def sync_eval_scores(job) -> list[dict[str, Any]]:
    """Pull completed EvalRun summaries onto FinetuningJobEval rows."""
    from overbae.models import EvalRun, FinetuningJobEval

    baseline = (
        FinetuningJobEval.objects.filter(job=job, kind=FinetuningJobEval.Kind.BASELINE)
        .order_by("created_at")
        .first()
    )
    baseline_score = baseline.aggregate_score if baseline else None

    for row in FinetuningJobEval.objects.filter(job=job, eval_run_id__isnull=False).select_related(
        "eval_run"
    ):
        run = row.eval_run
        if run is None:
            continue
        if run.status == EvalRun.Status.CANCELLED:
            if row.status != FinetuningJobEval.Status.CANCELLED:
                FinetuningJobEval.objects.filter(pk=row.pk).update(
                    status=FinetuningJobEval.Status.CANCELLED
                )
            continue
        if run.status == EvalRun.Status.FAILED:
            FinetuningJobEval.objects.filter(pk=row.pk).update(
                status=FinetuningJobEval.Status.FAILED,
                error_message=(run.error or "Eval run failed")[:2000],
            )
            continue
        if run.status != EvalRun.Status.COMPLETED:
            if (
                run.status == EvalRun.Status.RUNNING
                and row.status != FinetuningJobEval.Status.RUNNING
            ):
                FinetuningJobEval.objects.filter(pk=row.pk).update(
                    status=FinetuningJobEval.Status.RUNNING
                )
            continue

        # A run that "completed" but scored nothing is a failure, not a result: a green
        # row with no score hides provider errors from the monitor.
        summary = run.summary or {}
        score = _aggregate_from_summary(summary)
        ec = summary.get("error_counts") or {}
        all_errored = (
            score is None
            and (summary.get("completed_empty") or ec.get("error_rate") == 1.0)
            and (ec.get("total") or 0) > 0
        )
        if all_errored:
            FinetuningJobEval.objects.filter(pk=row.pk).update(
                status=FinetuningJobEval.Status.FAILED,
                error_message=(
                    f"All {ec.get('total')} samples errored during generation — "
                    "no gradable output (see eval run for per-sample errors)"
                )[:2000],
            )
            continue
        delta = None
        if (
            score is not None
            and baseline_score is not None
            and row.kind != FinetuningJobEval.Kind.BASELINE
        ):
            delta = round(score - baseline_score, 6)
        FinetuningJobEval.objects.filter(pk=row.pk).update(
            status=FinetuningJobEval.Status.COMPLETED,
            aggregate_score=score,
            baseline_delta=delta,
            class_metrics=row.class_metrics or _class_metrics_from_run(run),
            error_message="",
        )
        if row.kind == FinetuningJobEval.Kind.BASELINE and score is not None:
            baseline_score = score

    # Recompute deltas once baseline is known (baseline may finish after a ckpt).
    if baseline_score is not None:
        for row in FinetuningJobEval.objects.filter(job=job).exclude(
            kind=FinetuningJobEval.Kind.BASELINE
        ):
            if row.aggregate_score is None:
                continue
            delta = round(row.aggregate_score - baseline_score, 6)
            if row.baseline_delta != delta:
                FinetuningJobEval.objects.filter(pk=row.pk).update(baseline_delta=delta)

    rows = serialize_job_evals(job)
    # Mirror onto progress so list/detail payloads also see live scores.
    progress = dict(job.progress or {}) if isinstance(job.progress, dict) else {}
    progress["judge_evals"] = rows
    from overbae.models import FinetuningJob

    FinetuningJob.objects.filter(pk=job.pk).update(progress=progress)
    job.progress = progress
    return rows


def _class_metrics_from_run(run) -> dict[str, Any] | None:
    """Per-class classification metrics stamped at eval finalize, if any.

    ``aggregate_run`` appends ``{"class_metrics": …}`` to the dataset-scope Score's
    sub_scores for label-referenced datasets; non-label runs have none.
    """
    from overbae.models import Score

    for score in Score.objects.filter(run=run, scope="dataset").only("sub_scores"):
        for entry in score.sub_scores or []:
            if isinstance(entry, dict) and entry.get("class_metrics"):
                return entry["class_metrics"]
    return None


def _aggregate_from_summary(summary: dict[str, Any]) -> float | None:
    """The run headline: same number ``overall_aggregate`` reports, gate-only
    evaluators excluded. A summary that never built the ``metrics`` list still
    averages every named cell (tests and in-flight rows)."""
    from overbae.services.eval.comparison import overall_aggregate

    payload = dict(summary or {})
    if not payload.get("metrics"):
        names: set[str] = set()
        for variant in (payload.get("variants") or {}).values():
            names.update((variant or {}).get("metrics") or {})
        payload["metrics"] = sorted(names)
    mean = overall_aggregate(payload).mean
    return None if mean is None else round(mean, 6)


def cancel_related_evals(job) -> int:
    """Cancel in-flight EvalRuns linked to this fine-tune job.

    A baseline EvalRun shared across a wizard group keeps running while any sibling
    still needs it; only this job's FinetuningJobEval row is cancelled.
    """
    from overbae.models import EvalRun, FinetuningJob, FinetuningJobEval
    from overbae.tasks.eval import cancel_run

    revoked = 0
    qs = FinetuningJobEval.objects.filter(job=job, eval_run_id__isnull=False).select_related(
        "eval_run"
    )
    for row in qs:
        run = row.eval_run
        if run is None or run.is_terminal:
            if row.status not in (
                FinetuningJobEval.Status.COMPLETED,
                FinetuningJobEval.Status.FAILED,
                FinetuningJobEval.Status.CANCELLED,
                FinetuningJobEval.Status.SKIPPED,
            ):
                FinetuningJobEval.objects.filter(pk=row.pk).update(
                    status=FinetuningJobEval.Status.CANCELLED
                )
            continue
        # Shared group baseline: don't kill the run for siblings still training.
        if (
            FinetuningJobEval.objects.filter(eval_run_id=run.id)
            .exclude(job_id=job.id)
            .exclude(
                job__status__in=(
                    FinetuningJob.Status.CANCELLED,
                    FinetuningJob.Status.FAILED,
                )
            )
            .exists()
        ):
            FinetuningJobEval.objects.filter(pk=row.pk).update(
                status=FinetuningJobEval.Status.CANCELLED
            )
            continue
        try:
            revoked += cancel_run(run)
        except Exception:  # noqa: BLE001 — best-effort on FT cancel
            logger.warning(
                "Failed to cancel eval run %s for FT job %s", run.id, job.id, exc_info=True
            )
            EvalRun.objects.filter(pk=run.pk).update(status=EvalRun.Status.CANCELLED)
        FinetuningJobEval.objects.filter(pk=row.pk).update(
            status=FinetuningJobEval.Status.CANCELLED
        )
    return revoked


def tick_job_evals(job, *, checkpoints: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Idempotent: launch any missing evals + sync scores. Safe every poll."""
    if not job_wants_evals(job):
        return serialize_job_evals(job)

    try:
        ensure_baseline_eval(job)
    except Exception:  # noqa: BLE001 — never fail the training poll on eval launch
        logger.exception("Baseline eval launch failed for FT job %s", job.id)

    if checkpoints is not None:
        try:
            ensure_checkpoint_evals(job, checkpoints)
        except Exception:  # noqa: BLE001
            logger.exception("Checkpoint eval launch failed for FT job %s", job.id)

    if getattr(job, "output_model_name", ""):
        try:
            ensure_final_eval(job)
        except Exception:  # noqa: BLE001
            logger.exception("Final eval launch failed for FT job %s", job.id)

    try:
        result = sync_eval_scores(job)
    except Exception:  # noqa: BLE001
        logger.exception("Eval score sync failed for FT job %s", job.id)
        result = serialize_job_evals(job)
    # The only place FinetuningJobEval rows settle, so the graph projection belongs
    # here. Best-effort.

    return result


def _ready_deployment(job):
    """The job's READY Modal deployment (chat-callable vLLM), or None."""
    from overbae.models import DeployedModel

    return (
        DeployedModel.objects.filter(finetuning_job=job, status=DeployedModel.Status.READY)
        .exclude(inference_url="")
        .first()
    )


def _base_deployment(job):
    """READY Modal deployment of the job's UNTOUCHED base model, or None. Created by
    deploy_base_model_for_eval and shared across jobs with the same base.

    Context-length siblings share the slug prefix (``base--unsloth--qwen3-5-9b`` vs
    ``…-16k``). Prefer the longest ready context — a 4k leftover can be READY and
    still 500 while the 16k sibling is the one later jobs actually serve.
    """
    from django.db.models import Q

    from overbae.modal.model_registry import get_hf_base
    from overbae.models import DeployedModel
    from overbae.tasks.model_deployment import base_model_slug

    slug = base_model_slug(get_hf_base(job.base_model))
    return (
        DeployedModel.objects.filter(
            Q(model_id=slug) | Q(model_id__startswith=f"{slug}-"),
            status=DeployedModel.Status.READY,
        )
        .exclude(inference_url="")
        .order_by("-max_model_len")
        .first()
    )


@dataclass(frozen=True)
class _BaselineTarget:
    """What the baseline eval scores + how its ModelRef routes.

    ``kind``: ``"openrouter"`` (frontier incumbent), ``"gateway"`` (self-hosted
    incumbent on our infra) or ``"base_deploy"`` (untouched base on Modal, used only
    when the capability has no resolvable model). ``ready`` is False while the route is not
    callable yet — the tick loop retries until it is.
    """

    kind: str
    model_id: str
    provider: str
    base_url: str
    api_key_ref: str
    label: str
    ready: bool


def resolve_baseline_model(job) -> str:
    """The capability's production ('incumbent') model — the real before-comparison.

    Order: the submit-time snapshot, then ``Capability.active_model`` (what
    ``overmind/<capability-uuid>`` traffic actually hits), then the free-text ``Capability.model``.
    The FK outranks the text field because once routing lives on ``active_model`` the
    text goes stale, and a stale baseline benchmarks against a model nobody runs.
    """
    snap = (getattr(job, "baseline_model", "") or "").strip()
    if snap:
        return snap
    capability = getattr(job, "capability", None)
    if capability is None:
        return ""
    active = getattr(capability, "active_model", None)
    if active is not None:
        return (active.model_id or "").strip()
    return (getattr(capability, "model", "") or "").strip()


def _baseline_target(job) -> _BaselineTarget | None:
    from django.conf import settings

    from overbae.core.model_registry import PROVIDERS, resolve_openrouter_slug
    from overbae.models import DeployedModel, ModelRef

    gateway = (settings.INFERENCE_API_URL or "").rstrip("/")
    current = resolve_baseline_model(job)
    if current:
        # (a) Self-hosted incumbent already serving on our Modal infra → gateway.
        dep = (
            DeployedModel.objects.filter(
                project=job.project,
                model_id=current,
                status=DeployedModel.Status.READY,
            )
            .exclude(inference_url="")
            .first()
        )
        if dep is not None:
            return _BaselineTarget(
                kind="gateway",
                model_id=current,
                provider=ModelRef.Provider.CUSTOM,
                base_url=f"{gateway}/v1",
                api_key_ref="INFERENCE_API_KEY",
                label=f"Current model · {current}",
                ready=bool(gateway),
            )
        # (b) A set incumbent that is not one of our live deployments is an external
        # model the capability runs in production → OpenRouter, with no prefix/slug gate: a
        # set incumbent must never silently fall back to the base FT model, or the
        # "before" comparison scores the wrong thing.
        #
        # An incumbent that IS one of our deployments but is not callable yet would 404
        # at OpenRouter, so defer instead — ready=False means "not launchable" and
        # tick_job_evals retries idempotently.
        if DeployedModel.objects.filter(project=job.project, model_id=current).exists():
            return _BaselineTarget(
                kind="gateway",
                model_id=current,
                provider=ModelRef.Provider.CUSTOM,
                base_url=f"{gateway}/v1",
                api_key_ref="INFERENCE_API_KEY",
                label=f"Current model · {current}",
                ready=False,
            )
        return _BaselineTarget(
            kind="openrouter",
            model_id=resolve_openrouter_slug(current),
            provider=ModelRef.Provider.CUSTOM,
            base_url=PROVIDERS["openrouter"].base_url,
            api_key_ref=PROVIDERS["openrouter"].key_env,
            label=f"Current model · {current}",
            ready=True,
        )

    # (c) Fallback ONLY when the capability has no resolvable model at all: score the
    # untouched base model on Modal (deploy_base_model_for_eval).
    base = _base_deployment(job)
    return _BaselineTarget(
        kind="base_deploy",
        model_id=base.model_id if base else "",
        provider=ModelRef.Provider.CUSTOM,
        base_url=f"{gateway}/v1",
        api_key_ref="INFERENCE_API_KEY",
        label=f"Base model · {job.base_model}",
        ready=base is not None and bool(gateway),
    )


def baseline_needs_base_deploy(job) -> bool:
    """True only when the baseline falls back to Modal-deploying the base model —
    frontier and self-hosted incumbents route directly and need no GPU work.
    """
    if not _is_self_hosted(job):
        return False
    target = _baseline_target(job)
    return target is not None and target.kind == "base_deploy"


def _sibling_baseline_eval(job, *, model_id: str):
    """Reuse a group-mate's baseline EvalRun when it scores the same target.

    Wizard multi-model launches share ``group_id`` + eval set/dataset and one
    incumbent, so one EvalRun is enough. Different base_model fallbacks keep distinct
    model_ids and do not share.
    """
    from overbae.models import FinetuningJobEval

    gid = getattr(job, "group_id", None)
    if not gid or not model_id or not job.eval_dataset_id or not job.eval_set_id:
        return None
    return (
        FinetuningJobEval.objects.filter(
            job__group_id=gid,
            job__eval_dataset_id=job.eval_dataset_id,
            job__eval_set_id=job.eval_set_id,
            kind=FinetuningJobEval.Kind.BASELINE,
            model_id=model_id,
            eval_run_id__isnull=False,
        )
        .exclude(job_id=job.id)
        .exclude(
            status__in=(
                FinetuningJobEval.Status.FAILED,
                FinetuningJobEval.Status.CANCELLED,
                FinetuningJobEval.Status.SKIPPED,
            )
        )
        .select_related("eval_run")
        .order_by("created_at")
        .first()
    )


def _attach_shared_baseline(job, shared):
    """Point this job at a sibling's baseline EvalRun — no second enqueue."""
    from overbae.models import FinetuningJobEval

    row = FinetuningJobEval.objects.create(
        job=job,
        eval_run=shared.eval_run,
        kind=FinetuningJobEval.Kind.BASELINE,
        status=shared.status,
        model_id=shared.model_id,
        aggregate_score=shared.aggregate_score,
        class_metrics=shared.class_metrics,
        error_message=shared.error_message or "",
    )
    logger.info(
        "Reused group baseline EvalRun %s for FT job %s (from job %s)",
        shared.eval_run_id,
        job.id,
        shared.job_id,
    )
    return row


def ensure_baseline_eval(job):
    from overbae.models import FinetuningJobEval

    existing = FinetuningJobEval.objects.filter(
        job=job, kind=FinetuningJobEval.Kind.BASELINE
    ).first()
    if existing is not None:
        return existing

    if not _is_self_hosted(job):
        # Together serves the base model directly — score the incumbent later.
        model_id = job.base_model
        label = f"FT baseline · {job.name or job.base_model}"
    else:
        target = _baseline_target(job)
        if target is None or not target.ready or not target.model_id:
            return None
        model_id = target.model_id
        label = target.label

    # Multi-model wizard group: one baseline EvalRun per (group, target).
    shared = _sibling_baseline_eval(job, model_id=model_id)
    if shared is not None:
        return _attach_shared_baseline(job, shared)

    # Self-hosted/base incumbents wait for a READY route; frontier ones fire at once.
    target = _baseline_target(job)
    if target is None or not target.ready or not target.model_id:
        return None
    return _launch_eval(
        job,
        kind=FinetuningJobEval.Kind.BASELINE,
        model_id=model_id,
        label=label,
        checkpoint_id="",
        checkpoint_step=None,
    )


def ensure_final_eval(job):
    from overbae.models import FinetuningJobEval

    model_id = (job.output_model_name or "").strip()
    if not model_id:
        return None
    existing = FinetuningJobEval.objects.filter(job=job, kind=FinetuningJobEval.Kind.FINAL).first()
    if existing is not None:
        return existing
    if _is_self_hosted(job):
        # Weights are chat-callable only once the Modal deployment is READY; defer,
        # because register_finetuned_model re-ticks evals at READY.
        deployment = _ready_deployment(job)
        if deployment is None:
            return None
        model_id = deployment.model_id
    return _launch_eval(
        job,
        kind=FinetuningJobEval.Kind.FINAL,
        model_id=model_id,
        label=f"FT final · {job.name or model_id}",
        checkpoint_id="",
        checkpoint_step=None,
    )


def ensure_checkpoint_evals(job, checkpoints: list[dict[str, Any]]) -> list:
    from overbae.models import FinetuningJobEval

    launched = []
    for ckpt in checkpoints or []:
        if not is_checkpoint_inferable(job, ckpt):
            continue
        ckpt_id = str(ckpt.get("id") or ckpt.get("path") or "").strip()
        path = str(ckpt.get("path") or "").strip()
        if not ckpt_id or not path:
            continue
        step = ckpt.get("step")
        try:
            step_i = int(step) if step is not None else None
        except (TypeError, ValueError):
            step_i = None
        if FinetuningJobEval.objects.filter(
            job=job, kind=FinetuningJobEval.Kind.CHECKPOINT, checkpoint_id=ckpt_id
        ).exists():
            continue
        row = _launch_eval(
            job,
            kind=FinetuningJobEval.Kind.CHECKPOINT,
            model_id=path,
            label=f"FT ckpt step {step_i if step_i is not None else ckpt_id}",
            checkpoint_id=ckpt_id,
            checkpoint_step=step_i,
        )
        if row is not None:
            launched.append(row)
    return launched


def _provider_routing(job, *, kind: str) -> tuple[str, str, str]:
    """Return ``(ModelRef.provider, base_url, api_key_env)`` for one eval kind."""
    from overbae.models import FinetuningJobEval, ModelRef

    if _is_self_hosted(job):
        # Baseline follows the incumbent's own route; final always goes through the
        # gateway to the fine-tune's Modal deployment.
        if kind == FinetuningJobEval.Kind.BASELINE:
            target = _baseline_target(job)
            if target is None or not target.ready:
                raise RuntimeError(
                    f"{job.provider} job {job.id} baseline has no ready route — eval must wait for it"
                )
            return target.provider, target.base_url, target.api_key_ref

        deployment = _ready_deployment(job)
        if deployment is None:
            raise RuntimeError(
                f"{job.provider} job {job.id} has no READY {kind} deployment — eval must wait for it"
            )
        from django.conf import settings

        # The gateway's OpenAI-compatible /v1 base, never ``deployment.inference_url``:
        # that is a worker URL carrying a query string, so appending
        # /chat/completions to it yields "max_model_len=16384/chat/completions".
        gateway = (settings.INFERENCE_API_URL or "").rstrip("/")
        if not gateway:
            raise RuntimeError(
                "INFERENCE_API_URL is not configured — cannot eval Modal deployments"
            )
        return ModelRef.Provider.CUSTOM, f"{gateway}/v1", "INFERENCE_API_KEY"
    return ModelRef.Provider.TOGETHER, "", "TOGETHER_API_KEY"


def _get_or_create_model_ref(job, *, model_id: str, label: str, kind: str):
    from overbae.models import ModelRef

    provider, base_url, api_key_env = _provider_routing(job, kind=kind)
    # Modal deployments run a right-sized max_model_len and vLLM 400s any request whose
    # max_tokens exceeds it, so ``None`` makes call_llm omit the param and let the
    # server size output to the remaining context.
    params = {"max_tokens": None} if api_key_env == "INFERENCE_API_KEY" else {}
    existing = ModelRef.objects.filter(
        project=job.project,
        model_id=model_id,
        provider=provider,
        finetuning_job=job,
    ).first()
    if existing:
        # Self-heal refs holding a stale endpoint so relaunched evals hit the gateway.
        if (
            existing.base_url != base_url
            or existing.api_key_ref != api_key_env
            or (existing.params or {}) != params
        ):
            ModelRef.objects.filter(pk=existing.pk).update(
                base_url=base_url, api_key_ref=api_key_env, params=params
            )
            existing.refresh_from_db()
        return existing
    return ModelRef.objects.create(
        project=job.project,
        label=label[:255],
        provider=provider,
        model_id=model_id,
        base_url=base_url,
        api_key_ref=api_key_env,
        finetuning_job=job,
        params=params,
    )


def _copy_baseline_snapshots(job, run) -> int:
    """Checkpoint/final runs grade with the exact snapshots the baseline used —
    a rubric refreshed mid-training must not move the bar between before/after."""
    from overbae.models import FinetuningJobEval, RunEvaluator

    baseline = (
        FinetuningJobEval.objects.filter(
            job=job, kind=FinetuningJobEval.Kind.BASELINE, eval_run_id__isnull=False
        )
        .order_by("created_at")
        .first()
    )
    if baseline is None:
        return 0
    by_evaluator = {
        r.evaluator_id: r.snapshot for r in RunEvaluator.objects.filter(run_id=baseline.eval_run_id)
    }
    copied = 0
    for row in run.run_evaluators.all():
        snap = by_evaluator.get(row.evaluator_id)
        if snap and snap != row.snapshot:
            RunEvaluator.objects.filter(pk=row.pk).update(snapshot=snap)
            copied += 1
    return copied


def _launch_eval(
    job,
    *,
    kind: str,
    model_id: str,
    label: str,
    checkpoint_id: str,
    checkpoint_step: int | None,
):
    from overbae.models import EvalRun, EvalVariant, FinetuningJobEval
    from overbae.services.eval.eval_set import expand_to_run_evaluators
    from overbae.tasks.eval import run_eval_run

    if not model_id or not job.eval_dataset_id or not job.eval_set_id:
        return None

    with transaction.atomic():
        # Re-check uniqueness inside the transaction.
        if kind == FinetuningJobEval.Kind.BASELINE:
            if FinetuningJobEval.objects.filter(
                job=job, kind=FinetuningJobEval.Kind.BASELINE
            ).exists():
                return FinetuningJobEval.objects.filter(
                    job=job, kind=FinetuningJobEval.Kind.BASELINE
                ).first()
            # Serialize group baseline creation so concurrent sibling ticks
            # attach to one EvalRun instead of racing three identical ones.
            gid = getattr(job, "group_id", None)
            if gid:
                from overbae.models import FinetuningJob

                list(
                    FinetuningJob.objects.select_for_update()
                    .filter(group_id=gid)
                    .order_by("id")
                    .only("id")
                )
                shared = _sibling_baseline_eval(job, model_id=model_id)
                if shared is not None:
                    return _attach_shared_baseline(job, shared)
        elif kind == FinetuningJobEval.Kind.FINAL:
            if FinetuningJobEval.objects.filter(
                job=job, kind=FinetuningJobEval.Kind.FINAL
            ).exists():
                return FinetuningJobEval.objects.filter(
                    job=job, kind=FinetuningJobEval.Kind.FINAL
                ).first()
        elif FinetuningJobEval.objects.filter(
            job=job, kind=FinetuningJobEval.Kind.CHECKPOINT, checkpoint_id=checkpoint_id
        ).exists():
            return FinetuningJobEval.objects.filter(
                job=job, kind=FinetuningJobEval.Kind.CHECKPOINT, checkpoint_id=checkpoint_id
            ).first()

        from overbae.services.datasets import use as dataset_use

        # Same pin the eval-run API applies: generate scoring reads ``run.cell``.
        cell = dataset_use.use(job.eval_dataset, "eval", cell=job.eval_cell)
        ref = _get_or_create_model_ref(job, model_id=model_id, label=label, kind=kind)
        run = EvalRun.objects.create(
            project=job.project,
            name=label[:255],
            description=f"Auto in-training eval ({kind}) for FinetuningJob {job.id}",
            data_source=EvalRun.DataSource.DATASET,
            dataset_id=job.eval_dataset_id,
            cell=cell,
            eval_set_id=job.eval_set_id,
            max_items=eval_max_items(job),
            triggered_by=job.triggered_by,
            status=EvalRun.Status.PENDING,
        )
        expand_to_run_evaluators(run, job.eval_set)
        if kind != FinetuningJobEval.Kind.BASELINE:
            _copy_baseline_snapshots(job, run)
        if not run.run_evaluators.exists():
            run.delete()
            row = FinetuningJobEval.objects.create(
                job=job,
                kind=kind,
                status=FinetuningJobEval.Status.FAILED,
                checkpoint_id=checkpoint_id,
                checkpoint_step=checkpoint_step,
                model_id=model_id,
                error_message="Eval set has no runnable evaluators",
            )
            return row

        EvalVariant.objects.create(
            run=run,
            label=label[:255],
            model_ref=ref,
            mode=EvalVariant.Mode.GENERATE,
            is_baseline=kind == FinetuningJobEval.Kind.BASELINE,
            order=0,
        )
        row = FinetuningJobEval.objects.create(
            job=job,
            eval_run=run,
            kind=kind,
            status=FinetuningJobEval.Status.PENDING,
            checkpoint_id=checkpoint_id,
            checkpoint_step=checkpoint_step,
            model_id=model_id,
        )

    def _enqueue() -> None:
        result = run_eval_run.apply_async(kwargs={"eval_run_id": str(run.id)})
        EvalRun.objects.filter(pk=run.pk).update(celery_task_id=result.id)
        FinetuningJobEval.objects.filter(pk=row.pk).update(status=FinetuningJobEval.Status.RUNNING)
        logger.info(
            "Launched FT %s eval for job %s → EvalRun %s (model=%s, max_items=%s)",
            kind,
            job.id,
            run.id,
            model_id,
            eval_max_items(job),
        )

    # Enqueue only after the EvalRun row is COMMITTED: a caller holding an open
    # transaction lets the worker pick up run_eval_run before the row is visible, and
    # it exits "not found", orphaning the eval. Runs immediately outside atomic.
    transaction.on_commit(_enqueue)
    row.status = FinetuningJobEval.Status.RUNNING
    return row
