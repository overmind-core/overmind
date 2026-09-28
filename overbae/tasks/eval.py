"""Eval pipeline fan-out:

    run_eval_run -> chord(group(prepare_sample ...)) -> launch_evaluation
                 -> chord(group(execute_evaluator ...)) -> aggregate_run

``execute_evaluator`` is idempotent per ``(sample, evaluator)`` so retries never double-write.
"""

from __future__ import annotations

import logging
import random
import uuid
from typing import Any

from celery import chain, chord, group, shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.db import transaction
from django.utils import timezone

from overbae.core.llms import ModelSpec
from overbae.models import EvalRun, FinetuningJob
from overbae.services.datasets.examples import matches_reference
from overbae.services.eval import chatml, evidence, normalizer, ranking, runner
from overbae.services.eval.context import snapshot_context
from overbae.services.eval.evaluators import base as eval_base
from overbae.services.eval.evaluators import statistical
from overbae.services.eval.sampling import select_rows

logger = logging.getLogger(__name__)

_TERMINAL_FAIL = "failed"

# Stamped on the ``reasoning`` of Scores created when an evaluator call raises. Read
# both at creation and when counting execution failures, so the two never drift.
# Legitimate abstains carry different reasoning and are NOT matched.
_EVALUATOR_ERROR_PREFIX = "evaluator error:"


def _resolve_outcome(value: float | None, declared: str) -> str:
    """Enforces the ``value is None`` ⇔ ``outcome != scored`` invariant, defaulting a null value
    to ``ABSTAINED`` when the producer declared nothing."""
    if value is not None:
        return eval_base.OUTCOME_SCORED
    if declared and declared != eval_base.OUTCOME_SCORED:
        return declared
    return eval_base.OUTCOME_ABSTAINED


def _fail_run(eval_run_id: str, reason: str) -> int:
    """Only flips runs still in flight, so a COMPLETED run is never clobbered and a re-fired
    error callback is a no-op. Returns 1 when this call made the transition, 0 otherwise."""
    from overbae.models import EvalRun

    terminal = [
        EvalRun.Status.COMPLETED,
        EvalRun.Status.FAILED,
        EvalRun.Status.CANCELLED,
    ]
    now = timezone.now()
    flipped = (
        EvalRun.objects.filter(pk=eval_run_id)
        .exclude(status__in=terminal)
        .update(
            status=EvalRun.Status.FAILED,
            error=reason,
            completed_at=now,
            updated_at=now,
        )
    )
    return flipped


def revoke_run_tasks(run) -> int:
    """Targets ``run_eval_run`` plus every sample's ``prepare_sample``, by the ids fixed at
    dispatch. ``terminate=True`` kills a running task under prefork and stops a not-yet-started
    one under any pool; under ``--pool=threads`` an already-executing task cannot be signalled
    and only the per-request LLM socket timeout bounds it."""
    from celery import current_app

    task_ids = [run.celery_task_id] if run.celery_task_id else []
    task_ids += list(
        run.samples.exclude(celery_task_id="").values_list("celery_task_id", flat=True)
    )
    task_ids = [tid for tid in task_ids if tid]
    if task_ids:
        current_app.control.revoke(task_ids, terminate=True, signal="SIGTERM")
    return len(task_ids)


def cancel_run(run) -> int:
    """Cancel an eval run and return the number of revoked tasks."""
    from overbae.models import EvalRun

    EvalRun.objects.filter(pk=run.pk).update(status=EvalRun.Status.CANCELLED)
    revoked = revoke_run_tasks(run)
    # Stamp still-pending samples, or the UI and aggregates read them as genuine
    # results once their revoked generation never lands.
    run.samples.filter(error="", trajectory={}).update(error="cancelled")
    return revoked


def _link_failure(sig, eval_run_id: str):
    """A fresh ``handle_eval_failure`` signature is bound per task, so one header-task failure
    still routes to the failure path when the chord barrier never fires."""
    sig.link_error(handle_eval_failure.s(eval_run_id=eval_run_id))
    return sig


@shared_task(name="overbae.tasks.eval.handle_eval_failure")
def handle_eval_failure(*args, eval_run_id: str, **kwargs) -> dict[str, Any]:
    """Without this, a header task that exhausts its retries leaves the run wedged in RUNNING.
    Celery passes errbacks different positional args across versions (``(request, exc, traceback)``
    or ``(task_id,)``), hence ``*args``. The flip to FAILED is idempotent."""
    detail = next((str(a) for a in args if isinstance(a, BaseException)), None)
    reason = f"evaluation task failed: {detail}" if detail else "evaluation task failed"
    _fail_run(eval_run_id, reason)
    return {"status": "failed", "eval_run_id": eval_run_id}


