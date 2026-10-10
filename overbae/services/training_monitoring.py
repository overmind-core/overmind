import json
from datetime import UTC, datetime

import jsonschema
import modal
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from modal_shared.decisions import DECISION_OBJECTIVES
from modal_shared.training_monitoring import fingerprint, resolve_policy
from overbae.models import FinetuningJob, TrainingCheckpoint, TrainingValidationRun
from overbae.services import operational_progress, training_experience, training_quality
from overbae.services.training_release import for_job as job_release

TERMINAL = {"completed", "failed", "interrupted", "skipped"}


@transaction.atomic
def save_plan(job, plan):
    current = FinetuningJob.objects.select_for_update().get(pk=job.pk)
    expected = (current.hyperparameters or {}).get("monitoring")
    if (
        plan["policy"] != expected
        or fingerprint({"policy": expected, "probes": plan["probes"]}) != plan["identity"]
    ):
        raise ValueError("The prepared monitoring manifest does not match the frozen recipe")
    existing = (current.progress or {}).get("monitoring_manifest")
    if existing:
        if existing["identity"] != plan["identity"]:
            raise ValueError("The prepared monitoring manifest changed")
        return
    path = default_storage.save(
        f"training-monitoring/{job.pk}/{plan['identity']}.json",
        ContentFile(json.dumps(plan).encode()),
    )
    summary = {
        name: {key: value for key, value in probe.items() if key not in {"indices", "identities"}}
        for name, probe in plan["probes"].items()
    }
    FinetuningJob.objects.filter(pk=job.pk).update(
        progress={
            **(current.progress or {}),
            "monitoring_manifest": {"path": path, "identity": plan["identity"], "probes": summary},
        }
    )


def probe_rows(job, probe, *, offset=0, limit=25):
    saved = (job.progress or {}).get("monitoring_manifest")
    if not saved:
        return {"available": False, "count": 0, "items": [], "next_offset": None}
    with default_storage.open(saved["path"], "rb") as stream:
        plan = json.load(stream)
    if fingerprint({"policy": plan["policy"], "probes": plan["probes"]}) != saved["identity"]:
        raise ValueError("Stored monitoring manifest checksum mismatch")
    selected = plan["probes"].get(probe)
    if not selected:
        raise ValueError("This probe was not requested")
    rows = selected["identities"]
    return {
        "available": True,
        "count": len(rows),
        "items": rows[offset : offset + limit],
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "sha256": selected["fingerprint"],
        "metadata": saved["probes"][probe],
    }


def resolve(hyperparameters, *, has_development, provider, monitoring=None):
    supplied = monitoring if monitoring is not None else hyperparameters.get("monitoring")
    value = dict(supplied) if isinstance(supplied, dict) else supplied
    native = hyperparameters.get("objective") in DECISION_OBJECTIVES
    if native:
        if value is None:
            value = {} if has_development else {"mode": "off"}
        if not isinstance(value, dict):
            raise ValueError("monitoring must be an object")
        baseline = hyperparameters.get("pre_training_baseline")
        if baseline is not None:
            if type(baseline) is not bool:
                raise ValueError("pre_training_baseline must be a boolean")
            if "initial" in value and value["initial"] != baseline:
                raise ValueError("monitoring.initial conflicts with pre_training_baseline")
            value["initial"] = baseline
        checkpoint = hyperparameters.get("checkpoint_policy")
        if checkpoint:
            if "selection" in value and value["selection"] != checkpoint["selection"]:
                raise ValueError("Monitoring and native checkpoint selection conflict")
            value["selection"] = checkpoint["selection"]
    policy = resolve_policy(value, has_development=has_development, provider=provider)
    generation = policy["generation"]
    if generation and native:
        raise ValueError("Native decisions use probability metrics, not generation probes")
    if generation and provider == "baseten":
        raise ValueError(
            "Baseten generation evidence collection is not qualified; use loss monitoring or Modal"
        )
    if generation and generation["kind"] == "json_schema":
        schema = generation["schema"]
        try:
            jsonschema.validators.validator_for(schema).check_schema(schema)
        except jsonschema.SchemaError as exc:
            raise ValueError(f"Invalid generation schema: {exc.message}") from exc
        pending = [schema]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                for key, value in item.items():
                    if key in {"$ref", "$dynamicRef"} and not value.startswith("#"):
                        raise ValueError("Generation schema references must be local")
                    pending.append(value)
            elif isinstance(item, list):
                pending.extend(item)
    return policy


