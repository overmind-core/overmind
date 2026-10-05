import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from modal_shared.decision_inference import input_digest
from modal_shared.decisions import DECISION_OBJECTIVES, decision_request
from modal_shared.serving.artifacts import atomic_json
from modal_shared.stacks import TRAIN_U2026_8_18
from overbae.core import decisions
from overbae.core.errors import InputValidationError
from overbae.core.model_registry import decision_model
from overbae.modal.model_registry import (
    get_all_models_by_backend,
    get_hf_base,
    get_model_config_any_backend,
    get_unsloth_image,
)
from overbae.models import BillingService, DecisionProviderRequest, FinetuningJob
from overbae.services import evaluation_inputs
from overbae.services.billing_ledger import charge_llm_usage


class SubmissionUnknownError(RuntimeError):
    pass


def catalog(project):
    options = [
        {
            "id": model,
            "name": config.get("display", model),
            "kind": "foundation",
            "qualification": "catalog_eligible",
        }
        for model, config in get_all_models_by_backend("baseten", include_disabled=False).items()
        if get_unsloth_image(model) == TRAIN_U2026_8_18
    ]
    options.append(
        {
            "id": decision_model().slug,
            "name": decision_model().name,
            "kind": "external",
            "qualification": "catalog_eligible",
        }
    )
    for job in FinetuningJob.objects.filter(project=project, status="succeeded").order_by(
        "-created_at"
    ):
        if (job.hyperparameters or {}).get("objective") in DECISION_OBJECTIVES:
            options.append(
                {
                    "id": str(job.pk),
                    "name": job.name,
                    "kind": "trained",
                    "qualification": "checkpoint_available",
                }
            )
    return options


def qualify_participants(project, selected):
    if not isinstance(selected, list) or not 1 <= len(selected) <= 8:
        raise InputValidationError("Choose one to eight model participants")
    result = []
    seen = set()
    for participant in selected:
        if not isinstance(participant, dict) or set(participant) - {
            "key",
            "name",
            "kind",
            "job",
            "model",
            "served_model",
        }:
            raise InputValidationError("Unknown participant fields")
        key = participant.get("key", "")
        if (
            not isinstance(key, str)
            or re.fullmatch(r"[a-z][a-z0-9-]{0,47}", key) is None
            or key == "prepare"
            or key in seen
        ):
            raise InputValidationError("Participant keys must be distinct lowercase names")
        seen.add(key)
        name = participant.get("name")
        kind = participant.get("kind")
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 255
            or kind not in {"foundation", "trained", "external"}
        ):
            raise InputValidationError("Choose a named foundation, trained or external participant")
        reference = {"key": key, "name": name, "kind": kind}
        if kind == "external":
            model = participant.get("model")
            served = participant.get("served_model", "")
            if (
                participant.get("job")
                or model != decision_model().slug
                or not isinstance(served, str)
                or (served and served != model and not served.startswith(model + "-"))
            ):
                raise InputValidationError(
                    "External native evaluation requires a catalog System One model and a compatible served version"
                )
            reference.update(model=model, served_model=served)
        elif participant.get("job"):
            job = FinetuningJob.objects.filter(pk=participant["job"], project=project).first()
            if (
                job is None
                or (job.hyperparameters or {}).get("objective") not in DECISION_OBJECTIVES
            ):
                raise InputValidationError("Select a native decision training job in this project")
            if participant.get("model") not in {None, job.base_model} or participant.get(
                "served_model"
            ):
                raise InputValidationError(
                    "A trained reference gets its model identity from the saved job"
                )
            reference.update(job=str(job.pk), model=job.base_model)
        elif kind == "foundation":
            model = participant.get("model")
            if (
                not model
                or get_model_config_any_backend(model) is None
                or get_unsloth_image(model) != TRAIN_U2026_8_18
            ):
                raise InputValidationError(
                    "This foundation has no supported native evaluation runtime"
                )
            reference.update(model=model, hf_model=get_hf_base(model, backend="modal"))
        else:
            raise InputValidationError("A trained participant requires a job")
        result.append(reference)
    return result


