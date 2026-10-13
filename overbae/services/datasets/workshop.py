"""What a caller's agent can do to a dataset. Overmind runs what it is given and measures
the result; it never writes or chooses a script."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from overbae.models import Capability, Cell, Dataset, Execution
from overbae.services.datasets import diff, executions, lifecycle, paths, synthetic, versions
from overbae.services.datasets.lifecycle import DatasetError

UNSET: Any = object()


@dataclass
class Run:
    execution: Execution | None
    cell: Cell | None

    @property
    def status(self) -> str:
        if self.execution is None:
            return Execution.State.OK
        return self.execution.state


def _ready(dataset: Dataset) -> None:
    if dataset.state == Dataset.State.LANDING:
        raise DatasetError("The source is still landing.", code="landing")
    if dataset.source is None:
        raise DatasetError("The dataset has no source.", code="no_source")


def _run(dataset: Dataset, *, mode: str, parent: Cell, script: str, title: str, user) -> Run:
    submitted = executions.submit(
        dataset, mode=mode, parent=parent, script=script, title=title, user=user
    )
    if submitted.run is None:
        return Run(None, submitted.cell)
    run = executions.wait(submitted.run)
    return Run(run, run.cell if run.cell_id else None)


def try_script(dataset: Dataset, parent: Any, script: str, *, user=None) -> Run:
    _ready(dataset)
    return _run(
        dataset,
        mode=Execution.Mode.TRY,
        parent=versions.resolve_cell(dataset, parent, field="parent"),
        script=script,
        title="",
        user=user,
    )


def probe(dataset: Dataset, version: Any, script: str, *, user=None) -> Run:
    _ready(dataset)
    return _run(
        dataset,
        mode=Execution.Mode.PROBE,
        parent=versions.resolve_cell(dataset, version),
        script=script,
        title="",
        user=user,
    )


def add_version(dataset: Dataset, parent: Any, title: str, script: str, *, user=None) -> Run:
    _ready(dataset)
    return _run(
        dataset,
        mode=Execution.Mode.CELL,
        parent=versions.resolve_cell(dataset, parent, field="parent"),
        script=script,
        title=title,
        user=user,
    )


def add_synthetic_examples(
    dataset: Dataset, parent: Any, rows: Any, instruction: str, *, user=None
) -> Run:
    _ready(dataset)
    cell = versions.resolve_cell(dataset, parent, field="parent")
    text = synthetic.check(dataset, cell, rows, instruction)
    return _run(
        dataset,
        mode=Execution.Mode.SYNTHETIC,
        parent=cell,
        script=text,
        title=" ".join(instruction.split())[:120],
        user=user,
    )


def diff_versions(dataset: Dataset, start: Any, end: Any) -> dict[str, Any]:
    _ready(dataset)
    after = versions.resolve_cell(dataset, end, field="to")
    if start:
        before = versions.resolve_cell(dataset, start, field="from")
    elif after.parent_id is None:
        raise DatasetError(f"{after.label} is the source; name a version to compare.", code="diff")
    else:
        before = after.parent
    result = diff.between(
        paths.cell_path(dataset.pk, before.pk), paths.cell_path(dataset.pk, after.pk)
    )
    for key in ("changed_examples", "removed_examples"):
        if key in result:
            result[key] = executions.visible(result[key])
    return {"from": before.label, "to": after.label, **result}


def activate_version(dataset: Dataset, version: Any, *, expected_active: Any = None) -> Dataset:
    cell = versions.resolve_cell(dataset, version)
    expected = (
        versions.resolve_cell(dataset, expected_active, field="expected_active")
        if expected_active
        else None
    )
    return versions.activate(dataset, cell, expected_active=expected)


def update_dataset(
    dataset: Dataset,
    *,
    name: Any = UNSET,
    intent: Any = UNSET,
    capability: Capability | None | Any = UNSET,
) -> Dataset:
    if name is not UNSET and name is not None:
        lifecycle.rename(dataset, name)
    if intent is not UNSET and intent is not None:
        lifecycle.set_intent(dataset, intent)
    if capability is not UNSET:
        lifecycle.set_capability(dataset, capability)
    dataset.refresh_from_db()
    return dataset


def status(dataset: Dataset, cell_offset: int = 0, cell_limit: int = 50) -> dict[str, Any]:
    cells = dataset.cells.select_related("parent").order_by("position")
    return {
        "versions": list(cells[cell_offset : cell_offset + cell_limit]),
        "total": cells.count(),
        "active": dataset.cells.filter(pk=dataset.active_id).first() if dataset.active_id else None,
        "running": dataset.executions.filter(
            state__in=[Execution.State.QUEUED, Execution.State.RUNNING]
        ).count(),
    }
