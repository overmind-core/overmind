import time

from django.conf import settings

from overbae.core.telemetry.analytics import Event, capture
from overbae.core.telemetry.context import (
    auth_kind,
    authenticated,
    bind_context,
    request_project_id,
)


class TelemetryMiddleware:
    """One `api request` event per Django response, tied to its user and project."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.skipped = ("/health", "/static/", f"/{settings.ADMIN_URL_PATH}")

    def __call__(self, request):
        started = time.monotonic()
        response = self.get_response(request)
        if request.path.startswith(self.skipped):
            return response

        # DRF authenticates inside the view and mirrors user/auth onto this request.
        user = authenticated(getattr(request, "user", None))
        auth = getattr(request, "auth", None)
        project_id = request_project_id(request)
        bind_context(user=user, auth=auth, project_id=project_id)
        match = request.resolver_match
        capture(
            Event.API_REQUEST,
            project_id,
            user=user,
            method=request.method,
            # DRF router routes are regexes: "api/^projects/$".
            route="/" + match.route.replace("^", "").removesuffix("$") if match else None,
            view=match.view_name if match else None,
            status=response.status_code,
            duration_ms=round((time.monotonic() - started) * 1000),
            auth_kind=auth_kind(user, auth),
        )
        return response
