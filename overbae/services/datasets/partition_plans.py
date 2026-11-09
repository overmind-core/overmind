import json
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from overbae.core.errors import InputValidationError
from overbae.models import DataPartitionMember, DataPartitionPlan, Dataset, Project
from overbae.services.datasets import land, partition_assignment, paths, rows, store, use
from overbae.services.datasets.examples import native_decision, prepare_native_record
from overbae.services.datasets.partition import preserve_lineage
from overbae.services.datasets.partition_assignment import validate_recipe


@transaction.atomic
def request_plan(project, *, name, request_key, source_cell, recipe):
    if source_cell.dataset.project_id != project.pk:
        raise InputValidationError("The source must belong to this project")
    recipe = validate_recipe(recipe)
    Project.objects.select_for_update().get(pk=project.pk)
    Dataset.objects.select_for_update().get(pk=source_cell.dataset_id)
    existing = DataPartitionPlan.objects.filter(project=project, request_key=request_key).first()
    if existing:
        if (
            existing.recipe != recipe
            or existing.source_cell_id != source_cell.pk
            or existing.name != name
        ):
            raise InputValidationError(
                "This request key already identifies a different partition plan"
            )
        return existing
    rows.verify(source_cell)
    if not source_cell.ran or not source_cell.rows:
        raise InputValidationError("Partitioning requires a nonempty readable version")
    use.freeze(source_cell)
    return DataPartitionPlan.objects.create(
        project=project,
        name=name,
        request_key=request_key,
        source_cell=source_cell,
        source_fingerprint=source_cell.fingerprint,
        recipe=recipe,
    )


def assign(cell, recipe, destination, *, progress=None):
    return partition_assignment.assign(
        store.iter_rows(rows.frame_path(cell)), cell.rows, recipe, destination, progress=progress
    )


def prepared_member_rows(source, role):
    for line in source:
        record = json.loads(line)
        decision = native_decision(record)
        if role in {"calibration", "final"} and decision is not None:
            # This is a lossless native wire projection; interpretation remains Workshop-owned.
            yield prepare_native_record(record, decision, "eval")
        else:
            yield record


def build(plan_id, *, assignment_file=None):
    with transaction.atomic():
        plan = (
            DataPartitionPlan.objects.select_for_update()
            .select_related("source_cell__dataset", "project")
            .get(pk=plan_id)
        )
        if plan.state == "completed" or (
            plan.state == "running" and plan.updated_at > timezone.now() - timedelta(seconds=1260)
        ):
            return
        plan.state, plan.error = "running", ""
        plan.save()

    def progress(stage, completed, total, **extra):
        value = {
            "stage": stage,
            "completed": completed,
            "total": total,
            **extra,
            "updated_at": timezone.now().isoformat(),
        }
        DataPartitionPlan.objects.filter(pk=plan.pk, state="running").update(
            report={"progress": value}, updated_at=timezone.now()
        )
        return value

    try:
        progress("verifying", 0, plan.source_cell.rows)
        rows.verify(plan.source_cell)
        if plan.source_cell.fingerprint != plan.source_fingerprint:
            raise InputValidationError("The frozen partition source changed")
        directory = paths.media_root() / "data-partitions" / str(plan.id)
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            work = Path(temporary)

            if assignment_file is None:
                report = assign(plan.source_cell, plan.recipe, work, progress=progress)
            else:
                report = partition_assignment.verify(
                    store.iter_rows(rows.frame_path(plan.source_cell)),
                    plan.recipe,
                    assignment_file,
                )
                (work / "assignments.jsonl").write_bytes(assignment_file.read_bytes())
            progress("writing", 0, plan.source_cell.rows)
            streams = {
                role: (work / f"{role}.jsonl").open("w") for role in plan.recipe["fractions"]
            }
            try:
                with (work / "assignments.jsonl").open() as assignments:
                    for index, (record, line) in enumerate(
                        zip(
                            store.iter_rows(rows.frame_path(plan.source_cell)),
                            assignments,
                            strict=True,
                        )
                    ):
                        if index % 10000 == 0:
                            progress("writing", index, plan.source_cell.rows)
                        assignment = json.loads(line)
                        preserved = {
                            **record,
                            "_overmind_provenance": preserve_lineage(
                                record, group_by=plan.recipe["group_by"]
                            ),
                        }
                        preserved.setdefault("_overmind_provenance", {})["partition"] = {
                            "plan": str(plan.pk),
                            "role": assignment["role"],
                            "source_cell": str(plan.source_cell_id),
                            "source_row": assignment["row"],
                        }
                        streams[assignment["role"]].write(json.dumps(preserved) + "\n")
            finally:
                for stream in streams.values():
                    stream.close()
            for role in plan.recipe["fractions"]:
                progress("preparing_member", 0, report["counts"][role], role=role)
                existing = plan.members.select_related("cell__dataset").filter(role=role).first()
                if existing:
                    rows.verify(existing.cell)
                    continue
                with transaction.atomic():
                    dataset_id = uuid.uuid5(plan.pk, role)
                    dataset, _ = Dataset.objects.get_or_create(
                        pk=dataset_id,
                        defaults={
                            "project": plan.project,
                            "name": f"{plan.name[: 252 - len(role)]} · {role}",
                            "brief": plan.source_cell.dataset.brief,
                            "intent": "eval" if role in {"calibration", "final"} else "train",
                            "state": Dataset.State.LANDING,
                        },
                    )
                    with (work / f"{role}.jsonl").open() as source:
                        land.commit(
                            dataset,
                            land.Landing(
                                prepared_member_rows(source, role),
                                spec={
                                    "partition_plan": str(plan.pk),
                                    "role": role,
                                    "source_cell": str(plan.source_cell_id),
                                    "fingerprint": plan.source_fingerprint,
                                },
                            ),
                            infer_capability=False,
                        )
                    member, _ = DataPartitionMember.objects.get_or_create(
                        plan=plan, role=role, defaults={"cell": dataset.active_cell}
                    )
            assignment_path = directory / "assignments.jsonl"
            (work / "assignments.jsonl").replace(assignment_path)
            report["assignments_sha256"] = store.file_sha256(assignment_path)
            report["fingerprint"] = plan.source_fingerprint
            report["progress"] = {
                "stage": "completed",
                "completed": plan.source_cell.rows,
                "total": plan.source_cell.rows,
                "updated_at": timezone.now().isoformat(),
            }
        DataPartitionPlan.objects.filter(pk=plan.pk).update(
            state="completed", report=report, error="", updated_at=timezone.now()
        )
    except Exception as exc:
        DataPartitionPlan.objects.filter(pk=plan.pk).update(
            state="failed", error=str(exc)[:1000], updated_at=timezone.now()
        )


def describe(plan):
    return {
        "id": str(plan.pk),
        "project": str(plan.project_id),
        "name": plan.name,
        "source_cell": str(plan.source_cell_id),
        "recipe": plan.recipe,
        "report": plan.report,
        "state": plan.state,
        "error": plan.error,
        "members": [
            {"role": member.role, "cell": use.describe(member.cell)}
            for member in plan.members.select_related("cell__dataset")
        ],
    }


def retry(plan):
    if not DataPartitionPlan.objects.filter(pk=plan.pk, state="failed").update(
        state="queued", error=""
    ):
        raise InputValidationError("Only failed partition construction can be retried")
