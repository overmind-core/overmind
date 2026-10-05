from django.urls import reverse

from overbae.models import (
    DataExploration,
    DataPartitionPlan,
    DecisionPerformanceRun,
    NativeEvaluationPlan,
    TrainingExperiment,
)
from overbae.services import decision_performance, native_evaluation, training_experiments
from overbae.services.datasets import exploration, partition_plans

MODELS = {
    "data_exploration": DataExploration,
    "decision_performance": DecisionPerformanceRun,
    "data_partition": DataPartitionPlan,
    "native_evaluation": NativeEvaluationPlan,
    "training_experiment": TrainingExperiment,
}
DESCRIBERS = {
    "data_exploration": exploration.describe,
    "decision_performance": decision_performance.describe,
    "data_partition": partition_plans.describe,
    "native_evaluation": native_evaluation.describe,
    "training_experiment": training_experiments.describe,
}


def find(project, kind, identifier):
    return MODELS[kind].objects.filter(project=project, pk=identifier).first()


def _comparison_summary(comparison):
    result = {
        key: value
        for key, value in comparison.items()
        if key not in {"baseline", "candidate", "benchmarks"}
    }
    result["benchmark_count"] = len(comparison.get("benchmarks", {}))
    for arm in ("baseline", "candidate"):
        values = comparison.get(arm, {})
        benchmarks = values.get("benchmarks", {})
        coverage = {
            key: sum(card.get(key, 0) for card in benchmarks.values())
            for key in (
                "expected",
                "scored",
                "missing_predictions",
                "invalid_predictions",
                "incompatible_inputs",
            )
        }
        coverage["fraction"] = (
            coverage["scored"] / coverage["expected"] if coverage["expected"] else None
        )
        result[arm] = {
            key: value for key, value in values.items() if key not in {"benchmarks", "slices"}
        }
        result[arm]["coverage"] = coverage
    result["paired_coverage"] = {
        key: sum(card.get(key, 0) for card in comparison.get("benchmarks", {}).values())
        for key in ("expected", "paired_decisions", "baseline_only", "candidate_only")
    }
    return result


def _evaluation_results_summary(results):
    if not results:
        return {}
    summary = {
        key: value
        for key, value in results.items()
        if key not in {"calls", "comparisons", "calibration", "raw", "calibrated"}
    }
    summary["detail"] = "summary; complete benchmark metrics and diagnostics are in report"
    summary["comparisons"] = {
        arm: {mode: _comparison_summary(value) for mode, value in modes.items()}
        for arm, modes in results.get("comparisons", {}).items()
    }
    if results.get("calibration"):
        summary["calibration"] = _evaluation_results_summary(results["calibration"])
    return summary


def _evaluation_summary(record):
    summary = native_evaluation.describe(record)
    summary["results"] = _evaluation_results_summary(record.results)
    summary["calls"] = {
        stage: {
            **{key: value for key, value in call.items() if key != "receipt"},
            **(
                {
                    "receipt": (
                        {
                            key: value
                            for key, value in call["receipt"].items()
                            if key not in {"codebook", "tokens"}
                        }
                        if call.get("receipt") is not None
                        else None
                    )
                }
                if stage != "score"
                else {}
            ),
        }
        for stage, call in record.calls.items()
    }
    path = reverse("native-evaluation-report", kwargs={"pk": record.pk})
    summary["report"] = {
        "available": record.state == "completed",
        "json_path": path + "?format=json",
        "markdown_path": path + "?format=md",
        "authentication": "Use the same Overmind API credential on this server.",
        "contents": "All benchmarks, raw/calibrated metrics, paired intervals, coverage, failures, calibration results and diagnostics.",
    }
    return summary


def describe(kind, record):
    return {
        **(
            _evaluation_summary(record) if kind == "native_evaluation" else DESCRIBERS[kind](record)
        ),
        "next_actions": next_actions(kind, record),
        **guidance(kind, record),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def next_actions(kind, record):
    if record.state == "completed":
        return ["get_job"]
    if kind == "native_evaluation":
        return (
            ["launch_native_evaluation"]
            if record.state in {"draft", "prepared"}
            else ["resume_native_evaluation"]
            if record.state in {"paused", "failed", "submission_unknown"}
            else ["get_job", "pause_native_evaluation"]
        )
    if kind == "training_experiment" and record.state in {"draft", "preparation_failed"}:
        return ["prepare_training_experiment"]
    if kind == "training_experiment" and record.state == "prepared":
        return (
            ["launch_training_experiment"]
            if training_experiments.launch_readiness(record)["allowed"]
            else ["get_job"]
        )
    if kind == "data_exploration" and record.state == "failed":
        return ["derive_dataset" if record.kind == "derive" else "explore_dataset"]
    if kind == "data_partition" and record.state == "failed":
        return ["retry_data_partition"]
    return ["get_job"]


def guidance(kind, record):
    required = {}
    if kind == "training_experiment" and record.state == "prepared":
        required = {
            "experiment": str(record.pk),
            "quote_id": record.protocol.get("forecast", {}).get("id"),
            "authorization_scope": "saved_candidates_and_evaluation",
        }
        readiness = training_experiments.launch_readiness(record)
        if not readiness["allowed"]:
            required["blocked_reason"] = readiness["reason"]
    elif kind == "native_evaluation":
        required = {"evaluation": str(record.pk)}
        if record.state == "submission_unknown":
            required["unresolved_stages"] = [
                key
                for key, value in record.calls.items()
                if value.get("state") == "submission_unknown"
            ]
            required["resolution"] = (
                "Recover the existing provider call ID; never submit another request to resolve an unknown acknowledgement"
            )
        if record.state in {"draft", "prepared"}:
            required["authorization_scope"] = "saved_participants_and_suites"
    elif kind == "data_exploration" and record.state == "failed":
        required = {
            "source_cell": str(record.source_cell_id),
            "name": record.name,
            "request_key": record.request_key,
        }
        if record.config.get("sampling"):
            required["sampling"] = record.config["sampling"]
    return {
        "action_requirements": required,
        "input_required": record.state
        in {
            "draft",
            "prepared",
            "paused",
            "failed",
            "submission_unknown",
            "preparation_failed",
            "incomplete",
        },
        "poll_after_seconds": None
        if record.state
        in {
            "completed",
            "failed",
            "submission_unknown",
            "draft",
            "prepared",
            "paused",
            "preparation_failed",
            "incomplete",
        }
        else 10,
    }
