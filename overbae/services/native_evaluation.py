import hashlib
import json
import logging
import math
import shutil
import sqlite3
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

import modal
from django.db import transaction
from django.utils import timezone

from modal_shared.decision_inference import input_digest
from modal_shared.decision_metrics import score_decision
from modal_shared.decisions import DECISION_OBJECTIVES, decision_request
from modal_shared.serving.artifacts import atomic_json, digest_file
from modal_shared.training_release import evaluation_identity
from overbae.core.errors import InputValidationError
from overbae.models import (
    Dataset,
    DecisionProviderRequest,
    FinetuningJob,
    NativeEvaluationPlan,
    Project,
)
from overbae.services.compute_costs import estimate_usage
from overbae.services.datasets import paths, rows, use
from overbae.services.decision_benchmark_scoring import compare_suite, records
from overbae.services.decision_providers import (
    SubmissionUnknownError,
    external_step,
    qualify_participants,
)
from overbae.services.evaluation_inputs import artifact_lock
from overbae.services.training_release import current

logger = logging.getLogger(__name__)

TEMPERATURES = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]


def runtime():
    release = current()
    return {
        **evaluation_identity(Path(__file__).resolve().parents[2]),
        "environment": release["environment"],
        "training": release["training"],
    }


@transaction.atomic
def create_plan(
    project,
    *,
    name,
    request_key,
    final_cell,
    participants,
    baseline,
    calibration_cell=None,
    bootstrap_samples=1000,
    seed=73491,
    inference=None,
    job=None,
    triggered_by=None,
):
    if calibration_cell is not None and calibration_cell.id == final_cell.id:
        raise InputValidationError("Calibration and final suites must be separate frozen cells.")
    suites = {"final": final_cell}
    if calibration_cell is not None:
        suites = {"calibration": calibration_cell, **suites}
    for cell in suites.values():
        if cell.dataset.project_id != project.pk:
            raise InputValidationError("Evaluation suites must belong to this project.")
        if cell.dataset.intent != "eval" or not cell.fits("eval")[0]:
            raise InputValidationError("Select a technically readable evaluation version")
        evaluation = (cell.intent_report or {}).get("eval", {})
        if evaluation.get("input_type") != "decision" or any(
            k != "decision" and v for k, v in evaluation.get("input_types", {}).items()
        ):
            raise InputValidationError(
                "Evaluation suites must contain native decision inputs and separate references."
            )
    if (
        type(bootstrap_samples) is not int
        or not 2 <= bootstrap_samples <= 10000
        or type(seed) is not int
        or not 0 <= seed <= 2**32 - 1
    ):
        raise InputValidationError("Supply 2–10000 bootstrap samples and a nonnegative 32-bit seed")
    inference = {
        "context_length": 8192,
        "batch_size": 64,
        "padded_tokens": 32768,
        "concurrency": 8,
        **(inference or {}),
    }
    bounds = {
        "context_length": (128, 131072),
        "batch_size": (1, 256),
        "padded_tokens": (128, 262144),
        "concurrency": (1, 16),
    }
    if set(inference) != set(bounds) or any(
        type(inference[k]) is not int or not a <= inference[k] <= b for k, (a, b) in bounds.items()
    ):
        raise InputValidationError("Invalid native inference settings")
    Project.objects.select_for_update().get(pk=project.pk)
    existing = NativeEvaluationPlan.objects.filter(project=project, request_key=request_key).first()
    selected = qualify_participants(project, participants)
    if baseline not in {p["key"] for p in selected}:
        raise InputValidationError("Choose one of the participants as the baseline")
    config = {
        "runtime": existing.config["runtime"]
        if existing
        else runtime()
        if any(p["kind"] != "external" for p in selected)
        else {"adapter": "systemone", **evaluation_identity(Path(__file__).resolve().parents[2])},
        "suites": {
            role: {
                "cell": str(cell.pk),
                "dataset": str(cell.dataset_id),
                "fingerprint": cell.fingerprint,
                "rows": cell.rows,
            }
            for role, cell in suites.items()
        },
        "suite_overlap": existing.config.get("suite_overlap") if existing else None,
        "participants": selected,
        "baseline": baseline,
        "inference": inference,
        "calibration_method": "scalar_temperature_cross_entropy" if calibration_cell else None,
        "temperatures": TEMPERATURES if calibration_cell else [],
        "bootstrap_samples": bootstrap_samples,
        "seed": seed,
        "logarithm_floor": 1e-12,
        "boundaries": existing.config.get("boundaries", {}) if existing else {},
        "contamination_limits": "Exact input and declared grouping matches only; paraphrase and pretraining contamination remain unknown. Findings do not change suites or labels.",
    }
    if existing:
        if existing.config.get("authorization"):
            config["authorization"] = existing.config["authorization"]
        if existing.config.get("reuse"):
            config["reuse"] = existing.config["reuse"]
        if existing.config != config or existing.name != name:
            raise InputValidationError(
                "This request key already identifies a different frozen comparison"
            )
        return existing
    for cell in sorted(suites.values(), key=lambda cell: str(cell.dataset_id)):
        Dataset.objects.select_for_update().get(pk=cell.dataset_id)
        cell.refresh_from_db()
        if (
            cell.fingerprint
            != config["suites"][
                next(role for role, candidate in suites.items() if candidate.pk == cell.pk)
            ]["fingerprint"]
        ):
            raise InputValidationError("The selected source changed during planning")
        use.freeze(cell)
    return NativeEvaluationPlan.objects.create(
        project=project,
        name=name,
        request_key=request_key,
        job=job,
        triggered_by=triggered_by,
        calibration_cell=calibration_cell,
        final_cell=final_cell,
        config=config,
        state="draft",
    )


