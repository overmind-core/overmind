"""Every LLM-judge call goes through here. Explanation precedes the label in
every output schema (models reason before they answer) and the default judge
avoids the model family under test.
"""

from __future__ import annotations

import hashlib
import heapq
import logging
import os
import statistics
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.db import close_old_connections, connection
from pydantic import BaseModel

from overbae.core.llms import LLM_PROVIDER_BY_MODEL, ModelSpec, call_llm, try_json_parsing
from overbae.core.model_resolver import (
    TaskType,
    model_chain,
    openrouter_configured,
    resolve_model,
)

logger = logging.getLogger(__name__)


def _cache_enabled() -> bool:
    return bool(getattr(settings, "EVAL_JUDGE_CACHE", True))


@dataclass
class ResolvedJudge:
    model_name: str | None
    model_spec: ModelSpec | None
    family: str


def resolve_judge(judge_model: str, project_id: str | None = None) -> ResolvedJudge:
    """``judge_model`` is "" (the default judge), a catalog model name, or a
    :class:`ModelRef` id."""
    if not judge_model:
        name = resolve_model(TaskType.JUDGE_SCORING)
        return ResolvedJudge(model_name=name, model_spec=None, family=_family_of(name))

    spec = _model_ref_spec(judge_model)
    if spec is not None:
        return ResolvedJudge(model_name=None, model_spec=spec, family=spec.provider)

    return ResolvedJudge(model_name=judge_model, model_spec=None, family=_family_of(judge_model))


def resolve_default_judge(variant_models: list[str] | None = None) -> ResolvedJudge | None:
    """``None`` when nothing to avoid or no avoiding pick exists; lazy default
    resolution keeps API-key lookup out of mocked and cache-served paths."""
    avoid = {_family_of(m) for m in (variant_models or [])} - {"unknown"}
    if not avoid or not openrouter_configured():
        return None
    for model_name in model_chain(TaskType.JUDGE_SCORING):
        family = _family_of(model_name)
        if family not in avoid:
            return ResolvedJudge(model_name=model_name, model_spec=None, family=family)
    return None


def cross_family_judges(size: int = 3) -> list[ResolvedJudge]:
    picked: list[ResolvedJudge] = []
    seen: set[str] = set()
    for model_name in model_chain(TaskType.JUDGE_SCORING):
        family = _family_of(model_name)
        if family in seen:
            continue
        seen.add(family)
        picked.append(ResolvedJudge(model_name=model_name, model_spec=None, family=family))
        if len(picked) >= size:
            break
    return picked


def _model_ref_spec(maybe_id: str) -> ModelSpec | None:
    try:
        uuid.UUID(str(maybe_id))
    except (ValueError, AttributeError, TypeError):
        return None
    try:
        from overbae.models import ModelRef  # noqa: PLC0415

        ref = ModelRef.objects.filter(id=maybe_id).first()
    except Exception:  # noqa: BLE001
        return None
    if not ref:
        return None
    return ModelSpec(
        provider=ref.provider,
        model_id=ref.model_id,
        base_url=ref.base_url,
        api_key_env=ref.api_key_ref,
        params=ref.params or {},
    )


def _family_of(model_name: str) -> str:
    return LLM_PROVIDER_BY_MODEL.get(model_name, "unknown")


def resolved_model_name(judge: ResolvedJudge) -> str:
    """A silent fallback to the second-priority model is a new judge contract."""
    if judge.model_spec is not None:
        return judge.model_spec.model_id
    return judge.model_name or ""


def rubric_hash(*parts: Any) -> str:
    blob = "\x00".join(str(p) for p in parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def judge_contract_identifier(judge: ResolvedJudge, rubric_digest: str) -> str:
    return f"{judge.family}:{resolved_model_name(judge)}:{rubric_digest}"[:255]


def normalize_unit_interval(raw: float, lo: float, hi: float) -> float:
    """The only place a judge range maps to [0, 1]."""
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (float(raw) - lo) / (hi - lo)))


def geometric_median(values: list[float]) -> float:
    """In 1-D the geometric median is the median; named for the RoPoLL contract."""
    return float(statistics.median(values))


def is_rate_limited(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "rate-limit" in text


_PERMANENT_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "context window",
    "too many tokens",
    "prompt is too long",
)


def is_permanent_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _PERMANENT_MARKERS)


def sanitize_raw(raw: str) -> str:
    # Postgres text/jsonb reject NUL and judges occasionally emit one.
    return raw.replace("\\u0000", "").replace("\x00", "")


def parse_structured(raw: str, response_format: type[BaseModel]) -> BaseModel | None:
    raw = sanitize_raw(raw)
    try:
        return response_format.model_validate_json(raw)
    except Exception:  # noqa: BLE001 — fall through to JSON repair
        try:
            return response_format.model_validate(try_json_parsing(raw))
        except Exception:  # noqa: BLE001
            return None


@dataclass
class JudgeOutcome:
    parsed: BaseModel | None
    raw: str
    stats: dict[str, Any]
    judge_trace_id: str
    cached: bool = False


