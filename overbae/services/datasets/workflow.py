import hashlib
import json

from django.db import transaction
from django.utils import timezone

from overbae.models import Dataset, WorkshopRun, WorkshopWorkItem
from overbae.services.datasets import generation, review

MAX_FAILED_ATTEMPTS = 3
MAX_ACTIONS = 120
READ_ONLY = {"status", "query", "inspect", "diff", "try_script"}


def current(dataset_id):
    return (
        WorkshopRun.objects.filter(dataset_id=dataset_id, kind="preparation")
        .order_by("-created_at")
        .first()
    )


def start(dataset, *, request="", user=None, owner=""):
    plan = dataset.preparation_plan or {}
    return WorkshopRun.objects.create(
        dataset=dataset,
        request=request or dataset.brief,
        source=dataset.active_cell,
        source_fingerprint=dataset.active_cell.fingerprint if dataset.active_cell else "",
        owner=owner,
        created_by=user,
        state=WorkshopRun.State.RUNNING,
        plan=plan,
        specification=plan.get("specification", {}).get("outcome") or {},
    )


def ensure(dataset, *, user=None):
    return current(dataset.pk) or start(dataset, user=user)


def describe(dataset):
    run = current(dataset.pk)
    if run is None:
        return {}
    generated = (
        WorkshopRun.objects.filter(dataset=dataset, kind="generation")
        .order_by("-created_at")
        .first()
    )
    value = {
        "id": str(run.pk),
        "state": run.state,
        "revision": run.revision,
        "request": run.request,
        "original_request": dataset.brief,
        "outcome": run.specification,
        "result": run.result,
        "failure": run.failure,
        "source_cell": str(run.source_id) if run.source_id else None,
        "output_cell": str(run.output_id) if run.output_id else None,
        "updated_at": run.updated_at.isoformat(),
    }
    if generated is not None:
        value["generation"] = generation.describe(generated.pk)
    return value


