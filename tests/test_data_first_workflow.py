import json
import math
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from overbae.management.commands.seed_demo import seed_model_workflows
from overbae.models import (
    DataPartitionPlan,
    Dataset,
    DecisionPerformanceRun,
    NativeEvaluationPlan,
    Project,
    ProjectMembership,
    User,
)
from overbae.services import native_evaluation
from overbae.services.datasets import land, lifecycle, paths, rows, store, use, workbench
from overbae.services.datasets.partition_plans import build

pytestmark = pytest.mark.django_db


def workspace():
    project = Project.objects.create(name="Data-first", slug="data-first")
    user = User.objects.create_user(email="data-first@example.com", password="fixture")
    ProjectMembership.objects.create(project=project, user=user)
    client = APIClient()
    client.force_authenticate(user)
    dataset = Dataset.objects.create(
        project=project, name="Observations", intent="train", brief="Classify supplied evidence"
    )
    records = []
    for i in range(40):
        request = {
            "state": f"Evidence {i}",
            "question": "Choose",
            "kind": "choice",
            "options": ["yes", "no"],
        }
        for _ in range(2):
            records.append(
                {
                    "decision": {
                        **request,
                        "target_probabilities": [0.8, 0.2],
                        "target_semantics": "annotator_distribution",
                    },
                    "input": {"decision": request},
                    "expected_output": {"probabilities": [0.8, 0.2]},
                    "group_id": f"case-{i}",
                    "family": "held" if i >= 36 else "general",
                    "benchmark": "fixture",
                }
            )
    land.land_rows(dataset, records)
    dataset.refresh_from_db()
    return project, client, dataset


def test_data_first_partition_api_preserves_duplicates_lineage_and_explicit_holdouts(settings):

    settings.STRIPE_SECRET_KEY = ""
    project, client, dataset = workspace()
    url = reverse("data-partition-list")
    body = {
        "project": str(project.pk),
        "source_cell": str(dataset.active_cell.pk),
        "request_key": "partition-fixture",
        "name": "Frozen roles",
        "recipe": {
            "seed": 37,
            "fractions": {"train": 0.6, "development": 0.2, "calibration": 0.1, "final": 0.1},
            "group_by": ["group_id"],
            "stratify_by": "family",
            "holdouts": [{"field": "family", "values": ["held"], "role": "final"}],
        },
    }
    with patch("overbae.tasks.data_partitions.build_plan.delay"):
        first = client.post(url, body, format="json")
        assert first.status_code == 202, first.content
        second = client.post(url, body, format="json")
        assert second.json()["id"] == first.json()["id"]
        changed = client.post(
            url, {**body, "recipe": {**body["recipe"], "seed": 12}}, format="json"
        )
        assert changed.status_code == 400
    plan = DataPartitionPlan.objects.get(pk=first.json()["id"])
    build(plan.pk)
    plan.refresh_from_db()
    assert plan.state == "completed", plan.error
    assert sum(plan.report["counts"].values()) == 80
    assigned = {}
    for member in plan.members.select_related("cell__dataset"):
        for row in rows.iter_rows(member.cell):
            group = row.extra["group_id"]
            assert group not in assigned or assigned[group] == member.role
            assigned[group] = member.role
            assert row.extra["decision"]["target_probabilities"] == [0.8, 0.2]
            if row.extra["family"] == "held":
                assert member.role == "final"
    assert len(assigned) == 40
    before = list(plan.members.values_list("cell_id", flat=True))
    build(plan.pk)
    assert list(plan.members.values_list("cell_id", flat=True)) == before
    dataset.refresh_from_db()
    assert dataset.capability_id is None and dataset.active_cell.frozen
    foreign = Project.objects.create(name="Other", slug="other-data-first")
    assert client.post(url, {**body, "project": str(foreign.pk)}, format="json").status_code == 404

    final = plan.members.select_related("cell__dataset").get(role="final").cell
    lifecycle.set_intent(final.dataset, "eval")
    use.use(final.dataset, "eval", cell=final)
    with pytest.raises(ValueError, match="intent is fixed"):
        lifecycle.set_intent(final.dataset, "train")


