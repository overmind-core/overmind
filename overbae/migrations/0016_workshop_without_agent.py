import hashlib
import uuid
from collections import Counter
from pathlib import Path

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models, transaction

IMPACT_KEYS = (
    "rows_before",
    "rows_after",
    "rows_removed",
    "rows_added",
    "identity_preserved",
    "input_evidence_removed",
    "input_evidence_removed_examples",
    "instruction_changes",
    "instruction_change_examples",
    "coverage_before",
    "coverage_after",
    "removed_examples",
    "input_examples",
    "output_examples",
)
CONSUMERS = (
    ("FinetuningJob", ("cell", "validation_cell", "eval_cell")),
    ("EvalRun", ("cell",)),
    ("OptimizerExperiment", ("cell",)),
    ("TrainingPreparation", ("cell", "validation_cell")),
)


def _cell_file(dataset_id, cell_id) -> Path:
    return Path(settings.MEDIA_ROOT) / "datasets" / str(dataset_id) / "cells" / f"{cell_id}.parquet"


def _orphans(cells) -> set:
    """Cells the version tree cannot hold: not run, run on a frame that is no longer the
    previous cell's (an old chain kept ``ok`` cells after an edit upstream), or without
    their file. Every cell after one of these was computed from it, or from older data."""
    removed = set()
    kept = None
    for cell in cells:
        current = (
            cell.state == "ok"
            and (kept is None or cell.input_fingerprint == kept.fingerprint)
            and _cell_file(cell.dataset_id, cell.pk).exists()
        )
        if current and (kept is not None or not removed):
            kept = cell
        else:
            removed.add(cell.pk)
    return removed


def build_version_tree(apps, schema_editor):
    alias = schema_editor.connection.alias
    Cell = apps.get_model("overbae", "Cell")
    Dataset = apps.get_model("overbae", "Dataset")

    chains = {}
    for cell in Cell.objects.using(alias).order_by("dataset_id", "position"):
        chains.setdefault(cell.dataset_id, []).append(cell)
    if chains and not any(
        _cell_file(cell.dataset_id, cell.pk).exists() for chain in chains.values() for cell in chain
    ):
        raise RuntimeError(
            f"No cell file exists under {settings.MEDIA_ROOT}. Run the migration where the "
            "dataset volume is mounted."
        )
    stale_ids = set()
    for chain in chains.values():
        stale_ids |= _orphans(chain)
    stale = Cell.objects.using(alias).filter(pk__in=stale_ids)
    referenced = set()
    for model_name, fields in CONSUMERS:
        model = apps.get_model("overbae", model_name)
        for field in fields:
            referenced |= set(
                model.objects.using(alias)
                .filter(**{f"{field}_id__in": stale_ids})
                .values_list(f"{field}_id", flat=True)
            )
    if referenced:
        raise RuntimeError(
            "These cells are not run, are out of date or have no file, and training, "
            "evaluation or the optimizer uses them; resolve them before migrating: "
            + ", ".join(sorted(str(pk) for pk in referenced))
        )

    removed = Counter()
    files = []
    for cell_id, dataset_id in stale.values_list("pk", "dataset_id"):
        files.append(_cell_file(dataset_id, cell_id))
        removed[dataset_id] += 1
    stale.delete()
    for dataset_id, count in sorted(removed.items(), key=lambda item: str(item[0])):
        print(f"  dataset {dataset_id}: deleted {count} cells outside the version tree")

    def delete_files():
        for path in files:
            try:
                path.unlink()
            except FileNotFoundError:
                print(f"  missing {path}")
            except OSError as exc:
                print(f"  could not delete {path}: {exc}")

    transaction.on_commit(delete_files, using=alias)

    for dataset in Dataset.objects.using(alias).iterator():
        cells = list(Cell.objects.using(alias).filter(dataset_id=dataset.pk).order_by("position"))
        previous = None
        for cell in cells:
            cell.parent_id = previous.pk if previous else None
            cell.script_sha256 = (
                hashlib.sha256(cell.script.encode("utf-8")).hexdigest() if previous else ""
            )
            review = cell.review or {}
            cell.impact = (
                {key: review[key] for key in IMPACT_KEYS if key in review}
                if previous and "rows_before" in review
                else {}
            )
            previous = cell
        Cell.objects.using(alias).bulk_update(cells, ["parent", "script_sha256", "impact"])
        updates = {}
        if cells and dataset.active_id not in {cell.pk for cell in cells}:
            updates["active_id"] = cells[-1].pk
        if dataset.state in ("diagnosing", "running") or (dataset.state == "error" and cells):
            updates.update(state="idle", error="")
        if updates:
            Dataset.objects.using(alias).filter(pk=dataset.pk).update(**updates)

    periodic_task = apps.get_model("django_celery_beat", "PeriodicTask")
    periodic_task.objects.using(alias).filter(task="overbae.tasks.datasets.reap_stuck_runs").update(
        enabled=False
    )
    # The schema changes below alter tables whose deferred foreign-key checks this
    # function queued; Postgres refuses ALTER TABLE while those events are pending.
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    schema_editor.execute("SET CONSTRAINTS ALL DEFERRED")


def irreversible(apps, schema_editor):
    raise RuntimeError(
        "0016_workshop_without_agent deleted cells outside the version tree, chats and "
        "proposals. "
        "Restore a database backup to go back."
    )


class Migration(migrations.Migration):
    dependencies = [
        ("django_celery_beat", "0001_initial"),
        ("overbae", "0015_workshop_execution_clock"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
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
        migrations.RemoveField(
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
        migrations.RemoveIndex(
            model_name="dataset",
            name="dataset_workshop_busy_idx",
        ),
        migrations.RemoveField(model_name="dataset", name="chat"),
        migrations.RemoveField(model_name="dataset", name="agent_id"),
        migrations.RemoveField(model_name="dataset", name="agent_messages"),
        migrations.RemoveField(model_name="dataset", name="agent_turn_key"),
        migrations.RemoveField(model_name="dataset", name="workshop_task_id"),
        migrations.RemoveField(model_name="dataset", name="workshop_queued_at"),
        migrations.RemoveField(model_name="dataset", name="workshop_started_at"),
        migrations.AlterField(
            model_name="dataset",
            name="state",
            field=models.CharField(
                choices=[("landing", "Landing"), ("idle", "Idle"), ("error", "Error")],
                default="landing",
                max_length=12,
            ),
        ),
        migrations.RemoveIndex(
            model_name="datasetimport",
            name="dataset_import_handoff",
        ),
        migrations.RemoveField(model_name="datasetimport", name="handoff_pending"),
        migrations.RemoveField(model_name="datasetimport", name="handoff_owner"),
        migrations.RemoveField(model_name="datasetimport", name="handoff_lease_until"),
        migrations.RemoveField(model_name="datasetimport", name="handoff_attempts"),
        migrations.RemoveField(model_name="cell", name="state"),
        migrations.RemoveField(model_name="cell", name="error"),
        migrations.RemoveField(model_name="cell", name="review"),
        migrations.RemoveField(model_name="cell", name="quality_report"),
        migrations.RemoveField(model_name="cell", name="note"),
        migrations.AddConstraint(
            model_name="cell",
            constraint=models.UniqueConstraint(
                condition=models.Q(parent__isnull=False),
                fields=("parent", "script_sha256"),
                name="uniq_cell_parent_script",
            ),
        ),
    ]
