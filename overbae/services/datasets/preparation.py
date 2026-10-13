from uuid import UUID

from overbae.models import (
    Cell,
    DataExploration,
    DataPartitionMember,
    DataPartitionPlan,
    DatasetPipelineRun,
)
from overbae.services.datasets.lifecycle import DatasetError


def describe(dataset, *, cell=None):
    selected = cell or dataset.active_cell
    nodes, edges, visited_plans, visited_runs = {}, {}, set(), set()

    def visit(current, role=""):
        identity = str(current.pk)
        if identity in nodes:
            if role:
                nodes[identity]["role"] = role
            return
        if len(nodes) >= 200:
            raise DatasetError(
                "This preparation exceeds 200 cells. Inspect its source datasets separately.",
                code="preparation_limit",
            )
        nodes[identity] = {
            "cell": current,
            "dataset": str(current.dataset_id),
            "dataset_name": current.dataset.name,
            "intent": current.dataset.intent,
            "role": role,
        }
        member = (
            DataPartitionMember.objects.filter(
                cell=current,
                plan__project_id=dataset.project_id,
                plan__state="completed",
                plan__source_cell__dataset__project_id=dataset.project_id,
            )
            .select_related("plan__source_cell__dataset")
            .first()
        )
        if member and member.plan.source_fingerprint == member.plan.source_cell.fingerprint:
            nodes[identity]["role"] = member.role
            parent = member.plan.source_cell
            visit(parent)
            edges[(str(parent.pk), identity)] = {
                "source": str(parent.pk),
                "target": identity,
                "label": member.role,
            }
            add_members(member.plan)
            return
        derivation = (
            DataExploration.objects.filter(
                output_dataset_id=current.dataset_id,
                project_id=dataset.project_id,
                state="completed",
                kind="derive",
                report__output__id=identity,
                report__output__fingerprint=current.fingerprint,
                source_cell__dataset__project_id=dataset.project_id,
            )
            .select_related("source_cell__dataset")
            .first()
        )
        if derivation and derivation.source_fingerprint == derivation.source_cell.fingerprint:
            parent = derivation.source_cell
            visit(parent)
            edges[(str(parent.pk), identity)] = {
                "source": str(parent.pk),
                "target": identity,
                "label": "Derived source",
            }
            return
        if (current.review or {}).get("kind") == "attachment" and current.input_fingerprint:
            parent = (
                Cell.objects.filter(
                    dataset_id=current.dataset_id,
                    state="ok",
                    position__lt=current.position,
                    fingerprint=current.input_fingerprint,
                )
                .select_related("dataset")
                .order_by("-position")
                .first()
            )
            if parent:
                visit(parent)
                edges[(str(parent.pk), identity)] = {
                    "source": str(parent.pk),
                    "target": identity,
                    "label": "Added sources",
                }
            return
        try:
            run_id = UUID(str((current.review or {}).get("run")))
        except ValueError:
            return
        run = DatasetPipelineRun.objects.filter(
            pk=run_id,
            dataset_id=current.dataset_id,
            state="completed",
            mode="publish",
        ).first()
        if run is None:
            return
        step = next(
            (
                step
                for step in run.result.get("steps", [])
                if step.get("output_cell") == identity
                and step.get("output_fingerprint") == current.fingerprint
            ),
            None,
        )
        if step is None:
            return
        for entry in step.get("inputs", []):
            parent = (
                Cell.objects.filter(
                    pk=entry["cell"],
                    fingerprint=entry["fingerprint"],
                    state="ok",
                    dataset__project_id=dataset.project_id,
                )
                .select_related("dataset")
                .first()
            )
            if parent:
                visit(parent)
                edges[(str(parent.pk), identity)] = {
                    "source": str(parent.pk),
                    "target": identity,
                    "label": step.get("condition", ""),
                }
        if current.pk == run.output_id and run.pk not in visited_runs:
            visited_runs.add(run.pk)
            fingerprints = {
                entry["output_cell"]: entry["output_fingerprint"]
                for entry in run.result.get("steps", [])
                if entry.get("output_cell") and entry.get("output_fingerprint")
            }
            for sibling in (
                Cell.objects.filter(dataset_id=current.dataset_id, pk__in=fingerprints, state="ok")
                .select_related("dataset")
                .order_by("position")
            ):
                if sibling.fingerprint == fingerprints[str(sibling.pk)]:
                    visit(sibling)

    def add_members(plan):
        if plan.pk in visited_plans:
            return
        visited_plans.add(plan.pk)
        for member in (
            plan.members.filter(cell__dataset__project_id=dataset.project_id)
            .select_related("cell__dataset")
            .order_by("pk")
        ):
            visit(member.cell, member.role)

    if selected:
        visit(selected)
        if not visited_plans:
            plan = (
                DataPartitionPlan.objects.filter(
                    project_id=dataset.project_id,
                    source_cell=selected,
                    source_fingerprint=selected.fingerprint,
                    state="completed",
                )
                .order_by("-created_at", "-pk")
                .first()
            )
            if plan:
                add_members(plan)
    return {
        "selected": str(selected.pk) if selected else None,
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
    }


def record(process):
    return {
        **process,
        "nodes": [
            {
                **node,
                "cell": str(node["cell"].pk),
                "title": node["cell"].title,
                "rows": node["cell"].rows,
                "fingerprint": node["cell"].fingerprint,
            }
            for node in process["nodes"]
        ],
    }


def input_cells(cell):
    process = describe(cell.dataset, cell=cell)
    parents = {edge["source"] for edge in process["edges"] if edge["target"] == str(cell.pk)}
    return [node["cell"] for node in process["nodes"] if str(node["cell"].pk) in parents]