def schedule(job, *, calibration_cell, final_cell):
    if (job.hyperparameters or {}).get("objective") not in DECISION_OBJECTIVES:
        raise InputValidationError("Native evaluation requires a decision training job.")
    plan = create_plan(
        job.project,
        name=job.name or "Decision comparison",
        request_key=f"training:{job.pk}",
        job=job,
        triggered_by=job.triggered_by,
        calibration_cell=calibration_cell,
        final_cell=final_cell,
        participants=[
            {"key": "base", "name": "Unchanged base", "kind": "foundation", "job": str(job.pk)},
            {"key": "candidate", "name": "Trained model", "kind": "trained", "job": str(job.pk)},
        ],
        baseline="base",
    )
    return launch(plan)


@transaction.atomic
def launch(plan, *, user=None):
    # Tasks import this service; dispatch only after the draft transition commits.
    from overbae.tasks.native_evaluation import advance_plan

    plan = NativeEvaluationPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.state in {"draft", "prepared"}:
        actor = user or plan.triggered_by
        plan.config = {
            **plan.config,
            "authorization": {
                "user": str(actor.pk) if actor else None,
                "authorized_at": timezone.now().isoformat(),
                "scope": "saved_participants_and_suites",
                "configuration_sha256": hashlib.sha256(
                    json.dumps(
                        {k: v for k, v in plan.config.items() if k != "authorization"},
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
            },
        }
        plan.state = (
            "waiting_for_checkpoint" if any(p.get("job") for p in participants(plan)) else "queued"
        )
        plan.save()
        transaction.on_commit(lambda: advance_plan.delay(str(plan.pk)), robust=True)
    return plan


def participants(plan):
    return plan.config["participants"]


def preparation_groups(plan):
    groups = {}
    for participant in participants(plan):
        if participant["kind"] == "external" or participant["key"] in plan.config.get("reuse", {}):
            continue
        identity = participant.get("job") or participant["key"]
        if identity not in groups:
            key = "prepare" if not groups else "prepare_" + participant["key"]
            groups[identity] = (key, participant)
    return groups


def provider_stage(plan, stage):
    role, key = stage.split("_", 1)
    groups = preparation_groups(plan)
    for action, participant in groups.values():
        if key == action:
            return role, participant, True, action
    participant = next(p for p in participants(plan) if p["key"] == key)
    group = groups.get(participant.get("job") or key)
    return role, participant, False, group[0] if group else None


def directory(plan, role):
    return paths.media_root() / "native-evaluations" / str(plan.pk) / role


def seal_suite(plan, role):
    with artifact_lock(directory(plan, role)):
        return write_suite(plan, role)


def write_suite(plan, role):
    cell = getattr(plan, role + "_cell")
    rows.verify(cell)
    if cell.fingerprint != plan.config["suites"][role]["fingerprint"]:
        raise InputValidationError("Frozen native suite changed.")
    target = directory(plan, role)
    if target.exists():
        manifest = json.loads((target / "manifest.json").read_text())
        if any(
            digest_file(target / name) != checksum for name, checksum in manifest["files"].items()
        ):
            raise InputValidationError("Sealed native evaluation files changed.")
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
    stages = ["verify_inputs"]
    for role in ("calibration", "final"):
        if role not in plan.config["suites"]:
            continue
        stages.extend(f"{role}_{key}" for key, _ in preparation_groups(plan).values())
        stages.extend(f"{role}_{p['key']}" for p in participants(plan))
        if role == "calibration":
            stages.append("fit_calibration")
    return [*stages, "score"]


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


def comparison_for_calibration(plan, arm):
    source = directory(plan, "calibration")
    destination = source / "coverage" / arm
    if (destination / "summary.json").exists():
        result = json.loads((destination / "summary.json").read_text())
        for label, key in (("candidate", arm), ("baseline", plan.config["baseline"])):
            if result[label]["predictions_sha256"] != digest_file(
                source / f"{key}.jsonl"
            ) or result[label]["suite_manifest_sha256"] != digest_file(source / "manifest.json"):
                raise InputValidationError("Calibration inputs or predictions changed")
        return result
    baseline = plan.config["baseline"]
    return compare_suite(
        source,
        source / f"{baseline}.jsonl",
        source / f"{arm}.jsonl",
        destination,
        bootstrap_samples=2,
        seed=plan.config["seed"],
    )


def fit_calibration(plan):
    plan.refresh_from_db()
    if plan.calibration:
        return plan.calibration
    source = directory(plan, "calibration")
    for participant in participants(plan):
        arm = participant["key"]
        comparison = comparison_for_calibration(plan, arm)
        if any(
            card["missing_predictions"] or card["invalid_predictions"]
            for card in comparison["candidate"]["benchmarks"].values()
        ):
            raise InputValidationError("Calibration coverage is incomplete or invalid.")
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
        for arm in (p["key"] for p in participants(plan)):
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
                raise InputValidationError(
                    "Calibration has no valid probability-labelled decisions."
                )
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
    role, participant, preparing, group = provider_stage(plan, stage)
    selected = plan.config["runtime"]
    evaluation_id = f"{plan.id}-{role}" + ("" if group == "prepare" else "-" + group)
    model = None
    if participant.get("job"):
        model = FinetuningJob.objects.get(pk=participant["job"], project_id=plan.project_id)
        model_run = model.remote_job_id.split(":", 1)[0]
    else:
        model_run = None
    if preparing:
        source = seal_suite(plan, role)
        volume = modal.Volume.from_name("overmind-sft", environment_name=selected["environment"])
        with volume.batch_upload() as upload:
            upload.put_file(source, f"/decision-evaluations/{evaluation_id}/inputs.jsonl")
        function = modal.Function.from_name(
            selected["app"], "prepare", environment_name=selected["environment"]
        )
        return function.spawn(
            evaluation_id,
            model_run,
            digest_file(source),
            foundation=None if model else participant,
            inference=plan.config["inference"],
        ).object_id
    function = modal.Function.from_name(
        selected["app"], "predict", environment_name=selected["environment"]
    )
    return function.spawn(
        evaluation_id,
        model_run,
        base_only=participant["kind"] == "foundation",
        inference=plan.config["inference"],
    ).object_id


def collect(plan, stage, receipt):
    role, participant, preparing, group = provider_stage(plan, stage)
    native_label = "base" if participant["kind"] == "foundation" else "candidate"
    filename = "failures.jsonl" if preparing else f"{native_label}.jsonl"
    checksum = receipt["failures_sha256" if preparing else "predictions_sha256"]
    evaluation_id = f"{plan.id}-{role}" + ("" if group == "prepare" else "-" + group)
    target = directory(plan, role) / (
        f"{group}-failures.jsonl" if preparing else f"{participant['key']}-provider.jsonl"
    )
    volume = modal.Volume.from_name(
        "overmind-sft", environment_name=plan.config["runtime"]["environment"]
    )
    partial = target.with_suffix(".partial")
    with partial.open("wb") as output:
        for chunk in volume.read_file(f"/decision-evaluations/{evaluation_id}/{filename}"):
            output.write(chunk)
    if digest_file(partial) != checksum:
        raise InputValidationError("Native prediction artifact checksum differs.")
    partial.replace(target)
    if preparing:
        for arm in participants(plan):
            if (
                arm["kind"] != "external"
                and preparation_groups(plan)[arm.get("job") or arm["key"]][0] == group
            ):
                (directory(plan, role) / f"{arm['key']}.failures.jsonl").write_bytes(
                    target.read_bytes()
                )
    else:
        expected_runtime = plan.config["runtime"].get("training")
        if expected_runtime and receipt.get("runtime_fingerprint") != expected_runtime:
            raise InputValidationError(
                "Prediction runtime differs from the pinned evaluation release"
            )
        prepared = plan.calls[f"{role}_{group}"]["receipt"]
        if receipt.get("artifact_identity") != prepared.get("artifact_identity") or receipt.get(
            "input_sha256"
        ) != prepared.get("input_sha256"):
            raise InputValidationError("Prepared and predicted native identities differ")
        with (directory(plan, role) / f"{participant['key']}.jsonl").open("w") as output:
            for prediction in records(target):
                output.write(
                    json.dumps(
                        {
                            **prediction,
                            "log_probabilities": [
                                math.log(max(p, plan.config["logarithm_floor"]))
                                for p in prediction["probabilities"]
                            ]
                            if plan.config["logarithm_floor"] is not None
                            else prediction["log_probabilities"],
                        }
                    )
                    + "\n"
                )


def comparison(plan, role, arm, calibrated_output):
    source = directory(plan, role)
    mode = "calibrated" if calibrated_output else "raw"
    baseline = plan.config["baseline"]
    destination = source / "comparisons" / arm / mode
    prediction_paths = {}
    for key in {baseline, arm}:
        path = source / f"{key}.jsonl"
        if calibrated_output:
            adjusted = source / f"{key}-calibrated.jsonl"
            with adjusted.open("w") as output:
                for prediction in records(path):
                    output.write(
                        json.dumps(
                            calibrated(prediction, plan.calibration["arms"][key]["temperature"])
                        )
                        + "\n"
                    )
            failures = path.with_suffix(".failures.jsonl")
            if failures.exists():
                adjusted.with_suffix(".failures.jsonl").write_bytes(failures.read_bytes())
            path = adjusted
        prediction_paths[key] = path
    if (destination / "summary.json").exists():
        result = json.loads((destination / "summary.json").read_text())
        for label, key in (("baseline", baseline), ("candidate", arm)):
            if result[label]["predictions_sha256"] != digest_file(prediction_paths[key]) or result[
                label
            ]["suite_manifest_sha256"] != digest_file(source / "manifest.json"):
                raise InputValidationError("Saved scores no longer match their frozen inputs")
        return result
    return compare_suite(
        source,
        prediction_paths[baseline],
        prediction_paths[arm],
        destination,
        bootstrap_samples=plan.config["bootstrap_samples"],
        seed=plan.config["seed"],
    )


def cost_record(plan):
    result = {}
    for participant in participants(plan):
        job = (
            FinetuningJob.objects.filter(
                pk=participant.get("job"), project_id=plan.project_id
            ).first()
            if participant.get("job")
            else None
        )
        usages = [
            attempt.get("usage", {})
            for row in plan.requests.filter(participant=participant["key"])
            for attempt in (row.attempts or [{"usage": row.usage}])
        ]
        compute = []
        for stage, call in plan.calls.items():
            if stage in {"verify_inputs", "fit_calibration", "score"}:
                continue
            if provider_stage(plan, stage)[1]["key"] == participant["key"]:
                compute.append((call.get("receipt") or {}).get("compute_usage", {}))
        result[participant["key"]] = {
            "recorded_training_usd": float(job.cost_usd)
            if job and job.cost_usd is not None
            else None,
            "training_coverage": "training_gpu"
            if job and job.provider == "modal"
            else "provider_training"
            if job
            else "not_applicable",
            "recorded_evaluation_api_usd": sum(usage.get("response_cost") or 0 for usage in usages),
            "unknown_api_attempts": sum(usage.get("response_cost") is None for usage in usages),
            "native_compute": estimate_usage(compute)
            if participant["kind"] != "external"
            else None,
            "unreported_components": [
                "storage",
                "network",
                "local_orchestration",
                "provider_billing_adjustments",
            ],
            "all_in_invoice_usd": None,
        }
    return result


def score(plan):
    result = {
        "participants": participants(plan),
        "baseline": plan.config["baseline"],
        "comparisons": {},
        "calibration": None,
        "calls": {key: value for key, value in plan.calls.items() if key != "score"},
        "logarithm_floor": plan.config["logarithm_floor"],
        "limitations": plan.config["contamination_limits"],
        "costs": cost_record(plan),
    }
    modes = [False, True] if plan.calibration_cell_id else [False]
    for participant in participants(plan):
        arm = participant["key"]
        result["comparisons"][arm] = {
            ("calibrated" if mode else "raw"): comparison(plan, "final", arm, mode)
            for mode in modes
        }
    if plan.calibration_cell_id:
        result["calibration"] = {
            "in_sample": True,
            "fit": plan.calibration,
            "comparisons": {
                p["key"]: {
                    ("calibrated" if mode else "raw"): comparison(
                        plan, "calibration", p["key"], mode
                    )
                    for mode in modes
                }
                for p in participants(plan)
            },
        }
    if "candidate" in result["comparisons"]:
        result.update(result["comparisons"]["candidate"])
    directory(plan, "report").mkdir(parents=True, exist_ok=True)
    atomic_json(directory(plan, "report") / "results.json", result)
    report_lines = [
        f"# {plan.name}",
        "",
        f"Baseline: {plan.config['baseline']}",
        "",
        plan.config["contamination_limits"],
        "",
        "Costs are recorded provider usage, not an all-in invoice.",
        "",
    ]
    for participant, modes in result["comparisons"].items():
        for mode, summary in modes.items():
            report_lines += [
                f"## {participant} · {mode}",
                "",
                "| Benchmark | Metric | Baseline | Participant | Difference | 95% paired interval |",
                "| --- | --- | ---: | ---: | ---: | --- |",
            ]
            for benchmark, card in summary["benchmarks"].items():
                for metric, values in card["metrics"].items():
                    report_lines.append(
                        f"| {benchmark} | {metric} | {summary['baseline']['benchmarks'][benchmark]['metrics'][metric]['mean']} | {summary['candidate']['benchmarks'][benchmark]['metrics'][metric]['mean']} | {values.get('candidate_minus_baseline')} | {values['interval_95']} |"
                    )
            report_lines += [
                "",
                "Full coverage, metric denominators, failures and diagnostic slices are retained in results.json.",
                "",
            ]
    if plan.calibration_cell_id:
        report_lines += [
            "Calibration results in results.json are in-sample; fitted temperatures were frozen before final scoring.",
            "",
        ]
    (directory(plan, "report") / "results.md").write_text("\n".join(report_lines))
    return result


@transaction.atomic
def resume(plan, *, stage=None, call_id=None):
    plan = NativeEvaluationPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.state == "paused":
        plan.state, plan.error = "running", ""
        plan.save()
        return plan
    if plan.state not in {"failed", "submission_unknown"}:
        raise InputValidationError("Only a stopped evaluation can be resumed")
    pending = next(
        (s for s in stage_sequence(plan) if plan.calls.get(s, {}).get("state") != "completed"), None
    )
    if pending is None or (stage is not None and stage != pending):
        raise InputValidationError("Select the unfinished evaluation stage")
    call = dict(plan.calls.get(pending, {}))
    local = pending in {"verify_inputs", "fit_calibration", "score"}
    external = not local and provider_stage(plan, pending)[1]["kind"] == "external"
    if call_id:
        if (
            local
            or external
            or not call_id.startswith("fc-")
            or (call.get("id") and call["id"] != call_id)
        ):
            raise InputValidationError("Supply the existing native provider call ID")
        call.update(id=call_id, state="running")
    elif not local and not external and not call.get("id"):
        raise InputValidationError(
            "An unresolved native submission requires its existing provider call ID"
        )
    if external:
        role, participant, _, _ = provider_stage(plan, pending)
        requests = DecisionProviderRequest.objects.filter(
            plan=plan, participant=participant["key"], role=role
        )
        if requests.filter(state__in=["submitting", "submission_unknown"]).exists():
            raise InputValidationError(
                "Provider acknowledgement is unresolved; do not replay this request"
            )
        if requests.filter(state="failed", response__isnull=True).exists():
            raise InputValidationError(
                "Provider requests exhausted the saved technical retry policy"
            )
        requests.filter(state="failed", response__isnull=False).update(state="received", error="")
    call.pop("observe_started_at", None)
    call.pop("lease", None)
    call.pop("lease_started_at", None)
    if local or external:
        plan.calls = {k: v for k, v in plan.calls.items() if k != pending}
    else:
        call["state"] = "running"
        plan.calls = {**plan.calls, pending: call}
    plan.state, plan.error = pending, ""
    plan.save()
    return plan


@transaction.atomic
def prepare(plan):
    # The task imports this service and runs file checks outside request transactions.
    from overbae.tasks.native_evaluation import advance_plan

    plan = NativeEvaluationPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.state == "draft":
        plan.state = "preparing"
        plan.save()
        transaction.on_commit(lambda: advance_plan.delay(str(plan.pk)), robust=True)
    return plan


def verify_inputs(plan):
    for role in plan.config["suites"]:
        seal_suite(plan, role)
    if plan.calibration_cell_id and not any(
        isinstance(row.expected_output, dict) and "probabilities" in row.expected_output
        for row in rows.iter_rows(plan.calibration_cell)
    ):
        raise InputValidationError(
            "Temperature calibration requires probability-labelled decisions"
        )
    evidence = {
        "suite_overlap": rows.contamination(plan.calibration_cell, plan.final_cell)
        if plan.calibration_cell_id
        else None,
        "boundaries": {},
    }
    for participant in participants(plan):
        if participant.get("job"):
            job = FinetuningJob.objects.select_related("cell__dataset").get(
                pk=participant["job"], project_id=plan.project_id
            )
            evidence["boundaries"][participant["key"]] = {
                role: rows.contamination(job.cell, getattr(plan, role + "_cell"))
                if job.cell_id
                else {"status": "unknown"}
                for role in plan.config["suites"]
            }
    with transaction.atomic():
        locked = NativeEvaluationPlan.objects.select_for_update().get(pk=plan.pk)
        locked.config = {**locked.config, **evidence}
        locked.save()
    return evidence


@transaction.atomic
def reuse_predictions(plan, *, source, participant, source_participant):
    plan = NativeEvaluationPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.state != "draft" or source.project_id != plan.project_id or source.pk == plan.pk:
        raise InputValidationError(
            "Reuse requires a draft and a different comparison in the same project"
        )
    target_arm = next((p for p in participants(plan) if p["key"] == participant), None)
    source_arm = next((p for p in participants(source) if p["key"] == source_participant), None)
    if target_arm is None or source_arm is None:
        raise InputValidationError("Select saved participants")

    def identity(participant):
        return {k: v for k, v in participant.items() if k not in {"key", "name"}}

    if identity(target_arm) != identity(source_arm) or any(
        plan.config[key] != source.config[key]
        for key in ("suites", "runtime", "inference", "logarithm_floor")
    ):
        raise InputValidationError(
            "Prediction reuse requires identical sources, model identities, runtime and inference conditions"
        )
    stages = {}
    for role in plan.config["suites"]:
        stage = source.calls.get(f"{role}_{source_participant}", {})
        if stage.get("state") != "completed":
            raise InputValidationError(
                "Reuse requires completed predictions for each selected suite"
            )
        path = directory(source, role) / f"{source_participant}.jsonl"
        failure = path.with_suffix(".failures.jsonl")
        stages[role] = {
            "predictions_sha256": digest_file(path),
            "failures_sha256": digest_file(failure) if failure.exists() else None,
            "receipt": stage["receipt"],
        }
    lineage = {
        "evaluation": str(source.pk),
        "participant": source_participant,
        "stages": stages,
        "independent_repetition": False,
    }
    plan.config = {**plan.config, "reuse": {**plan.config.get("reuse", {}), participant: lineage}}
    plan.save()
    return plan


def collect_reused(plan, role, participant):
    lineage = plan.config["reuse"][participant["key"]]
    source = NativeEvaluationPlan.objects.get(pk=lineage["evaluation"], project_id=plan.project_id)
    pinned = lineage["stages"][role]
    seal_suite(plan, role)
    seal_suite(source, role)
    original = directory(source, role) / f"{lineage['participant']}.jsonl"
    target = directory(plan, role) / f"{participant['key']}.jsonl"
    for previous, destination, checksum in [
        (original, target, pinned["predictions_sha256"]),
        (
            original.with_suffix(".failures.jsonl"),
            target.with_suffix(".failures.jsonl"),
            pinned["failures_sha256"],
        ),
    ]:
        if checksum is not None:
            if digest_file(previous) != checksum:
                raise InputValidationError("Saved prediction evidence changed")
            shutil.copyfile(previous, destination)
    return {
        **pinned["receipt"],
        "reused_from": str(source.pk),
        "reused_participant": lineage["participant"],
        "incremental_provider_calls": 0,
    }


def ready_stages(plan):
    if plan.state in {"draft", "prepared", "paused", "completed"}:
        return []
    if plan.calls.get("verify_inputs", {}).get("state") != "completed":
        return (
            []
            if plan.calls.get("verify_inputs", {}).get("state") in {"failed", "submission_unknown"}
            else ["verify_inputs"]
        )
    if plan.state == "preparing":
        return []
    complete = {key for key, call in plan.calls.items() if call.get("state") == "completed"}
    ready = []
    jobs = {
        str(job.pk): job.status
        for job in FinetuningJob.objects.filter(
            pk__in=[p["job"] for p in participants(plan) if p.get("job")],
            project_id=plan.project_id,
        )
    }
    sequence = stage_sequence(plan)
    for stage in sequence:
        call = plan.calls.get(stage, {})
        if call.get("state") in {"completed", "failed", "submission_unknown"}:
            continue
        if stage == "verify_inputs":
            continue
        if stage == "fit_calibration":
            dependencies = [s for s in sequence if s.startswith("calibration_")]
        elif stage == "score":
            dependencies = sequence[:-1]
        else:
            role, participant, preparation, group = provider_stage(plan, stage)
            if participant.get("job") and jobs.get(participant["job"]) != "succeeded":
                continue
            dependencies = [] if preparation or not group else [f"{role}_{group}"]
            if role == "final" and plan.calibration_cell_id:
                dependencies.append("fit_calibration")
        if all(dependency in complete for dependency in dependencies):
            ready.append(stage)
    return ready


@transaction.atomic
def pause(plan):
    plan = NativeEvaluationPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.state not in {"draft", "completed"}:
        plan.state = "paused"
        plan.save()
    return plan


def settle(plan):
    if plan.state == "paused":
        return
    failures = [
        call
        for call in plan.calls.values()
        if call.get("state") in {"failed", "submission_unknown"}
    ]
    if failures and not ready_stages(plan):
        plan.state = (
            "submission_unknown"
            if any(c["state"] == "submission_unknown" for c in failures)
            else "failed"
        )
        plan.error = "An evaluation stage stopped. Independent eligible work is retained; inspect stage receipts before resuming."
    elif plan.state != "completed":
        plan.state = "running"


def advance(plan_id, *, stage=None):
    with transaction.atomic():
        plan = (
            NativeEvaluationPlan.objects.select_for_update(of=("self",))
            .select_related(
                "job", "project", "triggered_by", "calibration_cell__dataset", "final_cell__dataset"
            )
            .get(pk=plan_id)
        )
        failed_jobs = {
            str(job.pk): job.status
            for job in FinetuningJob.objects.filter(
                pk__in=[p["job"] for p in participants(plan) if p.get("job")],
                project_id=plan.project_id,
                status__in=["failed", "cancelled"],
            )
        }
        if plan.state not in {"draft", "prepared", "paused", "completed"}:
            for pending in stage_sequence(plan):
                if (
                    pending in {"verify_inputs", "fit_calibration", "score"}
                    or plan.calls.get(pending, {}).get("state") == "completed"
                ):
                    continue
                _, arm, _, _ = provider_stage(plan, pending)
                if arm.get("job") in failed_jobs:
                    plan.calls = {
                        **plan.calls,
                        pending: {
                            "state": "failed",
                            "error": "Required training checkpoint is unavailable",
                            "dependency": arm["job"],
                            "dependency_state": failed_jobs[arm["job"]],
                        },
                    }
            if failed_jobs:
                settle(plan)
                plan.save()
        available = ready_stages(plan)
        stage = stage or next(iter(available), None)
        if stage not in available:
            return
        local = stage in {"verify_inputs", "fit_calibration", "score"}
        reused = not local and provider_stage(plan, stage)[1]["key"] in plan.config.get("reuse", {})
        external = not local and provider_stage(plan, stage)[1]["kind"] == "external"
        call = dict(plan.calls.get(stage, {}))
        since = call.get("lease_started_at") or call.get("intent_at")
        if call.get("lease") or call.get("state") == "submitting":
            if since and (timezone.now() - datetime.fromisoformat(since)).total_seconds() < 1260:
                return
            if not local and not external and not reused and not call.get("id"):
                plan.calls = {**plan.calls, stage: {**call, "state": "submission_unknown"}}
                settle(plan)
                plan.save()
                return
        starting = not call.get("id")
        lease = uuid.uuid4().hex
        queued_at = call.get("enqueued_at")
        queue_wait = (
            max(0, (timezone.now() - datetime.fromisoformat(queued_at)).total_seconds())
            if queued_at
            else None
        )
        call = {
            **call,
            "state": "submitting" if starting else "running",
            "lease": lease,
            "lease_started_at": timezone.now().isoformat(),
            "last_queue_wait_seconds": queue_wait,
        }
        plan.calls = {**plan.calls, stage: call}
        preparing_only = plan.state == "preparing"
        if not preparing_only:
            plan.state = "running"
        plan.save()
    observed_at = time.perf_counter()
    try:
        if stage == "verify_inputs":
            receipt = verify_inputs(plan)
        elif stage == "fit_calibration":
            receipt = fit_calibration(plan)
        elif stage == "score":
            receipt = score(plan)
        elif reused:
            role, participant, _, _ = provider_stage(plan, stage)
            receipt = collect_reused(plan, role, participant)
        elif external:
            role, participant, _, _ = provider_stage(plan, stage)
            receipt = external_step(plan, role, participant)
        elif starting:
            call["id"] = submit(plan, stage)
            receipt = None
        else:
            receipt = modal.FunctionCall.from_id(call["id"]).get(timeout=0)
            collect(plan, stage, receipt)
        call.update(
            state="completed"
            if receipt is not None and (not external or reused or receipt["completed"])
            else "running",
            receipt=receipt,
        )
    except TimeoutError:
        call["state"] = (
            "submission_unknown"
            if starting and not local and not external and not reused
            else "running"
        )
    except Exception as exc:
        logger.exception("Native evaluation stage failed: %s %s", plan_id, stage)
        call.update(
            state="submission_unknown"
            if isinstance(exc, SubmissionUnknownError)
            or (starting and not local and not external and not reused)
            else "failed",
            error="The stage could not be verified. Inspect retained provider receipts and diagnostics.",
        )
    with transaction.atomic():
        locked = NativeEvaluationPlan.objects.select_for_update().get(pk=plan.pk)
        if locked.calls.get(stage, {}).get("lease") != lease:
            return
        call["observation_active_seconds"] = (
            call.get("observation_active_seconds", 0) + time.perf_counter() - observed_at
        )
        call["observed_at"] = timezone.now().isoformat()
        call["timing_scope"] = (
            "local stage execution and provider observation; native provider compute duration is separate"
        )
        call.pop("enqueued_at", None)
        call.pop("lease", None)
        call.pop("lease_started_at", None)
        locked.calls = {**locked.calls, stage: call}
        if (
            stage == "verify_inputs"
            and preparing_only
            and call["state"] == "completed"
            and locked.state != "paused"
        ):
            locked.state = "prepared"
        elif stage == "score" and call["state"] == "completed":
            locked.state, locked.results = "completed", receipt
        else:
            settle(locked)
        locked.save()


def describe(plan):
    return {
        "id": str(plan.id),
        "job": str(plan.job_id) if plan.job_id else None,
        "project": str(plan.project_id),
        "name": plan.name,
        "state": plan.state,
        "config": plan.config,
        "calls": plan.calls,
        "calibration": plan.calibration,
        "results": plan.results,
        "error": plan.error,
        "created_at": plan.created_at.isoformat(),
        "updated_at": plan.updated_at.isoformat(),
    }
