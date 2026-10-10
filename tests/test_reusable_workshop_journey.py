import asyncio
import io
import json
import os
import time
import uuid
import zipfile
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from overbae.models import APIToken, Dataset, Project, ProjectMembership, Span, User
from overbae.services.datasets import (
    paths,
    pipeline_bindings,
    pipeline_packages,
    pipeline_runner,
    store,
    workbench,
)
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext, bind_context
from overbae.services.mcp.resources import read_resource
from tests.workshop_script_fixtures import (
    conversation_script,
    execute_fixture,
    filter_script,
    identity_script,
    rename_script,
    retained_package,
    select_script,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("eligible", [[True, False, True], [True, True], [False, False]])
def test_branch_fan_in_retains_every_observation_and_exact_parents(eligible):
    project, client, context = workspace()
    dataset = source(client, project, "Converged", [{"eligible": value} for value in eligible])
    original = dataset.active_cell
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Converged",
        request_key="converged",
        package=retained_package(
            context.project,
            context.user,
            [
                filter_script("eligible", True, id="yes", input="source"),
                filter_script("eligible", False, id="no", input="source"),
                identity_script(id="final", inputs=["yes", "no"]),
            ],
        ),
    )["pipeline"]
    assert recipe["flow"]["terminal_steps"] == ["final"]
    assert recipe["flow"]["unconsumed_steps"] == []
    preview = execute(context, dataset, recipe, "preview-merge", mode="preview", preview_rows=1)
    assert preview["status"] == "completed", preview
    assert dataset.cells.count() == 1
    result = execute(context, dataset, recipe, "merge")
    assert result["status"] == "completed", result
    dataset.refresh_from_db()
    cells = list(dataset.cells.exclude(pk=original.pk).order_by("position"))
    assert cells[-1].review["input_cells"] == [str(cell.pk) for cell in cells[:2]]
    output = list(store.iter_rows(paths.cell_path(dataset.pk, cells[-1].pk)))
    assert sorted(row["source_row"] for row in output) == list(range(len(eligible)))
    assert sorted(row["eligible"] for row in output) == sorted(eligible)
    for row in output:
        parent = cells[0 if row["eligible"] else 1]
        assert row["_overmind_provenance"]["parents"] == [
            {"cell": str(parent.pk), "fingerprint": parent.fingerprint, "row": row["source_row"]}
        ]
    assert [item["rows"] for item in cells[-1].review["inputs"]] == [
        eligible.count(True),
        eligible.count(False),
    ]


def test_fan_in_rejects_ambiguous_identity_without_publishing():
    project, client, context = workspace()
    dataset = source(client, project, "Overlap", [{"value": 1}, {"value": 2}])
    original = dataset.active_cell
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Overlap",
        request_key="overlap",
        package=retained_package(
            context.project,
            context.user,
            [
                select_script(["value"], id="a"),
                select_script(["value"], id="b", input="source"),
                identity_script(inputs=["a", "b"]),
            ],
        ),
    )["pipeline"]
    result = execute(context, dataset, recipe, "overlap-run")
    assert result["status"] == "failed", result
    assert "overlapping source_row" in result["details"]["error"]
    dataset.refresh_from_db()
    assert dataset.active_cell.pk == original.pk
    assert dataset.cells.count() == 1


def test_fan_in_rejects_incompatible_types_without_coercing_observations():
    project, client, context = workspace()
    dataset = source(
        client,
        project,
        "Mixed branch types",
        [
            {"eligible": True, "number": 1, "text": "1"},
            {"eligible": False, "number": 2, "text": "2"},
        ],
    )
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Incompatible branches",
        request_key="types",
        package=retained_package(
            context.project,
            context.user,
            [
                filter_script("eligible", True, id="yes", input="source"),
                filter_script("eligible", False, id="no", input="source"),
                select_script(["number"], id="numeric", input="yes"),
                select_script(["text"], id="textual", input="no"),
                rename_script({"number": "value"}, id="a", input="numeric"),
                rename_script({"text": "value"}, id="b", input="textual"),
                identity_script(inputs=["a", "b"]),
            ],
        ),
    )["pipeline"]
    result = execute(context, dataset, recipe, "incompatible-types")
    assert result["status"] == "failed", result
    assert "incompatible columns or types" in result["details"]["error"]
    assert dataset.cells.count() == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"inputs": []},
        {"inputs": ["source", "source"]},
        {"inputs": ["missing", "source"]},
        {"inputs": ["source"], "input": "source"},
    ],
)
def test_invalid_fan_in_graph_is_rejected_before_execution(fields):
    _, _, context = workspace()
    with pytest.raises(DatasetError, match="earlier"):
        retained_package(context.project, context.user, [identity_script(**fields)])


