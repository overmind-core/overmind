import asyncio
import time

import pytest

from overbae.models import DatasetPipelineRun
from overbae.services import operational_progress
from overbae.services.datasets import (
    attachments,
    exploration,
    heartbeat,
    land,
    partition_plans,
    paths,
    pipeline_runner,
    review,
    store,
    workbench,
)
from overbae.services.mcp.catalog import CATALOG
from tests.test_reusable_workshop_journey import call, execute, source, workspace
from tests.workshop_script_fixtures import (
    execute_fixture,
    identity_script,
    retained_package,
    script_step,
)

pytestmark = pytest.mark.django_db(transaction=True)


def decision_step(**settings):
    return script_step(
        "def transform(rows, parameters):\n"
        "    for row in rows:\n"
        "        yield {'source_row': row['source_row'], 'decision': {\n"
        "            'state': str(row['value']), 'question': 'Is this positive?',\n"
        "            'kind': 'choice', 'options': ['No', 'Yes'],\n"
        "            'target_probabilities': [0.0, 1.0], 'target_semantics': 'categorical_gold'}}\n",
        columns=["value"],
        output_schema={"source_row": "integer", "decision": "object"},
        **settings,
    )


def test_partition_canvas_replaces_corrected_process_and_preserves_pinned_inputs():
    project, client, context = workspace()
    dataset = source(client, project, "Decision source", [{"value": i} for i in range(40)])
    original = dataset.active_cell
    package = retained_package(project, context.user, [decision_step()])
    recipe = call(
        context, "save_dataset_pipeline", name="Decisions", request_key="recipe", package=package
    )["pipeline"]
    first = execute(context, dataset, recipe, "initial", source_cell=original)
    selected_first = dataset.cells.get(pk=first["details"]["output_cell"])
    selected_first.used_at = selected_first.created_at
    selected_first.save(update_fields=["used_at"])
    final = execute(context, dataset, recipe, "final", source_cell=original)
    dataset.refresh_from_db()
    selected = dataset.active_cell
    plan = partition_plans.request_plan(
        project,
        name="Holdouts",
        request_key="partition",
        source_cell=selected,
        recipe={
            "fractions": {"train": 0.7, "development": 0.1, "calibration": 0.1, "final": 0.1},
            "seed": 7,
        },
    )
    partition_plans.build(plan.pk)
    plan.refresh_from_db()
    assert plan.state == "completed", plan.error
    member = plan.members.select_related("cell__dataset").get(role="train")
    response = client.get(f"/api/datasets/{member.cell.dataset_id}/preparation/")
    assert response.status_code == 200, response.data
    process = response.data
    ids = {node["cell"]["id"] for node in process["nodes"]}
    assert str(original.pk) in ids
    assert str(selected.pk) in ids
    assert first["details"]["output_cell"] not in ids
    assert {node["role"] for node in process["nodes"] if node["role"]} == {
        "train",
        "development",
        "calibration",
        "final",
    }
    assert len(ids) == 6
    assert any(node["cell"]["script"] for node in process["nodes"])
    assert process["selected"] == str(member.cell_id)
    mcp = call(context, "inspect_dataset_workbench", dataset=str(member.cell.dataset_id))
    assert {node["cell"] for node in mcp["preparation"]["nodes"]} == ids
    assert mcp["preparation"]["selected"] == process["selected"]
    assert len(mcp["preparation"]["edges"]) == 5
    selected_first.refresh_from_db()
    assert (
        selected_first.fingerprint
        == DatasetPipelineRun.objects.get(output=selected_first).result["steps"][0][
            "output_fingerprint"
        ]
    )
    assert final["details"]["output_cell"] == str(selected.pk)


