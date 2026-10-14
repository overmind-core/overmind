"""Decision models: train on a dataset version, evaluate, and answer System One requests.

Training and evaluation run on the ``overmind-decision`` Modal app. Requests never
wait on them: ``start`` and ``advance`` run in Celery, and the beat reconciler keeps
every active job moving.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import Counter
from decimal import Decimal
from typing import Any

import modal
from django.db import transaction
from django.utils import timezone

from overbae.core.errors import InputValidationError
from overbae.modal.decision_catalog import get as catalog_model
from overbae.modal.decision_catalog import models as catalog_models
from overbae.models import BillingService, Cell, Dataset, DecisionJob, Project
from overbae.services.billing_ledger import charge_credits
from overbae.services.datasets import decisions, rows, use, versions
from overbae.services.eval.evaluators.statistical import per_class_metrics
from overbae.services.inference_pricing import gpu_usd_per_second

logger = logging.getLogger(__name__)

APP_NAME = "overmind-decision"
GPU = "H100"
_SERVERS = {"clef": "ClefServer", "d3": "D3Server", "d1": "D1Server"}
# A cold server boots for minutes; a request answers 503 and the started call warms it.
WAIT_SECONDS = 20.0
RETRY_AFTER_SECONDS = 60
# A request holds the serving GPU for its duration; bill at least one second of it.
MIN_BILLED_SECONDS = 1.0
# The 503 body names one of these; any other worker code is reported as runtime_error.
UNAVAILABLE_REASONS = frozenset({"boot_failed", "adapter_missing", "runtime_error"})
_UNEXPECTED_TRAINING = "Training stopped unexpectedly."
_TRAINING_COPY = {
    "no_records_fit": (
        "No rows fit {model}: {too_long} too long for {max_context_length} tokens, "
        "{rejected} rejected by the model."
    ),
    "base_unavailable": "Training stopped: the base model could not be downloaded.",
    "runtime_error": _UNEXPECTED_TRAINING,
}


class DecisionWarmingError(Exception):
    pass


class DecisionUnavailableError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _modal_env() -> str | None:
    return os.environ.get("MODAL_ENVIRONMENT") or None


def _function(name: str):
    return modal.Function.from_name(APP_NAME, name, environment_name=_modal_env())


def model(model_id: str) -> dict:
    spec = catalog_model(model_id)
    if spec is None:
        raise InputValidationError(
            f"Unknown decision model {model_id}. Models: {', '.join(catalog_models())}"
        )
    return spec


def create(
    project: Project,
    user,
    model_id: str,
    dataset: Dataset,
    version: str | None = None,
    eval_dataset: Dataset | None = None,
    eval_version: str | None = None,
) -> DecisionJob:
    """Freeze the training (and evaluation) version and queue the job.
    Raises ``InputValidationError`` or the dataset's ``DatasetError``."""
    from overbae.tasks.decision_jobs import start

    spec = model(model_id)
    if dataset.project_id != project.id or (
        eval_dataset is not None and eval_dataset.project_id != project.id
    ):
        raise InputValidationError("The datasets must belong to this project.")
    with transaction.atomic():
        cell = use.use(
            dataset, "train", cell=versions.resolve_cell(dataset, version), decision=True
        )
        eval_cell = None
        if eval_dataset is not None:
            eval_cell = use.use(
                eval_dataset,
                "eval",
                cell=versions.resolve_cell(eval_dataset, eval_version, field="eval_version"),
                decision=True,
            )
        _refuse_over_limits(spec, cell)
        if eval_cell is not None:
            _refuse_over_limits(spec, eval_cell)
        job = DecisionJob.objects.create(
            project=project,
            created_by=user if getattr(user, "is_authenticated", False) else None,
            model_id=spec["id"],
            cell=cell,
            eval_cell=eval_cell,
        )
        transaction.on_commit(lambda: start.delay(str(job.id)))
    return job


