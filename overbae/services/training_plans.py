import math
from uuid import UUID

from django.db import transaction

from modal_shared.decisions import DECISION_OBJECTIVES
from modal_shared.training_monitoring import fingerprint
from overbae.core.errors import InputValidationError
from overbae.models import (
    Cell,
    DataPartitionPlan,
    Project,
    TrainingExperiment,
    TrainingPlan,
    TrainingValidationRun,
)
from overbae.services.datasets import use
from overbae.services.training_contract import dataset_objective
from overbae.services.training_plan_contract import TrainingPlanRequest


def scoped(model, project, identifier, **filters):
    query = model.objects.filter(pk=identifier, **filters)
    if model is Cell:
        query = query.select_related("dataset")
    record = query.first()
    owner = (
        record.dataset.project_id
        if isinstance(record, Cell)
        else getattr(record, "project_id", None)
    )
    if record is None or owner != project.pk:
        raise InputValidationError("Select a retained training-plan reference in this project")
    return record


def pin(cell):
    if cell.state != "ok" or not cell.fingerprint or not cell.rows:
        raise InputValidationError("Training plans require readable, nonempty source versions")
    use.freeze(cell)
    return {
        "cell": str(cell.pk),
        "dataset": str(cell.dataset_id),
        "fingerprint": cell.fingerprint,
        "rows": cell.rows,
    }


def missing_evidence(specification):
    kinds = {item["kind"] for item in specification["evidence"]}
    return sorted({"audit", "baseline", "sanity_check"} - kinds)


def selection_snapshot(project, selection, design_identity, metric):
    check = (
        TrainingValidationRun.objects.select_related("job")
        .filter(pk=selection["check"], job__project=project)
        .first()
    )
    if (
        check is None
        or check.stream != "development"
        or check.state != "completed"
        or check.step == 0
    ):
        raise InputValidationError(
            "Select a completed post-update development check from a quality pilot"
        )
    job = check.job
    experiment = TrainingExperiment.objects.filter(project=project, pk=job.group_id).first()
    binding = (experiment.protocol.get("training_plan") or {}) if experiment else {}
    if binding.get("design_identity") != design_identity or binding.get("stage") != "pilot":
        raise InputValidationError(
            "The pilot does not match this task, prepared data and partition"
        )
    if job.hyperparameters.get("runtime_profile") or job.status not in {"succeeded", "cancelled"}:
        raise InputValidationError(
            "Use a settled quality pilot; hardware profiles are not quality evidence"
        )
    loss = check.metrics.get("eval_loss")
    if type(loss) not in {int, float} or not math.isfinite(loss) or not check.receipt_fingerprint:
        raise InputValidationError(
            "The selected development check has no retained finite loss measurement"
        )
    if not check.coverage.get("expected") or check.coverage.get("expected") != check.coverage.get(
        "scored"
    ):
        raise InputValidationError("The selected development check has incomplete scored coverage")
    observed = check.metrics
    for part in metric.removeprefix("/").split("/"):
        observed = (
            observed.get(part.replace("~1", "/").replace("~0", "~"))
            if isinstance(observed, dict)
            else None
        )
    if type(observed) not in {int, float} or not math.isfinite(observed):
        raise InputValidationError(
            "The pilot check does not measure the task's declared primary metric; use its metrics JSON Pointer"
        )
    if metric.startswith("/generation/"):
        generated = check.metrics.get("generation") or {}
        if not generated.get("expected") or generated.get("scored") != generated["expected"]:
            raise InputValidationError(
                "The selected generative task metric has incomplete coverage"
            )
    checkpoint = job.retained_checkpoints.filter(
        attempt=check.attempt, step=check.step, state="available"
    ).first()
    if (
        checkpoint is None
        or not checkpoint.identity
        or checkpoint.verification.get("reload_verified") is not True
    ):
        raise InputValidationError("The selected pilot check has no verified retained checkpoint")
    variant = next(
        (
            v
            for index, v in enumerate(experiment.variants)
            if job.request_key == f"experiment:{experiment.pk}:{index}"
        ),
        None,
    )
    if variant is None:
        raise InputValidationError("The selected pilot has no matching saved candidate recipe")
    return {
        **selection,
        "measurement": {
            "job": str(job.pk),
            "experiment": str(experiment.pk),
            "check": str(check.pk),
            "receipt_fingerprint": check.receipt_fingerprint,
            "sample_fingerprint": check.sample_fingerprint,
            "step": check.step,
            "primary_metric": {"pointer": metric, "value": observed},
            "metrics": check.metrics,
            "coverage": check.coverage,
            "checkpoint": str(checkpoint.pk),
            "checkpoint_identity": checkpoint.identity,
        },
        "recipe": variant,
        "attribution": "selection_is_agent_authored; measurements_are_retained_platform_receipts",
    }


