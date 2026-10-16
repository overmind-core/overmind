"""Generates a run's outputs: what the model under test answers for each dataset row,
behind durable admission with project and run fairness."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, F, IntegerField, Max, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from overbae.core.llms import ModelSpec, call_llm
from overbae.innermind import replay
from overbae.models import EvalGenerationScheduler, EvalRun, EvalRunRow, Span
from overbae.services.datasets import rows as row_store
from overbae.services.datasets.examples import matches_reference
from overbae.services.datasets.profiler import bind_eval_reference
from overbae.services.transcripts import chatml, normalizer

logger = logging.getLogger(__name__)

IN_FLIGHT = (EvalRunRow.State.QUEUED, EvalRunRow.State.RUNNING)
UNFINISHED = (EvalRunRow.State.WAITING, *IN_FLIGHT)
QUEUED_LEASE = timedelta(minutes=30)
# The worker's hard time limit is 22 minutes; never replay an attempt whose outcome is unknown.
RUNNING_LEASE = timedelta(minutes=23)
SCHEDULER_HEALTHY = timedelta(minutes=2)


class GenerationStoppedError(Exception):
    pass


def ensure_run_active(run_id) -> None:
    if not EvalRun.objects.filter(pk=run_id).exclude(status__in=EvalRun.TERMINAL).exists():
        raise GenerationStoppedError("evaluation is no longer active")


@transaction.atomic
def dispatch(publish, publish_scoring) -> dict[str, int]:
    """One admission tick. ``publish(row_id, task_id)`` starts a generation and
    ``publish_scoring(run_id, task_id)`` hands a fully generated run to scoring."""
    EvalGenerationScheduler.objects.get_or_create(pk=1)
    scheduler = (
        EvalGenerationScheduler.objects.select_for_update(skip_locked=True).filter(pk=1).first()
    )
    if scheduler is None:
        return {"admitted": 0, "scoring": 0, "expired": 0, "busy": True}
    now = timezone.now()
    scheduler.last_tick_at = now
    scheduler.save(update_fields=["last_tick_at"])
    rows = EvalRunRow.objects
    rows.filter(
        state__in=(EvalRunRow.State.WAITING, EvalRunRow.State.QUEUED),
        run__status__in=EvalRun.TERMINAL,
    ).update(state=EvalRunRow.State.CANCELLED, finished_at=now)

    expired = list(rows.filter(state__in=IN_FLIGHT, expires_at__lt=now))
    for row in expired:
        saved = row.output is not None or bool(row.error)
        changed = rows.filter(pk=row.pk, state=row.state, task_id=row.task_id).update(
            state=EvalRunRow.State.DONE if saved else EvalRunRow.State.UNKNOWN, finished_at=now
        )
        if changed and not saved:
            rows.filter(pk=row.pk, error="").update(
                error="generation outcome unknown after worker deadline; not replayed"
                if row.state == EvalRunRow.State.RUNNING
                else "generation outcome unknown: dispatch expired before worker claim"
            )

    running = EvalRun.objects.filter(status=EvalRun.Status.RUNNING)
    ready = running.filter(scoring_task_id="")
    unfinished = rows.filter(run_id=OuterRef("pk"), state__in=UNFINISHED)
    scoring = 0
    for run in ready.annotate(unfinished=Exists(unfinished)).filter(unfinished=False):
        task_id = str(uuid.uuid4())
        EvalRun.objects.filter(pk=run.pk).update(scoring_task_id=task_id, updated_at=now)
        # Consumers check the committed token; a publish/commit ambiguity cannot start work.
        publish_scoring(str(run.pk), task_id)
        scoring += 1

    cap = max(1, int(getattr(settings, "EVAL_MAX_IN_FLIGHT", 12)))
    per_run = max(1, int(getattr(settings, "EVAL_MAX_IN_FLIGHT_PER_RUN", 2)))
    per_project = max(1, int(getattr(settings, "EVAL_MAX_IN_FLIGHT_PER_PROJECT", 4)))
    available = max(0, cap - rows.filter(state__in=IN_FLIGHT).count())
    own = rows.filter(run_id=OuterRef("pk"))
    own_active = (
        own.filter(state__in=IN_FLIGHT).values("run_id").annotate(total=Count("pk")).values("total")
    )
    project_active = (
        rows.filter(run__project_id=OuterRef("project_id"), state__in=IN_FLIGHT)
        .values("run__project_id")
        .annotate(total=Count("pk"))
        .values("total")
    )
    project_last = (
        running.filter(project_id=OuterRef("project_id"))
        .values("project_id")
        .annotate(latest=Max("last_admitted_at"))
        .values("latest")
    )
    admitted = 0
    while admitted < available:
        run = (
            ready.annotate(
                has_waiting=Exists(own.filter(state=EvalRunRow.State.WAITING)),
                active=Coalesce(Subquery(own_active, output_field=IntegerField()), 0),
                project_last=Subquery(project_last),
                project_active=Coalesce(Subquery(project_active, output_field=IntegerField()), 0),
            )
            .filter(has_waiting=True, active__lt=per_run, project_active__lt=per_project)
            .order_by(
                F("project_last").asc(nulls_first=True),
                F("last_admitted_at").asc(nulls_first=True),
                "created_at",
                "id",
            )
            .first()
        )
        if run is None:
            break
        row = (
            rows.filter(run_id=run.pk, state=EvalRunRow.State.WAITING).order_by("row_index").first()
        )
        if row is None:
            break
        queued_at = timezone.now()
        task_id = str(uuid.uuid4())
        rows.filter(pk=row.pk).update(
            task_id=task_id,
            state=EvalRunRow.State.QUEUED,
            queued_at=queued_at,
            expires_at=queued_at + QUEUED_LEASE,
        )
        EvalRun.objects.filter(pk=run.pk).update(last_admitted_at=queued_at, updated_at=queued_at)
        publish(str(row.pk), task_id)
        admitted += 1
    return {"admitted": admitted, "scoring": scoring, "expired": len(expired)}


@transaction.atomic
def claim(row_id, task_id) -> bool:
    row = (
        EvalRunRow.objects.select_for_update()
        .select_related("run")
        .filter(pk=row_id, run__status=EvalRun.Status.RUNNING)
        .first()
    )
    if row is None or row.state != EvalRunRow.State.QUEUED or row.task_id != task_id:
        return False
    now = timezone.now()
    if row.expires_at is not None and row.expires_at <= now:
        return False
    EvalRunRow.objects.filter(pk=row.pk).update(
        state=EvalRunRow.State.RUNNING, started_at=now, expires_at=now + RUNNING_LEASE
    )
    return True


def finish(row_id, task_id) -> int:
    return EvalRunRow.objects.filter(
        pk=row_id, task_id=task_id, state=EvalRunRow.State.RUNNING
    ).update(state=EvalRunRow.State.DONE, finished_at=timezone.now())


@transaction.atomic
def begin_scoring(run_id, task_id) -> bool:
    run = EvalRun.objects.select_for_update().filter(pk=run_id).first()
    if run is None or run.is_terminal:
        return False
    if run.scoring_task_id != task_id or run.scoring_started_at is not None:
        return False
    EvalRun.objects.filter(pk=run.pk).update(scoring_started_at=timezone.now())
    return True


def wait_is_healthy(run_id) -> bool:
    if not EvalRunRow.objects.filter(run_id=run_id, state__in=UNFINISHED).exists():
        return False
    return EvalGenerationScheduler.objects.filter(
        pk=1, last_tick_at__gte=timezone.now() - SCHEDULER_HEALTHY
    ).exists()


def model_of(run: EvalRun) -> tuple[str | None, ModelSpec | None]:
    """Catalog providers without a custom endpoint take the name path."""
    ref = run.model_ref
    if ref is None:
        return (run.model_name or None), None
    if ref.provider in ("openai", "anthropic", "gemini") and not ref.base_url:
        return ref.model_id, None
    return None, ModelSpec(
        provider=ref.provider,
        model_id=ref.model_id,
        base_url=ref.base_url,
        api_key_env=ref.api_key_ref,
        params=ref.params or {},
    )


def warm(run: EvalRun) -> None:
    """A freshly deployed fine-tuned model cold-starts for minutes on its first request;
    paying that on the untimed run task keeps it off every row's clock."""
    model_name, spec = model_of(run)
    if spec is None:
        return
    try:
        call_llm("ping", model=model_name, model_spec=spec, max_tokens=1)
    except Exception as exc:  # noqa: BLE001 — warmup is best-effort
        logger.warning("model warmup failed for %s: %s", spec.model_id, exc)


