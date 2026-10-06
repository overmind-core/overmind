import newrelic.agent
import sentry_sdk


def auth_kind(user, auth) -> str:
    if user is None:
        return "anonymous"
    if user.is_guest:
        return "guest"
    if hasattr(auth, "grant"):
        return "oauth"
    if hasattr(auth, "token_hash"):
        return f"api_key_{auth.scope['scope']}"
    if isinstance(auth, dict):
        return "clerk"
    return "session" if auth is None else "local"


def request_project_id(request) -> str | None:
    # Only DRF views set request.auth; unresolved and plain Django routes lack it.
    project_id = getattr(getattr(request, "auth", None), "project_id", None)
    if project_id is None and request.resolver_match is not None:
        project_id = request.resolver_match.kwargs.get("project_id")
    if project_id is None:
        project_id = request.GET.get("project_id") or request.GET.get("project")
    return str(project_id) if project_id else None


def authenticated(user):
    return user if user is not None and user.is_authenticated else None


def bind_context(*, user=None, auth=None, project_id=None, **tags) -> None:
    """Tag the current request or task in Sentry and New Relic."""
    user = authenticated(user)
    attributes = {"auth_kind": auth_kind(user, auth), **tags}
    if project_id is not None:
        attributes["project_id"] = str(project_id)
    if user is not None:
        attributes["user_id"] = user.pk
        sentry_sdk.set_user({"id": str(user.pk), "clerk_user_id": user.clerk_user_id})
    for key, value in attributes.items():
        sentry_sdk.set_tag(key, value)
    newrelic.agent.add_custom_attributes(attributes.items())
