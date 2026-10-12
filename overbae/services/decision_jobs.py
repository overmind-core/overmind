from __future__ import annotations

import logging
import os

import modal

from overbae.core.errors import InputValidationError
from overbae.modal.decision_catalog import get
from overbae.models import DecisionJob

logger = logging.getLogger(__name__)

APP_NAME = "overmind-decision"
_QUESTION_TYPES = {"choice", "noul", "score"}
_KNOWN_ERRORS = {
    "No records fit the context cap.",
    "Decision training failed.",
}


class DecisionUnavailableError(Exception):
    pass


def validate_questions(questions) -> None:
    if not isinstance(questions, dict) or not questions:
        raise InputValidationError("questions must be a non-empty object.")
    for question in questions.values():
        if not isinstance(question, dict) or question.get("type") not in _QUESTION_TYPES:
            raise InputValidationError("Each question needs a type of choice, noul, or score.")


def validate_records(records) -> None:
    if not isinstance(records, list) or not records:
        raise InputValidationError("records must be a non-empty list.")
    for record in records:
        if not isinstance(record, dict) or "state" not in record:
            raise InputValidationError("Each record needs a state, questions, and gold.")
        validate_questions(record.get("questions"))
        if not isinstance(record.get("gold"), dict):
            raise InputValidationError("Each record needs gold for its questions.")


def submit(project, model_id: str, records: list) -> DecisionJob:
    spec = get(model_id)
    if spec is None:
        raise InputValidationError("Unknown decision model.")
    validate_records(records)
    job = DecisionJob.objects.create(project=project, model_id=spec["id"], records=records)
    payload = {
        "job_id": str(job.id),
        "model": spec["id"],
        "repo": spec["repo"],
        "family": spec["family"],
        "max_context_length": spec["max_context_length"],
        "records": records,
    }
    try:
        call = modal.Function.from_name(
            APP_NAME,
            "train_decision",
            environment_name=os.environ.get("MODAL_ENVIRONMENT") or None,
        ).spawn(payload)
    except Exception:
        logger.exception("Could not start decision job %s", job.id)
        DecisionJob.objects.filter(pk=job.pk, status=DecisionJob.Status.QUEUED).update(
            status=DecisionJob.Status.FAILED,
            error="Could not start decision training.",
        )
        job.refresh_from_db()
        return job
    DecisionJob.objects.filter(pk=job.pk, status=DecisionJob.Status.QUEUED).update(
        status=DecisionJob.Status.RUNNING,
        remote_id=call.object_id,
    )
    job.refresh_from_db()
    return job


def refresh(job: DecisionJob) -> DecisionJob:
    if job.status != DecisionJob.Status.RUNNING or not job.remote_id:
        return job
    try:
        result = modal.FunctionCall.from_id(job.remote_id).get(timeout=0)
    except TimeoutError:
        return job
    except Exception:
        logger.exception("Decision job %s failed", job.id)
        DecisionJob.objects.filter(pk=job.pk, status=DecisionJob.Status.RUNNING).update(
            status=DecisionJob.Status.FAILED,
            error="Decision training failed.",
        )
        job.refresh_from_db()
        return job
    if not isinstance(result, dict):
        error = "Decision training failed."
        status = DecisionJob.Status.FAILED
        result = {}
    elif result.get("error"):
        error = result["error"] if result["error"] in _KNOWN_ERRORS else "Decision training failed."
        status = DecisionJob.Status.FAILED
    else:
        error = ""
        status = DecisionJob.Status.SUCCEEDED
    DecisionJob.objects.filter(pk=job.pk, status=DecisionJob.Status.RUNNING).update(
        status=status,
        error=error,
        result=result,
    )
    job.refresh_from_db()
    return job


def systemone(model_id: str, state, questions) -> dict:
    spec = get(model_id)
    if spec is None:
        raise InputValidationError("Unknown decision model.")
    validate_questions(questions)
    payload = {
        "repo": spec["repo"],
        "family": spec["family"],
        "max_context_length": spec["max_context_length"],
        "state": state,
        "questions": questions,
    }
    try:
        result = modal.Function.from_name(
            APP_NAME,
            "serve_decision",
            environment_name=os.environ.get("MODAL_ENVIRONMENT") or None,
        ).remote(payload)
    except Exception as exc:
        logger.exception("Decision inference failed for %s", model_id)
        raise DecisionUnavailableError from exc
    if not isinstance(result, dict) or "answers" not in result:
        if isinstance(result, dict) and result.get("error") == "max_length_exceeded":
            raise InputValidationError("max_length_exceeded")
        raise DecisionUnavailableError
    usage = result.get("usage") or {}
    return {
        "model": result.get("model") or spec["id"],
        "answers": result["answers"],
        "usage": {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        },
    }
