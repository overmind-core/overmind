import hashlib
import json
from itertools import islice

from django.db import transaction
from django.utils import timezone

from overbae.models import WorkshopRun, WorkshopWorkItem
from overbae.services import chatgpt
from overbae.services.billing_ledger import ensure_credits, record_workshop_usage
from overbae.services.datasets import generation, review, semantic_checks, store


def qualify(run_id):
    run = generation.get(run_id)
    generation.validate_source(run)
    batches = list(run.items.filter(kind="generation", state="complete").order_by("created_at"))
    context = generation.request_context(run)
    fingerprint = hashlib.sha256(
        store.json_dumps([context, [b.fingerprint for b in batches]]).encode()
    ).hexdigest()
    cached = run.items.filter(key=f"qualification:{fingerprint}", state="complete").first()
    if cached:
        charge(run, cached)
        return cached.result
    records = []
    with generation.source_index(run) as con:
        for batch in batches:
            path = generation.directory(run) / batch.artifact
            if store.file_sha256(path) != batch.fingerprint:
                raise ValueError("The saved pilot artifact changed.")
            for row in islice(store.iter_rows(path), max(0, 8 - len(records))):
                provenance = row[review.PROVENANCE_COLUMN]
                source = con.execute(
                    "SELECT body FROM seeds WHERE identity=?", (provenance["seed_row"],)
                ).fetchone()
                records.append(
                    {
                        "source": json.loads(source[0]),
                        "example": {
                            k: v
                            for k, v in row.items()
                            if k not in {store.SOURCE_ROW, review.PROVENANCE_COLUMN}
                        },
                        "evidence": provenance["evidence"],
                    }
                )
    if not records:
        raise ValueError("Save representative examples before qualifying the recipe.")
    questions = [
        "Does the example fulfil the user's original request and any explicit revision, including what the eventual model receives at inference? Judge the recipe against that request, not just the example against the recipe. Generator-only source evidence need not appear in model inputs: knowledge-learning questions and passage-conditioned comprehension are different tasks. If the request does not resolve that distinction, report insufficient rather than inventing a requirement.",
        "Are the answer and question supported by the independent source evidence, without introducing unsupported facts?"
        if run.specification["mode"] == "derive"
        else "Is this requested synthetic variant internally consistent and faithful to the source task and labels? Do not claim it is observed ground truth.",
    ]
    checks = [
        semantic_checks.SemanticCheck(
            name=name, question=question, evidence_columns=["source"], answer_columns=["example"]
        )
        for name, question in zip(["task_alignment", "answer_support"], questions, strict=True)
    ]
    session = chatgpt.selected_session(run.created_by)
    if run.created_by and session is None:
        ensure_credits(run.created_by)
    with transaction.atomic():
        locked = WorkshopRun.objects.select_for_update().get(pk=run_id)
        if locked.state in {"cancelled", "paused", "complete"}:
            raise ValueError("This generation has stopped.")
        generation.validate_source(run)
        unit, _ = WorkshopWorkItem.objects.get_or_create(
            run_id=run_id,
            key=f"qualification:{fingerprint}",
            defaults={
                "kind": "qualification",
                "inputs": {"fingerprint": fingerprint},
                "state": "pending",
            },
        )
        if unit.state == "complete":
            charge(run, unit)
            return unit.result
        if unit.state != "pending":
            raise ValueError(
                "Recipe qualification has an unresolved provider receipt; it will not be repeated automatically."
            )
        unit.state = "submitting"
        unit.attempts = 1
        unit.save(update_fields=["state", "attempts", "updated_at"])
    try:
        outcome = semantic_checks.evaluate_batch(
            records,
            checks,
            context,
            project_id=run.dataset.project_id,
            contract=fingerprint,
            chatgpt_session=session,
        )
    except Exception:
        WorkshopRun.objects.filter(pk=run_id).update(
            state="blocked", failure={"code": "qualification_outcome_unknown", "retryable": False}
        )
        raise
    answers = getattr(outcome.parsed, "answers", {}) or {}
    expected = {f"r{i}_c{j}" for i in range(len(records)) for j in range(len(checks))}
    if set(answers) != expected:
        answers = dict.fromkeys(expected, "insufficient")
    status = (
        "failed"
        if "fail" in answers.values()
        else "unmeasured"
        if "insufficient" in answers.values()
        else "passed"
    )
    result = {
        "status": status,
        "checked_rows": len(records),
        "pilot_rows": run.generated_rows,
        "results": answers,
        "source_rows": [row["source"][store.SOURCE_ROW] for row in records],
        "fingerprint": fingerprint,
        "limitations": "Model judgments on the saved pilot; final quality and uncovered source families remain unmeasured.",
    }
    WorkshopWorkItem.objects.filter(pk=unit.pk).update(
        state="complete",
        result=result,
        usage=outcome.stats,
        provider={
            "response_id": outcome.judge_trace_id,
            "response": outcome.raw,
            "funding_source": "chatgpt" if session else "platform",
        },
        updated_at=timezone.now(),
    )
    run.refresh_from_db()
    if run.state not in {"cancelled", "paused"}:
        WorkshopRun.objects.filter(pk=run_id).update(
            result={**run.result, "qualification": result},
            **(
                {
                    "state": "blocked",
                    "failure": {
                        "code": "recipe_qualification",
                        "retryable": False,
                        "detail": "Revise the recipe using the failed pilot checks. Saved examples remain available.",
                    },
                }
                if status == "failed"
                else {}
            ),
        )
    unit.refresh_from_db()
    charge(run, unit)
    return result


def charge(run, unit):
    record_workshop_usage(
        run.created_by,
        unit.usage,
        funding_source=unit.provider.get("funding_source", "platform"),
        project_id=run.dataset.project_id,
        idempotency_key=f"workshop-qualification:{unit.pk}",
        metadata={
            "run_id": str(run.pk),
            "dataset_id": str(run.dataset_id),
            "workload": "recipe_qualification",
        },
    )