def _judge_identity(judge: ResolvedJudge) -> str:
    if judge.model_spec is not None:
        s = judge.model_spec
        return f"spec:{s.provider}/{s.model_id}:{s.base_url}"
    return f"model:{judge.model_name}"


def _cache_key(
    judge: ResolvedJudge, system_prompt: str | None, prompt: str, response_format
) -> str:
    schema = f"{response_format.__module__}.{response_format.__qualname__}"
    payload = "\x00".join([_judge_identity(judge), schema, system_prompt or "", prompt])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ~100k tokens at 4 chars/token: inside every panel judge's context window.
_PROMPT_CHAR_BUDGET = 400_000
_TRUNCATION_MARKER = "\n…[evidence truncated to fit the judge context]…\n"


def fit_prompt(prompt: str, budget: int = _PROMPT_CHAR_BUDGET) -> str:
    """Rubric leads and closing instructions trail; the middle evidence drops."""
    if len(prompt) <= budget:
        return prompt
    head = int(budget * 0.7)
    tail = budget - head - len(_TRUNCATION_MARKER)
    return prompt[:head] + _TRUNCATION_MARKER + prompt[-tail:]


def invoke_judge(
    prompt: str,
    *,
    response_format: type[BaseModel],
    judge: ResolvedJudge | None = None,
    judge_model: str = "",
    project_id: str | None = None,
    system_prompt: str | None = None,
    use_cache: bool = True,
    request_kwargs: dict[str, Any] | None = None,
) -> JudgeOutcome:
    """Never raises on parse; cache hits report ``response_cost = 0``."""
    judge = judge or resolve_judge(judge_model, project_id)
    prompt = fit_prompt(prompt)
    trace_id = uuid.uuid4().hex

    cache_on = use_cache and _cache_enabled()
    key = _cache_key(judge, system_prompt, prompt, response_format) if cache_on else ""

    if cache_on:
        cached = _cache_get(key)
        if cached is not None:
            parsed = parse_structured(cached, response_format)
            return JudgeOutcome(
                parsed=parsed,
                raw=cached,
                stats={"response_cost": 0.0, "response_ms": 0, "cached": True},
                judge_trace_id=trace_id,
                cached=True,
            )

    raw, stats = call_llm(
        prompt,
        system_prompt=system_prompt,
        model=judge.model_name,
        model_spec=judge.model_spec,
        response_format=response_format,
        request_kwargs=request_kwargs,
    )
    parsed = parse_structured(raw, response_format)
    # A cached parse failure would be sticky.
    if cache_on and parsed is not None:
        _cache_set(key, raw, stats)
    return JudgeOutcome(parsed=parsed, raw=raw, stats=stats, judge_trace_id=trace_id)


def _cache_get(key: str) -> str | None:
    """A missing table is a cache miss."""
    try:
        from overbae.models import JudgeCache  # noqa: PLC0415

        row = JudgeCache.objects.filter(key=key).first()
        if row is None:
            return None
        JudgeCache.objects.filter(key=key).update(hits=row.hits + 1)
        return row.raw
    except Exception:  # noqa: BLE001
        return None


