from uuid import UUID

from overbae.models import Cell
from overbae.services.datasets.lifecycle import DatasetError


def resolve_cell(dataset, reference, *, ran_only=False):
    if not reference:
        cell = dataset.active_cell
    else:
        reference = str(reference).strip()
        versions = dataset.versions()
        cell_id = next((key for key, value in versions.items() if value == reference), None)
        if cell_id is None:
            try:
                cell_id = UUID(reference)
            except ValueError:
                cell_id = None
        cell = dataset.cells.filter(pk=cell_id).first() if cell_id else None
        if cell is None and reference.isdigit():
            cell = dataset.cells.filter(position=int(reference)).first()
    if cell is None:
        raise DatasetError("No readable version was found in this dataset.", code="no_cell")
    if ran_only and cell.state != Cell.State.OK:
        raise DatasetError("This version has not completed.", code="not_ran")
    return cell