def test_conditional_revision_retrieval_adaptation_and_conflict_recovery():
    project, client, context = workspace()
    dataset = source(client, project, "Routing", [{"eligible": True}, {"eligible": False}])
    first = call(
        context,
        "save_dataset_pipeline",
        name="Eligible",
        request_key="routing",
        package=retained_package(
            context.project,
            context.user,
            [filter_script("eligible", True, id="accepted", input="source")],
        ),
    )["pipeline"]
    revised = call(
        context,
        "save_dataset_pipeline",
        name="Ineligible",
        request_key="routing-revised",
        pipeline=first["pipeline_id"],
        expected_revision=1,
        package=retained_package(
            context.project,
            context.user,
            [filter_script("eligible", False, id="accepted", input="source")],
        ),
    )["pipeline"]
    variant = call(
        context,
        "save_dataset_pipeline",
        name="Other input",
        request_key="routing-derived",
        derived_from=first["id"],
        package=retained_package(
            context.project,
            context.user,
            [filter_script("approved", True, id="accepted", input="source")],
        ),
    )["pipeline"]
    inspected = call(context, "inspect_dataset_workbench", pipeline=first["id"], limit=1)
    assert inspected["pipeline"] == first
    assert inspected["pipeline_page"]["total"] == 2
    assert inspected["pipelines"][0]["id"] == revised["id"]
    older = call(
        context, "inspect_dataset_workbench", pipeline=first["id"], pipeline_offset=1, limit=1
    )
    assert older["pipelines"][0]["id"] == first["id"]
    assert all(item["id"] != variant["id"] for item in inspected["pipelines"])
    assert any(first["id"] in str(link["uri"]) for link in inspected["resource_links"])
    run = execute(context, dataset, first, "original-after-revision")
    assert run["status"] == "completed"
    scoped = call(context, "inspect_dataset_workbench", pipeline=revised["id"])
    assert scoped["runs"] == []
    stale = asyncio.run(
        CATALOG.call(
            "save_dataset_pipeline",
            {
                "name": "Stale",
                "request_key": "stale",
                "pipeline": first["pipeline_id"],
                "expected_revision": 1,
                "package": first["package"],
            },
            context,
        )
    )
    assert stale.isError and stale.structuredContent["error"]["code"] == "revision_conflict"
    unrelated = Project.objects.create(name="Other project", slug="conditional-other")
    foreign = workbench.save_pipeline(
        unrelated,
        context.user,
        name="Private",
        request_key="private",
        package=retained_package(unrelated, context.user, [filter_script("eligible", True)]),
    )
    denied = asyncio.run(
        CATALOG.call("inspect_dataset_workbench", {"pipeline": str(foreign.pk)}, context)
    )
    assert denied.isError


def test_agent_authored_branches_are_retained_executed_and_read_back(settings):
    project, client, context = workspace()
    dataset = source(
        client, project, "Explicit branches", [{"eligible": True}, {"eligible": False}]
    )
    original = dataset.active_cell
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Eligibility split",
        request_key="branch",
        package=retained_package(
            context.project,
            context.user,
            [
                filter_script("eligible", True, id="eligible", input="source"),
                filter_script("eligible", False, id="ineligible", input="source"),
            ],
        ),
    )["pipeline"]
    assert [(edge["source"], edge["target"]) for edge in recipe["flow"]["edges"]] == [
        ("source", "eligible"),
        ("source", "ineligible"),
    ]
    assert recipe["flow"]["output"] == "ineligible"
    result = execute(context, dataset, recipe, "branch-run")
    assert result["status"] == "completed", result
    cells = list(dataset.cells.exclude(pk=original.pk).order_by("position"))
    assert [cell.rows for cell in cells] == [1, 1]
    assert [cell.review["input_cells"] for cell in cells] == [
        [str(original.pk)],
        [str(original.pk)],
    ]
    assert [cell.review["step_id"] for cell in cells] == ["eligible", "ineligible"]
    with bind_context(context):
        retained = json.loads(
            asyncio.run(read_resource(f"overmind://dataset-pipelines/{recipe['id']}"))[0].content
        )
    assert retained["flow"] == recipe["flow"]
    with pytest.raises(DatasetError, match="Cycles"):
        retained_package(
            context.project,
            context.user,
            [
                select_script(["eligible"], id="a", input="b"),
                select_script(["eligible"], id="b", input="a"),
            ],
        )


def call(context, tool_name, **arguments):
    result = asyncio.run(CATALOG.call(tool_name, arguments, context))
    assert not result.isError, result.structuredContent
    return result.structuredContent


def workspace():
    user = User.objects.create_user(email="reusable-workshop@example.test")
    project = Project.objects.create(name="Reusable Workshop", slug="reusable-workshop")
    ProjectMembership.objects.create(user=user, project=project)
    client = APIClient()
    client.force_authenticate(user)
    context = MCPContext(
        user=user,
        project=project,
        token=APIToken(
            scope={
                "scope": "project",
                "resourceIds": [str(project.pk)],
                "permission": ["read", "write"],
            }
        ),
    )
    return project, client, context


def source(client, project, name, records):
    response = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "name": name,
            "intent": "explore",
            "source": {"rows": records},
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    return Dataset.objects.get(pk=response.data["id"])


