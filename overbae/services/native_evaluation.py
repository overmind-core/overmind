import json
import logging
import math
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import modal
from django.db import transaction
from django.utils import timezone

from modal_shared.decision_inference import input_digest
from modal_shared.decision_metrics import score_decision
from modal_shared.decisions import decision_request
from modal_shared.serving.artifacts import atomic_json, digest_file
from modal_shared.training_release import evaluation_identity
from overbae.models import FinetuningJob, NativeEvaluationPlan
from overbae.services.datasets import paths, rows, use
from overbae.services.decision_benchmark_scoring import compare_suite, records
from overbae.services.training_release import current

logger = logging.getLogger(__name__)

TEMPERATURES = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]


def runtime():
    release = current()
    return {
        **evaluation_identity(Path(__file__).resolve().parents[2]),
        "environment": release["environment"],
    }


@transaction.atomic
def schedule(job, *, calibration_cell, final_cell):
    if (job.hyperparameters or {}).get("objective") != "decision_cross_entropy":
        raise ValueError("Native evaluation requires a decision training job.")
    if calibration_cell.id == final_cell.id:
        raise ValueError("Calibration and final suites must be separate frozen cells.")
    for cell in (calibration_cell, final_cell):
        if cell.dataset.project_id != job.project_id:
            raise ValueError("Evaluation suites must belong to the training project.")
        use.check(cell.dataset, "eval", cell=cell)
        rows.verify(cell)
        evaluation = (cell.intent_report or {}).get("eval", {})
        input_types = evaluation.get("input_types", {})
        if evaluation.get("input_type") != "decision" or any(
            name != "decision" and count for name, count in input_types.items()
        ):
            raise ValueError(
                "Evaluation suites must contain native decision inputs and separate references."
            )
    if rows.contamination(calibration_cell, final_cell)["overlap_count"]:
        raise ValueError("Calibration and final suites overlap on input or grouping identity.")
    FinetuningJob.objects.select_for_update().get(pk=job.pk)
    existing = NativeEvaluationPlan.objects.filter(job=job).first()
    config = {
        "runtime": existing.config["runtime"] if existing else runtime(),
        "suites": {
            role: {
                "cell": str(cell.id),
                "dataset": str(cell.dataset_id),
                "fingerprint": cell.fingerprint,
                "rows": cell.rows,
            }
            for role, cell in (("calibration", calibration_cell), ("final", final_cell))
        },
        "baseline": "unchanged_training_base",
        "calibration_method": "scalar_temperature_cross_entropy",
        "temperatures": TEMPERATURES,
        "bootstrap_samples": 1000,
        "seed": 73491,
        "boundaries": existing.config["boundaries"]
        if existing
        else {
            role: rows.contamination(job.cell, cell)
            if job.cell_id
            else {"status": "unknown", "reason": "No pinned training cell"}
            for role, cell in (("calibration", calibration_cell), ("final", final_cell))
        },
        "contamination_limits": "Exact input and declared grouping matches only; paraphrase and pretraining contamination remain unknown. Findings do not change suites or labels.",
    }
    if existing:
        if existing.config != config:
            raise ValueError("This job already has a different frozen native evaluation plan.")
        return existing
    for cell in (calibration_cell, final_cell):
        use.use(cell.dataset, "eval", cell=cell)
    return NativeEvaluationPlan.objects.create(
        job=job, calibration_cell=calibration_cell, final_cell=final_cell, config=config
    )


def directory(plan, role):
    return paths.media_root() / "native-evaluations" / str(plan.pk) / role


