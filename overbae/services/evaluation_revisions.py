from types import SimpleNamespace

from django.db import transaction
from rest_framework.exceptions import ValidationError

from overbae.api.eval_serializers import EvalMatrixSerializer
from overbae.models import EvalMatrix, EvalMatrixRevision
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
    # Scope and set membership are stable; a revision changes the definition only.
    if payload.capability is not None and payload.capability != str(row.capability_id):
        raise MCPError(
            "invalid_input", "A revision cannot move an evaluator to another capability."
        )
    serializer = EvalMatrixSerializer(
        instance=row,
        data={
            "project": str(project.pk),
            "capability": str(row.capability_id) if row.capability_id else None,
            "name": payload.name,
            "type": payload.type,
            "source": payload.source,
            "span_type": payload.span_type,
            "span_name": payload.span_name,
            "enabled": payload.enabled,
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
    EvalMatrixRevision.objects.create(
        row=row,
        definition={
            **row.snapshot(),
            "enabled": row.enabled,
            "updated_at": row.updated_at.isoformat(),
        },
        created_by=user,
    )
    return serializer.save()
