import hashlib
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

import modal
from django.db import transaction
from django.utils import timezone

from modal_shared.decision_inference import input_digest
from modal_shared.decision_metrics import probability_distribution
from overbae.core import decisions
from overbae.core.errors import InputValidationError
from overbae.models import (
    BillingService,
    DecisionPerformanceRequest,
    DecisionPerformanceRun,
    FinetuningJob,
    Project,
)
from overbae.services import native_evaluation
from overbae.services.billing_ledger import charge_llm_usage
from overbae.services.compute_costs import estimate_usage
from overbae.services.decision_benchmark_scoring import records
from overbae.services.decision_providers import interpret_response, question


@transaction.atomic
def create(evaluation, *, name, request_key, workload):
    workload = (
        {"amortization_decisions": 1000000, **workload} if isinstance(workload, dict) else workload
    )
    bounds = {
        "amortization_decisions": (1, 1000000000000),
        "sample_size": (1, 64),
        "repetitions": (1, 10),
        "concurrency": (1, 16),
        "seed": (0, 4294967295),
        "questions_per_request": (1, 16),
    }
    if (
        not isinstance(workload, dict)
        or set(workload) != set(bounds)
        or any(
            type(workload[k]) is not int or not low <= workload[k] <= high
            for k, (low, high) in bounds.items()
        )
    ):
        raise InputValidationError(
            "Choose a bounded performance workload: 1–64 states, 1–10 repetitions, 1–16 concurrency/questions and a 32-bit seed"
        )
    qualified = {}
    for participant in native_evaluation.participants(evaluation):
        if participant["kind"] == "external":
            qualified[participant["key"]] = {
                "role": "final",
                "served_model": participant.get("served_model"),
            }
            continue
        found = next(
            (
                (role, evaluation.calls.get(f"{role}_{participant['key']}", {}).get("receipt", {}))
                for role in ("calibration", "final")
                if evaluation.calls.get(f"{role}_{participant['key']}", {}).get("state")
                == "completed"
            ),
            None,
        )
        if found is None:
            raise InputValidationError(
                "Native performance requires a verified prediction receipt; quality scoring need not be complete"
            )
        role, receipt = found
        path = native_evaluation.directory(evaluation, role) / f"{participant['key']}.jsonl"
        with path.open() as stream:
            prediction = json.loads(next(stream))
        qualified[participant["key"]] = {
            "role": role,
            "model_identity": prediction["model_identity"],
            "runtime": receipt.get("runtime_fingerprint"),
        }
        if (
            not qualified[participant["key"]]["model_identity"]
            or not qualified[participant["key"]]["runtime"]
        ):
            raise InputValidationError("Native qualification identity is incomplete")
    Project.objects.select_for_update().get(pk=evaluation.project_id)
    prior = DecisionPerformanceRun.objects.filter(
        project_id=evaluation.project_id, request_key=request_key
    ).first()
    if prior:
        if (
            prior.name != name
            or prior.evaluation_id != evaluation.pk
            or prior.workload["settings"] != workload
        ):
            raise InputValidationError(
                "This key already identifies a different performance workload"
            )
        return prior
    return DecisionPerformanceRun.objects.create(
        project_id=evaluation.project_id,
        evaluation=evaluation,
        name=name,
        request_key=request_key,
        workload={
            "settings": workload,
            "qualification": qualified,
            "source": evaluation.config["suites"]["final"],
        },
    )


def prepare_workload(run):
    if "inputs" in run.workload:
        return
    evaluation = run.evaluation
    workload, qualified = run.workload["settings"], run.workload["qualification"]
    if run.workload["source"] != evaluation.config["suites"]["final"]:
        raise InputValidationError("The frozen performance source changed")
    source = native_evaluation.seal_suite(evaluation, "final")
    generator = random.Random(workload["seed"])
    states, chosen = set(), []
    # State reservoirs preserve shared-state questions without loading all decisions.
    seen = 0
    for row in records(source):
        state = row["decision"]["state"]
        state_hash = hashlib.sha256(state.encode()).hexdigest()
        if state_hash in states:
            continue
        states.add(state_hash)
        seen += 1
        if len(chosen) < workload["sample_size"]:
            chosen.append(state)
        else:
            position = generator.randrange(seen)
            if position < len(chosen):
                chosen[position] = state
    groups = {state: [] for state in chosen}
    for row in records(source):
        group = groups.get(row["decision"]["state"])
        if group is not None and len(group) < workload["questions_per_request"]:
            group.append(row["decision"])
    inputs = [groups[state] for state in chosen if groups[state]]
    if not inputs:
        raise InputValidationError("The suite has no runnable performance inputs")
    frozen = {
        "qualification": qualified,
        "settings": workload,
        "inputs": inputs,
        "input_sha256": [[input_digest(r) for r in group] for group in inputs],
        "shape": [
            {
                "state_characters": len(group[0]["state"]),
                "questions": len(group),
                "option_counts": [len(r["options"]) for r in group],
            }
            for group in inputs
        ],
        "source": evaluation.config["suites"]["final"],
        "latency_basis": "client wall time through complete response, including transport and provider queueing",
        "throughput_basis": "valid decisions per active client measurement window; excludes observer downtime",
    }
    run.workload = frozen
    DecisionPerformanceRun.objects.filter(pk=run.pk).update(
        workload=frozen, updated_at=timezone.now()
    )