def _refuse_over_limits(spec: dict, cell: Cell) -> None:
    """Refuse a version with rows the model's serving limits cannot answer."""
    over = []
    for index, row in enumerate(rows.iter_rows(cell), start=1):
        questions = decisions.request(row.input)["questions"]
        problems = decisions.violations(questions, spec["limits"])
        if problems:
            over.append((index, problems[0]))
    if over:
        examples = "; ".join(f"row {index}: {problem}" for index, problem in over[:3])
        raise InputValidationError(f"{len(over)} rows exceed {spec['id']}'s limits; {examples}")


def _records(cell: Cell) -> str:
    return "".join(
        json.dumps(decisions.record(row.input, row.expected_output)) + "\n"
        for row in rows.iter_rows(cell)
    )


def _fail(job_id, expected: str, error: str) -> None:
    DecisionJob.objects.filter(pk=job_id, status=expected).update(
        status=DecisionJob.Status.FAILED, error=error, completed_at=timezone.now()
    )


def start(job_id) -> None:
    job = (
        DecisionJob.objects.select_related("cell", "eval_cell")
        .filter(pk=job_id, status=DecisionJob.Status.QUEUED)
        .first()
    )
    if job is None:
        return
    spec = model(job.model_id)
    try:
        _function("upload_records").remote(
            str(job.id),
            _records(job.cell),
            _records(job.eval_cell) if job.eval_cell_id else None,
        )
        call = _function("train_d1" if spec["family"] == "d1" else "train_decision").spawn(
            {
                "job_id": str(job.id),
                "model": spec["id"],
                "repo": spec["repo"],
                "family": spec["family"],
                "max_context_length": spec["max_context_length"],
            }
        )
    except Exception:
        logger.exception("Could not start decision job %s", job.id)
        _fail(job.id, DecisionJob.Status.QUEUED, "Could not start decision training.")
        return
    DecisionJob.objects.filter(pk=job.pk, status=DecisionJob.Status.QUEUED).update(
        status=DecisionJob.Status.RUNNING, remote_id=call.object_id, started_at=timezone.now()
    )


def _poll(job: DecisionJob) -> tuple[bool, Any]:
    """``(done, result)``; a lost or crashed call is done with ``None``."""
    try:
        return True, modal.FunctionCall.from_id(job.remote_id).get(timeout=0)
    except TimeoutError:
        return False, None
    except Exception:
        logger.exception("Decision job %s call %s failed", job.id, job.remote_id)
        return True, None


def advance(job_id) -> None:
    job = DecisionJob.objects.filter(pk=job_id, status__in=DecisionJob.ACTIVE).first()
    if job is None or not job.remote_id:
        return
    done, result = _poll(job)
    if not done:
        return
    if job.status == DecisionJob.Status.RUNNING:
        _trained(job, result)
    elif job.status == DecisionJob.Status.EVALUATING:
        _evaluated(job, result)
    finished = DecisionJob.objects.get(pk=job.pk)
    if finished.status not in DecisionJob.ACTIVE:
        charge(finished)


def _trained(job: DecisionJob, result: Any) -> None:
    if not isinstance(result, dict) or result.get("error") or not result.get("saved"):
        _fail(job.id, DecisionJob.Status.RUNNING, _training_failure(job, result))
        return
    if job.eval_cell_id is None:
        DecisionJob.objects.filter(pk=job.pk, status=DecisionJob.Status.RUNNING).update(
            status=DecisionJob.Status.SUCCEEDED, result=result, completed_at=timezone.now()
        )
        return
    spec = model(job.model_id)
    try:
        call = _function("evaluate_decision").spawn(
            {
                "job_id": str(job.id),
                "family": spec["family"],
                "repo": spec["repo"],
                "checkpoint": result["saved"],
                "max_context_length": spec["max_context_length"],
            }
        )
    except Exception:
        logger.exception("Could not start the evaluation of decision job %s", job.id)
        DecisionJob.objects.filter(pk=job.pk, status=DecisionJob.Status.RUNNING).update(
            status=DecisionJob.Status.SUCCEEDED,
            result=result,
            evaluation={"status": "failed", "error": "The evaluation could not start."},
            completed_at=timezone.now(),
        )
        return
    DecisionJob.objects.filter(pk=job.pk, status=DecisionJob.Status.RUNNING).update(
        status=DecisionJob.Status.EVALUATING, result=result, remote_id=call.object_id
    )


