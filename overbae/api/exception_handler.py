"""Global DRF exception handler.

Every failure must reach the frontend as JSON with a ``detail`` key — a
non-``APIException`` would otherwise escape to Django's HTML 500 page, and DRF
renders a string-arg ``ValidationError`` as a bare array. ``IntegrityError``
(slug/name races the serializer validators cannot see) becomes 409, not 500.
"""

import logging
import uuid

from django.conf import settings
from django.db import IntegrityError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        if isinstance(response.data, list):
            flat = " ".join(str(item) for item in response.data if item)
            response.data = {"detail": flat or "Invalid request."}
        elif isinstance(response.data, dict) and "code" not in response.data:
            # The client distinguishes e.g. plan_limit_exceeded from
            # permission_denied by this code, not by the shared 403.
            code = getattr(response.data.get("detail"), "code", None)
            if code:
                response.data["code"] = code
        return response

    view = context.get("view")
    view_name = type(view).__name__ if view is not None else "unknown view"

    if isinstance(exc, IntegrityError):
        logger.warning("IntegrityError in %s: %s", view_name, exc)
        return Response(
            {"detail": "This conflicts with an existing record.", "code": "conflict"},
            status=status.HTTP_409_CONFLICT,
        )

    error_id = uuid.uuid4().hex[:12]
    logger.exception("Unhandled API error [%s] in %s", error_id, view_name)
    data = {
        "detail": f"The server hit an unexpected error (ref {error_id}).",
        "code": "internal_error",
        "error_id": error_id,
    }
    if settings.DEBUG:
        data["debug"] = f"{type(exc).__name__}: {exc}"
    return Response(data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