def question(request):
    decision_request(request)
    kind, options = request["kind"], request["options"]
    result = {"type": kind, "instructions": request["question"]}
    if kind == "score":
        if len(options) > 10:
            raise InputValidationError(
                "This provider supports score decisions with at most ten options"
            )
        result["criteria"] = options
    elif kind == "noul":
        result["criteria"] = {"false": options[0], "true": options[1]}
    else:
        result["criteria"] = {str(i): value for i, value in enumerate(options)}
    return result


def interpret_response(request, payload, participant):
    expected = participant.get("served_model")
    served = payload.get("model")
    if (
        not isinstance(served, str)
        or (expected and served != expected)
        or (
            not expected
            and served != participant["model"]
            and not served.startswith(participant["model"] + "-")
        )
    ):
        raise InputValidationError("The provider served a different model identity")
    if payload.get("provider") != "TypeSafe" or set(payload.get("answers", {})) != {"decision"}:
        raise InputValidationError("Provider or answer coverage differs from the request")
    answer = payload["answers"]["decision"]
    kind, options = request["kind"], request["options"]
    n = len(options)
    if answer.get("type") != kind:
        raise InputValidationError("Provider changed the decision kind")
    if kind == "noul":
        value = answer.get("noul")
        if type(value) not in {int, float}:
            raise InputValidationError("Invalid binary probability")
        probabilities = [1 - value, value]
    else:
        raw = answer.get("probabilities")
        if not isinstance(raw, dict) or set(raw) != {str(i) for i in range(n)}:
            raise InputValidationError("Provider probability vector is incomplete")
        probabilities = [raw[str(i)] for i in range(n)]
        if kind == "score" and answer.get("legend") != {
            str(i): option for i, option in enumerate(options)
        }:
            raise InputValidationError("Provider score option order changed")
    if any(
        type(v) not in {int, float} or not math.isfinite(v) or not 0 <= v <= 1
        for v in probabilities
    ):
        raise InputValidationError("Provider probability values are invalid")
    total = math.fsum(probabilities)
    digits = next(
        (d for d in range(2, 13) if all(abs(v - round(v, d)) <= 1e-12 for v in probabilities)), 15
    )
    radius = 0.5 * 10**-digits
    lower = math.fsum(max(0, v - radius) for v in probabilities)
    upper = math.fsum(min(1, v + radius) for v in probabilities)
    if total <= 0 or not (abs(total - 1) <= 1e-6 or lower - 1e-8 <= 1 <= upper + 1e-8):
        raise InputValidationError("Probability mass cannot be explained by output rounding")
    p = [v / total for v in probabilities]
    diagnostic = {
        "served_identity": served,
        "identity_evidence": "provider_reported_alias"
        if served == participant["model"]
        else "provider_reported_version",
        "identity_limit": "Provider identity is recorded, not independently verified against weights",
        "original_probabilities": probabilities,
        "original_probability_sum": total,
        "response_decimal_precision": digits,
        "logarithm_floor": 1e-12,
    }
    if kind == "choice":
        selected = str(answer.get("choice"))
        maximum = max(range(n), key=p.__getitem__)
        diagnostic.update(
            provider_reported_choice=selected,
            probability_argmax_index=maximum,
            provider_choice_matches_common_tie_break=selected == str(maximum),
        )
    if kind == "score":
        point = answer.get("score")
        estimate = math.fsum(i * v for i, v in enumerate(p))
        diagnostic.update(
            provider_reported_score=point,
            probability_derived_score=estimate,
            provider_score_difference=point - estimate
            if type(point) in {float, int} and math.isfinite(point)
            else None,
        )
    return {
        "input_sha256": input_digest(request),
        "kind": kind,
        "model_identity": served,
        "probabilities": p,
        "log_probabilities": [math.log(max(v, 1e-12)) for v in p],
        "provider_response_id": payload.get("id"),
    }, diagnostic