def _training_failure(job: DecisionJob, result: Any) -> str:
    """Log the worker's message and return the copy shown on the job."""
    error = result.get("error") if isinstance(result, dict) else None
    if not isinstance(error, dict):
        logger.error("Decision job %s training returned no result", job.id)
        return _UNEXPECTED_TRAINING
    code = str(error.get("code") or "unknown")
    logger.error("Decision job %s training failed: %s: %s", job.id, code, error.get("message"))
    copy = _TRAINING_COPY.get(code, _UNEXPECTED_TRAINING)
    skipped = error.get("skipped") or {}
    return copy.format(
        model=job.model_id,
        max_context_length=(catalog_model(job.model_id) or {}).get("max_context_length", "its"),
        too_long=skipped.get("too_long", 0),
        rejected=skipped.get("rejected", 0),
    )


def _evaluated(job: DecisionJob, result: Any) -> None:
    try:
        evaluation = evaluate(job.eval_cell, result)
    except (decisions.DecisionRowError, KeyError, TypeError, ValueError):
        logger.exception("Decision job %s returned an unusable evaluation", job.id)
        evaluation = {"status": "failed", "error": "The evaluation failed."}
    DecisionJob.objects.filter(pk=job.pk, status=DecisionJob.Status.EVALUATING).update(
        status=DecisionJob.Status.SUCCEEDED, evaluation=evaluation, completed_at=timezone.now()
    )


def evaluate(cell: Cell, result: Any) -> dict:
    """Score the base and the trained model's answers against the version's answers.
    ``result`` is ``{"base": [answers per row], "trained": [...]}`` in row order."""
    if not isinstance(result, dict):
        raise ValueError("no evaluation result")
    gold = [decisions.record(row.input, row.expected_output) for row in rows.iter_rows(cell)]
    base = _metrics(gold, result["base"])
    trained = _metrics(gold, result["trained"])
    return {
        "status": _evaluation_status(base, trained),
        "rows": len(gold),
        "base": base,
        "trained": trained,
    }


def _evaluation_status(base: dict, trained: dict) -> str:
    if base["n"] == 0 or trained["n"] == 0:
        return "failed"
    if base["unanswered"]["total"] == 0 and trained["unanswered"]["total"] == 0:
        return "complete"
    return "partial"


def _metrics(gold: list[dict], answered: list[Any]) -> dict:
    if len(answered) != len(gold):
        raise ValueError("the evaluation skipped rows")
    per_question: dict[str, dict[str, list]] = {}
    by_reason: dict[str, int] = {}
    sample = None
    for record, row in zip(gold, answered, strict=True):
        if not isinstance(row, dict):
            raise ValueError("the evaluation returned an unusable row")
        error = row.get("error")
        if error is not None:
            if not isinstance(error, dict):
                raise ValueError("the evaluation returned an unusable error")
            code = str(error.get("code") or "runtime_error")
            sample = sample or error.get("message")
        for key, question in record["questions"].items():
            keys = decisions.options(question)
            target = [record["gold"][key]["probabilities"][k] for k in keys]
            slot = per_question.setdefault(key, {"pairs": [], "true": [], "pred": [], "ties": 0})
            leader = decisions.gold_option(record["gold"][key]["probabilities"])
            if leader is None:
                slot["ties"] += 1
            if error is not None:
                by_reason[code] = by_reason.get(code, 0) + 1
                continue
            try:
                predicted = decisions.predicted(question, (row.get("answers") or {}).get(key))
            except decisions.DecisionRowError:
                by_reason["unusable_answer"] = by_reason.get("unusable_answer", 0) + 1
                continue
            slot["pairs"].append((target, predicted))
            if leader is not None:
                slot["true"].append(leader)
                slot["pred"].append(keys[predicted.index(max(predicted))])
    questions = {key: _scores(slot) for key, slot in per_question.items()}
    overall = _scores(
        {
            "pairs": [pair for slot in per_question.values() for pair in slot["pairs"]],
            "true": [t for slot in per_question.values() for t in slot["true"]],
            "pred": [p for slot in per_question.values() for p in slot["pred"]],
            "ties": sum(slot["ties"] for slot in per_question.values()),
        },
        classes=False,
    )
    # Each question's baseline and macro F1 is pooled by its scored rows, so the overall
    # baseline weights per-question baselines by the rows they are measured on.
    scored = {key: len(slot["true"]) for key, slot in per_question.items()}
    if sum(scored.values()):
        overall["majority_baseline"] = round(
            sum(questions[key]["majority_baseline"] * n for key, n in scored.items() if n)
            / sum(scored.values()),
            6,
        )
    macro = [q["macro_f1"] for q in questions.values() if q["macro_f1"] is not None]
    if macro:
        overall["macro_f1"] = round(sum(macro) / len(macro), 6)
    unanswered = {"total": sum(by_reason.values()), "by_reason": by_reason, "sample": sample}
    return {**overall, "unanswered": unanswered, "questions": questions}