def _timestamp(value):
    return datetime.fromtimestamp(value, UTC) if value is not None else None


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _record_check(job, check):
    assessment = training_quality.assessment(job, check)
    if assessment is not None:
        check.facts = {**check.facts, "assessment": assessment}
    operation = operational_progress.record(
        job.project_id,
        "training_validation",
        check.id,
        check.attempt,
        stage=check.stream,
        status={"completed": "complete", "interrupted": "cancelled", "skipped": "complete"}.get(
            check.state, check.state
        ),
        completed=check.coverage.get("scored"),
        total=check.coverage.get("expected"),
        unit="rows",
        source_at=check.observed_at,
        facts={
            "check_id": str(check.pk),
            "sample_fingerprint": check.sample_fingerprint,
            "policy_fingerprint": check.policy_fingerprint,
            "trained_steps": check.step,
            "finding_count": len(check.facts.get("findings") or [])
            + len((assessment or {}).get("findings") or []),
            "error_code": check.failure.get("code"),
            "telemetry_scope": "development measurement",
        },
    )
    check.facts = {**check.facts, "operation_id": str(operation.pk)}


@transaction.atomic
def ingest(job, payload):
    FinetuningJob.objects.select_for_update().get(pk=job.pk)
    for record in payload.get("checks", []):
        state = record["state"]
        if state not in TERMINAL | {"running", "queued"}:
            raise ValueError("Invalid training check state")
        artifact = record.get("artifact") or {}
        examples_data = artifact.get("examples")
        if examples_data is not None and fingerprint(examples_data) != artifact.get("sha256"):
            raise ValueError("Training check evidence checksum mismatch")
        digest = fingerprint(
            {
                **{k: v for k, v in record.items() if k not in {"observed_at", "artifact"}},
                "artifact": {k: v for k, v in artifact.items() if k != "examples"},
            }
        )
        check = job.validation_runs.filter(key=record["key"]).first()
        previous_state = check.state if check else None
        previous_delivery = (check.facts.get("delivery") or {}) if check else {}
        previous_assessment = check.facts.get("assessment") if check else None
        if check is not None and check.state in TERMINAL:
            interrupted_locally = check.state == "interrupted" and check.failure.get("code") in {
                "cancelled",
                "failed",
                "error",
            }
            if state not in TERMINAL:
                continue
            if not interrupted_locally and check.receipt_fingerprint != digest:
                raise ValueError("Completed training check changed")
            if not interrupted_locally and not (examples_data is not None and not check.evidence):
                assessment = training_quality.assessment(job, check)
                if assessment is not None and assessment != previous_assessment:
                    _record_check(job, check)
                    check.save(update_fields=["facts"])
                continue
        values = {
            "attempt": record["attempt"],
            "stream": record["stream"],
            "step": record["step"],
            "state": state,
            "policy_fingerprint": record["policy_fingerprint"],
            "sample_fingerprint": record["sample_fingerprint"],
            "receipt_fingerprint": digest,
            "started_at": _timestamp(record.get("started_at")),
            "observed_at": _timestamp(record.get("observed_at")),
            "completed_at": _timestamp(record.get("observed_at")) if state in TERMINAL else None,
            "metrics": record.get("metrics", {}),
            "coverage": record.get("coverage", {}),
            "failure": record.get("error", {}),
            "facts": {
                **{
                    key: value
                    for key, value in record.get("facts", {}).items()
                    if key != "assessment"
                },
                **({"assessment": previous_assessment} if previous_assessment else {}),
            },
            "evidence_sha256": artifact.get("sha256", ""),
        }
        check, _ = TrainingValidationRun.objects.update_or_create(
            job=job, key=record["key"], defaults=values
        )
        _record_check(job, check)
        training_experience.check_received(
            job, check, previous_state, previous_delivery, timing=payload
        )
        check.save(update_fields=["facts"])
        if examples_data is not None:
            check.evidence.save(
                f"{check.id}-{artifact['sha256']}.json",
                ContentFile(json.dumps(examples_data, ensure_ascii=False).encode()),
                save=False,
            )
            check.evidence_sha256 = artifact["sha256"]
            check.save(update_fields=["evidence", "evidence_sha256"])
    for record in payload.get("checkpoints", []):
        manifest = record["manifest"]
        if fingerprint(manifest) != record["identity"]:
            raise ValueError("Checkpoint manifest checksum mismatch")
        checkpoint, created = TrainingCheckpoint.objects.get_or_create(
            job=job,
            key=record["key"],
            defaults={
                "attempt": record["attempt"],
                "step": record["step"],
                "state": record["state"],
                "identity": record["identity"],
                "manifest": manifest,
                "verification": record.get("verification", {}),
                "metrics": record.get("metrics", {}),
                "selected": record.get("selected", False),
            },
        )
        if not created and checkpoint.identity != record["identity"]:
            raise ValueError("Retained checkpoint identity changed")
        training_experience.checkpoint_received(job, checkpoint, record, created=created)
        if not created:
            TrainingCheckpoint.objects.filter(pk=checkpoint.pk).update(
                state=record["state"],
                verification=record.get("verification", {}),
                selected=record.get("selected", False),
            )