@pytest.mark.parametrize("fail_last", [False, True])
def test_explicit_row_batches_publish_atomically_and_preserve_order(fail_last):
    project, client, context = workspace()
    dataset = source(client, project, "Batched input", [{"value": i} for i in range(11)])
    original = dataset.active_cell
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Batched decisions",
        request_key="batch-recipe",
        package=retained_package(
            project,
            context.user,
            [
                decision_step(
                    batch_rows=4,
                    consumer="decision_train",
                    checks={"preserve_rows": True, "min_rows": 11},
                )
            ],
        ),
    )["pipeline"]
    receipt = call(
        context,
        "run_dataset_pipeline",
        dataset=str(dataset.pk),
        pipeline=recipe["id"],
        source_cell=str(original.pk),
        source_fingerprint=original.fingerprint,
        request_key="batch-run",
    )
    seen = []

    def executor(run, index, path, directory, step, files, progress):
        receipt = run.result["steps"][index]
        assert "exit_code" not in receipt
        assert "provider_execution" not in receipt
        receipt["exit_code"] = 0
        receipt["provider_execution"] = f"batch-{len(seen) + 1}"
        records = list(store.iter_rows(path))
        seen.append([row["value"] for row in records])
        if fail_last and len(seen) == 3:
            raise ValueError("Last batch failed")
        return execute_fixture(run, index, path, directory, step, files, progress)

    workbench.execute(receipt["run"]["id"], script_executor=executor)
    run = DatasetPipelineRun.objects.get(pk=receipt["run"]["id"])
    dataset.refresh_from_db()
    assert seen == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10]]
    if fail_last:
        assert run.state == "failed"
        assert dataset.active_cell.pk == original.pk
        assert dataset.cells.count() == 1
    else:
        assert run.state == "completed", run.error
        assert [
            row["decision"]["state"]
            for row in store.iter_rows(paths.cell_path(dataset.pk, dataset.active_cell.pk))
        ] == [str(i) for i in range(11)]
        assert run.result["steps"][0]["batches"]["completed"] == 3


def test_controller_restart_fails_interrupted_work_without_replaying_or_publishing():
    project, client, context = workspace()
    dataset = source(client, project, "Interrupted source", [{"value": 1}])
    original = dataset.active_cell
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Interrupted decisions",
        request_key="interrupted-recipe",
        package=retained_package(project, context.user, [decision_step()]),
    )["pipeline"]
    receipt = call(
        context,
        "run_dataset_pipeline",
        dataset=str(dataset.pk),
        pipeline=recipe["id"],
        source_cell=str(original.pk),
        source_fingerprint=original.fingerprint,
        request_key="interrupted-run",
    )
    run = DatasetPipelineRun.objects.get(pk=receipt["run"]["id"])
    with pipeline_runner.controller():
        run.refresh_from_db()
        assert run.state == "queued"
        DatasetPipelineRun.objects.filter(pk=run.pk).update(state="running")
        with (
            pytest.raises(pipeline_runner.RuntimeUnavailableError, match="already running"),
            pipeline_runner.controller(),
        ):
            pytest.fail("A second controller acquired the running controller's lock")
        run.refresh_from_db()
        assert run.state == "running"
    with pipeline_runner.controller():
        run.refresh_from_db()
        assert run.state == "failed"
        assert "controller stopped" in run.error
        assert run.output_id is None
        dataset.refresh_from_db()
        assert dataset.active_cell.pk == original.pk
        assert dataset.cells.count() == 1
    repeated = call(
        context,
        "run_dataset_pipeline",
        dataset=str(dataset.pk),
        pipeline=recipe["id"],
        source_cell=str(original.pk),
        source_fingerprint=original.fingerprint,
        request_key="interrupted-run",
    )
    assert repeated["run"]["id"] == str(run.pk)
    assert repeated["run"]["state"] == "failed"


def test_runner_heartbeat_survives_long_impact_measurement_without_claiming_progress(monkeypatch):
    project, client, context = workspace()
    dataset = source(client, project, "Long measurement", [{"value": 1}])
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Measured decisions",
        request_key="measurement-recipe",
        package=retained_package(project, context.user, [decision_step()]),
    )["pipeline"]
    submitted = call(
        context,
        "run_dataset_pipeline",
        dataset=str(dataset.pk),
        pipeline=recipe["id"],
        source_cell=str(dataset.active_cell.pk),
        source_fingerprint=dataset.active_cell.fingerprint,
        request_key="measurement-run",
    )
    run_id = submitted["run"]["id"]
    measure = review.impact_files
    observed = []

    def slow_measurement(before, after):
        previous = workbench.runner_status()["last_heartbeat_at"]
        progress = operational_progress.latest(project.pk, "dataset_pipeline", run_id)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = workbench.runner_status()
            if status["last_heartbeat_at"] != previous:
                break
            time.sleep(0.01)
        observed.append(status)
        assert status["status"] == "executing"
        assert status["last_heartbeat_at"] > previous
        assert (
            operational_progress.latest(project.pk, "dataset_pipeline", run_id)["last_progress_at"]
            == progress["last_progress_at"]
        )
        return measure(before, after)

    monkeypatch.setattr(heartbeat, "INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(pipeline_runner.DockerEngine, "request", lambda *args, **kwargs: {})
    monkeypatch.setattr(pipeline_runner, "execute_script", execute_fixture)
    monkeypatch.setattr(review, "impact_files", slow_measurement)
    pipeline_runner.tick()
    run = DatasetPipelineRun.objects.get(pk=run_id)
    assert run.state == "completed", run.error
    assert len(observed) == 1
    assert workbench.runner_status()["status"] == "ready"


def test_nested_decision_errors_fail_preview_before_full_publication():
    project, client, context = workspace()
    dataset = source(client, project, "Bad target", [{"value": 1}])
    step = decision_step(consumer="decision_train")
    step["code"] = step["code"].replace("[0.0, 1.0]", "[0.4, 0.4]")
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Invalid target",
        request_key="bad-target",
        package=retained_package(project, context.user, [step]),
    )["pipeline"]
    result = execute(context, dataset, recipe, "preview-invalid", mode="preview")
    assert result["status"] == "failed"
    assert "normalized" in result["details"]["error"]
    assert dataset.cells.count() == 1


