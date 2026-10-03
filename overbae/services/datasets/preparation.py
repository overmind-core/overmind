from __future__ import annotations

import copy
import uuid
from typing import Annotated, Literal

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, model_validator

from overbae.models import Cell, Dataset
from overbae.services.datasets import paths, rows, store
from overbae.services.datasets.context import context_fingerprint
from overbae.services.datasets.examples import MAPPING_FIELDS, field_value, validate_mapping


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Family(PlanModel):
    name: str = Field(min_length=1, max_length=200)
    evidence: str = Field(min_length=1, max_length=2000)
    input_columns: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list, max_length=30
    )
    target_columns: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list, max_length=30
    )
    group_columns: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list,
        max_length=30,
        description="Shared case, conversation or seed identities that must stay together across splits. Not task-family labels, classes, source names or numeric weights.",
    )
    coverage_columns: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list,
        max_length=30,
        description="Task, class, source or other strata used to measure coverage; these do not imply shared case identity.",
    )


class Step(PlanModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=2000)
    kind: Literal["inspect", "transform", "sample", "audit"]


class Check(PlanModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    category: Literal["technical", "preservation", "coverage", "semantic"]
    method: Literal["deterministic", "semantic", "unmeasured"]
    question: str = Field(min_length=1, max_length=2000)


class PlanRequest(PlanModel):
    version: str = Field(min_length=1, max_length=64)
    objective: str = Field(min_length=1, max_length=4000)
    consumer: Literal[
        "sft", "decision_training", "model_evaluation", "decision_evaluation", "custom"
    ]
    understanding: str = Field(min_length=1, max_length=6000)
    families: list[Family] = Field(min_length=1, max_length=30)
    mapping: dict[str, str] = Field(
        default_factory=dict,
        max_length=30,
        description="Canonical destination field -> actual source path. Supported destinations: "
        + ", ".join(sorted(MAPPING_FIELDS))
        + ". Include supplied example weights as decision.weight. A native mapping must provide decision.kind, or a constant kind justified by the source. Use a custom transform step for per-row derived kinds.",
    )
    constants: dict = Field(
        default_factory=dict,
        max_length=10,
        description="Declared constants for decision.kind, decision.state or decision.options only. Never invent target probabilities. Native kinds are choice, noul or score.",
    )
    assumptions: list[str] = Field(default_factory=list, max_length=30)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=30)
    steps: list[Step] = Field(default_factory=list, max_length=30)
    checks: list[Check] = Field(min_length=1, max_length=30)
    semantic_row_budget: int = Field(default=0, ge=0, le=2000)

    @model_validator(mode="after")
    def validate_plan(self):
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("Plan step IDs must be unique.")
        if len({check.name for check in self.checks}) != len(self.checks):
            raise ValueError("Plan check names must be unique.")
        for value in [*self.assumptions, *self.unresolved_questions]:
            if not value.strip() or len(value) > 2000:
                raise ValueError("Assumptions and questions must contain 1–2000 characters.")
        validate_mapping(self.mapping, self.constants)
        return self


def save_plan(dataset, cell, request: PlanRequest, *, user_request="", exploration=None):
    rows.verify(cell)
    expected_intent = {
        "sft": "train",
        "decision_training": "train",
        "model_evaluation": "eval",
        "decision_evaluation": "eval",
    }.get(request.consumer)
    if expected_intent and dataset.intent != expected_intent:
        raise ValueError("The plan's consumer does not match the dataset purpose.")
    declared = set(request.mapping.values())
    for family in request.families:
        declared.update(
            [
                *family.input_columns,
                *family.target_columns,
                *family.group_columns,
                *family.coverage_columns,
            ]
        )
    found = set()
    for row in store.iter_rows(paths.cell_path(dataset.id, cell.id)):
        for path in declared - found:
            try:
                field_value(row, path)
            except ValueError:
                continue
            found.add(path)
        if found == declared:
            break
    if declared - found:
        raise ValueError("Plan references missing fields: " + ", ".join(sorted(declared - found)))
    spec = request.model_dump(exclude={"version"})
    binding = {
        "source_cell": str(cell.id),
        "source_fingerprint": cell.fingerprint,
        "intent": dataset.intent,
        "context_fingerprint": context_fingerprint(dataset.capability),
    }
    with transaction.atomic():
        current = (
            Dataset.objects.select_for_update(of=("self",))
            .select_related("capability")
            .get(pk=dataset.pk)
        )
        pinned = Cell.objects.get(pk=cell.pk, dataset_id=dataset.pk)
        if (
            pinned.fingerprint != cell.fingerprint
            or current.intent != dataset.intent
            or current.capability_id != dataset.capability_id
            or context_fingerprint(current.capability) != binding["context_fingerprint"]
        ):
            raise ValueError("The dataset changed while planning. Inspect it again.")
        previous = current.preparation_plan
        if previous.get("specification") == spec and all(
            previous.get(k) == v for k, v in binding.items()
        ):
            dataset.preparation_plan = previous
            return previous
        plan = {
            "id": str(uuid.uuid4()),
            **binding,
            "specification": spec,
            "user_request": user_request[:8000],
            "exploration": (exploration or [])[-12:],
            "created_at": timezone.now().isoformat(),
        }
        Dataset.objects.filter(pk=current.pk).update(preparation_plan=plan)
    dataset.preparation_plan = plan
    return plan