@transaction.atomic
def interrupt(job, *, reason):
    FinetuningJob.objects.select_for_update().get(pk=job.pk)
    pending = list(job.validation_runs.exclude(state__in=TERMINAL))
    job.validation_runs.exclude(state__in=TERMINAL).update(
        state="interrupted",
        completed_at=timezone.now(),
        failure={"code": reason, "message": "Training ended before this check completed"},
    )
    for check in pending:
        previous_state = check.state
        check.refresh_from_db(fields=["state", "completed_at", "failure"])
        training_experience.check_received(
            job, check, previous_state, check.facts.get("delivery") or {}
        )
        check.save(update_fields=["facts"])


def collect(job, payload):
    if job.provider != "modal":
        return payload
    release = job_release(job)
    known = set(job.validation_runs.exclude(evidence="").values_list("key", flat=True))
    for check in payload.get("checks", []):
        artifact = check.get("artifact")
        if not artifact or check["key"] in known or check["state"] not in TERMINAL:
            continue
        reader = modal.Function.from_name(
            release["app"], "get_monitoring_examples", environment_name=release["environment"]
        )
        offset, rows = 0, []
        while offset is not None:
            page = reader.remote(job.remote_job_id.split(":", 1)[0], check["key"], offset, 50)
            if page["sha256"] != artifact["sha256"]:
                raise ValueError("Training evidence changed during collection")
            rows.extend(page["items"])
            following = page["next_offset"]
            if following is not None and following <= offset:
                raise ValueError("Training evidence cursor did not advance")
            offset = following
        artifact["examples"] = rows
    return payload


def observe(job, payload):
    ingest(job, payload)
    summary = {
        key: payload.get(key)
        for key in (
            "policy_fingerprint",
            "schedule",
            "schedule_decisions",
            "selected_checkpoint",
            "monitoring_seconds",
            "optimizer_seconds",
            "stop_reason",
            "numerical_health",
        )
    }
    try:
        collect(job, payload)
        ingest(job, payload)
        summary["collection"] = {"state": "observed", "observed_at": timezone.now().isoformat()}
    except Exception as exc:
        summary["collection"] = {
            "state": "unavailable",
            "observed_at": timezone.now().isoformat(),
            "error": str(exc),
            "impact": "Metrics retained; some evidence unavailable",
        }
    return summary