def _scores(slot: dict, *, classes: bool = True) -> dict:
    pairs = slot["pairs"]
    if not pairs:
        return {
            "n": 0,
            "ties": slot["ties"],
            "accuracy": None,
            "majority_baseline": None,
            "macro_f1": None,
            "log_loss": None,
            "brier": None,
        }
    n = len(pairs)
    truth = slot["true"]
    scores = {
        "n": n,
        "ties": slot["ties"],
        "accuracy": None,
        "majority_baseline": None,
        "macro_f1": None,
        "log_loss": round(
            sum(
                -math.fsum(g * math.log(max(p, 1e-12)) for g, p in zip(t, q, strict=True))
                for t, q in pairs
            )
            / n,
            6,
        ),
        "brier": round(
            sum(math.fsum((p - g) ** 2 for g, p in zip(t, q, strict=True)) for t, q in pairs) / n,
            6,
        ),
    }
    if truth:
        scores["accuracy"] = round(
            sum(t == p for t, p in zip(truth, slot["pred"], strict=True)) / len(truth), 6
        )
        scores["majority_baseline"] = round(Counter(truth).most_common(1)[0][1] / len(truth), 6)
    if classes:
        scores["class_metrics"] = per_class_metrics(slot["pred"], truth) or None
        if scores["class_metrics"]:
            f1 = [cls["f1"] for cls in scores["class_metrics"]["classes"]]
            scores["macro_f1"] = round(sum(f1) / len(f1), 6)
    return scores


def cancel(job: DecisionJob) -> DecisionJob:
    cancelled = DecisionJob.objects.filter(pk=job.pk, status__in=DecisionJob.ACTIVE).update(
        status=DecisionJob.Status.CANCELLED, completed_at=timezone.now()
    )
    job.refresh_from_db()
    if not cancelled:
        raise InputValidationError(f"The job is already {job.status}.")
    # Read after the update: an advance that raced it may have started the evaluation call.
    if job.remote_id:
        try:
            modal.FunctionCall.from_id(job.remote_id).cancel()
        except Exception:
            logger.exception("Could not cancel decision job %s", job.id)
    charge(job)
    return job


def charge(job: DecisionJob) -> None:
    """GPU time from start to finish, once. Never raises."""
    if job.cost_usd is not None or not job.started_at or not job.completed_at:
        return
    seconds = max(0.0, (job.completed_at - job.started_at).total_seconds())
    cost = Decimal(str(round(seconds * (gpu_usd_per_second(GPU) or 0.0), 4)))
    claimed = DecisionJob.objects.filter(pk=job.pk, cost_usd__isnull=True).update(cost_usd=cost)
    if not claimed or cost <= 0 or job.created_by_id is None:
        return
    try:
        charge_credits(
            job.created_by,
            cost,
            BillingService.FINETUNING_JOB,
            project_id=job.project_id,
            idempotency_key=f"decision-job:{job.id}",
            metadata={
                "kind": "decision",
                "decision_job_id": str(job.id),
                "model": job.model_id,
                "gpu_type": GPU,
                "seconds": seconds,
                "cost_usd": str(cost),
            },
        )
    except Exception:
        logger.exception("Failed to charge decision job %s", job.pk)