def prepared(row: row_store.DatasetRow) -> row_store.DatasetRow:
    """Rebinds a reference carried on the input, so the input never holds the gold."""
    inp, expected = bind_eval_reference(row.input, row.expected_output)
    if inp is row.input and expected is row.expected_output:
        return row
    return replace(row, input=inp, expected_output=expected)


def reference_of(row: row_store.DatasetRow, mode: str) -> Any:
    """A capability-surface row keeps the raw model answer apart from the delivered
    one; a generated output is compared with the raw model answer."""
    model_expected = (row.extra or {}).get("model_expected_output")
    if mode == EvalRun.Mode.GENERATE and model_expected is not None:
        return model_expected
    return row.expected_output


def recorded_output(row: row_store.DatasetRow) -> Any:
    messages = chatml.parse_messages(row.input) or []
    if messages and messages[-1].get("role") == "assistant":
        return messages[-1]
    return row.expected_output


def grades_model_surface(run: EvalRun) -> bool:
    """A cell holding only the model transcript grades the raw model output, so a
    trace is reconstructed without promoting the harness deliverable."""
    names = {column.get("name") for column in (run.cell.columns or [])} if run.cell_id else set()
    return "messages" in names and "input" not in names


@dataclass
class Seed:
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_defs: list[dict[str, Any]] = field(default_factory=list)
    provider: replay.ReplayToolProvider = field(default_factory=replay.ReplayToolProvider)


