"""Selected baseline/final evaluations through the normal EvalRun pipeline.

The incumbent is snapshotted at submission; the training model is its untouched
base for the baseline and its served checkpoint afterward. Baseline evaluations
launch alongside training. All evaluations reuse the first run's data cell and
evaluator snapshots. Grouped jobs share identical incumbent baseline runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from overbae.core.model_registry import PROVIDERS
from overbae.models import FinetuningJob
from overbae.services.eval.context import model_context
from overbae.services.model_catalog import resolve_training_openrouter_slug
from overbae.services.serving_context import evaluation_budget

logger = logging.getLogger(__name__)


def _is_self_hosted(job) -> bool:
    """Baseten and Modal ship weight-only artifacts with no provider chat endpoint, so
    their evals route through OUR Modal deployment. Together serves its own fine-tunes.
    """
    from overbae.models import FinetuningJob

    return job.provider in (FinetuningJob.Provider.BASETEN, FinetuningJob.Provider.MODAL)


def job_wants_evals(job) -> bool:
    return bool(
        job.eval_dataset_id
        and job.eval_set_id
        and any(
            (
                job.eval_incumbent_before,
                job.eval_incumbent_after,
                job.eval_model_before,
                job.eval_model_after,
            )
        )
    )


def _metric_scores_for_run(eval_run_id) -> list[dict[str, Any]]:
    """Per-metric mean judge scores (0–1) for one EvalRun.

    The same means ``summary.variants[..].metrics`` reports, but computed from the
    persisted Score rows so it also works while the run is still in flight.
    """
    from django.db.models import Avg

    from overbae.models import Score

    return [
        {"name": r["name"], "score": round(float(r["avg"]), 6)}
        for r in Score.objects.filter(run_id=eval_run_id, outcome=Score.Outcome.SCORED)
        .exclude(sample__degraded=True)
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
    comparison = comparison_eval(job)
    labels = {
        "baseline": "Incumbent · before" if resolve_baseline_model(job) else "Base model · before",
        "model_before": "Base model · before",
        "incumbent_after": "Incumbent · after",
        "final": "Trained model · after",
    }
    for row in FinetuningJobEval.objects.filter(job=job).order_by("checkpoint_step", "created_at"):
        rows.append(
            {
                "id": str(row.id),
                "kind": row.kind,
                "label": labels.get(row.kind, f"Checkpoint {row.checkpoint_step or ''}".strip()),
                "comparison_label": labels.get(comparison.kind)
                if comparison is not None and comparison.pk != row.pk
                else None,
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

    baseline = comparison_eval(job)
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
        degraded = int((summary.get("trust") or {}).get("degraded") or 0)
        if degraded:
            FinetuningJobEval.objects.filter(pk=row.pk).update(
                status=FinetuningJobEval.Status.FAILED,
                aggregate_score=None,
                baseline_delta=None,
                error_message=f"{degraded} evaluation samples have incomplete or degraded evidence. See the evaluation run before comparing model quality.",
            )
            if baseline is not None and row.pk == baseline.pk:
                baseline_score = None
            continue
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
        if score is not None and baseline_score is not None and row.pk != baseline.pk:
            delta = round(score - baseline_score, 6)
        FinetuningJobEval.objects.filter(pk=row.pk).update(
            status=FinetuningJobEval.Status.COMPLETED,
            aggregate_score=score,
            baseline_delta=delta,
            class_metrics=row.class_metrics or _class_metrics_from_run(run),
            error_message="",
        )
        if baseline is not None and row.pk == baseline.pk and score is not None:
            baseline_score = score

    # Recompute deltas once baseline is known (baseline may finish after a ckpt).
    if baseline_score is not None:
        for row in FinetuningJobEval.objects.filter(job=job).exclude(pk=baseline.pk):
            if row.aggregate_score is None:
                continue
            delta = round(row.aggregate_score - baseline_score, 6)
            if row.baseline_delta != delta:
                FinetuningJobEval.objects.filter(pk=row.pk).update(baseline_delta=delta)
    else:
        FinetuningJobEval.objects.filter(job=job).update(baseline_delta=None)

    rows = serialize_job_evals(job)
    # Deployment observations can update progress while eval scores are being read.
    with transaction.atomic():
        current = FinetuningJob.objects.select_for_update().get(pk=job.pk)
        progress = dict(current.progress or {})
        progress["judge_evals"] = rows
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


def comparison_eval(job):
    from overbae.models import FinetuningJobEval

    for kind in (
        FinetuningJobEval.Kind.MODEL_BEFORE,
        FinetuningJobEval.Kind.BASELINE,
        FinetuningJobEval.Kind.INCUMBENT_AFTER,
    ):
        row = FinetuningJobEval.objects.filter(job=job, kind=kind).first()
        if row is not None:
            return row
    return None


def start_before_evals(job) -> None:
    """Launch selected baseline evaluations without gating training submission."""
    from overbae.models import FinetuningJob
    from overbae.tasks.model_deployment import deploy_base_model_for_eval

    if not job_wants_evals(job) or not (job.eval_incumbent_before or job.eval_model_before):
        return
    progress = dict(job.progress or {})
    first_launch = progress.get("before_evals_started_at") is None
    if first_launch:
        progress["before_evals_started_at"] = timezone.now().timestamp()
        FinetuningJob.objects.filter(pk=job.pk).update(progress=progress)
        job.progress = progress
    try:
        if first_launch and baseline_needs_base_deploy(job):
            deploy_base_model_for_eval.delay(job_id=str(job.id))
    except Exception:  # noqa: BLE001 — evaluation setup must not block GPU training
        logger.exception("Baseline deployment launch failed for FT job %s", job.id)
    tick_job_evals(job)


def reset_before_evals_for_retry(job) -> None:
    from overbae.models import FinetuningJob, FinetuningJobEval
    from overbae.services.deployment import retry_baseline_deployments

    if job.remote_job_id:
        return
    retry_baseline_deployments(job)
    # Keep EvalRuns and their results; only detach unsuccessful attempt links.
    job.job_evals.filter(
        kind__in=(FinetuningJobEval.Kind.BASELINE, FinetuningJobEval.Kind.MODEL_BEFORE),
        status__in=(
            FinetuningJobEval.Status.FAILED,
            FinetuningJobEval.Status.CANCELLED,
            FinetuningJobEval.Status.SKIPPED,
        ),
    ).delete()
    progress = dict(job.progress or {})
    progress.pop("before_evals_started_at", None)
    FinetuningJob.objects.filter(pk=job.pk).update(progress=progress)
    job.progress = progress


def tick_job_evals(job) -> list[dict[str, Any]]:
    """Idempotent: launch any missing evals + sync scores. Safe every poll."""
    if not job_wants_evals(job):
        return serialize_job_evals(job)

    from overbae.models import FinetuningJob, FinetuningJobEval

    if job.status in (FinetuningJob.Status.FAILED, FinetuningJob.Status.CANCELLED):
        return serialize_job_evals(job)
    after_training = job.status in (FinetuningJob.Status.DEPLOYING, FinetuningJob.Status.SUCCEEDED)
    try:
        ensure_baseline_eval(job)
        if job.eval_model_before:
            ensure_target_eval(job, kind=FinetuningJobEval.Kind.MODEL_BEFORE)
    except Exception:  # noqa: BLE001
        logger.exception("Baseline eval launch failed for FT job %s", job.id)
    if after_training:
        try:
            if job.eval_incumbent_after:
                ensure_target_eval(job, kind=FinetuningJobEval.Kind.INCUMBENT_AFTER)
            ensure_final_eval(job)
        except Exception:  # noqa: BLE001
            logger.exception("After-training eval launch failed for FT job %s", job.id)

    try:
        result = sync_eval_scores(job)
    except Exception:  # noqa: BLE001
        logger.exception("Eval score sync failed for FT job %s", job.id)
        result = serialize_job_evals(job)
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
    from overbae.services.deployment import base_model_slug

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
    """Submitted jobs retain their benchmark even when capability selections change."""
    snap = (getattr(job, "baseline_model", "") or "").strip()
    if snap:
        return snap
    capability = getattr(job, "capability", None)
    if capability is None:
        return ""
    benchmark = getattr(capability, "benchmark_model", None)
    if benchmark is not None:
        return (benchmark.model_id or "").strip()
    return (getattr(capability, "model", "") or "").strip()


def _baseline_target(job) -> _BaselineTarget | None:
    from django.conf import settings

    from overbae.core.model_registry import PROVIDERS, resolve_openrouter_slug
    from overbae.models import DeployedModel, ModelRef

    gateway = (settings.INFERENCE_API_URL or "").rstrip("/")
    current = resolve_baseline_model(job)
    if current:
        dep = DeployedModel.objects.filter(project=job.project, model_id=current).first()
        if dep is not None:
            # A warming local deployment must wait, not fall back to OpenRouter.
            return _BaselineTarget(
                kind="gateway",
                model_id=current,
                provider=ModelRef.Provider.CUSTOM,
                base_url=f"{gateway}/v1",
                api_key_ref="INFERENCE_API_KEY",
                label=f"Current model · {current}",
                ready=bool(
                    dep.status == DeployedModel.Status.READY and dep.inference_url and gateway
                ),
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

    return _base_target(job)


def _base_target(job) -> _BaselineTarget:
    from django.conf import settings

    from overbae.models import ModelRef

    # Once an evaluation starts, catalog refreshes must not change its route or
    # provision a second copy of the same starting model.
    kinds = ["model_before"] if resolve_baseline_model(job) else ["model_before", "baseline"]
    existing = (
        job.job_evals.filter(kind__in=kinds, eval_run__isnull=False)
        .select_related("eval_run")
        .order_by("created_at")
        .first()
    )
    if existing:
        variant = existing.eval_run.variants.select_related("model_ref").first()
        if variant and variant.model_ref:
            ref = variant.model_ref
            return _BaselineTarget(
                "base_deploy" if ref.api_key_ref == "INFERENCE_API_KEY" else "provider",
                ref.model_id,
                ref.provider,
                ref.base_url,
                ref.api_key_ref,
                f"Base model · {job.base_model}",
                True,
            )
    # An attached deployment is already preparing this model. Keep that attempt
    # until an explicit retry, even if the provider catalog changes meanwhile.
    slug = (
        None
        if job.evaluation_deployments.exists()
        else resolve_training_openrouter_slug(job.base_model)
    )
    if slug:
        return _BaselineTarget(
            "openrouter",
            slug,
            ModelRef.Provider.CUSTOM,
            PROVIDERS["openrouter"].base_url,
            PROVIDERS["openrouter"].key_env,
            f"Base model · {job.base_model}",
            True,
        )
    if not _is_self_hosted(job):
        return _BaselineTarget(
            "provider",
            job.base_model,
            ModelRef.Provider.TOGETHER,
            "",
            "TOGETHER_API_KEY",
            f"Base model · {job.base_model}",
            True,
        )
    gateway = (settings.INFERENCE_API_URL or "").rstrip("/")
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
    if not job_wants_evals(job) or not _is_self_hosted(job):
        return False
    if job.eval_model_before and _base_target(job).kind == "base_deploy":
        return True
    if not job.eval_incumbent_before:
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
            eval_run__max_items=0,
            eval_run__sampling=1.0,
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

    if not job.eval_incumbent_before:
        return None
    existing = FinetuningJobEval.objects.filter(
        job=job, kind=FinetuningJobEval.Kind.BASELINE
    ).first()
    if existing is not None:
        return existing

    target = _baseline_target(job)
    if target is None or not target.ready or not target.model_id:
        return None
    model_id = target.model_id
    label = f"Incumbent before · {model_id}"

    return _launch_eval(
        job,
        kind=FinetuningJobEval.Kind.BASELINE,
        model_id=model_id,
        label=label,
        checkpoint_id="",
        checkpoint_step=None,
        target=target,
    )


def ensure_final_eval(job):
    from overbae.models import FinetuningJobEval

    if not job.eval_model_after:
        return None
    model_id = (job.output_model_name or "").strip()
    if not model_id:
        return None
    existing = FinetuningJobEval.objects.filter(job=job, kind=FinetuningJobEval.Kind.FINAL).first()
    if existing is not None:
        return existing
    if _is_self_hosted(job):
        # Weights are chat-callable only once the Modal deployment is READY; defer,
        # because the deployment controller re-ticks evals at READY.
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


def ensure_target_eval(job, *, kind: str):
    from overbae.models import FinetuningJobEval

    existing = FinetuningJobEval.objects.filter(job=job, kind=kind).first()
    if existing is not None:
        return existing
    target = (
        _base_target(job) if kind == FinetuningJobEval.Kind.MODEL_BEFORE else _baseline_target(job)
    )
    if target is None or not target.ready or not target.model_id:
        return None
    label = (
        "Base model before" if kind == FinetuningJobEval.Kind.MODEL_BEFORE else "Incumbent after"
    )
    return _launch_eval(
        job,
        kind=kind,
        model_id=target.model_id,
        label=f"{label} · {target.model_id}",
        checkpoint_id="",
        checkpoint_step=None,
        target=target,
    )


def _provider_routing(job, *, kind: str, target: _BaselineTarget | None) -> tuple[str, str, str]:
    """Return ``(ModelRef.provider, base_url, api_key_env)`` for one eval kind."""
    from overbae.models import FinetuningJobEval, ModelRef

    if kind in (
        FinetuningJobEval.Kind.BASELINE,
        FinetuningJobEval.Kind.INCUMBENT_AFTER,
        FinetuningJobEval.Kind.MODEL_BEFORE,
    ):
        if target is None or not target.ready:
            raise RuntimeError("The evaluation model has no ready route.")
        return target.provider, target.base_url, target.api_key_ref
    if _is_self_hosted(job):
        # Baseline follows the incumbent's own route; final always goes through the
        # gateway to the fine-tune's Modal deployment.
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


def _get_or_create_model_ref(
    job, *, model_id: str, label: str, kind: str, target: _BaselineTarget | None
):
    from overbae.models import ModelRef

    provider, base_url, api_key_env = _provider_routing(job, kind=kind, target=target)
    params = {
        "max_tokens": evaluation_budget(job.eval_cell, capability=job.capability).output_tokens
    }
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
        FinetuningJobEval.objects.filter(job=job, eval_run_id__isnull=False)
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
    target: _BaselineTarget | None = None,
):
    from overbae.models import EvalRun, EvalVariant, FinetuningJobEval
    from overbae.services.eval.eval_set import expand_to_run_evaluators
    from overbae.tasks.eval import run_eval_run

    if not model_id or not job.eval_dataset_id or not job.eval_set_id:
        return None

    with transaction.atomic():
        from overbae.models import FinetuningJob

        # Lock the entire group in one order: before-eval sharing and per-job
        # uniqueness must not race between deployment callbacks and poll ticks.
        jobs = (
            FinetuningJob.objects.filter(group_id=job.group_id)
            if job.group_id
            else FinetuningJob.objects.filter(pk=job.pk)
        )
        list(jobs.select_for_update().order_by("id").values_list("id", flat=True))
        existing = FinetuningJobEval.objects.filter(job=job, kind=kind)
        if kind == FinetuningJobEval.Kind.CHECKPOINT:
            existing = existing.filter(checkpoint_id=checkpoint_id)
        row = existing.first()
        if row is not None:
            return row
        if kind == FinetuningJobEval.Kind.BASELINE:
            shared = _sibling_baseline_eval(job, model_id=model_id)
            if shared is not None:
                return _attach_shared_baseline(job, shared)

        from overbae.services.datasets import use as dataset_use

        # Same pin the eval-run API applies: generate scoring reads ``run.cell``.
        previous = (
            FinetuningJobEval.objects.filter(job=job, eval_run__cell__isnull=False)
            .select_related("eval_run")
            .order_by("created_at")
            .first()
        )
        cell = (
            previous.eval_run.cell
            if previous
            else dataset_use.use(job.eval_dataset, "eval", cell=job.eval_cell)
        )
        ref = _get_or_create_model_ref(
            job, model_id=model_id, label=label, kind=kind, target=target
        )
        run = EvalRun.objects.create(
            project=job.project,
            name=label[:255],
            description=f"Auto in-training eval ({kind}) for FinetuningJob {job.id}",
            data_source=EvalRun.DataSource.DATASET,
            dataset_id=job.eval_dataset_id,
            cell=cell,
            eval_set_id=job.eval_set_id,
            max_items=0,
            sampling=1.0,
            triggered_by=job.triggered_by,
            status=EvalRun.Status.PENDING,
        )
        expand_to_run_evaluators(run, job.eval_set)
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

        previous_variant = (
            previous.eval_run.variants.order_by("order").first() if previous else None
        )
        context = model_context(run, capability=job.capability)
        if previous_variant and "system_prompt" in (previous_variant.params or {}):
            context = {
                key: previous_variant.params[key]
                for key in ("system_prompt", "execution_mode")
                if key in previous_variant.params
            }
        EvalVariant.objects.create(
            run=run,
            label=label[:255],
            model_ref=ref,
            mode=EvalVariant.Mode.GENERATE,
            is_baseline=kind
            in (FinetuningJobEval.Kind.BASELINE, FinetuningJobEval.Kind.MODEL_BEFORE),
            params=context,
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
            run.max_items,
        )

    # Enqueue only after the EvalRun row is COMMITTED: a caller holding an open
    # transaction lets the worker pick up run_eval_run before the row is visible, and
    # it exits "not found", orphaning the eval. Runs immediately outside atomic.
    transaction.on_commit(_enqueue)
    row.status = FinetuningJobEval.Status.RUNNING
    return row