def complete_response(record, request, participant):
    vector, diagnostics = interpret_response(request, record.response, participant)
    record.prediction, record.diagnostics, record.state, record.error = (
        vector,
        diagnostics,
        "completed",
        "",
    )
    record.save()


def meter_attempts(record):
    for index, attempt in enumerate(record.attempts):
        charge_llm_usage(
            record.plan.triggered_by,
            attempt.get("usage"),
            service=BillingService.EVALUATION,
            project_id=record.plan.project_id,
            idempotency_key=f"decision-evaluation:{record.pk}:{index}",
            metadata={"plan": str(record.plan_id), "participant": record.participant},
        )


def usage(plan, participant, role):
    entries = DecisionProviderRequest.objects.filter(plan=plan, participant=participant, role=role)
    counts = dict(entries.values("state").annotate(count=Count("id")).values_list("state", "count"))
    measured = 0.0
    unknown = tokens = 0
    for entry in entries.only("usage", "attempts").iterator():
        attempts = entry.attempts or [{"usage": entry.usage}]
        for attempt in attempts:
            stats = attempt.get("usage") or {}
            cost = stats.get("response_cost")
            measured += cost or 0
            unknown += cost is None
            tokens += (stats.get("prompt_tokens") or 0) + (stats.get("completion_tokens") or 0)
    return {
        "requests": counts,
        "recorded_cost_usd": measured,
        "unknown_cost_attempts": unknown,
        "tokens": tokens,
        "cost_basis": "provider_reported_api_usage",
    }