def execute(context, dataset, recipe, key, *, source_cell=None, **kwargs):
    original = source_cell or dataset.active_cell
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        receipt = call(
            context,
            "run_dataset_pipeline",
            dataset=str(dataset.pk),
            pipeline=recipe["id"],
            source_cell=str(original.pk),
            source_fingerprint=original.fingerprint,
            request_key=key,
            **kwargs,
        )
    workbench.execute(receipt["run"]["id"], script_executor=execute_fixture)
    return call(context, "get_job", kind="dataset_pipeline", id=receipt["run"]["id"])


def test_cell_transformation_inspection_binds_publication_not_review_claims():
    project, client, context = workspace()
    dataset = source(client, project, "Inspection", [{"value": 1}])
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Select",
        request_key="inspect",
        package=retained_package(context.project, context.user, [select_script(["value"])]),
    )["pipeline"]
    executed = execute(context, dataset, recipe, "inspect")
    assert executed["status"] == "completed"
    dataset.refresh_from_db()
    published = dataset.active_cell
    rest = client.get(f"/api/datasets/{dataset.pk}/").data
    mcp = call(context, "inspect_dataset", dataset=str(dataset.pk))
    rest_cell = next(cell for cell in rest["cells"] if cell["id"] == str(published.pk))
    mcp_cell = next(cell for cell in mcp["cells"] if cell["id"] == str(published.pk))
    assert rest_cell["transformation"] == mcp_cell["transformation"]
    assert rest_cell["transformation"]["execution"] == "isolated_container"
    assert rest_cell["transformation"]["run"] == executed["id"]
    assert rest_cell["transformation"]["pipeline"] == recipe["id"]
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        imported = call(
            context,
            "import_dataset_version",
            dataset=str(dataset.pk),
            source_cell=str(published.pk),
            source_fingerprint=published.fingerprint,
            request_key="external",
            name="External",
            provenance="Authored in local Python; not executed by Overmind.",
            imported_rows=[{"source_row": 0, "value": 2}],
        )
    workbench.execute(imported["run"]["id"], script_executor=execute_fixture)
    inspected = client.get(f"/api/datasets/{dataset.pk}/").data["cells"]
    assert inspected[-1]["transformation"]["execution"] == "external_import"
    assert inspected[-1]["transformation"]["package"] is None
    dataset.cells.create(
        position=3,
        title="Unverified claim",
        script="print('not evidence')",
        review=published.review,
    )
    unknown = client.get(f"/api/datasets/{dataset.pk}/").data["cells"][-1]
    assert unknown["transformation"]["execution"] == "unrecorded"
    assert unknown["transformation"]["run"] is None


def test_project_recipe_reuse_derivation_preview_and_atomic_cells():
    project, client, context = workspace()
    records = [{"question": "Same?", "answer": "Yes", "weight": 0.5}] * 3
    first = source(client, project, "First", records)
    second = source(client, project, "Second", records)
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Conversation",
        request_key="recipe",
        package=retained_package(
            context.project,
            context.user,
            [
                select_script(["question", "answer", "weight"]),
                conversation_script("question", "answer"),
            ],
        ),
    )["pipeline"]
    preview = execute(context, first, recipe, "preview", mode="preview", preview_rows=2)
    assert preview["status"] == "completed", preview
    assert first.cells.count() == 1
    assert len(preview["details"]["result"]["steps"]) == 2
    for dataset in (first, second):
        job = execute(context, dataset, recipe, "full")
        assert job["status"] == "completed", json.dumps(job)
        dataset.refresh_from_db()
        assert dataset.cells.count() == 3
        chain = list(dataset.cells.order_by("position"))
        assert chain[1].review["input_cells"] == [str(chain[0].pk)]
        assert chain[2].review["input_cells"] == [str(chain[1].pk)]
        output = list(store.iter_rows(paths.cell_path(dataset.pk, dataset.active_cell.pk)))
        assert len(output) == 3
        assert [row["weight"] for row in output] == [0.5] * 3
        assert output[0]["messages"][-1]["content"] == "Yes"
    variant = call(
        context,
        "save_dataset_pipeline",
        name="Different fields",
        request_key="variant",
        derived_from=recipe["id"],
        package=retained_package(context.project, context.user, [select_script(["other"])]),
    )["pipeline"]
    assert variant["derived_from"] == recipe["id"]
    assert variant["pipeline_id"] != recipe["pipeline_id"]
    compatibility = call(
        context,
        "validate_dataset_pipeline",
        pipeline=variant["id"],
        source_cell=str(second.active_cell.pk),
        source_fingerprint=second.active_cell.fingerprint,
    )
    assert not compatibility["validation"]["valid"]
    assert compatibility["validation"]["missing_columns"] == ["other"]
    before = second.active_cell.pk
    failed = execute(context, second, variant, "incompatible")
    assert failed["status"] == "failed"
    second.refresh_from_db()
    assert second.active_cell.pk == before
    assert second.cells.count() == 3


