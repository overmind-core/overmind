import sys
from importlib import import_module

import django
from django.conf import settings
from django.db import connection, models
from django.http import HttpResponse
from django.urls import path

APP = "innermind"
sys.modules.setdefault(APP, sys.modules[__name__])


settings.configure(
    SECRET_KEY="thisisthesecretkey",
    DEBUG=True,
    INSTALLED_APPS=[
        "django.contrib.contenttypes",
        "rest_framework",
        APP,
    ],
    # The module itself, whether run as a script (__main__) or imported (innermind.innermind).
    ROOT_URLCONF=__name__,
    REST_FRAMEWORK={
        "DEFAULT_AUTHENTICATION_CLASSES": [],
        "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
        "UNAUTHENTICATED_USER": None,
    },
    MIDDLEWARE=[
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
    ],
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": "innermind.sqlite3",
        }
    },
    TIME_ZONE="UTC",
    USE_TZ=True,
    DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
)


django.setup()


class Trace(models.Model):
    version = models.PositiveIntegerField(default=1)
    attributes = models.JSONField(default=dict)

    class Meta:
        app_label = APP


class Span(models.Model):
    version = models.PositiveIntegerField(default=1)
    span_id = models.CharField(max_length=64, unique=True)
    parent_id = models.CharField(max_length=64, null=True, blank=True)
    trace_id = models.CharField(max_length=64)
    kind = models.CharField(max_length=32, default="span")
    name = models.CharField(max_length=255)
    start = models.CharField(max_length=64)
    status = models.CharField(max_length=16, default="pending")
    conversation_id = models.CharField(max_length=64, blank=True, default="")
    session_id = models.CharField(max_length=64, blank=True, default="")
    attributes = models.JSONField(default=dict)
    eval_attributes = models.JSONField(default=dict)
    processing_pipeline = models.JSONField(default=list)
    in_use = models.BooleanField(default=False)

    class Meta:
        app_label = APP


class DeterministicCheck(models.Model):
    name = models.CharField(max_length=255, unique=True)
    source = models.TextField()
    kind = models.CharField(max_length=32, blank=True, default="")
    span_name = models.CharField(max_length=255, blank=True, default="")
    enabled = models.BooleanField(default=True)

    class Meta:
        app_label = APP


def ensure_schema() -> None:
    existing = set(connection.introspection.table_names())
    with connection.schema_editor() as editor:
        for model in (Trace, Span, DeterministicCheck):
            if model._meta.db_table not in existing:
                editor.create_model(model)


def index(request):
    return HttpResponse("Hello World")


# Run as a script, innermind/ is on sys.path and api is a top-level module.
api = import_module(f"{__package__}.api" if __package__ else "api")

urlpatterns = [
    path("", index),
    path("v1/traces", api.otlp_traces),
]


if __name__ == "__main__":
    # ensure_schema()
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)

# if __name__ == "__main__":
#     print(Span.objects.all())
#     # asyncio.run(span_scoring_queue())