def external_request(participant, requests):
    body = {
        "model": participant.get("served_model") or participant["model"],
        "state": requests[0]["state"],
        "questions": {
            ("decision" if len(requests) == 1 else str(i)): question(request)
            for i, request in enumerate(requests)
        },
    }
    started = time.perf_counter()
    payload, usage = decisions.request_once(body)
    elapsed = (time.perf_counter() - started) * 1000
    return payload, usage, elapsed


def validate_response(participant, requests, payload):
    if participant["kind"] == "external":
        keys = {"decision"} if len(requests) == 1 else {str(i) for i in range(len(requests))}
        if set(payload.get("answers", {})) != keys:
            raise InputValidationError("Performance response coverage changed")
        predictions = []
        for index, request in enumerate(requests):
            key = "decision" if len(requests) == 1 else str(index)
            vector, _ = interpret_response(
                request, {**payload, "answers": {"decision": payload["answers"][key]}}, participant
            )
            predictions.append(vector)
        return predictions
    if payload.get("model_identity") != participant["quality_identity"]:
        raise InputValidationError("Performance model identity differs from the quality comparison")
    if payload.get("runtime_fingerprint") != participant["quality_runtime"]:
        raise InputValidationError("Performance runtime differs from the quality comparison")
    predictions = payload["predictions"]
    if len(predictions) != len(requests):
        raise InputValidationError("Performance response coverage changed")
    for prediction, request in zip(predictions, requests, strict=True):
        if prediction["input_sha256"] != input_digest(request):
            raise InputValidationError("Performance response input changed")
        probability_distribution(prediction["probabilities"], len(request["options"]))
    return predictions


def endpoint(run, participant):
    role = run.workload["qualification"][participant["key"]]["role"]
    plan = run.evaluation
    while participant["key"] in plan.config.get("reuse", {}):
        lineage = plan.config["reuse"][participant["key"]]
        plan = type(plan).objects.get(pk=lineage["evaluation"], project_id=run.project_id)
        participant = next(
            p for p in native_evaluation.participants(plan) if p["key"] == lineage["participant"]
        )
    group = native_evaluation.preparation_groups(plan)[
        participant.get("job") or participant["key"]
    ][0]
    evaluation_id = f"{plan.pk}-{role}" + ("" if group == "prepare" else "-" + group)
    job = (
        FinetuningJob.objects.get(pk=participant["job"], project_id=run.project_id)
        if participant.get("job")
        else None
    )
    model_run = job.remote_job_id.split(":", 1)[0] if job else ""
    runtime = plan.config["runtime"]
    worker = modal.Cls.from_name(
        runtime["app"], "DecisionPerformanceEndpoint", environment_name=runtime["environment"]
    )
    return worker(
        evaluation_id=evaluation_id,
        model_run=model_run,
        base_only=participant["kind"] == "foundation",
        measurement_id=str(run.pk),
    )


def native_result(call, started):
    payload = call.get(timeout=900)
    return (
        payload,
        {"response_cost": None},
        (time.perf_counter() - started) * 1000 if started is not None else None,
    )


def percentiles(values):
    values = sorted(values)

    def percentile(q):
        return values[round(q * (len(values) - 1))] if values else None

    return {
        "count": len(values),
        "p50": percentile(0.5),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
    }