def test_preview_defers_population_bounds_but_publish_enforces_them(settings):
    project, client, context = workspace()
    settings.WORKSHOP_RUNTIME_IMAGES = ["sha256:" + "1" * 64]
    dataset = source(client, project, "Bounded recipe", [{"value": n} for n in range(4)])
    original = dataset.active_cell
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("step.py", "# Executed by the isolated runner in production.\n")
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "version": 1,
                    "runtime": settings.WORKSHOP_RUNTIME_IMAGES[0],
                    "steps": [
                        {
                            "name": "Retain",
                            "entrypoint": "step.py",
                            "checks": {"min_rows": 5, "preserve_rows": True},
                        }
                    ],
                }
            ),
        )
    stream.seek(0)
    package = pipeline_packages.save(project, context.user, stream)
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Retain",
        request_key="retain",
        package=str(package.pk),
    )["pipeline"]
    validation = call(
        context,
        "validate_dataset_pipeline",
        pipeline=recipe["id"],
        source_cell=str(original.pk),
        source_fingerprint=original.fingerprint,
    )
    assert any(
        w["code"] == "lineage_contract_missing" for w in validation["validation"]["warnings"]
    )
    for mode in ("preview", "publish"):
        receipt = call(
            context,
            "run_dataset_pipeline",
            dataset=str(dataset.pk),
            pipeline=recipe["id"],
            source_cell=str(original.pk),
            source_fingerprint=original.fingerprint,
            request_key=mode,
            mode=mode,
            preview_rows=2,
        )
        assert receipt["run"]["poll_after_seconds"] > 0
        pending = call(context, "inspect_dataset", dataset=str(dataset.pk))
        action = pending["next_actions"][0]
        assert action["tool"] == "get_job"
        assert action["arguments"] == {
            "project_id": str(project.pk),
            "kind": "dataset_pipeline",
            "id": receipt["run"]["id"],
        }
        assert receipt["next_actions"][0]["arguments"] == action["arguments"]

        def measured_executor(run, index, path, directory, step, files, progress):
            stage_started_at = run.result["stage_started_at"]
            for _ in range(2):
                time.sleep(0.025)
                progress()
                assert run.result["stage_started_at"] == stage_started_at
            return store.iter_rows(path)

        workbench.execute(
            receipt["run"]["id"],
            script_executor=measured_executor,
        )
        job = call(context, "get_job", kind="dataset_pipeline", id=receipt["run"]["id"])
        assert job["completed_at"] is not None
        assert job["details"]["poll_after_seconds"] is None
        assert job["progress"]["stage_seconds"]["step_1_executing"] >= 0.05
        step = job["progress"]["steps"][0]
        if mode == "preview":
            assert job["status"] == "completed", job
            assert step["check_results"]["deferred"] == {"min_rows": 5}
            assert step["check_results"]["passed"] == {"preserve_rows": True}
        else:
            assert job["status"] == "failed", job
            assert "min_rows" in job["job_error"] and "4" in job["job_error"]
            assert step["check_results"]["failed"] == {"min_rows": {"expected": 5, "actual": 4}}
    dataset.refresh_from_db()
    assert dataset.active_cell.pk == original.pk
    assert dataset.cells.count() == 1


def test_empty_result_later_step_failure_and_exact_cancel():
    project, client, context = workspace()
    dataset = source(client, project, "Source", [{"value": 1}])
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="No matches",
        request_key="empty",
        package=retained_package(context.project, context.user, [filter_script("value", 2)]),
    )["pipeline"]
    result = execute(context, dataset, recipe, "empty")
    assert result["status"] == "completed", result
    dataset.refresh_from_db()
    assert dataset.active_cell.rows == 0
    assert dataset.active_cell.review["condition"] == "value = 2"
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Broken later",
        request_key="broken",
        package=retained_package(
            context.project,
            context.user,
            [
                select_script(["value"]),
                rename_script({"missing": "other"}),
            ],
        ),
    )["pipeline"]
    result = execute(context, dataset, recipe, "broken")
    assert result["status"] == "failed"
    assert dataset.cells.count() == 2
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        run = call(
            context,
            "run_dataset_pipeline",
            dataset=str(dataset.pk),
            pipeline=recipe["id"],
            source_cell=str(dataset.active_cell.pk),
            source_fingerprint=dataset.active_cell.fingerprint,
            request_key="cancel",
        )["run"]
    cancelled = call(context, "cancel_dataset_pipeline_run", run=run["id"])
    assert cancelled["run"]["state"] == "cancelled"
    workbench.execute(run["id"], script_executor=execute_fixture)
    assert dataset.cells.count() == 2


