import logging
import logging.config
import os

from celery import Celery
from celery.signals import setup_logging

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "overbae.settings")

app = Celery("overbae")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(["overbae.tasks"])


@setup_logging.connect
def _configure_celery_logging(**_kwargs: object) -> None:
    """Use Django's LOGGING config: Celery's own formatters emit multi-line
    output that CloudWatch splits into separate log entries.
    """
    from django.conf import settings

    logging.config.dictConfig(settings.LOGGING)


def get_celery_app() -> Celery:
    return app