def test_preparation_does_not_trust_claimed_cross_project_source():
    project, client, context = workspace()
    dataset = source(client, project, "Forged parent", [{"value": 1}])
    dataset.source_spec = {
        "source_cell": "00000000-0000-0000-0000-000000000001",
        "partition_plan": "00000000-0000-0000-0000-000000000002",
    }
    dataset.save(update_fields=["source_spec"])
    response = client.get(f"/api/datasets/{dataset.pk}/preparation/")
    assert response.status_code == 200, response.data
    assert [node["cell"]["id"] for node in response.data["nodes"]] == [str(dataset.active_cell.pk)]
    assert response.data["edges"] == []


def test_preparation_follows_attached_sources_and_derived_datasets():
    project, client, context = workspace()
    dataset = source(client, project, "Combined sources", [{"value": 1}])
    original = dataset.active_cell
    added = attachments.commit(dataset, land.Landing([{"value": 2}]))
    operation = exploration.request(
        project, source_cell=added, name="Derived source", request_key="derive", kind="derive"
    )
    exploration.advance(operation.pk)
    operation.refresh_from_db()
    assert operation.state == "completed", operation.error
    response = client.get(f"/api/datasets/{operation.output_dataset_id}/preparation/")
    assert response.status_code == 200, response.data
    assert {node["cell"]["id"] for node in response.data["nodes"]} == {
        str(original.pk),
        str(added.pk),
        str(operation.output_dataset.active_cell.pk),
    }
    assert len(response.data["edges"]) == 2


def test_preparation_includes_successful_sibling_deliverables():
    project, client, context = workspace()
    dataset = source(client, project, "Branch source", [{"value": 1}])
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Two deliverables",
        request_key="branches",
        package=retained_package(
            project,
            context.user,
            [
                decision_step(id="first", input="source"),
                decision_step(id="second", input="source"),
            ],
        ),
    )["pipeline"]
    result = execute(context, dataset, recipe, "branches")
    assert result["status"] == "completed", result
    response = client.get(f"/api/datasets/{dataset.pk}/preparation/")
    assert response.status_code == 200, response.data
    assert len(response.data["nodes"]) == 3
    assert len(response.data["edges"]) == 2


def test_batched_empty_branch_still_executes_its_retained_script():
    project, client, context = workspace()
    dataset = source(client, project, "Empty branch", [{"value": 1}])
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Empty branch execution",
        request_key="empty",
        package=retained_package(
            project,
            context.user,
            [
                script_step(
                    "def transform(rows, parameters):\n    return []\n",
                    id="empty",
                    output_schema={"value": "integer", "source_row": "integer"},
                ),
                script_step(
                    "def transform(rows, parameters):\n    raise ValueError('The empty branch executed')\n",
                    input="empty",
                    batch_rows=4,
                ),
            ],
        ),
    )["pipeline"]
    result = execute(context, dataset, recipe, "empty")
    assert result["status"] == "failed", result
    assert "The empty branch executed" in result["details"]["error"]
    assert dataset.cells.count() == 1