@transaction.atomic
def save(project, *, user, **values):
    request = TrainingPlanRequest.model_validate(values).model_dump(mode="json")
    request_identity = fingerprint(request)
    Project.objects.select_for_update().get(pk=project.pk)
    prior = TrainingPlan.objects.filter(project=project, request_key=request["request_key"]).first()
    if prior:
        if prior.request_fingerprint != request_identity:
            raise InputValidationError("This request key identifies a different training plan")
        return prior
    source = scoped(Cell, project, request["source_cell"])
    previous = (
        scoped(TrainingPlan, project, request["supersedes"]) if request["supersedes"] else None
    )
    prepared = scoped(Cell, project, request["prepared_cell"]) if request["prepared_cell"] else None
    partition = (
        scoped(DataPartitionPlan, project, request["partition"]) if request["partition"] else None
    )
    bindings = {"source": pin(source), "roles": {}}
    if prepared:
        native = dataset_objective(prepared) in DECISION_OBJECTIVES
        if native != (request["task"]["mode"] == "decision"):
            raise InputValidationError(
                "The prepared target format does not match the declared training mode"
            )
        bindings["prepared"] = pin(prepared)
    if partition:
        if (
            prepared is None
            or partition.source_cell_id != prepared.pk
            or partition.source_fingerprint != prepared.fingerprint
            or partition.state != "completed"
        ):
            raise InputValidationError(
                "Select a completed partition of this exact prepared version"
            )
        if not set(request["task"]["group_by"]).issubset(partition.recipe.get("group_by", [])):
            raise InputValidationError("The partition omits task-declared grouping fields")
        bindings["partition"] = {
            "id": str(partition.pk),
            "recipe": partition.recipe,
            "report": partition.report,
        }
        for member in partition.members.select_related("cell__dataset"):
            if member.role in {"train", "development"}:
                try:
                    use.check(member.cell.dataset, "train", cell=member.cell, verify=False)
                except ValueError as exc:
                    raise InputValidationError(str(exc)) from exc
            bindings["roles"][member.role] = pin(member.cell)
        if not {"train", "development", "final"}.issubset(bindings["roles"]):
            raise InputValidationError(
                "Training plans require distinct train, development and final roles"
            )
    design = fingerprint({"task": request["task"], "bindings": bindings})
    evidence = []
    allowed = {source.pk}
    if prepared:
        allowed.add(prepared.pk)
    if partition:
        allowed.update(partition.members.values_list("cell_id", flat=True))
    for item in request["evidence"]:
        cells = [scoped(Cell, project, identifier) for identifier in item["cells"]]
        if any(cell.pk not in allowed for cell in cells):
            raise InputValidationError(
                "Evidence must reference this plan's source, prepared or partition versions"
            )
        if item["kind"] != "audit" and any(
            str(cell.pk)
            not in {
                p["cell"]
                for role, p in bindings["roles"].items()
                if role in {"train", "development"}
            }
            for cell in cells
        ):
            raise InputValidationError(
                "Baseline and sanity evidence must use only frozen train/development roles"
            )
        evidence.append(
            {
                **item,
                "bindings": [pin(cell) for cell in cells],
                "attribution": "agent_reported; not independently verified",
                "author": str(user.pk),
            }
        )
    selection = (
        selection_snapshot(project, request["selection"], design, request["task"]["primary_metric"])
        if request["selection"]
        else None
    )
    specification = {
        "task": request["task"],
        "evidence": evidence,
        "selection": selection,
        "unresolved": request["unresolved"],
    }
    state = (
        "discovery"
        if not partition
        else "needs_evidence"
        if missing_evidence(specification)
        else "selected"
        if selection
        else "ready"
    )
    return TrainingPlan.objects.create(
        project=project,
        name=request["name"],
        request_key=request["request_key"],
        request_fingerprint=request_identity,
        supersedes=previous,
        source_cell=source,
        prepared_cell=prepared,
        partition=partition,
        specification=specification,
        bindings=bindings,
        design_identity=design,
        state=state,
        created_by=user,
    )


