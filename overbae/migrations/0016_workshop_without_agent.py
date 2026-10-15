import hashlib
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.core.serializers.json import DjangoJSONEncoder
import json

from overbae.migration_compat import (
    RemoveFieldIfPresent,
    RemoveIndexIfPresent,
    RetireLegacyCellField,
)


def preserve_workshop(apps, schema_editor):
    alias = schema_editor.connection.alias
    if "overbae_datasetpipeline" in schema_editor.connection.introspection.table_names():
        return
    tables = [
        apps.get_model("overbae", name)._meta.db_table
        for name in ("Dataset", "Cell", "DatasetImport", "OptimizerExperiment")
    ]
    schema_editor.execute(
        "LOCK TABLE "
        + ", ".join(schema_editor.quote_name(table) for table in sorted(tables))
        + " IN ACCESS EXCLUSIVE MODE"
    )
    Dataset = apps.get_model("overbae", "Dataset")
    Cell = apps.get_model("overbae", "Cell")
    Import = apps.get_model("overbae", "DatasetImport")
    Experiment = apps.get_model("overbae", "OptimizerExperiment")
    for experiment in Experiment.objects.using(alias).exclude(cursor_usage={}).iterator():
        state = experiment.state
        if not isinstance(state, dict) or "archived_cursor_usage" in state:
            raise RuntimeError("Cannot preserve cursor usage without overwriting existing state.")
        Experiment.objects.using(alias).filter(pk=experiment.pk).update(
            state={**state, "archived_cursor_usage": experiment.cursor_usage}
        )
    for dataset in Dataset.objects.using(alias).iterator():
        spec = dict(dataset.source_spec)
        if "_migration_workshop_archive" in spec:
            raise RuntimeError("Workshop migration archive already exists.")
        spec["_migration_workshop_archive"] = json.loads(
            json.dumps(
                {
                    "dataset": {
                        name: getattr(dataset, name)
                        for name in (
                            "chat",
                            "agent_id",
                            "agent_messages",
                            "agent_turn_key",
                            "workshop_task_id",
                            "workshop_queued_at",
                            "workshop_started_at",
                        )
                    },
                    "cells": list(
                        Cell.objects.using(alias)
                        .filter(dataset_id=dataset.pk)
                        .values(
                            "id",
                            "state",
                            "error",
                            "review",
                            "quality_report",
                            "note",
                        )
                    ),
                    "imports": list(
                        Import.objects.using(alias)
                        .filter(dataset_id=dataset.pk)
                        .values(
                            "id",
                            "handoff_pending",
                            "handoff_owner",
                            "handoff_lease_until",
                            "handoff_attempts",
                        )
                    ),
                },
                cls=DjangoJSONEncoder,
            )
        )
        Dataset.objects.using(alias).filter(pk=dataset.pk).update(source_spec=spec)


def build_version_tree(apps, schema_editor):
    if "overbae_datasetpipeline" in schema_editor.connection.introspection.table_names():
        return
    alias = schema_editor.connection.alias
    Cell = apps.get_model("overbae", "Cell")
    Dataset = apps.get_model("overbae", "Dataset")
    for dataset in Dataset.objects.using(alias).iterator():
        previous = None
        for cell in Cell.objects.using(alias).filter(dataset_id=dataset.pk).order_by("position"):
            Cell.objects.using(alias).filter(pk=cell.pk).update(
                parent_id=previous,
                script_sha256=hashlib.sha256(cell.script.encode()).hexdigest() if previous else "",
            )
            previous = cell.pk
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def irreversible(apps, schema_editor):
    raise RuntimeError(
        "Workshop history has been archived. Restore a database backup to reverse this cutover."
    )