@shared_task(
    name="overbae.tasks.eval.preload_capability_eval_set",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def preload_capability_eval_set(self, *, capability_id: str, **kwargs) -> dict[str, Any]:  # noqa: ARG001
    """Its own task so slow LLM authoring never blocks a code scan reaching READY. Idempotent: an
    already-populated set is left untouched, so a re-scan is a cheap no-op."""
    from overbae.models import Capability
    from overbae.services.eval.eval_set import generate_and_preload_default_set
    from overbae.services.eval.preload_status import (
        STATUS_FAILED,
        STATUS_RUNNING,
        preload_counts_from_result,
        terminal_status_from_result,
        write_eval_preload,
    )

    try:
        capability = (
            Capability.objects.select_related("project", "active_eval_set")
            .current()
            .get(id=capability_id)
        )
    except Capability.DoesNotExist:
        return {"error": f"capability {capability_id} not found"}

    write_eval_preload(capability, status=STATUS_RUNNING)

    try:
        result = generate_and_preload_default_set(capability)
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            capability.refresh_from_db(fields=["improvement_metadata"])
            write_eval_preload(capability, status=STATUS_FAILED, error=str(exc))
        raise

    capability.refresh_from_db(fields=["improvement_metadata"])
    write_eval_preload(
        capability,
        status=terminal_status_from_result(result),
        counts=preload_counts_from_result(result) or None,
    )
    return result


def enqueue_capability_eval_preload(capability) -> None:
    from overbae.services.eval.preload_status import STATUS_PENDING, write_eval_preload

    write_eval_preload(capability, status=STATUS_PENDING)
    preload_capability_eval_set.delay(capability_id=str(capability.id))


def enqueue_capability_eval_preload_on_commit(capability) -> None:
    """After sync commits — same hook the retired server scan used post-persist."""
    cap_id = str(capability.id)

    def _send():
        from overbae.models import Capability

        row = Capability.objects.filter(pk=cap_id).first()
        if row is not None:
            enqueue_capability_eval_preload(row)

    transaction.on_commit(_send)


@shared_task(
    name="overbae.tasks.eval.sync_card_evaluators_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def sync_card_evaluators_task(self, *, capability_id: str, **kwargs) -> dict[str, Any]:  # noqa: ARG001
    """Tier-0 recompile on card change (signal-fired). No LLM calls; idempotent."""
    from overbae.models import Capability
    from overbae.services.eval.eval_set import sync_card_evaluators

    capability = Capability.objects.filter(id=capability_id, status="current").first()
    if capability is None:
        return {"error": f"capability {capability_id} not found"}
    return sync_card_evaluators(capability)


@shared_task(name="overbae.tasks.eval.sweep_eval_bindings")
def sweep_eval_bindings(*, dataset_id: str) -> dict[str, Any]:
    """First-data binding sweep: record binding health on authored evaluators.
    A miss is not-applicable at grade time. Idempotent (metadata stamp)."""
    from overbae.models import Dataset
    from overbae.services.eval.binding_check import sweep_dataset_bindings

    dataset = Dataset.objects.filter(id=dataset_id).first()
    if dataset is None:
        return {"error": f"dataset {dataset_id} not found"}
    return sweep_dataset_bindings(dataset)


def _attach_per_turn_judge(run, variants) -> None:
    """Attach the capability's per-turn judge when a run uses teacher-forced replay."""
    from overbae.services.eval import per_turn_judge, snapshots
    from overbae.services.eval.per_turn_judge import JUDGE_NAME

    replay = any(
        (v.params or {}).get("generation_strategy") == "per_assistant_turn" for v in variants
    )
    if not replay or not run.dataset_id:
        return
    if run.run_evaluators.filter(snapshot__name=JUDGE_NAME).exists():
        return
    # Per-run dimension selection rides in the variant params: ``None`` means use the
    # capability default, ``[]`` means the user turned every dimension off — attach nothing.
    selected = next(
        (
            (v.params or {}).get("replay_dimensions")
            for v in variants
            if (v.params or {}).get("replay_dimensions") is not None
        ),
        None,
    )
    if selected is not None and len(selected) == 0:
        return
    try:
        evaluator = per_turn_judge.author_per_turn_judge(run.dataset)
    except Exception:  # noqa: BLE001 — additive; never fail the run over the judge
        logger.exception("per-turn judge authoring failed for run %s", run.id)
        return
    if evaluator is None:
        return
    from overbae.models import RunEvaluator

    snapshot = snapshots.build_snapshot(evaluator, judge_model=run.judge_model)
    if selected:
        snapshot = {
            **snapshot,
            "config": {**(snapshot.get("config") or {}), "dimensions": list(selected)},
        }
    RunEvaluator.objects.create(
        run=run,
        evaluator=evaluator,
        snapshot=snapshot,
        order=run.run_evaluators.count(),
    )


@shared_task(name="overbae.tasks.eval.run_eval_run")
def run_eval_run(*, eval_run_id: str, **kwargs) -> dict[str, Any]:
    from overbae.models import EvalRun, EvalSample

    try:
        run = EvalRun.objects.select_related("dataset__capability", "eval_set__capability").get(
            id=eval_run_id
        )
    except EvalRun.DoesNotExist:
        return {"error": f"EvalRun {eval_run_id} not found"}

    # Stamp ``updated_at`` by hand: the pipeline mutates the row via ``QuerySet.update()``,
    # which bypasses ``auto_now``, and the stall watchdog reads this as the start time.
    EvalRun.objects.filter(pk=run.pk).update(
        status=EvalRun.Status.RUNNING, error="", updated_at=timezone.now()
    )

    try:
        items = _resolve_items(run)
        variants = list(run.variants.select_related("prompt", "model_ref"))
        if not variants:
            raise ValueError("EvalRun has no variants")
        if not items:
            raise ValueError("No items resolved from the data source")

        snapshot_context(run, variants)

        _attach_per_turn_judge(run, variants)

        # A re-fired run_eval_run (duplicate dispatch, retry, manual re-launch) must
        # not stack a second set of samples on the first — rebuild from a clean slate.
        if run.samples.exists():
            run.samples.all().delete()
            run.scores.all().delete()

        # Pay a Modal-hosted model's cold start here, off the per-sample clock, instead
        # of inside the first prepare_sample where it blows the time limit.
        _warm_generate_models(variants)

        run_id = str(run.id)
        # Each prepare_sample's task id is fixed at dispatch and persisted on the sample,
        # so ``cancel`` can revoke the in-flight task instead of leaking a hung generation.
        header = []
        for variant in variants:
            is_generate = variant.mode == variant.Mode.GENERATE
            for item in items:
                sample = EvalSample.objects.create(
                    run=run,
                    variant=variant,
                    row_index=item.get("row_index"),
                    source_trace_id=item.get("source_trace_id", ""),
                    expected=_sample_reference(item, is_generate=is_generate),
                )
                task_id = str(uuid.uuid4())
                EvalSample.objects.filter(pk=sample.pk).update(celery_task_id=task_id)
                sig = _link_failure(prepare_sample.s(sample_id=str(sample.id)), run_id)
                sig.set(task_id=task_id)
                header.append(sig)

        callback = _link_failure(launch_evaluation.s(eval_run_id=run_id), run_id)
        chord(group(header))(callback)
        return {"status": "running", "samples": len(header), "variants": len(variants)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("run_eval_run failed for %s", eval_run_id)
        EvalRun.objects.filter(pk=run.pk).update(status=_TERMINAL_FAIL, error=str(exc))
        return {"error": str(exc)}


def _preseeded_pairs(run) -> list[tuple[str, str]]:
    """Mirrors ``launch_evaluation``'s pairing: a prompt-scoped evaluator grades only its own
    prompt's samples, a global one (prompt null) grades all."""
    sample_rows = list(run.samples.values_list("id", "variant__prompt_id"))
    run_evals = list(run.run_evaluators.filter(enabled=True).values_list("id", "prompt_id"))
    global_reids = [str(reid) for reid, pid in run_evals if pid is None]
    reids_by_prompt: dict[str, list[str]] = {}
    for reid, pid in run_evals:
        if pid is not None:
            reids_by_prompt.setdefault(str(pid), []).append(str(reid))
    return [
        (str(sid), reid)
        for sid, prompt_id in sample_rows
        for reid in global_reids + reids_by_prompt.get(str(prompt_id), [])
    ]


def dispatch_preseeded_eval_run(run, *, on_complete=None) -> dict[str, Any]:
    """Async sibling of :func:`run_eval_run` for a run whose samples the caller already seeded:
    skips item resolution and the prepare phase and fans out only the scoring chord. Chains an
    optional ``on_complete`` signature after aggregation to wake the caller. The evaluator engine
    and aggregation are reused verbatim, so the run grades like any other."""
    from overbae.models import EvalRun

    run_id = str(run.id)
    EvalRun.objects.filter(pk=run.pk).update(
        status=EvalRun.Status.RUNNING, error="", updated_at=timezone.now()
    )
    pairs = _preseeded_pairs(run)
    aggregate = aggregate_run.s(eval_run_id=run_id)
    callback = chain(aggregate, on_complete) if on_complete is not None else aggregate
    callback = _link_failure(callback, run_id)
    if not pairs:
        # Finalize directly so ``on_complete`` still fires and the caller is never
        # left waiting on a run that cannot score.
        callback.apply_async(args=(None,))
        return {"status": "empty"}
    header = [
        _link_failure(execute_evaluator.s(sample_id=sid, run_evaluator_id=reid), run_id)
        for sid, reid in pairs
    ]
    chord(group(header))(callback)
    return {"status": "evaluating", "tasks": len(header)}


def _verify_pinned_version(run) -> None:
    """The product file must still match the hash the run pinned."""
    from overbae.services.datasets import rows as row_store

    if run.cell_id is None:
        raise RuntimeError("This run has no pinned dataset version.")
    row_store.verify(run.cell)


def _sample_reference(item: dict[str, Any], *, is_generate: bool) -> Any:
    """The golden this sample is graded against.

    Surface-aware. An CAPABILITY-surface dataset keeps the delivered (harness) answer
    in ``expected`` and the raw model output in ``model_expected``; a
    model-generation run re-runs the RAW model, so it must grade
    ``model_expected`` or it would compare model-vs-harness (the deliverable has
    fields the raw model never emits, guaranteeing false misses). A MODEL-surface
    dataset already stores the raw model output in ``expected`` and carries no
    ``model_expected``, so it grades that directly. Trace/existing runs always
    grade the delivered ``expected``. (Keyed on ``model_expected`` presence — set
    only for capability-surface eval rows — so legacy rows keep their prior behavior.)
    """
    model_expected = item.get("model_expected")
    if is_generate and model_expected is not None:
        return model_expected
    return item.get("expected")


def _grades_model_surface(sample) -> bool:
    """A product that carries only the model transcript (``messages`` without a
    capability ``input``) grades the raw model output, so its traces must be
    reconstructed WITHOUT promoting the harness deliverable — otherwise EXISTING-mode
    grading compares harness output against a model-surface reference and misses."""
    run = getattr(sample, "run", None)
    version = getattr(run, "cell", None) if getattr(run, "cell_id", None) else None
    if version is None:
        return False
    names = {c.get("name") for c in (version.columns or [])}
    return "messages" in names and "input" not in names


def _resolve_items(run) -> list[dict[str, Any]]:
    from overbae.models import Span

    sampling = run.sampling if 0 < run.sampling <= 1 else 1.0
    rng = random.Random(str(run.id))

    if run.data_source == run.DataSource.DATASET and run.dataset_id:
        from overbae.services.eval.profiler import bind_eval_reference

        _verify_pinned_version(run)
        items = []
        for dp in select_rows(run.cell, limit=run.max_items, fraction=sampling):
            # Object rows can still carry ``answer``/``gold`` on input; rebound
            # so ``{input}`` never contains the gold the judge is scoring against.
            inp, expected = bind_eval_reference(dp.input, dp.expected_output)
            items.append(
                {
                    "row_index": dp.index,
                    "source_trace_id": dp.source_trace_id or "",
                    "input": inp,
                    "expected": expected,
                    # Raw model-surface golden, captured at ingest only for capability-surface
                    # rows, so a model-generation run compares model-vs-model. Absent on
                    # model-surface rows, where `expected` already is it.
                    "model_expected": (dp.extra or {}).get("model_expected_output"),
                }
            )
        return items

    flt = run.trace_filter or {}
    qs = Span.objects.filter(project=run.project, parent_span_id__isnull=True)
    for key in ("service_name", "capability_id", "span_type", "trace_id"):
        if flt.get(key):
            qs = qs.filter(**{key: flt[key]})
    qs = qs.order_by("-start_time_ns")
    if run.max_items:
        qs = qs[: run.max_items]
    items = []
    for root in qs:
        if sampling < 1.0 and rng.random() > sampling:
            continue
        items.append(
            {
                "row_index": None,
                "source_trace_id": root.trace_id,
                "input": None,
                "expected": None,
            }
        )
    return items


# ``acks_late`` + ``reject_on_worker_lost``: this is a chord header task, so one lost
# to a worker restart or crash would never decrement the chord counter and the run
# would hang forever in "scoring". Redelivery is safe — the task is idempotent, it
# overwrites the sample's trajectory.
# The time limits are the backstop under the per-request socket timeout: a slow chain
# or stuck provider raises SoftTimeLimitExceeded (caught below) instead of pinning a
# worker thread. They are sized for teacher-forced replay, which chains one LLM call
# per turn and may cold-start a fine-tuned model on Modal. Celery enforces them only
# under the prefork pool; under ``--pool=threads`` the socket timeout frees the thread.
@shared_task(
    name="overbae.tasks.eval.prepare_sample",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
    soft_time_limit=1200,
    time_limit=1320,
)
def prepare_sample(self, *, sample_id: str, **kwargs) -> str:
    from overbae.models import EvalSample, Span

    sample = EvalSample.objects.select_related("variant", "run__dataset", "run__cell").get(
        id=sample_id
    )
    variant = sample.variant
    datapoint = _sample_row(sample)

    try:
        if variant.mode == variant.Mode.EXISTING and sample.source_trace_id:
            spans = list(
                Span.objects.filter(trace_id=sample.source_trace_id, project=sample.run.project)
            )
            normalized = normalizer.reconstruct_spans(
                spans, promote_harness=not _grades_model_surface(sample)
            )
            # Fall back to the datapoint when the source trace is unavailable or
            # degraded: it holds the canonical transform_trace transcript, so an empty
            # trajectory is never graded while a correct one exists.
            if not normalized.get("messages") and datapoint is not None:
                normalized = normalize_datapoint(datapoint)
        elif variant.mode == variant.Mode.GENERATE:
            normalized = _generate_sample(sample)
        elif datapoint is not None:
            # Existing mode with no trace: no model ran, so evaluators fall back to
            # reference-based scoring against the expected output.
            normalized = normalize_datapoint(datapoint)
        else:
            normalized = normalizer.normalize_spans([])

        structured = normalizer.structure_trajectory(normalized)
        # Hoist per-turn scoring payloads onto ``structured``, where evaluators read
        # them, and off the trajectory blob. Only per_assistant_turn runs have them.
        _meta = normalized.get("metadata") or {}
        if "per_turn_scoring" in _meta:
            structured["per_turn"] = _meta.pop("per_turn_scoring")
        # Drop the heavy raw tool-span blob before persisting, but read it for the
        # trust assessment first.
        raw_tool_spans = normalized.pop("_raw_tool_spans", None) or []
        degraded, degraded_reason = _assess_degradation(
            normalized,
            structured,
            raw_tool_spans,
            is_generate=variant.mode == variant.Mode.GENERATE,
        )
        EvalSample.objects.filter(pk=sample.pk).update(
            trajectory=normalized,
            structured=structured,
            degraded=degraded,
            degraded_reason=degraded_reason,
        )
    except SoftTimeLimitExceeded:
        # Caught, not re-raised, so it bypasses ``autoretry_for``: retrying a timed-out
        # generation re-enters the same hang. The run summary counts the errored sample.
        logger.warning("prepare_sample timed out for %s", sample_id)
        EvalSample.objects.filter(pk=sample.pk).update(
            error="generation timed out (exceeded soft_time_limit)"
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("prepare_sample failed for %s", sample_id)
        EvalSample.objects.filter(pk=sample.pk).update(error=str(exc))
    finally:
        _record_generation_activity(sample.run_id)
    return sample_id


def _record_generation_activity(run_id) -> None:
    # QuerySet.update bypasses auto_now; scores do not exist during generation.
    EvalRun.objects.filter(pk=run_id, status=EvalRun.Status.RUNNING).update(
        updated_at=timezone.now()
    )


def _assess_degradation(
    normalized: dict[str, Any],
    structured: dict[str, Any],
    raw_tool_spans: list[dict[str, Any]],
    *,
    is_generate: bool = False,
) -> tuple[bool, str]:
    """Returns ``(degraded, reason)``. Two non-obvious calls: a ``generate`` variant with an empty
    final output is ALWAYS degrading, because the model produced nothing and a deterministic
    grader must not score that a genuine 0.0; and a ``root_io`` extraction path is NOT degrading,
    because it lifts the answer from the root span rather than an intermediate lane."""
    meta = normalized.get("metadata") or {}
    num_calls = int(structured.get("num_tool_calls", 0) or 0)
    raw_count = len(raw_tool_spans)
    replay_misses = int(meta.get("replay_misses") or 0)
    final_output = (normalized.get("final_output") or "").strip()

    if meta.get("output_truncated"):
        return True, "output_token_limit: model response ended before completion"
    if is_generate and meta.get("generation_error"):
        return True, "generation_error: " + str(meta["generation_error"])[:210]

    if meta.get("generation_strategy") == "per_assistant_turn":
        if any(
            t.get("generated") or t.get("generated_final") for t in structured.get("per_turn", [])
        ):
            return False, ""
        return True, "no_decisions: per-turn generation produced no scoreable decisions"

    if raw_count > 0 and num_calls == 0:
        return True, f"tools_lost: {raw_count} tool span(s) present but 0 reconstructed"
    if not final_output and (num_calls > 0 or raw_count > 0):
        return True, "no_final_output: tool/lane activity present but no extractable final answer"
    if is_generate and not final_output:
        return True, "no_final_output: generate variant produced an empty final output"
    if replay_misses > 0:
        return True, f"replay_misses: {replay_misses} recorded tool result(s) unmatched in replay"
    return False, ""


def _sample_row(sample):
    """The pinned product row a sample grades, or None for trace-filter samples."""
    from dataclasses import replace

    from overbae.services.datasets import rows as row_store
    from overbae.services.eval.profiler import bind_eval_reference

    if sample.row_index is None or sample.run.cell_id is None:
        return None
    row = row_store.row(sample.run.cell, sample.row_index)
    if row is None:
        return None
    inp, expected = bind_eval_reference(row.input, row.expected_output)
    if inp is row.input and expected is row.expected_output:
        return row
    return replace(row, input=inp, expected_output=expected)


def normalize_datapoint(dp) -> dict[str, Any]:
    """Accepts a ChatML message list, a plain string or a JSON object. When ``expected_output``
    is present but the input carries no assistant turn, one is synthesised from it so
    ``final_output`` is non-empty and reference-based scoring works without a live model run."""
    import json as _json  # noqa: PLC0415

    from overbae.services.eval.profiler import bind_eval_reference

    inp, expected = bind_eval_reference(dp.input, dp.expected_output)

    msgs = normalizer.chatml.parse_messages(inp)
    if msgs:
        normalized = normalizer.normalize_messages(inp)
    else:
        if isinstance(inp, str):
            content = inp.strip()
        else:
            content = _json.dumps(inp, default=str) if inp is not None else ""
        msgs_manual = [{"role": "user", "content": content}]
        normalized = normalizer.normalize_messages(msgs_manual)

    has_assistant = any(m.get("role") == "assistant" for m in (normalized.get("messages") or []))
    output_synthesized_from_reference = False
    if not has_assistant and expected not in (None, "", []):
        exp_str = (
            expected
            if isinstance(expected, str)
            else _json.dumps(expected, default=str, ensure_ascii=False)
        )
        normalized.setdefault("messages", []).append({"role": "assistant", "content": exp_str})
        normalized["final_output"] = exp_str
        normalized["modality"] = "single_turn"
        # The "output" IS the reference here, so a judge can skip self-comparison.
        # ``has_expected`` is too loose for that — it is true for real outputs too.
        output_synthesized_from_reference = True

    # Embed the expected output so variable-mapping source="reference" works.
    normalized["expected"] = expected
    metadata = normalized.setdefault("metadata", {})
    metadata["has_expected"] = expected not in (None, "", [])
    metadata["output_synthesized_from_reference"] = output_synthesized_from_reference
    # Propagate row-level metadata so variable mappings can reach dataset columns.
    metadata["row_extra"] = dp.extra or {}
    return normalized


def _extract_text_tool_context(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A dataset storing tool calls as text (``[FunctionName(args)]``) instead of structured
    ``tool_calls`` gives the model no schema and the replay provider no graph. Scrapes those
    calls into ``(tool_defs, recorded_calls)``: minimal OpenAI-format schemas with empty
    ``parameters``, and ordered ``{tool, arguments, result}`` records. ``([], [])`` when the
    conversation has no text-format calls."""
    from overbae.services.eval.normalizer import (  # noqa: PLC0415
        _has_text_tool_calls,
        parse_text_tool_calls,
    )

    if not _has_text_tool_calls(messages):
        return [], []

    seen_tools: dict[str, dict[str, Any]] = {}
    recorded: list[dict[str, Any]] = []

    for idx, m in enumerate(messages):
        if m.get("role") != "assistant":
            continue
        calls = parse_text_tool_calls(m.get("content") or "")
        if not calls:
            continue
        tool_results: list[str | None] = []
        for j in range(idx + 1, len(messages)):
            role = messages[j].get("role", "")
            if role == "tool":
                tool_results.append(messages[j].get("content"))
            elif role == "assistant":
                break
        for i, call in enumerate(calls):
            original_name = call["name"]
            safe_name = chatml.sanitize_tool_name(original_name)
            result = tool_results[i] if i < len(tool_results) else None
            # ``name`` is what ReplayToolProvider._match() looks up; the original spelling
            # is kept beside it so evaluators can match either.
            recorded.append(
                {
                    "name": safe_name,
                    "original_name": original_name,
                    "arguments": call["arguments"],
                    "result": result,
                }
            )
            if safe_name not in seen_tools:
                seen_tools[safe_name] = {
                    "name": safe_name,
                    "description": f"Tool: {original_name}",
                    "parameters": {"type": "object", "properties": {}},
                }

    return list(seen_tools.values()), recorded


def _seed_from_trace(sample) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any]:
    """Goes through :func:`normalizer.reconstruct_spans` so the generate path sees the same
    tool-calling reconstruction the dataset-build path produces."""
    from overbae.models import Span
    from overbae.services.eval.runner import ReplayToolProvider

    spans = list(Span.objects.filter(trace_id=sample.source_trace_id, project=sample.run.project))
    source = normalizer.reconstruct_spans(spans, promote_harness=not _grades_model_surface(sample))
    source_struct = normalizer.structure_trajectory(source)
    seed_messages = _seed_from_messages(source.get("messages", []))
    tool_defs = source.get("tool_definitions", [])
    replay = ReplayToolProvider.from_structured(source_struct, tool_defs)
    return seed_messages, tool_defs, replay


def _seed_from_datapoint(dp) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any]:
    """Preferred over the source trace: the datapoint is the canonical ``transform_trace`` output.
    A transcript carrying structured ``tool_calls`` but no ``tools`` schema gets both definitions
    and replay calls synthesized, or the model under test is never told the tools exist."""
    from overbae.services.eval.runner import ReplayToolProvider

    msgs = chatml.parse_messages(dp.input)
    if not msgs:
        return _seed_from_domain_input(dp.input), [], ReplayToolProvider()

    prefix = msgs
    if msgs[-1].get("role") == "assistant" and (
        (dp.extra or {}).get("messages")
        or dp.expected_output is None
        or matches_reference(msgs[-1], dp.expected_output)
        or matches_reference(msgs[-1], (dp.extra or {}).get("model_expected_output"))
    ):
        prefix = msgs[:-1]
    seed_messages = _normalize_seed(prefix)
    raw_tools = dp.input.get("tools", []) if isinstance(dp.input, dict) else []
    tool_defs = chatml.parse_tool_definitions(raw_tools)

    # Replay material comes from the FULL transcript, not the stripped seed.
    norm = normalizer.normalize_messages(dp.input)
    struct = normalizer.structure_trajectory(norm)
    if not tool_defs:
        normalizer.synthesize_tool_definitions(norm)
        tool_defs = norm.get("tool_definitions", [])
    nodes = (struct.get("tool_graph") or {}).get("nodes", [])
    if nodes:
        return seed_messages, tool_defs, ReplayToolProvider.from_structured(struct, tool_defs)

    # Text-format tool-calling datasets (e.g. Toolace) store calls inside
    # assistant content; scrape them into synthetic defs + recorded calls.
    text_defs, text_recorded = _extract_text_tool_context(msgs)
    if text_defs and not tool_defs:
        tool_defs = text_defs
    replay = ReplayToolProvider(recorded_calls=text_recorded, tool_defs=tool_defs)
    return seed_messages, tool_defs, replay


def _seed_from_domain_input(dp_input: Any) -> list[dict[str, Any]]:
    """The canonical eval input never embeds the reference — it lives in ``expected_output`` — so
    the input is seeded verbatim."""
    import json as _json  # noqa: PLC0415

    if isinstance(dp_input, str) and dp_input.strip():
        return [{"role": "user", "content": dp_input.strip()}]
    if dp_input is not None:
        content = _json.dumps(dp_input, default=str)
        return [{"role": "user", "content": content}] if content else []
    return []


def _render_context(messages: list[dict[str, Any]], *, cap: int = 4000) -> str:
    """Compact role/content rendering of the golden prefix for the per-turn judge."""
    lines = []
    for m in messages or []:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"[{role}] {content}")
    text = "\n".join(lines)
    return text[-cap:] if len(text) > cap else text


def _structured_nodes(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Used on both the model's per-turn output and the recorded reference segment, so the two
    are structured identically."""
    if not messages:
        return []
    struct = normalizer.structure_trajectory(normalizer.normalize_messages(messages))
    return (struct.get("tool_graph") or {}).get("nodes", []) or []


def _generate_per_turn(
    sample, tool_defs, replay, model_name, model_spec, system_prompt, variant
) -> dict[str, Any] | None:
    """Each recorded assistant turn supplies a fixed prefix for one model decision."""
    messages = chatml.parse_messages(_sample_row(sample).input)
    assistant_indices = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    if not assistant_indices:
        return None
    # End of each turn's recorded segment: the assistant message plus its tool results,
    # up to the next assistant turn. That segment is the turn's golden reference.
    turn_bounds = assistant_indices[1:] + [len(messages)]

    per_turn: list[dict[str, Any]] = []
    # Read by execute_evaluator's per-turn scoring: generated tool graph beside the
    # recorded reference for exactly that turn, so evaluators grade decision-by-decision.
    per_turn_scoring: list[dict[str, Any]] = []
    generated: list[dict[str, Any]] = []
    totals = {
        "cost": 0.0,
        "latency_ms": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "steps": 0,
    }
    errors = 0
    finish_reasons = []
    output_truncated = False
    first_seed: list[dict[str, Any]] = []
    first_request: dict[str, Any] = {}

    for depth, idx in enumerate(assistant_indices):
        seed = _normalize_seed(messages[:idx])
        if depth == 0:
            first_seed = seed
        reference = messages[idx]
        result = runner.generate_decision(
            input_messages=seed,
            tool_provider=replay,
            model=model_name,
            model_spec=model_spec,
            system_prompt=system_prompt,
            reasoning_effort=(variant.params or {}).get("reasoning_effort"),
            project_id=str(sample.run.project_id),
        )
        if depth == 0:
            first_request = result.request
        _record_generation_activity(sample.run_id)
        totals["cost"] += result.cost or 0.0
        totals["latency_ms"] += result.latency_ms or 0.0
        totals["prompt_tokens"] += result.prompt_tokens or 0
        totals["completion_tokens"] += result.completion_tokens or 0
        totals["steps"] += result.steps or 0
        finish_reasons.extend(result.finish_reasons)
        output_truncated = output_truncated or result.truncated

        turn_meta: dict[str, Any] = {"turn_index": depth, "turn_depth": depth}
        turn_meta["finish_reasons"] = result.finish_reasons
        turn_meta["truncated"] = result.truncated
        turn_meta["context_checks"] = result.context_checks
        gen_nodes: list[dict[str, Any]] = []
        gen_final = ""
        if result.error and not result.output_messages:
            errors += 1
            turn_meta["error"] = result.error
        else:
            generated.extend(result.output_messages)
            gen = next((m for m in result.output_messages if m.get("role") == "assistant"), None)
            if gen is not None:
                turn_meta["generated"] = gen.get("content")
                gen_final = gen.get("content") or ""
            gen_nodes = _structured_nodes(result.output_messages)
        per_turn.append(turn_meta)

        # Recorded golden reference for THIS turn (deterministic — no judge).
        ref_segment = messages[idx : turn_bounds[depth]]
        ref_nodes = _structured_nodes(ref_segment)
        per_turn_scoring.append(
            {
                "depth": depth,
                "generated": gen_nodes,
                "reference": ref_nodes,
                "generated_final": gen_final,
                "reference_final": reference.get("content") or "",
                # The golden prefix the model saw, as context for the per-turn judge.
                "context_text": _render_context(seed),
            }
        )

    # Only a total failure raises: a partial run still scores the turns that succeeded.
    if errors == len(assistant_indices):
        raise RuntimeError("generation failed: all assistant turns errored")

    total_tokens = totals["prompt_tokens"] + totals["completion_tokens"]
    metadata = {
        "model": variant.resolved_model,
        "generation_strategy": "per_assistant_turn",
        "turns_generated": len(assistant_indices),
        "turn_errors": errors,
        "per_turn": per_turn,
        "per_turn_scoring": per_turn_scoring,
        "cost": totals["cost"],
        "latency_ms": totals["latency_ms"] or None,
        "prompt_tokens": totals["prompt_tokens"] or None,
        "completion_tokens": totals["completion_tokens"] or None,
        "total_tokens": total_tokens or None,
        "steps": totals["steps"],
        "finish_reasons": finish_reasons,
        "output_truncated": output_truncated,
        "truncated": output_truncated,
    }
    return normalizer.normalize_generation(
        input_value=first_seed,
        output_messages=generated,
        tool_definitions=tool_defs,
        metadata=metadata,
        request=first_request,
    )


def _generate_sample(sample) -> dict[str, Any]:
    from overbae.services.eval.runner import ReplayToolProvider, resolve_max_steps, run_capability

    variant = sample.variant
    model_name, model_spec = _variant_model(variant)

    # Prefer the already-reconstructed datapoint transcript; the source trace enriches
    # replay results when the datapoint carried none, or stands in as the sole source.
    seed_messages: list[dict[str, Any]] = []
    tool_defs: list[dict[str, Any]] = []
    replay = ReplayToolProvider()

    datapoint = _sample_row(sample)
    if datapoint is not None:
        seed_messages, tool_defs, replay = _seed_from_datapoint(datapoint)
        if not replay.recorded_calls and sample.source_trace_id:
            _, trace_tool_defs, trace_replay = _seed_from_trace(sample)
            replay = trace_replay
            if not tool_defs:
                tool_defs = trace_tool_defs
    elif sample.source_trace_id:
        seed_messages, tool_defs, replay = _seed_from_trace(sample)

    system_prompt = (variant.params or {}).get("system_prompt")
    if system_prompt is None and variant.prompt_id:
        system_prompt = variant.prompt.system_prompt

    # The step budget tracks the depth of the recorded workflow. A fixed default
    # strands deep tool-calling replays mid-loop.
    max_steps = resolve_max_steps(
        recorded_tool_calls=len(getattr(replay, "recorded_calls", []) or []),
        override=(variant.params or {}).get("max_steps"),
    )

    if (variant.params or {}).get("generation_strategy") == "per_assistant_turn":
        per_turn = _generate_per_turn(
            sample, tool_defs, replay, model_name, model_spec, system_prompt, variant
        )
        if per_turn is not None:
            return per_turn

    result = run_capability(
        input_messages=seed_messages,
        tool_provider=replay,
        model=model_name,
        model_spec=model_spec,
        system_prompt=system_prompt,
        max_steps=max_steps,
        reasoning_effort=(variant.params or {}).get("reasoning_effort"),
        project_id=str(sample.run.project_id),
    )
    # Raise on total failure so prepare_sample records sample.error: evaluators then
    # skip the sample instead of scoring empty output as a genuine result.
    if result.error and not result.output_messages:
        raise RuntimeError(f"generation failed: {result.error}")

    total_tokens = result.prompt_tokens + result.completion_tokens
    metadata = {
        "model": variant.resolved_model,
        "cost": result.cost,
        "latency_ms": result.latency_ms or None,
        "prompt_tokens": result.prompt_tokens or None,
        "completion_tokens": result.completion_tokens or None,
        "total_tokens": total_tokens or None,
        "steps": result.steps,
        "max_steps": max_steps,
        "replay_fuzzy_hits": result.fuzzy_tool_hits,
        "replay_misses": result.tool_misses,
        "finish_reasons": result.finish_reasons,
        "output_truncated": result.truncated,
        "context_checks": result.context_checks,
        "truncated": result.truncated,
    }
    if result.error:
        metadata["generation_error"] = result.error
    return normalizer.normalize_generation(
        input_value=seed_messages,
        output_messages=result.output_messages,
        tool_definitions=tool_defs,
        metadata=metadata,
        request=result.request,
    )


def _seed_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Takes the system+user prefix up to the first assistant turn. Used for source-trace re-runs,
    which replay the whole trajectory from scratch."""
    seed: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "assistant":
            break
        if m.get("role") in ("system", "user"):
            seed.append({"role": m["role"], "content": m.get("content", "")})
    if not seed and messages:
        first_user = next((m for m in messages if m.get("role") == "user"), None)
        if first_user:
            seed.append({"role": "user", "content": first_user.get("content", "")})
    return seed


def _normalize_seed(prefix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import json as _json
    import uuid as _uuid

    # OpenAI requires exactly N tool messages after an assistant with N tool_calls.
    # A dataset with fewer results than calls (common in Hermes) needs the tool_calls
    # list trimmed to match, so count the results up front.
    tool_results_after: list[int] = []  # indexed same as prefix
    for idx in range(len(prefix)):
        if prefix[idx].get("role") == "assistant" and prefix[idx].get("tool_calls"):
            count = 0
            for j in range(idx + 1, len(prefix)):
                r = prefix[j].get("role", "")
                if r == "tool":
                    count += 1
                elif r == "assistant":
                    break
            tool_results_after.append(count)
        else:
            tool_results_after.append(0)

    seed: list[dict[str, Any]] = []
    # Synthetic ID pool keeps tool_calls and tool results in sync when the dataset
    # format omits call IDs (Hermes stores tool_calls without one).
    pending_ids: list[str] = []
    # Text-based tool-call datasets (Toolace) put invocations in assistant content as
    # plain text, so their `tool` replies are orphaned to the API and must be dropped.
    last_assistant_had_tool_calls = False

    for idx, m in enumerate(prefix):
        role = m.get("role", "")
        if role not in ("system", "user", "assistant", "tool"):
            continue
        entry: dict[str, Any] = {"role": role, "content": m.get("content") or ""}
        if role == "assistant":
            raw_calls = m.get("tool_calls")
            if raw_calls:
                # Both wire shapes must be handled: the original
                # {type, function: {name, arguments}} and the {id, name, arguments}
                # chatml.normalize_message flattens it to. Trim to the available results
                # as well — a tool_calls/tool-message mismatch makes OpenAI reject the
                # whole request.
                n_results = tool_results_after[idx]
                normalized_calls: list[dict[str, Any]] = []
                pending_ids = []
                for tc in raw_calls:
                    if len(normalized_calls) >= n_results:
                        break  # trim to available results
                    tc_id = tc.get("id") or f"call_{_uuid.uuid4().hex[:8]}"
                    pending_ids.append(tc_id)
                    fn_dict = tc.get("function") if isinstance(tc.get("function"), dict) else None
                    if fn_dict is not None:
                        name = fn_dict.get("name", "")
                        args = fn_dict.get("arguments", {})
                    else:
                        name = tc.get("name", "")
                        args = tc.get("arguments", {})
                    if not isinstance(args, str):
                        args = _json.dumps(args, ensure_ascii=False)
                    if not name:
                        pending_ids.pop()
                        continue
                    normalized_calls.append(
                        {
                            "id": tc_id,
                            "type": tc.get("type", "function"),
                            "function": {"name": name, "arguments": args},
                        }
                    )
                if normalized_calls:
                    entry["tool_calls"] = normalized_calls
                    last_assistant_had_tool_calls = True
                else:
                    last_assistant_had_tool_calls = False
                    pending_ids = []
            else:
                pending_ids = []
                last_assistant_had_tool_calls = False
        if role == "tool":
            if not last_assistant_had_tool_calls:
                # Orphaned: the prior assistant used text-based tool calls, and OpenAI
                # rejects a tool message that follows no tool_calls.
                continue
            # Match tool messages to their call by position when tool_call_id is absent.
            tc_id = m.get("tool_call_id")
            if not tc_id and pending_ids:
                tc_id = pending_ids.pop(0)
            if tc_id:
                entry["tool_call_id"] = tc_id
            name = m.get("name", "")
            if name:
                entry["name"] = name
        seed.append(entry)
    return seed


def _warm_generate_models(variants) -> None:
    """A freshly-deployed fine-tuned model cold-starts on Modal for minutes on its first request.
    Paying that here, on the un-timed run task, leaves every ``prepare_sample`` hitting a hot
    worker. Only custom-endpoint variants are pinged; catalog models are already warm."""
    from overbae.core.llms import call_llm

    seen: set[str] = set()
    for v in variants:
        if getattr(v, "mode", "") != "generate":
            continue
        model_name, spec = _variant_model(v)
        if spec is None:  # OpenRouter / catalog — already warm
            continue
        key = spec.base_url or spec.model_id
        if key in seen:
            continue
        seen.add(key)
        try:
            call_llm("ping", model=model_name, model_spec=spec, max_tokens=1)
            logger.info("warmed eval model %s", spec.model_id)
        except Exception as exc:  # noqa: BLE001 — warmup is best-effort
            logger.warning("model warmup failed for %s: %s", spec.model_id, exc)


def _variant_model(variant) -> tuple[str | None, ModelSpec | None]:
    if variant.model_ref_id and variant.model_ref:
        ref = variant.model_ref
        spec = ModelSpec(
            provider=ref.provider,
            model_id=ref.model_id,
            base_url=ref.base_url,
            api_key_env=ref.api_key_ref,
            params=ref.params or {},
        )
        # Catalog providers without a custom endpoint take the name path.
        if ref.provider in ("openai", "anthropic", "gemini") and not ref.base_url:
            return ref.model_id, None
        return None, spec
    return (variant.model_name or None), None


@shared_task(name="overbae.tasks.eval.launch_evaluation")
def launch_evaluation(_prepare_results, *, eval_run_id: str, **kwargs) -> dict[str, Any]:
    from overbae.models import EvalRun

    run = EvalRun.objects.get(id=eval_run_id)
    if run.dataset_id:
        sweep_eval_bindings.delay(dataset_id=str(run.dataset_id))
    # A prompt-scoped evaluator grades only samples whose ``variant.prompt_id`` matches;
    # a global one (``prompt_id`` null) grades every sample.
    samples = list(run.samples.values_list("id", "variant__prompt_id"))
    run_evals = list(run.run_evaluators.filter(enabled=True).values_list("id", "prompt_id"))
    global_reids = [str(reid) for reid, pid in run_evals if pid is None]
    reids_by_prompt: dict[str, list[str]] = {}
    for reid, pid in run_evals:
        if pid is not None:
            reids_by_prompt.setdefault(str(pid), []).append(str(reid))

    header = [
        execute_evaluator.s(sample_id=str(sid), run_evaluator_id=reid)
        for sid, prompt_id in samples
        for reid in (global_reids + reids_by_prompt.get(str(prompt_id), []))
    ]
    if not header:
        return aggregate_run(None, eval_run_id=eval_run_id)
    # Refresh the stall anchor so the watchdog measures the scoring phase from here,
    # not from the run start.
    EvalRun.objects.filter(pk=run.pk).update(updated_at=timezone.now())
    header = [_link_failure(sig, eval_run_id) for sig in header]
    callback = _link_failure(aggregate_run.s(eval_run_id=eval_run_id), eval_run_id)
    chord(group(header))(callback)
    return {"status": "evaluating", "tasks": len(header)}


# Evaluators that compare the trajectory or steps against a reference rather than
# the final answer. Only these are scoped per-turn under teacher-forced replay;
# final-output judges still grade the sample once.
_TURN_SCOPED_KINDS = frozenset({"trajectory", "deterministic"})
_TURN_SCOPED_SCOPES = frozenset({"trajectory", "step", "turn"})


def _is_turn_scoped(evaluator) -> bool:
    scope = getattr(evaluator, "scope", "")
    if scope in ("turn", "step"):
        return True
    return getattr(evaluator, "kind", "") in _TURN_SCOPED_KINDS and scope in _TURN_SCOPED_SCOPES


def _score_per_turn(per_turn: list[dict[str, Any]], evaluator, ctx: dict[str, Any]) -> list:
    """One draft per turn, tagged ``target_ref=turn:<depth>``, so the rollup averages
    decision-level accuracy. A turn whose reference carries no tool calls abstains and is
    excluded from means, like any evaluator with no reference."""
    drafts: list = []
    for turn in per_turn:
        ref_nodes = turn.get("reference") or []
        gen_nodes = turn.get("generated") or []
        unit = eval_base.EvalUnit(
            structured={
                "tool_graph": {"nodes": gen_nodes},
                "num_turns": 1,
                # Read by the per-turn judge; the deterministic families ignore them.
                "_context_text": turn.get("context_text", ""),
                "_reference_final": turn.get("reference_final", ""),
                "_candidate_final": turn.get("generated_final", ""),
            },
            expected={
                "trajectory": ref_nodes,
                "tools": [
                    {"name": n.get("tool"), "arguments": n.get("arguments", {})} for n in ref_nodes
                ],
            },
        )
        for d in eval_base.evaluate(unit, evaluator, ctx):
            d.target_ref = f"turn:{turn.get('depth', 0)}"
            drafts.append(d)
    return drafts


# Late acks (see ``prepare_sample``): redeliver on worker loss so a dropped scoring
# task cannot strand the chord. Safe because the task is idempotent per
# ``(sample, run_evaluator)`` — it returns early when scores already exist.
@shared_task(
    name="overbae.tasks.eval.execute_evaluator",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def execute_evaluator(self, *, sample_id: str, run_evaluator_id: str, **kwargs) -> dict[str, Any]:
    from overbae.models import EvalRun, EvalSample, RunEvaluator, Score
    from overbae.services.eval import snapshots

    try:
        sample = EvalSample.objects.select_related(
            "run", "variant", "run__dataset", "run__dataset__capability"
        ).get(id=sample_id)
    except EvalSample.DoesNotExist:
        # Nothing to attach a Score to. Return a normal error result so a stray
        # (sample, evaluator) pairing cannot fail the run.
        logger.exception("execute_evaluator: sample %s not found", sample_id)
        return {"status": "error", "error": f"sample {sample_id} not found"}

    # Cancel guard: scoring task ids are not tracked for revoke, so cancelling a run
    # cannot kill its dispatched scoring tasks, which would each keep running a judge
    # LLM call. Short-circuiting on a terminal run drains the queue immediately. A task
    # already mid judge-call is not interrupted; the LLM socket timeout bounds that.
    terminal_statuses = (
        EvalRun.Status.CANCELLED,
        EvalRun.Status.FAILED,
        EvalRun.Status.COMPLETED,
    )
    if sample.run.status in terminal_statuses:
        return {"status": "skipped", "reason": f"run_{sample.run.status}"}

    # Containment: a failure loading the evaluator must NOT reach the run-level errback,
    # which fails the WHOLE run. Emit one error Score for this pair and return normally.
    try:
        run_eval = RunEvaluator.objects.select_related("evaluator").get(id=run_evaluator_id)
        evaluator = snapshots.snapshot_to_obj(
            run_eval.snapshot, scope_override=run_eval.scope_override
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("execute_evaluator: failed to load evaluator %s", run_evaluator_id)
        _record_evaluator_error(sample, None, "evaluator", "sample", exc)
        return {"status": "error", "error": str(exc)}

    if Score.objects.filter(sample=sample, run_evaluator=run_eval).exists():
        return {"status": "skipped", "reason": "already_scored"}

    # Per-evaluator sampling: a pricier rubric can run on a subset of samples.
    if 0 < run_eval.sampling < 1.0:
        rng = random.Random(f"{sample_id}:{run_evaluator_id}")
        if rng.random() > run_eval.sampling:
            return {"status": "skipped", "reason": "sampled_out"}

    if sample.error:
        return {"status": "skipped", "reason": "sample_error"}

    if (sample.trajectory.get("metadata") or {}).get("output_truncated"):
        Score.objects.create(
            project=sample.run.project,
            run=sample.run,
            variant=sample.variant,
            sample=sample,
            evaluator=run_eval.evaluator,
            run_evaluator=run_eval,
            name=evaluator.name,
            data_type=evaluator.score_type,
            value=None,
            outcome=Score.Outcome.SKIPPED,
            scope=evaluator.scope,
            reasoning="Generation reached its output token limit. Incomplete response; quality was not scored.",
        )
        return {"status": "skipped", "reason": "output_token_limit"}

    # Refuse to score an evaluator in a mode that cannot produce its evidence, e.g. a
    # harness_artifact judge on a generate variant. The not-applicable Score keeps it
    # out of trusted aggregates and counted separately — never scored as a 0.
    requirement = getattr(evaluator, "evidence_requirement", evidence.MODEL_OUTPUT)
    if not evidence.is_applicable(requirement, sample.variant.mode):
        reason = evidence.not_applicable_reason(requirement, sample.variant.mode)
        Score.objects.create(
            project=sample.run.project,
            run=sample.run,
            variant=sample.variant,
            sample=sample,
            evaluator=run_eval.evaluator,
            run_evaluator=run_eval,
            name=evaluator.name,
            data_type=evaluator.score_type,
            value=None,
            passed=None,
            outcome=Score.Outcome.NOT_APPLICABLE,
            reasoning=reason,
            scope=evaluator.scope,
            sub_scores=[
                {
                    evidence.NOT_APPLICABLE_MARKER: True,
                    "evidence_requirement": requirement,
                    "variant_mode": sample.variant.mode,
                    "reason": reason,
                }
            ],
        )
        # Created outside the main draft loop, so project here or abstain/NA-rate
        # queries never see it.
        return {"status": "not_applicable", "reason": requirement}

    try:
        unit = eval_base.EvalUnit.from_sample(sample)
        ctx = {
            "eval_surface": eval_base.SURFACE_GENERATIVE,
            "project_id": str(sample.run.project_id),
            "variant_model": sample.variant.resolved_model,
            # All models under test, so the judge can pick a different family.
            "run_variant_models": [
                v.resolved_model for v in sample.run.variants.select_related("model_ref").all()
            ],
        }
        # Teacher-forced replay scores each recorded turn against its own golden
        # reference instead of one stitched-together trajectory.
        per_turn = (sample.structured or {}).get("per_turn")
        if per_turn and _is_turn_scoped(evaluator):
            drafts = _score_per_turn(per_turn, evaluator, ctx)
        else:
            drafts = eval_base.evaluate(unit, evaluator, ctx)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "execute_evaluator failed for sample %s run_evaluator %s", sample_id, run_evaluator_id
        )
        _record_evaluator_error(sample, run_eval, evaluator.name, evaluator.scope, exc)
        return {"status": "error", "error": str(exc)}

    coverage = (ctx.get("_cascade") or {}).get("context_coverage")
    if coverage is not None:
        EvalSample.objects.filter(pk=sample.pk).update(context_coverage=coverage)

    version_stamp = {
        "_evaluator": {
            "version": (run_eval.snapshot or {}).get("version"),
            "content_hash": ((run_eval.snapshot or {}).get("config") or {}).get("content_hash"),
        }
    }
    new_scores = []
    for d in drafts:
        d.sub_scores = [*d.sub_scores, version_stamp]
        new_scores.append(
            Score.objects.create(
                project=sample.run.project,
                run=sample.run,
                variant=sample.variant,
                sample=sample,
                evaluator=run_eval.evaluator,
                run_evaluator=run_eval,
                scope=d.scope,
                target_ref=d.target_ref,
                name=d.name,
                data_type=d.data_type,
                value=d.value,
                string_value=d.string_value,
                passed=d.passed,
                outcome=_resolve_outcome(d.value, d.outcome),
                reasoning=d.reasoning,
                sub_scores=d.sub_scores,
                failure_role=d.failure_role,
                judge_trace_id=d.judge_trace_id,
                cost=d.cost,
                latency_ms=d.latency_ms,
            )
        )
    return {"status": "ok", "scores": len(new_scores)}


def _record_evaluator_error(sample, run_eval, name: str, scope: str, exc: Exception) -> None:
    """Mirrors the run-level error stamp, so a contained evaluator failure is counted in the run
    summary instead of stranding or failing the whole run."""
    from overbae.models import Score

    Score.objects.create(
        project=sample.run.project,
        run=sample.run,
        variant=sample.variant,
        sample=sample,
        evaluator=run_eval.evaluator if run_eval else None,
        run_evaluator=run_eval,
        name=name,
        data_type="numeric",
        value=None,
        outcome=Score.Outcome.ERROR,
        reasoning=f"{_EVALUATOR_ERROR_PREFIX} {exc}",
        scope=scope or "sample",
    )
    # Created outside the draft loop, so project here or evaluator error-rate queries
    # never see it. Must not defeat containment.


def _dataset_is_classification(run) -> bool:
    if not run.dataset_id:
        return False
    try:
        from overbae.services.eval.profiler import profile_dataset

        profile = profile_dataset(run.dataset)
        return bool(profile.get("has_reference")) and profile.get("output_kind") == "label"
    except Exception:  # noqa: BLE001 — never fail finalize on profiling
        logger.warning("dataset profiling failed for run %s", run.id, exc_info=True)
        return False


@shared_task(name="overbae.tasks.eval.aggregate_run")
def aggregate_run(_eval_results=None, *, eval_run_id: str, **kwargs) -> dict[str, Any]:
    from overbae.models import EvalRun, Score
    from overbae.services.eval import snapshots

    run = EvalRun.objects.get(id=eval_run_id)
    variants = list(run.variants.all())
    stat_scores = []
    # Lazy tri-state: profile the dataset at most once per finalize.
    is_classification: bool | None = None

    for run_eval in run.run_evaluators.filter(enabled=True).select_related("evaluator"):
        if (run_eval.snapshot or {}).get("kind") != "statistical":
            continue
        evaluator = snapshots.snapshot_to_obj(run_eval.snapshot)
        for variant in variants:
            if run_eval.prompt_id and run_eval.prompt_id != variant.prompt_id:
                continue
            preds, refs = _collect_predictions(run, variant, evaluator.name, evaluator.config or {})
            if not preds:
                continue
            draft = statistical.aggregate(preds, refs, evaluator)
            cm = statistical.confusion_matrix(preds, refs, evaluator.config or {})
            sub = list(draft.sub_scores)
            if cm:
                sub.append({"confusion_matrix": cm})
                # Per-class metrics only when the references are labels: a free-text
                # dataset would explode into one "class" per distinct string.
                if is_classification is None:
                    is_classification = _dataset_is_classification(run)
                if is_classification:
                    pcm = statistical.per_class_metrics(preds, refs, evaluator.config or {})
                    if pcm:
                        sub.append({"class_metrics": pcm})
            # A watchdog-finalize and a late chord callback can both reach here, so
            # replace any prior dataset-scope score rather than double-write it.
            Score.objects.filter(
                run=run, variant=variant, run_evaluator=run_eval, scope="dataset"
            ).delete()
            stat_scores.append(
                Score.objects.create(
                    project=run.project,
                    run=run,
                    variant=variant,
                    sample=None,
                    evaluator=run_eval.evaluator,
                    run_evaluator=run_eval,
                    scope="dataset",
                    name=draft.name,
                    data_type="numeric",
                    value=draft.value,
                    passed=draft.passed,
                    outcome=_resolve_outcome(draft.value, draft.outcome),
                    reasoning=draft.reasoning,
                    sub_scores=sub,
                )
            )

    summary = _build_summary(run)
    summary["error_counts"] = _count_sample_errors(run)

    # A run where nothing scored (every row abstained, was not applicable, or errored)
    # is "completed" but vacuous — flag it so the UI never reads an empty grid as a pass.
    scored_count = (
        Score.objects.filter(run=run, outcome=Score.Outcome.SCORED)
        .exclude(name__endswith="__prediction")
        .count()
    )
    summary["completed_empty"] = scored_count == 0

    # Only flip a still-in-flight run, mirroring ``_fail_run``, so a double callback or
    # one arriving after the watchdog finalized cannot clobber a terminal summary.
    terminal = [
        EvalRun.Status.COMPLETED,
        EvalRun.Status.FAILED,
        EvalRun.Status.CANCELLED,
    ]
    EvalRun.objects.filter(pk=run.pk).exclude(status__in=terminal).update(
        status=EvalRun.Status.COMPLETED,
        summary=summary,
        completed_at=timezone.now(),
    )

    # Training scheduling imports this task; defer the cyclic notification import.
    from overbae.services.finetuning_eval import sync_eval_scores

    for job in FinetuningJob.objects.filter(job_evals__eval_run_id=run.pk).distinct():
        sync_eval_scores(job)

    return {"status": "completed", "metrics": summary.get("metrics", [])}


def _sibling_sample_scores(run, variant, evaluator_name: str) -> dict[str, Any]:
    """Another evaluator's per-sample values, keyed by sample.

    Calibration needs a truth signal the unit cannot carry — whether the row was
    actually correct — and that already exists as a deterministic evaluator's
    score. Reading it here beats recomputing the comparison in a second place.
    """
    from overbae.models import Score

    rows = Score.objects.filter(
        run=run, variant=variant, name=evaluator_name, sample__isnull=False
    ).exclude(sample__degraded=True)
    return {str(row.sample_id): row.value for row in rows if row.value is not None}


def _collect_predictions(run, variant, evaluator_name: str, config=None) -> tuple[list, list]:
    from overbae.models import Score

    preds: list[str] = []
    refs: list = []
    sibling = (config or {}).get("reference_evaluator")
    from_sibling = _sibling_sample_scores(run, variant, sibling) if sibling else {}
    pred_name = f"{evaluator_name}__prediction"
    # Degraded samples are excluded so a pipeline-caused failure never skews the
    # dataset-level statistical metric (accuracy / F1 / ...).
    rows = (
        Score.objects.filter(run=run, variant=variant, name=pred_name)
        .exclude(sample__degraded=True)
        .select_related("sample")
    )
    for row in rows:
        sub = row.sub_scores[0] if row.sub_scores else {}
        prediction = sub.get("prediction", row.string_value)
        # Feeding ``str(None)`` into a classification metric would silently count
        # "the model produced nothing" as a concrete wrong label. Pred and ref drop
        # together to stay parallel.
        if prediction is None or prediction == "":
            continue
        reference = from_sibling.get(str(row.sample_id)) if sibling else sub.get("reference")
        # Pred and ref drop together to stay parallel: a sibling that abstained on
        # this row leaves the metric no truth to score the prediction against.
        if sibling and reference is None:
            continue
        preds.append(str(prediction))
        refs.append(reference)
    return preds, refs


def _build_summary(run) -> dict[str, Any]:
    from django.db.models import Count

    from overbae.models import EvalSample, Score

    baseline = (
        run.variants.filter(is_baseline=True).first() or run.variants.order_by("order").first()
    )
    baseline_id = str(baseline.id) if baseline else None

    # Degraded samples stay out of the scored aggregates: a pipeline-caused 0 must
    # never average in as a genuine model failure. The trust signal reports them.
    rows = (
        Score.objects.filter(run=run, sample__isnull=False)
        .exclude(name__endswith="__prediction")
        .exclude(sample__degraded=True)
        .select_related("variant")
    )
    score_dicts = [
        {
            "variant_id": str(s.variant_id),
            "variant_label": s.variant.label if s.variant_id else "",
            "name": s.name,
            "value": s.value,
            "passed": s.passed,
            "data_type": s.data_type,
            "scope": s.scope,
            "outcome": s.outcome,
            # Grouping key so rollup can resample at the conversation level.
            "sample_id": str(s.sample_id) if s.sample_id else None,
        }
        for s in rows
    ]

    variant_sample_counts: dict[str, int] = {
        str(row["variant"]): row["n"]
        for row in EvalSample.objects.filter(run=run).values("variant").annotate(n=Count("id"))
    }

    # ``n_override`` makes rollup show the number of evaluated samples, not the 1
    # dataset-scope score row.
    for s in Score.objects.filter(run=run, scope="dataset").select_related("variant"):
        vid = str(s.variant_id) if s.variant_id else "__none__"
        sub = s.sub_scores or []
        n_from_sub = sub[0].get("n") if sub else None
        n_override = n_from_sub or variant_sample_counts.get(vid, 1)
        score_dicts.append(
            {
                "variant_id": vid,
                "variant_label": s.variant.label if s.variant_id else "",
                "name": s.name,
                "value": s.value,
                "passed": s.passed,
                "data_type": s.data_type,
                "scope": "dataset",
                "outcome": s.outcome,
                "n_override": n_override,
            }
        )

    summary = ranking.rollup(score_dicts, baseline_variant_id=baseline_id)
    summary["trust"] = _trust_signal(run)
    summary["applicability"] = _applicability_signal(run)
    summary["measurement"] = _measurement_signal(run)
    summary["items"] = _item_signal(run)
    summary["pooled"] = _pooled_signal(run)
    _apply_pooled_to_metrics(summary)
    # Read from the frozen snapshots, so a later edit to the library evaluator
    # cannot retroactively move a metric in or out of this run's headline number.
    summary["gate_metrics"] = sorted(
        {
            str((re.snapshot or {}).get("name") or "")
            for re in run.run_evaluators.all()
            if ((re.snapshot or {}).get("config") or {}).get("gate_only")
        }
        - {""}
    )
    behaviours = _behaviour_rollup(run)
    if behaviours:
        summary["behaviours"] = behaviours
    return summary


def _item_signal(run) -> dict[str, Any]:
    """Per-checklist-item pass rates, keyed by variant then evaluator.

    The mean is where the readable signal dies: two models can share a score
    while failing different items. Aggregated here rather than in the view so a
    run's breakdown is frozen with it, but kept out of the headline — the run
    view shows one number per evaluator and reveals this on request.

    Only items with a stable ``id`` aggregate. Enumerated claims are keyed by
    their own text, which differs per sample, so counting them across a run
    would compare unrelated things.
    """
    from overbae.models import Score

    configured: dict[str, set[str]] = {}
    for link in run.run_evaluators.all():
        snap = link.snapshot or {}
        name = str(snap.get("name") or "")
        if not name:
            continue
        configured.setdefault(name, set()).update(
            str(item.get("id"))
            for item in (snap.get("checklist") or [])
            if isinstance(item, dict) and item.get("id")
        )

    counts: dict[str, dict[str, dict[str, dict[str, int]]]] = {}
    rows = (
        Score.objects.filter(run=run, sample__isnull=False)
        .exclude(sample__degraded=True)
        .exclude(name__endswith="__prediction")
        .only("variant_id", "name", "sub_scores")
    )
    for row in rows:
        allowed = configured.get(row.name) if configured else None
        for entry in row.sub_scores or []:
            if not isinstance(entry, dict) or "verdict" not in entry or not entry.get("id"):
                continue
            if entry["verdict"] is None:
                continue
            item_id = str(entry["id"])
            if allowed is not None and item_id not in allowed:
                continue
            item = (
                counts.setdefault(str(row.variant_id), {})
                .setdefault(row.name, {})
                .setdefault(item_id, {"passed": 0, "total": 0})
            )
            item["total"] += 1
            item["passed"] += 1 if entry["verdict"] else 0

    return {
        variant: {
            name: [
                {"id": item_id, **tally, "pass_rate": tally["passed"] / tally["total"]}
                for item_id, tally in sorted(items.items())
            ]
            for name, items in evaluators.items()
        }
        for variant, evaluators in counts.items()
    }


def _apply_pooled_to_metrics(summary: dict[str, Any]) -> None:
    """Put the pooled rate where the run view reads its headline number.

    ``mean`` is left alongside rather than overwritten — it is what it says it
    is, and the paired comparison still needs a per-row statistic. The view
    prefers ``pooled`` when present, which is the only correct run-level figure
    for an evaluator whose rows carry different denominators.
    """
    variants = summary.get("variants") or {}
    for variant_id, tallies in (summary.get("pooled") or {}).items():
        metrics = (variants.get(variant_id) or {}).get("metrics") or {}
        for name, tally in tallies.items():
            row = metrics.get(name)
            if row is not None and tally.get("rate") is not None:
                row["pooled"] = tally["rate"]


def _pooled_signal(run) -> dict[str, Any]:
    """Ratio-of-sums for proportional evaluators, keyed by variant then evaluator.

    Averaging per-row fractions is the wrong run-level statistic when rows carry
    different denominators: a row with one claim scores 1.0 or 0.0, and a model
    that writes tersely accumulates many such rows, inflating its mean. Measured
    on run 27018f68 the mean-of-fractions said 0.979 against 0.958 (p=0.017,
    apparently a regression) while the pooled rate said 0.948 against 0.952 —
    the opposite ordering, and the unbiased one.
    """
    from overbae.models import Score

    totals: dict[str, dict[str, dict[str, int]]] = {}
    rows = (
        Score.objects.filter(run=run, sample__isnull=False)
        .exclude(sample__degraded=True)
        .only("variant_id", "name", "sub_scores")
    )
    for row in rows:
        proportion = next(
            (
                e["_proportion"]
                for e in row.sub_scores or []
                if isinstance(e, dict) and "_proportion" in e
            ),
            None,
        )
        if not proportion:
            continue
        tally = totals.setdefault(str(row.variant_id), {}).setdefault(
            row.name, {"supported": 0, "judged": 0}
        )
        tally["supported"] += int(proportion.get("supported") or 0)
        tally["judged"] += int(proportion.get("judged") or 0)

    return {
        variant: {
            name: {**tally, "rate": tally["supported"] / tally["judged"]}
            for name, tally in evaluators.items()
            if tally["judged"]
        }
        for variant, evaluators in totals.items()
    }


def _measurement_signal(run) -> dict[str, Any]:
    """An evaluator that abstained on every row looks healthy until it is named.

    Walk attached RunEvaluators too: ``{name}__prediction`` rows are excluded
    from the Score-name tally, so a silent dataset-scope metric would vanish.
    ``uncovered_card_claims`` names generate-observable card criteria no
    attached checklist restates — a collapsed suite can still score 1.0.
    """
    from django.db.models import Count, Q

    from overbae.models import Score
    from overbae.services.eval.card_compiler import generate_observes_tool_calls
    from overbae.services.eval.grounding import resolve_grounding
    from overbae.services.eval.profiler import dataset_has_closed_form_reference
    from overbae.services.eval.surface_binding import uncovered_generate_card_claims

    scored_names = set(
        Score.objects.filter(run=run, outcome=Score.Outcome.SCORED)
        .exclude(name__endswith="__prediction")
        .values_list("name", flat=True)
        .distinct()
    )
    never: set[str] = set()
    rows = (
        Score.objects.filter(run=run, sample__isnull=False)
        .exclude(name__endswith="__prediction")
        .values("name")
        .annotate(total=Count("id"), scored=Count("id", filter=Q(value__isnull=False)))
    )
    for r in rows:
        if r["total"] and not r["scored"]:
            never.add(r["name"])
    for re in run.run_evaluators.filter(enabled=True):
        name = str((re.snapshot or {}).get("name") or "")
        if name and name not in scored_names:
            never.add(name)
    uncovered: list[str] = []
    dataset = getattr(run, "dataset", None)
    capability = getattr(dataset, "capability", None) if dataset is not None else None
    if capability is not None and dataset is not None:
        ds_ctx = resolve_grounding(dataset)
        card = ds_ctx.codebase_card
        checklists = [
            list((re.snapshot or {}).get("checklist") or [])
            for re in run.run_evaluators.filter(enabled=True)
            if str((re.snapshot or {}).get("kind") or "") == "llm_judge"
        ]
        uncovered = uncovered_generate_card_claims(
            card,
            checklists,
            generate_observes_tools=generate_observes_tool_calls(ds_ctx),
            closed_form_reference=dataset_has_closed_form_reference(dataset),
        )
    return {"never_scored": sorted(never), "uncovered_card_claims": uncovered}


def _behaviour_keys(run) -> dict[int, str]:
    """``row_index`` → ``behaviour_key`` from the pinned product, read once per rollup."""
    from overbae.services.datasets import rows as row_store

    if run.cell_id is None:
        return {}
    try:
        return {r.index: r.behaviour_key for r in row_store.iter_rows(run.cell) if r.behaviour_key}
    except Exception:  # noqa: BLE001 — the rollup is additive
        logger.warning("behaviour keys unavailable for run %s", run.pk, exc_info=True)
        return {}


def _behaviour_rollup(run) -> dict[str, Any]:
    """Rows minted from bound traces carry ``extra.behaviour_key``; unbound rows stay in the
    agent-level bucket the main rollup already covers."""
    from overbae.models import Score

    rows = (
        Score.objects.filter(run=run, sample__isnull=False, outcome=Score.Outcome.SCORED)
        .exclude(name__endswith="__prediction")
        .exclude(sample__degraded=True)
        .select_related("sample")
    )
    keys_by_index = _behaviour_keys(run)
    buckets: dict[str, dict[str, Any]] = {}
    for s in rows:
        key = keys_by_index.get(s.sample.row_index, "") if s.sample.row_index is not None else ""
        if not key:
            continue
        b = buckets.setdefault(key, {"n_scores": 0, "sum": 0.0, "samples": set()})
        b["samples"].add(str(s.sample_id))
        if s.value is not None:
            b["n_scores"] += 1
            b["sum"] += float(s.value)
    return {
        key: {
            "n_samples": len(b["samples"]),
            "n_scores": b["n_scores"],
            "mean": round(b["sum"] / b["n_scores"], 4) if b["n_scores"] else None,
        }
        for key, b in sorted(buckets.items())
    }


def _applicability_signal(run) -> dict[str, Any]:
    """Not-applicable scores are excluded from means and pass-rates, so they surface here for the
    UI to show separately from genuine failures."""
    from overbae.models import Score

    # ``outcome`` alone is the source of truth; no sub_scores-marker fallback.
    rows = Score.objects.filter(run=run, outcome=Score.Outcome.NOT_APPLICABLE).select_related(
        "variant"
    )

    by_variant: dict[str, dict[str, Any]] = {}
    by_evaluator: dict[str, int] = {}
    total = 0
    for s in rows:
        total += 1
        vid = str(s.variant_id) if s.variant_id else "__none__"
        row = by_variant.setdefault(
            vid, {"label": s.variant.label if s.variant_id else "", "not_applicable": 0}
        )
        row["not_applicable"] += 1
        by_evaluator[s.name] = by_evaluator.get(s.name, 0) + 1

    return {"total": total, "by_variant": by_variant, "by_evaluator": by_evaluator}


def _trust_signal(run) -> dict[str, Any]:
    """Degraded samples are excluded from the scored aggregates, so this reports how many and why,
    plus the replay miss and fuzzy-hit totals that make a low-fidelity replay visible."""
    from overbae.models import EvalSample

    by_variant: dict[str, dict[str, Any]] = {}
    total = 0
    degraded_total = 0
    for s in EvalSample.objects.filter(run=run).select_related("variant"):
        vid = str(s.variant_id) if s.variant_id else "__none__"
        row = by_variant.setdefault(
            vid,
            {
                "label": s.variant.label if s.variant_id else "",
                "total": 0,
                "degraded": 0,
                "reasons": {},
                "replay_misses": 0,
                "replay_fuzzy_hits": 0,
            },
        )
        row["total"] += 1
        total += 1
        if s.degraded:
            row["degraded"] += 1
            degraded_total += 1
            key = (s.degraded_reason or "unknown").split(":", 1)[0]
            row["reasons"][key] = row["reasons"].get(key, 0) + 1
        meta = (s.trajectory or {}).get("metadata") or {}
        row["replay_misses"] += int(meta.get("replay_misses") or 0)
        row["replay_fuzzy_hits"] += int(meta.get("replay_fuzzy_hits") or 0)

    return {
        "total": total,
        "degraded": degraded_total,
        "trusted": total - degraded_total,
        "degraded_rate": round(degraded_total / total, 3) if total else 0.0,
        "by_variant": by_variant,
    }


def _count_sample_errors(run) -> dict[str, Any]:
    """``errored`` counts samples whose generation failed; ``evaluator_errors`` counts evaluator
    calls that raised. They are separate because a sample can score on some evaluators and error
    on others, leaving its own ``error`` field empty. Legitimate abstains carry a different
    outcome and are excluded."""
    from django.db.models import Count, Q

    from overbae.models import EvalSample, Score

    samples = list(EvalSample.objects.filter(run=run).select_related("variant"))
    total = len(samples)
    errored = sum(1 for s in samples if s.error)

    # ``outcome=ERROR`` is the source of truth; the reasoning-prefix fallback still
    # counts a row written without an outcome.
    eval_error_scores = Score.objects.filter(
        Q(run=run)
        & (
            Q(outcome=Score.Outcome.ERROR)
            | Q(value__isnull=True, reasoning__startswith=_EVALUATOR_ERROR_PREFIX)
        )
    )
    evaluator_errors = eval_error_scores.count()

    by_variant: dict[str, dict[str, Any]] = {}
    for s in samples:
        vid = str(s.variant_id) if s.variant_id else "__none__"
        if vid not in by_variant:
            by_variant[vid] = {
                "label": s.variant.label if s.variant_id else "",
                "total": 0,
                "errored": 0,
                "evaluator_errors": 0,
            }
        by_variant[vid]["total"] += 1
        if s.error:
            by_variant[vid]["errored"] += 1

    for row in eval_error_scores.values("variant_id").annotate(n=Count("id")):
        vid = str(row["variant_id"]) if row["variant_id"] else "__none__"
        if vid in by_variant:
            by_variant[vid]["evaluator_errors"] = row["n"]

    return {
        "total": total,
        "errored": errored,
        "error_rate": round(errored / total, 3) if total else 0.0,
        "evaluator_errors": evaluator_errors,
        "by_variant": by_variant,
    }
