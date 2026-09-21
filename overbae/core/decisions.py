from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

import httpx
import redis
from django.conf import settings
from django.core.cache import cache
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from overbae.core.model_registry import PROVIDERS, decision_model

logger = logging.getLogger(__name__)

CONTRACT_VERSION = "decisions@1"
_STATE_QUESTION_BUDGET = 31_000
_REQUEST_BUDGET = 62_000
_MAX_QUESTIONS = 100
_TIMEOUT = 20.0
_CACHE_SECONDS = 86_400


class DecisionError(RuntimeError):
    def __init__(self, reason: str, *, stats: dict[str, Any] | None = None, answers=None):
        super().__init__(reason)
        self.reason = reason
        self.stats = stats or {"response_cost": 0.0, "response_ms": 0}
        self.answers = answers or {}


class ChoiceQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["choice"] = "choice"
    instructions: str = Field(min_length=1)
    criteria: dict[str, str] = Field(min_length=2, max_length=255)

    @model_validator(mode="after")
    def valid_options(self):
        if any(not key.strip() or not value.strip() for key, value in self.criteria.items()):
            raise ValueError("Decision options and descriptions must not be empty.")
        return self


class ChoiceAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1)

    def validate_question(self, question: ChoiceQuestion) -> None:
        if (
            set(self.probabilities) != set(question.criteria)
            or self.choice not in question.criteria
        ):
            raise ValueError("Decision answer does not match the question options.")
        values = self.probabilities.values()
        if any(not math.isfinite(p) or not 0 <= p <= 1 for p in values):
            raise ValueError("Invalid decision probabilities.")
        if not math.isclose(sum(values), 1.0, abs_tol=0.002):
            raise ValueError("Decision probabilities must sum to one.")
        if self.probabilities[self.choice] + 1e-6 < max(values):
            raise ValueError("Decision choice is not a highest-probability option.")


@dataclass
class DecisionResult:
    answers: dict[str, ChoiceAnswer]
    stats: dict[str, Any]


def _encoded(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode(
            "utf-8"
        )
    except (ValueError, TypeError) as exc:
        raise DecisionError("invalid_state") from exc


def question_batches(state: Any, questions: dict[str, ChoiceQuestion]) -> list[dict]:
    if not questions or len(questions) > 1000 or any(not key for key in questions):
        raise DecisionError("invalid_questions")
    # UTF-8 bytes are a conservative token bound; oversized evidence falls back intact.
    state_size = len(_encoded(state))
    batches: list[dict] = []
    batch: dict[str, dict] = {}
    size = state_size
    for key, question in questions.items():
        wire = question.model_dump()
        question_size = len(_encoded({key: wire}))
        if state_size + question_size > _STATE_QUESTION_BUDGET:
            raise DecisionError("context_budget")
        if batch and (size + question_size > _REQUEST_BUDGET or len(batch) >= _MAX_QUESTIONS):
            batches.append(batch)
            batch, size = {}, state_size
        batch[key] = wire
        size += question_size
    if batch:
        batches.append(batch)
    return batches


_RESERVE = """
local now = redis.call('TIME')
local ms = now[1] * 1000 + math.floor(now[2] / 1000)
local rpm = tonumber(ARGV[1])
local tps = tonumber(ARGV[2])
local tokens = tonumber(ARGV[3])
local req_at = tonumber(redis.call('GET', KEYS[1]) or ms)
local tok_at = tonumber(redis.call('GET', KEYS[2]) or ms)
local wait = math.max(req_at - ms, tok_at - ms, 0)
if wait > 0 then return wait end
local ttl = math.ceil(math.max(60000 / rpm, tokens * 1000 / tps)) + 60000
redis.call('SET', KEYS[1], ms + 60000 / rpm, 'PX', ttl)
redis.call('SET', KEYS[2], ms + tokens * 1000 / tps, 'PX', ttl)
return 0
"""


@lru_cache(maxsize=4)
def _redis(url: str):
    return redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)