def test_retained_scripts_have_a_dataset_home_and_retries_do_not_restore_old_work():
    project, client, context = workspace()
    dataset = source(client, project, "Preparation", [{"value": 1}])
    original = dataset.active_cell
    package = retained_package(project, context.user, [identity_script()])
    args = dict(dataset=str(dataset.pk), name="Preserve", request_key="home", package=package)
    recipe = call(context, "save_dataset_pipeline", **args)["pipeline"]
    assert (
        call(context, "inspect_dataset_workbench", dataset=str(dataset.pk))["current_pipeline"]
        == recipe["id"]
    )
    first = execute(context, dataset, recipe, "first", source_cell=original)
    assert first["status"] == "completed", first["details"]["error"]
    revised = call(
        context,
        "save_dataset_pipeline",
        dataset=str(dataset.pk),
        name="Corrected",
        request_key="correction",
        package=package,
        pipeline=recipe["pipeline_id"],
        expected_revision=recipe["revision"],
    )["pipeline"]
    call(context, "save_dataset_pipeline", **args)
    assert (
        call(context, "inspect_dataset_workbench", dataset=str(dataset.pk))["current_pipeline"]
        == revised["id"]
    )
    execute(context, dataset, revised, "corrected", source_cell=original)
    response = client.get(f"/api/datasets/{dataset.pk}/preparation/")
    assert response.status_code == 200, response.data
    assert first["details"]["output_cell"] not in {n["cell"]["id"] for n in response.data["nodes"]}
    assert response.data["nodes"][0]["cell"]["script"]


def test_corrected_cell_diffs_use_actual_input_not_the_previous_attempt():
    project, client, context = workspace()
    dataset = source(client, project, "Corrected rows", [{"value": 1}])
    original = dataset.active_cell
    for number in (999, 2):
        recipe = call(
            context,
            "save_dataset_pipeline",
            dataset=str(dataset.pk),
            name="Change value",
            request_key=f"change-{number}",
            package=retained_package(
                project,
                context.user,
                [
                    script_step(
                        f'def transform(rows, parameters):\n    for row in rows:\n        yield {{"source_row": row["source_row"], "value": {number}}}\n'
                    )
                ],
            ),
        )["pipeline"]
        result = execute(context, dataset, recipe, f"run-{number}", source_cell=original)
        assert result["status"] == "completed", result["details"]["error"]
    dataset.refresh_from_db()
    result = client.get(
        f"/api/datasets/{dataset.pk}/rows/", {"cell": str(dataset.active_cell.pk), "diff": "1"}
    )
    assert result.status_code == 200, result.data
    assert result.data["marks"][0]["before"]["value"] == 1


def test_workshop_surfaces_omit_retired_checks_but_keep_execution_receipts():
    project, client, context = workspace()
    dataset = source(client, project, "Retained preparation", [{"value": 1}])
    for name in ("inspect_dataset_preparation", "record_dataset_findings"):
        assert name not in {tool.name for tool in CATALOG.tools(frozenset({"read", "write"}))}
        result = asyncio.run(CATALOG.call(name, {"dataset": str(dataset.pk)}, context))
        assert result.isError
        assert result.structuredContent["error"]["code"] == "invalid_tool"
    assert client.get(f"/api/datasets/{dataset.pk}/preparation-checks/").status_code == 404
    assert (
        client.post(
            f"/api/datasets/{dataset.pk}/preparation-findings/", {}, format="json"
        ).status_code
        == 404
    )
    recipe = call(
        context,
        "save_dataset_pipeline",
        dataset=str(dataset.pk),
        name="Keep rows",
        request_key="keep-rows",
        package=retained_package(
            project, context.user, [identity_script(checks={"preserve_rows": True})]
        ),
    )["pipeline"]
    result = execute(context, dataset, recipe, "publish")
    assert result["status"] == "completed", result
    assert result["details"]["result"]["steps"][0]["check_results"]["passed"]["preserve_rows"]
    detail = client.get(f"/api/datasets/{dataset.pk}/")
    assert detail.status_code == 200
    assert "preparation_status" not in detail.data
    assert all("preparation_status" not in cell for cell in detail.data["cells"])
    mcp = call(context, "inspect_dataset", dataset=str(dataset.pk))
    assert "preparation_status" not in mcp
    assert all("preparation_status" not in cell for cell in mcp["cells"])
    process = call(context, "inspect_dataset_workbench", dataset=str(dataset.pk))
    assert "preparation_status" not in process
    assert len(process["preparation"]["nodes"]) == 2
    assert process["current_pipeline"] == recipe["id"]