def snapshot(job, *, offset=0, limit=25):
    checks = job.validation_runs.order_by("step", "attempt", "created_at")
    count = checks.count()
    entries = list(checks[offset : offset + limit])
    return {
        "job_id": str(job.id),
        "policy": (job.hyperparameters or {}).get("monitoring"),
        "status": job.status,
        "count": count,
        "current": (job.progress or {}).get("monitoring") or {},
        "probes": ((job.progress or {}).get("monitoring_manifest") or {}).get("probes", {}),
        "next_offset": offset + limit if offset + limit < count else None,
        "checks": [
            {
                "id": str(check.id),
                "key": check.key,
                "state": check.state,
                "step": check.step,
                "attempt": check.attempt,
                "stream": check.stream,
                "policy_fingerprint": check.policy_fingerprint,
                "sample_fingerprint": check.sample_fingerprint,
                "started_at": _iso(check.started_at),
                "observed_at": _iso(check.observed_at),
                "completed_at": _iso(check.completed_at),
                "metrics": check.metrics,
                "coverage": check.coverage,
                "facts": check.facts,
                "error": check.failure,
                "evidence_available": bool(check.evidence),
                "evidence_sha256": check.evidence_sha256,
            }
            for check in entries
        ],
        "checkpoints": list(
            job.retained_checkpoints.order_by("step", "attempt").values(
                "id",
                "key",
                "attempt",
                "step",
                "state",
                "identity",
                "manifest",
                "verification",
                "metrics",
                "selected",
            )
        ),
        "limitations": [
            "Development checks inform progress, not final generalisation.",
            "No monitoring records does not establish that a model is improving.",
        ],
    }


def summary(job):
    check = job.validation_runs.order_by("-attempt", "-step", "-created_at").first()
    generated = (
        job.validation_runs.filter(metrics__generation__isnull=False)
        .exclude(metrics__generation=None)
        .order_by("-attempt", "-step", "-created_at")
        .first()
    )
    selected = job.retained_checkpoints.filter(selected=True).first()
    generation = (check.metrics.get("generation") or {}) if check else {}
    return {
        "policy": (job.hyperparameters or {}).get("monitoring"),
        "check_count": job.validation_runs.count(),
        "latest_generation_check": {
            "id": str(generated.pk),
            "state": generated.state,
            "step": generated.step,
            "attempt": generated.attempt,
            "stream": generated.stream,
            "observed_at": _iso(generated.observed_at),
            "sample_fingerprint": generated.facts.get("generation_sample_fingerprint"),
            "generation": {
                key: generated.metrics["generation"].get(key)
                for key in (
                    "scored",
                    "expected",
                    "coverage",
                    "accuracy",
                    "pass_rate",
                    "macro_f1",
                    "technical_errors",
                    "unscorable",
                )
            },
            "findings": generated.facts.get("findings", []),
            "assessment": generated.facts.get("assessment"),
        }
        if generated
        else None,
        "latest_check": {
            "id": str(check.pk),
            "state": check.state,
            "step": check.step,
            "stream": check.stream,
            "observed_at": _iso(check.observed_at),
            "loss": check.metrics.get("eval_loss"),
            "coverage": check.coverage,
            "generation": {
                key: generation.get(key)
                for key in (
                    "scored",
                    "expected",
                    "coverage",
                    "accuracy",
                    "pass_rate",
                    "macro_f1",
                    "technical_errors",
                )
            },
            "error": check.failure,
            "findings": check.facts.get("findings", []),
        }
        if check
        else None,
        "selected_checkpoint": {
            "id": str(selected.pk),
            "step": selected.step,
            "state": selected.state,
            "verification": selected.verification,
        }
        if selected
        else None,
        "current": (job.progress or {}).get("monitoring") or {},
        "inspect_tool": "inspect_training_progress",
        "reads_invoke_provider": False,
    }


def examples(job, check_id, *, offset=0, limit=25):
    check = job.validation_runs.get(id=check_id)
    if not check.evidence:
        return {"items": [], "count": 0, "next_offset": None, "available": False}
    with check.evidence.open("rb") as source:
        rows = json.load(source)
    if fingerprint(rows) != check.evidence_sha256:
        raise ValueError("Retained evidence checksum mismatch")
    return {
        "items": rows[offset : offset + limit],
        "count": len(rows),
        "available": True,
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "sha256": check.evidence_sha256,
    }