def test_standalone_comparison_runs_three_participants_without_training_or_calibration(settings):

    settings.STRIPE_SECRET_KEY = ""
    project, client, dataset = workspace()
    dataset.intent = "eval"
    dataset.save()
    body = {
        "project": str(project.pk),
        "name": "Screen foundations",
        "request_key": "compare-fixture",
        "final_cell": str(dataset.active_cell.pk),
        "participants": [
            {
                "key": key,
                "name": key,
                "kind": "external",
                "model": "typesafe/jev-1.13",
                "served_model": "typesafe/jev-1.13-20260917",
            }
            for key in ("base", "candidate", "comparator")
        ],
        "baseline": "base",
        "bootstrap_samples": 10,
        "seed": 37,
    }
    with (
        patch.object(
            native_evaluation, "runtime", return_value={"app": "offline", "environment": "test"}
        ),
        patch("overbae.tasks.native_evaluation.advance_plan.delay"),
    ):
        response = client.post(reverse("native-evaluation-list"), body, format="json")
        assert response.status_code == 201, response.content
        repeated = client.post(reverse("native-evaluation-list"), body, format="json")
        assert repeated.json()["id"] == response.json()["id"]
    with patch("overbae.tasks.native_evaluation.advance_plan.delay"):
        launched = client.post(
            reverse("native-evaluation-launch-evaluation", args=[response.json()["id"]]),
            {},
            format="json",
        )
        assert launched.status_code == 202
    plan = NativeEvaluationPlan.objects.get(pk=response.json()["id"])
    assert plan.job_id is None and plan.calibration_cell_id is None
    submitted = []

    def external_step(plan, role, participant):
        assert role == "final" and plan.calibration == {}
        source = native_evaluation.seal_suite(plan, role)
        key = participant["key"]
        probability = 0.8 if key == "candidate" else 0.6
        destination = source.parent / f"{key}.jsonl"
        with destination.open("w") as output:
            for line in source.open():
                record = json.loads(line)
                assert set(record["decision"]) == {"state", "question", "kind", "options"}
                output.write(
                    json.dumps(
                        {
                            "key": record["key"],
                            "input_sha256": record["input_sha256"],
                            "kind": "choice",
                            "model_identity": participant["served_model"],
                            "probabilities": [probability, 1 - probability],
                            "log_probabilities": [math.log(probability), math.log(1 - probability)],
                        }
                    )
                    + "\n"
                )
        submitted.append(key)
        return {"completed": True, "decisions": 80, "recorded_cost_usd": 0.1}

    with patch("overbae.services.native_evaluation.external_step", side_effect=external_step):
        for _ in range(12):
            native_evaluation.advance(plan.pk)
    plan.refresh_from_db()
    assert plan.state == "completed", plan.error
    assert sorted(submitted) == ["base", "candidate", "comparator"]
    report = plan.results["comparisons"]["candidate"]["raw"]["benchmarks"]["fixture"]
    assert report["expected"] == report["paired_decisions"] == 80
    assert report["metrics"]["cross_entropy"]["candidate_minus_baseline"] < 0
    assert "accuracy" not in report["metrics"]
    assert plan.results["calibration"] is None
    assert not native_evaluation.directory(plan, "calibration").exists()
    ProjectMembership.objects.filter(project=project).delete()
    assert (
        client.get(reverse("native-evaluation-detail", kwargs={"pk": plan.pk})).status_code == 404
    )


def test_calibration_and_final_cannot_share_group_identity(settings):

    project, _, dataset = workspace()
    dataset.intent = "eval"
    dataset.save()
    with pytest.raises(ValueError, match="separate"):
        native_evaluation.create_plan(
            project,
            name="Bad split",
            request_key="bad-split",
            final_cell=dataset.active_cell,
            calibration_cell=dataset.active_cell,
            participants=[
                {
                    "key": "base",
                    "name": "base",
                    "kind": "external",
                    "model": "typesafe/jev-1.13",
                    "served_model": "typesafe/jev-1.13-20260917",
                }
            ],
            baseline="base",
        )