def advance(run_id):
    with transaction.atomic():
        run = (
            DecisionPerformanceRun.objects.select_for_update(of=("self",))
            .select_related("evaluation__triggered_by", "evaluation__final_cell__dataset")
            .get(pk=run_id)
        )
        if run.state == "completed" or (
            run.state == "running" and run.updated_at > timezone.now() - timedelta(seconds=1260)
        ):
            return
        run.state = "running"
        run.save()
    settings = run.workload["settings"]
    totals = dict(run.results.get("active_seconds", {}))
    try:
        prepare_workload(run)
        inputs = run.workload["inputs"] * settings["repetitions"]
        for original in native_evaluation.participants(run.evaluation):
            participant = dict(original)
            native = participant["kind"] != "external"
            if not native:
                identity = (
                    run.evaluation.requests.filter(
                        participant=participant["key"], state="completed"
                    )
                    .order_by("created_at")
                    .first()
                )
                if identity:
                    participant["served_model"] = identity.response["model"]
            if not native:
                retained = (
                    run.requests.filter(participant=participant["key"], response__isnull=False)
                    .order_by("position")
                    .first()
                )
                if retained:
                    participant["served_model"] = participant.get(
                        "served_model"
                    ) or retained.response.get("model")
            if native:
                qualified = run.workload["qualification"][participant["key"]]
                participant["quality_identity"] = qualified["model_identity"]
                participant["quality_runtime"] = qualified["runtime"]
            worker = endpoint(run, participant) if native else None
            # The first native request includes a fresh worker's startup; later ones reuse it.
            windows = [
                [0],
                *[
                    list(range(i, min(i + settings["concurrency"], len(inputs))))
                    for i in range(1, len(inputs), settings["concurrency"])
                ],
            ]
            for window in windows:
                started_window = time.perf_counter()
                launched = 0
                with ThreadPoolExecutor(max_workers=settings["concurrency"]) as executor:
                    futures = {}
                    for position in window:
                        request, _ = DecisionPerformanceRequest.objects.get_or_create(
                            run=run, participant=participant["key"], position=position
                        )
                        if request.response is not None and request.state != "completed":
                            try:
                                validate_response(participant, inputs[position], request.response)
                                request.state, request.error = "completed", ""
                            except (ValueError, KeyError, TypeError) as exc:
                                request.state, request.error = "failed", type(exc).__name__
                            request.save()
                            charge_llm_usage(
                                run.evaluation.triggered_by,
                                request.usage,
                                service=BillingService.EVALUATION,
                                project_id=run.project_id,
                                idempotency_key=f"decision-performance:{request.pk}",
                                metadata={"performance_run": str(run.pk)},
                            )
                        if request.state == "submitting" and not request.call_id:
                            request.state, request.error = (
                                "submission_unknown",
                                "Request acknowledgement was lost; it was not repeated",
                            )
                            request.save()
                        if request.state in {"completed", "failed"} or (
                            request.state == "submission_unknown"
                            and not (native and request.call_id)
                        ):
                            continue
                        request.state = "submitting"
                        request.save()
                        if native:
                            clock = None if request.call_id else time.perf_counter()
                            call = (
                                modal.FunctionCall.from_id(request.call_id)
                                if request.call_id
                                else worker.predict.spawn(inputs[position])
                            )
                            request.call_id = call.object_id
                            request.save()
                            future = executor.submit(native_result, call, clock)
                        else:
                            future = executor.submit(
                                external_request, participant, inputs[position]
                            )
                        futures[future] = request
                        launched += 1
                    for future in as_completed(futures):
                        request = futures[future]
                        try:
                            payload, usage, latency = future.result()
                            request.response, request.usage, request.latency_ms = (
                                payload,
                                usage,
                                latency,
                            )
                            request.save()
                            validate_response(participant, inputs[request.position], payload)
                            if not native and not participant.get("served_model"):
                                participant["served_model"] = payload["model"]
                            request.state = "completed"
                        except Exception as exc:
                            request.error = type(exc).__name__
                            request.state = (
                                "failed"
                                if request.response is not None
                                or isinstance(exc, decisions.DecisionError)
                                and exc.stats.get("status_code")
                                or native
                                and isinstance(
                                    exc,
                                    (
                                        ValueError,
                                        RuntimeError,
                                        modal.exception.RemoteError,
                                        modal.exception.FunctionTimeoutError,
                                        modal.exception.ExecutionError,
                                    ),
                                )
                                else "submission_unknown"
                            )
                            if isinstance(exc, decisions.DecisionError):
                                request.usage = exc.stats
                        request.save()
                        charge_llm_usage(
                            run.evaluation.triggered_by,
                            request.usage,
                            service=BillingService.EVALUATION,
                            project_id=run.project_id,
                            idempotency_key=f"decision-performance:{request.pk}",
                            metadata={"performance_run": str(run.pk)},
                        )
                if launched:
                    totals[participant["key"]] = (
                        totals.get(participant["key"], 0) + time.perf_counter() - started_window
                    )
                    DecisionPerformanceRun.objects.filter(pk=run.pk).update(
                        results={"active_seconds": totals}, updated_at=timezone.now()
                    )
        if run.requests.filter(state="submission_unknown").exclude(call_id="").exists():
            DecisionPerformanceRun.objects.filter(pk=run.pk).update(
                state="queued", results={"active_seconds": totals}, updated_at=timezone.now()
            )
            return
        result = {
            "participants": {},
            "workload": run.workload,
            "active_seconds": totals,
            "cost_basis": "provider-reported API charges; native GPU/CPU/memory estimates use worker usage and current rates; full invoices remain unknown",
        }
        for participant in native_evaluation.participants(run.evaluation):
            requests = list(
                run.requests.filter(participant=participant["key"]).order_by("position")
            )
            valid = [r for r in requests if r.state == "completed"]
            count = sum(len(inputs[r.position]) for r in valid)
            seconds = totals.get(participant["key"], 0)
            latencies = [r.latency_ms for r in valid if r.latency_ms is not None]
            native = participant["kind"] != "external"
            first_instance = (
                requests[0].response.get("worker_instance")
                if requests and requests[0].response
                else None
            )
            warm = [
                r
                for r in valid
                if r.position > 0
                and r.latency_ms is not None
                and first_instance
                and r.response.get("worker_instance") == first_instance
            ]
            job = (
                FinetuningJob.objects.filter(
                    pk=participant.get("job"), project_id=run.project_id
                ).first()
                if participant.get("job")
                else None
            )
            recorded = sum(r.usage.get("response_cost") or 0 for r in requests)
            unknown_costs = sum(r.usage.get("response_cost") is None for r in requests)
            marginal = recorded / count if count and not unknown_costs else None
            training_cost = (
                float(job.cost_usd) if job and job.cost_usd is not None else 0 if not job else None
            )
            compute = (
                estimate_usage([r.response.get("compute_usage", {}) for r in requests])
                if native
                else None
            )
            result["participants"][participant["key"]] = {
                "native_compute": compute,
                "estimated_compute_usd_per_valid_decision": compute["estimated_usd"] / count
                if compute and compute["estimated_usd"] is not None and count
                else None,
                "recorded_training_usd": training_cost,
                "marginal_recorded_usd_per_valid_decision": marginal,
                "amortized_recorded_component_usd_per_decision": marginal
                + training_cost / settings["amortization_decisions"]
                if marginal is not None and training_cost is not None
                else None,
                "amortization_scope": "recorded training and measured request costs only; excludes unreported components and is not an invoice forecast",
                "expected_requests": len(inputs),
                "valid_requests": len(valid),
                "valid_decisions": count,
                "failed_requests": sum(r.state == "failed" for r in requests),
                "unknown_requests": sum(r.state == "submission_unknown" for r in requests),
                "unmeasured_latency_requests": sum(r.latency_ms is None for r in valid),
                "latency_ms": percentiles(latencies),
                "first_request_ms": requests[0].latency_ms if requests else None,
                "warm_latency_ms": percentiles([r.latency_ms for r in warm]) if native else None,
                "valid_decisions_per_active_second": count / seconds
                if seconds and all(r.latency_ms is not None for r in valid)
                else None,
                "worker_instances": sorted(
                    {
                        r.response["worker_instance"]
                        for r in valid
                        if r.response.get("worker_instance")
                    }
                ),
                "recorded_cost_usd": sum(r.usage.get("response_cost") or 0 for r in requests),
                "unknown_cost_requests": sum(
                    r.usage.get("response_cost") is None for r in requests
                ),
                "conditions": {
                    "native_worker": native,
                    "hardware": "H100" if native else "provider controlled",
                    "provider_cache": "disabled" if native else "uncontrolled",
                    "cold_start": "first observed worker request; warm requires matching instance"
                    if native
                    else "provider controlled",
                    "concurrency": settings["concurrency"],
                    "shared_state_questions": settings["questions_per_request"],
                },
            }
        DecisionPerformanceRun.objects.filter(pk=run.pk).update(
            state="completed", results=result, error="", updated_at=timezone.now()
        )
    except Exception as exc:
        DecisionPerformanceRun.objects.filter(pk=run.pk).update(
            state="failed", error=type(exc).__name__, updated_at=timezone.now()
        )
        raise


def describe(run):
    return {
        "id": str(run.pk),
        "name": run.name,
        "state": run.state,
        "evaluation": str(run.evaluation_id),
        "workload": run.workload,
        "results": run.results,
        "error": run.error,
        "requests": list(
            run.requests.values(
                "id", "participant", "position", "state", "call_id", "latency_ms", "usage", "error"
            )
        ),
    }
