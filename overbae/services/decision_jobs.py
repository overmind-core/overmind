"""DecisionJob identity over the shared training lifecycle."""

import uuid

from overbae.core.errors import InputValidationError
from overbae.modal.decision_catalog import get as catalog_model
from overbae.models import DecisionJob
from overbae.services.datasets import versions
from overbae.services.finetuning_mcp import launch_finetune


def create(
    *,
    project,
    user,
    model_id,
    dataset,
    request_key,
    version=None,
    validation_dataset=None,
    validation_version=None,
    eval_dataset=None,
    eval_version=None,
    hyperparameters=None,
    name=None,
    use_case="",
    accepted_findings=None,
    validation_enabled=True,
    validation_split_ratio=0.2,
    split_method="random",
    admit_new_training,
):
    spec = catalog_model(model_id)
    if spec is None:
        raise InputValidationError(f"Unknown decision model {model_id}.")
    if not spec["training_available"]:
        raise InputValidationError(
            "This decision family does not yet have exact preparation and verified deployment."
        )
    if not request_key or len(request_key) > 128:
        raise InputValidationError("Supply a stable request_key of at most 128 characters.")
    if hyperparameters is not None and not isinstance(hyperparameters, dict):
        raise InputValidationError("Use a JSON object for hyperparameters.")
    if (validation_version and validation_dataset is None) or (
        eval_version and eval_dataset is None
    ):
        raise InputValidationError("A selected version requires its dataset.")
    for selected in (dataset, validation_dataset, eval_dataset):
        if selected is not None and selected.project_id != project.id:
            raise InputValidationError("All selected datasets must belong to this project.")
    cell = versions.resolve_cell(dataset, version)
    validation_cell = (
        versions.resolve_cell(validation_dataset, validation_version)
        if validation_dataset is not None
        else None
    )
    eval_cell = versions.resolve_cell(eval_dataset, eval_version) if eval_dataset else None
    hp = {
        "training_type": {"type": "Lora"},
        **(hyperparameters or {}),
    }
    job = launch_finetune(
        user=user,
        admit_new_training=admit_new_training,
        request_key=request_key,
        accepted_findings=accepted_findings,
        project=project,
        dataset=dataset,
        capability=None,
        eval_dataset=eval_dataset,
        eval_set=None,
        base_model=spec["id"],
        hyperparameters=hp,
        name=name or f"{dataset.name} · {spec['display']}",
        use_case=use_case,
        validation_enabled=validation_enabled,
        validation_split_ratio=validation_split_ratio,
        validation_dataset=validation_dataset,
        split_method=split_method,
        group_id=str(uuid.uuid4()),
        cell=cell,
        validation_cell=validation_cell,
        eval_cell=eval_cell,
        eval_model_before=False,
        eval_model_after=False,
        decision_job=True,
    )
    return DecisionJob.objects.select_related("finetuning_job").get(finetuning_job=job)