def seal_suite(plan, role):
    cell = getattr(plan, role + "_cell")
    rows.verify(cell)
    if cell.fingerprint != plan.config["suites"][role]["fingerprint"]:
        raise ValueError("Frozen native suite changed.")
    target = directory(plan, role)
    if target.exists():
        manifest = json.loads((target / "manifest.json").read_text())
        if any(
            digest_file(target / name) != checksum for name, checksum in manifest["files"].items()
        ):
            raise ValueError("Sealed native evaluation files changed.")
        return target / "inputs.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
        staging = Path(temporary) / "suite"
        staging.mkdir()
        count = 0
        with (
            (staging / "inputs.jsonl").open("w") as inputs,
            (staging / "references.jsonl").open("w") as references,
            (staging / "failures.jsonl").open("w") as failures,
        ):
            for row in rows.iter_rows(cell):
                request = row.input.get("decision") if isinstance(row.input, dict) else None
                benchmark = str(
                    row.extra.get("benchmark") or row.extra.get("source") or cell.dataset.name
                )
                try:
                    request = decision_request(request)
                    n = len(request["options"])
                    score_decision(
                        request,
                        row.expected_output,
                        {"probabilities": [1 / n] * n, "log_probabilities": [-math.log(n)] * n},
                    )
                    identity = {"key": str(row.index), "input_sha256": input_digest(request)}
                except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
                    failures.write(
                        json.dumps(
                            {
                                "benchmark": benchmark,
                                "decisions": 1,
                                "source_row": row.index,
                                "error": str(exc),
                            }
                        )
                        + "\n"
                    )
                    continue
                inputs.write(json.dumps({**identity, "decision": request}) + "\n")
                references.write(
                    json.dumps(
                        {
                            **identity,
                            "benchmark": benchmark,
                            "group": str(
                                row.extra.get("group")
                                or row.extra.get("case_id")
                                or identity["input_sha256"]
                            ),
                            "kind": request["kind"],
                            "query": str(
                                row.extra.get("query") or request.get("question") or "decision"
                            ),
                            "reference": row.expected_output,
                            "metadata": row.extra.get("metadata") or {},
                        }
                    )
                    + "\n"
                )
                count += 1
        manifest = {
            "decisions": count,
            "source_rows": cell.rows,
            "cell": str(cell.id),
            "fingerprint": cell.fingerprint,
            "files": {
                name: digest_file(staging / name)
                for name in ("inputs.jsonl", "references.jsonl", "failures.jsonl")
            },
        }
        atomic_json(staging / "manifest.json", manifest)
        staging.rename(target)
    return target / "inputs.jsonl"


def stage_sequence(plan):
    return [
        "calibration_prepare",
        "calibration_base",
        "calibration_candidate",
        "fit_calibration",
        "final_prepare",
        "final_base",
        "final_candidate",
        "score",
    ]


def calibrated(prediction, temperature):
    logits = [value / temperature for value in prediction["log_probabilities"]]
    maximum = max(logits)
    normalizer = maximum + math.log(math.fsum(math.exp(value - maximum) for value in logits))
    logp = [value - normalizer for value in logits]
    return {
        **prediction,
        "probabilities": [math.exp(value) for value in logp],
        "log_probabilities": logp,
    }


def fit_calibration(plan):
    plan.refresh_from_db()
    if plan.calibration:
        return plan.calibration
    source = directory(plan, "calibration")
    comparison_path = source / "comparison/summary.json"
    comparison = (
        json.loads(comparison_path.read_text())
        if comparison_path.exists()
        else compare_suite(
            source,
            source / "base.jsonl",
            source / "candidate.jsonl",
            source / "comparison",
            bootstrap_samples=plan.config["bootstrap_samples"],
            seed=plan.config["seed"],
        )
    )
    for arm, label in (("base", "baseline"), ("candidate", "candidate")):
        if comparison[label]["predictions_sha256"] != digest_file(source / f"{arm}.jsonl"):
            raise ValueError("Calibration prediction identity changed.")
        if any(
            card["missing_predictions"] or card["invalid_predictions"]
            for card in comparison[label]["benchmarks"].values()
        ):
            raise ValueError("Calibration coverage is incomplete or invalid.")
    result = {
        "fitted_on": "calibration",
        "method": plan.config["calibration_method"],
        "grid": plan.config["temperatures"],
        "source_manifest": digest_file(source / "manifest.json"),
        "frozen_at": timezone.now().isoformat(),
        "arms": {},
    }
    with tempfile.TemporaryDirectory() as temporary:
        db = sqlite3.connect(Path(temporary) / "references.sqlite")
        db.execute("CREATE TABLE refs(key TEXT PRIMARY KEY, hash TEXT, q TEXT)")
        for ref in records(source / "references.jsonl"):
            if "probabilities" in ref["reference"]:
                db.execute(
                    "INSERT INTO refs VALUES(?,?,?)",
                    (
                        ref["key"],
                        ref["input_sha256"],
                        json.dumps(ref["reference"]["probabilities"]),
                    ),
                )
        for arm in ("base", "candidate"):
            totals = [0.0] * len(result["grid"])
            count = 0
            for prediction in records(source / f"{arm}.jsonl"):
                found = db.execute(
                    "SELECT q FROM refs WHERE key=? AND hash=?",
                    (prediction["key"], prediction["input_sha256"]),
                ).fetchone()
                if not found:
                    continue
                q = json.loads(found[0])
                for i, temperature in enumerate(result["grid"]):
                    adjusted = calibrated(prediction, temperature)
                    totals[i] -= math.fsum(
                        a * b for a, b in zip(q, adjusted["log_probabilities"], strict=True)
                    )
                count += 1
            if not count:
                raise ValueError("Calibration has no valid probability-labelled decisions.")
            best = min(range(len(totals)), key=lambda i: (totals[i], abs(result["grid"][i] - 1)))
            result["arms"][arm] = {
                "temperature": result["grid"][best],
                "decisions": count,
                "cross_entropy": totals[best] / count,
                "prediction_sha256": digest_file(source / f"{arm}.jsonl"),
            }
        db.close()
    result["decisions"] = min(arm["decisions"] for arm in result["arms"].values())
    NativeEvaluationPlan.objects.filter(pk=plan.pk, calibration={}).update(calibration=result)
    plan.refresh_from_db()
    return plan.calibration


