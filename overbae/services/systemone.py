"""JEV systemone requests. The API proxies them in the request: a catalog model
goes to OpenRouter, and a fine-tuned deployment calls its Modal worker."""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import suppress
from decimal import Decimal

import requests
from django.conf import settings
from django.db.models import Q

from overbae.core.errors import InputValidationError
from overbae.core.model_registry import OPENROUTER_BASE_URL, decision_models, openrouter_configured
from overbae.models import APIToken, DeployedModel
from overbae.services.billing_ledger import BillingService, charge_credits
from overbae.services.decision_serving import worker
from overbae.services.deployed_chat import is_cold_start, record_inference_call

logger = logging.getLogger(__name__)

_KINDS = {"choice", "noul", "score"}
_OPENROUTER_TIMEOUT = 120


def parse(body: dict) -> tuple[str, str, list[str], list[dict]]:
    if not isinstance(body, dict):
        raise InputValidationError("Send a systemone JSON object.")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise InputValidationError("model is required.")
    state = body.get("state")
    if not isinstance(state, str):
        if isinstance(state, (dict, list)):
            state = json.dumps(state, ensure_ascii=False)
        else:
            raise InputValidationError("state must be text.")
    questions = body.get("questions")
    if not isinstance(questions, dict) or not 1 <= len(questions) <= 16:
        raise InputValidationError("questions must contain 1 to 16 decisions.")
    names: list[str] = []
    decisions: list[dict] = []
    for name, question in questions.items():
        key = str(name)
        if not key.strip():
            raise InputValidationError("question ids must be nonempty.")
        if not isinstance(question, dict) or question.get("type") not in _KINDS:
            raise InputValidationError(f"{key}: type must be choice, noul, or score.")
        instructions = question.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            raise InputValidationError(f"{key}: instructions are required.")
        kind = question["type"]
        options, instructions = _options(key, kind, question.get("criteria"), instructions)
        names.append(key)
        decisions.append(
            {
                "state": state,
                "question": instructions,
                "kind": kind,
                "options": options,
            }
        )
    return model.strip(), state, names, decisions


def _options(name: str, kind: str, criteria, instructions: str) -> tuple[list[str], str]:
    if kind == "noul":
        criteria = criteria or {}
        if not isinstance(criteria, dict):
            raise InputValidationError(f"{name}: noul criteria must be an object.")
        false_text = criteria.get("false")
        true_text = criteria.get("true")
        options = [
            false_text if isinstance(false_text, str) and false_text.strip() else "false",
            true_text if isinstance(true_text, str) and true_text.strip() else "true",
        ]
    elif kind == "score":
        if not isinstance(criteria, list):
            raise InputValidationError(f"{name}: score criteria must be a list of levels.")
        options = [str(level) for level in criteria]
    else:
        if not isinstance(criteria, dict):
            raise InputValidationError(f"{name}: choice criteria must be an object.")
        options = []
        lines = []
        for key, value in criteria.items():
            option = str(key)
            options.append(option)
            if isinstance(value, str) and value.strip() and value.strip() != option:
                line = f"{option}: {value.strip()}"
                if line not in instructions:
                    lines.append(line)
        if lines:
            instructions = instructions.rstrip() + "\n" + "\n".join(lines)
    if (
        not 2 <= len(options) <= 255
        or any(not option.strip() for option in options)
        or len(set(options)) != len(options)
    ):
        raise InputValidationError(f"{name}: provide 2–255 distinct options.")
    return options, instructions


def resolve(model: str, project_ids) -> DeployedModel | None:
    queryset = DeployedModel.objects.filter(
        project_id__in=project_ids, inference_contract="decision"
    )
    query = Q(model_id=model)
    with suppress(ValueError):
        query |= Q(pk=uuid.UUID(model))
    matches = list(queryset.filter(query)[:2])
    if len(matches) > 1:
        raise InputValidationError(
            "model matches more than one deployment. Pass the deployment id."
        )
    return matches[0] if matches else None


def catalog_model(model: str) -> bool:
    return any(
        model == item.slug or model.startswith(item.slug + "-") for item in decision_models()
    )


def answer(names: list[str], kinds: list[str], predictions: list[dict]) -> dict:
    payload = {}
    for name, kind, prediction in zip(names, kinds, predictions, strict=True):
        options = prediction["options"]
        probabilities = [float(value) for value in prediction["probabilities"]]
        if kind == "noul":
            payload[name] = {"type": "noul", "noul": probabilities[1]}
            continue
        if kind == "score":
            keys = [str(index) for index in range(len(options))]
            table = dict(zip(keys, probabilities, strict=True))
            payload[name] = {
                "type": "score",
                "score": sum(index * value for index, value in enumerate(probabilities)),
                "confidence": _score_confidence(probabilities),
                "legend": dict(zip(keys, options, strict=True)),
                "probabilities": table,
            }
            continue
        table = dict(zip(options, probabilities, strict=True))
        payload[name] = {
            "type": "choice",
            "choice": max(options, key=table.__getitem__),
            "confidence": _choice_confidence(probabilities),
            "probabilities": table,
        }
    return payload