@pytest.mark.parametrize(
    "output_schema",
    [{}, {"source_row": "integer"}, {"source_row": "integer", "value": "integer"}],
)
def test_empty_script_output_has_one_queryable_identity_column(settings, output_schema):
    project, client, context = workspace()
    settings.WORKSHOP_RUNTIME_IMAGES = ["sha256:" + "a" * 64]
    dataset = source(client, project, "Empty script output", [{"value": 1}])
    original = dataset.active_cell
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("empty.py", "import sys\nopen(sys.argv[2], 'w').close()\n")
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "version": 1,
                    "runtime": settings.WORKSHOP_RUNTIME_IMAGES[0],
                    "steps": [
                        {
                            "name": "Empty output",
                            "entrypoint": "empty.py",
                            "output_schema": output_schema,
                        },
                    ],
                }
            ),
        )
    stream.seek(0)
    package = pipeline_packages.save(project, context.user, stream)
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Empty script output",
        request_key="empty-script",
        package=str(package.pk),
    )["pipeline"]
    run = call(
        context,
        "run_dataset_pipeline",
        dataset=str(dataset.pk),
        pipeline=recipe["id"],
        source_cell=str(original.pk),
        source_fingerprint=original.fingerprint,
        request_key="empty-script-run",
    )["run"]
    workbench.execute(run["id"], script_executor=lambda *args: iter(()))
    job = call(context, "get_job", kind="dataset_pipeline", id=run["id"])
    assert job["status"] == "completed", job
    dataset.refresh_from_db()
    result = call(context, "query_dataset", dataset=str(dataset.pk), sql="SELECT * FROM t")
    assert result["rows"] == []
    assert sorted(result["columns"]) == sorted(set(output_schema) | {"source_row"})
    rest = client.get(f"/api/datasets/{dataset.pk}/").data
    cell = next(cell for cell in rest["cells"] if cell["id"] == str(dataset.active_cell.pk))
    assert [column["name"] for column in cell["columns"]] == result["columns"]
    assert store.file_sha256(paths.cell_path(dataset.pk, original.pk)) == original.fingerprint


def test_package_registration_never_executes_and_rejects_unsafe_archives(settings):
    project, client, context = workspace()
    settings.WORKSHOP_RUNTIME_IMAGES = ["sha256:" + "a" * 64]
    manifest = {
        "version": 1,
        "runtime": settings.WORKSHOP_RUNTIME_IMAGES[0],
        "steps": [
            {
                "name": "Preserve",
                "entrypoint": "preserve.py",
                "input_schema": {"value": "integer"},
                "output_schema": {"value": "integer"},
            }
        ],
    }

    def upload(entries):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        stream.seek(0)
        stream.name = "pipeline.zip"
        return client.post(
            "/api/dataset-pipeline-packages/",
            {"project": str(project.pk), "file": stream},
            format="multipart",
        )

    response = upload(
        {
            "manifest.json": json.dumps(manifest),
            "preserve.py": "raise RuntimeError('must not execute')",
        }
    )
    assert response.status_code == 201, response.data
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Packaged",
        request_key="packaged",
        package=response.data["id"],
    )["pipeline"]
    check = call(context, "validate_dataset_pipeline", pipeline=recipe["id"])
    assert check["validation"]["valid"]
    assert check["validation"]["execution"] == "not_run"
    assert (
        upload({"manifest.json": json.dumps(manifest), "../escape.py": "pass"}).status_code == 400
    )
    assert (
        upload(
            {"manifest.json": json.dumps(manifest), "preserve.py": "not valid python!"}
        ).status_code
        == 400
    )


def test_binding_is_paused_then_reuses_revision_without_an_agent():
    project, client, context = workspace()
    origin = source(client, project, "Incoming", [{"value": 1}, {"value": 1}])
    destination = source(client, project, "Prepared", [{"value": 0}])
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Projection",
        request_key="projection",
        package=retained_package(context.project, context.user, [select_script(["value"])]),
    )["pipeline"]
    binding = call(
        context,
        "save_dataset_pipeline_binding",
        dataset=str(destination.pk),
        pipeline=recipe["id"],
        source_dataset=str(origin.pk),
        request_key="binding",
        trigger="ingestion",
    )["binding"]
    assert not binding["enabled"]
    pipeline_bindings.tick()
    assert destination.cells.count() == 1
    call(
        context,
        "set_dataset_pipeline_binding_state",
        binding=binding["id"],
        expected_version=binding["version"],
        enabled=True,
    )
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        pipeline_bindings.tick()
    run = destination.pipeline_runs.get()
    workbench.execute(run.pk, script_executor=execute_fixture)
    destination.refresh_from_db()
    assert destination.active_cell.rows == 2
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        pipeline_bindings.tick()
    assert destination.pipeline_runs.count() == 1


def test_failed_binding_stops_and_requires_an_explicit_retry():
    project, client, context = workspace()
    origin = source(client, project, "Incoming", [{"value": 1}])
    destination = source(client, project, "Prepared", [{"value": 0}])
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Broken",
        request_key="broken",
        package=retained_package(context.project, context.user, [select_script(["missing"])]),
    )["pipeline"]
    binding = call(
        context,
        "save_dataset_pipeline_binding",
        dataset=str(destination.pk),
        source_dataset=str(origin.pk),
        pipeline=recipe["id"],
        request_key="binding",
        trigger="ingestion",
    )["binding"]
    enabled = call(
        context,
        "set_dataset_pipeline_binding_state",
        binding=binding["id"],
        expected_version=1,
        enabled=True,
    )["binding"]
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        started = call(context, "run_dataset_pipeline_binding", binding=binding["id"])
    assert started["job"]["id"] == started["run"]["id"]
    workbench.execute(started["run"]["id"], script_executor=execute_fixture)
    job = call(context, "get_job", kind="dataset_pipeline", id=started["run"]["id"])
    assert job["details"]["result"]["steps"][0]["state"] == "failed"
    destination.pipeline_bindings.update(next_check_at=None)
    pipeline_bindings.tick()
    saved = destination.pipeline_bindings.get()
    assert not saved.enabled and saved.error and saved.checkpoint == ""
    assert saved.runs_started == 1 and destination.pipeline_runs.count() == 1
    pipeline_bindings.tick()
    assert destination.pipeline_runs.count() == 1
    call(
        context,
        "set_dataset_pipeline_binding_state",
        binding=binding["id"],
        expected_version=enabled["version"],
        enabled=True,
    )
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        retried = call(context, "run_dataset_pipeline_binding", binding=binding["id"])
    assert retried["run"]["id"] != started["run"]["id"]