def _cache_set(key: str, raw: str, stats: dict[str, Any]) -> None:
    try:
        from overbae.models import JudgeCache  # noqa: PLC0415

        JudgeCache.objects.update_or_create(
            key=key,
            defaults={
                "raw": raw,
                "prompt_tokens": int(stats.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(stats.get("completion_tokens", 0) or 0),
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("Judge cache write skipped for key %s", key[:12])


DEFAULT_MAX_WORKERS = int(os.environ.get("EVAL_JUDGE_CONCURRENCY", "8"))
DEFAULT_MAX_RETRIES = 2

_AIMD_INCREASE = 0.5
_AIMD_CLEAN_WINDOW_S = 5.0
_AIMD_COLLAPSE_ERRORS = 2
_AIMD_COLLAPSE_WINDOW_S = 30.0

_BUCKET_INITIAL_RATE = 2.0  # calls/second per provider — modest start
_BUCKET_RECOVERY_CAP = 8.0  # multiple of the initial rate
_BUCKET_PENALTY_COOLDOWN_S = 2.0


@dataclass(frozen=True)
class Outcome:
    value: Any = None
    error: BaseException | None = None
    deferred: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and not self.deferred


@dataclass
class JudgeTask:
    key: Any
    fn: Callable[[], Any]
    provider: str = "default"
    retries: int = 0


class TokenBucket:
    def __init__(self, rate: float = _BUCKET_INITIAL_RATE):
        self._initial = rate
        self._rate = rate
        self._tokens = rate
        self._capacity = max(rate, 1.0)
        self._last = time.monotonic()
        self._last_penalty = 0.0
        self._lock = threading.Lock()

    def acquire(self, deadline: float | None = None) -> bool:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    self._rate = min(self._initial * _BUCKET_RECOVERY_CAP, self._rate * 1.05)
                    return True
                wait = (1.0 - self._tokens) / self._rate
            if deadline is not None and time.monotonic() + wait > deadline:
                return False
            time.sleep(min(wait, 1.0))

    def penalize(self) -> None:
        """The cooldown makes one 429 burst a single penalty, not one per call."""
        with self._lock:
            now = time.monotonic()
            if now - self._last_penalty < _BUCKET_PENALTY_COOLDOWN_S:
                return
            self._last_penalty = now
            self._rate = max(0.1, self._rate / 2.0)
            self._tokens = 0.0


class AIMDController:
    def __init__(self, cap: int):
        self._cap = float(cap)
        self._target = float(cap)
        self._lock = threading.Lock()
        self._last_error = 0.0
        self._last_increase = time.monotonic()
        self._recent_errors: list[float] = []

    def admits(self, lane: int) -> bool:
        with self._lock:
            now = time.monotonic()
            if (
                now - self._last_error >= _AIMD_CLEAN_WINDOW_S
                and now - self._last_increase >= _AIMD_CLEAN_WINDOW_S
            ):
                self._target = min(self._cap, self._target + _AIMD_INCREASE)
                self._last_increase = now
            return lane < int(self._target) or (lane == 0)

    def on_error(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._last_error = now
            self._recent_errors = [
                t for t in self._recent_errors if now - t < _AIMD_COLLAPSE_WINDOW_S
            ]
            self._recent_errors.append(now)
            if len(self._recent_errors) >= _AIMD_COLLAPSE_ERRORS:
                self._target = 1.0
            else:
                self._target = max(1.0, self._target / 2.0)


@dataclass(order=True)
class _QueueItem:
    priority: tuple
    task: JudgeTask = field(compare=False)


class JudgeExecutor:
    """Tasks still queued at the deadline come back ``deferred``. Worker threads
    close their own DB connection: an exited thread holding one leaks it for
    the process lifetime."""

    def __init__(
        self,
        max_workers: int = DEFAULT_MAX_WORKERS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self._max_workers = max(1, max_workers)
        self._max_retries = max_retries
        self._controller = AIMDController(cap=self._max_workers)
        self._buckets: dict[str, TokenBucket] = {}
        self._bucket_lock = threading.Lock()

    def _bucket(self, provider: str) -> TokenBucket:
        with self._bucket_lock:
            if provider not in self._buckets:
                self._buckets[provider] = TokenBucket()
            return self._buckets[provider]

    def run(self, tasks: list[JudgeTask], *, timeout_s: float | None = None) -> dict[Any, Outcome]:
        if not tasks:
            return {}
        deadline = time.monotonic() + timeout_s if timeout_s else None
        results: dict[Any, Outcome] = {}
        results_lock = threading.Lock()
        heap: list[_QueueItem] = []
        heap_lock = threading.Condition()
        outstanding = {"n": len(tasks)}
        seq = iter(range(1_000_000_000))

        def push(task: JudgeTask) -> None:
            # Retries first, then submission order.
            with heap_lock:
                heapq.heappush(heap, _QueueItem((-task.retries, next(seq)), task))
                heap_lock.notify()

        for task in tasks:
            push(task)

        def finish(task: JudgeTask, outcome: Outcome) -> None:
            with results_lock:
                results[task.key] = outcome
            with heap_lock:
                outstanding["n"] -= 1
                heap_lock.notify_all()

        def worker(lane: int) -> None:
            close_old_connections()
            try:
                while True:
                    with heap_lock:
                        while not heap and outstanding["n"] > 0:
                            timed_out = not heap_lock.wait(timeout=0.25)
                            if timed_out and deadline and time.monotonic() > deadline:
                                return
                        if outstanding["n"] <= 0:
                            return
                        item = heapq.heappop(heap)
                    task = item.task

                    if deadline and time.monotonic() > deadline:
                        finish(task, Outcome(deferred=True))
                        continue
                    if not self._controller.admits(lane):
                        push(task)
                        time.sleep(0.2)
                        continue
                    if not self._bucket(task.provider).acquire(deadline):
                        finish(task, Outcome(deferred=True))
                        continue

                    try:
                        finish(task, Outcome(value=task.fn()))
                    except Exception as exc:  # noqa: BLE001 — classified below
                        if is_rate_limited(exc):
                            self._bucket(task.provider).penalize()
                        self._controller.on_error()
                        if task.retries < self._max_retries and not is_permanent_error(exc):
                            task.retries += 1
                            push(task)
                        else:
                            finish(task, Outcome(error=exc))
            finally:
                connection.close()

        workers = min(self._max_workers, len(tasks))
        threads = [
            threading.Thread(target=worker, args=(lane,), name=f"judge-exec-{lane}", daemon=True)
            for lane in range(workers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=(timeout_s + 30.0) if timeout_s else None)

        # Anything unfinished (hung thread, deadline race) lands as deferred.
        for task in tasks:
            results.setdefault(task.key, Outcome(deferred=True))
        return results