class Migration(migrations.Migration):
    dependencies = [
        ("django_celery_beat", "0001_initial"),
        ("overbae", "0015_workshop_execution_clock"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(preserve_workshop, irreversible),
        migrations.AddField(
            model_name="cell",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="children",
                to="overbae.cell",
            ),
        ),
        migrations.AddField(
            model_name="cell",
            name="script_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="cell",
            name="stdout",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="cell",
            name="impact",
            field=models.JSONField(blank=True, default=dict),
        ),
        RemoveFieldIfPresent(
            model_name="optimizerexperiment",
            name="cursor_usage",
        ),
        migrations.CreateModel(
            name="Execution",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("key", models.CharField(max_length=64)),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("try", "Try"),
                            ("probe", "Probe"),
                            ("cell", "Cell"),
                            ("synthetic", "Synthetic"),
                        ],
                        max_length=10,
                    ),
                ),
                ("title", models.CharField(blank=True, default="", max_length=120)),
                ("script", models.TextField(blank=True, default="")),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("ok", "Ok"),
                            ("failed", "Failed"),
                        ],
                        default="queued",
                        max_length=8,
                    ),
                ),
                ("transient", models.BooleanField(default=False)),
                ("error", models.TextField(blank=True, default="")),
                ("stdout", models.TextField(blank=True, default="")),
                ("result", models.JSONField(blank=True, default=dict)),
                ("queued_at", models.DateTimeField()),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("queue_seconds", models.FloatField(blank=True, null=True)),
                ("transfer_seconds", models.FloatField(blank=True, null=True)),
                ("run_seconds", models.FloatField(blank=True, null=True)),
                (
                    "cell",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="overbae.cell",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "dataset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="executions",
                        to="overbae.dataset",
                    ),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="overbae.cell",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="overbae.project",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["dataset", "-queued_at"], name="execution_dataset"),
                    models.Index(fields=["key", "-queued_at"], name="execution_key"),
                    models.Index(
                        condition=models.Q(state__in=["queued", "running"]),
                        fields=["project", "state"],
                        name="execution_active",
                    ),
                    models.Index(
                        condition=models.Q(state="failed", transient=True),
                        fields=["finished_at"],
                        name="execution_transient_failed",
                    ),
                ],
            },
        ),
        migrations.RunPython(build_version_tree, irreversible),
        RemoveIndexIfPresent(
            model_name="dataset",
            name="dataset_workshop_busy_idx",
        ),
        RemoveFieldIfPresent(model_name="dataset", name="chat"),
        RemoveFieldIfPresent(model_name="dataset", name="agent_id"),
        RemoveFieldIfPresent(model_name="dataset", name="agent_messages"),
        RemoveFieldIfPresent(model_name="dataset", name="agent_turn_key"),
        RemoveFieldIfPresent(model_name="dataset", name="workshop_task_id"),
        RemoveFieldIfPresent(model_name="dataset", name="workshop_queued_at"),
        RemoveFieldIfPresent(model_name="dataset", name="workshop_started_at"),
        migrations.AlterField(
            model_name="dataset",
            name="state",
            field=models.CharField(
                choices=[("landing", "Landing"), ("idle", "Idle"), ("error", "Error")],
                default="landing",
                max_length=12,
            ),
        ),
        RemoveIndexIfPresent(
            model_name="datasetimport",
            name="dataset_import_handoff",
        ),
        RemoveFieldIfPresent(model_name="datasetimport", name="handoff_pending"),
        RemoveFieldIfPresent(model_name="datasetimport", name="handoff_owner"),
        RemoveFieldIfPresent(model_name="datasetimport", name="handoff_lease_until"),
        RemoveFieldIfPresent(model_name="datasetimport", name="handoff_attempts"),
        RetireLegacyCellField(model_name="cell", name="state"),
        RetireLegacyCellField(model_name="cell", name="error"),
        RetireLegacyCellField(model_name="cell", name="review"),
        RetireLegacyCellField(model_name="cell", name="quality_report"),
        RetireLegacyCellField(model_name="cell", name="note"),
        migrations.AddConstraint(
            model_name="cell",
            constraint=models.UniqueConstraint(
                condition=models.Q(parent__isnull=False),
                fields=("parent", "script_sha256"),
                name="uniq_cell_parent_script",
            ),
        ),
    ]
