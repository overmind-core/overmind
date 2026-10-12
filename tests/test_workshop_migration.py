import hashlib
import uuid
from pathlib import Path

import psycopg2
import pytest
from django.conf import settings
from django.db import connections
from django.db.backends.postgresql.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings

BASE = [
    ("overbae", "0015_workshop_execution_clock"),
    ("django_celery_beat", "0019_alter_periodictasks_options"),
]
TARGET = [("overbae", "0016_workshop_without_agent")]


@pytest.fixture
def scratch_database():
    config = connections["default"].settings_dict
    name = f"migration_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(
        dbname="postgres",
        user=config["USER"],
        password=config["PASSWORD"],
        host=config["HOST"],
        port=config["PORT"],
    )
    admin.autocommit = True
    admin.cursor().execute(f'CREATE DATABASE "{name}"')
    try:
        yield name
    finally:
        admin.cursor().execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


@pytest.fixture
def migrate(scratch_database, django_db_blocker):
    original = connections["default"]
    database = DatabaseWrapper(
        {**original.settings_dict, "NAME": scratch_database}, alias="default"
    )

    def step(targets):
        executor = MigrationExecutor(database)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    with django_db_blocker.unblock(), override_settings(MIGRATION_MODULES={}):
        connections["default"] = database
        try:
            yield step
        finally:
            database.close()
            connections["default"] = original


def _frame(dataset_id, cell_id) -> Path:
    path = Path(settings.MEDIA_ROOT) / "datasets" / str(dataset_id) / "cells" / f"{cell_id}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"frame")
    return path


def test_a_chain_with_a_proposal_and_a_failed_tail_becomes_a_tree_of_versions(migrate):
    apps = migrate(BASE)
    project = apps.get_model("overbae", "Project").objects.create(name="Migration")
    dataset_model = apps.get_model("overbae", "Dataset")
    cell_model = apps.get_model("overbae", "Cell")
    chained = dataset_model.objects.create(
        project=project, name="Chain", intent="train", state="diagnosing", chat=[{"role": "user"}]
    )
    source = cell_model.objects.create(dataset=chained, position=0, state="ok", fingerprint="a")
    cleaned = cell_model.objects.create(
        dataset=chained,
        position=1,
        state="ok",
        fingerprint="b",
        input_fingerprint="a",
        script="df = df.dropna()",
        review={"rows_before": 4, "rows_after": 3, "rows_removed": 1, "status": "accepted"},
    )
    proposal = cell_model.objects.create(
        dataset=chained, position=2, state="proposed", script="df = df.head(1)"
    )
    failed = cell_model.objects.create(
        dataset=chained, position=3, state="failed", script="1/0", error="ZeroDivisionError"
    )
    chained.active = proposal
    chained.save(update_fields=["active"])
    gone = [_frame(chained.pk, proposal.pk), _frame(chained.pk, failed.pk)]
    kept = [_frame(chained.pk, source.pk), _frame(chained.pk, cleaned.pk)]
    run = apps.get_model("overbae", "EvalRun").objects.create(
        project=project, dataset=chained, cell=source
    )
    broken = dataset_model.objects.create(
        project=project, name="Run failed", intent="eval", state="error", error="Traceback"
    )
    landed = cell_model.objects.create(dataset=broken, position=0, state="ok", fingerprint="c")
    _frame(broken.pk, landed.pk)
    unlanded = dataset_model.objects.create(
        project=project, name="Landing failed", state="error", error="Unreadable file"
    )
    reaper = apps.get_model("django_celery_beat", "PeriodicTask").objects.create(
        name="reap-stuck-dataset-runs", task="overbae.tasks.datasets.reap_stuck_runs"
    )

    apps = migrate(TARGET)
    cell_model = apps.get_model("overbae", "Cell")
    dataset_model = apps.get_model("overbae", "Dataset")
    versions = {c.position: c for c in cell_model.objects.filter(dataset_id=chained.pk)}
    assert sorted(versions) == [0, 1]
    assert versions[0].parent_id is None and versions[0].script_sha256 == ""
    assert versions[1].parent_id == source.pk
    assert versions[1].script_sha256 == hashlib.sha256(b"df = df.dropna()").hexdigest()
    assert versions[1].impact == {"rows_before": 4, "rows_after": 3, "rows_removed": 1}
    migrated = dataset_model.objects.get(pk=chained.pk)
    assert (migrated.state, migrated.active_id) == ("idle", cleaned.pk)
    assert apps.get_model("overbae", "EvalRun").objects.get(pk=run.pk).cell_id == source.pk
    assert dataset_model.objects.get(pk=broken.pk).state == "idle"
    assert dataset_model.objects.get(pk=broken.pk).error == ""
    assert dataset_model.objects.get(pk=unlanded.pk).state == "error"
    assert not any(path.exists() for path in gone)
    assert all(path.exists() for path in kept)
    periodic = apps.get_model("django_celery_beat", "PeriodicTask")
    assert periodic.objects.get(pk=reaper.pk).enabled is False