def test_revisions_conflicts_tenancy_and_resources():
    project, client, context = workspace()
    dataset = source(client, project, "Original", [{"value": 1}])
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Select",
        request_key="original",
        package=retained_package(context.project, context.user, [select_script(["value"])]),
    )["pipeline"]
    revised = call(
        context,
        "save_dataset_pipeline",
        name="Renamed",
        request_key="revised",
        pipeline=recipe["pipeline_id"],
        expected_revision=1,
        package=retained_package(
            context.project, context.user, [rename_script({"value": "renamed"})]
        ),
    )["pipeline"]
    assert revised["revision"] == 2 and revised["parent"] == recipe["id"]
    stale = asyncio.run(
        CATALOG.call(
            "save_dataset_pipeline",
            {
                "name": "Stale",
                "request_key": "stale",
                "pipeline": recipe["pipeline_id"],
                "expected_revision": 1,
                "package": retained_package(
                    context.project, context.user, [select_script(["value"])]
                ),
            },
            context,
        )
    )
    assert stale.isError
    other = Project.objects.create(name="Other", slug="other")
    outside = workbench.save_pipeline(
        other,
        context.user,
        name="Private",
        request_key="private",
        package=retained_package(other, context.user, [select_script(["value"])]),
    )
    rejected = asyncio.run(
        CATALOG.call(
            "run_dataset_pipeline",
            {
                "dataset": str(dataset.pk),
                "pipeline": str(outside.pk),
                "source_cell": str(dataset.active_cell.pk),
                "source_fingerprint": dataset.active_cell.fingerprint,
                "request_key": "outside",
            },
            context,
        )
    )
    assert rejected.isError
    assert dataset.cells.count() == 1
    conflict = asyncio.run(
        CATALOG.call(
            "save_dataset_pipeline",
            {
                "name": "Changed",
                "request_key": "original",
                "package": retained_package(
                    context.project, context.user, [select_script(["value"])]
                ),
            },
            context,
        )
    )
    assert conflict.isError
    assert conflict.structuredContent["error"]["code"] == "request_key_conflict"
    wrong_source = asyncio.run(
        CATALOG.call(
            "run_dataset_pipeline",
            {
                "dataset": str(dataset.pk),
                "pipeline": recipe["id"],
                "source_cell": str(dataset.active_cell.pk),
                "source_fingerprint": "0" * 64,
                "request_key": "wrong-fingerprint",
            },
            context,
        )
    )
    assert wrong_source.isError
    assert wrong_source.structuredContent["error"]["code"] == "source_conflict"
    assert not dataset.pipeline_runs.exists()

    async def read():
        with bind_context(context):
            result = list(await read_resource(f"overmind://dataset-pipelines/{recipe['id']}"))
            return json.loads(result[0].content)

    assert asyncio.run(read())["revision"] == 1
    job = execute(context, dataset, recipe, "original-still-works")
    assert job["status"] == "completed"
    assert job["details"]["operation"]["event_count"] > 0


def test_trace_binding_rebuilds_after_late_changes_and_removed_matches():
    project, client, context = workspace()
    destination = source(client, project, "Trace output", [{"trace_id": "seed"}])
    span = Span.objects.create(
        project=project,
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex,
        name="run",
        span_type="entry_point",
        service_name="incoming",
        start_time_ns=1,
        end_time_ns=2,
        duration_ns=1,
        attributes={"input": "Question", "output": "First answer"},
    )
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Trace projection",
        request_key="trace",
        package=retained_package(
            context.project, context.user, [select_script(["trace_id", "status", "duration_ms"])]
        ),
    )["pipeline"]
    binding = call(
        context,
        "save_dataset_pipeline_binding",
        dataset=str(destination.pk),
        pipeline=recipe["id"],
        trace_source={"filters": {"service_name": "incoming"}},
        request_key="traces",
    )["binding"]
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        first = pipeline_bindings.advance(project, binding["id"], manual=True)
    workbench.execute(first.pk, script_executor=execute_fixture)
    first.refresh_from_db()
    assert first.state == "completed", first.error
    Span.objects.filter(pk=span.pk).update(duration_ns=9000000)
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        second = pipeline_bindings.advance(project, binding["id"], manual=True)
    workbench.execute(second.pk, script_executor=execute_fixture)
    second.refresh_from_db()
    assert second.state == "completed", second.error
    assert second.pk != first.pk
    assert (
        next(store.iter_rows(paths.cell_path(destination.pk, second.output_id)))["duration_ms"] == 9
    )
    Span.objects.filter(pk=span.pk).update(service_name="removed")
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        third = pipeline_bindings.advance(project, binding["id"], manual=True)
    workbench.execute(third.pk, script_executor=execute_fixture)
    third.refresh_from_db()
    assert third.state == "completed", third.error
    assert third.output.rows == 0
    assert first.output.rows == 1


