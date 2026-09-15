import logging
import logging.config
import os

from celery import Celery
from celery.signals import setup_logging, worker_shutdown

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "overbae.settings")

app = Celery("overbae")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(["overbae.tasks"])

logger = logging.getLogger(__name__)


@setup_logging.connect
def _configure_celery_logging(**_kwargs: object) -> None:
    """Use Django's LOGGING config: Celery's own formatters emit multi-line
    output that CloudWatch splits into separate log entries.
    """
    from django.conf import settings

    logging.config.dictConfig(settings.LOGGING)


@worker_shutdown.connect
def _close_cursor_sdk_bridge(**_kwargs: object) -> None:
    """Tear down the worker-global ``cursor-sdk-bridge`` subprocess.

    The SDK's own ``atexit`` cleanup only fires on a clean interpreter exit, so
    without this the bridge is orphaned across restarts. Best-effort.
    """
    from cursor_sdk import close_default_client

    try:
        close_default_client()
    except Exception:  # noqa: BLE001 — shutdown cleanup is best-effort
        logger.warning("cursor-sdk-bridge shutdown cleanup failed", exc_info=True)


def get_celery_app() -> Celery:
    return app