def domain_seed(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str) and value.strip():
        return [{"role": "user", "content": value.strip()}]
    if value is not None:
        content = json.dumps(value, default=str)
        return [{"role": "user", "content": content}] if content else []
    return []


def text_tool_context(messages: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """A dataset storing tool calls as text (``[FunctionName(args)]``) gives the model
    no schema and the replay no graph; the calls are scraped into minimal schemas and
    ordered ``{name, arguments, result}`` records."""
    if not normalizer.has_text_tool_calls(messages):
        return [], []
    tools: dict[str, dict[str, Any]] = {}
    recorded: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        calls = normalizer.parse_text_tool_calls(message.get("content") or "")
        if not calls:
            continue
        results: list[str | None] = []
        for later in messages[index + 1 :]:
            if later.get("role") == "tool":
                results.append(later.get("content"))
            elif later.get("role") == "assistant":
                break
        for position, call in enumerate(calls):
            original = call["name"]
            name = chatml.sanitize_tool_name(original)
            recorded.append(
                {
                    "name": name,
                    "original_name": original,
                    "arguments": call["arguments"],
                    "result": results[position] if position < len(results) else None,
                }
            )
            tools.setdefault(
                name,
                {
                    "name": name,
                    "description": f"Tool: {original}",
                    "parameters": {"type": "object", "properties": {}},
                },
            )
    return list(tools.values()), recorded


def wire_messages(prefix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI requires exactly N tool messages after an assistant with N tool_calls:
    calls are trimmed to the results present, missing ids are synthesised in order,
    and tool replies to text-format calls are dropped as orphans."""
    results_after: list[int] = []
    for index, message in enumerate(prefix):
        count = 0
        if message.get("role") == "assistant" and message.get("tool_calls"):
            for later in prefix[index + 1 :]:
                if later.get("role") == "tool":
                    count += 1
                elif later.get("role") == "assistant":
                    break
        results_after.append(count)

    seed: list[dict[str, Any]] = []
    pending: list[str] = []
    calls_open = False
    for index, message in enumerate(prefix):
        role = message.get("role", "")
        if role not in ("system", "user", "assistant", "tool"):
            continue
        entry: dict[str, Any] = {"role": role, "content": message.get("content") or ""}
        if role == "assistant":
            pending, calls = [], []
            for call in message.get("tool_calls") or []:
                if len(calls) >= results_after[index]:
                    break
                function = call.get("function") if isinstance(call.get("function"), dict) else call
                name = function.get("name", "")
                if not name:
                    continue
                arguments = function.get("arguments", {})
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, ensure_ascii=False)
                call_id = call.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                pending.append(call_id)
                calls.append(
                    {
                        "id": call_id,
                        "type": call.get("type", "function"),
                        "function": {"name": name, "arguments": arguments},
                    }
                )
            if calls:
                entry["tool_calls"] = calls
            calls_open = bool(calls)
        if role == "tool":
            if not calls_open:
                continue
            call_id = message.get("tool_call_id") or (pending.pop(0) if pending else None)
            if call_id:
                entry["tool_call_id"] = call_id
            if message.get("name"):
                entry["name"] = message["name"]
        seed.append(entry)
    return seed


def row_seed(row: row_store.DatasetRow) -> Seed:
    """The request the model under test answers. Replay material comes from the full
    transcript, so recorded tool results answer the model's calls."""
    messages = chatml.parse_messages(row.input)
    if not messages:
        return Seed(domain_seed(row.input))
    prefix = messages
    if messages[-1].get("role") == "assistant" and (
        (row.extra or {}).get("messages")
        or row.expected_output is None
        or matches_reference(messages[-1], row.expected_output)
        or matches_reference(messages[-1], (row.extra or {}).get("model_expected_output"))
    ):
        prefix = messages[:-1]
    raw_tools = row.input.get("tools", []) if isinstance(row.input, dict) else []
    tool_defs = chatml.parse_tool_definitions(raw_tools)
    full = normalizer.normalize_messages(row.input)
    structured = normalizer.structure_trajectory(full)
    if not tool_defs:
        normalizer.synthesize_tool_definitions(full)
        tool_defs = full.get("tool_definitions", [])
    if (structured.get("tool_graph") or {}).get("nodes"):
        provider = replay.ReplayToolProvider.from_structured(structured, tool_defs)
    else:
        text_defs, recorded = text_tool_context(messages)
        tool_defs = tool_defs or text_defs
        provider = replay.ReplayToolProvider(recorded_calls=recorded, tool_defs=tool_defs)
    return Seed(wire_messages(prefix), tool_defs, provider)


def trace_seed(run: EvalRun, trace_id: str) -> Seed:
    spans = list(Span.objects.filter(trace_id=trace_id, project_id=run.project_id))
    source = normalizer.reconstruct_spans(spans, promote_harness=not grades_model_surface(run))
    tool_defs = source.get("tool_definitions", [])
    provider = replay.ReplayToolProvider.from_structured(
        normalizer.structure_trajectory(source), tool_defs
    )
    seed: list[dict[str, Any]] = []
    for message in source.get("messages", []):
        if message.get("role") == "assistant":
            break
        if message.get("role") in ("system", "user"):
            seed.append({"role": message["role"], "content": message.get("content", "")})
    return Seed(seed, tool_defs, provider)


def output_of(messages: list[dict[str, Any]]) -> Any:
    """The final answer text, or the assistant/tool turns when the model called tools."""
    if any(message.get("tool_calls") for message in messages):
        return messages
    answers = [m for m in messages if m.get("role") == "assistant" and m.get("content")]
    return answers[-1]["content"] if answers else None


def degradation(result: replay.RunResult, output: Any) -> str:
    if result.truncated:
        return "output_token_limit: model response ended before completion"
    if result.error:
        return f"generation_error: {result.error[:210]}"
    if output in (None, "", []):
        return "no_final_output: the model produced an empty output"
    if result.tool_misses:
        return f"replay_misses: {result.tool_misses} recorded tool result(s) unmatched in replay"
    return ""


def generate(run_row: EvalRunRow) -> None:
    """Generates one row's output and saves it; raises when nothing was produced."""
    run = run_row.run
    ensure_run_active(run.pk)
    row = row_store.row(run.cell, run_row.row_index)
    if row is None:
        raise RuntimeError("the row is missing from the pinned frame")
    seed = row_seed(prepared(row))
    if run_row.source_trace_id and not seed.provider.recorded_calls:
        traced = trace_seed(run, run_row.source_trace_id)
        seed = Seed(
            seed.messages or traced.messages, seed.tool_defs or traced.tool_defs, traced.provider
        )
    if not seed.messages:
        raise RuntimeError("the row has no request")

    params = run.params or {}
    model_name, model_spec = model_of(run)
    common = {
        "input_messages": seed.messages,
        "model": model_name,
        "model_spec": model_spec,
        "system_prompt": params.get("system_prompt"),
        "reasoning_effort": params.get("reasoning_effort"),
        "project_id": str(run.project_id),
    }
    strategy = params.get("generation_strategy") or "replay"
    if strategy == "single_completion":
        result = replay.generate_decision(
            tool_provider=replay.ReplayToolProvider(tool_defs=seed.tool_defs), **common
        )
    else:
        result = replay.run_capability(
            tool_provider=seed.provider,
            max_steps=replay.resolve_max_steps(
                recorded_tool_calls=len(seed.provider.recorded_calls),
                override=params.get("max_steps"),
            ),
            check_active=lambda: ensure_run_active(run.pk),
            **common,
        )
    if result.error and not result.output_messages:
        raise RuntimeError(f"generation failed: {result.error}")

    output = output_of(result.output_messages)
    reason = degradation(result, output)
    EvalRunRow.objects.filter(pk=run_row.pk).exclude(run__status__in=EvalRun.TERMINAL).update(
        output=output,
        degraded=bool(reason),
        degraded_reason=reason,
        metadata={
            "model": run.resolved_model,
            "generation_strategy": strategy,
            "cost": result.cost,
            "latency_ms": result.latency_ms or None,
            "prompt_tokens": result.prompt_tokens or None,
            "completion_tokens": result.completion_tokens or None,
            "steps": result.steps,
            "replay_misses": result.tool_misses,
            "finish_reasons": result.finish_reasons,
            "truncated": result.truncated,
            "context_checks": result.context_checks,
        },
    )
    EvalRun.objects.filter(pk=run.pk, status=EvalRun.Status.RUNNING).update(
        updated_at=timezone.now()
    )
