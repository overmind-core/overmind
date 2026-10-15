"""Validate a historical upgrade using an isolated, disposable local PostgreSQL database.

Run from the repository root: PYTHONPATH=. uv run python scripts/validate_migration_upgrade.py REF
Use `fresh` for an empty install. Requires the compose PostgreSQL service.
"""

import os
import pathlib
import subprocess
import sys
import tempfile
import uuid

import django
from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from psycopg2 import connect, sql

os.environ["DJANGO_SETTINGS_MODULE"] = "tests.settings"
django.setup()

if len(sys.argv) != 2:
    raise SystemExit("Supply a git baseline ref or fresh.")
ref = sys.argv[1]
name = "migration_check_" + uuid.uuid4().hex[:12]
settings.DATABASES["default"]["NAME"] = name
database = settings.DATABASES["default"]
if database["HOST"] not in {"localhost", "127.0.0.1", "::1"}:
    raise SystemExit("Upgrade validation only creates disposable databases on loopback.")
conn = connect(
    dbname="postgres",
    user=database["USER"],
    password=database["PASSWORD"],
    host=database["HOST"],
    port=database["PORT"],
)
conn.autocommit = True
with conn.cursor() as c:
    c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
print("Disposable database", name, "baseline", ref, flush=True)
media = tempfile.TemporaryDirectory(prefix="migration-files-")
settings.MEDIA_ROOT = media.name
try:
    if ref != "fresh":
        with tempfile.TemporaryDirectory(prefix="migration-baseline-") as tmp:
            pkg = pathlib.Path(tmp) / "baseline_migrations"
            pkg.mkdir()
            paths = subprocess.check_output(
                ["git", "ls-tree", "-r", "--name-only", ref, "overbae/migrations"], text=True
            ).splitlines()
            for path in paths:
                if path.endswith(".py"):
                    (pkg / pathlib.Path(path).name).write_bytes(
                        subprocess.check_output(["git", "show", f"{ref}:{path}"])
                    )
            sys.path.insert(0, tmp)
            settings.MIGRATION_MODULES = {"overbae": "baseline_migrations"}
            call_command("migrate", verbosity=0, interactive=False)
            print("Baseline migrated", flush=True)
            state = MigrationExecutor(connections["default"]).loader.project_state()
            historical = state.apps
            Project = historical.get_model("overbae", "Project")
            Dataset = historical.get_model("overbae", "Dataset")
            Cell = historical.get_model("overbae", "Cell")
            project = Project.objects.create(name="Migration canary", slug="migration-canary")
            dataset = Dataset.objects.create(
                project_id=project.pk, name="Retained dataset", state="idle"
            )
            first = Cell.objects.create(
                dataset_id=dataset.pk, position=0, title="Original", fingerprint="a" * 64
            )
            second = Cell.objects.create(
                dataset_id=dataset.pk,
                position=1,
                title="Prepared",
                fingerprint="b" * 64,
                script="df = df",
            )
            Dataset.objects.filter(pk=dataset.pk).update(active_id=second.pk)
            dataset_id, cell_ids = str(dataset.pk), [str(first.pk), str(second.pk)]
            is_main = ("overbae", "execution") in state.models
            if is_main:
                Cell.objects.filter(pk=second.pk).update(
                    parent_id=first.pk,
                    attachment={"sha256": "c" * 64, "filename": "retained.csv"},
                    analytics={"rows": 2},
                    impact={"rows_before": 2, "rows_after": 2},
                    stdout="measured output",
                    script_sha256="d" * 64,
                )
                Execution = historical.get_model("overbae", "Execution")
                execution = Execution.objects.create(
                    project_id=project.pk,
                    dataset_id=dataset.pk,
                    parent_id=first.pk,
                    cell_id=second.pk,
                    state="ok",
                    script="df = df",
                    key="canary",
                    mode="cell",
                    queued_at=timezone.now(),
                )
                DecisionJob = historical.get_model("overbae", "DecisionJob")
                decision = DecisionJob.objects.create(
                    project_id=project.pk,
                    cell_id=second.pk,
                    eval_cell_id=first.pk,
                    model_id="legacy-model",
                    status="succeeded",
                    result={"saved": "/weights/retained/checkpoint"},
                    evaluation={"status": "complete", "score": 0.75},
                    cost_usd="1.25",
                )
                Request = historical.get_model("overbae", "MCPRequestKey")
                request = Request.objects.create(
                    project_id=project.pk,
                    tool="create_dataset",
                    key="canary",
                    payload_sha256="e" * 64,
                    result={"dataset": dataset_id},
                )
                empty_project = Project.objects.create(name="No dataset", slug="no-dataset")
                Request.objects.create(
                    project_id=empty_project.pk,
                    tool="read_receipt",
                    key="canary",
                    payload_sha256="e" * 64,
                    result={"retained": True},
                )
            else:
                Cell.objects.filter(pk=first.pk).update(
                    state="ok", note="preserved", review={"retained": True}
                )
                Cell.objects.filter(pk=second.pk).update(
                    state="failed", error="original failure", quality_report={"count": 9}
                )

            from overbae.services.datasets.paths import cell_path

            retained_files = {}
            for cell_id in cell_ids:
                path = cell_path(dataset_id, cell_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                content = ("retained cell " + cell_id).encode()
                path.write_bytes(content)
                retained_files[path] = content
            settings.MIGRATION_MODULES = {}
    if ref != "fresh" and is_main:
        for model, row, field, active, terminal, message in [
            (Execution, execution, "state", "running", "ok", "Workshop executions"),
            (DecisionJob, decision, "status", "running", "succeeded", "decision jobs"),
        ]:
            model.objects.filter(pk=row.pk).update(**{field: active})
            try:
                call_command("migrate", verbosity=0, interactive=False)
            except RuntimeError as exc:
                assert message in str(exc), str(exc)
            else:
                raise AssertionError("Active legacy work must prevent cutover")
            assert model.objects.filter(pk=row.pk, **{field: active}).exists()
            assert all(path.read_bytes() == content for path, content in retained_files.items())
            model.objects.filter(pk=row.pk).update(**{field: terminal})
        print("Active execution and decision guards preserved records and files", flush=True)
    call_command("migrate", verbosity=1, interactive=False)
    ex = MigrationExecutor(connections["default"])
    assert not ex.migration_plan(ex.loader.graph.leaf_nodes())
    call_command("makemigrations", "overbae", check=True, dry_run=True, verbosity=1)
    if ref != "fresh":
        from overbae.models import Cell, Dataset, DatasetHistory

        assert Dataset.objects.get(pk=dataset_id).active_id == second.pk
        assert (
            list(
                map(
                    str,
                    Cell.objects.filter(dataset_id=dataset_id)
                    .order_by("position")
                    .values_list("id", flat=True),
                )
            )
            == cell_ids
        )
        if is_main:
            assert Cell.objects.get(pk=first.pk).state == "ok"
            history = DatasetHistory.objects.get(
                dataset_id=dataset_id, kind="main_cell_metadata", reference=str(second.pk)
            )
            assert history.payload["attachment"]["sha256"] == "c" * 64
            assert history.payload["parent_id"] == str(first.pk)
            archived = DatasetHistory.objects.get(
                kind="main_decision_job", reference=str(decision.pk)
            ).payload
            assert (
                archived["result"]["saved"] == "/weights/retained/checkpoint"
                and archived["cost_usd"] == "1.2500"
                and archived["evaluation"]["score"] == 0.75
            )
            assert DatasetHistory.objects.get(
                kind="main_execution", reference=str(execution.pk)
            ).payload["cell_id"] == str(second.pk)
            from django.apps import apps

            assert (
                apps.get_model("overbae", "ProjectHistory")
                .objects.filter(kind="main_mcp_request")
                .count()
                == 2
            )
        else:
            first.refresh_from_db()
            second.refresh_from_db()
            assert first.note == "preserved" and first.review == {"retained": True}
            assert (
                second.state == "failed"
                and second.error == "original failure"
                and second.quality_report == {"count": 9}
            )
        assert all(path.read_bytes() == content for path, content in retained_files.items())
        print("Populated records and file bytes retained", flush=True)
    from django.apps import apps

    with connections["default"].cursor() as cursor:
        for model in apps.get_app_config("overbae").get_models():
            columns = connections["default"].introspection.get_table_description(
                cursor, model._meta.db_table
            )
            assert {column.name for column in columns} == {
                field.column for field in model._meta.local_fields
            }, model._meta.db_table
    print("Physical columns match current models", flush=True)
    print("PASS", ref, flush=True)
finally:
    connections.close_all()
    with conn.cursor() as c:
        c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    conn.close()
    media.cleanup()
    print("Dropped", name, flush=True)
