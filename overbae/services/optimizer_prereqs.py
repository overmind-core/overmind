"""Optimizer launch prerequisites — the readiness report behind MCP."""

from __future__ import annotations

from overbae.models import Capability, Dataset
from overbae.models.optimizer import optimizer_dataset_error


def optimizer_prerequisite_report(project, capability: Capability) -> dict:
    eval_set = capability.active_eval_set

    eval_qs = Dataset.objects.filter(
        project=project, capability=capability, intent=Dataset.Intent.EVAL
    ).order_by("-updated_at")[:10]
    eval_datasets = []
    for row in eval_qs:
        product = row.active_cell
        eval_datasets.append(
            {
                "id": str(row.id),
                "name": row.name or str(row.id)[:8],
                "rows": str(product.rows if product is not None else 0),
                "usable": optimizer_dataset_error(capability, row) is None,
            }
        )
    has_usable_dataset = any(row["usable"] for row in eval_datasets)

    missing: list[str] = []
    if not has_usable_dataset:
        missing.append(
            "eval dataset — pass dataset_name to create_optimizer_experiment "
            "(needs a frozen version whose contract is eval; use list_datasets)"
        )
    if eval_set is None:
        missing.append(
            "eval set — set the capability's active eval set or pass eval_set_name (use list_eval_sets)"
        )

    return {
        "capability": capability.slug,
        "ready": not missing,
        "missing": missing,
        "eval_set": eval_set.name if eval_set else None,
        "eval_datasets": eval_datasets or None,
        "drive_command": "overmind optimise start -c "
        f"{capability.slug} -d <dataset-id> && overmind optimise next",
    }