def test_large_nested_data_repeated_runs_and_failed_binding_keep_exact_inputs():
    project, client, context = workspace()
    records = [
        {
            "value": index % 100,
            "target": {"options": ["B", "A"], "probabilities": [0.25, 0.75]},
            "weight": 0.5,
            "blank": "",
        }
        for index in range(10000)
    ]
    dataset = source(client, project, "Ten thousand", records)
    original = dataset.active_cell
    original_hash = original.fingerprint
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Retain meaning",
        request_key="large",
        package=retained_package(
            context.project, context.user, [select_script(["value", "target", "weight", "blank"])]
        ),
    )["pipeline"]
    first = execute(context, dataset, recipe, "large-one")
    assert first["status"] == "completed", first
    first_cell = dataset.cells.get(pk=first["details"]["output_cell"])
    job = execute(context, dataset, recipe, "large-two", source_cell=original)
    assert job["status"] == "completed", job
    assert first_cell.fingerprint == dataset.cells.get(pk=job["details"]["output_cell"]).fingerprint
    assert original.fingerprint == original_hash
    output = list(store.iter_rows(paths.cell_path(dataset.pk, first_cell.pk)))
    assert [{key: row[key] for key in records[0]} for row in output] == records
    destination = source(client, project, "Unchanged", [{"value": 0}])
    bad = call(
        context,
        "save_dataset_pipeline",
        name="Missing input",
        request_key="bad",
        package=retained_package(context.project, context.user, [select_script(["absent"])]),
    )["pipeline"]
    binding = call(
        context,
        "save_dataset_pipeline_binding",
        dataset=str(destination.pk),
        pipeline=bad["id"],
        source_dataset=str(dataset.pk),
        request_key="bad-binding",
    )["binding"]
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        run = pipeline_bindings.advance(project, binding["id"], manual=True)
    workbench.execute(run.pk, script_executor=execute_fixture)
    run.refresh_from_db()
    assert run.state == "failed"
    run.binding.refresh_from_db()
    assert run.binding.checkpoint == ""
    assert destination.cells.count() == 1


@pytest.mark.skipif(
    not os.environ.get("WORKSHOP_TEST_IMAGE"),
    reason="Requires a running local Docker daemon and a built Workshop runtime image",
)
def test_script_branch_declarations_have_code_evidence_and_measured_outputs(settings):
    project, client, context = workspace()
    settings.WORKSHOP_RUNTIME_IMAGES = [os.environ["WORKSHOP_TEST_IMAGE"]]
    settings.WORKSHOP_DOCKER_SOCKET = os.environ.get(
        "WORKSHOP_DOCKER_SOCKET", "/var/run/docker.sock"
    )
    dataset = source(client, project, "Split input", [{"value": n} for n in range(10000)])
    original = dataset.active_cell
    code = """import json,sys
with open(sys.argv[1]) as source, open(sys.argv[2], 'w') as output:
    for line in source:
        row = json.loads(line)
        if row['value'] % 2 == PARITY:
            output.write(json.dumps(row) + '\\n')
"""
    manifest = {
        "version": 1,
        "runtime": settings.WORKSHOP_RUNTIME_IMAGES[0],
        "steps": [
            {
                "id": name,
                "input": "source",
                "name": name,
                "entrypoint": name + ".py",
                "condition": {"expression": f"value % 2 == {parity}", "line": 5},
                "input_schema": {"value": "integer"},
                "output_schema": {"value": "integer"},
            }
            for name, parity in [("even", 0), ("odd", 1)]
        ],
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("even.py", code.replace("PARITY", "0"))
        archive.writestr("odd.py", code.replace("PARITY", "1"))
    stream.seek(0)
    package = pipeline_packages.save(project, context.user, stream)
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Parity split",
        request_key="parity",
        package=str(package.pk),
    )["pipeline"]
    assert recipe["flow"]["edges"][0]["condition"] == {
        "expression": "value % 2 == 0",
        "line": 5,
        "file": "even.py",
        "evidence": "agent_declared",
    }
    for mode in ("preview", "publish"):
        run = call(
            context,
            "run_dataset_pipeline",
            dataset=str(dataset.pk),
            pipeline=recipe["id"],
            source_cell=str(original.pk),
            source_fingerprint=original.fingerprint,
            request_key=mode,
            mode=mode,
            preview_rows=100,
        )["run"]
        workbench.execute(run["id"], script_executor=pipeline_runner.execute_script)
        job = call(context, "get_job", kind="dataset_pipeline", id=run["id"])
        assert job["status"] == "completed", job
        assert job["progress"]["seconds"] > 0
        for step in job["progress"]["steps"]:
            runtime = step["runtime"]
            assert runtime["stage"] == "completed"
            assert runtime["stage_seconds"]["script_execution"] > 0
            assert runtime["stage_seconds"]["input_transfer"] > 0
            assert runtime["stage_seconds"]["artifact_transfer"] > 0
            assert runtime["stage_seconds"]["cleanup"] >= 0
        assert [step["output_rows"] for step in job["details"]["result"]["steps"]] == (
            [50, 50] if mode == "preview" else [5000, 5000]
        )
    outputs = list(dataset.cells.exclude(pk=original.pk).order_by("position"))
    assert [cell.review["input_cells"] for cell in outputs] == [
        [str(original.pk)],
        [str(original.pk)],
    ]
    assert all(
        row["value"] % 2 == 0 for row in store.iter_rows(paths.cell_path(dataset.pk, outputs[0].pk))
    )
    assert all(
        row["value"] % 2 == 1 for row in store.iter_rows(paths.cell_path(dataset.pk, outputs[1].pk))
    )


