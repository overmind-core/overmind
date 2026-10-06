import logging
from enum import StrEnum
from functools import cache
from uuid import UUID

from django.conf import settings
from django.db import transaction
from posthog import Posthog

from overbae.core.telemetry.context import authenticated

logger = logging.getLogger(__name__)

SCALARS = (str, int, float, bool, type(None))


class Event(StrEnum):
    API_REQUEST = "api request"
    TASK_FINISHED = "task finished"
    PROJECT_CREATED = "project created"
    GUEST_CLAIMED = "guest claimed"
    SNAPSHOT_SYNCED = "snapshot synced"
    TRACES_INGESTED = "traces ingested"
    DATASET_CREATED = "dataset created"
    DATASET_IMPORT_FINISHED = "dataset import finished"
    WORKSHOP_TURN_STARTED = "workshop turn started"
    WORKSHOP_TURN_FINISHED = "workshop turn finished"
    WORKSHOP_RUN_STARTED = "workshop run started"
    WORKSHOP_RUN_FINISHED = "workshop run finished"
    EVALUATION_STARTED = "evaluation started"
    EVALUATION_FINISHED = "evaluation finished"
    TRAINING_JOB_STARTED = "training job started"
    TRAINING_JOB_FINISHED = "training job finished"
    DEPLOYMENT_FINISHED = "deployment finished"
    MODEL_ACTIVATION_STARTED = "model activation started"
    MODEL_ACTIVATION_FINISHED = "model activation finished"
    OPTIMIZER_STARTED = "optimizer started"
    OPTIMIZER_FINISHED = "optimizer finished"
    CONNECTOR_SYNC_FINISHED = "connector sync finished"
    CREDITS_PURCHASED = "credits purchased"
    PLAN_CHANGED = "plan changed"
    API_KEY_CREATED = "api key created"
    API_KEY_REVOKED = "api key revoked"


@cache
def get_client() -> Posthog | None:
    # The SDK re-creates its consumer after fork and flushes at exit.
    if not settings.POSTHOG_PROJECT_TOKEN:
        return None
    return Posthog(
        settings.POSTHOG_PROJECT_TOKEN,
        host=settings.POSTHOG_HOST,
        super_properties={
            "environment": settings.SENTRY_ENVIRONMENT,
            "release": settings.OVERMIND_RELEASE,
        },
    )


def capture(event: Event, project_id, *, user=None, **properties) -> None:
    """Send one event. Properties are scalar metadata (ids, counts, states), never payloads."""
    properties = {
        key: str(value) if isinstance(value, UUID) else value
        for key, value in {"project_id": project_id, **properties}.items()
    }
    if not all(isinstance(value, SCALARS) for value in properties.values()):
        if settings.TESTING:
            raise TypeError(f"{event} properties must be scalars: {properties}")
        logger.warning("dropped %s: non-scalar properties", event)
        return
    client = get_client()
    if client is None:
        return
    user = authenticated(user)
    if user is None:
        # Server-side actors attribute to the project without minting a person.
        properties["$process_person_profile"] = False
    # Inside an atomic block this waits for commit, so a rolled-back transition is never reported.
    transaction.on_commit(
        lambda: client.capture(
            str(event),
            distinct_id=(user.clerk_user_id or f"user:{user.pk}")
            if user
            else f"project:{project_id}",
            properties=properties,
            groups={"project": properties["project_id"]} if project_id else None,
        )
    )