def submit(plan, stage):
    role, action = stage.split("_", 1)
    selected = plan.config["runtime"]
    evaluation_id = f"{plan.id}-{role}"
    model_run = plan.job.remote_job_id.split(":", 1)[0]
    if action == "prepare":
        source = seal_suite(plan, role)
        volume = modal.Volume.from_name("overmind-sft", environment_name=selected["environment"])
        with volume.batch_upload() as upload:
            upload.put_file(source, f"/decision-evaluations/{evaluation_id}/inputs.jsonl")
        function = modal.Function.from_name(
            selected["app"], "prepare", environment_name=selected["environment"]
        )
        return function.spawn(evaluation_id, model_run, digest_file(source)).object_id
    function = modal.Function.from_name(
        selected["app"], "predict", environment_name=selected["environment"]
    )
    return function.spawn(evaluation_id, model_run, base_only=action == "base").object_id


def collect(plan, stage, receipt):
    role, arm = stage.split("_", 1)
    filename = "failures.jsonl" if arm == "prepare" else f"{arm}.jsonl"
    checksum = receipt["failures_sha256" if arm == "prepare" else "predictions_sha256"]
    target = directory(plan, role) / (
        "preparation-failures.jsonl" if arm == "prepare" else filename
    )
    volume = modal.Volume.from_name(
        "overmind-sft", environment_name=plan.config["runtime"]["environment"]
    )
    partial = target.with_suffix(".partial")
    with partial.open("wb") as output:
        for chunk in volume.read_file(f"/decision-evaluations/{plan.id}-{role}/{filename}"):
            output.write(chunk)
    if digest_file(partial) != checksum:
        raise ValueError("Native prediction artifact checksum differs.")
    partial.replace(target)


def score(plan):
    source = directory(plan, "final")
    result = {}
    for calibrated_output in (False, True):
        destination = source / ("calibrated" if calibrated_output else "raw")
        if (destination / "summary.json").exists():
            result[destination.name] = json.loads((destination / "summary.json").read_text())
            continue
        predictions = {}
        for arm in ("base", "candidate"):
            path = source / f"{arm}.jsonl"
            if calibrated_output:
                adjusted = source / f"{arm}-calibrated.jsonl"
                with adjusted.open("w") as output:
                    for prediction in records(path):
                        output.write(
                            json.dumps(
                                calibrated(prediction, plan.calibration["arms"][arm]["temperature"])
                            )
                            + "\n"
                        )
                path = adjusted
            predictions[arm] = path
        result[destination.name] = compare_suite(
            source,
            predictions["base"],
            predictions["candidate"],
            destination,
            bootstrap_samples=plan.config["bootstrap_samples"],
            seed=plan.config["seed"],
        )
    return result