@pytest.mark.skipif(
    not os.environ.get("WORKSHOP_TEST_IMAGE"),
    reason="Requires a running local Docker daemon and a built Workshop runtime image",
)
@pytest.mark.parametrize("row_count", [10000, 100000])
def test_real_isolated_script_execution_reproduction_and_timeout(settings, row_count):
    project, client, context = workspace()
    settings.WORKSHOP_RUNTIME_IMAGES = [os.environ["WORKSHOP_TEST_IMAGE"]]
    settings.WORKSHOP_DOCKER_SOCKET = os.environ.get(
        "WORKSHOP_DOCKER_SOCKET", "/var/run/docker.sock"
    )
    dataset = source(
        client, project, "Container input", [{"value": index} for index in range(row_count)]
    )
    script = """import json, os, pathlib, socket, sys
assert not pathlib.Path('/code').exists()
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not any(key in os.environ for key in ['AWS_SECRET_ACCESS_KEY', 'OPENROUTER_API_KEY', 'DATABASE_URL'])
probe = socket.socket()
probe.settimeout(1)
assert probe.connect_ex(('1.1.1.1', 443)) != 0
probe.close()
with open(sys.argv[1]) as source, open(sys.argv[2], 'w') as output:
    for line in source:
        row = json.loads(line)
        row['value'] *= 2
        output.write(json.dumps(row) + '\\n')
"""

    def package(code, seconds=30):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "version": 1,
                        "runtime": settings.WORKSHOP_RUNTIME_IMAGES[0],
                        "limits": {"seconds": seconds},
                        "steps": [
                            {
                                "name": "Double",
                                "entrypoint": "step.py",
                                "input_schema": {"value": "integer"},
                                "output_schema": {"value": "integer"},
                                "checks": {"preserve_rows": True},
                            }
                        ],
                    }
                ),
            )
            archive.writestr("step.py", code)
        stream.seek(0)
        return pipeline_packages.save(project, context.user, stream)

    registered = package(script)
    recipe = call(
        context,
        "save_dataset_pipeline",
        name="Double",
        request_key="isolated",
        package=str(registered.pk),
    )["pipeline"]
    outputs = []
    original = dataset.active_cell
    for key in ["isolated-one", "isolated-two"]:
        run = call(
            context,
            "run_dataset_pipeline",
            dataset=str(dataset.pk),
            pipeline=recipe["id"],
            source_cell=str(original.pk),
            source_fingerprint=original.fingerprint,
            request_key=key,
        )["run"]
        workbench.execute(run["id"], script_executor=pipeline_runner.execute_script)
        job = call(context, "get_job", kind="dataset_pipeline", id=run["id"])
        assert job["status"] == "completed", json.dumps(job)
        outputs.append(dataset.cells.get(pk=job["details"]["output_cell"]).fingerprint)
    assert outputs[0] == outputs[1]
    bad = package("while True: pass", seconds=1)
    bad_recipe = call(
        context, "save_dataset_pipeline", name="Timeout", request_key="timeout", package=str(bad.pk)
    )["pipeline"]
    run = call(
        context,
        "run_dataset_pipeline",
        dataset=str(dataset.pk),
        pipeline=bad_recipe["id"],
        source_cell=str(dataset.active_cell.pk),
        source_fingerprint=dataset.active_cell.fingerprint,
        request_key="timeout",
    )["run"]
    workbench.execute(run["id"], script_executor=pipeline_runner.execute_script)
    job = call(context, "get_job", kind="dataset_pipeline", id=run["id"])
    assert job["status"] == "failed", job
    runtime = job["details"]["result"]["steps"][0]["runtime"]
    assert runtime["stage"] == "stopped"
    assert runtime["cleanup_confirmed"] is True
    assert dataset.cells.count() == 3
