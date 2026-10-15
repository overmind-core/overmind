import uuid

import psycopg2
import pytest
from django.db import connections
from django.db.backends.postgresql.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings

BASE = ("overbae", "0015_workshop_execution_clock")
TARGET = ("overbae", "0016_innermind")


@pytest.fixture
def scratch_database():
    settings = connections["default"].settings_dict
    name = f"migration_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(
        dbname="postgres",
        user=settings["USER"],
        password=settings["PASSWORD"],
        host=settings["HOST"],
        port=settings["PORT"],
    )
    admin.autocommit = True
    admin.cursor().execute(f'CREATE DATABASE "{name}"')
    try:
        yield name
    finally:
        admin.cursor().execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def test_evaluators_sets_and_variant_runs_become_matrix_rows_sets_and_grouped_runs(
    scratch_database, django_db_blocker
):
    original = connections["default"]
    database = DatabaseWrapper(
        {**original.settings_dict, "NAME": scratch_database}, alias="default"
    )
    with django_db_blocker.unblock(), override_settings(MIGRATION_MODULES={}):
        connections["default"] = database
        try:
            executor = MigrationExecutor(database)
            executor.migrate([BASE])
            old = executor.loader.project_state([BASE]).apps
            project = old.get_model("overbae", "Project").objects.create(name="Migration")
            capability = old.get_model("overbae", "Capability").objects.create(
                project=project, name="Answer", slug="answer"
            )
            evaluator = old.get_model("overbae", "Evaluator")
            judge = evaluator.objects.create(
                project=project,
                capability=capability,
                name="resolves",
                kind="llm_judge",
                rubric_md="The reply confirms the refund.",
            )
            check = evaluator.objects.create(
                project=project,
                capability=capability,
                name="mentions-order",
                kind="deterministic",
                config={"check": "contains", "reference": "order"},
            )
            eval_set = old.get_model("overbae", "EvalSet").objects.create(
                project=project, capability=capability, name="Tickets"
            )
            member = old.get_model("overbae", "EvalSetMember")
            member.objects.create(eval_set=eval_set, evaluator=judge, order=0)
            member.objects.create(eval_set=eval_set, evaluator=check, order=1)
            dataset = old.get_model("overbae", "Dataset").objects.create(
                project=project, name="Evaluation", intent="eval"
            )
            run = old.get_model("overbae", "EvalRun").objects.create(
                project=project, dataset=dataset, eval_set=eval_set, name="Backtest"
            )
            variant = old.get_model("overbae", "EvalVariant")
            baseline = variant.objects.create(
                run=run, label="recorded", mode="recorded", is_baseline=True, order=0
            )
            candidate = variant.objects.create(
                run=run, label="candidate", mode="generate", model_name="fake/model", order=1
            )
            run.summary = {
                "variants": {
                    str(baseline.id): {"n": 3, "metrics": {"resolves": {"mean": 0.5, "n": 3}}},
                    str(candidate.id): {"n": 3, "metrics": {"resolves": {"mean": 0.9, "n": 3}}},
                }
            }
            run.save(update_fields=["summary"])
            run_evaluator = old.get_model("overbae", "RunEvaluator")
            run_evaluator.objects.create(
                run=run,
                evaluator=judge,
                snapshot={
                    "name": "resolves",
                    "kind": "llm_judge",
                    "rubric_md": "The frozen rule confirms the refund amount.",
                },
                order=0,
            )
            run_evaluator.objects.create(run=run, evaluator=check, order=1)
            run_evaluator.objects.create(
                run=run,
                evaluator=None,
                snapshot={
                    "name": "deleted-check",
                    "kind": "deterministic",
                    "config": {"check": "contains", "reference": "refund"},
                },
                order=2,
            )

            executor.loader.build_graph()
            executor.migrate([TARGET])
            new = executor.loader.project_state([TARGET]).apps

            rows = {
                row.name: row
                for row in new.get_model("overbae", "EvalMatrix").objects.filter(
                    project_id=project.id
                )
            }
            assert {name: row.type for name, row in rows.items()} == {
                "resolves": "llm",
                "mentions-order": "deterministic",
            }
            assert rows["resolves"].source == "The reply confirms the refund."
            assert "'order' in output" in rows["mentions-order"].source.replace("needle", "'order'")
            migrated_set = new.get_model("overbae", "EvalSet").objects.get(pk=eval_set.pk)
            assert set(migrated_set.rows.values_list("name", flat=True)) == set(rows)

            runs = {
                r.model_name or "recorded": r
                for r in new.get_model("overbae", "EvalRun").objects.filter(project_id=project.id)
            }
            assert set(runs) == {"recorded", "fake/model"}
            assert runs["recorded"].pk == run.pk
            assert runs["recorded"].group == runs["fake/model"].group
            assert runs["recorded"].is_baseline and not runs["fake/model"].is_baseline
            for migrated_run in runs.values():
                matrix = {row["name"]: row for row in migrated_run.matrix}
                assert list(matrix) == ["resolves", "mentions-order", "deleted-check"]
                assert matrix["resolves"]["source"] == "The frozen rule confirms the refund amount."
                assert matrix["deleted-check"]["type"] == "deterministic"
                assert "needle = 'refund'" in matrix["deleted-check"]["source"]
            assert runs["recorded"].summary["score"] == 0.5
            assert runs["fake/model"].summary["score"] == 0.9
        finally:
            connections["default"] = original
            database.close()