def require_current(plan):
    pins = [plan.bindings["source"], *plan.bindings["roles"].values()]
    if plan.bindings.get("prepared"):
        pins.append(plan.bindings["prepared"])
    actual = dict(
        Cell.objects.filter(
            dataset__project_id=plan.project_id, pk__in=[p["cell"] for p in pins]
        ).values_list("id", "fingerprint")
    )
    if any(actual.get(UUID(p["cell"])) != p["fingerprint"] for p in pins):
        raise InputValidationError(
            "A training-plan data binding is stale; save new evidence for the exact versions"
        )


def bind_experiment(project, identifier, stage, variants):
    if identifier is None:
        if stage != "exploratory":
            raise InputValidationError("Pilot and full stages require a saved training plan")
        return None
    plan = scoped(TrainingPlan, project, identifier)
    require_current(plan)
    if stage not in {"pilot", "full"} or not plan.partition_id:
        raise InputValidationError(
            "Bind prepared training plans to an explicit pilot or full stage"
        )
    if stage == "full" and missing_evidence(plan.specification):
        raise InputValidationError(
            "Record the audit, baseline and sanity check before full training"
        )
    for variant in variants:
        if any(
            str(variant.get(field)) != plan.bindings["roles"][role]["cell"]
            for field, role in (("cell", "train"), ("development_cell", "development"))
        ):
            raise InputValidationError(
                "Candidates must use the plan's exact train and development roles"
            )
        hp = variant["hyperparameters"]
        if hp.get("runtime_profile"):
            raise InputValidationError("Hardware profiling is separate from quality-pilot evidence")
        if hp.get("monitoring", {}).get("selection") != "development_loss":
            raise InputValidationError(
                "Plan-bound training requires explicit development-loss checkpoint selection"
            )
        if stage == "pilot" and (
            type(hp.get("max_steps")) is not int or not 1 <= hp["max_steps"] <= 2048
        ):
            raise InputValidationError(
                "Quality pilots require an explicit max_steps bound from 1 to 2048"
            )
        if stage == "full" and plan.specification.get("selection"):
            selected = plan.specification["selection"]
            recipe = selected["recipe"]
            excluded = set(selected["budget_fields"])
            if variant["base_model"] != recipe["base_model"] or {
                k: v for k, v in hp.items() if k not in excluded
            } != {k: v for k, v in recipe["hyperparameters"].items() if k not in excluded}:
                raise InputValidationError(
                    "The full recipe differs from its selected pilot beyond declared training-budget changes"
                )
    return {
        "id": str(plan.pk),
        "design_identity": plan.design_identity,
        "stage": stage,
        "bindings": plan.bindings,
        "selection": plan.specification["selection"],
        "unresolved": plan.specification["unresolved"],
    }


def describe(plan):
    return {
        "id": str(plan.pk),
        "name": plan.name,
        "state": plan.state,
        "supersedes": str(plan.supersedes_id) if plan.supersedes_id else None,
        "design_identity": plan.design_identity,
        "bindings": plan.bindings,
        **plan.specification,
        "missing_evidence": missing_evidence(plan.specification),
        "semantic_quality": "Not certified; evidence and selection remain attributable",
        "next_actions": ["create_training_experiment"]
        if plan.partition_id
        else ["explore_dataset", "save_dataset_pipeline"],
    }