@transaction.atomic
def record(run_id, *, tool, arguments, result, source_fingerprint):
    run = WorkshopRun.objects.select_for_update(of=("self",)).get(pk=run_id)
    failure = result.get("failure") or {}
    ok = result.get("ok") is not False and not result.get("error")
    code = failure.get("code", "invalid_arguments" if not ok else "")
    key = hashlib.sha256(
        f"{tool}:{code}:{source_fingerprint}:{run.plan.get('id', '')}".encode()
    ).hexdigest()
    attempts = dict(run.recovery)
    if ok and tool in READ_ONLY:
        read_key = (
            "read:"
            + hashlib.sha256(
                json.dumps(
                    [tool, arguments, source_fingerprint, run.plan.get("id")],
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
        )
        attempts[read_key] = attempts.get(read_key, 0) + 1
        if attempts[read_key] >= 4:
            run.state = WorkshopRun.State.BLOCKED
            run.failure = {
                "code": "no_progress",
                "detail": "The same inspection repeated without a change to the data or plan.",
                "tool": tool,
                "retryable": False,
                "action": "Inspect the saved findings and revise the preparation approach.",
            }
    elif ok and not result.get("unchanged"):
        attempts = {key: value for key, value in attempts.items() if not key.startswith("read:")}
    if not ok:
        attempts[key] = attempts.get(key, 0) + 1
        failure = {
            "code": code,
            "category": failure.get("category", "input"),
            "detail": str(result.get("error", "Operation failed."))[:600],
            "attempts": attempts[key],
            "retryable": attempts[key] < MAX_FAILED_ATTEMPTS,
            "action": failure.get(
                "action", "Inspect the pinned input and revise the failed operation."
            ),
            "tool": tool,
            "source_fingerprint": source_fingerprint,
        }
        result = {**result, "failure": failure}
        run.failure = failure
        if attempts[key] >= MAX_FAILED_ATTEMPTS:
            run.state = WorkshopRun.State.BLOCKED
    run.recovery = attempts
    run.revision += 1
    if run.revision >= MAX_ACTIONS:
        run.state = WorkshopRun.State.BLOCKED
        run.failure = {
            "code": "action_budget",
            "detail": "Preparation reached its action limit.",
            "retryable": False,
            "action": "Resume the saved workflow after inspecting its progress.",
        }
    WorkshopWorkItem.objects.create(
        run=run,
        key=f"tool:{run.revision}",
        kind="tool",
        state="complete" if ok else "rejected",
        inputs={"tool": tool, "arguments": arguments, "source_fingerprint": source_fingerprint},
        result=result,
        failure=failure,
    )
    run.save(update_fields=["state", "failure", "recovery", "revision", "updated_at"])
    return result, run.state == WorkshopRun.State.BLOCKED


def bind_plan(dataset, plan):
    run = ensure(dataset)
    WorkshopRun.objects.filter(pk=run.pk).update(
        plan=plan,
        specification=plan.get("specification", {}).get("outcome") or {},
        updated_at=timezone.now(),
    )


def finish(run_id, *, error="", require_prepared=True):
    run = WorkshopRun.objects.select_related("dataset__capability").get(pk=run_id)
    dataset = run.dataset
    active = dataset.active_cell
    generated = run.children.filter(kind="generation").order_by("-created_at").first()
    if generated is None and active is not None:
        generated = (
            WorkshopRun.objects.filter(
                dataset=dataset, kind="generation", output_id__in=[active.pk, run.source_id]
            )
            .order_by("-created_at")
            .first()
        )
    if generated and generated.state in {"queued", "running", "paused"}:
        state = generated.state
    elif run.state in {WorkshopRun.State.BLOCKED, WorkshopRun.State.CANCELLED}:
        state = run.state
    elif error or generated and generated.generated_rows < generation.target_count(generated):
        state = WorkshopRun.State.PARTIAL
    elif not require_prepared or dataset.intent == Dataset.Intent.EXPLORE:
        state = WorkshopRun.State.COMPLETE
    elif active is None or not active.fits(dataset.intent)[0]:
        state = WorkshopRun.State.PARTIAL
    else:
        state = WorkshopRun.State.COMPLETE
    assessment = review.readiness(dataset, active) if active else {}
    required = run.specification.get("required_checks", [])
    measured = {
        c["name"]
        for c in (active.quality_report if active else {}).get("checks", [])
        if c.get("rows_checked", 0) > 0
    }
    missing = sorted(set(required) - measured)
    target = run.specification.get("target_rows")
    if state == WorkshopRun.State.COMPLETE and (
        missing or target and (active is None or active.rows < target)
    ):
        state = WorkshopRun.State.PARTIAL
    result = {
        "execution": state,
        "compatibility": assessment.get("format_valid", False),
        "rows": active.rows if active else 0,
        "quality": assessment,
        "missing_checks": missing,
        "task_completion": {
            "status": state,
            "requested_rows": target or (generated.target_rows if generated else None),
            "delivered_rows": (
                generated.generated_rows
                + (
                    generated.specification["source_rows"]
                    if generated.specification["mode"] == "augment"
                    else 0
                )
                if generated and not generated.output_id
                else active.rows
                if active
                else 0
            ),
            "deliverables": run.specification.get("deliverables", []),
            "coverage_objective": run.specification.get("coverage", ""),
            "source_rows_without_examples": generation.describe(generated.pk)[
                "source_rows_without_examples"
            ]
            if generated
            else None,
        },
        "error": error,
    }
    if state == WorkshopRun.State.COMPLETE and run.failure:
        result["recovered_failure"] = run.failure
        run.failure = {}
    elif run.result.get("recovered_failure"):
        result["recovered_failure"] = run.result["recovered_failure"]
    WorkshopRun.objects.filter(pk=run.pk).update(
        state=state, result=result, output=active, failure=run.failure, updated_at=timezone.now()
    )
    return result