def test_an_unrun_cell_a_consumer_uses_stops_the_migration(migrate):
    apps = migrate(BASE)
    project = apps.get_model("overbae", "Project").objects.create(name="Migration")
    dataset = apps.get_model("overbae", "Dataset").objects.create(
        project=project, name="Used", intent="eval"
    )
    cell_model = apps.get_model("overbae", "Cell")
    source = cell_model.objects.create(dataset=dataset, position=0, state="ok", fingerprint="a")
    _frame(dataset.pk, source.pk)
    stale = cell_model.objects.create(
        dataset=dataset,
        position=1,
        state="failed",
        error="This version was used and cannot change.",
    )
    apps.get_model("overbae", "EvalRun").objects.create(
        project=project, dataset=dataset, cell=stale
    )

    with pytest.raises(RuntimeError, match=str(stale.pk)):
        migrate(TARGET)


def test_cells_run_on_an_older_frame_or_without_a_file_leave_the_tree(migrate):
    apps = migrate(BASE)
    project = apps.get_model("overbae", "Project").objects.create(name="Migration")
    dataset = apps.get_model("overbae", "Dataset").objects.create(
        project=project, name="Edited upstream", intent="train"
    )
    cell_model = apps.get_model("overbae", "Cell")

    def cell(position, fingerprint, input_fingerprint, state="ok", stored=True):
        made = cell_model.objects.create(
            dataset=dataset,
            position=position,
            state=state,
            fingerprint=fingerprint,
            input_fingerprint=input_fingerprint,
            script=f"df = df.head({position})",
        )
        if stored:
            _frame(dataset.pk, made.pk)
        return made

    source = cell(0, "a", "")
    edited = cell(1, "b2", "a")
    cell(2, "", "b1", state="running")
    after_lost_run = cell(3, "d", "c")
    missing = apps.get_model("overbae", "Dataset").objects.create(
        project=project, name="Missing file", intent="train"
    )
    lost_source = cell_model.objects.create(
        dataset=missing, position=0, state="ok", fingerprint="x"
    )
    lost_child = cell_model.objects.create(
        dataset=missing, position=1, state="ok", fingerprint="y", input_fingerprint="x"
    )
    _frame(missing.pk, lost_child.pk)

    apps = migrate(TARGET)
    cell_model = apps.get_model("overbae", "Cell")
    remaining = cell_model.objects.filter(dataset_id=dataset.pk).order_by("position")
    assert [(c.pk, c.parent_id) for c in remaining] == [(source.pk, None), (edited.pk, source.pk)]
    assert not cell_model.objects.filter(pk=after_lost_run.pk).exists()
    assert not cell_model.objects.filter(pk__in=[lost_source.pk, lost_child.pk]).exists()