def answer(
    project: Project,
    user,
    model_id: str,
    state: Any,
    questions: Any,
    *,
    job: DecisionJob | None = None,
) -> dict:
    """One System One request against a catalog model or a trained job's checkpoint."""
    spec = model(model_id)
    try:
        request = decisions.request({"state": state, "questions": questions})
    except decisions.DecisionRowError as exc:
        raise InputValidationError(str(exc)) from exc
    problems = decisions.violations(request["questions"], spec["limits"])
    if problems:
        raise InputValidationError(problems[0])
    payload = {
        "repo": spec["repo"],
        "family": spec["family"],
        "max_context_length": spec["max_context_length"],
        "state": request["state"],
        "questions": request["questions"],
    }
    source = spec["repo"]
    if job is not None:
        saved = (job.result or {}).get("saved")
        if job.status != DecisionJob.Status.SUCCEEDED or not saved:
            raise InputValidationError("The trained model is not ready.")
        # A Clef fine-tune is an adapter on the shared base. D3 and d1 fine-tunes are checkpoints.
        if spec["family"] == "clef":
            payload["adapter"] = saved
        else:
            source = saved
    server = _SERVERS[spec["family"]]
    gpu = spec.get("inference_gpu") or GPU
    started = time.monotonic()
    try:
        instance = modal.Cls.from_name(APP_NAME, server, environment_name=_modal_env())(
            source=source.replace("/", "--")
        )
        result = instance.score.spawn(payload).get(timeout=WAIT_SECONDS)
    except TimeoutError as exc:
        raise DecisionWarmingError from exc
    except Exception as exc:
        logger.exception("Decision request failed for %s", model_id)
        raise DecisionUnavailableError("runtime_error") from exc
    latency = time.monotonic() - started
    error = result.get("error") if isinstance(result, dict) else None
    if error is not None:
        code = error.get("code") if isinstance(error, dict) else None
        if code == "request_rejected":
            raise InputValidationError(error.get("message") or "The request was rejected.")
        if code == "max_length_exceeded":
            raise InputValidationError(
                f"The state and questions exceed {spec['max_context_length']} tokens."
            )
        logger.error(
            "Decision model %s could not answer: %s: %s",
            model_id,
            code,
            error.get("message") if isinstance(error, dict) else None,
        )
        raise DecisionUnavailableError(code if code in UNAVAILABLE_REASONS else "runtime_error")
    answers = result.get("answers") if isinstance(result, dict) else None
    try:
        for key, question in request["questions"].items():
            decisions.predicted(question, (answers or {}).get(key))
    except decisions.DecisionRowError as exc:
        logger.error("Decision model %s returned an unusable answer: %s", model_id, exc)
        raise DecisionUnavailableError("runtime_error") from exc
    _charge_request(project, user, spec["id"], latency, job, gpu)
    usage = result.get("usage") or {}
    return {
        "model": spec["id"],
        "answers": answers,
        "usage": {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        },
    }


def _charge_request(project, user, model_id: str, seconds: float, job, gpu: str) -> None:
    seconds = max(seconds, MIN_BILLED_SECONDS)
    cost = Decimal(str(round(seconds * (gpu_usd_per_second(gpu) or 0.0), 6)))
    if cost <= 0 or not getattr(user, "is_authenticated", False):
        return
    try:
        charge_credits(
            user,
            cost,
            BillingService.INFERENCE_FT_MODEL,
            project_id=project.id,
            metadata={
                "kind": "decision",
                "model": model_id,
                "decision_job_id": str(job.id) if job else None,
                "gpu_type": gpu,
                "seconds": round(seconds, 3),
            },
        )
    except Exception:
        logger.exception("Failed to charge a decision request for %s", model_id)