def reserve_capacity(account: str, tokens: int, deadline: float) -> None:
    location = settings.CACHES["default"].get("LOCATION")
    if not isinstance(location, str) or not location.startswith(("redis://", "rediss://")):
        raise DecisionError("capacity_unavailable")
    try:
        rpm = int(os.environ.get("JEV_REQUESTS_PER_MINUTE", "1200"))
        tps = int(os.environ.get("JEV_TOKENS_PER_SECOND", "250000"))
        if min(rpm, tps) <= 0:
            raise ValueError
    except ValueError as exc:
        raise DecisionError("capacity_configuration") from exc
    key = f"overmind:decisions:{{{account}}}"
    try:
        while True:
            if time.monotonic() >= deadline:
                raise DecisionError("capacity_timeout")
            wait = (
                float(
                    _redis(location).eval(
                        _RESERVE, 2, f"{key}:requests", f"{key}:tokens", rpm, tps, tokens
                    )
                )
                / 1000
            )
            if wait <= 0:
                return
            if time.monotonic() + wait >= deadline:
                raise DecisionError("capacity_timeout")
            time.sleep(min(wait, 0.5))
    except redis.RedisError as exc:
        raise DecisionError("capacity_unavailable") from exc


def merge_stats(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    costs = [item.get("response_cost") for item in attempts]
    return {
        "response_cost": None if any(cost is None for cost in costs) else sum(costs),
        "response_ms": sum(item.get("response_ms", 0) or 0 for item in attempts),
        "prompt_tokens": sum(item.get("prompt_tokens", 0) or 0 for item in attempts),
        "completion_tokens": sum(item.get("completion_tokens", 0) or 0 for item in attempts),
        "cached": bool(attempts) and all(item.get("cached") for item in attempts),
        "served_model": attempts[-1].get("served_model", "") if attempts else "",
        "attempts": attempts,
    }


def _usage(payload: dict, elapsed: float) -> dict[str, Any]:
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    cost = usage.get("cost")
    if (
        isinstance(cost, bool)
        or not isinstance(cost, (float, int))
        or not math.isfinite(cost)
        or cost < 0
    ):
        cost = None

    def tokens(name):
        value = usage.get(name)
        return value if type(value) is int and value >= 0 else 0

    return {
        "response_cost": cost,
        "response_ms": round(elapsed * 1000),
        "prompt_tokens": tokens("input_tokens"),
        "completion_tokens": tokens("output_tokens"),
        "served_model": str(payload.get("model") or ""),
        "provider": str(payload.get("provider") or "TypeSafe"),
        "request_id": str(payload.get("id") or ""),
    }


def _request(body: dict, *, deadline: float, account: str) -> dict:
    provider = PROVIDERS["openrouter"]
    uncertain_cost = False
    for attempt in range(3):
        try:
            reserve_capacity(account, len(_encoded(body)), deadline)
        except DecisionError as exc:
            if uncertain_cost:
                exc.stats["response_cost"] = None
            raise
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DecisionError(
                "provider_timeout", stats={"response_cost": None if uncertain_cost else 0.0}
            )
        try:
            response = httpx.post(
                f"{provider.base_url}/systemone",
                headers={"Authorization": f"Bearer {provider.key()}", **provider.headers},
                json=body,
                timeout=httpx.Timeout(min(remaining, _TIMEOUT), connect=min(remaining, 5)),
            )
        except httpx.TimeoutException as exc:
            # A timed-out request may have run and been billed; never claim zero cost.
            raise DecisionError("provider_timeout", stats={"response_cost": None}) from exc
        except httpx.HTTPError as exc:
            raise DecisionError("provider_unavailable", stats={"response_cost": None}) from exc
        if response.status_code in {429, 502, 503, 529} and attempt < 2:
            uncertain_cost |= response.status_code >= 500
            delay = 0.5 * 2**attempt
            with suppress(ValueError):
                delay = max(delay, float(response.headers.get("Retry-After", "0")))
            if time.monotonic() + delay >= deadline:
                raise DecisionError(
                    "provider_overloaded", stats={"response_cost": None if uncertain_cost else 0.0}
                )
            time.sleep(delay)
            continue
        if not response.is_success:
            raise DecisionError(
                f"provider_http_{response.status_code}",
                stats={
                    "response_cost": None if uncertain_cost or response.status_code >= 500 else 0.0
                },
            )
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            if uncertain_cost:
                payload["unreported_attempt_cost"] = True
            return payload
        except ValueError as exc:
            raise DecisionError("invalid_response", stats={"response_cost": None}) from exc
    raise DecisionError("provider_overloaded")


def decide(
    state: Any,
    questions: dict[str, ChoiceQuestion],
    *,
    project_id: str,
    workload: str,
    contract: str,
    model: str | None = None,
    use_cache: bool = True,
) -> DecisionResult:
    started = time.monotonic()
    model = model or decision_model().slug
    if model != decision_model().slug:
        raise DecisionError("unsupported_model")
    if not project_id:
        raise DecisionError("project_required")
    batches = question_batches(state, questions)
    provider = PROVIDERS["openrouter"]
    if not provider.configured():
        raise DecisionError("not_configured")
    account = hashlib.sha256(provider.key().encode()).hexdigest()[:24]
    deadline = started + _TIMEOUT
    answers: dict[str, ChoiceAnswer] = {}
    attempts: list[dict[str, Any]] = []
    for batch in batches:
        batch_started = time.monotonic()
        body = {"model": model, "state": state, "questions": batch}
        digest = hashlib.sha256(
            _encoded([project_id, workload, contract, CONTRACT_VERSION, body])
        ).hexdigest()
        key = f"decision:{digest}"
        payload = None
        if use_cache:
            try:
                payload = cache.get(key)
            except Exception:  # noqa: BLE001 — cache availability does not authorize unmetered calls
                logger.debug("Decision cache unavailable")
        cached = payload is not None
        try:
            if payload is None:
                payload = _request(body, deadline=deadline, account=account)
            stats = _usage(payload, time.monotonic() - batch_started)
            if payload.get("unreported_attempt_cost") and not cached:
                stats = merge_stats([{"response_cost": None}, stats])
            if cached:
                stats.update(
                    response_cost=0.0,
                    prompt_tokens=0,
                    completion_tokens=0,
                    cached=True,
                )
            attempts.append({**stats, "workload": workload, "question_count": len(batch)})
            raw_answers = payload.get("answers")
            if not isinstance(raw_answers, dict) or set(raw_answers) != set(batch):
                raise ValueError("Decision question coverage mismatch.")
            if stats["served_model"] != model and not stats["served_model"].startswith(model + "-"):
                raise ValueError("Decision model identity does not match the requested family.")
            parsed = {key: ChoiceAnswer.model_validate(value) for key, value in raw_answers.items()}
            for key, answer in parsed.items():
                answer.validate_question(questions[key])
            answers.update(parsed)
        except DecisionError as exc:
            stats = merge_stats(
                [
                    *attempts,
                    {**exc.stats, "response_ms": round((time.monotonic() - batch_started) * 1000)},
                ]
            )
            stats["response_ms"] = round((time.monotonic() - started) * 1000)
            raise DecisionError(exc.reason, stats=stats, answers=answers) from exc
        except (ValueError, TypeError, ValidationError) as exc:
            stats = merge_stats(attempts)
            stats["response_ms"] = round((time.monotonic() - started) * 1000)
            raise DecisionError("invalid_response", stats=stats, answers=answers) from exc
        if use_cache and not cached:
            try:
                cache.set(f"decision:{digest}", payload, timeout=_CACHE_SECONDS)
            except Exception:  # noqa: BLE001
                logger.debug("Decision cache write unavailable")
    stats = merge_stats(attempts)
    stats["response_ms"] = round((time.monotonic() - started) * 1000)
    logger.info("Decision usage workload=%s project=%s usage=%s", workload, project_id, stats)
    return DecisionResult(answers=answers, stats=stats)
