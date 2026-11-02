from types import SimpleNamespace

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from overbae.api.eval_serializers import EvalMatrixSerializer
from overbae.models import EvalMatrix, EvalMatrixRevision
from overbae.services.mcp.contracts.evaluations import ROW_SOURCE_MAX_CHARS
from overbae.services.mcp.errors import MCPError, serializer_fields


@transaction.atomic
def revise(project, user, payload):
    row = EvalMatrix.objects.select_for_update().filter(project=project, pk=payload.row).first()
    if row is None:
        raise MCPError("resource_not_found", "The evaluation row was not found in this project.")
    if payload.expected_updated_at != row.updated_at:
        raise MCPError(
            "revision_conflict",
            "The evaluator changed. Read its resource and review the current definition before revising.",
        )
    current = {**row.snapshot(), "enabled": row.enabled}
    definition = {
        **current,
        **payload.model_dump(exclude={"row", "expected_updated_at"}, exclude_none=True),
    }
    if len(definition["source"]) > ROW_SOURCE_MAX_CHARS:
        raise MCPError(
            "eval_matrix_row_invalid",
            "The revision failed validation.",
            fields={"source": f"A source is at most {ROW_SOURCE_MAX_CHARS} characters."},
        )
    serializer = EvalMatrixSerializer(
        instance=row,
        data={
            "project": str(project.pk),
            "capability": str(row.capability_id) if row.capability_id else None,
            **definition,
        },
        context={"request": SimpleNamespace(user=user)},
    )
    try:
        serializer.is_valid(raise_exception=True)
    except ValidationError as error:
        raise MCPError(
            "eval_matrix_row_invalid",
            "The revision failed validation.",
            fields=serializer_fields(error.detail),
        ) from None
    if all(serializer.validated_data[field] == value for field, value in current.items()):
        raise MCPError("invalid_input", "The revision changes nothing.")
    EvalMatrixRevision.objects.create(
        row=row,
        definition={**current, "updated_at": row.updated_at.isoformat()},
        created_by=user,
    )
    try:
        return serializer.save()
    except IntegrityError:
        raise MCPError(
            "eval_matrix_row_invalid",
            "The revision failed validation.",
            fields={"name": "Another evaluator in this scope has this name."},
        ) from None