def for_cell(dataset, cell):
    current = dataset.preparation_plan or {}
    plan = current if current.get("source_cell") == str(cell.id) else cell.preparation_plan or {}
    fingerprint = (
        plan.get("source_fingerprint") if plan is current else plan.get("result_fingerprint")
    )
    if (
        not plan
        or fingerprint != cell.fingerprint
        or plan.get("intent") != dataset.intent
        or plan.get("context_fingerprint") != context_fingerprint(dataset.capability)
    ):
        return {}
    return plan


def execution_plan(dataset, previous, step_id, *, required=False):
    if not required and not step_id:
        return {}
    plan = dataset.preparation_plan or {}
    if not plan:
        if required or step_id:
            raise ValueError(
                "Inspect the rows and save a preparation plan before transforming them."
            )
        return {}
    source = dataset.cells.filter(pk=plan.get("source_cell"), state=Cell.State.OK).first()
    if (
        source is None
        or source.fingerprint != plan.get("source_fingerprint")
        or plan.get("intent") != dataset.intent
        or plan.get("context_fingerprint") != context_fingerprint(dataset.capability)
    ):
        raise ValueError("The plan's source or task context changed. Inspect and revise the plan.")
    if str(previous.id) != plan["source_cell"] and (
        previous.preparation_plan.get("id") != plan["id"]
        or previous.preparation_plan.get("result_fingerprint") != previous.fingerprint
    ):
        raise ValueError("The active chain changed outside this plan. Inspect and revise the plan.")
    if not step_id:
        if required:
            raise ValueError("Choose the plan_step being executed.")
        return {}
    step = next((step for step in plan["specification"]["steps"] if step["id"] == step_id), None)
    if step is None or step["kind"] not in {"transform", "sample"}:
        raise ValueError("Choose a transform or sample step from the saved plan.")
    return {**copy.deepcopy(plan), "step_id": step_id}


def reserve_semantic_rows(dataset, cell, checks, requested):
    plan = for_cell(dataset, cell)
    if not plan:
        return 0
    declared = {
        check["name"] for check in plan["specification"]["checks"] if check["method"] == "semantic"
    }
    if not set(checks) <= declared:
        return 0
    with transaction.atomic():
        current = Dataset.objects.select_for_update().get(pk=dataset.pk)
        saved = current.preparation_plan
        if saved.get("id") != plan["id"]:
            return 0
        remaining = saved["specification"]["semantic_row_budget"] - saved.get(
            "semantic_rows_reserved", 0
        )
        reserved = min(requested, cell.rows, max(0, remaining))
        saved["semantic_rows_reserved"] = saved.get("semantic_rows_reserved", 0) + reserved
        Dataset.objects.filter(pk=current.pk).update(preparation_plan=saved)
    return reserved


def describe(dataset):
    plan = copy.deepcopy(dataset.preparation_plan or {})
    if not plan:
        return {}
    cells = list(dataset.cells.all())
    source = next((cell for cell in cells if str(cell.id) == plan["source_cell"]), None)
    plan["stale"] = (
        source is None
        or source.fingerprint != plan["source_fingerprint"]
        or plan["intent"] != dataset.intent
        or plan["context_fingerprint"] != context_fingerprint(dataset.capability)
    )
    plan["executions"] = [
        {
            "cell": str(cell.id),
            "step_id": cell.preparation_plan.get("step_id"),
            "state": cell.state,
            "rows": cell.rows,
            "fingerprint": cell.fingerprint,
        }
        for cell in cells
        if cell.preparation_plan.get("id") == plan["id"]
    ]
    return plan


def group_columns(dataset, cell):
    return sorted(
        set(dataset.source_spec.get("split", {}).get("group_by", []))
        | {
            column
            for family in cell.preparation_plan.get("specification", {}).get("families", [])
            for column in family["group_columns"]
        }
    )
