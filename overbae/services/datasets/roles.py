from django.db.models import Q

from overbae.models import Cell, DataPartitionMember, EvalRun, FinetuningJob, NativeEvaluationPlan

ROLE_NAMES = ("training", "development", "calibration", "evaluation", "source")
PARTITION_ROLES = {
    "train": "training",
    "development": "development",
    "calibration": "calibration",
    "final": "evaluation",
}


def dataset_roles(datasets):
    roles = {dataset.pk: set() for dataset in datasets}
    ids = list(roles)
    if not ids:
        return {}

    def add(identity, role):
        if identity in roles:
            roles[identity].add(role)

    for identity, source_identity, role in DataPartitionMember.objects.filter(
        Q(cell__dataset_id__in=ids) | Q(plan__source_cell__dataset_id__in=ids),
        plan__state="completed",
    ).values_list("cell__dataset_id", "plan__source_cell__dataset_id", "role"):
        if role in PARTITION_ROLES:
            add(identity, PARTITION_ROLES[role])
            add(source_identity, PARTITION_ROLES[role])

    training_fields = {
        "dataset_id": "training",
        "cell__dataset_id": "training",
        "validation_dataset_id": "development",
        "validation_cell__dataset_id": "development",
        "eval_dataset_id": "evaluation",
        "eval_cell__dataset_id": "evaluation",
    }
    evaluation_fields = {
        "calibration_cell__dataset_id": "calibration",
        "final_cell__dataset_id": "evaluation",
    }
    for model, fields in (
        (FinetuningJob, training_fields),
        (EvalRun, {"dataset_id": "evaluation", "cell__dataset_id": "evaluation"}),
        (NativeEvaluationPlan, evaluation_fields),
    ):
        scope = Q(pk__in=[])
        for field in fields:
            scope |= Q(**{f"{field}__in": ids})
        for row in model.objects.filter(scope).values_list(*fields):
            for identity, role in zip(row, fields.values(), strict=True):
                add(identity, role)

    sources = set()
    transformed = set()
    for identity, position in (
        Cell.objects.filter(dataset_id__in=ids, state="ok")
        .exclude(fingerprint="")
        .values_list("dataset_id", "position")
    ):
        (sources if position == 0 else transformed).add(identity)
    for identity in sources - transformed:
        if not roles[identity]:
            add(identity, "source")
    return {
        str(identity): [role for role in ROLE_NAMES if role in assigned]
        for identity, assigned in roles.items()
    }
