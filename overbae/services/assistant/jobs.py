"""Jobs a thread waits for. A job rests when the platform will not move it again by itself:
it ended, or it waits for a decision. A waiting thread resumes when all its jobs rest.

Job ids here come from the results of project-scoped tools, so the lookups carry no scope."""

from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Model

from overbae.models import (
    AssistantHandoff,
    Dataset,
    DatasetPipelineRun,
    DecisionJob,
    DeployedModel,
    EvalRun,
    FinetuningJob,
    InferenceRequest,
    ModelActivation,
    TrainingPreparation,
)
from overbae.services import model_workflows


@dataclass(frozen=True)
class Kind:
    model: type[Model]
    state: str
    resting: frozenset


_TRAINING = frozenset({"succeeded", "failed", "cancelled", "awaiting_launch"})
KINDS = {
    "dataset_pipeline": Kind(
        DatasetPipelineRun, "state", frozenset({"completed", "failed", "cancelled"})
    ),
    "dataset_run": Kind(Dataset, "state", frozenset({"idle", "error"})),
    "training_preparation": Kind(
        TrainingPreparation, "state", frozenset({"ready", "failed", "incompatible"})
    ),
    "finetune_job": Kind(FinetuningJob, "status", _TRAINING),
    "decision_job": Kind(DecisionJob, "finetuning_job__status", _TRAINING),
    "eval_run": Kind(EvalRun, "status", frozenset(EvalRun.TERMINAL)),
    "deployment": Kind(DeployedModel, "status", frozenset({"ready", "failed", "deleted"})),
    "model_activation": Kind(
        ModelActivation, "stage", frozenset({"complete", "failed", "cancelled"})
    ),
    "inference_request": Kind(InferenceRequest, "state", frozenset({"succeeded", "failed"})),
    "handoff": Kind(
        AssistantHandoff,
        "state",
        frozenset({AssistantHandoff.State.DONE, AssistantHandoff.State.CANCELLED}),
    ),
    **{
        kind: Kind(model, "state", model_workflows.RESTING)
        for kind, model in model_workflows.MODELS.items()
        # A training plan is guidance the agent reads, not work the platform runs.
        if kind != "training_plan"
    },
}


def moving(jobs: list[dict]) -> set[tuple[str, str]]:
    """The ``(kind, id)`` of the jobs the platform still works on, in one query per kind.
    An unknown kind or a missing record rests."""
    by_kind = defaultdict(list)
    for job in jobs:
        if job["kind"] in KINDS:
            by_kind[job["kind"]].append(job["id"])
    busy = set()
    for name, identifiers in by_kind.items():
        kind = KINDS[name]
        rows = kind.model.objects.filter(pk__in=identifiers)
        busy |= {
            (name, str(pk))
            for pk in rows.exclude(**{f"{kind.state}__in": kind.resting}).values_list(
                "pk", flat=True
            )
        }
    return busy