def _choice_confidence(probabilities: list[float]) -> float:
    width = len(probabilities)
    if width <= 1:
        return 1.0
    top = max(probabilities)
    return (top - 1 / width) / (1 - 1 / width)


def _score_confidence(probabilities: list[float]) -> float:
    width = len(probabilities)
    mode = max(range(width), key=probabilities.__getitem__)
    deviation = sum(prob * abs(index - mode) for index, prob in enumerate(probabilities))
    middle = (width - 1) / 2
    uniform = sum(abs(index - middle) for index in range(width)) / width
    if uniform == 0:
        return 1.0
    return max(0.0, 1 - deviation / uniform)


def _error(status: int, message: str, kind: str) -> tuple[int, dict]:
    return status, {"error": {"message": message, "type": kind}}


def call_deployment(
    deployment, user, auth, names: list[str], decisions: list[dict]
) -> tuple[int, dict]:
    if deployment.status != "ready":
        return _error(409, f"Model '{deployment.model_id}' is not ready.", "invalid_request")
    cold = is_cold_start(deployment)
    started = time.monotonic()
    source = "application" if isinstance(auth, APIToken) else "playground"
    try:
        result = worker(deployment).predict.remote(decisions)
    except Exception:
        logger.exception("Decision inference failed for %s", deployment.model_id)
        record_inference_call(
            deployment,
            None,
            (time.monotonic() - started) * 1000,
            is_cold=cold,
            user=user,
            outcome="failed",
            error_code="provider_failed",
            source=source,
            requested_model=deployment.model_id,
        )
        return _error(502, "Decision inference failed.", "server_error")
    elapsed_ms = (time.monotonic() - started) * 1000
    if not isinstance(result, dict) or result.get("error_code"):
        record_inference_call(
            deployment,
            None,
            elapsed_ms,
            is_cold=cold,
            user=user,
            outcome="failed",
            error_code=(result or {}).get("error_code") or "provider_failed",
            source=source,
            requested_model=deployment.model_id,
        )
        return _error(502, "Decision inference failed.", "server_error")
    record_inference_call(
        deployment,
        result.get("usage"),
        elapsed_ms,
        is_cold=cold,
        user=user,
        source=source,
        requested_model=deployment.model_id,
    )
    usage = result.get("usage") or {}
    return 200, {
        "model": deployment.model_id,
        "answers": answer(names, [item["kind"] for item in decisions], result["predictions"]),
        "usage": {
            "input_tokens": usage.get("prompt_tokens") or 0,
            "output_tokens": usage.get("completion_tokens") or 0,
            "cost": None,
        },
        "id": str(uuid.uuid4()),
        "provider": "Overmind",
    }


def call_openrouter(user, body: dict) -> tuple[int, dict]:
    if not openrouter_configured():
        return _error(503, "Frontier model inference is not configured.", "server_error")
    try:
        response = requests.post(
            f"{OPENROUTER_BASE_URL}/systemone",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://overmindlab.ai",
            },
            json=body,
            timeout=_OPENROUTER_TIMEOUT,
        )
    except requests.RequestException:
        logger.exception("OpenRouter systemone request failed")
        return _error(502, "The model provider request failed.", "server_error")
    if response.status_code == 429:
        return _error(
            429,
            "The model provider is rate-limiting requests. Try again shortly.",
            "rate_limit_error",
        )
    if not response.ok:
        logger.error("OpenRouter systemone HTTP %s", response.status_code)
        return _error(
            502,
            f"The model provider returned an error (HTTP {response.status_code}).",
            "server_error",
        )
    try:
        payload = response.json()
    except ValueError:
        return _error(502, "The model provider returned an invalid response.", "server_error")
    if not isinstance(payload, dict):
        return _error(502, "The model provider returned an invalid response.", "server_error")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    cost = usage.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
        try:
            charge_credits(
                user,
                Decimal(str(cost)),
                BillingService.INFERENCE,
                idempotency_key=f"inference:{user.pk}:{uuid.uuid4()}",
                metadata={"model_id": body.get("model"), "usage": usage},
            )
        except Exception:
            logger.exception("Failed to charge systemone inference for user_id=%s", user.pk)
    return 200, payload