def advance(plan_id):
    with transaction.atomic():
        plan = (
            NativeEvaluationPlan.objects.select_for_update()
            .select_related("job", "calibration_cell__dataset", "final_cell__dataset")
            .get(pk=plan_id)
        )
        if plan.state in {"completed", "failed", "submission_unknown"}:
            return
        if plan.job.status in {"failed", "cancelled"}:
            plan.state, plan.error = "failed", "Training did not produce a verified checkpoint."
            plan.save()
            return
        if plan.job.status != "succeeded":
            return
        stages = stage_sequence(plan)
        stage = next((s for s in stages if plan.calls.get(s, {}).get("state") != "completed"), None)
        if stage is None:
            return
        call = plan.calls.get(stage, {})
        if call.get("state") == "submitting":
            if (timezone.now() - plan.updated_at).total_seconds() < 1260:
                return
            if stage in {"fit_calibration", "score"}:
                call = {}
            else:
                plan.state, plan.error = (
                    "submission_unknown",
                    "Provider acknowledgement is unresolved. Reconcile its call ID before continuing.",
                )
                plan.save()
                return
        starting = not call
        if call.get("state") == "running":
            observed_at = call.get("observe_started_at")
            if (
                observed_at
                and (timezone.now() - datetime.fromisoformat(observed_at)).total_seconds() < 1260
            ):
                return
            call = {**call, "observe_started_at": timezone.now().isoformat()}
            plan.calls = {**plan.calls, stage: call}
            plan.save()
        if starting:
            plan.calls = {
                **plan.calls,
                stage: {"state": "submitting", "intent_at": timezone.now().isoformat()},
            }
            plan.state = stage
            plan.save()
    try:
        if stage == "fit_calibration":
            # Validate coverage and prediction integrity before fitting either arm.
            source = directory(plan, "calibration")
            if not (source / "comparison/summary.json").exists():
                compare_suite(
                    source,
                    source / "base.jsonl",
                    source / "candidate.jsonl",
                    source / "comparison",
                    bootstrap_samples=plan.config["bootstrap_samples"],
                    seed=plan.config["seed"],
                )
            receipt = fit_calibration(plan)
        elif stage == "score":
            receipt = score(plan)
        elif starting:
            call_id = submit(plan, stage)
            with transaction.atomic():
                locked = NativeEvaluationPlan.objects.select_for_update().get(pk=plan.pk)
                locked.calls = {
                    **locked.calls,
                    stage: {**locked.calls[stage], "state": "running", "id": call_id},
                }
                locked.save()
            return
        else:
            receipt = modal.FunctionCall.from_id(call["id"]).get(timeout=0)
            collect(plan, stage, receipt)
        with transaction.atomic():
            locked = NativeEvaluationPlan.objects.select_for_update().get(pk=plan.pk)
            locked.calls = {
                **locked.calls,
                stage: {**locked.calls[stage], "state": "completed", "receipt": receipt},
            }
            if stage == "score":
                locked.state, locked.results = "completed", receipt
            locked.save()
    except TimeoutError:
        if starting:
            NativeEvaluationPlan.objects.filter(pk=plan.pk).update(
                state="submission_unknown",
                error="Submission response timed out; reconcile the saved intent.",
            )
        else:
            with transaction.atomic():
                locked = NativeEvaluationPlan.objects.select_for_update().get(pk=plan.pk)
                pending = dict(locked.calls[stage])
                pending.pop("observe_started_at", None)
                locked.calls = {**locked.calls, stage: pending}
                locked.save()
    except Exception:
        logger.exception("Native evaluation stage failed: %s %s", plan_id, stage)
        NativeEvaluationPlan.objects.filter(pk=plan.pk).update(
            state="submission_unknown"
            if starting and stage not in {"score", "fit_calibration"}
            else "failed",
            error="Native evaluation could not verify this stage. Inspect its saved receipt and diagnostics.",
        )


def describe(plan):
    return {
        "id": str(plan.id),
        "job": str(plan.job_id),
        "state": plan.state,
        "config": plan.config,
        "calls": plan.calls,
        "calibration": plan.calibration,
        "results": plan.results,
        "error": plan.error,
        "created_at": plan.created_at.isoformat(),
        "updated_at": plan.updated_at.isoformat(),
    }