@pytest.mark.parametrize("start", ["0016_innermind", "0042_trainingplan"])
def test_joined_history_upgrades_from_either_branch(start, scratch_database, django_db_blocker):
    original = connections["default"]
    database = DatabaseWrapper(
        {**original.settings_dict, "NAME": scratch_database}, alias="default"
    )
    target = ("overbae", "0043_innermind_training")
    with django_db_blocker.unblock(), override_settings(MIGRATION_MODULES={}):
        connections["default"] = database
        try:
            executor = MigrationExecutor(database)
            executor.migrate([("overbae", start)])
            old = executor.loader.project_state([("overbae", start)]).apps
            project = old.get_model("overbae", "Project").objects.create(name="Preserved")
            dataset = old.get_model("overbae", "Dataset").objects.create(
                project=project, name="Source"
            )
            cell = old.get_model("overbae", "Cell").objects.create(dataset=dataset, position=0)
            if start == "0016_innermind":
                saved = old.get_model("overbae", "EvalMatrix").objects.create(
                    project=project, name="Quality", type="llm", source="A useful answer."
                )
            else:
                user = old.get_model("overbae", "User").objects.create(
                    email="migration@example.test", clerk_user_id="migration"
                )
                saved = old.get_model("overbae", "TrainingPlan").objects.create(
                    project=project,
                    source_cell=cell,
                    created_by=user,
                    name="Plan",
                    request_key="join",
                    request_fingerprint="request",
                    design_identity="design",
                    state="draft",
                    specification={"preserve": True},
                )
            executor.loader.build_graph()
            executor.migrate([target])
            new = executor.loader.project_state([target]).apps
            assert (
                new.get_model("overbae", "Project").objects.get(pk=project.pk).name == "Preserved"
            )
            assert new.get_model("overbae", "Cell").objects.filter(pk=cell.pk).exists()
            assert new.get_model("overbae", "EvalMatrix").objects.count() == (
                start == "0016_innermind"
            )
            assert new.get_model("overbae", "TrainingPlan").objects.count() == (
                start == "0042_trainingplan"
            )
            if start == "0042_trainingplan":
                assert new.get_model("overbae", "TrainingPlan").objects.get(
                    pk=saved.pk
                ).specification == {"preserve": True}
            else:
                assert (
                    new.get_model("overbae", "EvalMatrix").objects.get(pk=saved.pk).source
                    == "A useful answer."
                )
        finally:
            connections["default"] = original
            database.close()