def external_step(plan, role, participant):
    # Suite sealing shares the evaluator's local reference store; requests never receive it.
    # Evaluation imports this adapter; load its suite writer after module initialization.
    from overbae.services.native_evaluation import directory, seal_suite

    participant = dict(participant)
    previous = (
        DecisionProviderRequest.objects.filter(
            plan=plan, participant=participant["key"], state="completed", response__isnull=False
        )
        .order_by("created_at")
        .first()
    )
    if previous is not None:
        served = previous.response["model"]
        if participant.get("served_model") and participant["served_model"] != served:
            raise InputValidationError(
                "Saved provider identity differs from the requested snapshot"
            )
        participant["served_model"] = served
    source = directory(plan, role) / "inputs.jsonl"
    if not (source.parent / "chunks" / "index.json").exists():
        source = seal_suite(plan, role)
    progress = source.parent / f"{participant['key']}-progress.json"
    offset = json.loads(progress.read_text())["rows"] if progress.exists() else 0
    # Qualify one response before sending concurrent requests to a resolved snapshot.
    limit = 128 if participant.get("served_model") else 1
    chunk, total_rows = evaluation_inputs.read_from(source, offset, limit)
    pending = {}
    for row in chunk:
        record, _ = DecisionProviderRequest.objects.get_or_create(
            plan=plan, participant=participant["key"], role=role, input_sha256=row["input_sha256"]
        )
        if record.state == "submitting" or record.state == "submission_unknown":
            raise SubmissionUnknownError(
                "An external request has an unresolved provider acknowledgement"
            )
        if record.state == "received":
            meter_attempts(record)
            complete_response(record, row["decision"], participant)
        elif record.state == "failed":
            raise InputValidationError(
                "An external request exhausted its technical retries or has an invalid response"
            )
        elif record.state != "completed":
            try:
                question(row["decision"])
            except ValueError as exc:
                record.state, record.error = "incompatible", str(exc)
                record.save()
                continue
            pending[record.pk] = (record, row["decision"])
    with ThreadPoolExecutor(max_workers=plan.config["inference"]["concurrency"]) as executor:
        futures = {}
        for record, request in pending.values():
            with transaction.atomic():
                locked = DecisionProviderRequest.objects.select_for_update().get(pk=record.pk)
                if locked.state not in {"pending", "retryable"}:
                    raise SubmissionUnknownError("External request was claimed by another observer")
                locked.state = "submitting"
                locked.attempts = [
                    *locked.attempts,
                    {"intent_at": timezone.now().isoformat(), "usage": {"response_cost": None}},
                ]
                locked.save()
            record.refresh_from_db()
            body = {
                "model": participant.get("served_model") or participant["model"],
                "state": request["state"],
                "questions": {"decision": question(request)},
            }
            futures[executor.submit(decisions.request_once, body)] = (record, request)
        errors = []
        for future in as_completed(futures):
            record, request = futures[future]
            try:
                payload, stats = future.result()
                record.response, record.usage, record.state = payload, stats, "received"
                record.attempts[-1].update(
                    response_id=payload.get("id"),
                    usage=stats,
                    received_at=timezone.now().isoformat(),
                )
                record.save()
                meter_attempts(record)
                complete_response(record, request, participant)
            except decisions.DecisionError as exc:
                code = exc.stats.get("status_code")
                retryable = code == 429 or (isinstance(code, int) and 500 <= code < 600)
                record.attempts[-1].update(error=exc.reason, usage=exc.stats)
                record.state = (
                    "retryable"
                    if retryable and len(record.attempts) < 3
                    else "failed"
                    if code or exc.reason == "not_configured"
                    else "submission_unknown"
                )
                record.error = exc.reason
                record.save()
                meter_attempts(record)
                if record.state == "submission_unknown":
                    errors.append(SubmissionUnknownError(exc.reason))
                elif record.state == "failed":
                    errors.append(ValueError(exc.reason))
            except Exception as exc:
                record.state = "failed" if record.response is not None else "submission_unknown"
                record.error = str(exc)[:1000]
                record.save()
                errors.append(
                    ValueError(str(exc))
                    if record.response is not None
                    else SubmissionUnknownError(str(exc))
                )
        if errors:
            raise errors[0]
    unresolved = (
        DecisionProviderRequest.objects.filter(pk__in=pending)
        .exclude(state__in=["completed", "incompatible"])
        .exists()
    )
    if not unresolved:
        atomic_json(progress, {"rows": offset + len(chunk)})
    completed = offset + len(chunk) == total_rows and not unresolved
    if completed:
        output_path = source.parent / f"{participant['key']}.jsonl"
        failures_path = output_path.with_suffix(".failures.jsonl")
        with (
            output_path.with_suffix(".partial").open("w") as output,
            failures_path.with_suffix(".partial").open("w") as failures,
        ):
            for batch in evaluation_inputs.batches(source):
                saved = {
                    record.input_sha256: record
                    for record in DecisionProviderRequest.objects.filter(
                        plan=plan,
                        participant=participant["key"],
                        role=role,
                        input_sha256__in=[r["input_sha256"] for r in batch],
                    )
                }
                for row in batch:
                    record = saved.get(row["input_sha256"])
                    if record and record.state == "completed":
                        output.write(json.dumps({**record.prediction, "key": row["key"]}) + "\n")
                    elif record and record.state == "incompatible":
                        failures.write(
                            json.dumps(
                                {
                                    "key": row["key"],
                                    "input_sha256": row["input_sha256"],
                                    "reason": record.error,
                                }
                            )
                            + "\n"
                        )
                    else:
                        raise InputValidationError("Provider collection is incomplete")
        output_path.with_suffix(".partial").replace(output_path)
        failures_path.with_suffix(".partial").replace(failures_path)
    return {
        "completed": completed,
        "decisions": offset + len(chunk),
        "served_model": participant.get("served_model"),
        "identity_basis": "provider_reported",
        **(
            usage(plan, participant["key"], role)
            if completed
            else {"cost_basis": "request_ledger_pending_final_aggregation"}
        ),
    }