def test_native_agent_imports_declared_mean_without_inventing_probabilities(settings):

    project = Project.objects.create(name="Ratings", slug="ratings-workshop")
    dataset = Dataset.objects.create(
        project=project,
        name="Ratings",
        intent="eval",
        brief="Evaluate relevance using the supplied mean ratings",
    )
    land.land_rows(
        dataset,
        [
            {
                "evidence": "A relevant passage",
                "prompt": "Rate relevance",
                "rating": 3.4,
                "scale": [1, 2, 3, 4, 5],
                "choices": ["1", "2", "3", "4", "5"],
                "meaning": "ordinal_mean",
                "origin": {"source": "annotation guide", "annotation_count": 5},
            }
        ],
    )
    dataset.refresh_from_db()
    user = User.objects.create_user(email="ratings@example.com", password="fixture")
    ProjectMembership.objects.create(project=project, user=user)
    client = APIClient()
    client.force_authenticate(user)
    source = dataset.active_cell
    original = next(store.iter_rows(paths.cell_path(dataset.id, source.id)))
    request = {
        "state": original["evidence"],
        "question": original["prompt"],
        "kind": "score",
        "options": original["choices"],
        "option_values": original["scale"],
    }
    authored = {
        **original,
        "decision": {
            **request,
            "target_mean": original["rating"],
            "target_semantics": original["meaning"],
            "target_provenance": original["origin"],
        },
        "input": {"decision": request},
        "expected_output": {"mean": original["rating"], "values": original["scale"]},
    }
    receipt = client.post(
        f"/api/datasets/{dataset.pk}/versions/import/",
        {
            "source_cell": str(source.pk),
            "source_fingerprint": source.fingerprint,
            "request_key": "native-mean-mapping",
            "name": "Declared mean mapping",
            "provenance": "Native agent mapped the supplied annotation-guide mean; no distribution was inferred.",
            "imported_rows": [authored],
        },
        format="json",
    )
    assert receipt.status_code == 202, receipt.data
    workbench.execute(receipt.data["id"])
    dataset.refresh_from_db()
    row = next(rows.iter_rows(dataset.active_cell))
    assert row.expected_output == {"mean": 3.4, "values": [1, 2, 3, 4, 5]}
    assert "target_probabilities" not in row.extra["decision"]
    assert row.extra["rating"] == 3.4
    assert dataset.active_cell.review["execution"] == "external_attributed"
    assert next(store.iter_rows(paths.cell_path(dataset.pk, source.pk))) == original


def test_changing_an_existing_mean_records_semantic_impact_and_preserves_source():

    project = Project.objects.create(name="Mean review", slug="mean-review")
    dataset = Dataset.objects.create(project=project, name="Means", intent="train")
    request = {
        "state": "Evidence",
        "question": "Rate",
        "kind": "score",
        "options": ["low", "high"],
        "target_mean": 0.4,
        "option_values": [0, 1],
        "target_semantics": "ordinal_mean",
    }
    land.land_rows(dataset, [{"decision": request}])
    from conftest import import_version

    import_version(dataset, [{"source_row": 0, "decision": {**request, "target_mean": 0.9}}])
    dataset.refresh_from_db()
    assert next(rows.iter_rows(dataset.source)).extra["decision"]["target_mean"] == 0.4
    assert next(rows.iter_rows(dataset.active_cell)).extra["decision"]["target_mean"] == 0.9
    assert dataset.active_cell.review["execution"] == "external_attributed"
    assert dataset.active_cell.review["decision_changes"] == 1


def test_demo_model_workflows_remain_terminal_and_reset_without_provider_work():

    project, _, _ = workspace()
    with patch(
        "overbae.core.decisions.request_once",
        side_effect=AssertionError("demo must not call providers"),
    ):
        seed_model_workflows(project)
    assert set(
        DataPartitionPlan.objects.filter(project=project).values_list("state", flat=True)
    ) == {"completed"}
    comparison = NativeEvaluationPlan.objects.get(project=project)
    assert comparison.state == "completed"
    assert comparison.results["demo"] is True
    assert comparison.results["comparisons"]["demo"]["raw"]["candidate"]["benchmarks"]
    assert DecisionPerformanceRun.objects.get(project=project).state == "completed"
